# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the OFFICIAL Claude Code installer — visibly, on explicit consent.

The onboarding "Installieren" button uses this. We deliberately do NOT pipe a
remote script to a shell silently: the wx panel first shows the exact official
command + source, and only on confirm do we open a *visible* terminal that runs
it (so the user watches progress and sees errors). Sources (verified):
https://code.claude.com/docs/en/setup — native installer one-liners.

Pure logic (command builders, no wx/KiCad); unit-testable headless.
"""

from __future__ import annotations

import os

# Official setup docs (fallback if the user declines the automated install).
INSTALL_DOCS_URL = "https://code.claude.com/docs/en/setup"

# Official native-installer one-liners (auto-updating binary -> ~/.local/bin).
_PS1 = "irm https://claude.ai/install.ps1 | iex"
_SH = "curl -fsSL https://claude.ai/install.sh | bash"


def install_command_text() -> str:
    """The official command for the current OS, shown in the consent dialog."""
    return _PS1 if os.name == "nt" else _SH


def build_install_terminal_cmd() -> list:
    """A command that opens a VISIBLE terminal running the official installer.

    Windows: a new console runs the PowerShell one-liner, then pauses so the
    user can read the result. POSIX (dev): runs the shell one-liner.
    """
    if os.name == "nt":
        inner = (
            'powershell -NoProfile -ExecutionPolicy Bypass -Command '
            '"irm https://claude.ai/install.ps1 | iex" && '
            'echo. && echo Fertig - dann hier auf Erneut pruefen. && pause'
        )
        return ["cmd.exe", "/c", "start", "Claude Code installieren",
                "cmd", "/k", inner]
    return ["bash", "-lc", _SH]
