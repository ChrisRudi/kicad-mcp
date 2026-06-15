# SPDX-License-Identifier: GPL-3.0-or-later
"""Open a VISIBLE OS terminal that runs commands and STAYS open — robustly.

The naive ``cmd /k "<complex command>"`` passed as an argv LIST gets mangled by
Windows quoting: embedded quotes become ``\\"``, which cmd.exe misparses, so a
command containing quotes or ``|`` (e.g. ``irm … | iex``) breaks and the window
flashes shut. The fix: on Windows write the commands to a temp ``.bat`` and
launch THAT (no nested argv quoting); the .bat ends with ``pause`` so it stays
open and the user can read the result. POSIX is a dev fallback (bash).

Non-ASCII paths (e.g. a Windows username ``Schüler``) must NEVER appear as
literal text in the .bat: cmd.exe parses the batch through the console codepage
and folds ``ü`` to ``?`` — an invalid path char that makes pip's ``makedirs``
die with ``WinError 123``. ``chcp 65001`` alone does NOT fix this reliably. The
robust answer is to carry such paths in the child's ENVIRONMENT block (Windows
passes it as UTF-16, immune to codepage folding) and reference them as
``%VAR%`` in the .bat — the batch text itself stays pure ASCII. The working
directory rides ``%KICAD_MCP_CWD%`` this way; callers pass any other path-vars
via ``open_terminal(env=...)`` (see ``deps.pip_install_env``).

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
    """The .bat body: optional title + cd, the commands, then a pause.

    The cwd is referenced via ``%KICAD_MCP_CWD%`` (set by ``open_terminal`` in
    the child env), NOT inlined: a non-ASCII path inlined here would be folded
    to ``?`` by cmd.exe's codepage. See the module docstring.
    """
    lines = ["@echo off", "chcp 65001 >nul"]
    if title:
        lines.append("title " + title)
    if cwd:
        lines.append('cd /d "%KICAD_MCP_CWD%"')
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


def open_terminal(commands, title="", cwd=None, env=None,
                  _popen=subprocess.Popen, _writer=_write_temp_bat):
    """Spawn a visible terminal running ``commands``; returns the process.
    Raises on failure (the caller surfaces the error).

    ``env`` adds variables to the child's environment (e.g. a path-carrying
    var the .bat references as ``%VAR%`` — see the module docstring). The cwd
    is exported as ``KICAD_MCP_CWD`` so a non-ASCII working dir survives.
    """
    child_env = dict(os.environ)
    if cwd:
        child_env["KICAD_MCP_CWD"] = cwd
    if env:
        child_env.update(env)
    if os.name == "nt":
        path = _writer(build_bat(commands, title, cwd))
        # start "<title>" "<bat>": new console; the .bat's pause keeps it open.
        # env passed UTF-16 by Windows -> %KICAD_MCP_DEPS% / %KICAD_MCP_CWD%
        # expand uncorrupted even for usernames like "Schüler".
        return _popen(["cmd.exe", "/c", "start", title or "", path],  # noqa: S603
                      env=child_env)
    inner = " && ".join((['cd "%s"' % cwd] if cwd else []) + list(commands))
    return _popen(["bash", "-lc", inner], env=child_env)  # noqa: S603
