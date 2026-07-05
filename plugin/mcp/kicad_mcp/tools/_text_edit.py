# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemeinsamer Abschluss aller File-Edit-Tools (Konvention #4).

Jedes Mutations-Tool hat eine reine ``<tool>_text``-Companion; der Rest ist
immer gleich: Text holen, Companion aufrufen, bei Fehler das Result
durchreichen, bei Erfolg schreiben (board-open-Guard sitzt in
``write_text``), Effekt-Echo mit ``dry_run`` zurückgeben. Dieses Modul ist
die EINE Quelle für diesen Abschluss — statt desselben 7-Zeilen-Schwanzes
in jedem Tool-Body.

Pfad-Normalisierung + Existenz-Check bleiben bewusst IM Tool (Konvention #1,
von ``test_tool_audit`` als erste Body-Zeilen erzwungen).
"""

from __future__ import annotations

from typing import Any, Callable

from kicad_mcp.cache import get_text, write_text


def apply_text_edit(
    pcb_path: str,
    text_fn: Callable[..., tuple[str, dict[str, Any]]],
    dry_run: bool,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run ``text_fn(text, *args, **kwargs)`` against ``pcb_path`` and
    finish per the file-edit-tool contract. ``pcb_path`` must already be
    normalised and existence-checked by the calling tool."""
    text = get_text(pcb_path)
    new_text, result = text_fn(text, *args, **kwargs)
    if not result.get("success"):
        return result
    if not dry_run:
        write_text(pcb_path, new_text)
    return {"dry_run": dry_run, **result}
