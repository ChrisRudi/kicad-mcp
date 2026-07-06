# SPDX-License-Identifier: GPL-3.0-or-later
"""
Shared constants for schematic and PCB generators.

Extracted from auto_place.py, schematic_builder.py, pcb_router.py, pcb_builder.py.

Callers:
  - auto_place.py          (GRID, HALF_GRID, SHEET_*, MARGIN, *_GAP, IC_*, Y_*, OVERLAP_*)
  - schematic_builder.py   (WIRE_MAX_*, WIRE_CLEARANCE, POWER_CLEARANCE, LABEL_STUB_LEN)
  - pcb_builder.py         (JLCPCB_RULES, EURO_DIVIDER_SIZES)
  - pcb_router.py          (POWER_TRACE_W, SIGNAL_TRACE_W, VIA_*, TRACE_CLEARANCE, LAYER_*)
  - common/geometry.py     (HALF_GRID)
  - common/bbox.py         (GRID, HALF_GRID)
  - common/fd_refine.py    (MARGIN, SHEET_*, Y_CENTER)
  - common/routing.py      (HALF_GRID)
"""

from dataclasses import dataclass
import os


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.5, value)


# ---------------------------------------------------------------------------
# Sheet geometry
# ---------------------------------------------------------------------------

@dataclass
class SheetConfig:
    width: float = 270.0
    height: float = 180.0
    margin: float = 25.4


SHEET_W = 270.0
SHEET_H = 180.0
MARGIN = 25.4

# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

@dataclass
class PlacementConfig:
    inline_gap: float = 15.0
    vertical_gap: float = 20.0
    overlap_margin: float = 6.0
    overlap_passes: int = 10
    grid: float = 2.54
    half_grid: float = 1.27


GRID = 2.54
HALF_GRID = 1.27
_RAW_SCH_FACTOR = _env_float("KICAD_SCH_FACTOR", 0.0)  # 0 = auto
# „Einfach mehr Luft lassen": 1.4 → 1.7. Die Platzierung setzte Kondensatoren
# ohne Not mitten ins Gewühl (BME280 so nah am MCU, dass Netz-Beschriftungen
# keinen Platz hatten). Die crowding-Metrik wacht darüber, der Faktor sorgt
# vor. Übersteuerbar per KICAD_SCH_FACTOR.
SCHEMATIC_LAYOUT_FACTOR = _RAW_SCH_FACTOR if _RAW_SCH_FACTOR > 0 else 1.8


INLINE_GAP = 15.0 * SCHEMATIC_LAYOUT_FACTOR
VERTICAL_GAP = 20.0 * SCHEMATIC_LAYOUT_FACTOR
OVERLAP_MARGIN = 6.0
OVERLAP_PASSES = 10

# Derived placement positions
IC_X = SHEET_W / 2 - 15   # slightly left of center — leaves room for output connectors
IC_Y = SHEET_H / 2

# Backward-compatible aliases
SYMBOL_GAP = INLINE_GAP
GROUP_GAP = VERTICAL_GAP
Y_TOP = MARGIN + 10
Y_CENTER = SHEET_H / 2
Y_BOTTOM = SHEET_H - MARGIN - 10

# ---------------------------------------------------------------------------
# Routing (schematic)
# ---------------------------------------------------------------------------

@dataclass
class RoutingConfig:
    route_grid: float = 1.27
    wire_clearance: float = 3.0
    power_clearance: float = 4.5
    bend_cost: float = 3.0
    # PCB-specific
    trace_clearance: float = 0.2
    power_trace_w: float = 0.5
    signal_trace_w: float = 0.25


WIRE_MAX_PINS = max(20, int(round(20 * SCHEMATIC_LAYOUT_FACTOR)))
WIRE_MAX_LENGTH = 200.0 * SCHEMATIC_LAYOUT_FACTOR
WIRE_CLEARANCE = 2.0
POWER_CLEARANCE = 3.0
# Labels bekommen — wie alle Bauteile — eine sichtbare 5-mm-Leitung (2 Grid),
# nicht nur einen 2.54-mm-Stummel (Nutzer-Regel „auch Labels benötigen 5 mm
# Leitung"). Der längere Stub schiebt das Label auch weiter von Bauteilen und
# Nachbar-Drähten weg.
LABEL_STUB_LEN = 5.08

# Jeder verdrahtete Pin bekommt eine kurze axiale Leitung (1 Grid) aus dem
# Bauteil HERAUS, bevor der A*-Draht abbiegt — die „Stubs an den ICs", die ein
# Profi-Schaltbild an jedem Pin hat. Zwei Zwecke: (1) sichtbarer, sauberer
# Anschluss-Stummel; (2) der A*-Router startet damit AUSSERHALB des Körpers, statt
# den Draht direkt am Pin durch den (eigenen, großen) Bauteil-Körper zu ziehen —
# genau die „lokalen Busse über die Bauteile", die entstanden, wenn zwei Pins
# desselben ICs über eine durch den Körper gefräste Gasse verbunden wurden.
PIN_STUB_LEN = 2.54

# ---------------------------------------------------------------------------
# PCB routing
# ---------------------------------------------------------------------------

POWER_TRACE_W = 0.5
SIGNAL_TRACE_W = 0.25
VIA_SIZE = 0.8
VIA_DRILL = 0.4
TRACE_CLEARANCE = 0.2
LAYER_F = "F.Cu"
LAYER_B = "B.Cu"

# ---------------------------------------------------------------------------
# PCB design rules (JLCPCB)
# ---------------------------------------------------------------------------

JLCPCB_RULES = {
    "min_track_width": 0.127,
    "min_clearance": 0.127,
    "min_via_diameter": 0.45,
    "min_via_drill": 0.2,
    "min_hole": 0.3,
}

EURO_DIVIDER_SIZES = {
    "3U": {"width": 100.0, "height": 128.4},
    "6U": {"width": 233.35, "height": 220.0},
    "half_euro": {"width": 80.0, "height": 100.0},
}
