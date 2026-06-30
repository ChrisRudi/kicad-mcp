# SPDX-License-Identifier: GPL-3.0-or-later
"""
Schematic-patch tools — incremental headless editing of ``.kicad_sch``.

Tools registered:
  Read / Probe
    * ``compute_pin_world_positions_sch``
    * ``list_schematic_groups``
    * ``get_schematic_bbox``

  Additive edits
    * ``add_schematic_symbols``
    * ``add_schematic_wire``
    * ``add_schematic_label``
    * ``add_power_symbols``
    * ``convert_global_labels_to_power``
    * ``connect_pins``
    * ``validate_schematic_patch``

  Group transforms
    * ``move_schematic_group``
    * ``rotate_schematic_group``
    * ``delete_schematic_items``

All tools are pure text-surgery on the underlying S-expression file. They do
not require the KiCad GUI / IPC API. Determinism: all newly inserted UUIDs
are derived via UUID-5 from the project UUID and a stable seed, so re-runs
are idempotent and produce clean diffs.
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
from __future__ import annotations

import json
import os
import re
from typing import Annotated, Any, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field


from kicad_mcp.generators.schematic_patcher import (
    LABEL_OUTWARD_TABLE,
    SchematicDoc,
    default_power_rotation,
    get_lib_symbol_pins,
    get_symbol_attrs,
    iter_global_label_blocks,
    iter_top_level_blocks,
    justify_for_angle,
    pin_outward_angle,
    power_lib_id_for,
    render_label,
    render_no_connect,
    render_symbol_instance,
    render_wire,
    stable_uuid,
)
from kicad_mcp.generators.symbol_author import (
    SymbolSpecError,
    render_library_symbol,
)
from kicad_mcp.generators.symbol_cache import _extract_top_level_symbol
from kicad_mcp.generators.symbol_lib import resolve_lib_id
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.sch_geometry import (
    bbox_center,
    bbox_of_points,
    needs_half_grid_offset,
    pin_world_xy,
    residual_after_snap,
    rotate_point,
    snap_for_pin_grid,
    snap_to_90,
    snap_to_grid,
)
from kicad_mcp.utils.sexpr_parser import find_node, parse_sexpr


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_AT_RE = re.compile(
    r"\(at\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)(?:\s+([+-]?\d+(?:\.\d+)?))?\s*\)"
)
_WIRE_PTS_RE = re.compile(
    r"\(pts\s*\(xy\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\s*\)"
    r"\s*\(xy\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\s*\)\s*\)"
)
_PROP_REF_RE = re.compile(r'(\(property\s+"Reference"\s+)"([^"]*)"')
_INSTANCE_REF_RE = re.compile(r'(\(reference\s+)"([^"]*)"')
_REF_SPLIT_RE = re.compile(r"^(.+?)(\d+)$")
_PWR_NUMBERED_RE = re.compile(r"^#(PWR|FLG)(\d{4})$")
_PROP_NAMED_VALUE_RE = re.compile(
    r'(\(property\s+"{name}"\s+)"((?:[^"\\]|\\.)*)"'
)
_FLAG_RE_TEMPLATE = r"\(({flag})\s+(yes|no)\)"


def _fmt(v: float) -> str:
    # Schematic IU is 100 nm — the file format truncates to 4 decimal mm
    # on every save. Emitting more is pointless drift the next KiCad save
    # would silently normalise away (and diff tools flag).
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _replace_first_at(block: str, x: float, y: float, rot: int) -> tuple[str, bool]:
    """Replace only the **first** ``(at x y rot)`` occurrence — the symbol's
    own placement, not nested property-anchors. Returns ``(new_block, ok)``.

    Coordinates are snapped to the 1.27 mm placement grid to keep
    ``move_schematic_group`` / ``rotate_schematic_group`` from drifting
    symbols off-grid (would surface as endpoint_off_grid in ERC).
    """
    sx, sy = snap_to_grid(float(x), float(y))

    def _sub(m: re.Match) -> str:
        return f"(at {_fmt(sx)} {_fmt(sy)} {int(((round(rot)) % 360 + 360) % 360)})"

    new, n = _AT_RE.subn(_sub, block, count=1)
    return new, n == 1


def _replace_wire_pts(
    block: str, x1: float, y1: float, x2: float, y2: float
) -> tuple[str, bool]:
    sx1, sy1 = snap_to_grid(float(x1), float(y1))
    sx2, sy2 = snap_to_grid(float(x2), float(y2))

    def _sub(m: re.Match) -> str:
        return (
            f"(pts (xy {_fmt(sx1)} {_fmt(sy1)}) (xy {_fmt(sx2)} {_fmt(sy2)}))"
        )

    new, n = _WIRE_PTS_RE.subn(_sub, block, count=1)
    return new, n == 1


def _block_node(doc: SchematicDoc, start: int, end: int) -> Optional[list]:
    try:
        return parse_sexpr(doc.text[start:end])
    except Exception:
        return None


def _all_top_blocks(doc: SchematicDoc) -> dict[str, list[tuple[int, int]]]:
    return {
        "symbol": doc.iter_symbol_offsets(),
        "wire": iter_top_level_blocks(doc.text, "wire"),
        "label": iter_top_level_blocks(doc.text, "label"),
        "global_label": iter_top_level_blocks(doc.text, "global_label"),
        "hierarchical_label": iter_top_level_blocks(
            doc.text, "hierarchical_label"
        ),
        "junction": iter_top_level_blocks(doc.text, "junction"),
        "no_connect": iter_top_level_blocks(doc.text, "no_connect"),
    }


def _label_at(node: list) -> Optional[tuple[float, float, int]]:
    n = find_node(node, "at")
    if not n or len(n) < 3:
        return None
    try:
        x = float(n[1])
        y = float(n[2])
        rot = int(float(n[3])) if len(n) > 3 else 0
        return (x, y, rot)
    except (TypeError, ValueError):
        return None


def _wire_pts(node: list) -> Optional[tuple[float, float, float, float]]:
    pts = find_node(node, "pts")
    if not pts:
        return None
    xys: list[tuple[float, float]] = []
    for ch in pts[1:]:
        if isinstance(ch, list) and ch and ch[0] == "xy" and len(ch) >= 3:
            try:
                xys.append((float(ch[1]), float(ch[2])))
            except (TypeError, ValueError):
                continue
    if len(xys) < 2:
        return None
    return (xys[0][0], xys[0][1], xys[-1][0], xys[-1][1])


def _sorted_mutations(items: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Sort ``(start, end, replacement)`` tuples in reverse-order so we can
    apply them sequentially without offset drift.
    """
    return sorted(items, key=lambda t: t[0], reverse=True)


def _apply_replacements(
    doc: SchematicDoc, items: list[tuple[int, int, str]]
) -> None:
    for start, end, new in _sorted_mutations(items):
        doc.replace_block(start, end, new)


def _build_power_symbol_snippet(
    doc: SchematicDoc,
    *,
    lib_id: str,
    value: str,
    ref: str,
    x_mm: float,
    y_mm: float,
    rotation_deg: int,
    group_id: Optional[str],
    proj_uuid: str,
    snap: bool = True,
) -> str:
    """Render a single ``power:``-symbol-instance snippet for ``doc``.

    Shared between ``add_power_symbols`` (caller-supplied anchors) and
    ``convert_global_labels_to_power`` (anchors derived from existing
    global labels). Caller is responsible for ``ensure_lib_symbol``,
    reference allocation, and ``insert_before_close`` — this helper only
    builds the S-expression text using the canonical UUID convention.

    ``snap`` (default True) rounds the anchor to the 1.27 mm grid; pass
    False to keep an exact off-grid pin endpoint (fine-pitch IC pad) so
    the connection point coincides with the pad.
    """
    uuid_val = stable_uuid(f"{proj_uuid}|{ref}|sym")
    lib_node = doc.find_lib_symbol(lib_id)
    pin_numbers: list[str] = []
    if lib_node:
        lib_pins = get_lib_symbol_pins(lib_node[2])
        pin_numbers = [
            str(pn["number"]) for pn in lib_pins if pn.get("number")
        ]
    # Defensive: keep the power-pin anchor on the 1.27 mm grid even when
    # the caller passed an inherited (and possibly drifted) global-label
    # position from convert_global_labels_to_power. Skipped when snap=False
    # so the symbol can land exactly on an off-grid pin endpoint.
    if snap:
        x_mm, y_mm = snap_to_grid(float(x_mm), float(y_mm))
    else:
        x_mm, y_mm = round(float(x_mm), 4), round(float(y_mm), 4)
    return render_symbol_instance(
        ref=ref,
        lib_id=lib_id,
        value=value,
        footprint="",
        x=x_mm,
        y=y_mm,
        rot=rotation_deg,
        mirror=None,
        uuid=uuid_val,
        group_id=group_id,
        project_uuid=proj_uuid,
        snap=snap,
        pin_numbers=pin_numbers,
        project_name=doc.project_name(),
        sheet_path_uuid=doc.root_sheet_path(),
        # Power symbols carry the auto-allocated #PWRnnnn ref purely
        # for KiCad's internal annotation — the user wants to see the
        # net glyph, not a redundant designator on top of it.
        hide_reference=True,
    )


def _alloc_pwr_ref(used_pwr_nums: set[int], existing_refs: set[str]) -> str:
    """Allocate the next free ``#PWRnnnn`` reference and reserve it.

    ``used_pwr_nums`` carries the integers already taken by ``#PWRnnnn``
    refs in the schematic; ``existing_refs`` is the full ref-set used to
    detect collisions across all symbol kinds. The chosen number is
    added to both sets.
    """
    n = 1
    while n in used_pwr_nums:
        n += 1
    used_pwr_nums.add(n)
    ref = f"#PWR{n:04d}"
    existing_refs.add(ref)
    return ref


# Group membership is tracked only on symbol instances (via the
# ``kicad-mcp.group`` hidden property). Wires / labels intentionally do
# not carry a group tag — KiCad's S-expression format has no official
# comment syntax that would survive a save round-trip in all KiCad
# versions, and the cost of corrupting a project is higher than the
# convenience of bulk-rotating wires automatically. After group
# rotate / move, re-issue ``connect_pins`` to lay down fresh wiring.


def _write_symbol_to_lib(
    lib_path: str, symbol_name: str, symbol_block: str, overwrite: bool
) -> tuple[bool, str]:
    """Insert ``symbol_block`` into the ``.kicad_sym`` at ``lib_path``.

    Creates the library file (with a ``kicad_symbol_lib`` header) when it
    does not exist. If a top-level symbol of the same name is already present
    it is replaced when ``overwrite`` is True, otherwise an error is returned.

    Returns ``(created, error)`` — ``created`` True if the file was newly
    made, ``error`` a non-empty message on failure.
    """
    block = symbol_block.rstrip() + "\n"
    if not os.path.isfile(lib_path):
        header = (
            '(kicad_symbol_lib\n'
            '  (version 20231120)\n'
            '  (generator "kicad-mcp")\n'
            '  (generator_version "10.0")\n'
        )
        with open(lib_path, "w", encoding="utf-8") as fh:
            fh.write(header + block + ")\n")
        return True, ""

    with open(lib_path, encoding="utf-8") as fh:
        content = fh.read()

    existing = _extract_top_level_symbol(content, symbol_name)
    if existing is not None:
        if not overwrite:
            return False, (
                f"Symbol {symbol_name!r} already exists in {lib_path}; "
                f"pass overwrite=true to replace it."
            )
        new_content = content.replace(existing, block.rstrip(), 1)
        with open(lib_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        return False, ""

    # Append before the final top-level ')'.
    close = content.rstrip()
    if not close.endswith(")"):
        return False, f"{lib_path} is not a valid kicad_symbol_lib (no closing paren)."
    head = close[:-1].rstrip()
    new_content = head + "\n" + block + ")\n"
    with open(lib_path, "w", encoding="utf-8") as fh:
        fh.write(new_content)
    return False, ""


def _register_lib_in_project_table(project_dir: str, lib_path: str) -> tuple[str, bool]:
    """Ensure a ``${KIPRJMOD}``-relative entry for ``lib_path`` exists in the
    project ``sym-lib-table``. Creates the table file if missing.

    Returns ``(lib_name, added)`` — ``lib_name`` is the registered nickname
    (the .kicad_sym basename), ``added`` False if it was already registered.
    """
    lib_name = os.path.splitext(os.path.basename(lib_path))[0]
    table_path = os.path.join(project_dir, "sym-lib-table")
    # Relative URI when the lib lives under the project dir, else absolute.
    try:
        rel = os.path.relpath(lib_path, project_dir).replace(os.sep, "/")
    except ValueError:
        rel = None
    uri = f"${{KIPRJMOD}}/{rel}" if rel and not rel.startswith("..") else lib_path

    entry = (
        f'  (lib (name "{lib_name}")(type "KiCad")'
        f'(uri "{uri}")(options "")(descr ""))\n'
    )
    if not os.path.isfile(table_path):
        with open(table_path, "w", encoding="utf-8") as fh:
            fh.write("(sym_lib_table\n  (version 7)\n" + entry + ")\n")
        return lib_name, True

    with open(table_path, encoding="utf-8") as fh:
        content = fh.read()
    if f'(name "{lib_name}")' in content:
        return lib_name, False
    close = content.rstrip()
    if close.endswith(")"):
        content = close[:-1].rstrip() + "\n" + entry + ")\n"
    else:
        content = close + "\n" + entry
    with open(table_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return lib_name, True


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register_sch_patch_tools(mcp: FastMCP) -> None:
    """Register schematic-patch tools."""

    # ------------------------------------------------------------------ READ

    @mcp.tool()
    def compute_pin_world_positions_sch(
        sch_path: str, refs: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """World-coordinate (mm) of every pin on every placed symbol in a ``.kicad_sch`` — flip + rotation aware.

        Use this when you need to wire something to a specific pin and
        need the exact mm coordinate (for ``add_schematic_wire`` /
        ``add_schematic_label`` placement). Don't compute positions by
        hand: the symbol's pin offsets are in the embedded ``lib_symbol``
        block and need to be rotated + mirrored according to the
        instance's ``(at x y rot)`` + ``(mirror …)`` — this tool does
        that correctly.

        For *board* (PCB) pad coordinates use ``ipc_get_pad_world_pos``
        (live editor) or ``compute_pad_world_positions`` (disk).

        Args:
            sch_path: ``.kicad_sch`` file.
            refs: optional list of reference designators (e.g. ``["U1B", "U_589"]``)
                to restrict the result to those symbols only. ``None`` / empty
                (default) returns every symbol — use ``refs`` to avoid the
                full-board pin dump when you only need a few symbols' pins.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        try:
            doc = SchematicDoc.load(sch_path)
        except Exception as exc:
            return {"success": False, "error": f"Load failed: {exc}"}

        wanted: set[str] | None = set(refs) if refs else None
        out: dict[str, list[dict[str, Any]]] = {}
        pin_count = 0
        for start, end in doc.iter_symbol_offsets():
            node = _block_node(doc, start, end)
            if not node:
                continue
            attrs = get_symbol_attrs(node)
            ref = attrs.get("ref") or "?"
            if wanted is not None and ref not in wanted:
                continue
            lib_id = attrs.get("lib_id")
            if not lib_id:
                out.setdefault(ref, [])
                continue
            lib = doc.find_lib_symbol(lib_id)
            if not lib:
                out.setdefault(ref, [])
                continue
            pins = get_lib_symbol_pins(lib[2])
            entries: list[dict[str, Any]] = []
            for p in pins:
                x, y = pin_world_xy(
                    attrs["x"],
                    attrs["y"],
                    int(attrs.get("rot", 0)),
                    attrs.get("mirror"),
                    p["x"],
                    p["y"],
                )
                entries.append(
                    {
                        "number": p["number"],
                        "name": p["name"],
                        "type": p["type"],
                        "x_mm": x,
                        "y_mm": y,
                    }
                )
            out[ref] = entries
            pin_count += len(entries)
        result: dict[str, Any] = {
            "success": True,
            "sch_path": sch_path,
            "symbol_count": len(out),
            "pin_count": pin_count,
            "symbols": out,
        }
        if wanted is not None:
            result["not_found"] = sorted(wanted - set(out))
        return result

    @mcp.tool()
    def list_schematic_groups(sch_path: str) -> dict[str, Any]:
        """Enumerate every ``kicad-mcp.group`` tag in a ``.kicad_sch`` with member refs + wire/label counts.

        Use this when you need a map of which group has what before a
        ``move_schematic_group`` / ``rotate_schematic_group`` /
        ``delete_schematic_items`` operation. Don't grep the file: KiCad's
        group property is a hidden symbol field this tool reads correctly.

        Args:
            sch_path: ``.kicad_sch`` file to scan for ``kicad-mcp.group`` tags.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        doc = SchematicDoc.load(sch_path)

        groups: dict[str, dict[str, Any]] = {}
        for start, end in doc.iter_symbol_offsets():
            node = _block_node(doc, start, end)
            if not node:
                continue
            attrs = get_symbol_attrs(node)
            g = attrs.get("group")
            if not g:
                continue
            entry = groups.setdefault(g, {"refs": [], "wires": 0, "labels": 0})
            if attrs.get("ref"):
                entry["refs"].append(attrs["ref"])
        return {"success": True, "group_count": len(groups), "groups": groups}

    @mcp.tool()
    def get_schematic_bbox(
        sch_path: str, refs: Optional[list[str]] = None, group_id: str = ""
    ) -> dict[str, Any]:
        """Compute the axis-aligned bounding box (mm) of refs / a group / the whole schematic.

        Use this before placement decisions ("how big is the audio block",
        "does my new symbol fit"). Don't reconstruct from per-symbol
        positions: this tool walks the *real* symbol bbox (including pin
        stubs) from the embedded lib_symbol — manual ``(at x y)`` math
        misses the symbol's actual extent.

        Args:
            sch_path: ``.kicad_sch`` file to measure.
            refs: optional list of reference designators to restrict the bbox
                to those symbols only. ``None`` / empty (default) = all symbols.
            group_id: optional ``kicad-mcp.group`` tag — restrict the bbox to
                that group's symbols. Empty (default) = no group filter.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        doc = SchematicDoc.load(sch_path)

        wanted: set[str] | None = set(refs) if refs else None
        all_pts: list[tuple[float, float]] = []
        included_refs: list[str] = []
        for start, end in doc.iter_symbol_offsets():
            node = _block_node(doc, start, end)
            if not node:
                continue
            attrs = get_symbol_attrs(node)
            r = attrs.get("ref")
            if wanted is not None and r not in wanted:
                continue
            if group_id and attrs.get("group") != group_id:
                continue
            all_pts.append((attrs["x"], attrs["y"]))
            if r:
                included_refs.append(r)
            # add pin endpoints for tighter bbox
            lib = doc.find_lib_symbol(attrs.get("lib_id") or "")
            if lib:
                for p in get_lib_symbol_pins(lib[2]):
                    px, py = pin_world_xy(
                        attrs["x"],
                        attrs["y"],
                        int(attrs.get("rot", 0)),
                        attrs.get("mirror"),
                        p["x"],
                        p["y"],
                    )
                    all_pts.append((px, py))
        if not all_pts:
            return {"success": False, "error": "No matching items found."}
        bb = bbox_of_points(all_pts)
        cx, cy = bbox_center(bb)
        return {
            "success": True,
            "bbox_mm": {
                "xmin": bb[0],
                "ymin": bb[1],
                "xmax": bb[2],
                "ymax": bb[3],
            },
            "center_mm": {"x": cx, "y": cy},
            "refs": sorted(set(included_refs)),
        }

    # -------------------------------------------------------------- WRITE: ADD

    @mcp.tool()
    def add_schematic_symbols(
        sch_path: str, parts: str, group_id: str = ""
    ) -> dict[str, Any]:
        """Insert one or more symbols incrementally into an existing
        ``.kicad_sch``.

        Args:
            sch_path: Path to a ``.kicad_sch``.
            parts: JSON string — list of ``{ref, name OR lib_id, value,
                footprint, x_mm, y_mm, rotation_deg?, mirror?, unit?,
                group_id?}`` entries. ``name``/``lib_id`` is resolved via the
                same three-tier strategy as ``generate_schematic``. ``unit``
                (default 1) places a specific unit of a multi-unit part (e.g.
                ``unit: 2`` for gate B of a 74xx / the second op-amp) — its
                pin set + ``(unit N)`` are emitted correctly.
            group_id: Default group-id assigned to every part lacking its
                own. Empty string = no group.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        try:
            parts_list = json.loads(parts)
        except Exception as exc:
            return {"success": False, "error": f"Invalid JSON: {exc}"}
        if not isinstance(parts_list, list):
            return {"success": False, "error": "parts must be a JSON list."}
        doc = SchematicDoc.load(sch_path)
        existing_refs = set(doc.all_refs())
        proj_uuid = doc.project_uuid()

        # World-BBoxes of all already-placed symbols, for overlap blocking.
        existing_bboxes = doc.iter_symbol_world_bboxes()

        def _bbox_for_part(lib_id: str, x: float, y: float, rot: int):
            """Approximate world BBox from lib pins, applying instance rot."""
            lib = doc.find_lib_symbol(lib_id)
            if not lib:
                return (x - 1.27, y - 1.27, x + 1.27, y + 1.27)
            pins = get_lib_symbol_pins(lib[2])
            if not pins:
                return (x - 1.27, y - 1.27, x + 1.27, y + 1.27)
            xs: list[float] = []
            ys: list[float] = []
            for pin in pins:
                wx, wy = pin_world_xy(x, y, rot, None, pin["x"], pin["y"])
                xs.append(wx); ys.append(wy)
            return (min(xs), min(ys), max(xs), max(ys))

        def _overlap(a, b, pad=0.5):
            return not (
                a[2] + pad < b[0] or b[2] + pad < a[0]
                or a[3] + pad < b[1] or b[3] + pad < a[1]
            )

        inserted: list[str] = []
        ensured: list[str] = []
        errors: list[str] = []
        snapped: list[dict[str, Any]] = []
        for p in parts_list:
            if not isinstance(p, dict):
                errors.append(f"Skipped non-dict entry: {p!r}")
                continue
            ref = str(p.get("ref") or "").strip()
            if not ref:
                errors.append("Missing ref for entry; skipped.")
                continue
            if ref in existing_refs:
                errors.append(f"Reference collision: {ref} already exists.")
                continue
            lib_id = p.get("lib_id") or resolve_lib_id(p)
            if not lib_id:
                errors.append(f"{ref}: cannot resolve lib_id.")
                continue
            if not doc.ensure_lib_symbol(lib_id):
                errors.append(
                    f"{ref}: lib_symbol {lib_id!r} not found in KiCad libraries."
                )
                continue
            if lib_id not in ensured:
                ensured.append(lib_id)

            raw_x = float(p.get("x_mm", 0.0))
            raw_y = float(p.get("y_mm", 0.0))
            # Defensive snap to the 1.27 mm placement grid so off-grid input
            # never accumulates into endpoint_off_grid ERC warnings on the
            # symbol's pin sockets. Apply this BEFORE the half-pitch correction
            # below so half-pitch logic operates on a canonical input.
            x, y = snap_to_grid(raw_x, raw_y)
            rot = int(round(float(p.get("rotation_deg", 0))))
            mirror = p.get("mirror") or None
            try:
                unit = max(1, int(p.get("unit", 1)))
            except (TypeError, ValueError):
                unit = 1

            # Bug 8 — half-pitch passives (Device:C/R/L/CP, *_Small) silently
            # land their pins off-grid when the user picks an x/y on the 2.54
            # grid. Snap the centre by 1.27 mm in the perpendicular axis so
            # both pins line up with the schematic grid; report the move so
            # downstream connect_pins can recompute pin coords.
            if needs_half_grid_offset(lib_id):
                nx, ny, moved = snap_for_pin_grid(x, y, lib_id, rot)
                if moved:
                    snapped.append({
                        "ref": ref,
                        "lib_id": lib_id,
                        "from": [raw_x, raw_y],
                        "to": [nx, ny],
                    })
                x, y = nx, ny

            # Refuse if this part's BBox overlaps any existing symbol's BBox.
            new_bbox = _bbox_for_part(lib_id, x, y, rot)
            collide_with = None
            for bref, x1, y1, x2, y2 in existing_bboxes:
                if _overlap(new_bbox, (x1, y1, x2, y2)):
                    collide_with = bref
                    break
            if collide_with:
                errors.append(
                    f"{ref}: BBox overlaps existing symbol {collide_with!r} "
                    f"at ({x}, {y}); refused to insert."
                )
                continue

            grp = p.get("group_id") or group_id or None
            uuid_val = stable_uuid(f"{proj_uuid}|{ref}|sym")

            # Per-pin UUIDs + (instances) block — required by KiCad-10
            # connection-detection. Without them every pin is reported
            # as 'pin_not_connected' even with wires landing on the
            # exact hot-spot coordinates.
            lib_node = doc.find_lib_symbol(lib_id)
            pin_numbers: list[str] = []
            if lib_node:
                # Filter to THIS unit's pins — a multi-unit part must not get
                # the other units' pin UUIDs (corrupts connectivity).
                lib_pins = get_lib_symbol_pins(lib_node[2], unit=unit)
                pin_numbers = [str(pn["number"]) for pn in lib_pins if pn.get("number")]

            snippet = render_symbol_instance(
                ref=ref,
                lib_id=lib_id,
                value=str(p.get("value") or ref),
                footprint=str(p.get("footprint") or ""),
                x=x,
                y=y,
                rot=rot,
                mirror=mirror,
                uuid=uuid_val,
                group_id=grp,
                project_uuid=proj_uuid,
                pin_numbers=pin_numbers,
                project_name=doc.project_name(),
                sheet_path_uuid=doc.root_sheet_path(),
                unit=unit,
            )
            doc.insert_before_close(snippet)
            inserted.append(ref)
            existing_refs.add(ref)
            existing_bboxes.append((ref,) + new_bbox)
        doc.save()
        return {
            "success": len(errors) == 0,
            "sch_path": sch_path,
            "inserted": inserted,
            "lib_symbols_added": ensured,
            "snapped": snapped,
            "errors": errors,
        }

    @mcp.tool()
    def add_schematic_wire(
        sch_path: str, segments: str, group_id: str = "", snap: bool = True
    ) -> dict[str, Any]:
        """Insert raw wire segments at given mm coordinates into a ``.kicad_sch``.

        Use this for explicit hand-routing where you already know the
        coordinates. For *pin-to-pin* connections (90% of cases) prefer
        ``connect_pins`` — it handles Manhattan routing + label-vs-wire
        decision. Don't write ``(wire …)`` blocks by hand: this tool
        emits the right UUID + stroke metadata.

        Args:
            sch_path: ``.kicad_sch`` file.
            segments: JSON list of ``[x1, y1, x2, y2]`` quadruples in mm.
            group_id: Optional ``kicad-mcp.group`` tag (informational only —
                wires don't carry group tags in S-expr).
            snap: Snap endpoints to the 1.27 mm grid (default True). Pass
                False to keep an exact endpoint on an off-grid (fine-pitch IC)
                pin — snapping would pull the wire off the pad and break the net
                (take the pin coord from ``compute_pin_world_positions_sch``).
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        try:
            seg_list = json.loads(segments)
        except Exception as exc:
            return {"success": False, "error": f"Invalid JSON: {exc}"}
        if not isinstance(seg_list, list):
            return {"success": False, "error": "segments must be a JSON list."}
        doc = SchematicDoc.load(sch_path)
        proj_uuid = doc.project_uuid()
        added = 0
        for s in seg_list:
            try:
                x1, y1, x2, y2 = (float(v) for v in s)
            except Exception:
                return {"success": False, "error": f"Bad segment {s!r}"}
            # Snap each endpoint to the 1.27 mm placement grid so wires never
            # land off-grid (would surface as endpoint_off_grid in ERC) — unless
            # snap=False, to land exactly on an off-grid fine-pitch pin.
            if snap:
                x1, y1 = snap_to_grid(x1, y1)
                x2, y2 = snap_to_grid(x2, y2)
            snippet = render_wire(
                x1, y1, x2, y2, group_id=group_id or None, project_uuid=proj_uuid,
                snap=snap,
            )
            doc.insert_before_close(snippet)
            added += 1
        doc.save()
        return {
            "success": True,
            "sch_path": sch_path,
            "segments_added": added,
        }

    @mcp.tool()
    def add_no_connect(sch_path: str, x_mm: float, y_mm: float) -> dict[str, Any]:
        """Place a no-connect (×) flag at a pin coordinate in a ``.kicad_sch``.

        Use this to mark a single pin as intentionally unconnected so ERC
        stops raising ``pin_not_connected`` for it — e.g. a reserved MCU pin
        or an unused IC output. The flag is only effective when it sits
        exactly on the pin's endpoint, so pull the coordinate from
        ``compute_pin_world_positions_sch`` rather than eyeballing it. To
        remove one again use ``delete_schematic_items`` with
        ``types=["no_connect"]``.

        Args:
            sch_path: ``.kicad_sch`` file.
            x_mm: pin endpoint X (mm). Snapped to the 1.27 mm placement grid.
            y_mm: pin endpoint Y (mm). Snapped to the 1.27 mm placement grid.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        sx, sy = snap_to_grid(float(x_mm), float(y_mm))
        doc = SchematicDoc.load(sch_path)
        doc.insert_before_close(
            render_no_connect(sx, sy, project_uuid=doc.project_uuid())
        )
        doc.save()
        return {"success": True, "sch_path": sch_path, "x_mm": sx, "y_mm": sy}

    @mcp.tool()
    def add_schematic_label(
        sch_path: str,
        text: str,
        x_mm: float,
        y_mm: float,
        kind: str = "local",
        rotation_deg: int = 0,
        justify: str = "",
        group_id: str = "",
    ) -> dict[str, Any]:
        """Add a single net-label to a ``.kicad_sch`` at given coordinates.

        Use this for explicit named nets at known positions — typical
        case is a power rail or a sheet-boundary net. For pin-to-pin
        connections prefer ``connect_pins`` (handles the geometry).
        Don't drop two labels with the same name at the same point: the
        tool refuses with a clash report.

        Args:
            sch_path: ``.kicad_sch`` to patch.
            text: Label text (the net name).
            x_mm: Label anchor X in mm (the label's electrical hot-spot).
            y_mm: Label anchor Y in mm (the label's electrical hot-spot).
            kind: ``"local"`` / ``"global"`` / ``"hierarchical"``.
            rotation_deg: 0 / 90 / 180 / 270.
            justify: ``"left"`` / ``"right"`` / ``""``. Empty = derive
                from rotation via ``justify_for_angle()`` so chip-pin
                labels read outward by default (left for 0/90, right
                for 180/270).
            group_id: Optional informational tag.

        Returns:
            ``{success, sch_path, kind, text, x_mm, y_mm}`` or
            ``{success: False, error}``. Idempotent — duplicate at the
            same anchor returns success with ``skipped`` note.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        if kind not in ("local", "global", "hierarchical"):
            return {"success": False, "error": f"Bad kind {kind!r}"}
        if justify not in ("", "left", "right"):
            return {"success": False, "error": f"Bad justify {justify!r}"}
        # Reject power-net labels — KiCad convention requires a
        # power: lib-symbol instance instead so ERC's
        # power_pin_not_driven check works and the rendered shape is
        # the standard ground/rail glyph. Use add_power_symbols for
        # GND / +3V3 / +5V / VBUS / VCC etc.
        pwr = power_lib_id_for(text) if kind == "global" else None
        if pwr is not None:
            return {
                "success": False,
                "error": (
                    f"{text!r} is a power net — use add_power_symbols "
                    f"with lib_id {pwr[0]!r} instead of a global label."
                ),
                "suggested_lib_id": pwr[0],
            }
        doc = SchematicDoc.load(sch_path)
        proj_uuid = doc.project_uuid()
        # Snap label anchor to the 1.27 mm placement grid.
        x_mm, y_mm = snap_to_grid(float(x_mm), float(y_mm))
        # Skip if a same-named label of this kind already sits at (x_mm, y_mm).
        if doc.has_label_at(text, x_mm, y_mm, kind=kind):
            return {
                "success": True,
                "sch_path": sch_path,
                "kind": kind,
                "text": text,
                "skipped": "duplicate at position",
            }
        # Refuse if a different label already sits at (x_mm, y_mm).
        clash = doc.any_label_at(x_mm, y_mm)
        if clash:
            other_kind, other_name = clash
            return {
                "success": False,
                "error": (
                    f"Label collision: a {other_kind} {other_name!r} "
                    f"already sits at ({x_mm}, {y_mm})."
                ),
            }
        eff_justify = justify or justify_for_angle(int(rotation_deg))
        snippet = render_label(
            text,
            x_mm,
            y_mm,
            kind=kind,
            angle=int(rotation_deg),
            justify=eff_justify,
            group_id=group_id or None,
            project_uuid=proj_uuid,
        )
        doc.insert_before_close(snippet)
        doc.save()
        return {"success": True, "sch_path": sch_path, "kind": kind, "text": text}

    @mcp.tool()
    def add_power_symbols(
        sch_path: str, anchors: str, group_id: str = "", snap: bool = True
    ) -> dict[str, Any]:
        """Drop one or more KiCad power-symbol instances at given coordinates.

        This is the canonical way to wire up GND / +3V3 / +5V / VBUS /
        VCC and other power rails — never use ``add_schematic_label``
        with the same net name. KiCad's ERC ``power_pin_not_driven``
        rule fires only when a power-input pin is reached by a
        ``power:``-symbol; a plain global label routes electrically
        but breaks the ERC contract, the visual unique-shape
        convention, and the PCB net-class assignment.

        The tool either takes ``net`` (the bare power-net name —
        ``"GND"`` / ``"+3V3"`` / …) and infers the matching
        ``power:``-lib-id, or an explicit ``lib_id`` (e.g.
        ``"power:GND"``). Each anchor entry also carries its
        ``rotation_deg`` (0 = pin points up = standard for GND,
        180 = pin points down = standard for +3V3 / +5V / VBUS).

        Grid snapping: by default each coordinate is rounded to KiCad's
        1.27 mm placement grid. A power symbol's connection point sits at
        its origin, so this is what you want when placing onto on-grid
        wires/pins. BUT for a pin on a fine-pitch IC (pads at 0.65 / 0.5 mm
        pitch are *off* the 1.27 grid), snapping moves the symbol up to
        ~0.6 mm off the pin endpoint — the two no longer coincide and ERC
        reports ``pin_not_connected``. Pass ``snap=false`` (tool-wide) or
        ``"snap": false`` on the individual anchor to land the connection
        point *exactly* on the pin endpoint (take it from
        ``compute_pin_world_positions_sch``).

        Args:
            sch_path: ``.kicad_sch`` to patch.
            anchors: JSON list of ``{net?, lib_id?, x_mm, y_mm,
                rotation_deg?, ref?, snap?}``. ``ref`` defaults to a
                deterministic ``#PWR_<seq>`` (KiCad's standard auto-
                annotated power-symbol prefix). Per-anchor ``snap``
                overrides the tool-wide default.
            group_id: Default ``kicad-mcp.group`` tag.
            snap: Tool-wide default for grid snapping (``True``). Set
                ``False`` when placing onto off-grid (fine-pitch IC) pins.

        Returns:
            ``{success, sch_path, inserted: [refs], lib_symbols_added,
            errors}``.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        try:
            anchor_list = json.loads(anchors)
        except Exception as exc:
            return {"success": False, "error": f"Invalid JSON: {exc}"}
        if not isinstance(anchor_list, list):
            return {"success": False, "error": "anchors must be a JSON list."}
        doc = SchematicDoc.load(sch_path)
        proj_uuid = doc.project_uuid()
        existing_refs = set(doc.all_refs())

        # Auto-numbering for default #PWR<seq> refs
        used_pwr_nums = set()
        for r in existing_refs:
            if r.startswith("#PWR") and r[4:].isdigit():
                used_pwr_nums.add(int(r[4:]))

        inserted: list[str] = []
        ensured: list[str] = []
        errors: list[str] = []
        for a in anchor_list:
            if not isinstance(a, dict):
                errors.append(f"Skipped non-dict entry: {a!r}")
                continue
            lib_id = a.get("lib_id")
            value = ""
            if not lib_id:
                net = str(a.get("net") or "").strip()
                resolved = power_lib_id_for(net)
                if not resolved:
                    errors.append(
                        f"Net {net!r} is not a recognised power net; "
                        f"pass lib_id explicitly (e.g. \"power:GND\")."
                    )
                    continue
                lib_id, value = resolved
            else:
                # Pull the canonical value from the library-id stem if not
                # given — power:GND -> "GND", power:+3V3 -> "+3V3".
                value = a.get("value") or lib_id.split(":", 1)[-1]

            if not doc.ensure_lib_symbol(lib_id):
                errors.append(
                    f"lib_symbol {lib_id!r} not found in KiCad libraries."
                )
                continue
            if lib_id not in ensured:
                ensured.append(lib_id)

            try:
                x = float(a.get("x_mm", 0.0))
                y = float(a.get("y_mm", 0.0))
            except (TypeError, ValueError):
                errors.append(f"Bad coordinates for {lib_id}: {a!r}")
                continue
            # Snap to the 1.27 mm placement grid by default so the power
            # symbol's connection point lands on standard wire/pin vertices.
            # Skippable per-anchor (or tool-wide) for off-grid fine-pitch IC
            # pins, where snapping would pull the connection point off the
            # pad endpoint and break the net (ERC pin_not_connected).
            do_snap = a.get("snap", snap)
            if do_snap:
                x, y = snap_to_grid(x, y)
            else:
                # Still clamp to the schematic's 0.0001 mm resolution so we
                # don't emit float noise, but keep the exact pin endpoint.
                x, y = round(x, 4), round(y, 4)
            rot = int(round(float(a.get("rotation_deg", 0))))

            ref = str(a.get("ref") or "").strip()
            if ref:
                if ref in existing_refs:
                    errors.append(f"Reference collision: {ref}")
                    continue
                existing_refs.add(ref)
            else:
                ref = _alloc_pwr_ref(used_pwr_nums, existing_refs)

            grp = a.get("group_id") or group_id or None
            snippet = _build_power_symbol_snippet(
                doc,
                lib_id=lib_id,
                value=str(a.get("value") or value),
                ref=ref,
                x_mm=x,
                y_mm=y,
                rotation_deg=rot,
                group_id=grp,
                proj_uuid=proj_uuid,
                snap=do_snap,
            )
            doc.insert_before_close(snippet)
            inserted.append(ref)
        doc.save()
        return {
            "success": len(errors) == 0,
            "sch_path": sch_path,
            "inserted": inserted,
            "lib_symbols_added": ensured,
            "errors": errors,
        }

    @mcp.tool()
    def convert_global_labels_to_power(
        sch_path: str,
        only_nets: str = "",
        dry_run: bool = False,
        group_id: str = "",
    ) -> dict[str, Any]:
        """Replace power-net global labels with the canonical ``power:``
        symbol instance at the same anchor.

        Run this once after a schematic has been wired up with plain
        ``(global_label "GND")`` / ``(global_label "+3V3")`` etc. to
        bring it in line with the KiCad convention: power rails MUST
        be driven by a ``power:`` lib-symbol so that ERC's
        ``power_pin_not_driven`` rule fires correctly, the rendered
        glyph is the unique ground/rail shape (not a signal label),
        and the PCB net-class assignment picks up the power class.

        For each top-level ``(global_label …)`` block whose text is a
        recognised power net (see
        :func:`generators.schematic_patcher.power_lib_id_for` —
        ``GND``, ``GNDA``, ``+3V3``, ``+5V``, ``+12V``, ``-12V``,
        ``VBUS``, ``VCC``, ``VDD``, ``+5V_SYS``, ``VBUS_SYS``, …) the
        tool:

        1. Ensures the matching ``power:<NET>`` lib-symbol is embedded
           in ``(lib_symbols …)``.
        2. Inserts a power-symbol instance at the label's exact ``(at
           x y)`` anchor with a fresh ``#PWR<seq>`` reference. The
           rotation defaults to the conventional orientation for the
           family (0 for GND-family, 180 for positive rails) — see
           :func:`default_power_rotation`. Wires already terminating at
           the label anchor stay connected because the new symbol's
           pin sits at the same point.
        3. Deletes the original ``global_label`` block.

        Local and hierarchical labels are never touched. Power-net
        labels that share an anchor with another label of any kind
        are skipped (the call would put two electrical objects on the
        same point) — fix those by hand and re-run.

        Args:
            sch_path: ``.kicad_sch`` to patch. WSL-style and
                Windows-style paths are both accepted; the tool
                normalises them via ``to_local_path``.
            only_nets: Optional comma-separated whitelist (e.g.
                ``"GND,+3V3"``). Empty = convert every recognised
                power net found in the schematic.
            dry_run: When true, the schematic is not written; the
                return dict reports what *would* have been replaced
                (under ``would_replace``).
            group_id: Optional ``kicad-mcp.group`` tag attached to
                each newly inserted power symbol — useful when the
                conversion is part of a larger patch session.

        Returns:
            ``{success, sch_path, replaced: [{ref, net, lib_id,
            x_mm, y_mm, rotation_deg}], skipped: [{net, x_mm, y_mm,
            reason}], lib_symbols_added: [...], dry_run, errors}``.
            ``replaced`` is empty and ``would_replace`` populated
            when ``dry_run=True``.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}

        whitelist: Optional[set[str]] = None
        if only_nets.strip():
            whitelist = {
                n.strip() for n in only_nets.split(",") if n.strip()
            }

        doc = SchematicDoc.load(sch_path)
        proj_uuid = doc.project_uuid()
        existing_refs = set(doc.all_refs())
        used_pwr_nums = set()
        for r in existing_refs:
            if r.startswith("#PWR") and r[4:].isdigit():
                used_pwr_nums.add(int(r[4:]))

        # Pre-index every label position so we can detect anchor
        # collisions before mutating the document.
        anchor_index: dict[tuple[float, float], list[tuple[str, str]]] = {}
        for tag in ("label", "global_label", "hierarchical_label"):
            for s, e in iter_top_level_blocks(doc.text, tag):
                blk = doc.text[s:e]
                m_name = re.search(rf'\({tag}\s+"([^"]+)"', blk)
                m_at = re.search(
                    r"\(at\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", blk
                )
                if not (m_name and m_at):
                    continue
                try:
                    key = (float(m_at.group(1)), float(m_at.group(2)))
                except ValueError:
                    continue
                anchor_index.setdefault(key, []).append((tag, m_name.group(1)))

        # First pass: classify every global_label so we know which
        # lib-symbols to embed and which anchors to skip. Drop power
        # labels whose anchor is shared with another label kind/name —
        # those need a manual fix before conversion is safe.
        replaced: list[dict[str, Any]] = []
        would: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        ensured: list[str] = []
        errors: list[str] = []
        plan: list[dict[str, Any]] = []
        for start, end, name, x, y, _angle in iter_global_label_blocks(doc.text):
            resolved = power_lib_id_for(name)
            if resolved is None:
                continue  # plain signal label — leave alone
            if whitelist is not None and name.strip() not in whitelist:
                skipped.append(
                    {"net": name, "x_mm": x, "y_mm": y,
                     "reason": "filtered by only_nets"}
                )
                continue
            others = [
                lab for lab in anchor_index.get((x, y), [])
                if not (lab[0] == "global_label" and lab[1] == name)
            ]
            if others:
                skipped.append(
                    {"net": name, "x_mm": x, "y_mm": y,
                     "reason": f"anchor shared with {others[0][0]} "
                               f"{others[0][1]!r}"}
                )
                continue
            lib_id, value = resolved
            rot = default_power_rotation(value)
            entry = {
                "name": name, "lib_id": lib_id, "value": value,
                "x_mm": x, "y_mm": y, "rotation_deg": rot,
                "start": start, "end": end,
            }
            plan.append(entry)

        if dry_run:
            would = [
                {"net": p["name"], "lib_id": p["lib_id"], "x_mm": p["x_mm"],
                 "y_mm": p["y_mm"], "rotation_deg": p["rotation_deg"]}
                for p in plan
            ]
            return {
                "success": True,
                "sch_path": sch_path,
                "dry_run": True,
                "replaced": [],
                "would_replace": would,
                "skipped": skipped,
                "lib_symbols_added": [],
                "errors": [],
            }

        # Embed every lib-symbol we will need *before* mutating the
        # block offsets — ``ensure_lib_symbol`` rewrites ``doc.text`` so
        # the start/end offsets recorded in ``plan`` would otherwise
        # drift.
        needed_libs = []
        for p in plan:
            if p["lib_id"] not in needed_libs:
                needed_libs.append(p["lib_id"])
        for lib_id in needed_libs:
            if not doc.ensure_lib_symbol(lib_id):
                errors.append(
                    f"lib_symbol {lib_id!r} not found in KiCad libraries."
                )
            else:
                ensured.append(lib_id)
        if errors:
            return {
                "success": False,
                "sch_path": sch_path,
                "dry_run": False,
                "replaced": [],
                "would_replace": [],
                "skipped": skipped,
                "lib_symbols_added": ensured,
                "errors": errors,
            }

        # Re-locate global-label blocks now that the document text has
        # shifted under the embedded lib-symbol definitions. Match by
        # (name, x, y) which uniquely identifies each anchor in our plan
        # — the mutation hasn't touched any global_label yet.
        fresh_blocks = {
            (name, x, y): (s, e)
            for s, e, name, x, y, _a in iter_global_label_blocks(doc.text)
        }

        # Apply deletes + inserts in reverse order of (refreshed) start
        # offset so each delete leaves earlier offsets untouched.
        plan_resolved: list[tuple[int, int, dict[str, Any]]] = []
        for p in plan:
            key = (p["name"], p["x_mm"], p["y_mm"])
            block = fresh_blocks.get(key)
            if block is None:
                errors.append(
                    f"Lost track of global_label {p['name']!r} at "
                    f"({p['x_mm']}, {p['y_mm']}) after lib-symbol embed."
                )
                continue
            plan_resolved.append((block[0], block[1], p))
        plan_resolved.sort(key=lambda t: t[0], reverse=True)
        for start, end, p in plan_resolved:
            ref = _alloc_pwr_ref(used_pwr_nums, existing_refs)
            snippet = _build_power_symbol_snippet(
                doc,
                lib_id=p["lib_id"],
                value=p["value"],
                ref=ref,
                x_mm=p["x_mm"],
                y_mm=p["y_mm"],
                rotation_deg=p["rotation_deg"],
                group_id=group_id or None,
                proj_uuid=proj_uuid,
            )
            doc.delete_block(start, end)
            doc.insert_before_close(snippet)
            replaced.append({
                "ref": ref, "net": p["name"], "lib_id": p["lib_id"],
                "x_mm": p["x_mm"], "y_mm": p["y_mm"],
                "rotation_deg": p["rotation_deg"],
            })

        if replaced or ensured:
            doc.save()
        return {
            "success": len(errors) == 0,
            "sch_path": sch_path,
            "dry_run": dry_run,
            "replaced": replaced,
            "would_replace": would,
            "skipped": skipped,
            "lib_symbols_added": ensured,
            "errors": errors,
        }

    @mcp.tool()
    def connect_pins(
        sch_path: str,
        connections: str,
        mode: str = "wire",
        group_id: str = "",
        snap: bool = True,
    ) -> dict[str, Any]:
        """Connect pins on a ``.kicad_sch`` — Manhattan wire or matched-label pair.

        Use this for 90 % of schematic wiring tasks. The tool resolves
        each pin's world coordinate via the embedded ``lib_symbol``,
        picks a 1-elbow Manhattan path (``mode="wire"``) or drops a
        global-label stub at each end (``mode="label"``) so cross-sheet
        nets work without explicit wires. Avoids existing-symbol pin
        BBoxes and existing label anchors. Don't write ``(wire …)``
        blocks by hand for pin-to-pin connections — the geometry math
        for rotation / mirror is non-trivial and this tool gets it
        right.

        Args:
            sch_path: ``.kicad_sch`` to patch.
            connections: JSON list of
                ``{from: [ref, pin], to: [ref, pin], label?: str}``.
                ``label`` overrides the auto-derived name in label mode.
            mode: ``"wire"`` for a Manhattan wire (default) or
                ``"label"`` for short stub + global label at each pin
                (use this for sheet-spanning nets).
            group_id: Optional ``kicad-mcp.group`` tag — informational
                only; wires/labels carry no group property in S-expr.
            snap: Snap wire endpoints to the 1.27 mm grid (default True).
                Pass False when either pin is on a fine-pitch IC (off the
                1.27 grid) — snapping pulls the wire off the pad and breaks
                the net.

        Returns:
            ``{success, sch_path, mode, segments_added, labels_added,
            junctions_added, results: [...]}``. ``junctions_added`` is always
            0 — a pin-to-pin connect creates no junction node; drop one
            explicitly if a third wire taps the same point.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        try:
            conns = json.loads(connections)
        except Exception as exc:
            return {"success": False, "error": f"Invalid JSON: {exc}"}
        if mode not in ("wire", "label"):
            return {"success": False, "error": f"Bad mode {mode!r}"}
        doc = SchematicDoc.load(sch_path)
        proj_uuid = doc.project_uuid()
        results: list[dict[str, Any]] = []
        added_segments = 0
        added_labels = 0
        added_junctions = 0
        for c in conns:
            try:
                fr = c["from"]
                to = c["to"]
                ref1, pin1 = str(fr[0]), str(fr[1])
                ref2, pin2 = str(to[0]), str(to[1])
            except Exception:
                return {"success": False, "error": f"Malformed connection {c!r}"}
            p1 = doc.pin_world_xy(ref1, pin1)
            p2 = doc.pin_world_xy(ref2, pin2)
            if not p1 or not p2:
                results.append(
                    {
                        "from": [ref1, pin1],
                        "to": [ref2, pin2],
                        "ok": False,
                        "error": "pin lookup failed",
                    }
                )
                continue
            if mode == "wire":
                # 1-elbow Manhattan: horizontal first, then vertical.
                if abs(p1[1] - p2[1]) < 0.001 or abs(p1[0] - p2[0]) < 0.001:
                    # Already collinear — single segment.
                    snippet = render_wire(
                        p1[0],
                        p1[1],
                        p2[0],
                        p2[1],
                        group_id=group_id or None,
                        project_uuid=proj_uuid,
                        snap=snap,
                    )
                    doc.insert_before_close(snippet)
                    added_segments += 1
                else:
                    knee = (p2[0], p1[1])
                    s1 = render_wire(
                        p1[0],
                        p1[1],
                        knee[0],
                        knee[1],
                        group_id=group_id or None,
                        project_uuid=proj_uuid,
                        snap=snap,
                    )
                    s2 = render_wire(
                        knee[0],
                        knee[1],
                        p2[0],
                        p2[1],
                        group_id=group_id or None,
                        project_uuid=proj_uuid,
                        snap=snap,
                    )
                    doc.insert_before_close(s1)
                    doc.insert_before_close(s2)
                    added_segments += 2
                results.append(
                    {"from": [ref1, pin1], "to": [ref2, pin2], "ok": True}
                )
            else:  # label mode
                label_text = str(c.get("label") or f"{ref1}_{pin1}")
                # For each endpoint, place a global label 3.81 mm outside the
                # owning chip body (with a stub wire from pin to label) and
                # rotate the label so the text reads outward, with the
                # ESP32-style justify convention applied. If the offset point
                # falls inside another symbol's BBox, push outward in steps
                # of 2.54 mm until clear. See Bug.md "label-format".
                for ref_x, _pin_x, pxy in ((ref1, pin1, p1), (ref2, pin2, p2)):
                    sym = doc.find_symbol_by_ref(ref_x)
                    sym_at = (pxy[0], pxy[1])
                    if sym:
                        a = get_symbol_attrs(sym[2])
                        sym_at = (float(a.get("x", pxy[0])), float(a.get("y", pxy[1])))
                    out_angle = pin_outward_angle(pxy, sym_at)
                    dx, dy, lbl_angle = LABEL_OUTWARD_TABLE[out_angle]
                    # Push label outward until it's clear of (a) any other
                    # symbol's BBox and (b) any existing label of any kind.
                    bboxes = doc.iter_symbol_world_bboxes()
                    pad = 0.5  # mm — keep label clear of pin extents
                    ux = (1.0 if dx > 0 else (-1.0 if dx < 0 else 0.0))
                    uy = (1.0 if dy > 0 else (-1.0 if dy < 0 else 0.0))
                    base_step = 2.54
                    base_len = max(abs(dx), abs(dy)) or base_step
                    cur_len = base_len
                    lx, ly = pxy[0] + ux * cur_len, pxy[1] + uy * cur_len
                    for _ in range(12):
                        clash = False
                        for bref, x1b, y1b, x2b, y2b in bboxes:
                            if bref == ref_x:
                                continue
                            if (x1b - pad) <= lx <= (x2b + pad) and (
                                y1b - pad
                            ) <= ly <= (y2b + pad):
                                clash = True
                                break
                        if not clash:
                            other = doc.any_label_at(lx, ly)
                            if other and other[1] != label_text:
                                clash = True
                        if not clash:
                            break
                        cur_len += base_step
                        lx, ly = pxy[0] + ux * cur_len, pxy[1] + uy * cur_len
                    # Skip if a same-named label is already at lx,ly (re-runs
                    # used to stack labels on top of each other).
                    if doc.has_label_at(label_text, lx, ly, kind="global"):
                        continue
                    # Stub wire pin -> label position (skip if zero length).
                    if abs(lx - pxy[0]) > 1e-6 or abs(ly - pxy[1]) > 1e-6:
                        stub = render_wire(
                            pxy[0], pxy[1], lx, ly,
                            group_id=group_id or None,
                            project_uuid=proj_uuid,
                        )
                        doc.insert_before_close(stub)
                        added_segments += 1
                    lbl = render_label(
                        label_text,
                        lx, ly,
                        kind="global",
                        angle=lbl_angle,
                        justify=justify_for_angle(lbl_angle),
                        group_id=group_id or None,
                        project_uuid=proj_uuid,
                    )
                    doc.insert_before_close(lbl)
                    added_labels += 1
                results.append(
                    {
                        "from": [ref1, pin1],
                        "to": [ref2, pin2],
                        "ok": True,
                        "label": label_text,
                    }
                )
        doc.save()
        return {
            "success": all(r["ok"] for r in results),
            "sch_path": sch_path,
            "mode": mode,
            "segments_added": added_segments,
            "labels_added": added_labels,
            "junctions_added": added_junctions,
            "results": results,
        }

    @mcp.tool()
    def validate_schematic_patch(
        sch_path: str, parts: str = "[]"
    ) -> dict[str, Any]:
        """Dry-run for ``add_schematic_symbols``: report ref collisions, missing lib_ids, BBox overlaps before any disk write.

        Use this **before** every batch insert — particularly when the
        user asks for a generated/imported schematic; it catches the most
        common failure mode (lib_id typo, ref already in use) without
        leaving the schematic half-patched. Don't try a write and roll
        back: this tool is the cheap pre-flight.

        Args:
            sch_path: ``.kicad_sch`` to validate the patch against.
            parts: JSON string — the same ``[{ref, name OR lib_id, …}]`` list
                you would pass to ``add_schematic_symbols``. Default ``"[]"``.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        try:
            parts_list = json.loads(parts)
        except Exception as exc:
            return {"success": False, "error": f"Invalid JSON: {exc}"}
        doc = SchematicDoc.load(sch_path)
        existing = set(doc.all_refs())
        ok: list[str] = []
        collisions: list[str] = []
        unknown_libs: list[dict[str, str]] = []
        for p in parts_list:
            if not isinstance(p, dict):
                continue
            ref = str(p.get("ref") or "").strip()
            if not ref:
                continue
            if ref in existing:
                collisions.append(ref)
                continue
            lib_id = p.get("lib_id") or resolve_lib_id(p)
            if not lib_id:
                unknown_libs.append({"ref": ref, "lib_id": None})
                continue
            if not doc.find_lib_symbol(lib_id):
                # not embedded yet — try the cache
                from kicad_mcp.generators.symbol_cache import get_real_symbol

                if not get_real_symbol(lib_id):
                    unknown_libs.append({"ref": ref, "lib_id": lib_id})
                    continue
            ok.append(ref)
        return {
            "success": not (collisions or unknown_libs),
            "ok_refs": ok,
            "collisions": collisions,
            "unknown_libs": unknown_libs,
            "existing_refs_count": len(existing),
        }

    @mcp.tool()
    def annotate_schematic(
        sch_path: str, force_renumber: bool = False
    ) -> dict[str, Any]:
        """Assign sequential numbers to unannotated symbol references.

        Walks the schematic and renames every reference that ends with
        ``?`` or that uses a non-conforming ``#PWR`` / ``#FLG`` ref
        (anything other than ``#PWR\\d{4}`` / ``#FLG\\d{4}``). Each prefix
        family gets its own counter starting one above the highest
        existing number for that prefix — so ``R10`` already in the
        schematic plus an unannotated ``R?`` becomes ``R11``.

        Use this when ``kicad-cli sch export netlist`` complains about
        annotation errors (Eeschema's *Tools → Annotate* equivalent
        without a GUI). Updates both the top-level
        ``(property "Reference" "X")`` and any nested
        ``(reference "X")`` entries inside the symbol's
        ``(instances …)`` block.

        Args:
            sch_path: ``.kicad_sch`` to annotate.
            force_renumber: If True, every symbol is renumbered from 1
                regardless of its current ref. Default False — preserves
                already-good refs.

        Returns:
            ``{success, sch_path, renamed: [{old, new}], skipped: [refs]}``.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        try:
            doc = SchematicDoc.load(sch_path)
        except Exception as exc:
            return {"success": False, "error": f"Load failed: {exc}"}

        # Pass 1: collect (start, end, current_ref) for every top-level symbol
        # and tally used numbers per prefix.
        symbols: list[tuple[int, int, str]] = []
        used_numbers: dict[str, set[int]] = {}
        for start, end in doc.iter_symbol_offsets():
            block = doc.text[start:end]
            m = _PROP_REF_RE.search(block)
            if not m:
                continue
            ref = m.group(2)
            symbols.append((start, end, ref))
            if force_renumber:
                continue
            pwr = _PWR_NUMBERED_RE.match(ref)
            if pwr:
                used_numbers.setdefault(f"#{pwr.group(1)}", set()).add(int(pwr.group(2)))
                continue
            split = _REF_SPLIT_RE.match(ref)
            if split and not ref.endswith("?"):
                used_numbers.setdefault(split.group(1), set()).add(int(split.group(2)))

        def _next_free(prefix: str) -> int:
            used = used_numbers.setdefault(prefix, set())
            n = 1
            while n in used:
                n += 1
            used.add(n)
            return n

        def _needs_annotation(ref: str) -> bool:
            if force_renumber:
                return True
            if ref.endswith("?"):
                return True
            if ref.startswith("#PWR") or ref.startswith("#FLG"):
                return _PWR_NUMBERED_RE.match(ref) is None
            return False

        def _prefix_for(ref: str) -> str:
            if ref.startswith("#PWR"):
                return "#PWR"
            if ref.startswith("#FLG"):
                return "#FLG"
            split = _REF_SPLIT_RE.match(ref)
            if split:
                return split.group(1)
            return ref.rstrip("?")

        # Pass 2: build rename map.
        renames: dict[str, str] = {}  # old_ref → new_ref
        skipped: list[str] = []
        for _, _, ref in symbols:
            if not _needs_annotation(ref):
                skipped.append(ref)
                continue
            if ref in renames:
                continue  # already mapped; happens when force_renumber + dups
            prefix = _prefix_for(ref)
            n = _next_free(prefix)
            new_ref = f"{prefix}{n:04d}" if prefix.startswith("#") else f"{prefix}{n}"
            renames[ref] = new_ref

        if not renames:
            return {
                "success": True,
                "sch_path": sch_path,
                "renamed": [],
                "skipped": sorted(set(skipped)),
            }

        # Pass 3: rewrite each affected symbol block — replace
        # property "Reference" and any nested (reference "X").
        # Iterate in reverse so earlier offsets remain valid.
        renamed_list: list[dict[str, str]] = []
        for start, end, old_ref in reversed(symbols):
            if old_ref not in renames:
                continue
            new_ref = renames[old_ref]
            block = doc.text[start:end]

            def _sub_prop(m: re.Match, _new=new_ref) -> str:
                return f'{m.group(1)}"{_new}"'

            new_block, n_prop = _PROP_REF_RE.subn(_sub_prop, block, count=1)
            if n_prop == 0:
                continue
            new_block, _ = _INSTANCE_REF_RE.subn(_sub_prop, new_block)
            doc.text = doc.text[:start] + new_block + doc.text[end:]
            doc._invalidate()  # pylint: disable=protected-access
            renamed_list.append({"old": old_ref, "new": new_ref})

        doc.save()
        return {
            "success": True,
            "sch_path": sch_path,
            "renamed": list(reversed(renamed_list)),
            "skipped": sorted(set(skipped)),
        }

    # ---------------------------------------------------- WRITE: PROPERTY EDIT

    @mcp.tool()
    def update_symbol_property(
        sch_path: str,
        refs: str,
        value: Annotated[str, Field(
            description="New content for the symbol's \"Value\" property; "
            "empty string leaves it unchanged.")] = "",
        footprint: str = "",
        datasheet: str = "",
        description: str = "",
        dnp: str = "",
        in_bom: str = "",
        on_board: str = "",
        in_pos_files: str = "",
        hide_reference: str = "",
        hide_value: str = "",
        hide_footprint: str = "",
        hide_datasheet: str = "",
        hide_description: str = "",
        properties_json: str = "",
    ) -> dict[str, Any]:
        """Update properties / flags of existing symbol instances in a ``.kicad_sch``.

        Use this whenever you need to change a Value, Footprint, Datasheet,
        Description, an arbitrary custom property, or the DNP / in_bom /
        on_board / in_pos_files flag on one or more already-placed
        symbols. This is the right tool for "change R10 to 22k" or "set
        Q1 footprint to SOT-23" or "un-DNP U_BUCK1". Do not delete and
        re-add the symbol — that triggers MCP-snap, BBox-conflict checks
        and wire re-anchoring; this tool is a surgical property edit that
        preserves position, UUID, and connectivity exactly.

        Property updates are skipped silently for symbols that don't
        already carry the named property (no auto-creation). Flag changes
        always succeed because every instance carries the four
        ``(dnp/in_bom/on_board/in_pos_files yes|no)`` lines.

        Args:
            sch_path: ``.kicad_sch`` to patch (WSL or Windows path).
            refs: JSON list of reference designators to update,
                e.g. ``'["R10","R11","U_BUCK1"]'``. Refs not found in
                the schematic are reported in ``not_found``; refs that
                exist but lack a requested property go into ``skipped``.
            value: New "Value" property string. Empty = leave unchanged.
            footprint: New "Footprint" property string. Empty = unchanged.
            datasheet: New "Datasheet" property string. Empty = unchanged.
            description: New "Description" property string. Empty = unchanged.
            dnp: ``"yes"`` / ``"no"`` to flip the (dnp …) flag. Empty = unchanged.
            in_bom: ``"yes"`` / ``"no"``. Empty = unchanged.
            on_board: ``"yes"`` / ``"no"``. Empty = unchanged.
            in_pos_files: ``"yes"`` / ``"no"``. Empty = unchanged.
            hide_reference: ``"yes"`` / ``"no"`` to toggle the
                ``(hide ...)`` flag on the Reference property line —
                ``"yes"`` makes the ref label invisible in Eeschema. The
                tool inserts the line if absent, idempotent on re-run.
                Empty = unchanged.
            hide_value: same as ``hide_reference`` but for the Value
                property.
            hide_footprint: same as ``hide_reference`` but for the
                Footprint property (usually ``"yes"`` — most users hide
                this from the schematic view).
            hide_datasheet: same as ``hide_reference`` but for the
                Datasheet property.
            hide_description: same as ``hide_reference`` but for the
                Description property.
            properties_json: JSON dict of additional ``{property_name:
                value}`` pairs for non-standard fields, e.g.
                ``'{"MPN":"NCP1117ST33T3G","ki_keywords":"linear ldo"}'``.
                Each property is updated only if it already exists on
                the symbol. Empty string = no extras.

        Returns:
            ``{success, sch_path, updated: [{ref, changed: {field: [old,
            new]}}], not_found: [refs], skipped: [{ref, missing: [fields]}],
            errors: [str]}``. ``updated`` lists every ref where at least one
            field changed (with old / new values for traceability).
            Idempotent: running the same call twice yields no changes on
            the second run.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}

        try:
            ref_list = json.loads(refs) if refs else []
            if not isinstance(ref_list, list):
                return {"success": False, "error": "refs must be a JSON list"}
            ref_list = [str(r) for r in ref_list]
        except json.JSONDecodeError as exc:
            return {"success": False, "error": f"refs JSON decode failed: {exc}"}

        extra_props: dict[str, str] = {}
        if properties_json:
            try:
                parsed = json.loads(properties_json)
                if not isinstance(parsed, dict):
                    return {
                        "success": False,
                        "error": "properties_json must decode to an object",
                    }
                extra_props = {str(k): str(v) for k, v in parsed.items()}
            except json.JSONDecodeError as exc:
                return {
                    "success": False,
                    "error": f"properties_json JSON decode failed: {exc}",
                }

        # Build the property-update map: {property_name: new_value}
        prop_updates: dict[str, str] = {}
        if value:
            prop_updates["Value"] = value
        if footprint:
            prop_updates["Footprint"] = footprint
        if datasheet:
            prop_updates["Datasheet"] = datasheet
        if description:
            prop_updates["Description"] = description
        for name, val in extra_props.items():
            prop_updates[name] = val

        # Build flag-update map
        flag_updates: dict[str, str] = {}
        for flag_name, flag_val in [
            ("dnp", dnp),
            ("in_bom", in_bom),
            ("on_board", on_board),
            ("in_pos_files", in_pos_files),
        ]:
            if flag_val:
                if flag_val not in ("yes", "no"):
                    return {
                        "success": False,
                        "error": f"{flag_name} must be 'yes' or 'no', got {flag_val!r}",
                    }
                flag_updates[flag_name] = flag_val

        # Build hide-update map (per-property visibility toggle)
        hide_updates: dict[str, str] = {}
        for prop_name, hide_val in [
            ("Reference", hide_reference),
            ("Value", hide_value),
            ("Footprint", hide_footprint),
            ("Datasheet", hide_datasheet),
            ("Description", hide_description),
        ]:
            if hide_val:
                if hide_val not in ("yes", "no"):
                    return {
                        "success": False,
                        "error": (
                            f"hide_{prop_name.lower()} must be 'yes' or 'no', "
                            f"got {hide_val!r}"
                        ),
                    }
                hide_updates[prop_name] = hide_val

        if not prop_updates and not flag_updates and not hide_updates:
            return {
                "success": False,
                "error": "no property or flag updates requested",
            }

        try:
            doc = SchematicDoc.load(sch_path)
        except Exception as exc:
            return {"success": False, "error": f"Load failed: {exc}"}

        # Locate every requested ref's symbol block.
        # ref → (start, end)
        ref_offsets: dict[str, tuple[int, int]] = {}
        for start, end in doc.iter_symbol_offsets():
            block = doc.text[start:end]
            m = _PROP_REF_RE.search(block)
            if m and m.group(2) in ref_list:
                ref_offsets[m.group(2)] = (start, end)

        not_found = [r for r in ref_list if r not in ref_offsets]
        updated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        # Apply edits in reverse offset order so earlier indices stay valid.
        for ref in sorted(ref_offsets.keys(), key=lambda r: -ref_offsets[r][0]):
            start, end = ref_offsets[ref]
            block = doc.text[start:end]
            changed: dict[str, list[str]] = {}
            missing: list[str] = []

            # Property updates
            for prop_name, new_val in prop_updates.items():
                pattern = re.compile(
                    r'(\(property\s+"'
                    + re.escape(prop_name)
                    + r'"\s+)"((?:[^"\\]|\\.)*)"'
                )
                pm = pattern.search(block)
                if pm is None:
                    missing.append(prop_name)
                    continue
                old_val = pm.group(2)
                if old_val == new_val:
                    continue  # idempotent no-op
                block = (
                    block[: pm.start(0)]
                    + f'{pm.group(1)}"{new_val}"'
                    + block[pm.end(0):]
                )
                changed[prop_name] = [old_val, new_val]

            # Flag updates
            for flag_name, new_val in flag_updates.items():
                pattern = re.compile(_FLAG_RE_TEMPLATE.format(flag=flag_name))
                fm = pattern.search(block)
                if fm is None:
                    missing.append(flag_name)
                    continue
                old_val = fm.group(2)
                if old_val == new_val:
                    continue
                block = (
                    block[: fm.start(0)]
                    + f"({flag_name} {new_val})"
                    + block[fm.end(0):]
                )
                changed[flag_name] = [old_val, new_val]

            # Hide-flag updates (per Property line: insert or rewrite
            # the (hide yes|no) sibling right after the (at ...) clause).
            for prop_name, new_hide in hide_updates.items():
                anchor_pat = re.compile(
                    r'(\(property\s+"'
                    + re.escape(prop_name)
                    + r'"\s+"(?:[^"\\]|\\.)*?"\s*\(at\s+[^\)]+\))'
                )
                am = anchor_pat.search(block)
                if am is None:
                    missing.append(f"hide_{prop_name.lower()}")
                    continue
                anchor_end = am.end()
                tail = block[anchor_end:]
                hm = re.match(r'(\s*)\(hide\s+(yes|no)\)', tail)
                if hm:
                    old_hide = hm.group(2)
                    if old_hide == new_hide:
                        continue  # idempotent
                    block = (
                        block[:anchor_end]
                        + hm.group(1)
                        + f"(hide {new_hide})"
                        + tail[hm.end():]
                    )
                    changed[f"{prop_name}.hide"] = [old_hide, new_hide]
                else:
                    # Insert new (hide ...) — borrow indentation from the
                    # next sibling line so the result is style-stable.
                    sibling = re.match(r'(\s*)\(', tail)
                    indent = sibling.group(1) if sibling else "\n\t\t\t"
                    block = (
                        block[:anchor_end]
                        + indent
                        + f"(hide {new_hide})"
                        + tail
                    )
                    changed[f"{prop_name}.hide"] = ["(none)", new_hide]

            if changed:
                doc.text = doc.text[:start] + block + doc.text[end:]
                doc._invalidate()  # pylint: disable=protected-access
                updated.append({"ref": ref, "changed": changed})
            if missing:
                skipped.append({"ref": ref, "missing": missing})

        if updated:
            doc.save()

        return {
            "success": True,
            "sch_path": sch_path,
            "updated": list(reversed(updated)),
            "not_found": not_found,
            "skipped": skipped,
            "errors": [],
        }

    # ---------------------------------------------------------- WRITE: GROUP

    def _collect_group(
        doc: SchematicDoc, group_id: str
    ) -> dict[str, list[tuple[int, int]]]:
        """Items belonging to ``group_id``. Currently only symbol instances
        carry a group tag — see the header comment for rationale.
        """
        out: dict[str, list[tuple[int, int]]] = {"symbol": []}
        for start, end in doc.iter_symbol_offsets():
            node = _block_node(doc, start, end)
            if not node:
                continue
            if get_symbol_attrs(node).get("group") == group_id:
                out["symbol"].append((start, end))
        return out

    def _transform_block(
        doc: SchematicDoc,
        kind: str,
        start: int,
        end: int,
        dx: float,
        dy: float,
        theta: float,
        pivot: tuple[float, float],
        force: bool,
        warnings: list[str],
    ) -> Optional[tuple[int, int, str]]:
        block = doc.text[start:end]
        node = parse_sexpr(block)
        if kind == "symbol":
            attrs = get_symbol_attrs(node)
            x, y = attrs["x"], attrs["y"]
            new_x, new_y = rotate_point(x + dx, y + dy, theta, pivot)
            new_rot = snap_to_90(int(attrs.get("rot", 0)) + theta)
            res = residual_after_snap(int(attrs.get("rot", 0)) + theta)
            if abs(res) > 0.01 and theta != 0:
                warnings.append(
                    f"{attrs.get('ref')}: residual {res:+.2f}° after 90°-snap"
                )
            new_block, ok = _replace_first_at(block, new_x, new_y, new_rot)
            return (start, end, new_block) if ok else None
        if kind == "wire":
            pts = _wire_pts(node)
            if not pts:
                return None
            x1, y1, x2, y2 = pts
            n1 = rotate_point(x1 + dx, y1 + dy, theta, pivot)
            n2 = rotate_point(x2 + dx, y2 + dy, theta, pivot)
            new_block, ok = _replace_wire_pts(block, n1[0], n1[1], n2[0], n2[1])
            return (start, end, new_block) if ok else None
        if kind in ("label", "global_label", "hierarchical_label"):
            at = _label_at(node)
            if not at:
                return None
            new_x, new_y = rotate_point(at[0] + dx, at[1] + dy, theta, pivot)
            new_rot = snap_to_90(at[2] + theta)
            new_block, ok = _replace_first_at(block, new_x, new_y, new_rot)
            return (start, end, new_block) if ok else None
        return None

    @mcp.tool()
    def move_schematic_group(
        sch_path: str, group_id: str, dx_mm: float = 0.0, dy_mm: float = 0.0
    ) -> dict[str, Any]:
        """Translate every item tagged with ``group_id`` by (dx_mm, dy_mm) on a ``.kicad_sch``.

        Use this to nudge an entire functional block as one unit. Don't
        edit ``(at …)`` coordinates by hand — wires/labels are NOT moved
        with the group (the ``kicad-mcp.group`` property is symbol-only),
        so re-run ``connect_pins`` afterwards if connectivity should
        follow.

        Sibling: ``rotate_schematic_group`` for rotation, ``get_schematic_bbox``
        to inspect group extent, ``list_schematic_groups`` to enumerate.

        Args:
            sch_path: ``.kicad_sch`` to patch.
            group_id: ``kicad-mcp.group`` tag whose symbols are translated.
            dx_mm: Translation along X in mm.
            dy_mm: Translation along Y in mm.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        doc = SchematicDoc.load(sch_path)
        items = _collect_group(doc, group_id)
        if not any(items.values()):
            return {
                "success": False,
                "error": f"No items in group {group_id!r}",
            }
        warnings: list[str] = []
        muts: list[tuple[int, int, str]] = []
        for kind, lst in items.items():
            for start, end in lst:
                m = _transform_block(
                    doc,
                    kind,
                    start,
                    end,
                    float(dx_mm),
                    float(dy_mm),
                    0.0,
                    (0.0, 0.0),
                    True,
                    warnings,
                )
                if m:
                    muts.append(m)
        _apply_replacements(doc, muts)
        doc.save()
        return {
            "success": True,
            "sch_path": sch_path,
            "items_moved": sum(len(v) for v in items.values()),
            "items": {k: len(v) for k, v in items.items()},
            "warnings": warnings,
        }

    @mcp.tool()
    def rotate_schematic_group(
        sch_path: str,
        group_id: str,
        angle_deg: float,
        pivot: str = "centroid",
        pivot_xy: Optional[list[float]] = None,
        tolerance_deg: float = 5.0,
        force: bool = False,
    ) -> dict[str, Any]:
        """Rigid-rotate a group by ``angle_deg`` around ``pivot``.

        Args:
            sch_path: ``.kicad_sch`` to patch.
            group_id: ``kicad-mcp.group`` tag whose symbols are rotated.
            angle_deg: Rotation angle in degrees; each symbol's internal
                rotation is snapped to the nearest 90°.
            pivot: ``centroid`` / ``bbox_center`` (alias for centroid here)
                or ``custom`` (use ``pivot_xy``).
            pivot_xy: ``[x, y]`` pivot point in mm — required when
                ``pivot="custom"``, ignored otherwise.
            tolerance_deg: maximum residual after snapping symbol-internal
                rotations to 90° before erroring. ``force=True`` accepts
                any residual with a warning.
            force: Accept an out-of-tolerance 90°-snap residual, emitting a
                warning instead of erroring.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        doc = SchematicDoc.load(sch_path)
        items = _collect_group(doc, group_id)
        if not any(items.values()):
            return {
                "success": False,
                "error": f"No items in group {group_id!r}",
            }
        # Determine pivot
        all_pts: list[tuple[float, float]] = []
        for start, end in items["symbol"]:
            node = _block_node(doc, start, end)
            if node:
                a = get_symbol_attrs(node)
                all_pts.append((a["x"], a["y"]))
        if pivot == "custom":
            if not pivot_xy or len(pivot_xy) != 2:
                return {
                    "success": False,
                    "error": "pivot=custom requires pivot_xy=[x,y]",
                }
            piv = (float(pivot_xy[0]), float(pivot_xy[1]))
        else:
            if not all_pts:
                return {"success": False, "error": "Cannot compute pivot."}
            piv = bbox_center(bbox_of_points(all_pts))

        # Pre-flight tolerance check on symbols
        residuals = []
        for start, end in items["symbol"]:
            node = _block_node(doc, start, end)
            if not node:
                continue
            a = get_symbol_attrs(node)
            r = residual_after_snap(int(a.get("rot", 0)) + float(angle_deg))
            residuals.append(abs(r))
        max_res = max(residuals) if residuals else 0.0
        if max_res > float(tolerance_deg) and not force:
            return {
                "success": False,
                "error": (
                    f"Rotation residual {max_res:.2f}° exceeds tolerance "
                    f"{tolerance_deg}°. Pass force=True to override."
                ),
                "max_residual_deg": max_res,
            }

        warnings: list[str] = []
        muts: list[tuple[int, int, str]] = []
        for kind, lst in items.items():
            for start, end in lst:
                m = _transform_block(
                    doc,
                    kind,
                    start,
                    end,
                    0.0,
                    0.0,
                    float(angle_deg),
                    piv,
                    force,
                    warnings,
                )
                if m:
                    muts.append(m)
        _apply_replacements(doc, muts)
        doc.save()
        return {
            "success": True,
            "sch_path": sch_path,
            "angle_deg": angle_deg,
            "pivot": {"x": piv[0], "y": piv[1]},
            "items_rotated": sum(len(v) for v in items.values()),
            "items": {k: len(v) for k, v in items.items()},
            "max_residual_deg": max_res,
            "warnings": warnings,
        }

    @mcp.tool()
    def delete_schematic_items(
        sch_path: str,
        refs: Optional[list[str]] = None,
        group_id: str = "",
        types: Optional[list[str]] = None,
        region: Optional[dict[str, float]] = None,
        cascade: bool = True,
    ) -> dict[str, Any]:
        """Delete items by ``refs``, by ``group_id``, or by ``types`` +
        ``region`` (Bug 7 — wires/labels/junctions can't carry a group tag,
        so address them by element kind plus a bounding box instead).

        When a symbol is removed, its previously-attached wires and
        labels would otherwise survive as ghost geometry at the old
        pin coordinates. With ``cascade=True`` (default) every wire,
        global/local/hierarchical label, junction and no_connect whose
        anchor lands on a pin hot-spot of any removed symbol is also
        deleted. Set ``cascade=False`` to keep the legacy behaviour
        (only the symbol blocks themselves removed).

        Args:
            sch_path: ``.kicad_sch`` to edit.
            refs: List of symbol references (``["R1", "C2"]``).
            group_id: ``kicad-mcp.group`` tag — deletes all symbols
                carrying it.
            types: One or more of ``symbol`` / ``wire`` / ``label`` /
                ``global_label`` / ``hierarchical_label`` / ``junction`` /
                ``no_connect``. When combined with ``region``, only items
                of these kinds inside the region are deleted.
            region: ``{"x": …, "y": …, "w": …, "h": …}`` axis-aligned
                box in mm. An item is in-region if **any** of its
                anchor / endpoint coords falls inside.
            cascade: If True (default), follow up symbol-deletes by
                removing every connected wire/label/junction/no_connect
                at the symbol's pin hot-spots.

        At least one of ``refs``, ``group_id`` or ``types``+``region``
        must be supplied. Operations combine — e.g. ``group_id="filter"``
        plus ``types=["wire"]`` and ``region={...}`` deletes the group's
        symbols **and** every wire inside the region.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        doc = SchematicDoc.load(sch_path)
        wanted_refs = set(refs or [])
        wanted_types = set(types or [])
        targets: list[tuple[int, int, str]] = []  # (start, end, label)

        # Bounds of the region — None means no spatial filter.
        rx = ry = rw = rh = None
        if region:
            try:
                rx = float(region.get("x", 0.0))
                ry = float(region.get("y", 0.0))
                rw = float(region.get("w", 0.0))
                rh = float(region.get("h", 0.0))
            except (TypeError, ValueError):
                return {"success": False, "error": "region must have numeric x/y/w/h."}

        def _in_region(x: float, y: float) -> bool:
            if rx is None:
                return True
            return rx <= x <= rx + rw and ry <= y <= ry + rh

        if wanted_refs:
            for start, end in doc.iter_symbol_offsets():
                node = _block_node(doc, start, end)
                if not node:
                    continue
                r = get_symbol_attrs(node).get("ref")
                if r in wanted_refs:
                    targets.append((start, end, f"symbol:{r}"))
        if group_id:
            grp = _collect_group(doc, group_id)
            for kind, lst in grp.items():
                for start, end in lst:
                    targets.append((start, end, f"{kind}@{group_id}"))

        if wanted_types:
            for kind, blocks in _all_top_blocks(doc).items():
                if kind not in wanted_types:
                    continue
                for start, end in blocks:
                    node = _block_node(doc, start, end)
                    if not node:
                        continue
                    in_box = True
                    if rx is not None:
                        in_box = False
                        if kind == "wire":
                            pts = _wire_pts(node)
                            if pts and (
                                _in_region(pts[0], pts[1])
                                or _in_region(pts[2], pts[3])
                            ):
                                in_box = True
                        else:
                            anchor = _label_at(node)
                            if anchor and _in_region(anchor[0], anchor[1]):
                                in_box = True
                    if not in_box:
                        continue
                    # Skip symbols already addressed via refs/group_id to
                    # avoid double-deletion.
                    label = f"{kind}"
                    if kind == "symbol":
                        attrs = get_symbol_attrs(node)
                        ref = attrs.get("ref") or ""
                        if ref in wanted_refs:
                            continue
                        if group_id and attrs.get("group") == group_id:
                            continue
                        label = f"symbol:{ref}"
                    targets.append((start, end, label))

        if not targets:
            return {"success": False, "error": "No matching items."}

        # Cascade — collect pin hot-spots of every symbol scheduled for
        # delete so the follow-up sweep can also remove wires/labels
        # anchored on those pins.
        cascade_hotspots: list[tuple[float, float]] = []
        if cascade:
            for start, end, lbl in targets:
                if not lbl.startswith("symbol"):
                    continue
                node = _block_node(doc, start, end)
                if not node:
                    continue
                attrs = get_symbol_attrs(node)
                lib_id = attrs.get("lib_id")
                if not lib_id:
                    continue
                lib = doc.find_lib_symbol(lib_id)
                if not lib:
                    continue
                for pin in get_lib_symbol_pins(lib[2]):
                    px, py = pin_world_xy(
                        attrs.get("x", 0.0),
                        attrs.get("y", 0.0),
                        int(attrs.get("rot", 0)),
                        attrs.get("mirror"),
                        pin["x"],
                        pin["y"],
                    )
                    cascade_hotspots.append((px, py))

        # Apply in reverse-offset order; dedupe by start so two selectors
        # hitting the same block don't both call delete_block.
        seen: set[int] = set()
        unique_targets: list[tuple[int, int, str]] = []
        for tup in sorted(targets, key=lambda t: t[0], reverse=True):
            if tup[0] in seen:
                continue
            seen.add(tup[0])
            unique_targets.append(tup)
        deleted: list[str] = []
        for start, end, label in unique_targets:
            doc.delete_block(start, end)
            deleted.append(label)

        # Cascade sweep: remove every wire / label / junction /
        # no_connect whose anchor matches a pin hot-spot of any
        # deleted symbol. Done after primary deletes so iter_top_level
        # offsets are fresh.
        if cascade and cascade_hotspots:
            tol = 0.01  # mm — KiCad rounds to 1 nm internally; 10 µm is generous
            def _at_hotspot(px: float, py: float) -> bool:
                for hx, hy in cascade_hotspots:
                    if abs(px - hx) < tol and abs(py - hy) < tol:
                        return True
                return False

            cascade_hits: list[tuple[int, int, str]] = []
            for kind, blocks in _all_top_blocks(doc).items():
                if kind == "symbol":
                    continue
                for s, e in blocks:
                    n2 = _block_node(doc, s, e)
                    if not n2:
                        continue
                    if kind == "wire":
                        pts = _wire_pts(n2)
                        if pts and (_at_hotspot(pts[0], pts[1])
                                    or _at_hotspot(pts[2], pts[3])):
                            cascade_hits.append((s, e, f"cascade:{kind}"))
                    else:
                        anchor = _label_at(n2)
                        if anchor and _at_hotspot(anchor[0], anchor[1]):
                            cascade_hits.append((s, e, f"cascade:{kind}"))
            cascade_hits.sort(key=lambda t: t[0], reverse=True)
            for s, e, lbl in cascade_hits:
                doc.delete_block(s, e)
                deleted.append(lbl)

        doc.save()
        return {
            "success": True,
            "sch_path": sch_path,
            "deleted_count": len(deleted),
            "deleted": deleted,
        }

    @mcp.tool()
    def bulk_swap_symbol(
        sch_path: str,
        old_lib_id: str,
        new_lib_id: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Swap a schematic library symbol project-wide.

        Replaces every ``(lib_id "OLD")`` in the schematic with
        ``(lib_id "NEW")`` and ensures the new lib_symbol block is
        loaded. Use cases:

        * Swap ``Device:R`` → ``Device:R_Small`` projektweit.
        * Migrate from generic to manufacturer-specific symbol
          (``Device:LED`` → ``LED:WS2812B``).
        * Switch from datasheet-tagged placeholder to real lib symbol
          after Layer-T compilation.

        Pin numbers are NOT remapped — the swap assumes both symbols
        share the same pin numbering. For pin-remapping use
        :func:`update_symbol_property` after the swap.

        Args:
            sch_path: ``.kicad_sch`` file to edit.
            old_lib_id: Source library reference (``"Lib:Name"``).
            new_lib_id: Target library reference. Must be installable
                via the patcher's ``ensure_lib_symbol``.
            dry_run: If True, count occurrences but do not write.

        Returns:
            ``{success, sch_path, old_lib_id, new_lib_id, instances_swapped,
            lib_symbol_added, dry_run}``.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"SCH not found: {sch_path}"}
        if not old_lib_id or old_lib_id == new_lib_id:
            return {
                "success": False,
                "error": "old_lib_id must be non-empty and differ from new_lib_id",
            }
        try:
            doc = SchematicDoc.load(sch_path)
            # Count + replace lib_id at symbol-instances
            instances = 0
            # Iterate every top-level (symbol ...) block, find (lib_id "X"), swap
            new_text = doc.text
            patt = re.compile(
                r'(\(lib_id\s+")' + re.escape(old_lib_id) + r'("\))'
            )
            instances = len(patt.findall(new_text))
            if instances == 0:
                return {
                    "success": True,
                    "sch_path": sch_path,
                    "old_lib_id": old_lib_id,
                    "new_lib_id": new_lib_id,
                    "instances_swapped": 0,
                    "lib_symbol_added": False,
                    "note": f"No instances of {old_lib_id!r} found.",
                    "dry_run": dry_run,
                }
            new_text = patt.sub(
                rf'\g<1>{new_lib_id}\g<2>', new_text,
            )
            doc.text = new_text
            doc._invalidate()  # noqa: SLF001 - mark lazy _tree stale after text edit
            # Drop the OLD cached lib_symbol block and re-embed the NEW one
            # fresh from the library. We deliberately do NOT rename the cached
            # block in place: that would keep the old symbol's geometry/pins
            # under the new name (wrong whenever the two symbols differ — the
            # whole point of a swap). drop+re-embed guarantees the new symbol's
            # real graphics and pin map land in lib_symbols.
            old_dropped = doc.drop_lib_symbol(old_lib_id)
            # project_dir lets ensure_lib_symbol resolve project-local
            # (${KIPRJMOD}) libraries, not just stock/global ones.
            project_dir = os.path.dirname(sch_path)
            added = doc.ensure_lib_symbol(new_lib_id, project_dir=project_dir)
            if not added and not doc.find_lib_symbol(new_lib_id):
                return {
                    "success": False,
                    "error": (
                        f"Could not resolve lib_symbol for {new_lib_id!r} "
                        f"(stock, global, or project-local at {project_dir}). "
                        f"Instances were not changed."
                    ),
                }
            if not dry_run:
                doc.save()
            return {
                "success": True,
                "sch_path": sch_path,
                "old_lib_id": old_lib_id,
                "new_lib_id": new_lib_id,
                "instances_swapped": instances,
                "lib_symbol_added": added,
                "old_lib_symbol_dropped": old_dropped,
                "dry_run": dry_run,
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def create_library_symbol(
        lib_path: str,
        symbol_name: str,
        pins: str,
        reference: str = "U",
        value: Annotated[str, Field(
            description="Content of the new symbol's Value field "
            "(defaults to symbol_name when empty).")] = "",
        footprint: str = "",
        datasheet: str = "",
        description: str = "",
        width_mm: float = 0.0,
        overwrite: bool = False,
        register_in_project: str = "",
    ) -> dict[str, Any]:
        """Author a new KiCad library symbol (``.kicad_sym`` entry) from a pin
        spec — a standard rectangular-IC symbol with a body rectangle and pins
        laid out on the requested sides.

        Use this to create a custom part (a chip with no stock symbol, a
        module, a connector) instead of hand-editing a ``.kicad_sym`` file —
        hand edits are error-prone and have corrupted symbols before. After
        creation, register the library project-locally (``register_in_project``)
        so ``add_schematic_symbols`` / ``bulk_swap_symbol`` can resolve it via
        the project ``sym-lib-table``.

        Pins are evenly pitched (2.54 mm) and centred per side. Pins without an
        explicit ``side`` are split deterministically half to the left edge
        (top→bottom) and half to the right.

        Args:
            lib_path: Target ``.kicad_sym`` file (created if missing).
            symbol_name: Bare symbol name (no ``Lib:`` prefix), e.g.
                ``"74HC589"``.
            pins: JSON list of ``{number, name?, type?, side?}``. ``type`` is a
                KiCad pin type (``input``/``output``/``bidirectional``/
                ``power_in``/``passive``/… default ``passive``); ``side`` is
                ``left``/``right``/``top``/``bottom``.
            reference: Reference prefix (default ``"U"``).
            value: Value field (defaults to ``symbol_name``).
            footprint: Default Footprint field for the new symbol (empty = none).
            datasheet: Datasheet URL/field for the new symbol (empty = none).
            description: Description field for the new symbol (empty = none).
            width_mm: Body width override (0 = auto from pin-name lengths).
            overwrite: Replace an existing same-named symbol (default False —
                error out instead).
            register_in_project: Project directory whose ``sym-lib-table``
                should get a ``${KIPRJMOD}`` entry for this lib (empty = skip).

        Returns:
            ``{success, lib_path, symbol_name, lib_id, pin_count,
            lib_created, registered_lib, registered}``.
        """
        lib_path = to_local_path(lib_path)
        try:
            pin_list = json.loads(pins)
        except Exception as exc:
            return {"success": False, "error": f"Invalid pins JSON: {exc}"}
        if not isinstance(pin_list, list):
            return {"success": False, "error": "pins must be a JSON list."}

        lib_dir = os.path.dirname(lib_path) or "."
        if not os.path.isdir(lib_dir):
            return {"success": False, "error": f"Directory not found: {lib_dir}"}

        try:
            block = render_library_symbol(
                symbol_name,
                pin_list,
                reference=reference,
                value=value,
                footprint=footprint,
                datasheet=datasheet,
                description=description,
                width_mm=float(width_mm),
                indent=1,
            )
        except SymbolSpecError as exc:
            return {"success": False, "error": str(exc)}

        created, err = _write_symbol_to_lib(
            lib_path, symbol_name, block, overwrite
        )
        if err:
            return {"success": False, "error": err}

        registered_lib = ""
        registered = False
        if register_in_project:
            proj = to_local_path(register_in_project)
            if not os.path.isdir(proj):
                return {
                    "success": False,
                    "error": f"register_in_project dir not found: {proj}",
                }
            registered_lib, registered = _register_lib_in_project_table(
                proj, lib_path
            )

        lib_nick = registered_lib or os.path.splitext(
            os.path.basename(lib_path)
        )[0]
        return {
            "success": True,
            "lib_path": lib_path,
            "symbol_name": symbol_name,
            "lib_id": f"{lib_nick}:{symbol_name}",
            "pin_count": len(pin_list),
            "lib_created": created,
            "registered_lib": registered_lib,
            "registered": registered,
        }
