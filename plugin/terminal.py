# SPDX-License-Identifier: GPL-3.0-or-later
"""Open a VISIBLE OS terminal that runs commands and STAYS open — robustly.

The naive ``cmd /k "<complex command>"`` passed as an argv LIST gets mangled by
Windows quoting: embedded quotes become ``\\"``, which cmd.exe misparses, so a
command containing quotes or ``|`` (e.g. ``irm … | iex``) breaks and the window
flashes shut. The fix: on Windows write the commands to a temp ``.bat`` and
launch THAT (no nested argv quoting); the .bat ends with ``pause`` so it stays
open and the user can read the result. POSIX is a dev fallback (bash).

``build_bat`` is pure (unit-tested); ``open_terminal`` does the I/O + spawn.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

_DONE = ["echo.",
         "echo ============================================",
         "echo Fertig - Fenster schliessen und im Plugin auf 'Erneut pruefen'.",
         "echo ============================================",
         "pause"]


def build_bat(commands, title="", cwd=None) -> str:
    """The .bat body: optional title + cd, the commands, then a pause."""
    lines = ["@echo off", "chcp 65001 >nul"]
    if title:
        lines.append("title " + title)
    if cwd:
        lines.append('cd /d "%s"' % cwd)
    lines += list(commands)
    lines += _DONE
    return "\r\n".join(lines) + "\r\n"


def _write_temp_bat(text: str) -> str:
    # UTF-8 (no BOM) to match the batch's own `chcp 65001` — an ASCII write
    # would replace non-ASCII path chars (e.g. ü in C:\Users\Schüler) with "?",
    # an INVALID Windows path char → pip's makedirs fails (WinError 123).
    fd, path = tempfile.mkstemp(suffix=".bat", prefix="kicad_claude_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def open_terminal(commands, title="", cwd=None,
                  _popen=subprocess.Popen, _writer=_write_temp_bat):
    """Spawn a visible terminal running ``commands``; returns the process.
    Raises on failure (the caller surfaces the error)."""
    if os.name == "nt":
        path = _writer(build_bat(commands, title, cwd))
        # start "<title>" "<bat>": new console; the .bat's pause keeps it open.
        return _popen(["cmd.exe", "/c", "start", title or "", path])  # noqa: S603
    inner = " && ".join((['cd "%s"' % cwd] if cwd else []) + list(commands))
    return _popen(["bash", "-lc", inner])  # noqa: S603
