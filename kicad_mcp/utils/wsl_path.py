# SPDX-License-Identifier: GPL-3.0-or-later
r"""
WSL path conversion utilities.

Converts between WSL paths (/mnt/c/...) and Windows paths (C:\...)
for use with kicad-cli.exe which requires Windows-style paths.
"""

import subprocess


def is_wsl() -> bool:
    """Check if running under Windows Subsystem for Linux."""
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except (FileNotFoundError, PermissionError):
        return False


_IS_WSL = is_wsl()


def to_windows_path(path: str) -> str:
    """Convert a WSL path to Windows path for kicad-cli.exe.

    Only converts if running under WSL. On native Windows/Linux, returns path unchanged.

    Args:
        path: File path (may be WSL /mnt/c/... style)

    Returns:
        Windows-style path if WSL, otherwise unchanged
    """
    if not _IS_WSL or not path.startswith("/mnt/"):
        return path

    try:
        result = subprocess.run(
            ["wslpath", "-w", path],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Fallback: manual conversion
    # /mnt/c/Users/... -> C:\Users\...
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1:
        drive = parts[2].upper()
        rest = "\\".join(parts[3:])
        return f"{drive}:\\{rest}"

    return path
