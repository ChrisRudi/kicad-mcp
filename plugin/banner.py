# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure text builders for the chat panel's startup banner (Dok 2).

No wx, no kipy — every function here is a plain string/struct builder so the
panel start is unit-testable headless. The panel (``chat_dialog``) wires the
strings into the read-only output control and the click dispatch; the only new
external action is opening the ``mailto:`` href via the OS handler.
"""

from __future__ import annotations

from urllib.parse import urlencode

from . import i18n
from .i18n import tr

REPO_URL = "https://github.com/ChrisRudi/kicad-mcp"

_RECOMMEND_SUBJECT = "KiCad + Claude — das solltest du kennen"
_RECOMMEND_BODY = (
    "Hi! Ich nutze kicad-mcp — damit redet man im KiCad-PCB-Editor direkt mit "
    "Claude: Bauteile/Netze finden, markieren, Routing prüfen, ganze Blöcke aus "
    "Datenblättern bauen. Klickbare Links springen direkt aufs Element im "
    f"Editor. Open Source (GPL-3.0): {REPO_URL} — viel Spaß!"
)


def recommend_mailto() -> str:
    """A ``mailto:`` link (no recipient — the user fills in the friend) with a
    pre-filled, URL-encoded subject and body recommending the plugin.

    Pure; only opened via the OS ``mailto:`` handler on click, so there is no
    network call and no tracking. The empty recipient is intentional: the user
    addresses it to whoever they like."""
    query = urlencode({"subject": _RECOMMEND_SUBJECT, "body": _RECOMMEND_BODY})
    return "mailto:?" + query


# The interaction guide shown statically at panel start (Dok 2 §3.4). Kept here
# (not in wx) so the wording is testable and easy to edit. The "nicht möglich"
# lines mirror CLAUDE.md's KiCad-10 limits so the user never waits on something
# the API cannot do.
INTERACTION_GUIDE_LINES = (
    "So arbeitest du mit mir",
    "  • Orange unterstrichene Namen sind klickbar: R12, GND, F.Cu, U1.33,",
    "    (120.5, 84.0) → wählt + zoomt das Element im PCB-Editor.",
    "  • Markiere etwas im Editor und hak „🔗 Auswahl einbeziehen“ an, dann",
    "    frag „was ist das?“ — ohne die Referenz abzutippen.",
    "  • Rechtsklick auf einen Link: nur markieren / hinzoomen / Eigenschaften.",
    "  • Beispiele:  „wie viele GND-Vias?“  ·  „markier die 3 kleinsten Cs“",
    "  • ⚑ unten = Claude-Optionen (z. B. --model sonnet) · „Stopp“ bricht ab.",
    "  • Nicht möglich (KiCad 10): Hover/Mausposition, Schaltplan-Live-Links,",
    "    3D-Viewer-Steuerung.",
)

# Englische Fassung — 1:1-Zeilenäquivalent (1. Linux-Smoke zeigte den Block
# als letzte deutsche Insel im EN-Modus). Beim Sprachwechsel EN gewählt;
# fehlt eine Sprache, bleibt die deutsche Quelle.
_GUIDE_EN = (
    "How to work with me",
    "  • Orange underlined names are clickable: R12, GND, F.Cu, U1.33,",
    "    (120.5, 84.0) → selects + zooms the element in the PCB editor.",
    "  • Select something in the editor and tick „🔗 Include selection“, then",
    "    ask „what is this?“ — without typing the reference.",
    "  • Right-click a link: just highlight / zoom to / properties.",
    "  • Examples:  „how many GND vias?“  ·  „mark the 3 smallest Cs“",
    "  • ⚑ below = Claude options (e.g. --model sonnet) · „Stop“ cancels.",
    "  • Not possible (KiCad 10): hover/mouse position, live schematic links,",
    "    3D-viewer control.",
)


def interaction_guide() -> str:
    """The static interaction guide as one newline-joined block, in the GUI
    language (English fällt auf die deutsche Quelle zurück, wenn keine EN da)."""
    lines = _GUIDE_EN if i18n.get_lang() == "en" else INTERACTION_GUIDE_LINES
    return "\n".join(lines)


def summary_lines(summary: dict, extent_mm=None) -> list[str]:
    """Render :func:`board_links.board_summary` output into the banner's
    "Platine" block lines. ``extent_mm`` is an optional ``(w, h)`` tuple; its
    line is dropped when None (best-effort size couldn't be determined)."""
    layers = summary.get("layers") or []
    by_prefix = summary.get("by_prefix") or {}
    lines = [tr("Platine"),
             (f"  {tr('Footprints')}   {summary.get('footprints', 0)}"
              f"       {tr('Netze')}   {summary.get('nets', 0)}"
              f"       {tr('Lagen')}   {len(layers)}"
              + (f" ({', '.join(layers)})" if layers else ""))]
    if by_prefix:
        parts = "  ".join(f"{k}:{v}" for k, v in by_prefix.items())
        lines.append(f"  {tr('Bestückung')}   {parts}")
    if extent_mm and len(extent_mm) == 2:
        lines.append(f"  {tr('Größe')}        "
                     f"{extent_mm[0]} × {extent_mm[1]} mm"
                     "        " + tr("(ⓘ aus Edge.Cuts, best effort)"))
    return lines
