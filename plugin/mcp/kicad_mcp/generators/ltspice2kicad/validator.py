# SPDX-License-Identifier: GPL-3.0-or-later
# validator.py
"""Validation and report generation for rebuilt KiCad schematics."""
from __future__ import annotations

from kicad_mcp.generators.ltspice2kicad.models import (
    TransformedComponent,
    TransformedJunction,
    TransformedLabel,
    TransformedWire,
    ValidationResult,
)
from kicad_mcp.generators.ltspice2kicad.normalizer import KICAD_GRID_FINE


def _on_grid(value: float, grid: float = KICAD_GRID_FINE) -> bool:
    """Check if a value lies on the grid."""
    remainder = value / grid
    return abs(remainder - round(remainder)) < 1e-4


def validate(
    components: list[TransformedComponent],
    wires: list[TransformedWire],
    junctions: list[TransformedJunction],
    labels: list[TransformedLabel],
    confirmed_junction_count: int = 0,
    output_path: str = "",
) -> list[ValidationResult]:
    """Run all validation checks.

    Returns list of ValidationResult (ERROR, WARNING, OK).
    """
    results: list[ValidationResult] = []

    # --- Hard checks (12.1) ---

    # 1. Pin connectivity: all components have pins
    comps_without_pins = [c for c in components if not c.pins_abs and not c.is_power]
    if comps_without_pins:
        for c in comps_without_pins:
            results.append(ValidationResult(
                "ERROR", "pin_connectivity",
                f"Component {c.reference} ({c.type_ltspice}) has no pin positions",
            ))
    else:
        results.append(ValidationResult("OK", "pin_connectivity", "All components have pin positions"))

    # 2. Electrical pin connection: check wire endpoints touch pins
    pin_set: set[tuple[float, float]] = set()
    for comp in components:
        for px, py, _pn in comp.pins_abs:
            pin_set.add((round(px, 4), round(py, 4)))

    wire_end_set: set[tuple[float, float]] = set()
    for w in wires:
        wire_end_set.add((round(w.x1_mm, 4), round(w.y1_mm, 4)))
        wire_end_set.add((round(w.x2_mm, 4), round(w.y2_mm, 4)))

    unconnected_pins = pin_set - wire_end_set
    # Power symbols and NC pins may not have wires
    real_unconnected = []
    nc_pin_set: set[tuple[float, float]] = set()
    for comp in components:
        if comp.is_power:
            # Power symbol pins are OK to count as connected via symbol
            for px, py, _pn in comp.pins_abs:
                nc_pin_set.add((round(px, 4), round(py, 4)))
        for nc in comp.nc_pins:
            for px, py, pn in comp.pins_abs:
                if pn == nc:
                    nc_pin_set.add((round(px, 4), round(py, 4)))

    for pin_pos in unconnected_pins:
        if pin_pos not in nc_pin_set:
            real_unconnected.append(pin_pos)

    if real_unconnected:
        results.append(ValidationResult(
            "WARNING", "electrical_connection",
            f"{len(real_unconnected)} pin(s) not directly touched by wire endpoints",
        ))
    else:
        results.append(ValidationResult("OK", "electrical_connection", "All non-NC pins connected"))

    # 3. No open wire ends
    all_endpoints: set[tuple[float, float]] = set()
    for w in wires:
        all_endpoints.add((round(w.x1_mm, 4), round(w.y1_mm, 4)))
        all_endpoints.add((round(w.x2_mm, 4), round(w.y2_mm, 4)))

    label_positions = {(round(lb.x_mm, 4), round(lb.y_mm, 4)) for lb in labels}
    _junction_positions = {(round(j.x_mm, 4), round(j.y_mm, 4)) for j in junctions}

    open_ends = 0
    for w in wires:
        for ep in ((round(w.x1_mm, 4), round(w.y1_mm, 4)),
                   (round(w.x2_mm, 4), round(w.y2_mm, 4))):
            # Count how many wires share this endpoint
            count = sum(
                1 for w2 in wires
                if (round(w2.x1_mm, 4), round(w2.y1_mm, 4)) == ep
                or (round(w2.x2_mm, 4), round(w2.y2_mm, 4)) == ep
            )
            if ep not in pin_set and ep not in label_positions and count <= 1:
                open_ends += 1

    if open_ends > 0:
        results.append(ValidationResult(
            "WARNING", "open_wire_ends",
            f"{open_ends} wire endpoint(s) not connected to pin, junction, or other wire",
        ))
    else:
        results.append(ValidationResult("OK", "open_wire_ends", "No open wire ends"))

    # 4. Grid conformance
    off_grid_count = 0
    for comp in components:
        if not _on_grid(comp.x_mm) or not _on_grid(comp.y_mm):
            off_grid_count += 1
    for w in wires:
        for val in (w.x1_mm, w.y1_mm, w.x2_mm, w.y2_mm):
            if not _on_grid(val):
                off_grid_count += 1

    if off_grid_count > 0:
        results.append(ValidationResult(
            "ERROR", "grid_conformance",
            f"{off_grid_count} coordinate(s) not on {KICAD_GRID_FINE}mm grid",
        ))
    else:
        results.append(ValidationResult("OK", "grid_conformance", f"All coordinates on {KICAD_GRID_FINE}mm grid"))

    # 5. Power symbol reference check
    bad_power = [c for c in components if c.is_power and not c.reference.startswith("#")]
    if bad_power:
        results.append(ValidationResult(
            "ERROR", "power_reference",
            f"{len(bad_power)} power symbol(s) missing # prefix in reference",
        ))
    else:
        results.append(ValidationResult("OK", "power_reference", "All power refs start with #"))

    # 6. Float check — all final coordinates must have max 4 decimals
    def _check_decimals(v: float) -> bool:
        return abs(v - round(v, 4)) < 1e-9

    float_errors = 0
    for comp in components:
        if not _check_decimals(comp.x_mm) or not _check_decimals(comp.y_mm):
            float_errors += 1
    for w in wires:
        for val in (w.x1_mm, w.y1_mm, w.x2_mm, w.y2_mm):
            if not _check_decimals(val):
                float_errors += 1

    if float_errors > 0:
        results.append(ValidationResult(
            "ERROR", "float_precision",
            f"{float_errors} coordinate(s) exceed 4 decimal places",
        ))
    else:
        results.append(ValidationResult("OK", "float_precision", "All coordinates within 4 decimal places"))

    # 7. File validity check (if path given)
    if output_path:
        import os
        if os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.startswith("(kicad_sch") and content.rstrip().endswith(")"):
                    results.append(ValidationResult("OK", "file_validity", "Output is valid UTF-8 S-expression"))
                else:
                    results.append(ValidationResult("ERROR", "file_validity", "Output does not look like valid .kicad_sch"))
            except UnicodeDecodeError:
                results.append(ValidationResult("ERROR", "encoding", "Output is not valid UTF-8"))
        else:
            results.append(ValidationResult("ERROR", "file_validity", f"Output file not found: {output_path}"))

    # --- Warnings (12.2) ---

    # Overlap check
    for i, c1 in enumerate(components):
        for c2 in components[i + 1:]:
            if c1.is_power or c2.is_power:
                continue
            if abs(c1.x_mm - c2.x_mm) < 2.0 and abs(c1.y_mm - c2.y_mm) < 2.0:
                results.append(ValidationResult(
                    "WARNING", "overlap",
                    f"Components {c1.reference} and {c2.reference} may overlap"
                    f" (distance: {abs(c1.x_mm - c2.x_mm):.1f}, {abs(c1.y_mm - c2.y_mm):.1f}mm)",
                ))

    # Labels on wire/pin check
    for lb in labels:
        lp = (round(lb.x_mm, 4), round(lb.y_mm, 4))
        if lp not in all_endpoints and lp not in pin_set:
            results.append(ValidationResult(
                "WARNING", "label_placement",
                f"Label '{lb.name}' at ({lb.x_mm:.2f}, {lb.y_mm:.2f}) not on wire or pin",
            ))

    return results


def format_report(results: list[ValidationResult]) -> str:
    """Format validation results as text report."""
    lines: list[str] = []
    for r in results:
        lines.append(f"[{r.level}] {r.check}: {r.message}")
    errors = sum(1 for r in results if r.level == "ERROR")
    warnings = sum(1 for r in results if r.level == "WARNING")
    oks = sum(1 for r in results if r.level == "OK")
    lines.append(f"\nSummary: {errors} errors, {warnings} warnings, {oks} passed")
    return "\n".join(lines)
