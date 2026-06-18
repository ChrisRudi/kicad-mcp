# SPDX-License-Identifier: GPL-3.0-or-later
"""Datasheet pin-type / pin-name normalisation for the pinout validator.

Two pure helpers, no I/O:

* :func:`map_datasheet_type` maps a free-form datasheet type token
  (``"I"``, ``"PWR"``, ``"I/O"``, ``"EP"`` …) to a *valid KiCad
  electrical type* (the names in
  ``symbol_author.VALID_PIN_TYPES`` — verified against
  ``generators/validator.py``). An unknown token returns ``None`` so the
  caller can flag the pin ``unclassifiable`` rather than silently passing
  it.
* :func:`normalize_pin_name` canonicalises a pin name so the two sides of
  a diff compare apples-to-apples: uppercase, whitespace stripped,
  every active-low spelling (``nX`` / ``/X`` / ``X#`` / ``X_N`` /
  KiCad overbar ``~{X}`` / Unicode combining overline) collapsed to one
  canonical ``~X`` token, and ``-`` / ``_`` / ``.`` separators unified.
  Functional suffixes are *not* stripped (name fidelity).
"""
from __future__ import annotations

import re
import unicodedata

from ..symbol_author import VALID_PIN_TYPES

# Datasheet type token (already uppercased + punctuation-normalised by
# _canon_type) → KiCad electrical type. Every right-hand value is checked
# against VALID_PIN_TYPES at import time (see _assert_targets_valid).
_TYPE_LOOKUP: dict[str, str] = {
    # input
    "I": "input", "IN": "input", "INPUT": "input", "DI": "input",
    # output
    "O": "output", "OUT": "output", "OUTPUT": "output", "DO": "output",
    # bidirectional
    "I/O": "bidirectional", "IO": "bidirectional", "B": "bidirectional",
    "BIDIR": "bidirectional", "DIO": "bidirectional",
    # power_in (supply rails)
    "P": "power_in", "PWR": "power_in", "POWER": "power_in",
    "SUPPLY": "power_in", "VCC": "power_in", "VDD": "power_in",
    "VS": "power_in", "VM": "power_in", "VIN": "power_in",
    # power_in (ground class — KiCad has no separate "ground" type)
    "G": "power_in", "GND": "power_in", "GROUND": "power_in",
    "VSS": "power_in", "RTN": "power_in",
    # exposed pad / thermal pad — power_in, plus a dedicated EP number check
    # lives in diff.py.
    "EP": "power_in", "PAD": "power_in", "POWERPAD": "power_in",
    # power_out
    "PO": "power_out", "VREF_OUT": "power_out", "LDO_OUT": "power_out",
    # open_collector / open_drain
    "OC": "open_collector", "OD": "open_collector",
    "OPEN-DRAIN": "open_collector", "OPEN-COLLECTOR": "open_collector",
    # passive
    "PAS": "passive", "PASSIVE": "passive",
    # no_connect
    "NC": "no_connect", "N/C": "no_connect", "DNC": "no_connect",
    # rarer direct KiCad-name tokens
    "TRI_STATE": "tri_state", "TRI-STATE": "tri_state",
    "OPEN_EMITTER": "open_emitter", "OPEN-EMITTER": "open_emitter",
    "OE": "open_emitter",
    "FREE": "free",
    "UNSPECIFIED": "unspecified", "UNSPEC": "unspecified",
}

# Tokens whose datasheet meaning is "exposed / thermal pad". diff.py uses
# this to run the dedicated EP-number cross-check.
EP_TOKENS = frozenset({"EP", "PAD", "POWERPAD"})


def _assert_targets_valid() -> None:
    """Fail fast if a mapping target is not a real KiCad electrical type."""
    bad = sorted(set(_TYPE_LOOKUP.values()) - set(VALID_PIN_TYPES))
    if bad:
        raise RuntimeError(
            f"pinout.type_map targets not in VALID_PIN_TYPES: {bad}"
        )


_assert_targets_valid()


def _canon_type(raw: str) -> str:
    """Uppercase + strip surrounding punctuation/space from a type token,
    while keeping the inner ``/`` and ``-`` that distinguish ``I/O`` from
    ``IO`` and ``OPEN-DRAIN`` from ``OPENDRAIN``."""
    s = (raw or "").strip().upper()
    # Drop a trailing/leading lone punctuation but keep internal / and -.
    s = s.strip(" \t.,;:()[]")
    return s


def map_datasheet_type(raw: str) -> str | None:
    """Map a datasheet type token to a valid KiCad electrical type.

    Args:
        raw: The raw type cell from the datasheet pinout table, e.g.
            ``"I"``, ``"I/O"``, ``"PWR"``, ``"GND"``, ``"EP"``.

    Returns:
        A KiCad electrical type from ``VALID_PIN_TYPES`` (``"input"``,
        ``"power_in"`` …), or ``None`` when the token is not recognised —
        ``None`` is the signal for "unclassifiable", never a silent pass.
    """
    canon = _canon_type(raw)
    if not canon:
        return None
    if canon in _TYPE_LOOKUP:
        return _TYPE_LOOKUP[canon]
    # A datasheet sometimes already uses the exact KiCad type name.
    low = canon.lower()
    if low in VALID_PIN_TYPES:
        return low
    return None


# --- pin-name normalisation -------------------------------------------------

# KiCad overbar: ~{NAME} or the legacy bare ~NAME.
_OVERBAR_BRACED_RE = re.compile(r"~\{([^}]*)\}")
# Active-low prefixes the datasheet might use.
_SEP_RE = re.compile(r"[-_.]+")


def _strip_combining_overline(s: str) -> tuple[str, bool]:
    """Remove Unicode combining overline (U+0305) marks. Returns the
    cleaned string and whether any were present (→ active-low)."""
    decomposed = unicodedata.normalize("NFD", s)
    had = "̅" in decomposed
    cleaned = decomposed.replace("̅", "")
    return unicodedata.normalize("NFC", cleaned), had


def normalize_pin_name(raw: str) -> str:
    """Canonicalise a pin name so symbol-side and datasheet-side names
    compare equal when they denote the same pin.

    Rules:
      * uppercase, all whitespace removed;
      * every active-low spelling unified to a leading ``~`` token —
        KiCad ``~{X}`` / bare ``~X``, datasheet ``nX``, ``/X``, ``X#``,
        ``X_N`` and a Unicode combining-overline ``X̄`` all become ``~X``;
      * ``-`` / ``_`` / ``.`` separators collapsed to ``_``;
      * functional suffixes are kept (name fidelity — we never strip them).

    Args:
        raw: The raw pin-name string from either side.

    Returns:
        The canonical token (may be empty if ``raw`` was empty).
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""

    active_low = False

    # Unicode combining overline.
    s, had_overline = _strip_combining_overline(s)
    active_low = active_low or had_overline

    # KiCad overbar forms.
    if "~{" in s:
        active_low = True
        s = _OVERBAR_BRACED_RE.sub(r"\1", s)
    while s.startswith("~"):
        active_low = True
        s = s[1:]

    # Datasheet lowercase-n active-low prefix (nRESET, nCS) — checked on the
    # original casing so a genuine leading "N" (NC, NReset uppercased) is not
    # eaten. Trigger only when an UPPER-case letter follows the lowercase n.
    if len(s) >= 2 and s[0] == "n" and s[1].isalpha() and s[1].isupper():
        active_low = True
        s = s[1:]

    # Remove whitespace, uppercase.
    s = re.sub(r"\s+", "", s).upper()

    # Datasheet active-low forms.
    if s.startswith("/"):
        active_low = True
        s = s[1:]
    if s.endswith("#"):
        active_low = True
        s = s[:-1]

    # _N suffix (e.g. RESET_N) → active low. Strip the suffix token.
    if s.endswith("_N"):
        active_low = True
        s = s[:-2]

    # Unify separators.
    s = _SEP_RE.sub("_", s).strip("_")

    return ("~" + s) if active_low else s
