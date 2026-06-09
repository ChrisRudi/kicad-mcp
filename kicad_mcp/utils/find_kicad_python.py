# SPDX-License-Identifier: GPL-3.0-or-later
"""
Auto-detect KiCad's bundled Python interpreter across platforms.

KiCad ships its own Python with pcbnew/eeschema bindings pre-installed.
This module locates that interpreter so the MCP server can re-launch itself
under the correct Python if needed.
"""

import os
import platform
import re


def _windows_to_wsl_path(win_path: str) -> str | None:
    """Convert C:\\... to /mnt/c/... for WSL."""
    match = re.match(r"^([A-Za-z]):\\(.*)$", win_path)
    if not match:
        return None
    drive = match.group(1).lower()
    rest = match.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def find_kicad_python() -> str | None:
    """
    Locate the KiCad-bundled Python interpreter.

    Search order:
      1. KICAD_PYTHON_PATH environment variable
      2. Derive from KICAD_INSTALL_DIR  (e.g. .../KiCad/10.0 → bin/python.exe)
      3. Platform-specific common installation paths

    Returns the absolute path usable on the *current* runtime (WSL-aware),
    or None if not found.
    """
    system = platform.system()

    # --- 1. Explicit env var ---
    env_path = os.environ.get("KICAD_PYTHON_PATH")
    if env_path:
        resolved = _resolve_path(env_path, system)
        if resolved:
            return resolved

    # --- 2. Derive from KICAD_INSTALL_DIR ---
    install_dir = os.environ.get("KICAD_INSTALL_DIR")
    if install_dir:
        candidates = _python_candidates_from_install_dir(install_dir, system)
        for c in candidates:
            resolved = _resolve_path(c, system)
            if resolved:
                return resolved

    # --- 3. Platform-specific common paths ---
    for path in _get_common_python_paths(system):
        resolved = _resolve_path(path, system)
        if resolved:
            return resolved

    return None


def _python_candidates_from_install_dir(install_dir: str, system: str) -> list[str]:
    """Build candidate Python paths from a KiCad installation directory."""
    candidates = []
    if system == "Windows":
        candidates.append(os.path.join(install_dir, "bin", "python.exe"))
    elif system == "Darwin":
        # macOS: Python inside the app bundle
        candidates.append(
            os.path.join(
                install_dir,
                "Contents",
                "Frameworks",
                "Python.framework",
                "Versions",
                "Current",
                "bin",
                "python3",
            )
        )
        candidates.append(os.path.join(install_dir, "Contents", "MacOS", "python"))
    else:
        # Linux / WSL — install_dir might be a Windows or Linux path
        candidates.append(os.path.join(install_dir, "bin", "python.exe"))
        candidates.append(os.path.join(install_dir, "bin", "python3"))
    return candidates


def _get_common_python_paths(system: str) -> list[str]:
    """Return well-known KiCad Python locations per platform.

    KiCad 10+ only — pre-10 lacks the IPC API the server depends on.
    """
    if system == "Windows":
        return [
            r"C:\Program Files\KiCad\10.0\bin\python.exe",
            r"C:\Program Files (x86)\KiCad\10.0\bin\python.exe",
        ]
    elif system == "Darwin":
        return [
            "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/Applications/KiCad/KiCad.app/Contents/MacOS/python",
        ]
    else:
        # Linux + WSL
        return [
            "/mnt/c/Program Files/KiCad/10.0/bin/python.exe",
            "/usr/bin/python3",
        ]


def _resolve_path(path: str, system: str) -> str | None:
    """Check if *path* (or its WSL translation) is a valid executable."""
    candidates = [path]
    if system != "Windows":
        wsl = _windows_to_wsl_path(path)
        if wsl and wsl not in candidates:
            candidates.append(wsl)

    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def is_kicad_python() -> bool:
    """Return True if the *running* interpreter has pcbnew available."""
    try:
        return True
    except ImportError:
        return False
