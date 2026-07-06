# SPDX-License-Identifier: GPL-3.0-or-later
"""Geteilte Board-Geometrie: Montagelöcher u. Ä.

Eine Quelle für Builder (emittiert die Loch-Footprints) UND Platzierung
(reserviert die Plätze als fixe Hindernisse) — vorher kannte nur der Builder
die Löcher, die Platzierung setzte Stecker mitten auf MH3 (hole_clearance-
Fehler der Demo-Board-Messlatte).
"""

from __future__ import annotations

MOUNTING_HOLE_OFFSET = 3.5   # mm von der Board-Kante
MOUNTING_HOLE_RADIUS = 1.6   # M3 (Bohrung 3.2)
# Platzbedarf rund ums Loch (Pad-Ring + Schraubenkopf) für die Platzierung
MOUNTING_HOLE_KEEPOUT = 7.0


def board_has_mounting_holes(board: dict, w: float, h: float) -> bool:
    """Dieselbe Regel wie der Builder: explizit per Spec, sonst ab 30×30 mm."""
    return bool(board.get("mounting_holes", w >= 30 and h >= 30))


def mounting_hole_positions(w: float, h: float) -> list[tuple[float, float]]:
    """Die vier Eck-Positionen, board-lokal (0..w / 0..h, ohne Offset)."""
    o = MOUNTING_HOLE_OFFSET
    return [(o, o), (w - o, o), (o, h - o), (w - o, h - o)]
