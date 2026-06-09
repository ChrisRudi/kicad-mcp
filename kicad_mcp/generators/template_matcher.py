# SPDX-License-Identifier: GPL-3.0-or-later
"""
Template-based placement matcher.

Loads placement templates extracted from Elektor schematics and matches
them against a given circuit (parts + nets). When a template matches,
it provides placement coordinates that override the default Net-Chain algorithm.

Integration point: called as Phase 0 in auto_place.py, before Net-Chain.
"""

from dataclasses import dataclass
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Template directory ──────────────────────────────────────────────────────

_TEMPLATE_DIR = Path(__file__).parent.parent / "training" / "templates" / "schematic"
_WIRING_DIR = Path(__file__).parent.parent / "training" / "templates" / "wiring"


# ── Data structures ─────────────────────────────────────────────────────────

@dataclass
class TemplateMatch:
    template_id: str
    confidence: float
    matched_components: dict[str, str]  # ref → template_role
    placement: dict[str, tuple[float, float, int]]  # ref → (rx, ry, rotation)


@dataclass
class Template:
    template_id: str
    circuit_type: str
    match_rules: dict
    placement: dict
    wiring_hint: str = ""
    source_count: int = 0


# ── Template loading ────────────────────────────────────────────────────────

_templates_cache: list[Template] | None = None
_universal_rules_cache: dict | None = None
_wiring_rules_cache: dict | None = None


def load_templates(template_dir: Path | None = None) -> list[Template]:
    """Load all schematic placement templates."""
    global _templates_cache
    if _templates_cache is not None:
        return _templates_cache

    tdir = template_dir or _TEMPLATE_DIR
    templates = []

    if not tdir.is_dir():
        logger.warning("Template directory not found: %s", tdir)
        return []

    for f in sorted(tdir.glob("*.json")):
        if f.stem == "universal_placement_rules":
            continue  # loaded separately
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            templates.append(Template(
                template_id=data["template_id"],
                circuit_type=data.get("circuit_type", ""),
                match_rules=data.get("match_rules", {}),
                placement=data.get("placement", {}),
                wiring_hint=data.get("wiring_hint", ""),
                source_count=data.get("source_count", 0),
            ))
        except Exception as e:
            logger.error("Failed to load template %s: %s", f.name, e)

    _templates_cache = templates
    logger.info("Loaded %d placement templates", len(templates))
    return templates


def load_universal_rules(template_dir: Path | None = None) -> dict:
    """Load universal placement rules."""
    global _universal_rules_cache
    if _universal_rules_cache is not None:
        return _universal_rules_cache

    tdir = template_dir or _TEMPLATE_DIR
    rules_file = tdir / "universal_placement_rules.json"
    if rules_file.exists():
        _universal_rules_cache = json.loads(rules_file.read_text(encoding="utf-8"))
    else:
        _universal_rules_cache = {}
    return _universal_rules_cache


def load_wiring_rules(wiring_dir: Path | None = None) -> dict:
    """Load wiring rules (label vs wire decisions)."""
    global _wiring_rules_cache
    if _wiring_rules_cache is not None:
        return _wiring_rules_cache

    wdir = wiring_dir or _WIRING_DIR
    rules_file = wdir / "elektor_wiring_rules.json"
    if rules_file.exists():
        _wiring_rules_cache = json.loads(rules_file.read_text(encoding="utf-8"))
    else:
        _wiring_rules_cache = {}
    return _wiring_rules_cache


def clear_caches():
    """Clear template caches (for testing)."""
    global _templates_cache, _universal_rules_cache, _wiring_rules_cache
    _templates_cache = None
    _universal_rules_cache = None
    _wiring_rules_cache = None


# ── Matching logic ──────────────────────────────────────────────────────────

def _part_matches_rule(part: dict, rule: dict) -> bool:
    """Check if a part matches a template component rule."""
    match_patterns = rule.get("match", [])

    name = part.get("name", "").upper()
    value = part.get("value", "").upper()
    lib_id = part.get("lib_id", "").upper()
    ref = part.get("ref", "")
    prefix = "".join(c for c in ref if c.isalpha()).upper()
    combined = f"{name} {value} {lib_id}"

    if match_patterns:
        return any(pat.upper() in combined for pat in match_patterns)

    # No explicit match patterns — use type-based matching
    rule_type = rule.get("type", "").lower()
    if "npn" in rule_type:
        return prefix == "Q" and "PNP" not in combined
    if "pnp" in rule_type:
        return prefix == "Q" and "PNP" in combined
    if "transistor" in rule_type:
        return prefix == "Q"
    if "mosfet" in rule_type:
        return prefix in ("Q", "T") and any(k in combined for k in ("FET", "MOS", "IRF"))
    if "inductor" in rule_type:
        return prefix == "L"
    if "diode" in rule_type:
        return prefix == "D"
    if "capacitor" in rule_type:
        return prefix == "C"
    if "resistor" in rule_type:
        return prefix == "R"

    return False


def _count_matching_parts(parts: list[dict], rule: dict) -> list[dict]:
    """Find all parts matching a component rule."""
    matched = []
    for part in parts:
        if _part_matches_rule(part, rule):
            matched.append(part)

    count_min = rule.get("count_min", 1)
    if len(matched) >= count_min:
        return matched
    return []


def match_template(
    parts: list[dict],
    nets: list[dict],
    template: Template,
) -> TemplateMatch | None:
    """Try to match a single template against the circuit.

    Returns TemplateMatch if confidence >= threshold, else None.
    """
    rules = template.match_rules
    min_confidence = rules.get("min_confidence", 0.5)

    required = rules.get("required_components", [])
    optional = rules.get("optional_components", [])

    # Check required components
    matched_components: dict[str, str] = {}
    required_score = 0

    for rule in required:
        matching_parts = _count_matching_parts(parts, rule)
        if not matching_parts:
            return None  # required component missing → no match
        required_score += 1
        rule_type = rule.get("type", "unknown")
        for p in matching_parts:
            matched_components[p["ref"]] = rule_type

    if not required:
        return None

    # Check optional components (boost confidence)
    optional_score = 0
    if isinstance(optional, list):
        for opt in optional:
            if isinstance(opt, dict):
                if _count_matching_parts(parts, opt):
                    optional_score += 1
            elif isinstance(opt, str):
                # Simple string match against any part
                for part in parts:
                    combined = f"{part.get('name', '')} {part.get('value', '')} {part.get('ref', '')}".upper()
                    if opt.upper() in combined:
                        optional_score += 1
                        break

    # Calculate confidence
    # All required matched → base confidence 0.6, optional boosts up to 1.0
    base_confidence = 0.6
    optional_max = max(len(optional), 1) if isinstance(optional, list) else 1
    optional_bonus = 0.4 * (optional_score / optional_max) if optional_max > 0 else 0
    confidence = min(1.0, base_confidence + optional_bonus)

    if confidence < min_confidence:
        return None

    # Generate placement coordinates
    placement = _compute_template_placement(
        parts, matched_components, template.placement
    )

    return TemplateMatch(
        template_id=template.template_id,
        confidence=confidence,
        matched_components=matched_components,
        placement=placement,
    )


def match_templates(
    parts: list[dict],
    nets: list[dict],
    templates: list[Template] | None = None,
) -> list[TemplateMatch]:
    """Match all templates against the circuit.

    Returns list of matches sorted by confidence (highest first).
    """
    if templates is None:
        templates = load_templates()

    matches = []
    for template in templates:
        m = match_template(parts, nets, template)
        if m:
            matches.append(m)

    matches.sort(key=lambda m: -m.confidence)
    return matches


# ── Placement computation ───────────────────────────────────────────────────

# Sheet dimensions (must match auto_place.py)
_SHEET_W = 270.0
_SHEET_H = 180.0
_MARGIN = 25.4
_HALF_GRID = 1.27


def _snap(val: float) -> float:
    return round(val / _HALF_GRID) * _HALF_GRID


def _rx_to_x(rx: float) -> float:
    """Convert relative x (0-1) to absolute mm coordinate."""
    usable = _SHEET_W - 2 * _MARGIN
    return _snap(_MARGIN + rx * usable)


def _ry_to_y(ry: float) -> float:
    """Convert relative y (0-1) to absolute mm coordinate."""
    usable = _SHEET_H - 2 * _MARGIN
    return _snap(_MARGIN + ry * usable)


def _compute_template_placement(
    parts: list[dict],
    matched_components: dict[str, str],
    template_placement: dict,
) -> dict[str, tuple[float, float, int]]:
    """Compute placement coordinates from template regions.

    For each matched component, find its role and look up the
    template region to get (rx, ry) range, then place within that range.
    """
    result: dict[str, tuple[float, float, int]] = {}
    regions = template_placement.get("regions", {})
    rotations = template_placement.get("component_rotation", {})
    defaults = _get_universal_defaults()

    # Group matched parts by role
    role_parts: dict[str, list[dict]] = {}
    ref_to_part = {p["ref"]: p for p in parts}
    for ref, role in matched_components.items():
        role_parts.setdefault(role, []).append(ref_to_part.get(ref, {"ref": ref}))

    # For each role, find the best matching region
    for role, role_refs in role_parts.items():
        region = _find_region_for_role(role, regions)
        if not region:
            continue

        rx_min, rx_max = region.get("rx_range", [0.3, 0.6])
        ry_min, ry_max = region.get("ry_range", [0.3, 0.6])

        # Distribute multiple parts within the region
        n = len(role_refs)
        for i, part in enumerate(role_refs):
            if n == 1:
                rx = (rx_min + rx_max) / 2
                ry = (ry_min + ry_max) / 2
            else:
                # Spread evenly within region
                t = i / max(n - 1, 1)
                rx = rx_min + t * (rx_max - rx_min)
                ry = ry_min + t * (ry_max - ry_min)

            prefix = "".join(c for c in part["ref"] if c.isalpha())
            rotation = rotations.get(prefix, 0)

            result[part["ref"]] = (_rx_to_x(rx), _ry_to_y(ry), rotation)

    # Also place unmatched parts using universal defaults
    for part in parts:
        if part["ref"] not in result and part["ref"] not in matched_components:
            default_pos = _get_default_position(part, defaults)
            if default_pos:
                result[part["ref"]] = default_pos

    return result


def _find_region_for_role(role: str, regions: dict) -> dict | None:
    """Find the best template region for a component role."""
    role_lower = role.lower()

    # Direct match
    if role_lower in regions:
        return regions[role_lower]

    # Fuzzy match — look for role keywords in region names
    for region_name, region_data in regions.items():
        rn = region_name.lower()
        if role_lower in rn or rn in role_lower:
            return region_data

    # Keyword mapping
    keyword_map = {
        "timer": ["oscillator", "timer"],
        "counter": ["counter", "decoder"],
        "mcu": ["mcu", "microcontroller"],
        "opamp": ["opamp", "comparator", "signal_conditioning"],
        "transistor": ["driver", "npn", "pnp"],
        "mosfet": ["driver", "switch", "mosfet"],
        "sensor": ["sensor", "input"],
        "regulator": ["power", "regulator"],
        "switching_regulator": ["regulator_ic", "pwm"],
        "inductor": ["inductor"],
        "diode_schottky": ["catch_diode"],
        "relay": ["actuator", "output"],
        "motor": ["actuator", "output"],
        "led": ["output", "indicator", "load"],
        "buzzer": ["output", "actuator"],
        "connector": ["input", "output", "connector"],
        "current_sensor": ["sensor"],
        "level_shifter": ["rs232", "interface"],
        "lcd": ["display"],
        "voltage_reference": ["reference_input", "reference"],
        "pwm_controller": ["regulator_ic", "pwm"],
    }

    keywords = keyword_map.get(role_lower, [])
    for kw in keywords:
        for region_name, region_data in regions.items():
            if kw in region_name.lower():
                return region_data

    return None


def _get_universal_defaults() -> dict:
    """Get universal component placement defaults."""
    rules = load_universal_rules()
    return rules.get("component_placement_defaults", {})


def _get_default_position(part: dict, defaults: dict) -> tuple[float, float, int] | None:
    """Get default position for a part from universal rules."""
    from .common.classify import _classify

    group = _classify(part)

    if group in defaults:
        d = defaults[group]
        rx = d.get("rx", 0.5)
        ry = d.get("ry", 0.5)
        return (_rx_to_x(rx), _ry_to_y(ry), 0)

    return None


# ── Wiring decision helper ──────────────────────────────────────────────────

def should_use_label(
    net: dict,
    parts: list[dict],
    distance_mm: float | None = None,
) -> bool:
    """Decide whether a net connection should use a label or direct wire.

    Based on Elektor wiring rules analysis:
    - Power nets: always label
    - Simple circuits (<= 10 parts): always wire
    - MCU bus signals: label if MCU has >= 20 pins
    - Distance > 60mm: label
    - Otherwise: direct wire
    """
    rules = load_wiring_rules()
    lv = rules.get("label_vs_wire", {})

    net_name = net.get("name", "").upper()
    net_type = net.get("type", "signal")

    # Power nets: always label
    if net_type == "power":
        return True

    # Check if any "always_label" category applies
    always_label = lv.get("always_label", [])
    if "power_nets" in always_label and net_type == "power":
        return True
    if "ground_nets" in always_label and any(g in net_name for g in ("GND", "VSS", "AGND")):
        return True

    # Distance threshold — checked early, overrides circuit simplicity
    threshold_mm = lv.get("distance_threshold_mm", 90)
    if distance_mm is not None and distance_mm > threshold_mm:
        return True

    # MCU with many pins: label bus signals — but only if distance is long
    # Short connections near a big MCU should still be wired for clarity.
    if distance_mm is not None and distance_mm <= threshold_mm:
        pass  # Short distance → always try to wire, even near big MCUs
    else:
        for ctx in lv.get("context_rules", []):
            pin_threshold = ctx.get("threshold_mcu_pins")
            if pin_threshold:
                for part in parts:
                    if len(part.get("pins", [])) >= pin_threshold:
                        connections = net.get("connections", [])
                        refs = {c.split(":")[0] for c in connections if ":" in c}
                        if len(refs) >= 2:
                            return True

    # Simple circuits: always wire (when distance is OK)
    component_count = len(parts)
    for ctx in lv.get("context_rules", []):
        threshold = ctx.get("threshold_components")
        if threshold and component_count <= threshold:
            return False

    # Default: direct wire
    return False
