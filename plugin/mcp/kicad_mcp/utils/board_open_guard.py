# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard against disk-patching a .kicad_pcb while it is open in the KiCad GUI.

Collaboration model: when a board is open in KiCad, the safe single source of
truth is KiCad's IN-MEMORY model. The IPC live tools (``ipc_*`` / ``live_*``)
edit that model, so the user keeps every window open and BOTH sides can save
coherently. A DISK patch of the same file, by contrast, is invisible to the
running editor — the user's next Ctrl+S overwrites it (or the agent's write
clobbers the user's unsaved work). There is no real two-sided file lock; the
only safe coexistence is "edit the open board via IPC, not the file".

This module enforces that for PCB: a disk write to a .kicad_pcb that a running
KiCad has open is BLOCKED (raises :class:`BoardOpenError`), steering the agent
to the IPC tools. Schematics are intentionally NOT blocked — Eeschema has no
IPC save in KiCad 10.0.x, so the text-patcher is the supported SCH path.

Cheap + safe when headless: the check only runs when ``KICAD_API_SOCKET`` is
set (KiCad sets it for API clients — exactly when the IPC tools are reachable),
and a short-lived client is cached with a negative-result TTL so writes never
pay a repeated connect cost. Override with
``KICAD_MCP_ALLOW_DISK_WRITE_WHILE_OPEN=1``.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

# kipy's protobuf enums (DocumentType) are generated; pylint can't see the
# names via static analysis. Suppress file-wide (same as ipc_tools.py).
# pylint: disable=no-name-in-module

_ALLOW_ENV = "KICAD_MCP_ALLOW_DISK_WRITE_WHILE_OPEN"
_SOCKET_ENV = "KICAD_API_SOCKET"
_NEG_TTL_S = 5.0  # don't re-attempt a failed connect for this long

# Cached IPC client + negative-result timestamp (per server process).
_client: Any = None
_last_fail: float = 0.0


class BoardOpenError(RuntimeError):
    """Raised when a disk write targets a .kicad_pcb open in the KiCad GUI."""


def _override_enabled() -> bool:
    return os.environ.get(_ALLOW_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


def _reset_client() -> None:
    global _client, _last_fail
    _client = None
    _last_fail = time.time()


def _get_client(factory: Optional[Callable] = None):
    """A connected kipy client, or None — negative-cached so a missing KiCad
    isn't retried on every write."""
    global _client
    if _client is not None:
        return _client
    if time.time() - _last_fail < _NEG_TTL_S:
        return None
    if factory is None:
        if not os.environ.get(_SOCKET_ENV):
            _reset_client()  # no socket → KiCad not reachable as an API client
            return None
        try:
            from kipy import KiCad  # lazy: only inside a KiCad-reachable env
            factory = lambda: KiCad(timeout_ms=1000)  # noqa: E731
        except Exception:
            _reset_client()
            return None
    try:
        _client = factory()
        return _client
    except Exception:
        _reset_client()
        return None


def is_pcb_open_in_gui(path: str, factory: Optional[Callable] = None) -> bool:
    """True if a running KiCad has this .kicad_pcb open (best-effort)."""
    client = _get_client(factory)
    if client is None:
        return False
    try:
        from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
            DocumentType,
        )
        docs = client.get_open_documents(DocumentType.Value("DOCTYPE_PCB"))
    except Exception:
        _reset_client()  # stale socket (KiCad closed/restarted) → drop it
        return False
    target = os.path.normcase(os.path.basename(path))
    target_full = os.path.normcase(os.path.normpath(path))
    for doc in docs:
        name = getattr(doc, "board_filename", "") or ""
        if not name:
            continue
        if os.path.normcase(os.path.basename(name)) == target:
            return True
        if os.path.normcase(os.path.normpath(name)) == target_full:
            return True
    return False


def guard_pcb_disk_write(path: str, factory: Optional[Callable] = None) -> None:
    """Raise :class:`BoardOpenError` if a disk write to ``path`` would race the
    KiCad GUI. No-op for non-PCB files, when the override is set, or when the
    board is not open.
    """
    if not str(path).lower().endswith(".kicad_pcb"):
        return  # SCH/others: text-patcher is the supported path — don't block
    if _override_enabled():
        return
    if is_pcb_open_in_gui(path, factory):
        raise BoardOpenError(
            f"'{os.path.basename(path)}' ist gerade in KiCad geoeffnet. "
            "Direkte Datei-Patches kollidieren mit dem offenen Editor "
            "(das naechste Speichern ueberschreibt sie, oder umgekehrt). "
            "Bearbeite das offene Board ueber die Live-Tools (ipc_* / live_*) "
            "im Arbeitsspeicher — dann koennen beide Seiten speichern. "
            f"Um die Datei doch direkt zu patchen, setze {_ALLOW_ENV}=1 "
            "(oder schliesse das Board in KiCad)."
        )
