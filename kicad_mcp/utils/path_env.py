# SPDX-License-Identifier: GPL-3.0-or-later
r"""
Environment + path normalization for kicad-mcp.

The MCP server may be invoked from any of:

  * **windows** — native Windows interpreter, paths look like ``C:\foo\bar``.
  * **wsl** — Linux interpreter inside WSL, paths look like ``/mnt/c/foo/bar``;
    the same on-disk file is reachable from Windows as ``C:\foo\bar``.
  * **linux** — native Linux, plain POSIX paths.
  * **darwin** — macOS, plain POSIX paths.

Clients (LLM agents, scripts, IDE plugins) often run in a *different* env
than the server. A WSL agent talking to a server on Windows would otherwise
have to manually translate ``/mnt/c/foo`` ↔ ``C:\foo`` for every call.

This module centralises that translation and a few related concerns:

  * :func:`detect_environment` — single source of truth for env detection.
  * :func:`to_local_path` — convert any incoming path to the local-OS form.
    Idempotent on already-local paths, lossless across both directions.
  * :func:`from_local_to_other` — invert direction (mainly used to hand a
    path back to a remote tool, e.g. ``kicad-cli.exe`` from WSL).
  * :func:`kicad_paths` — discover well-known KiCad install locations
    (``kicad-cli``, ``footprints/``, ``symbols/``, bundled Python) for the
    current environment, with environment-variable overrides.

All other tool modules should import from here instead of re-rolling their
own resolver. ``KICAD_LIB_ROOT``, ``KICAD_BIN``, ``KICAD_PYTHON_PATH`` are
the canonical override env vars across the codebase.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from functools import lru_cache


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def detect_environment() -> str:
    """Return the runtime environment as one of:
    ``"windows"``, ``"wsl"``, ``"linux"``, ``"darwin"``.

    Cached: the answer cannot change during a process lifetime.
    """
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    # Linux: distinguish WSL from native Linux.
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as fh:
            text = fh.read().lower()
        if "microsoft" in text or "wsl" in text:
            return "wsl"
    except OSError:
        # /proc/version nicht lesbar → als natives Linux behandeln
        pass
    return "linux"


def is_wsl() -> bool:
    """Backwards-compatible alias for :func:`detect_environment` == ``"wsl"``."""
    return detect_environment() == "wsl"


# ---------------------------------------------------------------------------
# Path conversion
# ---------------------------------------------------------------------------


_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$", re.DOTALL)
_WSL_MNT_RE = re.compile(r"^/mnt/([a-zA-Z])(?:/(.*))?$", re.DOTALL)


def _windows_to_wsl(path: str) -> str:
    """``C:\\foo\\bar`` → ``/mnt/c/foo/bar``. Returns input on no match."""
    m = _DRIVE_RE.match(path)
    if not m:
        return path
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def _wsl_to_windows(path: str) -> str:
    """``/mnt/c/foo/bar`` → ``C:\\foo\\bar``. Returns input on no match.

    Tries the ``wslpath`` helper first (handles symlinks and edge cases like
    ``//wsl$/...`` and UNC paths) and falls back to a regex-based conversion
    if ``wslpath`` is not available or fails.
    """
    if not path.startswith("/mnt/"):
        return path
    try:
        result = subprocess.run(
            ["wslpath", "-w", path],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        # wslpath nicht verfügbar/fehlgeschlagen — Regex-Fallback unten
        pass
    m = _WSL_MNT_RE.match(path)
    if not m:
        return path
    drive = m.group(1).upper()
    rest = (m.group(2) or "").replace("/", "\\")
    return f"{drive}:\\{rest}" if rest else f"{drive}:\\"


def to_local_path(path: str) -> str:
    """Normalize ``path`` to whatever the *current* OS expects.

    * On **Windows**: ``/mnt/c/...`` style is converted to ``C:\\...``;
      mixed separators are normalized to ``\\``.
    * On **WSL**: ``C:\\...`` style is converted to ``/mnt/c/...``.
    * On **native Linux/macOS**: returned unchanged (no Windows drives expected).

    Empty / non-string input is returned as-is. The result is always a
    string the local ``open()`` / ``os.path`` calls will accept directly.
    """
    if not path or not isinstance(path, str):
        return path
    env = detect_environment()
    if env == "windows":
        if _WSL_MNT_RE.match(path):
            return _wsl_to_windows(path)
        if _DRIVE_RE.match(path):
            return path.replace("/", "\\")
        return path
    if env == "wsl":
        if _DRIVE_RE.match(path):
            return _windows_to_wsl(path)
        return path
    # Native linux / darwin — Windows paths shouldn't appear here, but if a
    # user passes one accidentally, leave it; ``os.path.isfile`` will fail
    # and the caller surfaces a clear error.
    return path


def from_local_to_other(path: str) -> str:
    """Inverse of :func:`to_local_path`: produce the *cross-environment* form.

    Useful when handing a local path to a binary that lives in the other
    environment (e.g. ``kicad-cli.exe`` started from WSL needs Windows
    paths even though the script's local paths are POSIX).
    """
    if not path or not isinstance(path, str):
        return path
    env = detect_environment()
    if env == "wsl":
        # Local form is /mnt/c/...; the "other side" is Windows.
        return _wsl_to_windows(path)
    if env == "windows":
        return _windows_to_wsl(path)
    return path


# ---------------------------------------------------------------------------
# KiCad install discovery
# ---------------------------------------------------------------------------


_KICAD_VERSIONS = ("10.0",)  # KiCad 10+ only — pre-10 lacks the IPC API

_KICAD_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "windows": {
        "kicad_cli": [
            rf"C:\Program Files\KiCad\{v}\bin\kicad-cli.exe" for v in _KICAD_VERSIONS
        ],
        "footprints": [
            rf"C:\Program Files\KiCad\{v}\share\kicad\footprints" for v in _KICAD_VERSIONS
        ],
        "symbols": [
            rf"C:\Program Files\KiCad\{v}\share\kicad\symbols" for v in _KICAD_VERSIONS
        ],
        "python": [
            rf"C:\Program Files\KiCad\{v}\bin\python.exe" for v in _KICAD_VERSIONS
        ],
    },
    "wsl": {
        # On WSL the KiCad install lives on the Windows host. Tools can call
        # the .exe directly via /mnt/c/... thanks to WSL's interop.
        "kicad_cli": [
            f"/mnt/c/Program Files/KiCad/{v}/bin/kicad-cli.exe" for v in _KICAD_VERSIONS
        ],
        "footprints": [
            f"/mnt/c/Program Files/KiCad/{v}/share/kicad/footprints" for v in _KICAD_VERSIONS
        ],
        "symbols": [
            f"/mnt/c/Program Files/KiCad/{v}/share/kicad/symbols" for v in _KICAD_VERSIONS
        ],
        "python": [
            f"/mnt/c/Program Files/KiCad/{v}/bin/python.exe" for v in _KICAD_VERSIONS
        ],
    },
    "linux": {
        "kicad_cli": ["/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli"],
        "footprints": [
            "/usr/share/kicad/footprints",
            "/usr/local/share/kicad/footprints",
            "/opt/kicad/share/kicad/footprints",
        ],
        "symbols": [
            "/usr/share/kicad/symbols",
            "/usr/local/share/kicad/symbols",
            "/opt/kicad/share/kicad/symbols",
        ],
        "python": ["/usr/bin/python3", "/usr/local/bin/python3"],
    },
    "darwin": {
        "kicad_cli": [
            "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
        ],
        "footprints": [
            "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints",
        ],
        "symbols": [
            "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
        ],
        "python": [
            "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/Applications/KiCad/KiCad.app/Contents/MacOS/python",
        ],
    },
}

_ENV_OVERRIDE = {
    "kicad_cli": "KICAD_BIN",
    "footprints": "KICAD_LIB_ROOT",
    "symbols": "KICAD_SYMBOL_ROOT",
    "python": "KICAD_PYTHON_PATH",
}


def _first_existing(paths: list[str]) -> str:
    for p in paths:
        if not p:
            continue
        if os.path.isfile(p) or os.path.isdir(p):
            return p
    return ""


@lru_cache(maxsize=1)
def kicad_paths() -> dict[str, str]:
    """Return a dict of well-known KiCad locations for the current env.

    Keys: ``"kicad_cli"``, ``"footprints"``, ``"symbols"``, ``"python"``.
    Each value is the first existing candidate, or ``""`` if none found.

    Resolution order per key:

      1. Environment variable override (see :data:`_ENV_OVERRIDE`).
      2. Bundled per-environment defaults, newest KiCad version first.

    The result is cached for the process lifetime. Pass overrides as env
    vars to influence detection.
    """
    env = detect_environment()
    out: dict[str, str] = {}
    for key, candidates in _KICAD_CANDIDATES[env].items():
        override = os.environ.get(_ENV_OVERRIDE[key], "").strip()
        ordered = ([override] if override else []) + candidates
        out[key] = _first_existing(ordered)
    return out


def kicad_lib_root() -> str:
    """Convenience: footprint library root for the current env (``""`` if none)."""
    return kicad_paths()["footprints"]


def kicad_cli() -> str:
    """Convenience: kicad-cli binary path for the current env (``""`` if none)."""
    return kicad_paths()["kicad_cli"]
