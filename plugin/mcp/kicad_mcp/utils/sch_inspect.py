# SPDX-License-Identifier: GPL-3.0-or-later
"""Lightweight ``.kicad_sch`` inspection for the footprint-resync tools.

Two facts the text-patch tools need from the schematic, both pulled by a
balanced-paren scan (no SWIG, no pcbnew):

* ``schematic_footprint_map`` — ``ref -> "Lib:Name"`` (the Footprint property
  of each symbol instance), the authoritative lib_id a PCB footprint should
  carry.
* ``schematic_pin_names`` — ``ref -> {pin_number: pin_name}``, resolved through
  the ``(lib_symbols …)`` cache, so a pad's ``(pinfunction …)`` can be
  refreshed from the symbol's real pin names.

Pure text functions (stdlib only) → unit-testable headless with `.kicad_sch`
strings, no KiCad needed.
"""

from __future__ import annotations

import re

_REF_RE = re.compile(r'\(property "Reference" "([^"]+)"')
_FP_RE = re.compile(r'\(property "Footprint" "([^"]*)"')
_LIBID_RE = re.compile(r'\(lib_id "([^"]+)"')
_PIN_NAME_RE = re.compile(r'\(name "([^"]*)"')
_PIN_NUMBER_RE = re.compile(r'\(number "([^"]*)"')


def _block(s: str, start: int) -> int:
    """Index just past the balanced ``(...)`` block that begins at ``start``."""
    depth = 0
    for i in range(start, len(s)):
        c = s[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
    return len(s)


def schematic_footprint_map(sch_text: str) -> dict[str, str]:
    """Map ``ref -> "Lib:Name"`` from each symbol instance's Footprint property.

    Only instances that carry a non-empty Footprint property are included
    (a bare ``""`` is skipped). Use to look up the authoritative lib_id a PCB
    footprint should have.
    """
    out: dict[str, str] = {}
    for m in re.finditer(r'\(symbol\b', sch_text):
        block = sch_text[m.start():_block(sch_text, m.start())]
        ref = _REF_RE.search(block)
        fp = _FP_RE.search(block)
        if ref and fp and fp.group(1):
            out[ref.group(1)] = fp.group(1)
    return out


def _lib_symbol_pins(sch_text: str) -> dict[str, dict[str, str]]:
    """``lib_id -> {pin_number: pin_name}`` from the ``(lib_symbols …)`` cache.

    Multi-unit symbols (``NAME_u_p`` sub-symbols) are merged onto their base
    lib_id key AND kept under their own key, so resolution works either way.
    """
    out: dict[str, dict[str, str]] = {}
    lm = re.search(r'\(lib_symbols\b', sch_text)
    if not lm:
        return out
    libblk = sch_text[lm.start():_block(sch_text, lm.start())]
    for sm in re.finditer(r'\(symbol "([^"]+)"', libblk):
        sub = libblk[sm.start():_block(libblk, sm.start())]
        pins: dict[str, str] = {}
        for pm in re.finditer(r'\(pin\b', sub):
            pb = sub[pm.start():_block(sub, pm.start())]
            name = _PIN_NAME_RE.search(pb)
            number = _PIN_NUMBER_RE.search(pb)
            if name and number:
                pins[number.group(1)] = name.group(1)
        if not pins:
            continue
        key = sm.group(1)
        out.setdefault(key, {}).update(pins)
        # also merge onto the base lib_id (strip a trailing _<unit>_<style>)
        base = re.sub(r'_\d+_\d+$', '', key)
        if base != key:
            out.setdefault(base, {}).update(pins)
    return out


def schematic_pin_names(sch_text: str) -> dict[str, dict[str, str]]:
    """Map ``ref -> {pin_number: pin_name}`` via the lib_symbols cache.

    Resolves each symbol instance's ``lib_id`` against the cached pin names.
    Use to refresh a PCB pad's ``(pinfunction …)`` from the schematic.
    """
    lib = _lib_symbol_pins(sch_text)
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r'\(symbol\b', sch_text):
        block = sch_text[m.start():_block(sch_text, m.start())]
        ref = _REF_RE.search(block)
        lid = _LIBID_RE.search(block)
        if not (ref and lid):
            continue
        pins = lib.get(lid.group(1))
        if pins is None:  # try the base lib_id (multi-unit)
            pins = lib.get(re.sub(r'_\d+_\d+$', '', lid.group(1)))
        if pins:
            out[ref.group(1)] = dict(pins)
    return out
