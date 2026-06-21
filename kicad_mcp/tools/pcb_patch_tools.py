# SPDX-License-Identifier: GPL-3.0-or-later
"""
PCB text-patcher tools for headless workflows without KiCad GUI.

These tools manipulate ``.kicad_pcb`` files directly as S-expression text and
do not depend on the SWIG ``pcbnew`` Python bindings. They are robust against
the KiCad-10 ``SwigPyObject`` quirk in script context (``FootprintLoad`` and
related ``PCB_IO_KICAD_SEXPR`` calls returning untyped objects without
``FOOTPRINT`` methods) and can run in CI / batch pipelines.

Tools registered:
  * ``patch_pcb_nets_from_netlist`` — F8-equivalent without KiCad GUI; reads a
    kicad-cli-exported netlist (``kicadsexpr`` format) and patches every pad
    in the PCB with its net tag, plus inserts the ``(net N "name")``
    definitions at the board top level.
  * ``resolve_pcb_footprints`` — Tag-based built-in resolution. Looks for
    placeholder footprints whose ``Value`` property contains a ``[lib:fp_name]``
    marker and replaces them with the real KiCad built-in footprint loaded
    from the corresponding ``.kicad_mod`` file. Bypasses the SWIG
    ``FootprintLoad`` bug entirely.
  * ``validate_footprints`` — Cross-checks schematic pin mapping (from netlist)
    against actual PCB pad names per component; reports mismatches and
    library/footprint pin-count conflicts before routing.
  * ``rotate_pcb`` — Rotate the entire PCB about ``(0,0)`` by an arbitrary
    angle. Uses the pcbnew API (no ``FootprintLoad`` involved, so the SWIG
    quirk does not apply).
"""

import math
import os
import re
import uuid
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text, write_text
from kicad_mcp.tools.clearance_tools import attach_clearance
from kicad_mcp.utils.path_env import kicad_lib_root, to_local_path
from kicad_mcp.utils.pcb_geometry import (
    align_radial_rotation,
    bbox_center,
    compute_fp_bbox,
    pcb_local_to_world,
    pcb_world_to_local,
)
from kicad_mcp.utils.pcb_net_format import (
    ensure_net_tag,
    ensure_pad_net_tag,
    pcb_net_format,
)

# ---------------------------------------------------------------------------
# Text-function registry (Universal Callable convention).
#
# Every file-edit tool in this module exposes a *pure* counterpart with the
# signature ``fn(pcb_text: str, **args) -> tuple[str, dict]``. The pure
# function is registered here so a generic ``pcb_batch`` tool can chain
# multiple operations in one open/write cycle. The MCP-decorated tool is
# then a thin I/O wrapper that calls the registered ``_text`` function.
# ---------------------------------------------------------------------------

PCB_PATCH_TEXT_FNS: dict[str, Callable[..., tuple[str, dict[str, Any]]]] = {}


def _register_text_fn(name: str) -> Callable[[Callable], Callable]:
    """Decorator that adds a pure text-mutation function to the
    ``PCB_PATCH_TEXT_FNS`` registry under ``name``."""

    def deco(fn: Callable) -> Callable:
        PCB_PATCH_TEXT_FNS[name] = fn
        return fn

    return deco


# ---------------------------------------------------------------------------
# Footprint pose helpers (used by place_at_pivot).
# ---------------------------------------------------------------------------


_FP_HEADER_AT_RE = re.compile(
    r'(\)\s*)\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+[\d.\-]+)?\)'
)
_FP_LAYER_TAG_RE = re.compile(r'\(layer\s+"([FB]\.Cu)"\)')
_PAD_HEADER_INLINE_RE = re.compile(
    r'\(pad\s+"([^"]*)"\s+\w+\s+\w+\s*'
    r'((?:[^()]|\([^()]*\))*?)'
    r'\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+([\d.\-]+))?\)',
    re.DOTALL,
)


def _find_footprint_block(pcb_text: str, ref: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` byte offsets of the ``(footprint …)`` block
    whose Reference property is ``ref``, or ``None`` if not found."""
    pos = 0
    needle = "(footprint "
    ref_re = re.compile(rf'\(property\s+"Reference"\s+"{re.escape(ref)}"')
    while True:
        idx = pcb_text.find(needle, pos)
        if idx < 0:
            return None
        depth = 0
        end = idx
        for j in range(idx, len(pcb_text)):
            ch = pcb_text[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if ref_re.search(pcb_text[idx:end]) is not None:
            return (idx, end)
        pos = end


def _read_fp_layer(block: str) -> str | None:
    m = _FP_LAYER_TAG_RE.search(block)
    return m.group(1) if m else None


_NET_REF_RE = re.compile(r'\(net\s+(\d+)\)')
_LAYER_TAG_RE = re.compile(r'\(layer\s+"([^"]+)"\)')
_LAYERS_PAIR_RE = re.compile(r'\(layers\s+"([^"]+)"\s+"([^"]+)"\)')
_AT_RE = re.compile(r'\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+[\d.\-]+)?\)')
_START_RE = re.compile(r'\(start\s+([\d.\-]+)\s+([\d.\-]+)\)')
_END_RE = re.compile(r'\(end\s+([\d.\-]+)\s+([\d.\-]+)\)')
_MID_RE = re.compile(r'\(mid\s+([\d.\-]+)\s+([\d.\-]+)\)')


def _is_routing_block_top(text: str, start: int, end: int) -> bool:
    """Return True iff the block at ``[start:end)`` is at PCB top level
    (i.e. NOT nested inside a ``(footprint …)``)."""
    # Walk backwards counting unmatched open-parens; if any open
    # ``(footprint`` is on the stack, the block is inside a footprint.
    depth = 0
    i = start - 1
    while i >= 0:
        ch = text[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                if text.startswith("(footprint", i):
                    return False
                # Otherwise this open paren is whatever else we're
                # nested in; we want top-level, so continue tracking.
                # If we hit the kicad_pcb root, we're done = top-level.
                if text.startswith("(kicad_pcb", i):
                    return True
            else:
                depth -= 1
        i -= 1
    return True


def _block_matches(
    block: str,
    kind: str,
    match_net_names: set[str] | None,
    layer_filter: str,
    bbox: tuple[float, float, float, float] | None,
    id_to_name: dict[int, str] | None = None,
) -> bool:
    # Net filter — match by name so we work on both indexed-form PCBs
    # (where each block carries (net N) + a top-level (net N "name") table)
    # AND string-form PCBs (each block carries (net "name") directly, no
    # table). ``id_to_name`` is needed to resolve the indexed-form name;
    # ``None`` means "indexed form not in use" (string-form-only board)
    # and the (net N) lookup is skipped.
    if match_net_names is not None:
        name: str | None = None
        # String-form pad / track / arc / via / zone: (net "name")
        str_m = re.search(r'\(net\s+"((?:[^"\\]|\\.)*)"\s*\)', block)
        if str_m:
            name = str_m.group(1)
        elif id_to_name is not None:
            net_m = _NET_REF_RE.search(block)
            if net_m:
                name = id_to_name.get(int(net_m.group(1)))
        if name not in match_net_names:
            return False
    # Layer filter
    if layer_filter:
        if kind == "via":
            pair_m = _LAYERS_PAIR_RE.search(block)
            if pair_m is None:
                return False
            if layer_filter not in (pair_m.group(1), pair_m.group(2)):
                return False
        else:
            lay_m = _LAYER_TAG_RE.search(block)
            if lay_m is None or lay_m.group(1) != layer_filter:
                return False
    # Bbox filter — match if ANY endpoint is inside.
    if bbox is not None:
        xmin, ymin, xmax, ymax = bbox
        points: list[tuple[float, float]] = []
        if kind == "via":
            at_m = _AT_RE.search(block)
            if at_m:
                points.append((float(at_m.group(1)), float(at_m.group(2))))
        else:
            for r in (_START_RE, _END_RE, _MID_RE):
                m = r.search(block)
                if m:
                    points.append((float(m.group(1)), float(m.group(2))))
        if not any(xmin <= px <= xmax and ymin <= py <= ymax
                   for px, py in points):
            return False
    return True


def _block_descriptor(
    block: str, kind: str, name_to_id: dict[str, int],
) -> dict[str, Any]:
    """Tiny human-readable summary of a routing block (for preview).

    Resolves the net both from the indexed form ``(net N)`` (looking the
    id up in ``name_to_id``'s reverse map) and from the string short form
    ``(net "name")`` — so a delete-routing preview lists the right net
    on both formats.
    """
    nid: int = 0
    nname: str = ""
    str_m = re.search(r'\(net\s+"((?:[^"\\]|\\.)*)"\s*\)', block)
    if str_m:
        nname = str_m.group(1)
        nid = name_to_id.get(nname, 0)
    else:
        net_m = _NET_REF_RE.search(block)
        if net_m:
            nid = int(net_m.group(1))
            nname = next((n for n, i in name_to_id.items() if i == nid), "")
    out: dict[str, Any] = {"kind": kind, "net_id": nid, "net": nname}
    if kind == "via":
        at_m = _AT_RE.search(block)
        if at_m:
            out["at"] = (float(at_m.group(1)), float(at_m.group(2)))
        pair_m = _LAYERS_PAIR_RE.search(block)
        if pair_m:
            out["layers"] = (pair_m.group(1), pair_m.group(2))
    else:
        for key, r in (("start", _START_RE), ("end", _END_RE),
                       ("mid", _MID_RE)):
            m = r.search(block)
            if m:
                out[key] = (float(m.group(1)), float(m.group(2)))
        lay_m = _LAYER_TAG_RE.search(block)
        if lay_m:
            out["layer"] = lay_m.group(1)
    return out


def _read_fp_pose(block: str) -> tuple[float, float, float]:
    """Extract ``(x_mm, y_mm, rotation_deg)`` from a footprint header."""
    m = _FP_HEADER_AT_RE.search(block)
    if m is None:
        return (0.0, 0.0, 0.0)
    # _FP_HEADER_AT_RE has groups (preceding-close, x, y); rotation is
    # optional and not captured separately, so re-scan for the full
    # ``(at x y rot)`` triple here.
    m2 = re.search(
        r'\)\s*\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+([\d.\-]+))?\)',
        block,
    )
    if m2 is None:
        return (0.0, 0.0, 0.0)
    return (
        float(m2.group(1)),
        float(m2.group(2)),
        float(m2.group(3)) if m2.group(3) else 0.0,
    )


def _find_pad_at(block: str, pad_name: str) -> tuple[float, float, float] | None:
    """Return ``(lx, ly, lokal_rot)`` of the named pad inside a footprint
    block, or ``None`` if the pad does not exist. The first match wins
    (KiCad uses unique pad names per footprint)."""
    for m in _PAD_HEADER_INLINE_RE.finditer(block):
        if m.group(1) == pad_name:
            rot = float(m.group(5)) if m.group(5) else 0.0
            return (float(m.group(3)), float(m.group(4)), rot)
    return None


def _patch_fp_pose(
    block: str,
    new_anchor: tuple[float, float],
    new_rot: float,
    layer: str | None = None,
) -> tuple[str, int]:
    """Rewrite the footprint header ``(at …)``, optionally swap the
    ``(layer …)`` tag, and rotate every pad's local rotation by the
    same delta so pad shapes rotate with the body — additively, so
    pads whose library entry already carries a non-zero rotation
    (e.g. 45°-rotated SMT pads, chamfered QFN corners) retain that
    library orientation **plus** the user's footprint rotation.

    Returns ``(new_block, pads_updated)``.
    """
    rot_int = int(round(float(new_rot))) % 360
    # Capture the OLD footprint rotation BEFORE rewriting the header so
    # we can compute the additive delta for pad-local rotations.
    _ox, _oy, old_fp_rot = _read_fp_pose(block)
    delta_rot = (rot_int - old_fp_rot) % 360

    # 1) Footprint header
    new = _FP_HEADER_AT_RE.sub(
        rf'\1(at {new_anchor[0]:.6f} {new_anchor[1]:.6f} {rot_int})',
        block, count=1,
    )

    # 2) Optional layer swap
    if layer is not None:
        new = _FP_LAYER_TAG_RE.sub(f'(layer "{layer}")', new, count=1)

    # 3) Pad local-rot — bump each pad's ``(at lx ly rot)`` by ``delta_rot``
    # so the on-disk pad orientation stays consistent with both the
    # footprint body AND the library's pre-rotated pad shapes. Naive
    # overwrite (set every pad to ``rot_int``) destroys lib-rot ≠ 0.
    pads_updated = [0]

    def _pad_repl(m: re.Match[str]) -> str:
        pads_updated[0] += 1
        full = m.group(0)
        # Replace just the trailing (at lx ly [rot]) portion.
        head_end = full.rfind("(at")
        head = full[:head_end]
        # Old absolute pad rot in the file already equals lib_rot +
        # old_fp_rot (KiCad's writer keeps the absolute angle). Adding
        # ``delta_rot`` gives lib_rot + new_fp_rot — the new absolute.
        old_pad_rot = float(m.group(5)) if m.group(5) else 0.0
        new_pad_rot = (old_pad_rot + delta_rot) % 360
        # Match the file's typical formatting: omit the rot token only
        # when the result is exactly 0 (KiCad does the same).
        if new_pad_rot == 0:
            return f'{head}(at {m.group(3)} {m.group(4)})'
        return f'{head}(at {m.group(3)} {m.group(4)} {new_pad_rot:g})'

    new = _PAD_HEADER_INLINE_RE.sub(_pad_repl, new)

    return new, pads_updated[0]


# ---------------------------------------------------------------------------
# Generic S-expression helpers (depth-balanced block extraction).
# ---------------------------------------------------------------------------


def _find_block_end(text: str, start: int) -> int:
    """Return the index just past the matching closing parenthesis for the
    opening parenthesis at ``start`` (must satisfy ``text[start] == '('``)."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _find_footprint_header_at_end(block: str) -> int:
    """Return the index just past the footprint-header ``(at …)`` token.

    Walks ``block`` with a paren-depth counter and locates the first
    ``(at …)`` that is a *direct child* of the outer ``(footprint …)``
    — that is the anchor, regardless of whether ``(layer)``, ``(uuid)``,
    ``(path)`` etc. appear before or after it in the header. Returns 0
    if no anchor is found (malformed block).

    Both header orders occur in the wild:
    * ``kicad-cli`` / hand-curated PCBs: ``(uuid …) (at …)``
    * ``generate_project`` (this server): ``(at …) (uuid …)``
    """
    depth = 0
    n = len(block)
    i = 0
    while i < n:
        ch = block[i]
        if ch == "(":
            if depth == 1 and block.startswith("(at ", i):
                return _find_block_end(block, i)
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    return 0


def _iter_top_blocks(text: str, head: str) -> list[tuple[int, int]]:
    """Yield ``(start, end)`` offsets for every top-level S-expression block
    that begins with ``"(" + head`` (e.g. ``head="footprint"`` finds every
    ``(footprint …)`` block; nested occurrences inside other blocks are still
    matched). Use ``_find_block_end`` to skip a block once handled.
    """
    needle = "(" + head
    out: list[tuple[int, int]] = []
    i = 0
    while True:
        i = text.find(needle, i)
        if i < 0:
            return out
        end = _find_block_end(text, i)
        out.append((i, end))
        i = end


def _iter_pad_blocks(footprint_text: str) -> list[tuple[int, int]]:
    """Return all ``(pad …)`` sub-block offsets within a ``(footprint …)``
    block. Recognizes both ``(pad ` and ``(pad\t`` openings."""
    out: list[tuple[int, int]] = []
    k = 0
    L = len(footprint_text)
    while k < L:
        if footprint_text.startswith("(pad ", k) or footprint_text.startswith("(pad\t", k):
            end = _find_block_end(footprint_text, k)
            out.append((k, end))
            k = end
        else:
            k += 1
    return out


# ---------------------------------------------------------------------------
# Tool 1: patch_pcb_nets_from_netlist  (F8-equivalent, headless)
# ---------------------------------------------------------------------------


def _extract_netlist_text(schematic_path: str) -> str | None:
    """Run ``kicad-cli sch export netlist --format kicadsexpr`` and
    return the resulting text. Returns ``None`` on failure (missing
    kicad-cli, broken schematic, etc.)."""
    import subprocess  # local — only needed here
    import tempfile

    try:
        from kicad_mcp.utils.kicad_cli import (  # type: ignore
            KiCadCLIError, get_kicad_cli_path,
        )
    except ImportError:
        return None
    try:
        cli_path = get_kicad_cli_path(required=True)
    except KiCadCLIError:
        return None

    try:
        from kicad_mcp.utils.wsl_path import to_windows_path  # type: ignore
    except ImportError:
        def to_windows_path(p):  # type: ignore[no-redef]
            return p

    with tempfile.NamedTemporaryFile(suffix=".net", delete=False) as tmp:
        out_path = tmp.name
    try:
        cmd = [
            cli_path, "sch", "export", "netlist",
            "--format", "kicadsexpr",
            "--output", to_windows_path(out_path),
            to_windows_path(schematic_path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False,
        )
        if (result.returncode != 0
                or not os.path.exists(out_path)
                or os.path.getsize(out_path) == 0):
            return None
        with open(out_path, encoding="utf-8") as fh:
            return fh.read()
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _update_fp_value(pcb_text: str, ref: str, new_value: str) -> str:
    """Rewrite the ``Value`` property of the footprint with reference
    ``ref``. Leaves the rest of the footprint block untouched.
    Returns the new text (unchanged if ``ref`` is not found)."""
    for fp_start, fp_end in _iter_top_blocks(pcb_text, "footprint"):
        block = pcb_text[fp_start:fp_end]
        ref_m = _REF_PROP_RE.search(block)
        if not ref_m or ref_m.group(1) != ref:
            continue
        new_block = _VAL_PROP_RE.sub(
            f'(property "Value" "{new_value}"', block, count=1,
        )
        return pcb_text[:fp_start] + new_block + pcb_text[fp_end:]
    return pcb_text


def _swap_fp_library(
    pcb_text: str, ref: str, new_lib_id: str, library_root: str,
) -> tuple[str, bool]:
    """Replace the entire footprint block for ``ref`` with one loaded
    from the new library entry ``new_lib_id`` (``lib:fp_name``).
    Preserves the old footprint's position, rotation, side and Value.

    Returns ``(new_text, success)``. ``success=False`` if the new
    .kicad_mod cannot be located in ``library_root`` (the original
    block is left untouched in that case).
    """
    if ":" not in new_lib_id:
        return pcb_text, False
    lib_name, fp_name = new_lib_id.split(":", 1)
    template = _load_kicad_mod_text(library_root, lib_name, fp_name)
    if template is None:
        return pcb_text, False

    for fp_start, fp_end in _iter_top_blocks(pcb_text, "footprint"):
        block = pcb_text[fp_start:fp_end]
        ref_m = _REF_PROP_RE.search(block)
        if not ref_m or ref_m.group(1) != ref:
            continue
        # Preserve the old pose + value.
        at_m = _FIRST_AT_RE.search(block)
        if not at_m:
            return pcb_text, False
        x = float(at_m.group(1))
        y = float(at_m.group(2))
        rot = float(at_m.group(3)) if at_m.group(3) else 0.0
        layer_m = re.search(r'\(layer "([FB]\.Cu)"\)', block)
        layer = layer_m.group(1) if layer_m else "F.Cu"
        val_m = _VAL_PROP_RE.search(block)
        value = val_m.group(1) if val_m else fp_name

        new_block = _patch_loaded_footprint(
            template, ref, value, x, y, rot,
            mirror_to_bcu=(layer == "B.Cu"),
        )
        return pcb_text[:fp_start] + new_block + pcb_text[fp_end:], True
    return pcb_text, False


def _remove_fp(pcb_text: str, ref: str) -> tuple[str, bool]:
    """Delete the footprint block with reference ``ref``. Returns
    ``(new_text, success)``."""
    for fp_start, fp_end in _iter_top_blocks(pcb_text, "footprint"):
        block = pcb_text[fp_start:fp_end]
        ref_m = _REF_PROP_RE.search(block)
        if not ref_m or ref_m.group(1) != ref:
            continue
        # Strip the indentation on the same line, and the trailing
        # newline, so the surrounding file stays clean.
        line_start = pcb_text.rfind("\n", 0, fp_start) + 1
        trail = fp_end
        if trail < len(pcb_text) and pcb_text[trail] == "\n":
            trail += 1
        if pcb_text[line_start:fp_start].strip() == "":
            return pcb_text[:line_start] + pcb_text[trail:], True
        return pcb_text[:fp_start] + pcb_text[fp_end:], True
    return pcb_text, False


def _parse_netlist_components(netlist_text: str) -> dict[str, dict[str, str]]:
    """Parse the ``(components …)`` section of a kicadsexpr netlist.

    Returns ``{ref: {"value": str, "footprint": str, "libsource_lib": str,
    "libsource_part": str}}``. Components without a Footprint field have
    ``footprint=""``; the caller must decide whether to flag that as an
    error.
    """
    out: dict[str, dict[str, str]] = {}
    cs = netlist_text.find("(components")
    if cs < 0:
        return out
    cs_end = _find_block_end(netlist_text, cs)
    section = netlist_text[cs:cs_end]
    pos = 0
    while True:
        s = section.find("(comp ", pos)
        if s < 0:
            s = section.find("(comp\n", pos)
        if s < 0:
            return out
        end = _find_block_end(section, s)
        block = section[s:end]
        ref_m = re.search(r'\(ref "([^"]+)"\)', block)
        if ref_m:
            ref = ref_m.group(1)
            val_m = re.search(r'\(value "([^"]*)"\)', block)
            fp_m = re.search(r'\(footprint "([^"]*)"\)', block)
            ls_lib_m = re.search(
                r'\(libsource\s+\(lib "([^"]*)"\)\s+\(part "([^"]*)"\)',
                block,
            )
            out[ref] = {
                "value": val_m.group(1) if val_m else "",
                "footprint": fp_m.group(1) if fp_m else "",
                "libsource_lib": ls_lib_m.group(1) if ls_lib_m else "",
                "libsource_part": ls_lib_m.group(2) if ls_lib_m else "",
            }
        pos = end


def _parse_pcb_components(pcb_text: str) -> dict[str, dict[str, Any]]:
    """Parse PCB-side footprints. Returns
    ``{ref: {"lib_id": str, "value": str, "x": float, "y": float,
             "rot": float, "layer": str, "start": int, "end": int}}``.
    """
    out: dict[str, dict[str, Any]] = {}
    for fp_start, fp_end in _iter_top_blocks(pcb_text, "footprint"):
        block = pcb_text[fp_start:fp_end]
        ref_m = _REF_PROP_RE.search(block)
        if not ref_m:
            continue
        lib_m = re.match(r'\(footprint\s+"([^"]*)"', block)
        val_m = _VAL_PROP_RE.search(block)
        at_m = _FIRST_AT_RE.search(block)
        layer_m = re.search(r'\(layer "([FB]\.Cu)"\)', block)
        out[ref_m.group(1)] = {
            "lib_id": lib_m.group(1) if lib_m else "",
            "value": val_m.group(1) if val_m else "",
            "x": float(at_m.group(1)) if at_m else 0.0,
            "y": float(at_m.group(2)) if at_m else 0.0,
            "rot": float(at_m.group(3)) if (at_m and at_m.group(3)) else 0.0,
            "layer": layer_m.group(1) if layer_m else "F.Cu",
            "start": fp_start,
            "end": fp_end,
        }
    return out


def _parse_netlist_node_map(netlist_text: str) -> tuple[dict[tuple[str, str], str], list[str]]:
    """Parse a kicad-cli ``kicadsexpr`` netlist. Returns ``(node_map, all_nets)``
    where ``node_map[(component_ref, pin_name)] = net_name`` and ``all_nets``
    is the sorted list of unique net names referenced by any node.
    """
    node_map: dict[tuple[str, str], str] = {}
    nets_section = netlist_text.find("(nets")
    if nets_section < 0:
        return node_map, []
    pos = nets_section
    while True:
        s = netlist_text.find("(net\n", pos)
        if s < 0:
            s = netlist_text.find("(net ", pos)
        if s < 0:
            break
        if netlist_text.startswith("(net_class", s):
            pos = s + len("(net_class")
            continue
        end = _find_block_end(netlist_text, s)
        block = netlist_text[s:end]
        name_m = re.search(r'\(name "([^"]*)"\)', block)
        if name_m:
            net_name = name_m.group(1)
            for nd in re.finditer(
                r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)', block
            ):
                node_map[(nd.group(1), nd.group(2))] = net_name
        pos = end
    return node_map, sorted({n for n in node_map.values()})


_TOP_NET_DEF_RE = re.compile(
    r'^([ \t]+)\(net (\d+) "([^"]*)"\)[ \t]*\n', re.MULTILINE
)


def _parse_existing_net_defs(pcb_text: str) -> tuple[dict[str, int], int, int, str]:
    """Parse the top-level ``(net N "name")`` defs already in the PCB.

    Returns ``(name_to_index, max_index, last_def_end, indent)`` where
    ``last_def_end`` is the byte offset just past the last existing net-def
    line (suitable as an insertion point) and ``indent`` is the leading
    whitespace used by those lines (so newly inserted lines match style).
    If no net defs exist, returns ``({}, -1, -1, "\\t")``.
    """
    name_to_idx: dict[str, int] = {}
    max_idx = -1
    last_end = -1
    indent = "\t"
    for m in _TOP_NET_DEF_RE.finditer(pcb_text):
        indent = m.group(1)
        idx = int(m.group(2))
        name = m.group(3)
        # First occurrence wins; honor the on-disk numbering.
        if name not in name_to_idx:
            name_to_idx[name] = idx
        if idx > max_idx:
            max_idx = idx
        last_end = m.end()
    return name_to_idx, max_idx, last_end, indent


def _merge_net_index(
    pcb_text: str, all_nets: list[str]
) -> tuple[str, dict[str, int], list[tuple[str, int]]]:
    """Build a unified ``{net_name: index}`` map that **preserves existing
    PCB net indices** and only assigns fresh indices to nets that aren't
    yet defined in the PCB. Inserts the missing ``(net N "name")`` lines
    just after the last existing net-def line (or after the ``(layers …)``
    block if there are none yet).

    Returns ``(new_pcb_text, net_index, new_nets_added)`` — the latter is
    the list of ``(name, idx)`` pairs that were newly inserted.
    """
    existing, max_idx, last_end, indent = _parse_existing_net_defs(pcb_text)

    net_index: dict[str, int] = dict(existing)
    if "" not in net_index:
        # Standard KiCad PCBs always have (net 0 ""); ensure it's mapped.
        net_index[""] = 0
        if max_idx < 0:
            max_idx = 0

    new_nets: list[tuple[str, int]] = []
    next_idx = max_idx + 1 if max_idx >= 0 else 1
    for n in all_nets:
        if n in net_index:
            continue
        net_index[n] = next_idx
        new_nets.append((n, next_idx))
        next_idx += 1

    bootstrap_zero = last_end < 0 and "" not in existing
    if not new_nets and not bootstrap_zero:
        return pcb_text, net_index, new_nets

    lines_to_emit: list[tuple[str, int]] = []
    if bootstrap_zero:
        # No existing net defs at all — KiCad always expects (net 0 "")
        # as the first entry in the table. Emit it as part of the bootstrap.
        lines_to_emit.append(("", 0))
    lines_to_emit.extend(new_nets)
    new_lines = "".join(
        f'{indent}(net {idx} "{nm}")\n' for nm, idx in lines_to_emit
    )

    if last_end >= 0:
        # Append directly after the last existing net-def line.
        pcb_text = pcb_text[:last_end] + new_lines + pcb_text[last_end:]
    else:
        # No existing net defs — drop them after the (layers …) block.
        layers_m = re.search(r'(\(layers\s*\n[\s\S]*?\n\t\))', pcb_text)
        if layers_m:
            insert_at = layers_m.end()
            pcb_text = (
                pcb_text[:insert_at] + "\n" + new_lines.rstrip("\n") + pcb_text[insert_at:]
            )
        else:
            first_nl = pcb_text.find("\n")
            insert_at = first_nl + 1 if first_nl > 0 else 0
            pcb_text = pcb_text[:insert_at] + new_lines + pcb_text[insert_at:]

    return pcb_text, net_index, new_nets


def _parse_all_pad_world_pos(
    pcb_text: str,
) -> list[tuple[float, float, int, str, str, str]]:
    """Collect (world_x, world_y, net_idx, net_name, ref, pin) for every pad in
    the PCB. Only pads with a non-empty net (net_idx > 0) are returned —
    they're the lookup targets for net-tag inference.
    """
    out: list[tuple[float, float, int, str, str, str]] = []
    for fp_start, fp_end in _iter_top_blocks(pcb_text, "footprint"):
        block = pcb_text[fp_start:fp_end]
        ref_m = re.search(r'\(property "Reference" "([^"]+)"', block)
        if not ref_m:
            continue
        ref = ref_m.group(1)
        # Footprint anchor + rotation + layer
        try:
            fx, fy, frot = _read_fp_pose(block)
        except Exception:
            continue
        layer = _read_fp_layer(block) or "F.Cu"
        flipped = layer.startswith("B.")
        # Iterate pads
        for p_start, p_end in _iter_pad_blocks(block):
            pad_text = block[p_start:p_end]
            pin_m = re.match(r'\(pad\s+"([^"]+)"', pad_text)
            if not pin_m:
                continue
            pin = pin_m.group(1)
            at_m = re.search(r'\(at\s+([\d.\-]+)\s+([\d.\-]+)', pad_text)
            net_m = re.search(r'\(net\s+(\d+)\s+"([^"]*)"\)', pad_text)
            if not at_m or not net_m:
                continue
            net_idx = int(net_m.group(1))
            net_name = net_m.group(2)
            if net_idx <= 0 or not net_name:
                continue
            lx, ly = float(at_m.group(1)), float(at_m.group(2))
            wx, wy = pcb_local_to_world((fx, fy), frot, lx, ly, flipped=flipped)
            out.append((wx, wy, net_idx, net_name, ref, pin))
    return out


def _find_pad_at_xy(
    pad_index: list[tuple[float, float, int, str, str, str]],
    x: float,
    y: float,
    tol_mm: float,
) -> tuple[int, str, str, str] | None:
    """Find the closest pad within tolerance of (x, y). Returns
    (net_idx, net_name, ref, pin) or None."""
    best = None
    best_d2 = tol_mm * tol_mm
    for px, py, idx, name, ref, pin in pad_index:
        d2 = (px - x) ** 2 + (py - y) ** 2
        if d2 <= best_d2:
            best_d2 = d2
            best = (idx, name, ref, pin)
    return best


@_register_text_fn("patch_track_nets_from_pads")
def patch_track_nets_from_pads_text(
    pcb_text: str,
    tolerance_mm: float = 0.5,
    include_arcs: bool = True,
    include_vias: bool = True,
    dry_run: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``patch_track_nets_from_pads``.

    Walks every top-level ``(segment ...)``, ``(arc ...)``, ``(via ...)``
    block. If the element's net is empty (``(net 0)`` or ``(net "")`` or
    ``(net 0 "")``) and one of its endpoints lands on a pad within
    ``tolerance_mm``, the element's net tag is rewritten to that pad's
    net.
    """
    pad_index = _parse_all_pad_world_pos(pcb_text)
    patched = 0
    skipped_no_pad = 0
    skipped_already_tagged = 0
    audit: list[dict[str, Any]] = []

    kinds = ["segment"]
    if include_arcs:
        kinds.append("arc")
    if include_vias:
        kinds.append("via")

    # Collect (start, end) block ranges + element kinds first to avoid
    # invalidating offsets during in-place rewrites.
    blocks_to_process: list[tuple[int, int, str]] = []
    for kind in kinds:
        for s, e in _iter_top_blocks(pcb_text, kind):
            blocks_to_process.append((s, e, kind))
    blocks_to_process.sort(key=lambda t: t[0])

    # Build output in chunks
    out_chunks: list[str] = []
    last = 0
    for s, e, kind in blocks_to_process:
        out_chunks.append(pcb_text[last:s])
        block = pcb_text[s:e]
        last = e

        # Read existing net
        net_idx_m = re.search(r'\(net\s+(\d+)(?:\s+"([^"]*)")?\)', block)
        # Also matches `(net "")` quote-only form
        net_name_only_m = re.search(r'\(net\s+"([^"]*)"\)', block)
        cur_idx = -1
        cur_name = ""
        if net_idx_m:
            cur_idx = int(net_idx_m.group(1))
            cur_name = net_idx_m.group(2) or ""
        elif net_name_only_m:
            cur_idx = 0
            cur_name = net_name_only_m.group(1)
        else:
            cur_idx = -1  # no net tag at all
            cur_name = ""

        # Skip if already tagged with a real net
        if cur_idx > 0 and cur_name:
            skipped_already_tagged += 1
            out_chunks.append(block)
            continue

        # Get representative endpoints to search
        endpoints: list[tuple[float, float]] = []
        if kind == "via":
            am = re.search(r'\(at\s+([\d.\-]+)\s+([\d.\-]+)\)', block)
            if am:
                endpoints.append((float(am.group(1)), float(am.group(2))))
        else:
            # segment / arc — both have (start) and (end); arc also has (mid)
            for tag in ("start", "end"):
                tm = re.search(
                    rf'\({tag}\s+([\d.\-]+)\s+([\d.\-]+)\)', block,
                )
                if tm:
                    endpoints.append((float(tm.group(1)), float(tm.group(2))))

        match = None
        for x, y in endpoints:
            m = _find_pad_at_xy(pad_index, x, y, tolerance_mm)
            if m is not None:
                match = m
                break

        if match is None:
            skipped_no_pad += 1
            out_chunks.append(block)
            continue

        _new_idx, new_name, ref, pin = match
        # Format-aware tag: `(net N)` on indexed-form boards (KiCad's
        # canonical routing-element form), `(net "name")` on string-form
        # boards. ensure_net_tag mutates pcb_text on indexed-form boards
        # iff the net is new to the table — but the caller built
        # `pad_index` from existing pads, so the net is guaranteed to
        # already be in the table on indexed-form boards (no-op).
        pcb_text, new_net_line, _fmt, _idx = ensure_net_tag(pcb_text, new_name)
        if net_idx_m:
            new_block = block[:net_idx_m.start()] + new_net_line + block[net_idx_m.end():]
        elif net_name_only_m:
            new_block = block[:net_name_only_m.start()] + new_net_line + block[net_name_only_m.end():]
        else:
            # No existing net tag — insert before the closing paren
            stripped = block.rstrip()
            if stripped.endswith(")"):
                body = stripped[:-1].rstrip()
                new_block = body + f"\n\t\t{new_net_line}\n\t)\n"
            else:
                new_block = block

        if new_block != block:
            patched += 1
            audit.append({
                "kind": kind,
                "net": new_name,
                "via_ref_pin": f"{ref}.{pin}",
            })
        out_chunks.append(new_block)

    out_chunks.append(pcb_text[last:])
    new_pcb = "".join(out_chunks)
    if dry_run:
        new_pcb = pcb_text  # don't actually change the text
    return new_pcb, {
        "success": True,
        "patched": patched,
        "skipped_already_tagged": skipped_already_tagged,
        "skipped_no_pad_match": skipped_no_pad,
        "tolerance_mm": tolerance_mm,
        "kinds_processed": kinds,
        "dry_run": dry_run,
        "audit": audit[:50],  # cap audit log to first 50 patches
    }


def _rename_net_in_text(
    pcb_text: str, mapping: dict[str, str],
) -> tuple[str, dict[str, int]]:
    """Rename net names everywhere they appear in the PCB text.

    Handles every KiCad-10 net-binding form:
    * ``(net N "OLD")`` → ``(net N "NEW")`` — top-level net-table entries
      and any nested pad / segment / arc / via net tags.
    * ``(net "OLD")`` → ``(net "NEW")`` — short-form (no index) variant.
    * ``(net_name "OLD")`` → ``(net_name "NEW")`` — zone net binding.

    For bulk renames (multiple pairs at once, possibly swapping
    ``A↔B``), the mapping is applied with a per-pair sentinel so a
    A→B / B→A pair becomes (A→tmp, B→A, tmp→B) and no name is double-
    substituted.

    Returns ``(new_text, counts)`` where ``counts`` is
    ``{old_name: number_of_replacements}``.
    """
    counts: dict[str, int] = {n: 0 for n in mapping}
    if not mapping:
        return pcb_text, counts

    # Three patterns to match. Each captures the OLD name (group 1 inside
    # the (net …) / (net_name …) wrapper); the surrounding prefix is kept
    # so the replacement reconstructs the identical form.
    patterns = [
        # (net N "name") — index + name
        re.compile(r'(\(net\s+\d+\s+")([^"]*)("\))'),
        # (net "name") — name only (KiCad-10 short form)
        re.compile(r'(\(net\s+")([^"]*)("\))'),
        # (net_name "name")
        re.compile(r'(\(net_name\s+")([^"]*)("\))'),
    ]

    # Sentinel-pass strategy for bulk swaps.
    # 1. For every (old → new) pair, replace OLD with a unique sentinel
    #    that cannot collide with real net names.
    # 2. After all OLDs are sentinelled, replace each sentinel with its
    #    target NEW.
    sentinels: dict[str, str] = {
        old: f"__RENAME_SENTINEL_{i}__"
        for i, old in enumerate(mapping)
    }

    # Pass 1: old → sentinel
    new_text = pcb_text
    for pat in patterns:
        def repl_pass1(m: re.Match) -> str:
            name = m.group(2)
            if name in sentinels:
                counts[name] += 1
                return f"{m.group(1)}{sentinels[name]}{m.group(3)}"
            return m.group(0)
        new_text = pat.sub(repl_pass1, new_text)

    # Pass 2: sentinel → new
    for old, new in mapping.items():
        sentinel = sentinels[old]
        # Plain string-replace is safe — sentinel is unique
        new_text = new_text.replace(sentinel, new)

    # counts in pass 1 incremented per pattern → may overcount the same
    # net if a pad has (net N "X") form (matched by pattern 1) but the
    # second pattern's negative lookahead would also see it. Re-check by
    # dividing by number of patterns is wrong. Instead, simplify the
    # counts: re-count by searching the original text for each old name
    # in any of the three forms.
    for old in mapping:
        n = 0
        for pat in patterns:
            for m in pat.finditer(pcb_text):
                if m.group(2) == old:
                    n += 1
        counts[old] = n
    return new_text, counts


@_register_text_fn("rename_net")
def rename_net_text(
    pcb_text: str, old_name: str, new_name: str,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``rename_net``. Renames a single net
    across the entire PCB (net-table + all pad / track / arc / via /
    zone bindings).
    """
    if not old_name or new_name == old_name:
        return pcb_text, {
            "success": False,
            "error": "old_name must be non-empty and differ from new_name",
        }
    new_text, counts = _rename_net_in_text(pcb_text, {old_name: new_name})
    return new_text, {
        "success": True,
        "old_name": old_name,
        "new_name": new_name,
        "replacements": counts.get(old_name, 0),
    }


@_register_text_fn("bulk_rename_nets")
def bulk_rename_nets_text(
    pcb_text: str, mapping: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``bulk_rename_nets``. Applies a
    ``{old: new}`` mapping in one pass, with sentinel-based swap support
    so that ``{"A": "B", "B": "A"}`` swaps the two names correctly.
    """
    if not mapping:
        return pcb_text, {
            "success": False,
            "error": "mapping must contain at least one (old, new) pair",
        }
    bad = [k for k, v in mapping.items() if not k or k == v]
    if bad:
        return pcb_text, {
            "success": False,
            "error": (
                f"mapping has invalid keys (empty or self-mapping): {bad}"
            ),
        }
    new_text, counts = _rename_net_in_text(pcb_text, mapping)
    return new_text, {
        "success": True,
        "mapping": mapping,
        "replacements_per_net": counts,
        "total_replacements": sum(counts.values()),
    }


def _rename_refs_in_text(
    pcb_text: str, mapping: dict[str, str],
) -> tuple[str, dict[str, int]]:
    """Rename footprint refs in the PCB. Replaces every
    ``(property "Reference" "OLD" …)`` instance. Sentinel-based swap
    so {"R1": "R2", "R2": "R1"} swaps the two correctly.

    Returns ``(new_text, counts={old: n_replacements})``.
    """
    counts: dict[str, int] = {old: 0 for old in mapping}
    if not mapping:
        return pcb_text, counts
    sentinels = {old: f"__REF_SENTINEL_{i}__" for i, old in enumerate(mapping)}
    pat = re.compile(r'(\(property\s+"Reference"\s+")([^"]+)(")')
    new_text = pcb_text

    def repl(m: re.Match) -> str:
        ref = m.group(2)
        if ref in sentinels:
            counts[ref] += 1
            return f"{m.group(1)}{sentinels[ref]}{m.group(3)}"
        return m.group(0)
    new_text = pat.sub(repl, new_text)
    for old, new in mapping.items():
        new_text = new_text.replace(sentinels[old], new)
    # Recount accurately from original text
    for old in mapping:
        counts[old] = len(
            re.findall(rf'\(property\s+"Reference"\s+"{re.escape(old)}"', pcb_text)
        )
    return new_text, counts


@_register_text_fn("bulk_rename_refs")
def bulk_rename_refs_text(
    pcb_text: str, mapping: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``bulk_rename_refs``."""
    if not mapping:
        return pcb_text, {
            "success": False,
            "error": "mapping must contain at least one (old, new) pair",
        }
    bad = [k for k, v in mapping.items() if not k or k == v]
    if bad:
        return pcb_text, {
            "success": False,
            "error": f"mapping has invalid keys: {bad}",
        }
    new_text, counts = _rename_refs_in_text(pcb_text, mapping)
    return new_text, {
        "success": True,
        "mapping": mapping,
        "replacements_per_ref": counts,
        "total_replacements": sum(counts.values()),
    }


def _bulk_set_property_in_text(
    pcb_text: str, ref_pattern: str, prop_name: str, value: str | bool,
) -> tuple[str, int, list[str]]:
    """Set a property on every footprint whose reference matches
    ``ref_pattern`` (fnmatch-style wildcard, e.g. ``"C_DNP*"``).

    For boolean knobs like ``dnp`` / ``in_bom`` / ``on_board`` /
    ``in_pos_files``, the toplevel footprint flag is rewritten
    (``(dnp yes|no)``). For text properties (``Value``,
    ``Datasheet``, custom), the matching ``(property "<name>" "<value>" …)``
    line is rewritten.

    Returns ``(new_text, n_changed, list_of_refs_touched)``.
    """
    import fnmatch
    refs_touched: list[str] = []
    out: list[str] = []
    last = 0
    BOOLEAN_FLAGS = {"dnp", "in_bom", "on_board", "in_pos_files",
                     "exclude_from_pos_files", "exclude_from_bom"}
    is_bool = prop_name in BOOLEAN_FLAGS
    bool_val: str = ""
    if is_bool:
        if isinstance(value, bool):
            bool_val = "yes" if value else "no"
        elif isinstance(value, str) and value.lower() in ("yes", "no", "true", "false"):
            bool_val = "yes" if value.lower() in ("yes", "true") else "no"
        else:
            return pcb_text, 0, []

    for fp_start, fp_end in _iter_top_blocks(pcb_text, "footprint"):
        block = pcb_text[fp_start:fp_end]
        ref_m = re.search(r'\(property "Reference" "([^"]+)"', block)
        out.append(pcb_text[last:fp_start])
        if not ref_m or not fnmatch.fnmatch(ref_m.group(1), ref_pattern):
            out.append(block)
            last = fp_end
            continue
        ref = ref_m.group(1)
        new_block = block
        if is_bool:
            # Look for existing (dnp yes|no) etc and replace, or insert
            bool_re = re.compile(rf'\({prop_name}\s+(yes|no)\)')
            if bool_re.search(new_block):
                new_block = bool_re.sub(f'({prop_name} {bool_val})', new_block, count=1)
            else:
                # Insert near top of footprint block, after the (layer …) line
                lay = re.search(r'\(layer\s+"[^"]+"\)', new_block)
                if lay:
                    insert_pos = lay.end()
                    new_block = (new_block[:insert_pos]
                                 + f'\n\t({prop_name} {bool_val})'
                                 + new_block[insert_pos:])
        else:
            # Text property
            esc = re.escape(prop_name)
            pat = re.compile(
                rf'(\(property\s+"{esc}"\s+")([^"]*)(")'
            )
            if pat.search(new_block):
                new_block = pat.sub(rf'\g<1>{value}\g<3>', new_block, count=1)
            else:
                # Skip insert for unknown property (avoid breaking layout)
                pass
        if new_block != block:
            refs_touched.append(ref)
        out.append(new_block)
        last = fp_end
    out.append(pcb_text[last:])
    return "".join(out), len(refs_touched), refs_touched


@_register_text_fn("bulk_set_property")
def bulk_set_property_text(
    pcb_text: str,
    ref_pattern: str,
    property_name: str,
    value: str | bool,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``bulk_set_property``."""
    if not ref_pattern or not property_name:
        return pcb_text, {
            "success": False,
            "error": "ref_pattern and property_name required",
        }
    new_text, n, refs = _bulk_set_property_in_text(
        pcb_text, ref_pattern, property_name, value,
    )
    return new_text, {
        "success": True,
        "ref_pattern": ref_pattern,
        "property_name": property_name,
        "value": value,
        "refs_touched": refs,
        "count": n,
    }


def _patch_pad_with_net(pad_text: str, net_tag: str) -> tuple[str, bool]:
    """Replace any existing ``(net …)`` inside the pad block with ``net_tag``,
    or insert ``net_tag`` before the closing ``)`` if missing. ``net_tag``
    is a literal S-expression like ``(net 7 "GND")`` (index form) or
    ``(net "GND")`` (string form) — produced by
    :func:`kicad_mcp.utils.pcb_net_format.ensure_pad_net_tag`.

    Returns ``(new_text, changed)``.
    """
    # Match both forms: (net N "name") indexed AND (net "name") string short.
    existing = re.compile(
        r'\(net\s+(?:\d+\s+)?"[^"]*"\)'
    )
    if existing.search(pad_text):
        replaced = existing.sub(net_tag, pad_text, count=1)
        return replaced, replaced != pad_text
    # Insert before the trailing ).
    stripped = pad_text.rstrip()
    if stripped.endswith(")"):
        body = stripped[:-1].rstrip()
        return body + f"\n\t\t\t{net_tag}\n\t\t)", True
    return pad_text, False


def _patch_pcb_nets(pcb_text: str, netlist_text: str) -> tuple[str, int, int, int]:
    """Apply netlist net tags to all matching pads in the PCB text.
    Returns ``(new_text, pads_patched, total_pads_seen, nets_added)``.

    Net-tag convention is detected from the PCB itself:

    * **Index-form PCBs** (existing ``(net N "name")`` table at the top)
      preserve their original indices; only missing nets get new indices
      appended after the last existing net-def line. Pads carry the full
      ``(net N "name")`` tag.
    * **String-form PCBs** (no top-level table, only ``(net "name")``
      short-form refs throughout — e.g. reference V13_x mainboards) keep
      that convention: no synthetic net table is inserted; pads carry
      ``(net "name")`` short-form.

    Both forms repeatable / idempotent: running the patch twice yields
    identical output (apart from the net table re-shuffle if the
    netlist names a previously unknown net).
    """
    node_map, all_nets = _parse_netlist_node_map(netlist_text)
    fmt = pcb_net_format(pcb_text)
    nets_added = 0
    if fmt == "index":
        pcb_text, _net_index, new_nets = _merge_net_index(pcb_text, all_nets)
        nets_added = len(new_nets)
    # On string-form PCBs the netlist's all_nets are emitted into the
    # pads directly — no table to merge. nets_added stays 0 (the table
    # is absent by design).

    pads_patched = 0
    total_pads = 0
    out_chunks: list[str] = []
    last = 0
    for fp_start, fp_end in _iter_top_blocks(pcb_text, "footprint"):
        block = pcb_text[fp_start:fp_end]
        ref_m = re.search(r'\(property "Reference" "([^"]+)"', block)
        new_block = block
        if ref_m:
            ref = ref_m.group(1)
            patched_block_chunks: list[str] = []
            inner_last = 0
            for p_start, p_end in _iter_pad_blocks(block):
                patched_block_chunks.append(block[inner_last:p_start])
                pad_text = block[p_start:p_end]
                total_pads += 1
                pn_m = re.match(r'\(pad\s+"([^"]+)"', pad_text)
                if pn_m:
                    pin_name = pn_m.group(1)
                    target = node_map.get((ref, pin_name))
                    if target is not None:
                        # ensure_pad_net_tag is format-aware: returns the
                        # short ``(net "name")`` on string-form boards
                        # and the long ``(net N "name")`` on index-form
                        # boards. The text mutation it performs is only
                        # the table-bootstrap on index-form (no-op on
                        # string-form), and only the first call per name
                        # actually mutates — subsequent calls are pure.
                        pcb_text_after, net_tag, _fmt, _idx = ensure_pad_net_tag(
                            pcb_text, target,
                        )
                        if pcb_text_after != pcb_text:
                            # A new (net N "name") line was inserted at
                            # the top — fp_start/fp_end / inner_last are
                            # now off by the length difference. Bail out
                            # of the per-pad loop and recompute from
                            # scratch on the patched text. This path is
                            # cold (only for newly-discovered nets on
                            # index-form PCBs) so the extra pass is fine.
                            pcb_text = pcb_text_after
                            return _patch_pcb_nets(pcb_text, netlist_text)
                        new_pad, changed = _patch_pad_with_net(pad_text, net_tag)
                        if changed:
                            pads_patched += 1
                        patched_block_chunks.append(new_pad)
                    else:
                        patched_block_chunks.append(pad_text)
                else:
                    patched_block_chunks.append(pad_text)
                inner_last = p_end
            patched_block_chunks.append(block[inner_last:])
            new_block = "".join(patched_block_chunks)
        out_chunks.append(pcb_text[last:fp_start])
        out_chunks.append(new_block)
        last = fp_end
    out_chunks.append(pcb_text[last:])
    return "".join(out_chunks), pads_patched, total_pads, nets_added


# ---------------------------------------------------------------------------
# Tool 2: resolve_pcb_footprints  (tag-based built-in resolution)
# ---------------------------------------------------------------------------


def _default_kicad_lib_root() -> str:
    """Best-effort path to the bundled KiCad footprint library root.

    Delegates to :func:`kicad_mcp.utils.path_env.kicad_lib_root` so the same
    detection logic + ``KICAD_LIB_ROOT`` override applies everywhere.
    """
    return kicad_lib_root()


def _load_kicad_mod_text(library_root: str, lib_name: str, fp_name: str) -> str | None:
    """Read the ``.kicad_mod`` file for ``lib_name:fp_name`` and return its
    top-level ``(footprint …)`` block. Returns ``None`` if not found."""
    candidate = os.path.join(library_root, f"{lib_name}.pretty", f"{fp_name}.kicad_mod")
    if not os.path.isfile(candidate):
        return None
    with open(candidate, encoding="utf-8") as fh:
        text = fh.read()
    start = text.find("(footprint")
    if start < 0:
        return None
    end = _find_block_end(text, start)
    return text[start:end]


_TAG_RE = re.compile(r"\[([^:\]]+):([^\]]+)\]")
_FIRST_AT_RE = re.compile(r"\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)")
_REF_PROP_RE = re.compile(r'\(property "Reference" "([^"]*)"')
_VAL_PROP_RE = re.compile(r'\(property "Value" "([^"]*)"')


def _patch_loaded_footprint(
    template: str, ref: str, value: str,
    x_mm: float, y_mm: float, rot_deg: float, mirror_to_bcu: bool,
) -> str:
    """Patch a ``.kicad_mod`` template into a placement-ready ``(footprint …)``
    block. Sets the footprint position/rotation, reference, value and (if
    requested) mirrors all ``F.*`` layers to ``B.*`` so it sits on the bottom
    side."""
    block = template

    # Set the footprint-HEADER position. CRUCIAL: a raw .kicad_mod has NO
    # header (at) — its first (at …) is the Reference property's *local*
    # offset. Matching "the first (at)" therefore clobbers the Reference label
    # position (so the part stacks at 0,0 and its ref designator flies off by
    # the board coordinate). Always INSERT a real header (at) right after the
    # (footprint line, and leave every property's local (at) untouched. If the
    # template already carries a header (at) (an already-placed block), replace
    # that one instead of adding a second.
    _HDR_AT = re.compile(
        r"(\(footprint[^\n]*\n(?:[ \t]*\((?:version|generator|generator_version|"
        r"layer|uuid|tedit|descr|tags|attr)\b[^\n]*\n)*)[ \t]*\(at [^\n]*\n"
    )
    hdr = f"\t(at {x_mm:.6f} {y_mm:.6f} {rot_deg:.6f})\n"
    if _HDR_AT.search(block):
        block = _HDR_AT.sub(lambda m: m.group(1) + hdr, block, count=1)
    else:
        block = re.sub(
            r"(\(footprint[^\n]*\n)", lambda m: m.group(1) + hdr, block, count=1,
        )

    # Reference + Value properties.
    block = _REF_PROP_RE.sub(f'(property "Reference" "{ref}"', block, count=1)
    block = _VAL_PROP_RE.sub(f'(property "Value" "{value}"', block, count=1)

    if mirror_to_bcu:
        # Layer-pair F↔B swap. Cover EVERY paired F.*/B.* layer KiCad
        # 10 knows — Cu, Mask, Paste, SilkS (legacy), Silkscreen (new
        # name since KiCad 8), Fab, CrtYd, Adhes. Missing any of these
        # produces a "courtyard / adhesive on wrong side" footprint
        # after ``add_placeholder_footprint(layer="B.Cu")`` (Footgun
        # K15 in CLAUDE.md §Coord-Systems).
        for f_layer, b_layer in (
            ("F.Cu", "B.Cu"),
            ("F.Mask", "B.Mask"),
            ("F.Paste", "B.Paste"),
            ("F.SilkS", "B.SilkS"),
            ("F.Silkscreen", "B.Silkscreen"),
            ("F.Fab", "B.Fab"),
            ("F.CrtYd", "B.CrtYd"),
            ("F.Adhes", "B.Adhes"),
        ):
            block = re.sub(
                rf'"{re.escape(f_layer)}"', f'"{b_layer}"', block,
            )

    return block


def _scan_placeholders(pcb_text: str) -> list[dict[str, Any]]:
    """Find every footprint whose ``Value`` contains a ``[lib:fp_name]``
    tag. Returns a list of dicts with ``start, end, ref, value_full, lib, fp,
    x, y, rot, layer``."""
    found: list[dict[str, Any]] = []
    for fp_start, fp_end in _iter_top_blocks(pcb_text, "footprint"):
        block = pcb_text[fp_start:fp_end]
        val_m = _VAL_PROP_RE.search(block)
        if not val_m:
            continue
        value_full = val_m.group(1)
        tag = _TAG_RE.search(value_full)
        if not tag:
            continue
        ref_m = _REF_PROP_RE.search(block)
        ref = ref_m.group(1) if ref_m else "?"
        at_m = _FIRST_AT_RE.search(block)
        if not at_m:
            continue
        x = float(at_m.group(1))
        y = float(at_m.group(2))
        rot = float(at_m.group(3)) if at_m.group(3) else 0.0
        layer_m = re.search(r'\(layer "([FB]\.Cu)"\)', block)
        layer = layer_m.group(1) if layer_m else "F.Cu"
        found.append(
            {
                "start": fp_start, "end": fp_end, "ref": ref,
                "value_full": value_full, "lib": tag.group(1),
                "fp": tag.group(2), "x": x, "y": y, "rot": rot,
                "layer": layer,
            }
        )
    return found


def _resolve_pcb_footprints(
    pcb_text: str, library_root: str
) -> tuple[str, int, list[str]]:
    """Replace every tagged placeholder by the real footprint loaded from the
    library. Returns ``(new_text, replaced_count, missing_libfp_list)``.
    Replacements are applied back-to-front so offsets remain valid."""
    placeholders = _scan_placeholders(pcb_text)
    placeholders.sort(key=lambda p: p["start"], reverse=True)

    replaced = 0
    missing: list[str] = []
    for ph in placeholders:
        template = _load_kicad_mod_text(library_root, ph["lib"], ph["fp"])
        if not template:
            missing.append(f"{ph['lib']}:{ph['fp']}")
            continue
        clean = _TAG_RE.sub("", ph["value_full"]).strip() or ph["fp"]
        new_block = _patch_loaded_footprint(
            template, ph["ref"], clean, ph["x"], ph["y"], ph["rot"],
            mirror_to_bcu=(ph["layer"] == "B.Cu"),
        )
        pcb_text = pcb_text[: ph["start"]] + new_block + pcb_text[ph["end"]:]
        replaced += 1
    return pcb_text, replaced, missing


# ---------------------------------------------------------------------------
# Tool 3: validate_footprints  (schematic vs PCB pad-name comparison)
# ---------------------------------------------------------------------------


def _parse_netlist_pins_per_ref(netlist_text: str) -> dict[str, set[str]]:
    """Returns ``{component_ref: {pin_name, …}}`` from a kicadsexpr netlist."""
    out: dict[str, set[str]] = {}
    map_, _ = _parse_netlist_node_map(netlist_text)
    for (ref, pin), _net in map_.items():
        out.setdefault(ref, set()).add(pin)
    return out


def _parse_pcb_pads_per_ref(pcb_text: str) -> dict[str, tuple[str, set[str]]]:
    """Returns ``{component_ref: (footprint_lib_id, {pad_name, …})}``."""
    out: dict[str, tuple[str, set[str]]] = {}
    for fp_start, fp_end in _iter_top_blocks(pcb_text, "footprint"):
        block = pcb_text[fp_start:fp_end]
        ref_m = _REF_PROP_RE.search(block)
        if not ref_m:
            continue
        ref = ref_m.group(1)
        lib_m = re.match(r'\(footprint\s+"([^"]*)"', block)
        lib_id = lib_m.group(1) if lib_m else ""
        pads: set[str] = set()
        for p_start, p_end in _iter_pad_blocks(block):
            pn_m = re.match(r'\(pad\s+"([^"]+)"', block[p_start:p_end])
            if pn_m:
                pads.add(pn_m.group(1))
        out[ref] = (lib_id, pads)
    return out


# ---------------------------------------------------------------------------
# Pure text-mutation functions (Universal Callable companions).
#
# Each function here mirrors a public MCP tool below — the MCP-decorated
# tool is a thin I/O wrapper that:
#   1. Normalises paths via to_local_path
#   2. Reads the PCB text from disk
#   3. Delegates to the _text function for the actual mutation
#   4. Writes the result back (skipped when dry_run=True)
# A generic ``pcb_batch`` tool can chain the registered _text functions
# against one in-memory PCB text without N×file-I/O.
# ---------------------------------------------------------------------------


@_register_text_fn("place_at_pivot")
def place_at_pivot_text(
    pcb_text: str,
    ref: str,
    target_x_mm: float,
    target_y_mm: float,
    pivot_kind: str = "anchor",
    pivot_arg: str = "",
    rotation_deg: float = 0.0,
    auto_rotation: str = "",
    center_x_mm: float = 0.0,
    center_y_mm: float = 0.0,
    layer: str = "",
    mod_text: str = "",
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``place_at_pivot``. ``mod_text`` is the
    .kicad_mod source for ``pivot_kind='bbox_center'`` (caller pre-loads
    it from disk)."""
    if pivot_kind not in ("anchor", "pad", "bbox_center"):
        return pcb_text, {
            "success": False,
            "error": "pivot_kind must be one of: anchor, pad, bbox_center",
        }
    if pivot_kind == "pad" and not pivot_arg:
        return pcb_text, {
            "success": False,
            "error": "pivot_kind='pad' requires pivot_arg (pad name)",
        }
    if pivot_kind == "bbox_center" and not mod_text:
        return pcb_text, {
            "success": False,
            "error": "pivot_kind='bbox_center' requires mod_text "
                     "(.kicad_mod content)",
        }
    if layer and layer not in ("F.Cu", "B.Cu"):
        return pcb_text, {
            "success": False, "error": "layer must be F.Cu or B.Cu",
        }

    fp_span = _find_footprint_block(pcb_text, ref)
    if fp_span is None:
        return pcb_text, {
            "success": False, "error": f"Footprint not found: {ref}",
        }
    fp_start, fp_end = fp_span
    block = pcb_text[fp_start:fp_end]

    if auto_rotation:
        try:
            new_rot = align_radial_rotation(
                (float(target_x_mm), float(target_y_mm)),
                (float(center_x_mm), float(center_y_mm)),
                mode=auto_rotation,
            )
        except ValueError as exc:
            return pcb_text, {"success": False, "error": str(exc)}
    else:
        new_rot = float(rotation_deg) % 360.0

    if pivot_kind == "anchor":
        pivot_local = (0.0, 0.0)
    elif pivot_kind == "pad":
        pad_at = _find_pad_at(block, pivot_arg)
        if pad_at is None:
            return pcb_text, {
                "success": False,
                "error": f"Pad '{pivot_arg}' not found in {ref}",
            }
        pivot_local = (pad_at[0], pad_at[1])
    else:
        pivot_local = bbox_center(compute_fp_bbox(mod_text))

    target_layer = layer or _read_fp_layer(block) or "F.Cu"

    # NOTE: place_at_pivot does NOT mirror pad-local coords when the
    # target layer is B.Cu — _patch_fp_pose only rewrites the (layer)
    # tag, fp.rot, and pad-local rotations. Pad-local positions stay in
    # their on-disk state, which KiCad treats as the physical position
    # regardless of layer. Therefore the anchor-from-pivot calculation
    # uses flipped=False here. Passing flipped=True ("if B.Cu, mirror
    # the pivot") double-flipped the pivot relative to where KiCad
    # actually renders it, landing the anchor on the wrong side by the
    # full pivot-X (pad pitch for SOIC, body width for bbox). Same
    # root cause as the _transform_pad_world bug.
    rotated_pivot = pcb_local_to_world(
        (0.0, 0.0), new_rot, pivot_local[0], pivot_local[1],
        flipped=False,
    )
    new_anchor = (
        float(target_x_mm) - rotated_pivot[0],
        float(target_y_mm) - rotated_pivot[1],
    )

    new_block, pads_updated = _patch_fp_pose(
        block, new_anchor, new_rot,
        layer=target_layer if layer else None,
    )

    new_text = pcb_text[:fp_start] + new_block + pcb_text[fp_end:]
    return new_text, {
        "success": True,
        "ref": ref,
        "anchor": {"x_mm": round(new_anchor[0], 4),
                   "y_mm": round(new_anchor[1], 4)},
        "rotation": round(new_rot, 4),
        "layer": target_layer,
        "pivot_kind": pivot_kind,
        "pads_updated": pads_updated,
    }


@_register_text_fn("clone_layout_around_pivot")
def clone_layout_around_pivot_text(
    pcb_text: str,
    source_ref: str,
    source_peripherals: list[str],
    target_pivots: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``clone_layout_around_pivot``."""
    if not isinstance(source_peripherals, list) or not source_peripherals:
        return pcb_text, {
            "success": False,
            "error": "source_peripherals must be a non-empty list",
        }
    if not isinstance(target_pivots, list) or not target_pivots:
        return pcb_text, {
            "success": False,
            "error": "target_pivots must be a non-empty list",
        }

    src_span = _find_footprint_block(pcb_text, source_ref)
    if src_span is None:
        return pcb_text, {
            "success": False,
            "error": f"Source anchor not found: {source_ref}",
        }
    src_pose = _read_fp_pose(pcb_text[src_span[0]:src_span[1]])

    template: list[tuple[float, float, float]] = []
    for pref in source_peripherals:
        span = _find_footprint_block(pcb_text, pref)
        if span is None:
            return pcb_text, {
                "success": False,
                "error": f"Source peripheral not found: {pref}",
            }
        pose = _read_fp_pose(pcb_text[span[0]:span[1]])
        lx, ly = pcb_world_to_local(
            (src_pose[0], src_pose[1]), src_pose[2], pose[0], pose[1],
        )
        local_rot = (pose[2] - src_pose[2]) % 360.0
        template.append((lx, ly, local_rot))

    for i, tp in enumerate(target_pivots):
        if not isinstance(tp, dict) or "anchor_ref" not in tp \
                or "peripheral_refs" not in tp:
            return pcb_text, {
                "success": False,
                "error": (
                    f"target_pivots[{i}] must be a dict with "
                    "'anchor_ref' and 'peripheral_refs'"
                ),
            }
        if not isinstance(tp["peripheral_refs"], list):
            return pcb_text, {
                "success": False,
                "error": f"target_pivots[{i}].peripheral_refs must be a list",
            }
        if len(tp["peripheral_refs"]) != len(source_peripherals):
            return pcb_text, {
                "success": False,
                "error": (
                    f"target_pivots[{i}].peripheral_refs length "
                    f"{len(tp['peripheral_refs'])} != "
                    f"source_peripherals length {len(source_peripherals)}"
                ),
            }

    details: list[dict[str, Any]] = []
    placed = 0
    for tp in target_pivots:
        anchor_ref = tp["anchor_ref"]
        anchor_span = _find_footprint_block(pcb_text, anchor_ref)
        if anchor_span is None:
            return pcb_text, {
                "success": False,
                "error": f"Target anchor not found: {anchor_ref}",
            }
        anchor_pose = _read_fp_pose(
            pcb_text[anchor_span[0]:anchor_span[1]],
        )
        for (lx, ly, local_rot), target_ref in zip(
            template, tp["peripheral_refs"],
        ):
            span = _find_footprint_block(pcb_text, target_ref)
            if span is None:
                return pcb_text, {
                    "success": False,
                    "error": f"Target peripheral not found: {target_ref}",
                }
            fp_block = pcb_text[span[0]:span[1]]
            target_layer = _read_fp_layer(fp_block) or "F.Cu"
            new_rot = (local_rot + anchor_pose[2]) % 360.0
            wx, wy = pcb_local_to_world(
                (anchor_pose[0], anchor_pose[1]), anchor_pose[2],
                lx, ly, flipped=False,
            )
            new_block, _ = _patch_fp_pose(
                fp_block, (wx, wy), new_rot, layer=None,
            )
            pcb_text = (
                pcb_text[:span[0]] + new_block + pcb_text[span[1]:]
            )
            placed += 1
            details.append({
                "ref": target_ref,
                "anchor_ref": anchor_ref,
                "x_mm": round(wx, 4),
                "y_mm": round(wy, 4),
                "rotation": round(new_rot, 4),
                "layer": target_layer,
            })

    return pcb_text, {
        "success": True,
        "source_ref": source_ref,
        "targets": len(target_pivots),
        "placed": placed,
        "details": details,
    }


# ---------------------------------------------------------------------------
# clone_routing helpers — pad-correspondence-fitted isometry (rotation OR
# reflection). Unlike clone_layout_around_pivot, the transform is *measured*
# from the actual source/target pad positions, so it reproduces mirrored
# anchor groups correctly.
# ---------------------------------------------------------------------------

_NET_STR_RE = re.compile(r'\(net\s+"((?:[^"\\]|\\.)*)"\)')
_NETTABLE_RE = re.compile(r'\(net\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\s*\)')
_WIDTH_RE = re.compile(r'\(width\s+([\d.\-]+)\)')
_VIA_SIZE_RE = re.compile(r'\(size\s+([\d.\-]+)\)')
_VIA_DRILL_RE = re.compile(r'\(drill\s+([\d.\-]+)\)')


def _fp_pad_world_coords(block: str) -> dict[str, tuple[float, float]]:
    """Map pad-number -> world ``(x, y)`` for every numbered pad in a
    footprint block, flip- and rotation-aware. First pad of a number wins
    (KiCad thermal pads can repeat a number)."""
    fx, fy, frot = _read_fp_pose(block)
    flipped = _read_fp_layer(block) == "B.Cu"
    out: dict[str, tuple[float, float]] = {}
    for m in _PAD_HEADER_INLINE_RE.finditer(block):
        num = m.group(1)
        if not num or num in out:
            continue
        lx, ly = float(m.group(3)), float(m.group(4))
        out[num] = pcb_local_to_world((fx, fy), frot, lx, ly, flipped)
    return out


def _fit_isometry(
    src: list[tuple[float, float]],
    tgt: list[tuple[float, float]],
) -> tuple[tuple[float, float, float, float, float, float], float]:
    """Least-squares 2D isometry (rotation OR reflection + translation)
    mapping ``src`` onto ``tgt``. Returns ``((a, b, c, d, tx, ty), rms)``
    where ``(x', y') = (a*x + b*y + tx, c*x + d*y + ty)`` and ``rms`` is
    the residual fit error in mm."""
    n = len(src)
    sx = sum(p[0] for p in src) / n
    sy = sum(p[1] for p in src) / n
    tx = sum(p[0] for p in tgt) / n
    ty = sum(p[1] for p in tgt) / n
    s = [(p[0] - sx, p[1] - sy) for p in src]
    t = [(p[0] - tx, p[1] - ty) for p in tgt]
    m00 = sum(t[i][0] * s[i][0] for i in range(n))
    m01 = sum(t[i][0] * s[i][1] for i in range(n))
    m10 = sum(t[i][1] * s[i][0] for i in range(n))
    m11 = sum(t[i][1] * s[i][1] for i in range(n))
    # Rotation candidate: R = [[c, -s], [s, c]]
    rot = math.atan2(m10 - m01, m00 + m11)
    cr, sr = math.cos(rot), math.sin(rot)
    rot_score = (m00 + m11) * cr + (m10 - m01) * sr
    # Reflection candidate: R = [[c, s], [s, -c]]
    ref = math.atan2(m10 + m01, m00 - m11)
    cf, sf = math.cos(ref), math.sin(ref)
    ref_score = (m00 - m11) * cf + (m10 + m01) * sf
    if rot_score >= ref_score:
        a, b, c, d = cr, -sr, sr, cr
    else:
        a, b, c, d = cf, sf, sf, -cf
    txx = tx - (a * sx + b * sy)
    tyy = ty - (c * sx + d * sy)
    sq = 0.0
    for i in range(n):
        px = a * src[i][0] + b * src[i][1] + txx
        py = c * src[i][0] + d * src[i][1] + tyy
        sq += (px - tgt[i][0]) ** 2 + (py - tgt[i][1]) ** 2
    return (a, b, c, d, txx, tyy), math.sqrt(sq / n)


def _iso_apply(
    iso: tuple[float, float, float, float, float, float],
    x: float, y: float,
) -> tuple[float, float]:
    a, b, c, d, tx, ty = iso
    return (a * x + b * y + tx, c * x + d * y + ty)


def _iter_top_routing(text: str):
    """Yield ``(kind, start, end)`` for every top-level segment/arc/via."""
    for mt in re.finditer(r"\n\t\((segment|arc|via)\b", text):
        kind = mt.group(1)
        op = text.index("(", mt.start())
        depth = 0
        for j in range(op, len(text)):
            ch = text[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    yield kind, op, j + 1
                    break


def _block_net_name(block: str, id_to_name: dict[int, str]) -> str | None:
    """Resolve a routing block's net to a name, supporting both the
    ``(net N)`` table form and the short ``(net "name")`` form."""
    sm = _NET_STR_RE.search(block)
    if sm is not None:
        return sm.group(1)
    nm = _NET_REF_RE.search(block)
    if nm is not None:
        return id_to_name.get(int(nm.group(1)))
    return None


def _block_points(block: str, kind: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    if kind == "via":
        am = _AT_RE.search(block)
        if am:
            pts.append((float(am.group(1)), float(am.group(2))))
    else:
        sm = _START_RE.search(block)
        em = _END_RE.search(block)
        if sm:
            pts.append((float(sm.group(1)), float(sm.group(2))))
        if em:
            pts.append((float(em.group(1)), float(em.group(2))))
    return pts


@_register_text_fn("clone_routing")
def clone_routing_text(
    pcb_text: str,
    source_anchor: str,
    target_anchors: list[dict[str, Any]],
    net_filter: list[str] | None = None,
    radius_mm: float = 12.0,
    bbox_xy_mm: list[float] | None = None,
    clear_target: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``clone_routing``. See the MCP tool for
    full semantics."""
    if not isinstance(target_anchors, list) or not target_anchors:
        return pcb_text, {
            "success": False,
            "error": "target_anchors must be a non-empty list",
        }
    src_span = _find_footprint_block(pcb_text, source_anchor)
    if src_span is None:
        return pcb_text, {
            "success": False,
            "error": f"Source anchor not found: {source_anchor}",
        }
    src_pads = _fp_pad_world_coords(pcb_text[src_span[0]:src_span[1]])
    src_pose = _read_fp_pose(pcb_text[src_span[0]:src_span[1]])
    if len(src_pads) < 3:
        return pcb_text, {
            "success": False,
            "error": f"Source anchor {source_anchor} has <3 numbered pads "
                     "— need >=3 for a reliable transform fit",
        }

    bbox = None
    if bbox_xy_mm is not None:
        if not (isinstance(bbox_xy_mm, list) and len(bbox_xy_mm) == 4):
            return pcb_text, {
                "success": False,
                "error": "bbox_xy_mm must be [xmin, ymin, xmax, ymax]",
            }
        bbox = tuple(float(v) for v in bbox_xy_mm)

    def _in_region(pts: list[tuple[float, float]],
                   cx: float, cy: float) -> bool:
        if not pts:
            return False
        if bbox is not None:
            return all(bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]
                       for x, y in pts)
        return all(math.hypot(x - cx, y - cy) <= radius_mm
                   for x, y in pts)

    id_to_name: dict[int, str] = {
        int(m.group(1)): m.group(2) for m in _NETTABLE_RE.finditer(pcb_text)
    }
    name_to_id: dict[str, int] = {v: k for k, v in id_to_name.items()}
    net_set = set(net_filter) if net_filter else None

    # Single board-wide format detection — used by ``_emit`` below so a
    # cloned block emits the same net-tag convention as the rest of the
    # PCB. Pre-fix the heuristic looked only at the *source* block — fine
    # when source and target are on the same board, but it didn't catch
    # the (hypothetical) future case where ``clone_layout`` cloned a
    # legacy index-form block onto a string-form board.
    board_net_format = pcb_net_format(pcb_text)

    # ---- collect source routing objects ----
    sources: list[dict[str, Any]] = []
    for kind, s0, s1 in _iter_top_routing(pcb_text):
        block = pcb_text[s0:s1]
        name = _block_net_name(block, id_to_name)
        if net_set is not None and name not in net_set:
            continue
        pts = _block_points(block, kind)
        if not _in_region(pts, src_pose[0], src_pose[1]):
            continue
        sources.append({"kind": kind, "block": block, "net": name})
    if not sources:
        return pcb_text, {
            "success": False,
            "error": "no source routing objects matched the region/"
                     "net_filter around " + source_anchor,
        }

    def _emit(kind: str, block: str, iso, new_net: str | None) -> str:
        u = str(uuid.uuid4())
        net_txt = ""
        if new_net is not None:
            if board_net_format == "string" or new_net not in name_to_id:
                net_txt = f'\n\t\t(net "{new_net}")'
            else:
                net_txt = f"\n\t\t(net {name_to_id[new_net]})"
        if kind == "via":
            am = _AT_RE.search(block)
            sz = _VIA_SIZE_RE.search(block)
            dr = _VIA_DRILL_RE.search(block)
            lp = _LAYERS_PAIR_RE.search(block)
            nx, ny = _iso_apply(iso, float(am.group(1)), float(am.group(2)))
            lay = (f'\n\t\t(layers "{lp.group(1)}" "{lp.group(2)}")'
                   if lp else "")
            return (f"\n\t(via\n\t\t(at {nx:.6f} {ny:.6f})"
                    f"\n\t\t(size {sz.group(1) if sz else '0.6'})"
                    f"\n\t\t(drill {dr.group(1) if dr else '0.3'})"
                    f"{lay}{net_txt}\n\t\t(uuid \"{u}\")\n\t)")
        sm = _START_RE.search(block)
        em = _END_RE.search(block)
        wm = _WIDTH_RE.search(block)
        lm = _LAYER_TAG_RE.search(block)
        sx, sy = _iso_apply(iso, float(sm.group(1)), float(sm.group(2)))
        ex, ey = _iso_apply(iso, float(em.group(1)), float(em.group(2)))
        width = wm.group(1) if wm else "0.2"
        layer = lm.group(1) if lm else "F.Cu"
        mid_txt = ""
        if kind == "arc":
            mm = _MID_RE.search(block)
            if mm:
                mx, my = _iso_apply(iso, float(mm.group(1)),
                                    float(mm.group(2)))
                mid_txt = f"\n\t\t(mid {mx:.6f} {my:.6f})"
        return (f"\n\t({kind}\n\t\t(start {sx:.6f} {sy:.6f})"
                f"{mid_txt}\n\t\t(end {ex:.6f} {ey:.6f})"
                f"\n\t\t(width {width})\n\t\t(layer \"{layer}\")"
                f"{net_txt}\n\t\t(uuid \"{u}\")\n\t)")

    # ---- per target ----
    new_blocks: list[str] = []
    clear_spans: list[tuple[int, int]] = []
    details: list[dict[str, Any]] = []
    for i, ta in enumerate(target_anchors):
        if not isinstance(ta, dict) or "anchor_ref" not in ta:
            return pcb_text, {
                "success": False,
                "error": f"target_anchors[{i}] needs an 'anchor_ref' key",
            }
        a_ref = ta["anchor_ref"]
        net_map = ta.get("net_map") or {}
        if not isinstance(net_map, dict):
            return pcb_text, {
                "success": False,
                "error": f"target_anchors[{i}].net_map must be a dict",
            }
        a_span = _find_footprint_block(pcb_text, a_ref)
        if a_span is None:
            return pcb_text, {
                "success": False,
                "error": f"Target anchor not found: {a_ref}",
            }
        tgt_pads = _fp_pad_world_coords(pcb_text[a_span[0]:a_span[1]])
        tgt_pose = _read_fp_pose(pcb_text[a_span[0]:a_span[1]])
        common = [p for p in src_pads if p in tgt_pads]
        if len(common) < 3:
            return pcb_text, {
                "success": False,
                "error": (f"source {source_anchor} and target {a_ref} "
                          f"share only {len(common)} numbered pads — "
                          "need >=3 to fit the transform"),
            }
        iso, rms = _fit_isometry(
            [src_pads[p] for p in common], [tgt_pads[p] for p in common],
        )
        det = iso[0] * iso[3] - iso[1] * iso[2]
        kindp = "reflection" if det < 0 else "rotation"

        mapped = {net_map.get(s["net"], s["net"]) for s in sources
                  if s["net"] is not None}
        cleared = 0
        if clear_target:
            for kind, s0, s1 in _iter_top_routing(pcb_text):
                block = pcb_text[s0:s1]
                name = _block_net_name(block, id_to_name)
                if name not in mapped:
                    continue
                pts = _block_points(block, kind)
                if _in_region(pts, tgt_pose[0], tgt_pose[1]):
                    clear_spans.append((s0, s1))
                    cleared += 1

        for s in sources:
            new_net = net_map.get(s["net"], s["net"])
            new_blocks.append(_emit(s["kind"], s["block"], iso, new_net))
        details.append({
            "anchor_ref": a_ref,
            "transform": kindp,
            "fit_rms_mm": round(rms, 4),
            "pads_fitted": len(common),
            "cloned": len(sources),
            "cleared": cleared,
        })

    out = pcb_text
    for s0, s1 in sorted(clear_spans, reverse=True):
        out = out[:s0] + out[s1:]
    insert_at = out.rfind("\n)")
    if insert_at < 0:
        return pcb_text, {
            "success": False, "error": "malformed PCB: no closing paren",
        }
    out = out[:insert_at] + "".join(new_blocks) + out[insert_at:]
    return out, {
        "success": True,
        "source_anchor": source_anchor,
        "source_objects": len(sources),
        "targets": len(target_anchors),
        "cloned_total": len(new_blocks),
        "cleared_total": len(clear_spans),
        "details": details,
    }


@_register_text_fn("delete_pcb_routing")
def delete_pcb_routing_text(
    pcb_text: str,
    net_name: str = "",
    layer: str = "",
    element_kinds: list[str] | None = None,
    bbox_xy_mm: list[float] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``delete_pcb_routing``."""
    kinds = list(element_kinds) if element_kinds else \
        ["segment", "arc", "via"]
    bad = [k for k in kinds if k not in ("segment", "arc", "via")]
    if bad:
        return pcb_text, {
            "success": False,
            "error": "element_kinds may only contain "
                     f"'segment', 'arc', 'via' — got {bad}",
        }

    bbox = None
    if bbox_xy_mm is not None:
        if not (isinstance(bbox_xy_mm, list) and len(bbox_xy_mm) == 4):
            return pcb_text, {
                "success": False,
                "error": "bbox_xy_mm must be a list of 4 floats "
                         "[xmin, ymin, xmax, ymax]",
            }
        bbox = (
            float(bbox_xy_mm[0]), float(bbox_xy_mm[1]),
            float(bbox_xy_mm[2]), float(bbox_xy_mm[3]),
        )

    # Build name_to_id from both formats: indexed-form (net N "name")
    # table entries AND string-form (net "name") refs anywhere in the
    # body. String-form refs have no numeric id, so they get sentinel 0
    # in the reverse map — the matcher only uses the *name* set anyway.
    net_idx_re = re.compile(r'\(net\s+(\d+)\s+"([^"]*)"\s*\)')
    name_to_id: dict[str, int] = {
        m.group(2): int(m.group(1)) for m in net_idx_re.finditer(pcb_text)
    }
    id_to_name: dict[int, str] = {v: k for k, v in name_to_id.items()}
    net_str_re = re.compile(r'\(net\s+"((?:[^"\\]|\\.)*)"\s*\)')
    for m in net_str_re.finditer(pcb_text):
        name_to_id.setdefault(m.group(1), 0)

    match_net_names: set[str] | None
    if net_name == "":
        match_net_names = None
    else:
        if net_name not in name_to_id:
            return pcb_text, {
                "success": False,
                "error": f"Net not found in PCB: {net_name!r}. "
                         "Pass an empty string to match all nets.",
            }
        match_net_names = {net_name}

    deletions: list[tuple[int, int, str, dict[str, Any]]] = []
    for kind in ("segment", "arc", "via"):
        if kind not in kinds:
            continue
        for start, end in _iter_top_blocks(pcb_text, kind):
            block = pcb_text[start:end]
            if not _is_routing_block_top(pcb_text, start, end):
                continue
            if not _block_matches(
                block, kind, match_net_names, layer, bbox,
                id_to_name=id_to_name,
            ):
                continue
            deletions.append(
                (start, end, kind,
                 _block_descriptor(block, kind, name_to_id)),
            )

    by_kind = {"segment": 0, "arc": 0, "via": 0}
    for _, _, kind, _desc in deletions:
        by_kind[kind] += 1

    if not deletions:
        return pcb_text, {
            "success": True,
            "deleted": 0,
            "by_kind": by_kind,
        }

    deletions.sort(key=lambda r: r[0], reverse=True)
    new_text = pcb_text
    for start, end, _kind, _desc in deletions:
        line_start = new_text.rfind("\n", 0, start) + 1
        if new_text[line_start:start].strip() == "":
            trail = end
            if trail < len(new_text) and new_text[trail] == "\n":
                trail += 1
            new_text = new_text[:line_start] + new_text[trail:]
        else:
            new_text = new_text[:start] + new_text[end:]

    return new_text, {
        "success": True,
        "deleted": len(deletions),
        "by_kind": by_kind,
        "preview": [d for _, _, _, d in deletions[:20]],
    }


@_register_text_fn("update_pcb_from_schematic")
def update_pcb_from_schematic_text(
    pcb_text: str,
    netlist_text: str,
    library_root: str,
    add_new: bool = True,
    update_values: bool = True,
    update_footprints: bool = True,
    sync_nets: bool = True,
    remove_orphans: bool = False,
    stage_position_x_mm: float = 250.0,
    stage_position_y_mm: float = 50.0,
    stage_pitch_mm: float = 2.5,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``update_pcb_from_schematic``. The
    netlist text is supplied by the caller (the MCP wrapper runs
    kicad-cli to derive it). ``library_root`` must be a valid path
    to the bundled KiCad footprint library."""
    if not (library_root and os.path.isdir(library_root)):
        return pcb_text, {
            "success": False,
            "error": f"library_root invalid: {library_root!r}",
        }
    sch_components = _parse_netlist_components(netlist_text)
    if not sch_components:
        return pcb_text, {
            "success": False,
            "error": "Schematic netlist had no (components …) section.",
        }
    pcb_components = _parse_pcb_components(pcb_text)
    sch_refs = set(sch_components)
    pcb_refs = set(pcb_components)

    added_refs: list[str] = []
    missing_libs: list[str] = []
    updated_values: list[dict[str, str]] = []
    updated_footprints: list[dict[str, str]] = []
    removed_refs: list[str] = []

    new_refs = sorted(sch_refs - pcb_refs)
    orphan_refs = sorted(pcb_refs - sch_refs)
    for ref in sorted(sch_refs & pcb_refs):
        sch_val = sch_components[ref]["value"]
        sch_fp = sch_components[ref]["footprint"]
        pcb_val = pcb_components[ref]["value"]
        pcb_fp = pcb_components[ref]["lib_id"]
        if update_values and sch_val and sch_val != pcb_val:
            updated_values.append({
                "ref": ref, "from": pcb_val, "to": sch_val,
            })
        if update_footprints and sch_fp and sch_fp != pcb_fp:
            updated_footprints.append({
                "ref": ref, "from": pcb_fp, "to": sch_fp,
            })

    if update_values and updated_values:
        for entry in updated_values:
            pcb_text = _update_fp_value(
                pcb_text, entry["ref"], entry["to"],
            )

    if update_footprints and updated_footprints:
        for entry in updated_footprints:
            new_text, ok = _swap_fp_library(
                pcb_text, entry["ref"], entry["to"], library_root,
            )
            if ok:
                pcb_text = new_text
            else:
                missing_libs.append(entry["to"])

    if remove_orphans and orphan_refs:
        for ref in orphan_refs:
            pcb_text, ok = _remove_fp(pcb_text, ref)
            if ok:
                removed_refs.append(ref)

    if add_new and new_refs:
        stage_row, stage_col = 0, 0
        cols_per_row = 8
        for ref in new_refs:
            fp_lib = sch_components[ref]["footprint"]
            value = sch_components[ref]["value"] or fp_lib
            if not fp_lib or ":" not in fp_lib:
                missing_libs.append(
                    f"{ref} (schematic Footprint property "
                    f"unset or malformed: {fp_lib!r})"
                )
                continue
            lib_name, fp_name = fp_lib.split(":", 1)
            template = _load_kicad_mod_text(
                library_root, lib_name, fp_name,
            )
            if template is None:
                missing_libs.append(fp_lib)
                continue
            x = stage_position_x_mm + stage_col * stage_pitch_mm
            y = stage_position_y_mm + stage_row * stage_pitch_mm
            stage_col += 1
            if stage_col >= cols_per_row:
                stage_col = 0
                stage_row += 1
            new_block = _patch_loaded_footprint(
                template, ref, value, x, y, 0.0, mirror_to_bcu=False,
            )
            last = pcb_text.rstrip().rfind(")")
            pcb_text = (
                pcb_text[:last] + "\n" + new_block + pcb_text[last:]
            )
            added_refs.append(ref)

    nets_added = 0
    pads_patched = 0
    if sync_nets:
        pcb_text, pads_patched, _total, nets_added = _patch_pcb_nets(
            pcb_text, netlist_text,
        )

    return pcb_text, {
        "success": True,
        "added": added_refs,
        "updated_values": updated_values,
        "updated_footprints": updated_footprints,
        "removed": removed_refs,
        "missing_libraries": missing_libs,
        "nets_added": nets_added,
        "pads_patched": pads_patched,
    }


# ---------------------------------------------------------------------------
# Tool 4: rotate_pcb  (delegates to pcbnew when available, no FootprintLoad)
# ---------------------------------------------------------------------------


def _try_pcbnew_rotate(pcb_path: str, angle_deg: float) -> dict[str, Any]:
    """Rotate the entire board around ``(0,0)`` using the pcbnew API. Falls
    back to an error dict if pcbnew is not importable in the current Python
    environment.
    """
    try:
        try:
            import wx as _wx  # type: ignore
            _wx.DisableAsserts()  # pylint: disable=no-member
        except Exception:
            pass
        import pcbnew  # type: ignore
        try:
            import wx as _wx2  # type: ignore
            _wx2.DisableAsserts()  # pylint: disable=no-member
        except Exception:
            pass
    except Exception as exc:  # pragma: no cover - import-time only
        return {
            "success": False,
            "error": f"pcbnew unavailable: {exc!s}. Run rotate_pcb from the "
                     f"KiCad-bundled Python interpreter.",
        }
    board = pcbnew.LoadBoard(pcb_path)
    rot = pcbnew.EDA_ANGLE(angle_deg, pcbnew.DEGREES_T)
    origin = pcbnew.VECTOR2I(0, 0)

    n_fp = sum(1 for _ in board.GetFootprints())
    n_tr = sum(1 for _ in board.GetTracks())
    n_dr = sum(1 for _ in board.GetDrawings())
    n_zn = 0
    try:
        n_zn = sum(1 for _ in board.Zones())
    except AttributeError:  # pragma: no cover - older API fallback
        pass

    for fp in board.GetFootprints():
        fp.Rotate(origin, rot)
    for tr in board.GetTracks():
        tr.Rotate(origin, rot)
    for d in board.GetDrawings():
        d.Rotate(origin, rot)
    try:
        for z in board.Zones():
            z.Rotate(origin, rot)
    except AttributeError:  # pragma: no cover
        pass

    pcbnew.SaveBoard(pcb_path, board)
    return {
        "success": True,
        "angle_deg": angle_deg,
        "footprints_rotated": n_fp,
        "tracks_rotated": n_tr,
        "drawings_rotated": n_dr,
        "zones_rotated": n_zn,
    }


# ---------------------------------------------------------------------------
# Helpers + pure text-functions for the 5 new PCB-edit tools (2026-05-20)
#
# add_segment / delete_footprint / add_footprint_text /
# set_footprint_3d_model / set_footprint_property_visibility
# ---------------------------------------------------------------------------


def _insert_before_root_close(pcb_text: str, blob: str) -> str:
    """Insert ``blob`` just before the final closing ``)`` of the
    ``(kicad_pcb …)`` root expression."""
    last = pcb_text.rstrip().rfind(")")
    return pcb_text[:last] + blob + pcb_text[last:]


def _segment_block(
    p1: tuple[float, float], p2: tuple[float, float],
    width_mm: float, layer: str, net_tag: str,
) -> str:
    return (
        "\t(segment\n"
        f"\t\t(start {p1[0]:.6f} {p1[1]:.6f})\n"
        f"\t\t(end {p2[0]:.6f} {p2[1]:.6f})\n"
        f"\t\t(width {width_mm:.6f})\n"
        f'\t\t(layer "{layer}")\n'
        f"\t\t{net_tag}\n"
        f'\t\t(uuid "{uuid.uuid4()}")\n'
        "\t)\n"
    )


def _read_fp_header_info(block: str) -> dict[str, Any]:
    """Extract ``lib_id`` and position from a footprint block header."""
    out: dict[str, Any] = {"lib_id": "", "position": [0.0, 0.0, 0.0]}
    head_m = re.search(r'\(footprint\s+"([^"]+)"', block)
    if head_m:
        out["lib_id"] = head_m.group(1)
    pose = _read_fp_pose(block)
    out["position"] = [pose[0], pose[1], pose[2]]
    return out


@_register_text_fn("add_segment")
def add_segment_text(
    pcb_text: str,
    start_x_mm: float, start_y_mm: float,
    end_x_mm: float, end_y_mm: float,
    layer: str,
    net_name: str,
    width_mm: float = 0.25,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``add_segment``."""
    if not isinstance(layer, str) or not layer.endswith(".Cu"):
        return pcb_text, {
            "success": False,
            "error": f"layer must be a copper layer (*.Cu) — got {layer!r}",
        }
    if start_x_mm == end_x_mm and start_y_mm == end_y_mm:
        return pcb_text, {
            "success": False,
            "error": "start and end coincide — degenerate segment.",
        }
    new_text, net_tag, net_fmt, net_id = ensure_net_tag(pcb_text, net_name)
    blob = _segment_block(
        (float(start_x_mm), float(start_y_mm)),
        (float(end_x_mm), float(end_y_mm)),
        float(width_mm), layer, net_tag,
    )
    new_text = _insert_before_root_close(new_text, blob)
    return new_text, {
        "success": True,
        "segment_added": {
            "start": [round(float(start_x_mm), 4),
                      round(float(start_y_mm), 4)],
            "end": [round(float(end_x_mm), 4),
                    round(float(end_y_mm), 4)],
            "layer": layer,
            "net": net_name,
            "net_id": net_id,
            "net_format": net_fmt,
            "width": round(float(width_mm), 4),
        },
    }


@_register_text_fn("delete_footprint")
def delete_footprint_text(
    pcb_text: str, ref: str,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``delete_footprint``."""
    span = _find_footprint_block(pcb_text, ref)
    if span is None:
        return pcb_text, {
            "success": False,
            "error": f"Footprint with Reference {ref!r} not found.",
        }
    start, end = span
    block = pcb_text[start:end]
    info = _read_fp_header_info(block)
    # Trim leading indentation on the line + trailing newline so the
    # surrounding whitespace stays clean.
    line_start = pcb_text.rfind("\n", 0, start) + 1
    if pcb_text[line_start:start].strip() == "":
        del_start = line_start
    else:
        del_start = start
    del_end = end
    if del_end < len(pcb_text) and pcb_text[del_end] == "\n":
        del_end += 1
    new_text = pcb_text[:del_start] + pcb_text[del_end:]
    return new_text, {
        "success": True,
        "deleted": {
            "ref": ref,
            "lib_id": info["lib_id"],
            "position": info["position"],
        },
    }


@_register_text_fn("add_footprint_text")
def add_footprint_text_text(
    pcb_text: str,
    ref: str,
    text: str,
    local_x_mm: float,
    local_y_mm: float,
    layer: str,
    font_size_mm: float = 0.8,
    font_thickness_mm: float = 0.12,
    rotation_deg: float = 0,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``add_footprint_text``."""
    span = _find_footprint_block(pcb_text, ref)
    if span is None:
        return pcb_text, {
            "success": False,
            "error": f"Footprint with Reference {ref!r} not found.",
        }
    start, end = span
    # Find the indentation prefix of this footprint block.
    line_start = pcb_text.rfind("\n", 0, start) + 1
    fp_indent = pcb_text[line_start:start]
    if fp_indent.strip() != "":
        fp_indent = "\t"
    child_indent = fp_indent + "\t"

    safe_text = text.replace('"', '\\"')
    rot_int = int(round(float(rotation_deg))) % 360
    if rot_int != 0:
        at_str = (f"{float(local_x_mm):.6f} "
                  f"{float(local_y_mm):.6f} {rot_int}")
    else:
        at_str = f"{float(local_x_mm):.6f} {float(local_y_mm):.6f}"

    is_back = isinstance(layer, str) and layer.startswith("B.")
    effects_lines = [
        f"{child_indent}\t(effects",
        f"{child_indent}\t\t(font",
        f"{child_indent}\t\t\t(size {float(font_size_mm):.6f} "
        f"{float(font_size_mm):.6f})",
        f"{child_indent}\t\t\t(thickness {float(font_thickness_mm):.6f})",
        f"{child_indent}\t\t)",
    ]
    if is_back:
        effects_lines.append(f"{child_indent}\t\t(justify mirror)")
    effects_lines.append(f"{child_indent}\t)")

    new_block_lines = [
        f"{child_indent}(fp_text user \"{safe_text}\"",
        f"{child_indent}\t(at {at_str})",
        f"{child_indent}\t(layer \"{layer}\")",
        f"{child_indent}\t(uuid \"{uuid.uuid4()}\")",
    ] + effects_lines + [f"{child_indent})"]
    new_block = "\n".join(new_block_lines) + "\n"

    # Insert before the footprint's closing ``)``.
    # The footprint block ends at index `end`, so closing ')' is at `end-1`.
    # Find the line that contains it.
    close_line_start = pcb_text.rfind("\n", start, end - 1) + 1
    new_text = (
        pcb_text[:close_line_start] + new_block + pcb_text[close_line_start:]
    )
    return new_text, {
        "success": True,
        "text_added": {
            "ref": ref,
            "text": text,
            "layer": layer,
            "local_xy": [round(float(local_x_mm), 4),
                         round(float(local_y_mm), 4)],
            "rotation": rot_int,
        },
    }


_FP_MODEL_HEAD_RE = re.compile(r'\(model\s+"')


def _find_fp_model_span(block: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` offsets of the ``(model …)`` block inside a
    footprint block, or ``None`` if absent."""
    m = _FP_MODEL_HEAD_RE.search(block)
    if m is None:
        return None
    start = m.start()
    end = _find_block_end(block, start)
    return (start, end)


def _fmt_model_block(
    indent: str,
    model_path: str,
    offset_xyz: tuple[float, float, float],
    scale_xyz: tuple[float, float, float],
    rotate_xyz: tuple[float, float, float],
) -> str:
    safe = model_path.replace('"', '\\"')
    return (
        f'{indent}(model "{safe}"\n'
        f'{indent}\t(offset\n'
        f'{indent}\t\t(xyz {offset_xyz[0]} {offset_xyz[1]} '
        f'{offset_xyz[2]})\n'
        f'{indent}\t)\n'
        f'{indent}\t(scale\n'
        f'{indent}\t\t(xyz {scale_xyz[0]} {scale_xyz[1]} '
        f'{scale_xyz[2]})\n'
        f'{indent}\t)\n'
        f'{indent}\t(rotate\n'
        f'{indent}\t\t(xyz {rotate_xyz[0]} {rotate_xyz[1]} '
        f'{rotate_xyz[2]})\n'
        f'{indent}\t)\n'
        f'{indent})\n'
    )


@_register_text_fn("set_footprint_3d_model")
def set_footprint_3d_model_text(
    pcb_text: str,
    ref: str,
    model_path: str,
    offset_xyz: list[float] | None = None,
    scale_xyz: list[float] | None = None,
    rotate_xyz: list[float] | None = None,
    replace_existing: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``set_footprint_3d_model``."""
    off = tuple(offset_xyz) if offset_xyz else (0.0, 0.0, 0.0)
    sca = tuple(scale_xyz) if scale_xyz else (1.0, 1.0, 1.0)
    rot = tuple(rotate_xyz) if rotate_xyz else (0.0, 0.0, 0.0)
    for name, t in (("offset_xyz", off), ("scale_xyz", sca),
                    ("rotate_xyz", rot)):
        if len(t) != 3:
            return pcb_text, {
                "success": False,
                "error": f"{name} must be a list of exactly 3 numbers.",
            }
    off3 = (float(off[0]), float(off[1]), float(off[2]))
    sca3 = (float(sca[0]), float(sca[1]), float(sca[2]))
    rot3 = (float(rot[0]), float(rot[1]), float(rot[2]))

    span = _find_footprint_block(pcb_text, ref)
    if span is None:
        return pcb_text, {
            "success": False,
            "error": f"Footprint with Reference {ref!r} not found.",
        }
    fp_start, fp_end = span
    block = pcb_text[fp_start:fp_end]

    # Determine indent of footprint contents — look at the line of the
    # closing ')' of the footprint.
    close_line_start = pcb_text.rfind("\n", fp_start, fp_end - 1) + 1
    close_line_indent = pcb_text[close_line_start:fp_end - 1]
    if close_line_indent.strip() == "":
        child_indent = close_line_indent + "\t"
    else:
        child_indent = "\t\t"

    existing = _find_fp_model_span(block)
    new_model = _fmt_model_block(child_indent, model_path, off3, sca3, rot3)

    if existing is not None and replace_existing:
        m_start, m_end = existing
        abs_start = fp_start + m_start
        abs_end = fp_start + m_end
        # Also strip trailing newline that belonged to the old block.
        if abs_end < len(pcb_text) and pcb_text[abs_end] == "\n":
            abs_end += 1
        # And strip the model's leading indent on that line.
        line_start = pcb_text.rfind("\n", fp_start, abs_start) + 1
        if pcb_text[line_start:abs_start].strip() == "":
            abs_start = line_start
        new_text = pcb_text[:abs_start] + new_model + pcb_text[abs_end:]
        replaced = True
    elif existing is not None and not replace_existing:
        return pcb_text, {
            "success": False,
            "error": (
                f"Footprint {ref!r} already has a (model ...) block and "
                "replace_existing=False."
            ),
        }
    else:
        # Insert before the footprint's closing ')'.
        new_text = (
            pcb_text[:close_line_start] + new_model
            + pcb_text[close_line_start:]
        )
        replaced = False

    return new_text, {
        "success": True,
        "model_set": {
            "ref": ref,
            "model_path": model_path,
            "offset": list(off3),
            "scale": list(sca3),
            "rotate": list(rot3),
            "replaced_existing": replaced,
        },
        "note": (
            "Call ipc_revert (NOT ipc_save) to apply in GUI — "
            "ipc_save strips (model) overrides."
        ),
    }


@_register_text_fn("set_footprint_property_visibility")
def set_footprint_property_visibility_text(
    pcb_text: str,
    ref: str,
    property_name: str,
    hide: bool,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``set_footprint_property_visibility``."""
    span = _find_footprint_block(pcb_text, ref)
    if span is None:
        return pcb_text, {
            "success": False,
            "error": f"Footprint with Reference {ref!r} not found.",
        }
    fp_start, fp_end = span
    block = pcb_text[fp_start:fp_end]

    # Find the (property "<name>" ...) sub-block.
    prop_pat = re.compile(
        rf'\(property\s+"{re.escape(property_name)}"\s+'
    )
    pm = prop_pat.search(block)
    if pm is None:
        return pcb_text, {
            "success": False,
            "error": (
                f"Property {property_name!r} not found on footprint {ref!r}."
            ),
        }
    prop_start = pm.start()
    prop_end = _find_block_end(block, prop_start)
    prop_block = block[prop_start:prop_end]

    hide_yes_re = re.compile(r'[ \t]*\(hide\s+yes\)[ \t]*\n?')
    hide_no_re = re.compile(r'(\(hide\s+)no(\))')
    has_yes = hide_yes_re.search(prop_block) is not None
    has_no = re.search(r'\(hide\s+no\)', prop_block) is not None
    hide_before = has_yes  # treat "(hide no)" or absent as not-hidden

    new_prop = prop_block
    if hide:
        if has_yes:
            pass  # already hidden, no-op
        elif has_no:
            new_prop = hide_no_re.sub(r'\1yes\2', new_prop, count=1)
        else:
            # Insert (hide yes) after (layer "..."). Determine indent from
            # the (layer line.
            layer_m = re.search(
                r'(\n([ \t]+)\(layer\s+"[^"]+"\)[ \t]*\n)', new_prop,
            )
            if layer_m is not None:
                indent = layer_m.group(2)
                insert_at = layer_m.end()
                new_prop = (
                    new_prop[:insert_at]
                    + f"{indent}(hide yes)\n"
                    + new_prop[insert_at:]
                )
            else:
                # Fallback: inject before closing ')'.
                last_close = new_prop.rfind(")")
                line_start = new_prop.rfind("\n", 0, last_close) + 1
                indent = new_prop[line_start:last_close]
                if indent.strip() != "":
                    indent = "\t\t\t"
                new_prop = (
                    new_prop[:line_start]
                    + f"{indent}(hide yes)\n"
                    + new_prop[line_start:]
                )
    else:
        if has_yes:
            new_prop = hide_yes_re.sub("", new_prop, count=1)
        if has_no:
            # Strip explicit (hide no) too — it's default-implied.
            new_prop = re.sub(
                r'[ \t]*\(hide\s+no\)[ \t]*\n?', "", new_prop, count=1,
            )

    hide_after = (
        hide and (has_yes or has_no or
                  hide_yes_re.search(new_prop) is not None)
    ) or (hide_yes_re.search(new_prop) is not None)

    abs_prop_start = fp_start + prop_start
    abs_prop_end = fp_start + prop_end
    new_text = pcb_text[:abs_prop_start] + new_prop + pcb_text[abs_prop_end:]
    return new_text, {
        "success": True,
        "property": {
            "ref": ref,
            "name": property_name,
            "hide_before": bool(hide_before),
            "hide_after": bool(hide_after),
        },
    }


@_register_text_fn("cluster_block_outside_pcb")
def cluster_block_outside_pcb_text(
    pcb_text: str,
    refs: list[str],
    cluster_phi_deg: float,
    cluster_r_mm: float = 42.0,
    pcb_center_x_mm: float = 0.0,
    pcb_center_y_mm: float = 0.0,
    grid_cols: int = 4,
    spacing_t_mm: float = 5.0,
    spacing_r_mm: float = 5.0,
    align_mode: str = "radial_in",
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``cluster_block_outside_pcb``.

    ``refs`` is pre-resolved by the MCP wrapper from the schematic's
    ``kicad-mcp.group`` tag — this function does not touch the
    schematic. Each ref is placed via :func:`place_at_pivot_text`
    with ``pivot_kind="anchor"`` so no .kicad_mod load is required.
    """
    if not isinstance(refs, list) or not refs:
        return pcb_text, {
            "success": False, "error": "refs must be a non-empty list",
        }
    if grid_cols <= 0:
        return pcb_text, {
            "success": False, "error": "grid_cols must be > 0",
        }
    if align_mode not in (
        "radial_in", "radial_out", "tangential_cw", "tangential_ccw",
    ):
        return pcb_text, {
            "success": False,
            "error": "align_mode must be one of radial_in / radial_out "
                     "/ tangential_cw / tangential_ccw",
        }

    phi_rad = math.radians(cluster_phi_deg)
    # KiCad uses y-down; math convention is y-up. Negate sin for the
    # y-component.
    cluster_x = pcb_center_x_mm + cluster_r_mm * math.cos(phi_rad)
    cluster_y = pcb_center_y_mm - cluster_r_mm * math.sin(phi_rad)

    # Radial-outward unit vector in KiCad y-down coords.
    rx = math.cos(phi_rad)
    ry = -math.sin(phi_rad)
    # Tangential CCW in y-down = radial rotated 90° in math (CCW).
    tx = -math.sin(phi_rad)
    ty = -math.cos(phi_rad)

    new_text = pcb_text
    placed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for i, ref in enumerate(refs):
        col = i % grid_cols
        row = i // grid_cols
        # Centre the grid tangentially around the cluster centre.
        t_off = (col - (grid_cols - 1) / 2.0) * spacing_t_mm
        # Rows grow radially outward (further from PCB centre).
        r_off = row * spacing_r_mm
        target_x = cluster_x + t_off * tx + r_off * rx
        target_y = cluster_y + t_off * ty + r_off * ry

        new_text, result = place_at_pivot_text(
            new_text, ref, target_x, target_y,
            pivot_kind="anchor",
            auto_rotation=align_mode,
            center_x_mm=pcb_center_x_mm,
            center_y_mm=pcb_center_y_mm,
        )
        if not result.get("success"):
            errors.append({
                "ref": ref,
                "error": result.get("error", "unknown"),
            })
            continue
        placed.append({
            "ref": ref,
            "row": row, "col": col,
            "x_mm": round(target_x, 4),
            "y_mm": round(target_y, 4),
            "rotation": result.get("rotation"),
        })

    return new_text, {
        "success": len(errors) == 0,
        "placed_count": len(placed),
        "error_count": len(errors),
        "placed": placed,
        "errors": errors,
        "cluster_center": {
            "x_mm": round(cluster_x, 4),
            "y_mm": round(cluster_y, 4),
        },
    }


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register_pcb_patch_tools(mcp: FastMCP) -> None:
    """Register PCB text-patcher tools with the MCP server."""

    @mcp.tool()
    def patch_pcb_nets_from_netlist(
        pcb_path: str, netlist_path: str
    ) -> dict[str, Any]:
        """Apply schematic netlist net tags to all matching PCB pads.

        Equivalent to KiCad's GUI command "Tools → Update PCB from Schematic"
        (F8) for the net-assignment portion, but runs without any KiCad GUI
        and without the SWIG ``FootprintLoad`` quirk. The netlist must have
        been produced by ``kicad-cli sch export netlist --format kicadsexpr``.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file.
            netlist_path: Path to a ``.net`` file in kicadsexpr format.

        Returns:
            Dict with ``success``, ``pads_patched``, ``total_pads``,
            ``nets_added`` (or ``error`` on failure).
        """
        pcb_path = to_local_path(pcb_path)
        netlist_path = to_local_path(netlist_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if not os.path.isfile(netlist_path):
            return {"success": False, "error": f"Netlist not found: {netlist_path}"}
        try:
            pcb_text = get_text(pcb_path)
            with open(netlist_path, encoding="utf-8") as fh:
                net_text = fh.read()
            new_pcb, n_patched, n_total, n_nets = _patch_pcb_nets(pcb_text, net_text)
            write_text(pcb_path, new_pcb)
            return {
                "success": True,
                "pads_patched": n_patched,
                "total_pads": n_total,
                "nets_added": n_nets,
                "pcb_path": pcb_path,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def resolve_pcb_footprints(
        pcb_path: str, library_root: str = ""
    ) -> dict[str, Any]:
        """Replace ``[lib:footprint]``-tagged placeholder footprints with the
        real KiCad built-in footprint loaded from disk.

        Looks at every footprint's ``Value`` property. If it contains a marker
        of the form ``[Capacitor_SMD:C_0402_1005Metric]`` (anywhere in the
        value text), the placeholder is replaced by the corresponding
        ``.kicad_mod`` block. Footprints without such a tag are left untouched.

        This bypasses the SWIG ``FootprintLoad`` quirk on KiCad 10 entirely
        because no Python pcbnew API is invoked.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file.
            library_root: Optional path to the KiCad ``footprints`` directory
                that contains ``*.pretty/`` library folders. If empty, falls
                back to ``KICAD_LIB_ROOT`` env var, then a few well-known
                install paths.

        Returns:
            Dict with ``success``, ``replaced``, ``missing`` (list of
            ``lib:fp`` references whose ``.kicad_mod`` file was not found),
            ``library_root`` (the resolved path).
        """
        pcb_path = to_local_path(pcb_path)
        library_root = to_local_path(library_root) if library_root else ""
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        root = library_root or _default_kicad_lib_root()
        if not root:
            return {
                "success": False,
                "error": "No KiCad footprint library root found. Pass "
                         "'library_root=' explicitly or set KICAD_LIB_ROOT.",
            }
        if not os.path.isdir(root):
            return {"success": False, "error": f"Library root not a dir: {root}"}
        try:
            pcb_text = get_text(pcb_path)
            new_pcb, replaced, missing = _resolve_pcb_footprints(pcb_text, root)
            write_text(pcb_path, new_pcb)
            return {
                "success": True,
                "replaced": replaced,
                "missing": missing,
                "library_root": root,
                "pcb_path": pcb_path,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def validate_footprints(
        pcb_path: str, netlist_path: str
    ) -> dict[str, Any]:
        """Cross-check schematic-pin assignments against actual PCB pad names.

        Identifies four classes of issues *before* routing:

          * ``perfect_match`` — pad set in PCB equals pin set in schematic;
          * ``mismatches`` — same component ref on both sides but pad names
            differ (footprint pin numbering vs. schematic symbol pin
            numbering — common when a footprint has an Exposed Pad or a
            different pin-count variant);
          * ``pcb_only`` — footprints in PCB without any schematic counterpart
            (mounting holes, test points, mechanical-only footprints);
          * ``schematic_only`` — components in the schematic that have not
            been placed in the PCB yet.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file.
            netlist_path: Path to a ``.net`` file in kicadsexpr format.

        Returns:
            Dict with ``success``, ``perfect_match`` (count), ``mismatches``
            (list of ``{ref, lib, missing_in_pcb, extra_in_pcb}``),
            ``pcb_only`` (list of ``{ref, lib, pad_count}``),
            ``schematic_only`` (list of ``{ref, pin_count}``).
        """
        pcb_path = to_local_path(pcb_path)
        netlist_path = to_local_path(netlist_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if not os.path.isfile(netlist_path):
            return {"success": False, "error": f"Netlist not found: {netlist_path}"}
        try:
            pcb_text = get_text(pcb_path)
            with open(netlist_path, encoding="utf-8") as fh:
                net_text = fh.read()
            sch = _parse_netlist_pins_per_ref(net_text)
            pcb = _parse_pcb_pads_per_ref(pcb_text)

            sch_only = sorted(set(sch) - set(pcb))
            pcb_only_refs = sorted(set(pcb) - set(sch))
            both = sorted(set(sch) & set(pcb))

            mismatches = []
            perfect = 0
            for ref in both:
                spins = sch[ref]
                lib, ppins = pcb[ref]
                missing = sorted(spins - ppins)
                extra = sorted(ppins - spins)
                if not missing and not extra:
                    perfect += 1
                else:
                    mismatches.append(
                        {
                            "ref": ref, "lib": lib,
                            "missing_in_pcb": missing,
                            "extra_in_pcb": extra,
                        }
                    )
            return {
                "success": True,
                "perfect_match": perfect,
                "mismatches": mismatches,
                "pcb_only": [
                    {
                        "ref": r, "lib": pcb[r][0],
                        "pad_count": len(pcb[r][1]),
                    }
                    for r in pcb_only_refs
                ],
                "schematic_only": [
                    {"ref": r, "pin_count": len(sch[r])} for r in sch_only
                ],
                "schematic_components": len(sch),
                "pcb_footprints": len(pcb),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def rotate_pcb(pcb_path: str, angle_deg: float) -> dict[str, Any]:
        """Rotate every object on the board around ``(0,0)`` by ``angle_deg``.

        Footprints, tracks, vias, drawings and zones are rotated in place.
        Uses the pcbnew Python API (``board.GetFootprints()/GetTracks()`` …
        ``.Rotate(origin, angle)``) because no ``FootprintLoad`` is involved
        and the SWIG quirk does not affect this code path. Requires the
        pcbnew bindings (i.e. running from the KiCad-bundled Python
        interpreter or with ``kicad-python``-equivalent bindings on path).

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file.
            angle_deg: Rotation angle in degrees, positive = counter-clockwise.

        Returns:
            Dict with ``success``, ``footprints_rotated``, ``tracks_rotated``,
            ``drawings_rotated``, ``zones_rotated`` (or ``error`` on failure).
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            return _try_pcbnew_rotate(pcb_path, angle_deg)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def place_at_pivot(
        pcb_path: str,
        ref: str,
        target_x_mm: float,
        target_y_mm: float,
        pivot_kind: str = "anchor",
        pivot_arg: str = "",
        rotation_deg: float = 0.0,
        auto_rotation: str = "",
        center_x_mm: float = 0.0,
        center_y_mm: float = 0.0,
        layer: str = "",
        mod_path: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Place a footprint so a chosen reference point lands at a target
        world coordinate, with rotation propagated to every pad shape.

        This is the headless equivalent of grabbing a footprint by a
        specific handle (pad N or the footprint bounding-box centre) and
        dropping it on a chosen world position, while simultaneously
        rotating it. It is the right tool for radial layouts (a ring of
        ICs around a motor bore), clustered placement (peripheral caps and
        resistors hugging an IC), or any workflow that has been emitting
        the placement S-expression by hand.

        Pad-shape rotation is updated in lock-step with the footprint
        rotation. KiCad renders each pad via its own local ``(at lx ly
        rot)`` lokal-rot — editing only the footprint header rotates body
        and silkscreen but leaves pad rectangles in their library
        orientation, which short-circuits adjacent pads. This tool sets
        each pad's lokal-rot to the new footprint rotation so the saved
        file matches what the GUI's right-click → "Rotate" produces.

        Use this instead of editing the footprint header `(at)` by hand —
        a manual edit leaves pad rectangles in their library orientation
        and produces shorting violations after rotation. Use this when:
            * You want pad N (not the footprint origin) to land on a
              specific world coordinate.
            * You want the bounding-box centre to be the pivot — e.g.
              centring a footprint on a circle of given radius.
            * You want the footprint rotated so its long axis points
              radially in/out or tangentially relative to a board centre.

        Args:
            pcb_path: Path to the ``.kicad_pcb`` to edit.
            ref: Reference designator of the footprint to move (e.g.
                ``"U1"``).
            target_x_mm: World X coordinate (mm) where the chosen pivot
                point should end up.
            target_y_mm: World Y coordinate (mm).
            pivot_kind: One of:
                * ``"anchor"`` (default) — the footprint's own
                  ``(at)`` origin lands at ``(target_x_mm, target_y_mm)``.
                * ``"pad"`` — the named pad's *world centre* lands at the
                  target. Requires ``pivot_arg`` to name the pad.
                * ``"bbox_center"`` — the bbox centre of the footprint
                  (pads + silkscreen + courtyard) lands at the target.
                  Requires ``mod_path`` so the bbox can be read.
            pivot_arg: Pad name when ``pivot_kind="pad"`` (e.g. ``"1"``).
            rotation_deg: New footprint rotation in degrees (CCW positive).
                Ignored when ``auto_rotation`` is non-empty.
            auto_rotation: If non-empty, compute the rotation
                automatically from the target's position relative to a
                board-centre. One of: ``"radial_in"``, ``"radial_out"``,
                ``"tangential_ccw"``, ``"tangential_cw"``. Uses
                ``(center_x_mm, center_y_mm)`` as the reference centre.
            center_x_mm: Board-centre X (mm), used with ``auto_rotation``.
            center_y_mm: Board-centre Y (mm), used with ``auto_rotation``.
            layer: Optional ``"F.Cu"`` or ``"B.Cu"`` — move the footprint
                to this layer at the same time. Empty string leaves the
                current layer untouched.
            mod_path: Required only when ``pivot_kind="bbox_center"``.
                Path to the ``.kicad_mod`` so the bbox can be computed.
            dry_run: If True, compute the new pose and report it in
                the return value but do not write the file. Default
                False. The PCB stays untouched in this mode — useful
                for previewing where the footprint would land.

        Returns:
            Dict with ``success``, the resolved ``rotation`` (degrees),
            the ``anchor`` ``(x_mm, y_mm)`` actually written to the file,
            ``pads_updated`` (count of pad ``(at)`` rotations rewritten),
            and ``ref`` echoed back. On failure: ``success: False`` and
            ``error``.

        Example:
            Place ``U_DRV1`` so its bbox centre is 22.5 mm to the east of
            the PCB centre at (148.5, 105), with the long axis pointing
            radially outward:

            >>> place_at_pivot(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     ref="U_DRV1",
            ...     target_x_mm=171.0,
            ...     target_y_mm=105.0,
            ...     pivot_kind="bbox_center",
            ...     mod_path="/.../DRV8313.kicad_mod",
            ...     auto_rotation="radial_out",
            ...     center_x_mm=148.5,
            ...     center_y_mm=105.0,
            ... )

        Idempotency:
            Calling with the same arguments twice produces a byte-identical
            ``.kicad_pcb`` (no UUID churn — only the footprint header,
            optional layer tag and pad ``(at)`` rotations are rewritten).
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        # bbox_center mode requires the .kicad_mod source — load here so
        # the pure text function does not touch the filesystem.
        mod_text = ""
        if pivot_kind == "bbox_center":
            if not mod_path:
                return {
                    "success": False,
                    "error": "pivot_kind='bbox_center' requires mod_path",
                }
            mod_path = to_local_path(mod_path)
            if not os.path.isfile(mod_path):
                return {
                    "success": False,
                    "error": f"mod_path not found: {mod_path}",
                }
            with open(mod_path, encoding="utf-8") as fh:
                mod_text = fh.read()

        text = get_text(pcb_path)
        new_text, result = place_at_pivot_text(
            text, ref, target_x_mm, target_y_mm,
            pivot_kind=pivot_kind, pivot_arg=pivot_arg,
            rotation_deg=rotation_deg, auto_rotation=auto_rotation,
            center_x_mm=center_x_mm, center_y_mm=center_y_mm,
            layer=layer, mod_text=mod_text,
        )
        if not result.get("success"):
            return result
        if not dry_run:
            write_text(pcb_path, new_text)
        return {"dry_run": dry_run, **result}

    @mcp.tool()
    def clone_layout_around_pivot(
        pcb_path: str,
        source_ref: str,
        source_peripherals: list[str],
        target_pivots: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Replicate a *manually-placed* group of peripherals around one
        anchor onto N other anchors, preserving the relative layout.

        Use this instead of repeating ``place_at_pivot`` N times by hand —
        a typical pattern is "I placed driver U_DRV2 and its caps/resistors
        the way I want; now do the same around U_DRV1, U_DRV3, U_DRV4,
        U_DRV5, U_DRV6 with the same relative spacing and rotation."

        The transform is:

        1. Read the source anchor's pose ``(ax, ay, rot)``.
        2. For each named peripheral, read its world pose ``(px, py, prot)``
           and compute the local-frame offset
           ``local = R⁻¹(rot) · (px - ax, py - ay)`` plus the relative
           rotation ``local_rot = prot - rot``.
        3. For each target pivot ``(target_ref, peripheral_refs)``, read
           the target's pose ``(tx, ty, trot)``, then for each peripheral
           in ``source_peripherals`` find the corresponding entry in
           ``peripheral_refs`` and write its new pose:

           ``new_world = (tx, ty) + R(trot) · local``
           ``new_rot = local_rot + trot``

        The pad-shape rotation match (every pad's ``(at … rot)`` updated
        to the new footprint rotation) is applied automatically — the
        same lock-step fix ``place_at_pivot`` performs.

        Args:
            pcb_path: Path to a ``.kicad_pcb``.
            source_ref: Reference designator of the template anchor
                (e.g. ``"U_DRV2"``). Its pose stays unchanged.
            source_peripherals: List of refs whose poses define the
                template (e.g. ``["R_SLEEP2", "C_VCP2", "C_CP2",
                "C_VMBULK2"]``). All must exist as footprints in the
                PCB.
            target_pivots: List of dicts. Each dict has:
                * ``"anchor_ref"`` — the target anchor footprint
                  reference (e.g. ``"U_DRV1"``); its pose is read and is
                  not modified.
                * ``"peripheral_refs"`` — list of refs, same length and
                  ordering as ``source_peripherals``. Each entry is the
                  target ref that should receive the corresponding
                  source peripheral's relative pose.
            dry_run: If True, compute every placement but do not write
                the file. Default False.

        Returns:
            Dict with ``success``, ``placed`` (count of peripherals
            written), ``targets`` (count of pivots processed), and a
            ``details`` list describing each placement. On failure:
            ``success: False`` and ``error``.

        Example:
            Clone DRV2's six peripherals onto DRV1/3/4/5/6, each with
            its own per-DRV numbering convention:

            >>> clone_layout_around_pivot(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     source_ref="U_DRV2",
            ...     source_peripherals=["R_SLEEP2","R_FAULT2","C_VCP2",
            ...                         "C_CP2","C_VMBULK2","C_V3P4"],
            ...     target_pivots=[
            ...       {"anchor_ref": "U_DRV1",
            ...        "peripheral_refs": ["R_SLEEP1","R_FAULT1",
            ...                            "C_VCP1","C_CP1","C_VMBULK1",
            ...                            "C_V3P3"]},
            ...       # ... and so on for DRV3, 4, 5, 6
            ...     ],
            ... )
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        text = get_text(pcb_path)
        new_text, result = clone_layout_around_pivot_text(
            text, source_ref, source_peripherals, target_pivots,
        )
        if not result.get("success"):
            return result
        if not dry_run:
            write_text(pcb_path, new_text)
        return {"dry_run": dry_run, **result}

    @mcp.tool()
    def clone_routing(
        pcb_path: str,
        source_anchor: str,
        target_anchors: list[dict[str, Any]],
        net_filter: list[str] | None = None,
        radius_mm: float = 12.0,
        bbox_xy_mm: list[float] | None = None,
        clear_target: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Clone tracks/arcs/vias from one anchor's region onto N other
        anchors, fitting the transform from the actual pad correspondence
        so mirrored anchor groups are reproduced correctly.

        Use this when a peripheral cluster (e.g. one driver IC plus its
        decoupling caps) is already routed around a source anchor and you
        want the same routing around sibling anchors. Unlike
        ``clone_layout_around_pivot`` — which only repositions footprints
        by a pure rotation and therefore fails when the sibling anchors
        are mirror images (a dihedral arrangement: position rotated one
        way, body the other) — this tool *measures* the source-to-target
        transform from >=3 shared pad positions via an orthogonal
        Procrustes fit. The fit yields either a rotation or a reflection,
        whichever the pads demand, so the cloned copper lands exactly on
        the target pads in both cases. Footprints must already be placed
        (use ``clone_layout_around_pivot`` for that first); this tool
        only clones the copper between them.

        Args:
            pcb_path: Path to a ``.kicad_pcb``.
            source_anchor: Reference of the footprint whose surrounding
                routing is the template (e.g. ``"U_DRV1"``).
            target_anchors: List of dicts, one per destination. Each has
                ``"anchor_ref"`` (the sibling footprint reference) and an
                optional ``"net_map"`` dict substituting source net names
                with the destination's per-instance net names, e.g.
                ``{"anchor_ref": "U_DRV2", "net_map":
                {"Net-(U_DRV1-CP1)": "Net-(U_DRV2-CP1)",
                "nFAULT_DRV3": "nFAULT_DRV2"}}``. Global nets such as
                ``+20V``/``GND`` are simply omitted from ``net_map`` so
                they pass through unchanged.
            net_filter: Optional list of net names; only routing on these
                nets is cloned. Default (None/empty) clones every net in
                the region.
            radius_mm: Source/target region radius around the anchor in
                mm. An object is selected only if all its endpoints are
                within the region. Default 12.0. Ignored if ``bbox_xy_mm``
                is given.
            bbox_xy_mm: Optional explicit ``[xmin, ymin, xmax, ymax]``
                region instead of the radius (applied around each anchor
                by offset). Default None.
            clear_target: If True (default) delete existing top-level
                routing on the mapped nets within each target's region
                before adding the clones — avoids duplicate/overlapping
                copper from earlier manual attempts.
            dry_run: If True, compute everything but do not write the
                file. Default False.

        Returns:
            Dict with ``success``, ``source_objects`` (count selected),
            ``cloned_total``, ``cleared_total``, and a ``details`` list
            with per-target ``transform`` ("rotation"/"reflection"),
            ``fit_rms_mm`` (pad-fit residual — should be near 0),
            ``pads_fitted``, ``cloned`` and ``cleared`` counts. On
            failure: ``success: False`` and ``error``.

        Example:
            >>> clone_routing(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     source_anchor="U_DRV1",
            ...     target_anchors=[
            ...       {"anchor_ref": "U_DRV2",
            ...        "net_map": {"Net-(U_DRV1-CP1)": "Net-(U_DRV2-CP1)",
            ...                    "nFAULT_DRV3": "nFAULT_DRV2"}},
            ...     ],
            ...     net_filter=["+20V", "+3V3", "Net-(U_DRV1-CP1)"],
            ... )

        Idempotency: with ``clear_target=True`` a second identical call
            reproduces the same copper (old clones are cleared and
            re-added). Track/via UUIDs are freshly generated each call,
            so the file is not byte-identical, but the electrical result
            is. With ``clear_target=False`` repeated calls stack
            duplicate copper.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        text = get_text(pcb_path)
        new_text, result = clone_routing_text(
            text, source_anchor, target_anchors, net_filter,
            radius_mm, bbox_xy_mm, clear_target,
        )
        if not result.get("success"):
            return result
        if not dry_run:
            write_text(pcb_path, new_text)
        return {"dry_run": dry_run, **result}

    @mcp.tool()
    def delete_pcb_routing(
        pcb_path: str,
        net_name: str = "",
        layer: str = "",
        element_kinds: list[str] | None = None,
        bbox_xy_mm: list[float] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Delete tracks (segments), arcs and vias from a ``.kicad_pcb``
        by net, layer, element kind, and/or bounding-box filter.

        Use this when iterating on a routing strategy — re-running the
        router on a fresh canvas after a placement change, deleting an
        old keepout-violation segment before a manual re-route, or
        clearing one net so a different net can take its place. Edits
        only top-level routing elements (``(segment …)``, ``(arc …)``,
        ``(via …)`` at the PCB root). Footprints, zones, drawings and
        the net table are preserved.

        Use this instead of regex-deleting elements by hand. The S-expr
        parser is depth-balanced, so nested parentheses inside
        ``(uuid …)`` etc. don't cause silent off-by-one removals.

        Args:
            pcb_path: Path to a ``.kicad_pcb``.
            net_name: Restrict to elements on this net (exact match
                against the ``(net N "name")`` table). Empty string =
                match all nets. Pass ``"<no net>"`` (literally) to match
                pads currently with net id 0.
            layer: Restrict to a single copper layer (e.g. ``"In2.Cu"``).
                Vias are matched if **either** of their layer-pair
                endpoints equals ``layer``. Empty string = match all
                layers.
            element_kinds: Subset of ``["segment", "arc", "via"]``.
                Default (``None`` or empty list) matches all three.
            bbox_xy_mm: Optional axis-aligned bounding box ``[xmin,
                ymin, xmax, ymax]`` in mm. Elements are kept if **any**
                endpoint (or via position) falls inside the box.
                Default ``None`` = no spatial filter.
            dry_run: If ``True``, scan and report which elements would
                be deleted but do not modify the file. Useful as a
                preview before a destructive sweep.

        Returns:
            Dict with ``success``, ``deleted`` (count of removed
            elements), ``by_kind`` (e.g. ``{"segment": 12, "arc": 9,
            "via": 18}``), ``dry_run`` flag echoed, and (when
            ``dry_run=True``) ``preview`` listing the first 20 matched
            element descriptors. On failure: ``success: False`` and
            ``error``.

        Example:
            Remove all JUNCT_P0 routing before re-trying a path:

            >>> delete_pcb_routing(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     net_name="/JUNCT_P0",
            ... )

            Strip only the long-way arcs on In2.Cu inside a bbox:

            >>> delete_pcb_routing(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     layer="In2.Cu",
            ...     element_kinds=["arc"],
            ...     bbox_xy_mm=[100, 50, 200, 160],
            ... )

        Idempotency:
            A second call with identical arguments deletes zero further
            elements (the first call removed them already) and returns
            ``deleted: 0``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        text = get_text(pcb_path)
        new_text, result = delete_pcb_routing_text(
            text,
            net_name=net_name, layer=layer,
            element_kinds=element_kinds, bbox_xy_mm=bbox_xy_mm,
        )
        if not result.get("success"):
            return result
        if not dry_run and result.get("deleted", 0) > 0:
            write_text(pcb_path, new_text)
        return {"dry_run": dry_run, **result}

    @mcp.tool()
    def update_pcb_from_schematic(
        pcb_path: str,
        schematic_path: str,
        add_new: bool = True,
        update_values: bool = True,
        update_footprints: bool = True,
        sync_nets: bool = True,
        remove_orphans: bool = False,
        library_root: str = "",
        stage_position_x_mm: float = 250.0,
        stage_position_y_mm: float = 50.0,
        stage_pitch_mm: float = 2.5,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Bring a ``.kicad_pcb`` into sync with its ``.kicad_sch``
        without launching KiCad — the headless equivalent of GUI's
        Tools → "Update PCB from Schematic" (F8).

        Use this when an automated workflow has modified the schematic
        (added a component, renamed a value, swapped a footprint) and
        the PCB needs to reflect those changes for routing / DRC. Use
        this instead of chaining ``patch_pcb_nets_from_netlist`` +
        manual footprint edits — that path only covers nets and misses
        the add / update / remove half of F8.

        The tool runs ``kicad-cli sch export netlist`` under the hood
        to derive the schematic's component table and net topology,
        then applies the requested operations to the PCB. Each
        operation is independently switchable so a workflow can opt in
        to only the parts it trusts.

        Args:
            pcb_path: Path to the ``.kicad_pcb`` to update.
            schematic_path: Path to the matching ``.kicad_sch``.
            add_new: If True, footprints present in the schematic but
                missing from the PCB are loaded from the bundled KiCad
                library and inserted at a staging position outside the
                board. Default True.
            update_values: If True, footprints whose Value differs
                between schematic and PCB are rewritten with the
                schematic value. Default True.
            update_footprints: If True, footprints whose library id
                (e.g. ``Resistor_SMD:R_0402``) differs are reloaded
                from the new library entry, keeping their current
                position and reference. Default True.
            sync_nets: If True, run ``patch_pcb_nets_from_netlist``'s
                pad-net assignment as a final pass so newly added
                footprints have their nets. Default True.
            remove_orphans: If True, footprints present in the PCB but
                missing from the schematic are deleted. Default False
                — safer to disable because component reannotation can
                temporarily make an existing footprint look orphaned.
            library_root: Override path to the KiCad bundled footprint
                library root. Default "" = auto-detect via
                :func:`kicad_lib_root`.
            stage_position_x_mm, stage_position_y_mm: Top-left corner
                (mm) of the staging grid for newly-added footprints.
                Defaults to ``(250, 50)`` — well outside a typical
                board outline.
            stage_pitch_mm: Spacing between staged footprints. Default
                2.5 mm.
            dry_run: If True, compute the diff and report it but do
                not modify the PCB file. Useful as a preview.

        Returns:
            Dict with ``success``, the action lists (``added``,
            ``updated_values``, ``updated_footprints``, ``removed``,
            ``missing_libraries``), the net-sync summary
            (``nets_added``, ``pads_patched``), and ``dry_run`` echoed
            back. On failure: ``success: False`` and ``error``.

        Example:
            Preview what F8 would do to the PCB after a schematic edit:

            >>> update_pcb_from_schematic(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     schematic_path="/tmp/board.kicad_sch",
            ...     dry_run=True,
            ... )

            Then apply, but skip the orphan removal:

            >>> update_pcb_from_schematic(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     schematic_path="/tmp/board.kicad_sch",
            ... )

        Idempotency:
            A second call with no schematic edits in between returns
            empty action lists.
        """
        pcb_path = to_local_path(pcb_path)
        schematic_path = to_local_path(schematic_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if not os.path.isfile(schematic_path):
            return {
                "success": False,
                "error": f"Schematic not found: {schematic_path}",
            }
        lib_root = to_local_path(library_root) if library_root \
            else _default_kicad_lib_root()
        if not (lib_root and os.path.isdir(lib_root)):
            return {
                "success": False,
                "error": (
                    "KiCad library root not found "
                    f"(library_root={library_root!r}, auto={lib_root!r}). "
                    "Set KICAD_LIB_ROOT or pass library_root explicitly."
                ),
            }

        netlist_text = _extract_netlist_text(schematic_path)
        if netlist_text is None:
            return {
                "success": False,
                "error": "Failed to run kicad-cli sch export netlist. "
                         "Ensure KICAD_BIN points at a working install.",
            }

        pcb_text = get_text(pcb_path)

        new_text, result = update_pcb_from_schematic_text(
            pcb_text, netlist_text, lib_root,
            add_new=add_new,
            update_values=update_values,
            update_footprints=update_footprints,
            sync_nets=sync_nets,
            remove_orphans=remove_orphans,
            stage_position_x_mm=stage_position_x_mm,
            stage_position_y_mm=stage_position_y_mm,
            stage_pitch_mm=stage_pitch_mm,
        )
        if not result.get("success"):
            return result
        if not dry_run:
            write_text(pcb_path, new_text)
        return {"dry_run": dry_run, **result}

    @mcp.tool()
    def pcb_batch(
        pcb_path: str,
        operations: list[dict[str, Any]],
        dry_run: bool = False,
        halt_on_error: bool = True,
        check_clearance: bool = True,
    ) -> dict[str, Any]:
        """Apply a sequence of file-edit operations to a ``.kicad_pcb`` in
        a single open / write cycle.

        Use this when a workflow needs many small mutations (place ten
        footprints, delete one net's routing, drop in three vias) — the
        file is read once, every operation chains against the in-memory
        text, and one write happens at the end (or none in ``dry_run``
        mode). Use this instead of calling the underlying tools N times
        through MCP: each individual call costs an open + re-parse +
        write, which dominates wall-clock when the PCB file is large
        or sits on a synced drive (OneDrive, Dropbox).

        Args:
            pcb_path: Path to a ``.kicad_pcb``.
            operations: List of dicts, each
                ``{"tool": "<name>", "args": {...}}``. The ``tool``
                field must match one of the keys registered in
                ``PCB_PATCH_TEXT_FNS`` (pcb_patch_tools) or
                ``PCB_GEOMETRY_TEXT_FNS`` (pcb_geometry_tools) — i.e. a
                tool that has a pure ``_text`` companion. Tools without
                a text companion (IPC, CLI exports, anything that
                touches more than the PCB text) cannot be batched here.
            dry_run: If True, run every operation against the in-memory
                text but skip the final write. Returns the result list
                so the caller can preview the diff.
            halt_on_error: If True (default), stop on the first
                operation that returns ``success=False`` and report it.
                If False, continue chaining and collect each operation's
                result.
            check_clearance: If True (default), run the clearance engine
                ONCE over the whole board after the batch writes and fold the
                result into the ``clearance`` key — the per-tranche verify
                pattern for a multi-mutation batch. Skipped on ``dry_run`` or
                when nothing was written.

        Returns:
            Dict with ``success`` (True iff all operations succeeded),
            ``count`` (operations actually executed), ``results`` (one
            dict per operation, mirroring what the standalone tool
            would return), ``dry_run`` flag, ``unknown_tools``
            (any tool names that weren't in the registry), and
            ``clearance`` (board-wide engine effect-echo when written).

        Example:
            Move three footprints and drop one routing net in one call:

            >>> pcb_batch(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     operations=[
            ...       {"tool": "place_at_pivot",
            ...        "args": {"ref": "U_DRV1",
            ...                 "target_x_mm": 171.0,
            ...                 "target_y_mm": 105.0,
            ...                 "auto_rotation": "radial_out",
            ...                 "center_x_mm": 148.5,
            ...                 "center_y_mm": 105.0}},
            ...       {"tool": "delete_pcb_routing",
            ...        "args": {"net_name": "/JUNCT_P0"}},
            ...     ],
            ... )
        """
        # Local import to avoid a module-level cycle between the two
        # tool modules.
        from kicad_mcp.tools.pcb_geometry_tools import (  # noqa: WPS433
            PCB_GEOMETRY_TEXT_FNS,
        )

        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if not isinstance(operations, list) or not operations:
            return {
                "success": False,
                "error": "operations must be a non-empty list",
            }
        all_fns: dict[str, Callable[..., tuple[str, dict[str, Any]]]] = {
            **PCB_PATCH_TEXT_FNS,
            **PCB_GEOMETRY_TEXT_FNS,
        }
        unknown: list[str] = []
        for i, op in enumerate(operations):
            if not isinstance(op, dict) or "tool" not in op:
                return {
                    "success": False,
                    "error": (
                        f"operations[{i}] must be a dict with a "
                        "'tool' key"
                    ),
                }
            if op["tool"] not in all_fns:
                unknown.append(op["tool"])
        if unknown:
            return {
                "success": False,
                "error": (
                    "Unknown tool(s) in operations — must be one of "
                    f"{sorted(all_fns)}; got: {sorted(set(unknown))}"
                ),
                "unknown_tools": sorted(set(unknown)),
            }

        text = get_text(pcb_path)

        results: list[dict[str, Any]] = []
        all_ok = True
        for i, op in enumerate(operations):
            fn = all_fns[op["tool"]]
            args = op.get("args", {}) or {}
            try:
                new_text, result = fn(text, **args)
            except TypeError as exc:
                result = {
                    "success": False,
                    "error": (
                        f"operations[{i}] tool={op['tool']!r}: "
                        f"argument mismatch — {exc}"
                    ),
                }
                new_text = text
            except Exception as exc:  # pylint: disable=broad-except
                result = {
                    "success": False,
                    "error": (
                        f"operations[{i}] tool={op['tool']!r} raised: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                }
                new_text = text
            results.append({"tool": op["tool"], **result})
            if result.get("success"):
                text = new_text
            else:
                all_ok = False
                if halt_on_error:
                    break

        wrote = all_ok and not dry_run
        if wrote:
            write_text(pcb_path, text)

        out = {
            "success": all_ok,
            "count": len(results),
            "results": results,
            "dry_run": dry_run,
        }
        return attach_clearance(out, pcb_path, None,
                                enabled=check_clearance and wrote)

    @mcp.tool()
    def patch_track_nets_from_pads(
        pcb_path: str,
        tolerance_mm: float = 0.5,
        include_arcs: bool = True,
        include_vias: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Assign net tags to tracks/arcs/vias whose net is empty by
        snapping to the nearest pad within ``tolerance_mm``.

        Use this after a sequence of ``add_track_to_pcb`` /
        ``add_arc_to_pcb`` / direct S-expression appends that left some
        tracks with ``(net 0)`` or ``(net "")`` (e.g. because the source
        pads carry the custom KiCad-10 ``(net "name")`` short-form
        instead of the indexed ``(net N "name")``). The DRC engine
        otherwise reports those tracks as "shorting nets <untagged> and
        XYZ" for every neighbour.

        Pad world-positions are computed with the same flip-aware
        transformation as ``compute_pad_world_positions``, so B.Cu pads
        snap correctly. The tool reads every pad with a positive net
        index, then for each segment/arc/via with an empty net checks
        whether one of its endpoints is within ``tolerance_mm`` of a pad
        position — if yes, the pad's net is written into the track block.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file.
            tolerance_mm: Snap radius. 0.5 mm covers typical track-end
                placement errors; raise to 1.0 mm for hand-routed tracks.
            include_arcs: If True (default), arcs are processed too.
            include_vias: If True (default), vias are processed too.
            dry_run: If True, scan and count but do not write the file.

        Returns:
            ``{success, patched, skipped_already_tagged, skipped_no_pad_match,
            tolerance_mm, audit, dry_run}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = patch_track_nets_from_pads_text(
                pcb_text,
                tolerance_mm=tolerance_mm,
                include_arcs=include_arcs,
                include_vias=include_vias,
                dry_run=dry_run,
            )
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def align_to_pcb_edge_rotation(
        target_x_mm: float,
        target_y_mm: float,
        center_x_mm: float,
        center_y_mm: float,
        mode: str = "radial_out",
    ) -> dict[str, Any]:
        """Compute the rotation angle (deg, CCW) that aligns a footprint's
        long axis to a radial or tangential direction at ``target`` relative
        to ``center``.

        Use this when you need the rotation value for further processing
        (e.g. clustering decoupling caps around an IC, pre-computing the
        angle before a `clone_layout_around_pivot` call) without actually
        moving anything. Prefer ``place_at_pivot(auto_rotation=...)``
        instead when you want to set the footprint pose in one step —
        this tool is the pure-helper version of that same math.

        Args:
            target_x_mm, target_y_mm: World position the rotation is
                computed at.
            center_x_mm, center_y_mm: World position the rotation is
                measured against — usually the PCB centre or a parent IC
                anchor.
            mode: One of:
                * ``"radial_out"`` — long axis points away from centre.
                * ``"radial_in"`` — long axis points toward centre.
                * ``"tangential_ccw"`` — long axis CCW-tangential.
                * ``"tangential_cw"`` — long axis CW-tangential.

        Returns:
            ``{success, rotation_deg, target, center, mode}``.
        """
        try:
            rot = align_radial_rotation(
                (float(target_x_mm), float(target_y_mm)),
                (float(center_x_mm), float(center_y_mm)),
                mode=mode,
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "rotation_deg": round(rot, 4),
            "target": [float(target_x_mm), float(target_y_mm)],
            "center": [float(center_x_mm), float(center_y_mm)],
            "mode": mode,
        }

    @mcp.tool()
    def flip_footprint_to_layer(
        pcb_path: str,
        ref: str,
        target_layer: str = "B.Cu",
        mirror: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Flip a footprint to the opposite copper side with proper layer
        swapping and X-axis sub-item mirror.

        KiCad's ``F`` hotkey ("Flip to back") does three things in
        lock-step (verified against ``FOOTPRINT::Flip`` →
        ``FLIP_DIRECTION::LEFT_RIGHT`` in the upstream source):

        1. Swap every layer string ``F.*`` ↔ ``B.*`` in the footprint
           block (``F.Cu/Mask/Paste/SilkS/Fab/CrtYd`` etc.) — applies to
           the footprint header AND every pad / fp_line / fp_arc /
           fp_text inside.
        2. **X-mirror** all sub-item ``(at x y rot)``, ``(start)``,
           ``(end)``, ``(mid)`` coords — negate the local **X** (not Y)
           and negate the rotation. See ``CLAUDE.md`` §B.Cu-Flip and
           Footgun #2 for why X — KiCad's reader applies the same X
           mirror at parse time on B.Cu footprints, so the file-level
           write must do the inverse (write `-x` so the read multiplies
           back to the original world position).
        3. Leave the footprint anchor position unchanged.

        Editing only the footprint-header ``(layer …)`` is **not enough**:
        the pads stay on F.Cu and the renderer still draws the footprint
        on top. This tool does the full transform that the GUI does.

        Args:
            pcb_path: ``.kicad_pcb`` file to edit.
            ref: Footprint reference (e.g. ``"U1"``).
            target_layer: ``"B.Cu"`` (default) or ``"F.Cu"``. If the
                footprint is already on ``target_layer`` the tool is a
                no-op.
            mirror: If True (default), apply X-mirror to all sub-items.
                Set False if the footprint was already mirrored externally
                (rare).
            dry_run: If True, compute the diff but do not write the file.

        Returns:
            ``{success, ref, from_layer, to_layer, pads_flipped,
            subitems_mirrored, dry_run}``.
        """
        if target_layer not in ("F.Cu", "B.Cu"):
            return {
                "success": False,
                "error": "target_layer must be F.Cu or B.Cu",
            }
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            fp_span = _find_footprint_block(pcb_text, ref)
            if fp_span is None:
                return {
                    "success": False,
                    "error": f"Footprint not found: {ref}",
                }
            fp_start, fp_end = fp_span
            block = pcb_text[fp_start:fp_end]
            from_layer = _read_fp_layer(block) or "F.Cu"
            if from_layer == target_layer:
                return {
                    "success": True,
                    "ref": ref,
                    "from_layer": from_layer,
                    "to_layer": target_layer,
                    "pads_flipped": 0,
                    "subitems_mirrored": 0,
                    "note": "no-op (already on target layer)",
                    "dry_run": dry_run,
                }

            # 1. Layer-pair swap. Use sentinels to avoid cycle. Cover
            # every paired KiCad-10 F.*/B.* layer — same set as
            # ``_render_footprint_block`` (Footgun K15). ``F.Silkscreen``
            # is KiCad-8+'s new name for ``F.SilkS``; modern Lib
            # footprints emit either, so we swap both.
            swaps = [
                ("F.Cu", "B.Cu"), ("F.Mask", "B.Mask"),
                ("F.Paste", "B.Paste"), ("F.SilkS", "B.SilkS"),
                ("F.Silkscreen", "B.Silkscreen"),
                ("F.Fab", "B.Fab"), ("F.CrtYd", "B.CrtYd"),
                ("F.Adhes", "B.Adhes"),
            ]
            new_block = block
            for a, b in swaps:
                sentinel = f"__FLIP_{a}__"
                new_block = new_block.replace(f'"{a}"', f'"{sentinel}"')
                new_block = new_block.replace(f'"{b}"', f'"{a}"')
                new_block = new_block.replace(f'"{sentinel}"', f'"{b}"')

            pads_flipped = 0
            subitems_mirrored = 0

            if mirror:
                # 2. X-mirror sub-items. Skip the footprint header
                # `(at …)` — it's the anchor and must stay at the same
                # world position. Use the depth-walking helper so both
                # header orders work: `kicad-cli` emits `(uuid …) (at …)`,
                # `generate_project` emits `(at …) (uuid …)`.
                exclude_end = _find_footprint_header_at_end(new_block)

                # Mirror (at x y rot?) — negate X, negate rot if present.
                def mirror_at(m: re.Match) -> str:
                    x = float(m.group(1))
                    y = m.group(2)
                    rot = m.group(3)
                    new_x = -x
                    if rot is not None:
                        new_rot = -float(rot) % 360
                        return f'(at {new_x:.6f} {y} {new_rot:.3f})'
                    return f'(at {new_x:.6f} {y})'

                post_header = new_block[exclude_end:]
                at_pat = re.compile(
                    r'\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+([\d.\-]+))?\)'
                )
                # Apply only to sub-items (pads and fp_*)
                new_post = at_pat.sub(mirror_at, post_header)

                # Mirror start/end/mid in fp_line/arc/text/poly etc.
                def mirror_se(m: re.Match) -> str:
                    tag = m.group(1)
                    x = float(m.group(2))
                    y = m.group(3)
                    return f'({tag} {-x:.6f} {y})'

                se_pat = re.compile(
                    r'\((start|end|mid)\s+([\d.\-]+)\s+([\d.\-]+)\)'
                )
                # Count for reporting
                pads_flipped = len(at_pat.findall(post_header))
                subitems_mirrored = len(se_pat.findall(post_header))

                new_post = se_pat.sub(mirror_se, new_post)
                new_block = new_block[:exclude_end] + new_post

            new_text = pcb_text[:fp_start] + new_block + pcb_text[fp_end:]
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            return {
                "success": True,
                "ref": ref,
                "from_layer": from_layer,
                "to_layer": target_layer,
                "pads_flipped": pads_flipped,
                "subitems_mirrored": subitems_mirrored,
                "dry_run": dry_run,
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def insert_keepout_zone(
        pcb_path: str,
        center_x_mm: float,
        center_y_mm: float,
        shape: str = "circle",
        radius_mm: float = 0.0,
        width_mm: float = 0.0,
        height_mm: float = 0.0,
        polygon_pts: list[list[float]] | None = None,
        layers: list[str] | None = None,
        name: str = "keepout",
        block_tracks: bool = True,
        block_vias: bool = True,
        block_pads: bool = False,
        block_footprints: bool = True,
        block_copperpour: bool = False,
        circle_segments: int = 48,
    ) -> dict[str, Any]:
        """Insert a keepout zone (circle, rectangle, or polygon) into a
        PCB. Tracks/vias/pads/footprints/copperpour can each be
        independently allowed or blocked.

        Use this for motor-shadow keepouts (round bore around a brushless
        spindle), mount-hole exclusion margins, antenna keep-aways, or
        any "no copper" region. KiCad zones are polygon-based; circles
        are approximated with ``circle_segments`` vertices (default 48 =
        7.5° per segment, smooth).

        Args:
            pcb_path: ``.kicad_pcb`` to edit.
            center_x_mm, center_y_mm: Centre of the keepout (mm).
            shape: ``"circle"`` (default), ``"rectangle"``, or
                ``"polygon"``.
            radius_mm: Required if ``shape="circle"``.
            width_mm, height_mm: Required if ``shape="rectangle"``.
            polygon_pts: Required if ``shape="polygon"`` — list of
                ``[x_mm, y_mm]`` pairs relative to ``center_*``. Closed
                implicitly.
            layers: Copper layers the keepout applies to (default
                ``["F.Cu","B.Cu","In1.Cu","In2.Cu"]``).
            name: Optional zone label.
            block_tracks, block_vias, block_pads, block_footprints,
            block_copperpour: Keepout flags — each blocks (``True``) or
                allows (``False``) that element type in the zone.
            circle_segments: Number of polygon vertices used to
                approximate a circle. 48 is smooth; 24 is acceptable for
                large keepouts.

        Returns:
            ``{success, pcb_path, polygon_vertices, layers, name}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if shape == "circle":
            if radius_mm <= 0:
                return {
                    "success": False,
                    "error": "shape='circle' requires radius_mm > 0",
                }
            n = max(8, int(circle_segments))
            pts = []
            for i in range(n):
                theta = 2.0 * math.pi * i / n
                pts.append((
                    center_x_mm + radius_mm * math.cos(theta),
                    center_y_mm + radius_mm * math.sin(theta),
                ))
        elif shape == "rectangle":
            if width_mm <= 0 or height_mm <= 0:
                return {
                    "success": False,
                    "error": (
                        "shape='rectangle' requires width_mm > 0 and "
                        "height_mm > 0"
                    ),
                }
            hw, hh = width_mm / 2.0, height_mm / 2.0
            pts = [
                (center_x_mm - hw, center_y_mm - hh),
                (center_x_mm + hw, center_y_mm - hh),
                (center_x_mm + hw, center_y_mm + hh),
                (center_x_mm - hw, center_y_mm + hh),
            ]
        elif shape == "polygon":
            if not polygon_pts or len(polygon_pts) < 3:
                return {
                    "success": False,
                    "error": (
                        "shape='polygon' requires polygon_pts with >= 3 "
                        "[x_mm, y_mm] pairs"
                    ),
                }
            pts = [
                (center_x_mm + float(p[0]), center_y_mm + float(p[1]))
                for p in polygon_pts
            ]
        else:
            return {
                "success": False,
                "error": (
                    f"shape must be circle / rectangle / polygon, got "
                    f"{shape!r}"
                ),
            }

        if layers is None:
            layers = ["F.Cu", "B.Cu", "In1.Cu", "In2.Cu"]
        if not layers:
            return {
                "success": False,
                "error": "at least one layer required",
            }

        try:
            pcb_text = get_text(pcb_path)

            uuid_str = str(uuid.uuid4())
            layer_list = " ".join(f'"{lyr}"' for lyr in layers)

            def kpo(flag: bool) -> str:
                return "not_allowed" if flag else "allowed"

            # Build polygon pts text in lines of 4
            pts_lines: list[str] = []
            line: list[str] = []
            for px, py in pts:
                line.append(f"(xy {px:.6f} {py:.6f})")
                if len(line) >= 4:
                    pts_lines.append(" ".join(line))
                    line = []
            if line:
                pts_lines.append(" ".join(line))
            pts_text = "\n\t\t\t\t".join(pts_lines)

            zone_block = (
                "\t(zone\n"
                f"\t\t(layers {layer_list})\n"
                f'\t\t(uuid "{uuid_str}")\n'
                f'\t\t(name "{name}")\n'
                "\t\t(hatch edge 0.5)\n"
                "\t\t(connect_pads\n\t\t\t(clearance 0)\n\t\t)\n"
                "\t\t(min_thickness 0.25)\n"
                "\t\t(keepout\n"
                f"\t\t\t(tracks {kpo(block_tracks)})\n"
                f"\t\t\t(vias {kpo(block_vias)})\n"
                f"\t\t\t(pads {kpo(block_pads)})\n"
                f"\t\t\t(copperpour {kpo(block_copperpour)})\n"
                f"\t\t\t(footprints {kpo(block_footprints)})\n"
                "\t\t)\n"
                "\t\t(placement\n\t\t\t(enabled no)\n\t\t\t(sheetname \"\")\n\t\t)\n"
                "\t\t(fill\n"
                "\t\t\t(thermal_gap 0.5)\n"
                "\t\t\t(thermal_bridge_width 0.5)\n"
                "\t\t\t(island_removal_mode 0)\n"
                "\t\t)\n"
                "\t\t(polygon\n"
                "\t\t\t(pts\n"
                f"\t\t\t\t{pts_text}\n"
                "\t\t\t)\n"
                "\t\t)\n"
                "\t)\n"
            )

            # Insert before final closing paren
            last_close = pcb_text.rfind("\n)")
            if last_close < 0:
                last_close = pcb_text.rfind(")")
            new_text = pcb_text[:last_close] + zone_block + pcb_text[last_close:]

            write_text(pcb_path, new_text)

            return {
                "success": True,
                "pcb_path": pcb_path,
                "polygon_vertices": len(pts),
                "layers": layers,
                "name": name,
                "uuid": uuid_str,
                "shape": shape,
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def insert_footprint(
        pcb_path: str,
        fp_library_name: str,
        ref: str,
        value: str,
        x_mm: float,
        y_mm: float,
        rotation_deg: float = 0.0,
        layer: str = "F.Cu",
        library_root: str = "",
    ) -> dict[str, Any]:
        """Insert a fresh footprint instance into a PCB from a
        ``.kicad_mod`` template.

        This is the headless equivalent of dragging a footprint from the
        library browser onto the board. Use this when a Schematic→PCB
        sync (``update_pcb_from_schematic`` / F8) misses a footprint, or
        for scripted placement of mount-holes, test-points, fiducials
        etc. without round-tripping through Eeschema.

        Use ``update_pcb_from_schematic`` instead of this when the
        footprint comes from a schematic symbol — it carries the
        net / value / footprint trio from the netlist. This tool takes
        an **explicit** library reference + position, so it is the
        right one when there is no matching schematic symbol (mount
        holes, fiducials, manually-added test points).

        Args:
            pcb_path: ``.kicad_pcb`` file to edit.
            fp_library_name: Library:Footprint reference, e.g.
                ``"Capacitor_SMD:C_0402_1005Metric"`` or
                ``"TestPoint:TestPoint_Pad_D1.5mm"``.
            ref: Reference designator the new footprint should get
                (e.g. ``"C42"``, ``"TP1"``). Must not collide with an
                existing PCB reference.
            value: Value text (e.g. ``"100nF"``, ``"GND"``).
            x_mm, y_mm: World position for the footprint anchor.
            rotation_deg: Footprint rotation (CCW, deg). Pad-local rot is
                automatically updated to match.
            layer: ``"F.Cu"`` (default) or ``"B.Cu"``. If ``"B.Cu"`` the
                F.* layers are mirrored to B.*.
            library_root: Optional override for the footprint library
                root. Defaults to the bundled KiCad library
                (``KICAD_LIB_ROOT`` env var or auto-detected).

        Returns:
            ``{success, ref, pcb_path, source_kicad_mod, uuid}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if ":" not in fp_library_name:
            return {
                "success": False,
                "error": (
                    f"fp_library_name must be 'Library:Footprint', "
                    f"got {fp_library_name!r}"
                ),
            }
        lib_name, fp_name = fp_library_name.split(":", 1)
        roots: list[str] = []
        if library_root:
            roots.append(to_local_path(library_root))
        else:
            kr = kicad_lib_root()
            if kr:
                roots.append(kr)
            # Also try fp-lib-table next to the PCB (project-local)
            proj_fp_table = os.path.join(os.path.dirname(pcb_path), "fp-lib-table")
            if os.path.isfile(proj_fp_table):
                try:
                    with open(proj_fp_table, encoding="utf-8") as fh:
                        ft = fh.read()
                    # parse (lib (name "X")(type KiCad)(uri "PATH")...)
                    for m in re.finditer(
                        r'\(lib[^)]*\(name\s+"([^"]+)"\)[^)]*\(type\s+"?KiCad"?\)[^)]*'
                        r'\(uri\s+"([^"]+)"\)',
                        ft, re.DOTALL,
                    ):
                        if m.group(1) == lib_name:
                            uri = m.group(2)
                            # Expand ${KIPRJMOD} → project dir
                            uri = uri.replace(
                                "${KIPRJMOD}", os.path.dirname(pcb_path)
                            )
                            # Library root = parent of *.pretty dir
                            if uri.endswith(".pretty"):
                                parent = os.path.dirname(uri)
                            else:
                                parent = uri
                            if parent and parent not in roots:
                                roots.insert(0, parent)
                            break
                except Exception:  # pylint: disable=broad-except
                    pass

        template = None
        source = None
        for root in roots:
            tpl = _load_kicad_mod_text(root, lib_name, fp_name)
            if tpl is not None:
                template = tpl
                source = os.path.join(root, f"{lib_name}.pretty", f"{fp_name}.kicad_mod")
                break
        if template is None:
            return {
                "success": False,
                "error": (
                    f"Footprint {fp_library_name!r} not found in any of "
                    f"the library roots: {roots}"
                ),
            }
        if layer not in ("F.Cu", "B.Cu"):
            return {"success": False, "error": "layer must be F.Cu or B.Cu"}
        try:
            pcb_text = get_text(pcb_path)
            # Check ref-collision
            if re.search(
                rf'\(property\s+"Reference"\s+"{re.escape(ref)}"', pcb_text,
            ):
                return {
                    "success": False,
                    "error": (
                        f"Reference {ref!r} already exists in the PCB."
                    ),
                }
            mirror = layer == "B.Cu"
            instance = _patch_loaded_footprint(
                template, ref, value, x_mm, y_mm, rotation_deg, mirror,
            )
            # Set fresh UUIDs (template UUIDs would collide on re-insert)
            fresh_uuid = str(uuid.uuid4())
            # Replace ONLY the footprint-header uuid (first occurrence)
            instance = re.sub(
                r'\(uuid\s+"[^"]+"\)',
                f'(uuid "{fresh_uuid}")', instance, count=1,
            )
            # Pad uuids stay as in template (KiCad accepts duplicates
            # across different footprint instances, only header UUID is
            # required to be unique). For safety, regenerate them too.
            def _regen_uuid(_m: re.Match) -> str:
                return f'(uuid "{uuid.uuid4()}")'
            instance = re.sub(r'\(uuid\s+"[^"]+"\)', _regen_uuid, instance)

            # Insert before final )
            last_close = pcb_text.rfind("\n)")
            if last_close < 0:
                last_close = pcb_text.rfind(")")
            new_text = (
                pcb_text[:last_close]
                + "\t" + instance.lstrip() + "\n"
                + pcb_text[last_close:]
            )
            write_text(pcb_path, new_text)
            return {
                "success": True,
                "ref": ref,
                "value": value,
                "fp_library_name": fp_library_name,
                "source_kicad_mod": source,
                "uuid": fresh_uuid,
                "pcb_path": pcb_path,
                "layer": layer,
                "position": [x_mm, y_mm, rotation_deg],
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def rename_net(
        pcb_path: str,
        old_name: str,
        new_name: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Rename a single net everywhere it appears in the PCB.

        Updates the net-table entry plus every pad / segment / arc / via
        / zone binding that uses ``old_name``. Handles all three KiCad-10
        net-binding forms:

        * ``(net N "old")`` — full net-table + tagged pads/tracks.
        * ``(net "old")`` — short-form without index (custom KiCad-10
          variant some tools emit).
        * ``(net_name "old")`` — zone net binding.

        Use this when you want to logically rename a net (e.g.
        ``DRIVE_P0_A`` → ``OUT_P0_A``) without touching the schematic.
        Note that the change lives only in the PCB; running
        ``patch_pcb_nets_from_netlist`` afterwards would resurrect the
        old name from the schematic.

        Args:
            pcb_path: ``.kicad_pcb`` to edit.
            old_name: Current net name (exact match).
            new_name: New net name. Must be different from ``old_name``.
            dry_run: If True, count occurrences but do not write.

        Returns:
            ``{success, old_name, new_name, replacements, pcb_path,
            dry_run}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = rename_net_text(pcb_text, old_name, new_name)
            if not report.get("success"):
                return report
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            report["dry_run"] = dry_run
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def bulk_rename_nets(
        pcb_path: str,
        mapping: dict[str, str],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply a ``{old: new}`` net-name mapping in one pass.

        Sentinel-based swap support: a mapping like ``{"A": "B", "B":
        "A"}`` correctly **swaps** the two names instead of collapsing
        both to one. Useful for renaming a whole cluster of nets in one
        call — e.g. swap ``DRIVE_P0_*`` ↔ ``DRIVE_P6_*`` after a DRV
        reassignment, or batch-rename ``HALL_1`` … ``HALL_8`` to
        ``HX_0`` … ``HX_7``.

        Same net-binding forms supported as :func:`rename_net`
        (``(net N "name")``, ``(net "name")``, ``(net_name "name")``).

        Args:
            pcb_path: ``.kicad_pcb`` to edit.
            mapping: Dict of ``old_name → new_name``. Keys must be unique
                non-empty strings; mapping a name to itself is rejected
                to catch accidental self-loops.
            dry_run: If True, count occurrences but do not write.

        Returns:
            ``{success, mapping, replacements_per_net, total_replacements,
            pcb_path, dry_run}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = bulk_rename_nets_text(pcb_text, mapping)
            if not report.get("success"):
                return report
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            report["dry_run"] = dry_run
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def bulk_rename_refs(
        pcb_path: str,
        mapping: dict[str, str],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Rename footprint references in one pass; sentinel-based pair-
        swap so ``{"U1": "U3", "U3": "U1"}`` swaps the two refs without
        collisions.

        Operates only on the PCB ``(property "Reference" "OLD" …)``
        entries. Use cases:
            * Swap DRV cluster labels (U_DRV1 ↔ U_DRV3) without moving
              the components — re-syncs which schematic component each
              physical IC represents.
            * Renumber after a refactor (R1→R10, R2→R11 …).
            * Apply a project-wide naming-convention change.

        After a rename, the pad nets are NOT automatically re-synced.
        Run :func:`patch_pcb_nets_from_netlist` afterwards if the
        schematic uses the new refs.

        Args:
            pcb_path: ``.kicad_pcb`` file to edit.
            mapping: ``{old_ref: new_ref}`` dict. Keys/values must be
                unique non-empty strings; mapping a ref to itself is
                rejected.
            dry_run: If True, count occurrences but do not write.

        Returns:
            ``{success, mapping, replacements_per_ref, total_replacements,
            pcb_path, dry_run}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = bulk_rename_refs_text(pcb_text, mapping)
            if not report.get("success"):
                return report
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            report["dry_run"] = dry_run
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def place_with_constraints(
        pcb_path: str,
        constraints: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Declarative multi-component placement.

        Walks a list of placement constraints in order and applies each
        via the existing primitives (``place_at_pivot``,
        ``clone_layout_around_pivot``). Use this when you have a batch
        of related placement decisions to express declaratively
        (decoupling caps near an IC, six DRV-IC clusters arranged
        radially) and want one transactional operation. Prefer calling
        ``place_at_pivot`` / ``clone_layout_around_pivot`` directly
        instead of this tool when you only have a single component to
        move — the constraint list adds parsing overhead with no
        benefit at N=1.

        Each constraint is one of:

        **near** — place ``ref`` near a reference pad of another ref:
            ``{"type": "near", "ref": "C1", "near_ref": "U1",
              "near_pin": "1", "offset_mm": [1.5, 0.0],
              "rotation_deg": 0, "layer": "F.Cu"}``

        **align_radial** — place ``ref`` aligned radially to a centre:
            ``{"type": "align_radial", "ref": "U_DRV3",
              "target_x_mm": 137.24, "target_y_mm": 85.49,
              "center_x_mm": 148.5, "center_y_mm": 105.065,
              "mode": "radial_out", "layer": "F.Cu"}``

        **cluster_around** — place a list of refs in a tangential
        grid around a parent ref:
            ``{"type": "cluster_around", "parent_ref": "U_DRV1",
              "companions": ["C_VCP1", "C_CP1", "R_FAULT1"],
              "ring_radius_mm": 6.0, "step_deg": 30,
              "rotation_inherit": true}``

        **mirror_layout** — clone the layout of a parent+companions
        group around a new pivot (wraps
        ``clone_layout_around_pivot``). ``include_refs`` are the source
        peripherals whose relative poses form the template; pass distinct
        ``target_refs`` (same length) to relocate a *sibling* group,
        otherwise the same refs are moved. ``rotation_offset_deg`` must be
        0 (the clone reproduces poses verbatim — no rotation hook):
            ``{"type": "mirror_layout", "source_pivot_ref": "U_DRV1",
              "target_pivot_ref": "U_DRV3", "include_refs": [...],
              "target_refs": [...], "rotation_offset_deg": 0}``

        Args:
            pcb_path: ``.kicad_pcb`` file.
            constraints: List of constraint dicts. Each is applied in
                order; later constraints can reference positions set
                by earlier ones.
            dry_run: If True, simulate and report what would happen.

        Returns:
            ``{success, applied: [...], failed: [...], pcb_path,
            dry_run}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}

        applied: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        try:
            pcb_text = get_text(pcb_path)

            def _find_pad_world(text: str, ref: str, pin: str) -> tuple[float, float] | None:
                fp_span = _find_footprint_block(text, ref)
                if fp_span is None:
                    return None
                fp_start, fp_end = fp_span
                block = text[fp_start:fp_end]
                fx, fy, frot = _read_fp_pose(block)
                layer = _read_fp_layer(block) or "F.Cu"
                flipped = layer.startswith("B.")
                pad_at = _find_pad_at(block, pin)
                if pad_at is None:
                    return None
                lx, ly, _ = pad_at
                return pcb_local_to_world(
                    (fx, fy), frot, lx, ly, flipped=flipped,
                )

            for i, c in enumerate(constraints):
                ctype = c.get("type", "")
                try:
                    if ctype == "near":
                        ref = c["ref"]
                        near_ref = c["near_ref"]
                        near_pin = c.get("near_pin", "1")
                        offset = c.get("offset_mm", [0.0, 0.0])
                        rot = float(c.get("rotation_deg", 0.0))
                        layer = c.get("layer", "")
                        target = _find_pad_world(pcb_text, near_ref, near_pin)
                        if target is None:
                            failed.append({
                                "index": i, "constraint": c,
                                "error": (
                                    f"near_ref pad not found: "
                                    f"{near_ref}.{near_pin}"
                                ),
                            })
                            continue
                        tx = target[0] + float(offset[0])
                        ty = target[1] + float(offset[1])
                        new_text, rep = place_at_pivot_text(
                            pcb_text, ref,
                            target_x_mm=tx, target_y_mm=ty,
                            pivot_kind="anchor",
                            rotation_deg=rot,
                            layer=layer,
                        )
                        if rep.get("success"):
                            pcb_text = new_text
                            applied.append({"index": i, "constraint": c, **rep})
                        else:
                            failed.append({
                                "index": i, "constraint": c,
                                "error": rep.get("error"),
                            })

                    elif ctype == "align_radial":
                        ref = c["ref"]
                        tx = float(c["target_x_mm"])
                        ty = float(c["target_y_mm"])
                        cx = float(c["center_x_mm"])
                        cy = float(c["center_y_mm"])
                        mode = c.get("mode", "radial_out")
                        layer = c.get("layer", "")
                        new_text, rep = place_at_pivot_text(
                            pcb_text, ref,
                            target_x_mm=tx, target_y_mm=ty,
                            pivot_kind="anchor",
                            auto_rotation=mode,
                            center_x_mm=cx, center_y_mm=cy,
                            layer=layer,
                        )
                        if rep.get("success"):
                            pcb_text = new_text
                            applied.append({"index": i, "constraint": c, **rep})
                        else:
                            failed.append({
                                "index": i, "constraint": c,
                                "error": rep.get("error"),
                            })

                    elif ctype == "cluster_around":
                        parent = c["parent_ref"]
                        companions = list(c.get("companions", []))
                        radius = float(c.get("ring_radius_mm", 6.0))
                        step_deg = float(c.get("step_deg", 30.0))
                        start_deg = float(c.get("start_deg", 0.0))
                        layer = c.get("layer", "")
                        # Find parent center
                        fp_span = _find_footprint_block(pcb_text, parent)
                        if fp_span is None:
                            failed.append({
                                "index": i, "constraint": c,
                                "error": f"parent_ref not found: {parent}",
                            })
                            continue
                        fp_start, fp_end = fp_span
                        block = pcb_text[fp_start:fp_end]
                        fx, fy, _ = _read_fp_pose(block)
                        # Place each companion at parent + offset.
                        # KiCad is Y-down (screen-Y grows downward), so
                        # the polar Y-component negates sin to match the
                        # user's intuitive "phi=90° → north of parent"
                        # — same convention as ``cluster_phi_deg`` above.
                        success_count = 0
                        for k, comp in enumerate(companions):
                            phi = math.radians(start_deg + k * step_deg)
                            tx = fx + radius * math.cos(phi)
                            ty = fy - radius * math.sin(phi)
                            new_text, rep = place_at_pivot_text(
                                pcb_text, comp,
                                target_x_mm=tx, target_y_mm=ty,
                                pivot_kind="anchor",
                                auto_rotation="radial_out",
                                center_x_mm=fx, center_y_mm=fy,
                                layer=layer,
                            )
                            if rep.get("success"):
                                pcb_text = new_text
                                success_count += 1
                        applied.append({
                            "index": i, "constraint": c,
                            "companions_placed": success_count,
                            "companions_total": len(companions),
                        })

                    elif ctype == "mirror_layout":
                        src = c["source_pivot_ref"]
                        dst = c["target_pivot_ref"]
                        include = c.get("include_refs", [])
                        # The target peripherals to relocate. Defaults to the
                        # same refs as the source template (relocate this group
                        # to mirror around the target pivot); pass distinct
                        # ``target_refs`` (same length) to move a sibling group.
                        target_refs = c.get("target_refs", include)
                        rot_off = float(c.get("rotation_offset_deg", 0.0))
                        if rot_off:
                            # clone_layout_around_pivot_text reproduces the
                            # source group's relative poses verbatim around the
                            # target anchor; it has no rotation-offset hook, so
                            # honour the request honestly rather than silently
                            # dropping it.
                            failed.append({
                                "index": i, "constraint": c,
                                "error": ("rotation_offset_deg is not supported "
                                          "by mirror_layout (use 0)"),
                            })
                            continue
                        new_text, rep = clone_layout_around_pivot_text(
                            pcb_text,
                            source_ref=src,
                            source_peripherals=include,
                            target_pivots=[{
                                "anchor_ref": dst,
                                "peripheral_refs": target_refs,
                            }],
                        )
                        if rep.get("success"):
                            pcb_text = new_text
                            applied.append({"index": i, "constraint": c, **rep})
                        else:
                            failed.append({
                                "index": i, "constraint": c,
                                "error": rep.get("error"),
                            })

                    else:
                        failed.append({
                            "index": i, "constraint": c,
                            "error": (
                                f"unknown constraint type {ctype!r}. "
                                "Expected: near / align_radial / "
                                "cluster_around / mirror_layout"
                            ),
                        })
                except Exception as exc:
                    failed.append({
                        "index": i, "constraint": c,
                        "error": f"{type(exc).__name__}: {exc}",
                    })

            if not dry_run and applied:
                write_text(pcb_path, pcb_text)
            return {
                "success": len(failed) == 0,
                "applied": applied,
                "failed": failed,
                "applied_count": len(applied),
                "failed_count": len(failed),
                "pcb_path": pcb_path,
                "dry_run": dry_run,
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def bulk_set_property(
        pcb_path: str,
        ref_pattern: str,
        property_name: str,
        value: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Set a property on every footprint whose reference matches a
        glob-style pattern.

        Supported targets:
            * Boolean flags (``dnp``, ``in_bom``, ``on_board``,
              ``in_pos_files``, ``exclude_from_pos_files``,
              ``exclude_from_bom``) — pass ``value="yes"``/``"no"`` /
              ``"true"`` / ``"false"``.
            * Text properties (``Value``, ``Datasheet``, or any
              custom property name) — overwrites the existing string.

        Pattern is ``fnmatch``-style:
            * ``"C_DNP*"`` — all refs starting with C_DNP
            * ``"R_FAULT?"`` — single-char wildcard
            * ``"U1"`` — exact match (no wildcards)

        Args:
            pcb_path: ``.kicad_pcb`` to edit.
            ref_pattern: fnmatch pattern.
            property_name: ``dnp`` / ``in_bom`` / ``Value`` / etc.
            value: New value. For boolean flags, accepts
                ``"yes"/"no"/"true"/"false"`` strings.
            dry_run: If True, count but do not write.

        Returns:
            ``{success, ref_pattern, property_name, value, refs_touched,
            count, dry_run}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = bulk_set_property_text(
                pcb_text, ref_pattern, property_name, value,
            )
            if not report.get("success"):
                return report
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            report["dry_run"] = dry_run
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def add_segment(
        pcb_path: str,
        start_x_mm: float,
        start_y_mm: float,
        end_x_mm: float,
        end_y_mm: float,
        layer: str,
        net_name: str,
        width_mm: float = 0.25,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Insert a single raw routing segment between two world coordinates.

        Use this when you need a straight ``(segment …)`` between arbitrary
        XY points on a copper layer — for example to finish a track stub
        from a via to a free location, or to draw a guideline before a
        pad-to-pad track exists. For pad-to-pad routing prefer
        ``add_track_to_pcb`` (which looks up pad world coordinates with
        the flip-aware math); ``add_segment`` is the lower-level escape
        hatch when the endpoint is not a pad.

        The new segment is appended to the top-level routing list of the
        PCB (NOT inside any footprint). A fresh UUID is generated. The
        target net is created at the top of the PCB if it does not yet
        exist.

        Args:
            pcb_path: ``.kicad_pcb`` to edit (WSL or Windows path).
            start_x_mm, start_y_mm: World coordinates of the segment start.
            end_x_mm, end_y_mm: World coordinates of the segment end.
            layer: Copper layer name, e.g. ``"F.Cu"``, ``"B.Cu"``,
                ``"In1.Cu"``. Must end in ``.Cu``.
            net_name: Net to bind the segment to. Created at the top
                of the PCB if not yet defined. Pass empty string for
                no-connect (net 0).
            width_mm: Track width in mm. Default 0.25.
            dry_run: If True, compute the result but skip the disk write.

        Returns:
            Dict with ``success``, ``segment_added`` ``{start, end, layer,
            net, net_id, width}``, ``pcb_path``, ``dry_run``. On error:
            ``{success: False, error: ...}``.

        Idempotency:
            Each call generates a fresh UUID and appends a new segment
            block — calling the tool twice with identical args inserts
            TWO segments.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = add_segment_text(
                pcb_text,
                start_x_mm=start_x_mm, start_y_mm=start_y_mm,
                end_x_mm=end_x_mm, end_y_mm=end_y_mm,
                layer=layer, net_name=net_name, width_mm=width_mm,
            )
            if not report.get("success"):
                return report
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            report["dry_run"] = dry_run
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def delete_footprint(
        pcb_path: str,
        ref: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove a footprint block from the PCB by its Reference designator.

        Locates the ``(footprint …)`` block whose
        ``(property "Reference" "<ref>" …)`` matches and removes the
        whole block including the trailing newline. Other footprints,
        routing and zones are left untouched. Use this when you need to
        prune a placed footprint headless (e.g. swapping a TP for a
        proper pad later, or cleaning up a stale mounting hole) instead
        of editing the ``.kicad_pcb`` file with a text editor — the
        balanced-paren walker won't truncate a footprint mid-block the
        way regex would.

        Args:
            pcb_path: ``.kicad_pcb`` to edit (WSL or Windows path).
            ref: Reference designator of the footprint to delete
                (e.g. ``"TP_GND2"`` or ``"R1"``).
            dry_run: If True, compute the result but skip the disk write.

        Returns:
            Dict with ``success``, ``deleted`` ``{ref, lib_id, position}``,
            ``pcb_path``, ``dry_run``. If the ref is not found:
            ``{success: False, error: ...}``.

        Idempotency:
            Calling twice with the same ref: first call deletes, second
            call returns ``success=False`` (ref no longer found). Not a
            silent no-op — the caller is meant to react.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = delete_footprint_text(pcb_text, ref=ref)
            if not report.get("success"):
                return report
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            report["dry_run"] = dry_run
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def add_footprint_text(
        pcb_path: str,
        ref: str,
        text: str,
        local_x_mm: float,
        local_y_mm: float,
        layer: str,
        font_size_mm: float = 0.8,
        font_thickness_mm: float = 0.12,
        rotation_deg: float = 0,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Add an ``(fp_text user …)`` annotation inside a footprint.

        Use this when you need a per-instance label on a placed
        footprint (orientation marker, pin-1 dot label, pad-function
        hint) instead of editing the library's ``.kicad_mod`` file —
        the change lives in the PCB and stays with this specific
        instance. The position is given in the footprint's LOCAL
        coordinate frame, NOT world coordinates — KiCad applies the
        footprint's own rotation/flip when rendering. If the layer
        starts with ``"B."`` the tool automatically adds
        ``(effects (justify mirror))`` so the text reads correctly
        from the back side. For drawing free-floating board text not
        attached to a footprint, use a top-level ``(gr_text …)`` block
        instead (not yet exposed as an MCP tool).

        Args:
            pcb_path: ``.kicad_pcb`` to edit (WSL or Windows path).
            ref: Reference designator of the host footprint (e.g.
                ``"U1"``).
            text: The text to render. May contain ``${REFERENCE}`` and
                other KiCad text-variables.
            local_x_mm, local_y_mm: Position in the footprint's local
                frame, in mm.
            layer: Target layer (silkscreen / fab / courtyard / user
                etc.), e.g. ``"F.SilkS"``, ``"B.Fab"``, ``"F.Cu"``.
            font_size_mm: Font size in mm (uniform x/y). Default 0.8.
            font_thickness_mm: Stroke thickness in mm. Default 0.12.
            rotation_deg: Local rotation of the text in degrees,
                default 0.
            dry_run: If True, compute the result but skip the disk write.

        Returns:
            Dict with ``success``, ``text_added`` ``{ref, text, layer,
            local_xy, rotation}``, ``pcb_path``, ``dry_run``. On error:
            ``{success: False, error: ...}``.

        Idempotency:
            Each call generates a fresh UUID and appends a new fp_text
            block — calling the tool twice inserts TWO text items.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = add_footprint_text_text(
                pcb_text,
                ref=ref, text=text,
                local_x_mm=local_x_mm, local_y_mm=local_y_mm,
                layer=layer,
                font_size_mm=font_size_mm,
                font_thickness_mm=font_thickness_mm,
                rotation_deg=rotation_deg,
            )
            if not report.get("success"):
                return report
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            report["dry_run"] = dry_run
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def set_footprint_3d_model(
        pcb_path: str,
        ref: str,
        model_path: str,
        offset_xyz: list[float] | None = None,
        scale_xyz: list[float] | None = None,
        rotate_xyz: list[float] | None = None,
        replace_existing: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Set or replace the ``(model …)`` 3D override inside a footprint.

        Use this to point a single placed footprint at a different
        ``.step`` / ``.wrl`` model than the library default (e.g. a
        per-instance 3D variant, a mechanical CAD model with offset, a
        downloaded vendor STEP). Edits the PCB text directly because
        KiCad's IPC ``SaveDocument`` does not persist per-instance
        ``(model)`` overrides reliably — they are stripped when the
        in-memory footprint object doesn't carry them. The recommended
        workflow after calling this tool is therefore:

            set_footprint_3d_model(...)
            ipc_revert(doc_type="pcb")   # NOT ipc_save

        ``ipc_save`` will re-emit the in-memory footprint and strip the
        override; ``ipc_revert`` reloads disk content into the GUI which
        preserves the override.

        Args:
            pcb_path: ``.kicad_pcb`` to edit (WSL or Windows path).
            ref: Reference designator of the host footprint (e.g.
                ``"U1"``).
            model_path: Path to the 3D model file. KiCad text variables
                like ``${KIPRJMOD}`` and ``${KICAD10_3DMODEL_DIR}`` are
                accepted.
            offset_xyz: ``[x, y, z]`` translation in mm, default
                ``[0, 0, 0]``.
            scale_xyz: ``[x, y, z]`` scale factor (unit-less), default
                ``[1, 1, 1]``.
            rotate_xyz: ``[x, y, z]`` Euler rotation in degrees, default
                ``[0, 0, 0]``.
            replace_existing: If True (default) and the footprint
                already has a ``(model)`` block, replace it. If False,
                refuse to overwrite an existing model.
            dry_run: If True, compute the result but skip the disk write.

        Returns:
            Dict with ``success``, ``model_set`` ``{ref, model_path,
            offset, scale, rotate, replaced_existing}``, ``pcb_path``,
            ``dry_run`` and a ``note`` about the ``ipc_revert`` workflow.
            On error: ``{success: False, error: ...}``.

        Idempotency:
            With ``replace_existing=True`` (default) calling twice with
            identical args yields a byte-identical PCB after the second
            call (no UUIDs in this block).
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = set_footprint_3d_model_text(
                pcb_text,
                ref=ref, model_path=model_path,
                offset_xyz=offset_xyz, scale_xyz=scale_xyz,
                rotate_xyz=rotate_xyz,
                replace_existing=replace_existing,
            )
            if not report.get("success"):
                return report
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            report["dry_run"] = dry_run
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def set_footprint_property_visibility(
        pcb_path: str,
        ref: str,
        property_name: str,
        hide: bool,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Toggle the ``(hide yes/no)`` flag on a footprint property.

        KiCad 10 stores property visibility as an optional
        ``(hide yes)`` token inside the property block (its absence
        means visible). Use this tool to hide the Reference / Value
        / Datasheet / Description / any custom property text on a
        specific placed footprint — for example to declutter a dense
        silkscreen by hiding Value text on every 0402 part. Editing
        text directly is fragile because the property block can have
        multi-line ``(effects …)`` sub-blocks; this tool uses
        balanced-paren scanning to locate the right property without
        touching neighbours.

        Args:
            pcb_path: ``.kicad_pcb`` to edit (WSL or Windows path).
            ref: Reference designator of the host footprint (e.g.
                ``"U1"``).
            property_name: Exact property name (case-sensitive). KiCad
                built-ins: ``"Reference"``, ``"Value"``, ``"Footprint"``,
                ``"Datasheet"``, ``"Description"``. Custom properties
                use whatever name the schematic defines.
            hide: ``True`` to set ``(hide yes)``, ``False`` to remove
                the flag (visibility implied by absence).
            dry_run: If True, compute the result but skip the disk write.

        Returns:
            Dict with ``success``, ``property`` ``{ref, name,
            hide_before, hide_after}``, ``pcb_path``, ``dry_run``. On
            error: ``{success: False, error: ...}``.

        Idempotency:
            Calling twice with the same args yields a byte-identical
            PCB after the second call (no UUIDs in this block).
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            pcb_text = get_text(pcb_path)
            new_text, report = set_footprint_property_visibility_text(
                pcb_text,
                ref=ref, property_name=property_name, hide=hide,
            )
            if not report.get("success"):
                return report
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            report["pcb_path"] = pcb_path
            report["dry_run"] = dry_run
            return report
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def cluster_block_outside_pcb(
        pcb_path: str,
        sch_path: str,
        block_name: str,
        cluster_phi_deg: float,
        cluster_r_mm: float = 42.0,
        pcb_center_x_mm: float = 0.0,
        pcb_center_y_mm: float = 0.0,
        grid_cols: int = 4,
        spacing_t_mm: float = 5.0,
        spacing_r_mm: float = 5.0,
        align_mode: str = "radial_in",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Cluster every footprint of a schematic ``kicad-mcp.group`` in a
        tangential grid outside the PCB outline.

        Reads the schematic's ``kicad-mcp.group`` tag, finds all refs of
        ``block_name``, and places their PCB footprints in an N-column
        grid centred at polar position ``(cluster_phi_deg,
        cluster_r_mm)`` relative to the PCB centre. Each footprint is
        rotated according to ``align_mode``.

        Use this for initial-placement workflows on round PCBs: instead
        of every block landing piled-on at the schematic origin after
        ``update_pcb_from_schematic`` / F8, this tool pre-arranges each
        block in its own outside-the-board sector so the user can drag
        them inward one block at a time.

        Use this when:
            * Your schematic has ``kicad-mcp.group`` tags (added e.g.
              via ``add_schematic_symbols(group_id=…)``).
            * You want the cluster to land at a specific polar position
              outside the board (typically ``cluster_r_mm`` > board
              radius) so it does not overlap existing placements.

        Args:
            pcb_path: ``.kicad_pcb`` to edit (WSL or Windows path).
            sch_path: ``.kicad_sch`` to read the group tag from.
            block_name: Value of the ``kicad-mcp.group`` property to
                cluster (e.g. ``"block_usb_pd"``).
            cluster_phi_deg: Math-angle in degrees (CCW from +X axis)
                for the cluster centre.
            cluster_r_mm: Distance from the PCB centre (default 42.0).
            pcb_center_x_mm: PCB centre X coordinate (mm, default 0.0).
            pcb_center_y_mm: PCB centre Y coordinate (mm, default 0.0).
            grid_cols: Number of columns in the grid (default 4).
            spacing_t_mm: Tangential spacing between columns in mm
                (default 5.0).
            spacing_r_mm: Radial spacing between rows in mm (default 5.0).
            align_mode: ``"radial_in"`` (default), ``"radial_out"``,
                ``"tangential_cw"``, ``"tangential_ccw"``. Controls the
                rotation applied to each placed footprint.
            dry_run: If True, compute placement but skip the disk write.

        Returns:
            Dict with ``success``, ``block_name``, ``refs_resolved``
            (the list of refs found in the group), ``placed_count``,
            ``placed`` (per-ref ``{ref, row, col, x_mm, y_mm,
            rotation}``), ``error_count``, ``errors`` (refs that failed
            placement, e.g. missing from the PCB), ``cluster_center``,
            ``dry_run``. On schematic-side failure (file missing, group
            empty): ``{success: False, error: ...}``.

        Example:
            Cluster the USB-PD block at phi=80 deg (= upper-right), 42
            mm from PCB centre (148.5, 105), 4-column grid:

            >>> cluster_block_outside_pcb(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     sch_path="/tmp/board.kicad_sch",
            ...     block_name="block_usb_pd",
            ...     cluster_phi_deg=80.0,
            ...     cluster_r_mm=42.0,
            ...     pcb_center_x_mm=148.5,
            ...     pcb_center_y_mm=105.0,
            ...     grid_cols=4,
            ...     align_mode="radial_in",
            ... )

        Idempotency:
            Calling twice with identical args produces a byte-identical
            PCB (only the placed footprints' anchor + rotation + pad
            ``(at)`` rotations are rewritten — unchanged for unchanged
            inputs).
        """
        pcb_path = to_local_path(pcb_path)
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"SCH not found: {sch_path}"}

        try:
            sch_text = get_text(sch_path)
        except Exception as exc:  # pylint: disable=broad-except
            return {
                "success": False,
                "error": f"Failed to read SCH: {exc}",
            }

        refs: list[str] = []
        for start, end in _iter_top_blocks(sch_text, "symbol"):
            head = sch_text[start:start + 200]
            # Skip ``(symbol "Lib:Name" …)`` blocks inside ``(lib_symbols …)``
            # — those are library definitions, not placed instances.
            if "(lib_id" not in head:
                continue
            block = sch_text[start:end]
            m_group = re.search(
                r'\(property\s+"kicad-mcp\.group"\s+"([^"]+)"', block,
            )
            if not m_group or m_group.group(1) != block_name:
                continue
            m_ref = re.search(
                r'\(property\s+"Reference"\s+"([^"]+)"', block,
            )
            if m_ref:
                refs.append(m_ref.group(1))

        if not refs:
            return {
                "success": False,
                "error": (
                    f"No refs found in group '{block_name}' of {sch_path}"
                ),
            }

        try:
            pcb_text = get_text(pcb_path)
            new_text, result = cluster_block_outside_pcb_text(
                pcb_text, refs,
                cluster_phi_deg=cluster_phi_deg,
                cluster_r_mm=cluster_r_mm,
                pcb_center_x_mm=pcb_center_x_mm,
                pcb_center_y_mm=pcb_center_y_mm,
                grid_cols=grid_cols,
                spacing_t_mm=spacing_t_mm,
                spacing_r_mm=spacing_r_mm,
                align_mode=align_mode,
            )
            if "error" in result and result.get("placed_count", 0) == 0 \
                    and not result.get("placed"):
                return result
            if not dry_run and new_text != pcb_text:
                write_text(pcb_path, new_text)
            result["block_name"] = block_name
            result["refs_resolved"] = refs
            result["dry_run"] = dry_run
            result["pcb_path"] = pcb_path
            return result
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}
