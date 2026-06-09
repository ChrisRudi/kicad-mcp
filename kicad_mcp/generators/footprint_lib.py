# SPDX-License-Identifier: GPL-3.0-or-later
"""
KiCad footprint library resolver.

Reads real .kicad_mod files from the KiCad library and embeds them
in generated .kicad_pcb files — exactly as KiCad would when importing
a netlist.  No placeholder pads, no invented geometry.

If a footprint is not found in the library the PCB builder emits a
minimal stub so that the file still opens, but the user must run
"Update from Library" in KiCad to get the real graphics.
"""

from functools import lru_cache
import logging
import os
from pathlib import Path
import re

from ..utils.sexpr_parser import find_nodes, parse_sexpr

logger = logging.getLogger(__name__)

# ── KiCad footprint library path detection ──────────────────────────────────

_KICAD_FP_DIRS = [
    Path(r"C:\Program Files\KiCad\10.0\share\kicad\footprints"),
    Path("/mnt/c/Program Files/KiCad/10.0/share/kicad/footprints"),
    Path(r"C:\Program Files\KiCad\9.0\share\kicad\footprints"),
    Path("/usr/share/kicad/footprints"),
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"),
]


def _find_fp_dir() -> Path | None:
    """Find the first existing KiCad footprint library directory."""
    env = os.environ.get("KICAD_FOOTPRINT_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    for d in _KICAD_FP_DIRS:
        if d.exists():
            return d
    return None


# ── THT/SMD default table (only used when part has no footprint set) ────────

_DEFAULTS_BY_PREFIX: dict[str, str] = {
    "R":   "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
    "C":   "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm",
    "L":   "Inductor_THT:L_Axial_L5.3mm_D2.2mm_P10.16mm_Horizontal",
    "D":   "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
    "LED": "LED_THT:LED_D5.0mm",
    "SW":  "Button_Switch_THT:SW_PUSH_6mm",
    "F":   "Fuse:Fuse_Littelfuse_395Series",
    "Y":   "Crystal:Crystal_HC49-4H_Vertical",
    "J":   "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    "P":   "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
}

_DIP_BY_PINS: dict[int, str] = {
    8:  "Package_DIP:DIP-8_W7.62mm",
    14: "Package_DIP:DIP-14_W7.62mm",
    16: "Package_DIP:DIP-16_W7.62mm",
    20: "Package_DIP:DIP-20_W7.62mm",
    28: "Package_DIP:DIP-28_W7.62mm",
    40: "Package_DIP:DIP-40_W15.24mm",
}

_CONN_BY_PINS: dict[int, str] = {
    i: f"Connector_PinHeader_2.54mm:PinHeader_1x{i:02d}_P2.54mm_Vertical"
    for i in range(1, 41)
}


def resolve_footprint(part: dict) -> str:
    """Return the footprint ID for *part*, using its explicit value or a default."""
    fp = part.get("footprint", "")
    if fp:
        return fp

    ref = part.get("ref", "")
    prefix = "".join(c for c in ref if c.isalpha())
    n_pins = len(part.get("pins", []))

    if prefix in _DEFAULTS_BY_PREFIX:
        return _DEFAULTS_BY_PREFIX[prefix]

    if prefix in ("J", "P", "CN", "X"):
        return _CONN_BY_PINS.get(n_pins, _CONN_BY_PINS[2])

    if prefix in ("U", "IC"):
        for cnt in sorted(_DIP_BY_PINS):
            if cnt >= n_pins:
                return _DIP_BY_PINS[cnt]
        return _DIP_BY_PINS[max(_DIP_BY_PINS)]

    return "Package_DIP:DIP-8_W7.62mm"


# ── Read raw .kicad_mod from library ────────────────────────────────────────

@lru_cache(maxsize=128)
def read_kicad_mod(footprint_id: str) -> str | None:
    """Read a .kicad_mod file verbatim from the KiCad library.

    If the exact footprint_id is not found, attempts fuzzy lookup via
    the library index (handles typos, missing suffixes like _Horizontal,
    wrong library names like Connector_Phoenix_MKDS vs TerminalBlock_Phoenix).

    Returns the raw file text, or None when the library is missing.
    """
    fp_dir = _find_fp_dir()
    if not fp_dir or ":" not in footprint_id:
        return None

    lib_name, fp_name = footprint_id.split(":", 1)
    mod_path = fp_dir / f"{lib_name}.pretty" / f"{fp_name}.kicad_mod"

    if not mod_path.exists():
        # Fuzzy fallback via library index — try multiple search terms
        from .kicad_library_index import find_footprint
        candidates = [fp_name]
        # Extract core part (e.g. "MKDS-1,5-2_1x02" from long Phoenix names)
        # Try stripping common prefixes like "PhoenixContact_", "TerminalBlock_Phoenix_"
        for prefix in ("PhoenixContact_", "TerminalBlock_Phoenix_", "TerminalBlock_"):
            if fp_name.startswith(prefix):
                candidates.append(fp_name[len(prefix):])
        # Normalize underscores to hyphens in model-number segments only
        # (before _1x, _P, _Horizontal etc.): MKDS_1,5_2 → MKDS-1,5-2
        for c in list(candidates):
            # Split at first _1x or _P (package descriptor boundary)
            m = re.match(r'^(.+?)(_\dx|_P\d)', c)
            if m:
                model_part = m.group(1)
                rest = c[len(model_part):]
                normalized_model = re.sub(r'(?<=[A-Za-z\d,])_(?=\d)', '-', model_part)
                if normalized_model != model_part:
                    candidates.append(normalized_model + rest)
                    # Also try just the normalized model part as search term
                    candidates.append(normalized_model)
        for term in candidates:
            resolved = find_footprint(term)
            if resolved and resolved != footprint_id:
                r_lib, r_fp = resolved.split(":", 1)
                alt_path = fp_dir / f"{r_lib}.pretty" / f"{r_fp}.kicad_mod"
                if alt_path.exists():
                    logger.info("Footprint '%s' resolved to '%s' via fuzzy lookup", footprint_id, resolved)
                    return alt_path.read_text(encoding="utf-8")
        logger.warning("Footprint file not found: %s", mod_path)
        return None

    return mod_path.read_text(encoding="utf-8")


# ── Embed a real footprint into a .kicad_pcb ────────────────────────────────

def build_footprint_with_nets(
    footprint_id: str,
    ref: str,
    value: str,
    x: float,
    y: float,
    pad_to_net: dict[str, tuple[int, str]],
    fp_uuid: str,
    ref_uuid: str,
    val_uuid: str,
    sym_uuid: str = "",
) -> str | None:
    """Return a ready-to-embed footprint string for a .kicad_pcb file.

    The philosophy: change as little as possible.  Read the real
    .kicad_mod, inject placement + UUID + nets on pads, and keep
    every other byte untouched — exactly as KiCad would.

    Args:
        sym_uuid: The schematic symbol UUID — links this footprint to its
                  schematic symbol via (path "/sym_uuid").

    Returns None when the .kicad_mod file is not available.
    """
    raw = read_kicad_mod(footprint_id)
    if raw is None:
        return None

    # ── 1.  Parse the footprint to understand its pads ──────────────
    tree = parse_sexpr(raw)

    # Collect pad numbers that exist in this footprint
    existing_pads = set()
    for pad_node in find_nodes(tree, "pad"):
        if len(pad_node) > 1:
            existing_pads.add(str(pad_node[1]))

    # ── 2.  Reassemble with minimal changes ─────────────────────────

    lines = raw.strip().split("\n")
    out: list[str] = []

    # --- header line: replace footprint name with library:footprint
    out.append(f'\t(footprint "{footprint_id}"')
    # --- insert placement metadata right after header
    out.append('\t\t(layer "F.Cu")')
    out.append(f"\t\t(at {x} {y})")
    out.append(f'\t\t(uuid "{fp_uuid}")')
    # --- path links this footprint to its schematic symbol
    if sym_uuid:
        out.append(f'\t\t(path "/{sym_uuid}")')

    # Track which lines to skip / transform
    skip_depth = 0          # >0 → skip lines until balanced
    header_emitted = False  # first line already replaced above

    for line in lines:
        stripped = line.strip()

        # Skip the very first line (already replaced)
        if not header_emitted:
            header_emitted = True
            continue

        # ── Skip sections we replaced with our header block ────────
        if stripped.startswith("(version ") or stripped.startswith("(generator "):
            continue
        # The footprint's own (layer ...) — we already emitted ours
        if stripped.startswith("(layer ") and skip_depth == 0:
            continue

        # ── Handle depth-based skipping (for multi-line properties) ─
        if skip_depth > 0:
            skip_depth += stripped.count("(") - stripped.count(")")
            if skip_depth <= 0:
                skip_depth = 0
            continue

        # ── Replace Reference property ──────────────────────────────
        if stripped.startswith('(property "Reference"'):
            out.append(f'\t\t(property "Reference" "{ref}"')
            out.append("\t\t\t(at 0 -2 0)")
            out.append('\t\t\t(layer "F.SilkS")')
            out.append(f'\t\t\t(uuid "{ref_uuid}")')
            out.append("\t\t\t(effects (font (size 1 1) (thickness 0.15)))")
            out.append("\t\t)")
            skip_depth = stripped.count("(") - stripped.count(")")
            if skip_depth <= 0:
                skip_depth = 0
            continue

        # ── Replace Value property ──────────────────────────────────
        if stripped.startswith('(property "Value"'):
            out.append(f'\t\t(property "Value" "{value}"')
            out.append("\t\t\t(at 0 2 0)")
            out.append('\t\t\t(layer "F.Fab")')
            out.append(f'\t\t\t(uuid "{val_uuid}")')
            out.append("\t\t\t(effects (font (size 1 1) (thickness 0.15)))")
            out.append("\t\t)")
            skip_depth = stripped.count("(") - stripped.count(")")
            if skip_depth <= 0:
                skip_depth = 0
            continue

        # ── Inject net on pad lines ─────────────────────────────────
        if stripped.startswith("(pad "):
            m = re.match(r'\(pad\s+"?([^"\s]+)"?', stripped)
            if m:
                pad_name = m.group(1)
                net_info = pad_to_net.get(pad_name)
                if net_info:
                    net_num, net_name = net_info
                    net_clause = f'(net {net_num} "{net_name}")'
                    # Append net just before the last closing paren of the pad
                    line = _inject_before_last_close(line, net_clause)

        out.append(line)

    return "\n".join(out)


def _inject_before_last_close(line: str, clause: str) -> str:
    """Insert *clause* just before the final ')' in *line*.

    Handles both single-line pads  ``(pad ... (layers ...))``
    and pads that close on this line after a multi-line block.
    """
    # Find the position of the very last ')' that closes the pad
    rpos = line.rfind(")")
    if rpos == -1:
        # Pad continues on next lines — just append
        return line + "\n\t\t\t" + clause
    return line[:rpos] + " " + clause + ")"


