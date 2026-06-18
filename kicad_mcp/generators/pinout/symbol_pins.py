# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract a KiCad symbol's pin list from a ``.kicad_sym`` file.

Pure logic, no MCP / no path normalisation (the MCP wrapper does that).
Reuses the battle-tested, string-literal-aware block extractor and the
``(extends "Base")`` inlining from :mod:`generators.symbol_cache`, and the
S-expression pin walk pattern from :mod:`generators.netlist_expander`, so
this module owns no duplicate parsing logic of its own.

The returned pins carry ``num`` / ``name`` / ``type`` exactly as written in
the symbol — normalisation (name canonicalisation, type mapping) happens in
:mod:`diff` so both the symbol and datasheet sides go through the same
normaliser.
"""
# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks guard it
from __future__ import annotations

import os
import re
from typing import Any

from ..symbol_cache import _extract_top_level_symbol, _symbol_properties
from ...utils.sexpr_parser import parse_sexpr, find_node


def _inline_extends(content: str, sym_text: str, symbol_name: str) -> tuple[str, str | None]:
    """If ``sym_text`` declares ``(extends "Base")`` inline the base symbol's
    geometry (which carries the pins) under ``symbol_name``.

    Returns ``(resolved_text, base_name_or_None)``.
    """
    extends_match = re.search(r'\(extends\s+"([^"]+)"\)', sym_text)
    if not extends_match:
        return sym_text, None

    base_name = extends_match.group(1)
    base_text = _extract_top_level_symbol(content, base_name)
    if not base_text:
        # Base missing — return as-is; pins simply won't be found.
        return sym_text, base_name

    derived_props = _symbol_properties(sym_text)
    renamed = base_text.replace(f'"{base_name}"', f'"{symbol_name}"')
    renamed = renamed.replace(f'"{base_name}_', f'"{symbol_name}_')
    base_props = _symbol_properties(renamed)
    for prop_name, derived_block in derived_props.items():
        if prop_name in base_props:
            renamed = renamed.replace(base_props[prop_name], derived_block, 1)
    return renamed, base_name


def _pins_from_symbol_text(sym_text: str) -> list[dict[str, str]]:
    """Walk a symbol block and collect every ``(pin TYPE … (number) (name))``.

    Multi-unit symbols nest their pins inside several ``(symbol "Name_u_s")``
    sub-blocks; the recursive walk visits all of them, so multi-unit pins are
    captured. Duplicate pin numbers (the same physical pin echoed across
    body styles) are de-duplicated, first occurrence wins.
    """
    tree = parse_sexpr(sym_text)
    pins: list[dict[str, str]] = []
    seen: set[str] = set()

    def _walk(node: Any) -> None:
        if not isinstance(node, list) or not node:
            return
        if node[0] == "pin" and len(node) > 1:
            pin_type = node[1] if isinstance(node[1], str) else "unspecified"
            num_node = find_node(node, "number")
            name_node = find_node(node, "name")
            if num_node and len(num_node) >= 2:
                num = str(num_node[1])
                name = (
                    str(name_node[1])
                    if name_node and len(name_node) >= 2
                    else num
                )
                if num not in seen:
                    seen.add(num)
                    pins.append({
                        "num": num,
                        "name": name if name else num,
                        "type": pin_type,
                    })
        for child in node:
            if isinstance(child, list):
                _walk(child)

    _walk(tree)
    return pins


def extract_symbol_pins(sym_path: str, symbol_name: str) -> dict[str, Any]:
    """Extract the pin list of ``symbol_name`` from a ``.kicad_sym`` file.

    Args:
        sym_path: Local filesystem path to a ``.kicad_sym`` library file.
        symbol_name: Bare symbol name inside the library (no ``Lib:`` prefix).

    Returns:
        ``{success, symbol, pins:[{num,name,type}], pin_count, extends?}`` on
        success, or ``{success: False, error}`` when the file is missing /
        unreadable or the symbol is not present. ``extends`` is set to the
        base-symbol name when the symbol inherits one.
    """
    if not sym_path or not os.path.isfile(sym_path):
        return {"success": False, "error": f"Symbol file not found: {sym_path}"}

    try:
        with open(sym_path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        return {"success": False, "error": f"Could not read {sym_path}: {exc}"}

    sym_text = _extract_top_level_symbol(content, symbol_name)
    if sym_text is None:
        return {
            "success": False,
            "error": f"Symbol '{symbol_name}' not found in {sym_path}",
        }

    resolved, base_name = _inline_extends(content, sym_text, symbol_name)
    pins = _pins_from_symbol_text(resolved)

    out: dict[str, Any] = {
        "success": True,
        "symbol": symbol_name,
        "pins": pins,
        "pin_count": len(pins),
    }
    if base_name:
        out["extends"] = base_name
    return out
