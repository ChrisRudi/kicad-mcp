# SPDX-License-Identifier: GPL-3.0-or-later
# mapping.py
"""Symbol mapping: LTspice symbol name -> KiCad symbol + pin map."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent
_MAPPING: dict[str, Any] | None = None


def _load_mapping() -> dict[str, Any]:
    """Load symbol_mapping.json once."""
    global _MAPPING
    if _MAPPING is None:
        with open(_DIR / "symbol_mapping.json", encoding="utf-8") as f:
            _MAPPING = json.load(f)
    return _MAPPING


def _normalize_lt_name(lt_name: str) -> str:
    """Normalize LTspice symbol name to lowercase base name."""
    return lt_name.lower().replace("\\", "/").split("/")[-1]


def find_mapping(lt_symbol: str) -> dict[str, Any] | None:
    """Find mapping entry for an LTspice symbol name.

    Checks exact match first, then aliases.
    Returns the mapping dict or None.
    """
    data = _load_mapping()
    name = _normalize_lt_name(lt_symbol)

    for entry in data["mappings"]:
        if entry["ltspice_symbol"] == name:
            return entry
        for alias in entry.get("ltspice_aliases", []):
            if alias == name:
                return entry
    return None


def get_pin_map(lt_symbol: str, mirrored: bool = False) -> dict[str, str]:
    """Get pin mapping for a symbol.

    Args:
        lt_symbol: LTspice symbol name.
        mirrored: If True and mirror_semantic is 'restricted',
                  returns pin_map_mirrored.

    Returns:
        Dict mapping LTspice pin label -> KiCad pin number.
        Empty dict if no mapping found.
    """
    entry = find_mapping(lt_symbol)
    if entry is None:
        return {}

    if mirrored and entry.get("mirror_semantic") == "restricted":
        mirrored_map = entry.get("pin_map_mirrored")
        if mirrored_map:
            return mirrored_map
        # No mirrored map defined for restricted symbol = error case
        # Caller should handle this (fall back to no mirror)
        return entry.get("pin_map", {})

    return entry.get("pin_map", {})


def get_kicad_symbol(lt_symbol: str) -> str:
    """Get KiCad library ID for an LTspice symbol."""
    entry = find_mapping(lt_symbol)
    if entry is None:
        return ""
    return entry.get("kicad_symbol", "")


def get_kicad_footprint(lt_symbol: str) -> str:
    """Get default KiCad footprint for an LTspice symbol."""
    entry = find_mapping(lt_symbol)
    if entry is None:
        return ""
    return entry.get("kicad_footprint", "")


def get_mirror_semantic(lt_symbol: str) -> str:
    """Get mirror semantic for a symbol: 'safe', 'restricted', 'forbidden'."""
    entry = find_mapping(lt_symbol)
    if entry is None:
        return "forbidden"
    return entry.get("mirror_semantic", "safe")


def get_explicit_nc_pins(lt_symbol: str) -> list[str]:
    """Get list of KiCad pin numbers that should be marked no-connect."""
    entry = find_mapping(lt_symbol)
    if entry is None:
        return []
    return entry.get("explicit_nc_pins", [])


def get_power_symbol(label_name: str) -> str | None:
    """Check if a net label corresponds to a power symbol.

    Returns KiCad power library ID (e.g. 'power:GND') or None.
    """
    data = _load_mapping()
    power_map = data.get("power_symbols", {})
    return power_map.get(label_name)


def transcode_value(value: str) -> str:
    """Apply context-sensitive character transcoding for KiCad values.

    Only replaces in unit contexts (after digits), never in references.
    """
    import re
    data = _load_mapping()
    char_map = data.get("char_map", {})

    u_char = char_map.get("u_prefix", "\u00b5")
    ohm_char = char_map.get("ohm_suffix", "\u03a9")
    sq_char = char_map.get("squared", "\u00b2")

    value = re.sub(r"(?<=\d)u(?=[A-Za-z]|$)", u_char, value)
    value = re.sub(r"(?<=\d)Ohm\b", ohm_char, value)
    value = re.sub(r"\^2\b", sq_char, value)

    return value
