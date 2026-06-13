# SPDX-License-Identifier: GPL-3.0-or-later
"""Central KiCad IPC client layer: one reused connection, a configurable
timeout, busy-retry with exponential backoff, and file logging.

Why this exists
---------------
Every IPC tool used to construct a brand-new ``kipy.KiCad()`` per call, with
kipy's default 2000 ms timeout. On large boards that timeout is exceeded
(``get_board`` / ``get_selection`` → "KiCad is busy"), surfacing as a bare
"failed", and the per-call reconnect is the single biggest latency source.

This module owns:
* ``get_client()`` — a process-wide REUSED client (the speed lever); it
  auto-reconnects when the cached connection has gone stale.
* ``new_client()`` — a fresh client (for lifecycle/wait-for-restart loops that
  must not see a stale cached connection), with the same configured timeout.
* ``call_with_retry()`` — runs an IPC call, retrying "KiCad is busy" with
  exponential backoff and reconnecting once on a dropped connection.
* file logging next to the open ``.kicad_pcb`` (fallback: temp dir), capturing
  connect/reconnect, timeouts, busy-retries and tool-call durations — stdout is
  invisible under a plugin launch.

Timeout is configurable via ``KICAD_MCP_IPC_TIMEOUT_MS`` (default 15000).
All kipy imports are lazy so this module imports headless (no kipy needed for
the pure helpers / unit tests).
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
from typing import Any, Callable, Optional, TypeVar

log = logging.getLogger("kicad_mcp.ipc_session")

T = TypeVar("T")

DEFAULT_TIMEOUT_MS = 15000
_TIMEOUT_ENV = "KICAD_MCP_IPC_TIMEOUT_MS"

# Busy-retry: KiCad serialises its API + UI on one thread, so a transient
# failure means "busy", not a race — back off and try again.
BUSY_RETRY_ATTEMPTS = 5
BUSY_BACKOFF_BASE_S = 0.15

# Process-wide reused client + one-time logging setup.
_client: Any = None
_logging_configured = False


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

def timeout_ms() -> int:
    """The IPC timeout in ms — ``KICAD_MCP_IPC_TIMEOUT_MS`` or the default."""
    raw = os.environ.get(_TIMEOUT_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_TIMEOUT_MS


# --------------------------------------------------------------------------- #
# Error classification
# --------------------------------------------------------------------------- #

def is_busy_error(exc: BaseException) -> bool:
    """True for the "KiCad is busy and cannot respond" transient (retryable)."""
    return "busy" in str(exc).lower()


def is_connection_error(exc: BaseException) -> bool:
    """True for a dropped/broken connection — reconnect, then retry once."""
    text = str(exc).lower()
    if any(s in text for s in ("broken pipe", "connection", "not connected",
                               "reset by peer", "closed")):
        return True
    try:
        from kipy.errors import ConnectionError as KiConnError  # type: ignore
        return isinstance(exc, KiConnError)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Client lifecycle
# --------------------------------------------------------------------------- #

def _make_client(factory: Optional[Callable[[], Any]] = None) -> Any:
    if factory is not None:
        return factory()
    from kipy import KiCad  # type: ignore  # lazy: only when actually connecting
    return KiCad(timeout_ms=timeout_ms())


def new_client(factory: Optional[Callable[[], Any]] = None) -> Any:
    """A FRESH client with the configured timeout (no caching). Use for
    wait-for-restart / lifecycle loops that must observe a new KiCad instance.
    Raises ``RuntimeError`` with a clear message if KiCad is unreachable."""
    try:
        client = _make_client(factory)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot reach KiCad IPC server: {exc}. Is KiCad running and the "
            "IPC API enabled (Preferences → Plugins → IPC API)?"
        ) from exc
    log.info("ipc connect (fresh, timeout=%d ms)", timeout_ms())
    return client


def get_client(factory: Optional[Callable[[], Any]] = None,
               force_new: bool = False) -> Any:
    """The REUSED process-wide client (connecting on first use). ``force_new``
    drops any cached client first. Raises ``RuntimeError`` if unreachable."""
    global _client
    if force_new:
        _client = None
    if _client is not None:
        return _client
    _client = new_client(factory)
    return _client


def reset_client() -> None:
    """Drop the cached client so the next ``get_client`` reconnects."""
    global _client
    _client = None


# --------------------------------------------------------------------------- #
# Retry wrapper
# --------------------------------------------------------------------------- #

def call_with_retry(fn: Callable[[], T], label: str = "ipc",
                    attempts: int = BUSY_RETRY_ATTEMPTS) -> T:
    """Run ``fn`` (one IPC call), retrying "busy" with exponential backoff and
    reconnecting once on a dropped connection. Re-raises the last error after
    exhausting attempts. Logs each retry and the call duration."""
    last: Optional[BaseException] = None
    for i in range(attempts):
        t0 = time.perf_counter()
        try:
            result = fn()
            log.debug("%s ok in %.3fs", label, time.perf_counter() - t0)
            return result
        except Exception as exc:  # noqa: BLE001 - classified + re-raised below
            last = exc
            final = i >= attempts - 1
            if is_busy_error(exc) and not final:
                wait = BUSY_BACKOFF_BASE_S * (2 ** i)
                log.warning("%s busy (attempt %d/%d): %s; retry in %.2fs",
                            label, i + 1, attempts, exc, wait)
                time.sleep(wait)
                continue
            if is_connection_error(exc) and not final:
                log.warning("%s connection lost (attempt %d/%d): %s; reconnect",
                            label, i + 1, attempts, exc)
                reset_client()
                time.sleep(BUSY_BACKOFF_BASE_S)
                continue
            log.error("%s failed in %.3fs: %s",
                      label, time.perf_counter() - t0, exc)
            raise
    assert last is not None
    raise last


@contextlib.contextmanager
def timed(label: str):
    """Context manager that logs how long a tool call / IPC section took."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        log.info("%s took %.3fs", label, time.perf_counter() - t0)


# --------------------------------------------------------------------------- #
# File logging (next to the open board, fallback temp dir)
# --------------------------------------------------------------------------- #

def board_log_dir(client: Any = None) -> str:
    """Directory of the open ``.kicad_pcb`` (best effort), else the temp dir."""
    try:
        from kipy.proto.common.types.base_types_pb2 import (  # type: ignore  # pylint: disable=no-name-in-module
            DocumentType,
        )
        docs = client.get_open_documents(DocumentType.Value("DOCTYPE_PCB"))
        for doc in docs:
            name = getattr(doc, "board_filename", "") or ""
            if name and os.path.dirname(name):
                return os.path.dirname(name)
    except Exception:
        pass
    return tempfile.gettempdir()


def configure_logging(client: Any = None, force: bool = False) -> str:
    """Attach a file handler under the board dir (once). Returns the log path.

    Idempotent: only the first call (per process) installs the handler unless
    ``force``. Logs connect/reconnect, timeouts, busy-retries, durations.
    """
    global _logging_configured
    path = os.path.join(board_log_dir(client), "kicad_mcp_ipc.log")
    if _logging_configured and not force:
        return path
    root = logging.getLogger("kicad_mcp")
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    _logging_configured = True
    log.info("ipc file logging → %s (timeout=%d ms)", path, timeout_ms())
    return path


def connect_board(factory: Optional[Callable[[], Any]] = None):
    """Reused client + its board, with busy-retry and one-time file logging.
    Returns ``(client, board)``; raises ``RuntimeError`` on unreachable KiCad
    or no open board."""
    client = get_client(factory)
    try:
        configure_logging(client)
    except Exception:  # logging must never break a tool
        pass
    try:
        board = call_with_retry(client.get_board, "get_board")
    except Exception as exc:
        raise RuntimeError(
            f"No board accessible via IPC: {exc}. Open a .kicad_pcb in the "
            "PCB Editor before calling IPC tools."
        ) from exc
    return client, board
