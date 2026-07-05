# SPDX-License-Identifier: GPL-3.0-or-later
# parser.py
"""Parse LTspice .asc files (Windows-1252) into internal graph."""
from __future__ import annotations

from kicad_mcp.generators.ltspice2kicad.models import (
    Component,
    Junction,
    NetLabel,
    ParsedSchematic,
    Wire,
)


def _parse_rotation(rot_str: str) -> tuple[int, bool]:
    """Parse LTspice rotation string like 'R0', 'R90', 'M0', 'M180'.

    Returns (angle, mirror).
    """
    if not rot_str:
        return 0, False
    mirror = rot_str.startswith("M")
    digits = rot_str.lstrip("RMrm")
    angle = int(digits) if digits.isdigit() else 0
    if angle not in (0, 90, 180, 270):
        angle = 0
    return angle, mirror


def parse_asc(filepath: str) -> ParsedSchematic:
    """Parse an LTspice .asc file into a ParsedSchematic.

    Reads with Windows-1252 encoding as per LTspice convention.
    Extracts: SYMBOL, WIRE, FLAG, IOPIN, TEXT, junction markers.
    Ignores: SPICE directives (.tran, .ac, .param, etc.), comments.
    """
    components: list[Component] = []
    wires: list[Wire] = []
    junctions: list[Junction] = []
    labels: list[NetLabel] = []
    texts: list[str] = []

    current_comp: Component | None = None
    comp_counter = 0

    with open(filepath, encoding="cp1252", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\r\n")
            parts = line.split()
            if not parts:
                continue

            cmd = parts[0].upper()

            if cmd == "WIRE" and len(parts) >= 5:
                try:
                    wires.append(Wire(
                        x1=int(parts[1]), y1=int(parts[2]),
                        x2=int(parts[3]), y2=int(parts[4]),
                    ))
                except ValueError:
                    # fehlerhafte WIRE-Koordinaten — Zeile tolerant überspringen
                    pass

            elif cmd == "SYMBOL" and len(parts) >= 4:
                # Save previous component
                comp_counter += 1
                rot_str = parts[4] if len(parts) > 4 else "R0"
                angle, mirror = _parse_rotation(rot_str)
                try:
                    current_comp = Component(
                        id=f"U{comp_counter}",
                        type_ltspice=parts[1],
                        x=int(parts[2]),
                        y=int(parts[3]),
                        rotation=angle,
                        mirror=mirror,
                    )
                    components.append(current_comp)
                except ValueError:
                    current_comp = None

            elif cmd == "SYMATTR" and current_comp and len(parts) >= 3:
                attr_name = parts[1]
                attr_value = " ".join(parts[2:])
                if attr_name == "InstName":
                    current_comp.reference = attr_value
                elif attr_name == "Value":
                    current_comp.value = attr_value
                elif attr_name == "Value2":
                    # Secondary value (e.g. SPICE model) - store in value if empty
                    if not current_comp.value:
                        current_comp.value = attr_value

            elif cmd == "FLAG" and len(parts) >= 4:
                try:
                    labels.append(NetLabel(
                        name=parts[3],
                        x=int(parts[1]),
                        y=int(parts[2]),
                    ))
                except ValueError:
                    # fehlerhafte FLAG-Koordinaten — Zeile tolerant überspringen
                    pass

            elif cmd == "IOPIN" and len(parts) >= 3:
                # I/O pin marker - treated similar to flag
                pass

            elif cmd == "TEXT" and len(parts) >= 5:
                # Collect text but skip SPICE directives
                text_content = " ".join(parts[4:]).lstrip(";").strip()
                if text_content and not text_content.startswith("."):
                    texts.append(text_content)

            # LTspice uses "DATAFLAG" for junctions in some versions,
            # but typically junctions are implicit at wire crossings.
            # We detect them in topology.py from wire connectivity.

    # Assign references if missing
    type_counters: dict[str, int] = {}
    for comp in components:
        if not comp.reference:
            base = comp.type_ltspice.split("/")[-1].split("\\")[-1]
            prefix = _guess_prefix(base)
            type_counters[prefix] = type_counters.get(prefix, 0) + 1
            comp.reference = f"{prefix}{type_counters[prefix]}"

    return ParsedSchematic(
        components=components,
        wires=wires,
        junctions=junctions,
        labels=labels,
        texts=texts,
    )


def _guess_prefix(lt_name: str) -> str:
    """Guess KiCad reference prefix from LTspice symbol name."""
    name_lower = lt_name.lower()
    if name_lower in ("res", "res2"):
        return "R"
    if name_lower in ("cap", "cap2", "polcap"):
        return "C"
    if name_lower in ("ind", "ind2"):
        return "L"
    if name_lower in ("diode", "schottky", "zener"):
        return "D"
    if name_lower in ("nmos", "pmos", "nmos3", "pmos3"):
        return "Q"
    if name_lower in ("npn", "pnp"):
        return "Q"
    if name_lower in ("voltage", "voltage2"):
        return "V"
    if name_lower in ("current", "current2"):
        return "I"
    if "opamp" in name_lower:
        return "U"
    return "U"
