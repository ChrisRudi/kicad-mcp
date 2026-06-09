# SPDX-License-Identifier: GPL-3.0-or-later
# models.py
"""Data classes for LTspice-to-KiCad geometry rebuilder."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Pin:
    """A symbol pin with position relative to symbol origin."""
    name: str       # logical name / label (e.g. "G", "D", "S")
    number: str     # physical pin number (e.g. "1", "2", "3")
    x: int          # relative to symbol origin
    y: int
    orientation: int = 0  # 0, 90, 180, 270


@dataclass
class PinVector:
    """Distance vector between two pins of a symbol."""
    pin_a: str      # pin name/label
    pin_b: str      # pin name/label
    dx: int         # relative distance x
    dy: int         # relative distance y


@dataclass
class SymbolMeta:
    """Metadata for a symbol in either LTspice or KiCad."""
    name: str
    width: int
    height: int
    origin_x: int = 0
    origin_y: int = 0
    pin_1_offset: tuple[int, int] = (0, 0)
    centroid_offset: tuple[int, int] = (0, 0)
    pins: list[Pin] = field(default_factory=list)
    pin_vectors: list[PinVector] = field(default_factory=list)
    supports_mirror: bool = True
    mirror_semantic: str = "safe"  # "safe", "restricted", "forbidden"


@dataclass
class Component:
    """A parsed LTspice component instance."""
    id: str
    type_ltspice: str
    x: int
    y: int
    rotation: int = 0       # 0, 90, 180, 270
    mirror: bool = False
    pins: list[Pin] = field(default_factory=list)
    value: str = ""
    reference: str = ""


@dataclass
class Wire:
    """A wire segment with two endpoints."""
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class Junction:
    """An explicit junction point from LTspice."""
    x: int
    y: int


@dataclass
class NetLabel:
    """A net label (flag) with position."""
    name: str
    x: int
    y: int
    orientation: int = 0  # 0, 90, 180, 270


@dataclass
class Net:
    """A logical net connecting pins."""
    name: str
    nodes: list[tuple[str, str]] = field(default_factory=list)  # (component_id, pin_name)


@dataclass
class ParsedSchematic:
    """Complete parsed LTspice schematic."""
    components: list[Component] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    junctions: list[Junction] = field(default_factory=list)
    labels: list[NetLabel] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)


@dataclass
class TransformedComponent:
    """A component after coordinate transformation to KiCad space (mm)."""
    id: str
    type_ltspice: str
    kicad_symbol: str     # e.g. "Device:R"
    x_mm: float
    y_mm: float
    rotation: int
    mirror: bool
    reference: str
    value: str
    footprint: str = ""
    pins_abs: list[tuple[float, float, str]] = field(default_factory=list)
    # (x_mm, y_mm, pin_number) - absolute pin positions after transform
    is_power: bool = False
    nc_pins: list[str] = field(default_factory=list)


@dataclass
class TransformedWire:
    """A wire after coordinate transformation to KiCad space (mm)."""
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float


@dataclass
class TransformedJunction:
    """A junction after coordinate transformation, only if topology-confirmed."""
    x_mm: float
    y_mm: float


@dataclass
class TransformedLabel:
    """A net label after coordinate transformation."""
    name: str
    x_mm: float
    y_mm: float
    orientation: int


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    level: str       # "ERROR", "WARNING", "OK"
    check: str       # check name
    message: str     # description


@dataclass
class RebuildResult:
    """Complete result of the rebuild pipeline."""
    success: bool
    output_path: str = ""
    scale_factor: int = 1
    lgu: int = 1
    component_count: int = 0
    wire_count: int = 0
    net_count: int = 0
    validation: list[ValidationResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
