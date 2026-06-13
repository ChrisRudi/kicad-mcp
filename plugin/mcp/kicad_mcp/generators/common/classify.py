# SPDX-License-Identifier: GPL-3.0-or-later
"""
Component classification, bypass-cap detection, and rotation assignment.

Extracted from auto_place.py.

Callers:
  - auto_place.py          (_classify, _classify_component, _is_bypass_cap, _assign_rotation,
                             _POSITIVE/_NEGATIVE/_GND_PATTERNS, _get_power_net_names)
  - pcb_builder.py         (via auto_place re-export: _classify_component,
                             _is_bypass_cap)
  - template_matcher.py    (via auto_place re-export: _classify)
  - schematic_scorer.py    (via auto_place re-export: _get_power_net_names, _*_PATTERNS)
  - common/fd_refine.py    (_classify_component — fuer PCB fixed-set)
"""

from collections import defaultdict
import re

# ── Keywords for connector sub-classification ───────────────────────────────

_OUTPUT_KEYWORDS = ("OUT", "SPEAKER", "SPK", "MOTOR", "DRIVE", "LOAD", "ACTUATOR")
_INPUT_KEYWORDS = ("IN", "AUDIO", "MIC", "SENSOR", "SIG")
_POWER_KEYWORDS = ("POWER", "SUPPLY", "PWR", "BATT", "VCC", "VDD")

# ── Power net patterns ──────────────────────────────────────────────────────

_POSITIVE_PATTERNS = ("V+", "VCC", "VDD", "+5", "+3", "+12", "+15", "+24", "+48")
_NEGATIVE_PATTERNS = ("V-", "VEE", "VSS", "-5", "-12", "-15", "-24")
_GND_PATTERNS = ("GND", "AGND", "DGND", "0V")


# ── Classification ──────────────────────────────────────────────────────────

def _classify(part: dict) -> str:
    ref = part.get("ref", "")
    name = part.get("name", "").upper()
    value = part.get("value", "").upper()
    prefix = "".join(c for c in ref if c.isalpha())

    if prefix in ("J", "P", "CN", "X"):
        combined = f"{name} {value}"
        if any(kw in combined for kw in _POWER_KEYWORDS):
            return "connector_pwr"
        if any(kw in combined for kw in _OUTPUT_KEYWORDS):
            return "connector_out"
        if any(kw in combined for kw in _INPUT_KEYWORDS):
            return "connector_in"
        pin_types = {p.get("type", "") for p in part.get("pins", [])}
        if "output" in pin_types:
            return "connector_out"
        return "connector_in"

    if any(kw in name for kw in ("REG", "LDO", "BUCK", "BOOST", "TPS", "AP2112", "AMS1117", "MCP1700")):
        return "power_reg"
    if prefix in ("U", "IC"):
        return "main_ic"
    if prefix == "Q":
        return "transistor"
    if prefix in ("LED",) or (prefix == "D" and "LED" in name):
        return "indicator"
    return "passive"  # R, C, D, L, etc.


def _classify_component(part: dict) -> str:
    """Alias kept for backward compatibility with pcb_builder."""
    return _classify(part)


# ── Bypass capacitor detection ──────────────────────────────────────────────

def _is_bypass_cap(part: dict) -> bool:
    if "".join(c for c in part.get("ref", "") if c.isalpha()) != "C":
        return False
    value = part.get("value", "").upper().strip()
    patterns = [r"^100\s*N", r"^0\.1\s*U", r"^10\s*N", r"^1\s*U", r"^4\.7\s*U", r"^10\s*U"]
    return any(re.match(p, value) for p in patterns)


# ── Rotation ────────────────────────────────────────────────────────────────

def _assign_rotation(part: dict) -> int:
    prefix = "".join(c for c in part.get("ref", "") if c.isalpha())
    name = (part.get("name", "") + part.get("value", "")).upper()
    if prefix in ("R", "C", "L", "D"):
        return 90  # vertical
    if prefix == "Q":
        return 180 if "PNP" in name else 0
    return 0


# ── Power net helpers ───────────────────────────────────────────────────────

def _get_power_net_names(ref: str, nets: list[dict]) -> set[str]:
    """Get the names of power nets a component is connected to."""
    names = set()
    for net in nets:
        if net.get("type") != "power":
            continue
        for conn in net.get("connections", []):
            if conn.split(":")[0] == ref:
                names.add(net["name"])
    return names


def _is_pullup(part: dict, nets: list[dict]) -> bool:
    """Detect pullup/pulldown resistors: R with one pin on power, one on signal.

    Usable by both schematic and PCB placement to identify inline-on-signal parts.
    """
    if not part.get("ref", "").startswith("R"):
        return False
    if len(part.get("pins", [])) != 2:
        return False
    pin_types = set()
    for net in nets:
        for conn in net.get("connections", []):
            if conn.split(":")[0] == part["ref"]:
                pin_types.add(net.get("type", "signal"))
    return "power" in pin_types and "signal" in pin_types


def _map_bypass_caps_round_robin(
    caps: list[dict], ic_refs_sorted: list[str],
) -> dict[str, str]:
    """Assign bypass caps to ICs in round-robin order (1:1 distribution).

    Args:
        caps: bypass cap part dicts
        ic_refs_sorted: IC refs sorted by placement X (left-to-right)

    Returns: {cap_ref: ic_ref}
    """
    if not ic_refs_sorted:
        return {}
    result = {}
    for i, cap in enumerate(caps):
        ic = ic_refs_sorted[i % len(ic_refs_sorted)]
        result[cap["ref"]] = ic
    return result


_SMALL_PREFIXES = ("R", "C", "L", "D", "Y", "F")
_SMALL_PREFIXES_2 = ("SW",)


def _is_small_passive(part: dict) -> bool:
    """Check if a component is a small passive (R, C, L, D, Y, SW, F)."""
    ref = part.get("ref", "")
    return ref[:1] in _SMALL_PREFIXES or ref[:2] in _SMALL_PREFIXES_2
