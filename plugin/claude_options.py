# SPDX-License-Identifier: GPL-3.0-or-later
"""Sensible Claude-CLI switches for the chat panel's options dropdown.

The free-text options field takes raw ``claude`` switches — powerful, but
nobody remembers them. This module feeds a dropdown next to it: a CURATED list
of switches that make sense for a headless board-chat turn (model choice, fast
mode, fallback model), **dynamically filtered against the installed CLI** —
``claude --help`` is parsed once and only switches the binary actually
understands are offered, so the dropdown can never insert a flag that makes
the turn die with "unknown option". Selecting an entry merges the switch into
the text field (replacing an existing value of the same flag, so picking
"Modell: Opus" after "Modell: Sonnet" swaps instead of duplicating).

Pure logic (parsing + string merging; injectable runner) — unit-testable
headless. The wx dropdown lives in ``chat_dialog``.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Optional

from .claude_bridge import hidden_console_kwargs

HELP_TIMEOUT_S = 20.0

# One dropdown entry: (label shown to the user, the switch it inserts).
# Only offered when the installed CLI's --help actually lists the flag.
# Deliberately SHORT — this is "sinnvolle Schalter", not the full CLI surface:
# transport/permissions/output flags are owned by claude_bridge.build_command
# and must not be user-overridable here.
CURATED: tuple[tuple[str, str], ...] = (
    ("Modell: Sonnet (schnell, günstig)", "--model sonnet"),
    ("Modell: Opus (stärkste Qualität)", "--model opus"),
    ("Modell: Haiku (am schnellsten)", "--model haiku"),
    ("Fast-Modus (Opus mit schneller Ausgabe)", "--fast"),
    ("Fallback: bei Überlastung auf Sonnet", "--fallback-model sonnet"),
)

# Flags claude_bridge.build_command already sets — offering them again would
# produce duplicate argv entries with undefined precedence.
RESERVED_FLAGS = frozenset({
    "--mcp-config", "--strict-mcp-config", "--dangerously-skip-permissions",
    "--disallowedTools", "--append-system-prompt", "--output-format",
    "--verbose", "--max-turns", "--resume", "-p",
})

_FLAG_RE = re.compile(r"(--[A-Za-z][\w-]*)")


def parse_supported_flags(help_text: str) -> set:
    """Every ``--flag`` mentioned in a ``claude --help`` output."""
    return set(_FLAG_RE.findall(help_text or ""))


def switch_flag(switch: str) -> str:
    """``"--model sonnet"`` → ``"--model"`` (the flag word of a switch)."""
    parts = switch.split()
    return parts[0] if parts else ""


def available_options(help_text: str) -> list:
    """The dropdown entries: curated ∩ actually supported by this CLI.

    An empty/unreadable help text yields [] — the dropdown then simply stays
    hidden and the free-text field keeps working (graceful degradation).
    """
    supported = parse_supported_flags(help_text)
    out = []
    for label, switch in CURATED:
        flag = switch_flag(switch)
        if flag in supported and flag not in RESERVED_FLAGS:
            out.append((label, switch))
    return out


def apply_switch(current: str, switch: str) -> str:
    """Merge ``switch`` into the free-text options ``current``.

    An existing occurrence of the same flag (with or without value) is
    REPLACED — picking "Modell: Opus" after "Modell: Sonnet" swaps the value
    instead of appending a duplicate. Unrelated switches are preserved.
    Unparseable current text is left alone and the switch appended.
    """
    switch = (switch or "").strip()
    if not switch:
        return (current or "").strip()
    flag = switch_flag(switch)
    try:
        tokens = shlex.split((current or "").strip())
    except ValueError:  # unbalanced quotes — don't make it worse
        return ((current or "").strip() + " " + switch).strip()
    kept, i = [], 0
    while i < len(tokens):
        if tokens[i] == flag:
            # skip the flag and its value (if the next token isn't a flag)
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        kept.append(tokens[i])
        i += 1
    return " ".join(kept + shlex.split(switch))


def read_help_text(claude_cmd: list, _run=subprocess.run) -> str:
    """``claude --help`` output (stdout+stderr), or ``""`` on any failure.

    Never raises — a missing/broken CLI just means an empty dropdown.
    """
    if not claude_cmd:
        return ""
    try:
        proc = _run(list(claude_cmd) + ["--help"], capture_output=True,
                    text=True, timeout=HELP_TIMEOUT_S, check=False,
                    **hidden_console_kwargs())
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        return ""


_HELP_CACHE: dict = {}


def cached_options(claude_cmd: Optional[list], _run=subprocess.run) -> list:
    """``available_options`` for this CLI, computed once per session."""
    key = tuple(claude_cmd or ())
    if key not in _HELP_CACHE:
        _HELP_CACHE[key] = available_options(
            read_help_text(list(key), _run=_run))
    return _HELP_CACHE[key]
