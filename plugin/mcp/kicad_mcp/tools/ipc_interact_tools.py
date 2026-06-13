# SPDX-License-Identifier: GPL-3.0-or-later
"""Live-editor interaction tools (selection read/set) over the KiCad IPC API.

Condensed Block-B gaps G1 + G2 (see PLAN.md §4.2). These complement the
existing ``ipc_*`` layer (connect / save / DRC / routing) and the ``live_*``
diff layer; they cover the one thing neither did — reading and setting the
*user's selection* in the running PCB editor.

All tools reuse the connection/precondition helpers from ``ipc_tools`` (one
client, no second connection path) and return the standard ``{success: …}``
dict. Read-only here; the marker/edit/DRC-session gaps (G3-G6) live in
sibling modules.
"""

import json
import os
import re
import subprocess
import tempfile
from typing import Any, Optional

from kicad_mcp.utils.kicad_cli import find_kicad_cli
from kicad_mcp.utils.path_env import to_local_path

from .ipc_tools import (
    _board_default_via_nm,
    _connect_kicad,
    _find_net,
    _layer_to_enum,
    _require_editor,
)


# The MCP "Skizze" (sketch / proposal) layer: where the agent draws marker
# proposals (circle/cross/label) + DRC findings that the user then accepts
# (ipc_accept_markers) or clears (ipc_clear_markers). Graphics only, no copper.
# DEFAULT_MARKER_LAYER is the KiCad layer *identifier* (resolved to the enum
# BL_User_9) — keep it a real layer name. Rename its DISPLAY in KiCad Board
# Setup to SKETCH_LAYER_DISPLAY_NAME so the human sees what it is; the tools
# address it by enum and keep working regardless of the display name.
DEFAULT_MARKER_LAYER = "User.9"
SKETCH_LAYER_DISPLAY_NAME = "MCP.Skizze"
_MARKER_ID_RE = re.compile(r"^(M\d+)")


# ---------------------------------------------------------------------------
# Item serialisation
# ---------------------------------------------------------------------------

_TYPE_FRIENDLY = {
    "FootprintInstance": "footprint",
    "Footprint": "footprint",
    "Track": "track",
    "ArcTrack": "arc_track",
    "Via": "via",
    "Zone": "zone",
    "BoardText": "text",
    "BoardTextBox": "textbox",
    "BoardCircle": "shape_circle",
    "BoardSegment": "shape_segment",
    "BoardArc": "shape_arc",
    "BoardRectangle": "shape_rect",
    "BoardPolygon": "shape_poly",
}


def _nm(v: Any) -> Optional[float]:
    """nanometres → millimetres, rounded to KiCad's 1 nm board resolution."""
    try:
        return round(float(v) / 1_000_000.0, 6)
    except (TypeError, ValueError):
        return None


def _friendly_type(item: Any) -> str:
    return _TYPE_FRIENDLY.get(type(item).__name__, type(item).__name__)


def _safe_uuid(item: Any) -> Optional[str]:
    """Best-effort KIID string (``item.id.value``)."""
    iid = getattr(item, "id", None)
    if iid is None:
        return None
    val = getattr(iid, "value", None)
    return str(val) if val else (str(iid) if iid else None)


def _layer_name(board: Any, layer: Any) -> Optional[str]:
    if layer is None:
        return None
    try:
        return board.get_layer_name(layer)
    except Exception:
        name = getattr(layer, "name", None)
        return str(name) if name else str(layer)


def _field_text(field: Any) -> Optional[str]:
    """Extract the string of a footprint Field (Reference/Value).

    Live kipy nests it: ``Field.text`` is a ``BoardText`` whose ``.value``
    holds the actual string. (The flat ``.text``-is-a-string shape only
    shows up in simplified mocks.) Handle both.
    """
    if field is None:
        return None
    txt = getattr(field, "text", None)
    if txt is None:
        return None
    return getattr(txt, "value", txt)


def _xy_mm(vec: Any) -> Optional[list[float]]:
    if vec is None:
        return None
    x, y = _nm(getattr(vec, "x", None)), _nm(getattr(vec, "y", None))
    return [x, y] if x is not None and y is not None else None


def _serialize_item(board: Any, item: Any) -> dict[str, Any]:
    """Compact JSON view of one board item for the LLM.

    Defensive throughout: kipy item shapes vary by type, so every accessor
    is guarded — a missing attribute just omits that key rather than raising.
    """
    out: dict[str, Any] = {"type": _friendly_type(item), "uuid": _safe_uuid(item)}

    rf = getattr(item, "reference_field", None)
    if rf is not None:
        out["reference"] = _field_text(rf)
        out["value"] = _field_text(getattr(item, "value_field", None))

    # Free text / shape text carries its content in `.value`.
    if out["type"] in ("text", "textbox") and hasattr(item, "value"):
        out["text"] = getattr(item, "value", None)

    net = getattr(item, "net", None)
    if net is not None:
        out["net"] = getattr(net, "name", None)

    layer = getattr(item, "layer", None)
    if layer is not None:
        out["layer"] = _layer_name(board, layer)

    pos = _xy_mm(getattr(item, "position", None))
    if pos is not None:
        out["position_mm"] = pos

    start = _xy_mm(getattr(item, "start", None))
    end = _xy_mm(getattr(item, "end", None))
    if start is not None and end is not None:
        out["start_mm"], out["end_mm"] = start, end

    width = _nm(getattr(item, "width", None))
    if width is not None:
        out["width_mm"] = width

    # Best-effort bounding box (KiCad does the math).
    try:
        bbox = board.get_item_bounding_box(item)
        bpos = _xy_mm(getattr(bbox, "pos", None))
        bsize = _xy_mm(getattr(bbox, "size", None))
        if bpos is not None and bsize is not None:
            out["bbox_mm"] = {"pos": bpos, "size": bsize}
    except Exception:
        pass

    return out


def _footprint_pad_nets(item: Any) -> Optional[list[dict[str, Any]]]:
    """For a footprint instance, return ``[{number, net}]`` from its pads.

    Returns None if the item is not a footprint (no ``definition.pads``).
    Used by ``ipc_inspect_item`` because kipy's ``get_connected_items``
    rejects a footprint argument ("none of the requested IDs were valid") —
    the pad→net map is the meaningful "what is this wired to" answer.
    """
    defn = getattr(item, "definition", None)
    pads = getattr(defn, "pads", None) if defn is not None else None
    if pads is None:
        return None
    out: list[dict[str, Any]] = []
    for p in pads:
        net = getattr(p, "net", None)
        out.append({
            "number": getattr(p, "number", None),
            "net": getattr(net, "name", None) if net is not None else None,
        })
    return out


# ---------------------------------------------------------------------------
# Markers (G3) — circle / cross / label on the MCP user layer, IDs in the text
# ---------------------------------------------------------------------------


def _ensure_layer_enabled(board: Any, layer_enum: int) -> bool:
    """Make the marker layer both **enabled** and **visible** so markers
    actually appear in the editor. Returns True if it had to enable the layer
    (it was disabled). KiCad silently drops ``create_items`` onto a disabled
    layer, and a layer that is enabled but hidden draws nothing the user sees —
    so we guarantee both whenever the MCP draws markers.
    """
    enabled = [int(x) for x in board.get_enabled_layers()]
    changed = layer_enum not in enabled
    if changed:
        board.set_enabled_layers(
            board.get_copper_layer_count(), enabled + [layer_enum]
        )
    # Visibility is a separate setting from enablement — force the marker
    # layer visible so the user sees what the MCP drew.
    try:
        vis = [int(x) for x in board.get_visible_layers()]
        if layer_enum not in vis:
            board.set_visible_layers(vis + [layer_enum])
    except Exception:
        pass
    return changed


def _marker_text_value(marker_id: str, label_text: str) -> str:
    """The BoardText string that encodes a marker's ID (+ optional label)."""
    label_text = (label_text or "").strip()
    return f"{marker_id}: {label_text}" if label_text else marker_id


def _build_marker_items(
    layer_enum: int,
    marker_id: str,
    x_mm: float,
    y_mm: float,
    kind: str,
    label_text: str,
    size_mm: float,
) -> list[Any]:
    """Construct the kipy board items for one marker (shape + ID text).

    Every marker carries a ``BoardText`` starting ``M<n>`` at its anchor so
    ``ipc_list_markers`` / ``ipc_clear_markers`` can find it by ID. Lazy kipy
    import keeps the module importable without a KiCad runtime.
    """
    import kipy.board_types as bt  # local: optional dep
    from kipy.geometry import Vector2  # local: optional dep

    half = max(float(size_mm), 0.2) / 2.0
    items: list[Any] = []

    if kind == "circle":
        c = bt.BoardCircle()
        c.center = Vector2.from_xy_mm(x_mm, y_mm)
        # kipy Circle has NO radius setter — radius is derived from
        # center↔radius_point. Setting `.radius` is a silent no-op that leaves
        # radius_point at (0,0) → a degenerate circle. Set the point instead.
        c.radius_point = Vector2.from_xy_mm(x_mm + half, y_mm)
        c.layer = layer_enum
        items.append(c)
    elif kind == "cross":
        s1 = bt.BoardSegment()
        s1.start = Vector2.from_xy_mm(x_mm - half, y_mm - half)
        s1.end = Vector2.from_xy_mm(x_mm + half, y_mm + half)
        s1.layer = layer_enum
        s2 = bt.BoardSegment()
        s2.start = Vector2.from_xy_mm(x_mm - half, y_mm + half)
        s2.end = Vector2.from_xy_mm(x_mm + half, y_mm - half)
        s2.layer = layer_enum
        items += [s1, s2]
    # "label" adds no shape — just the text below.

    t = bt.BoardText()
    t.value = _marker_text_value(marker_id, label_text)
    t.position = Vector2.from_xy_mm(x_mm, y_mm)
    t.layer = layer_enum
    items.append(t)
    return items


def _scan_marker_texts(board: Any, layer_enum: int) -> list[dict[str, Any]]:
    """Return ``[{id, label, position_mm, _obj}]`` for every ID-tagged text on
    the marker layer (value starting ``M<n>``)."""
    out: list[dict[str, Any]] = []
    try:
        texts = board.get_text()
    except Exception:
        return out
    for t in texts:
        if getattr(t, "layer", None) != layer_enum:
            continue
        val = getattr(t, "value", None)
        m = _MARKER_ID_RE.match(val or "")
        if not m:
            continue
        out.append({
            "id": m.group(1),
            "label": val,
            "position_mm": _xy_mm(getattr(t, "position", None)),
            "_obj": t,
        })
    return out


# Legend lines are tagged so they survive ``ipc_clear_markers`` (which only
# removes ``M<n>`` markers) and aren't miscounted as markers.
_LEGEND_TAG = "☰"  # ☰ — marks a sketch-layer legend/how-to line


def _build_legend_items(
    layer_enum: int, x_mm: float, y_mm: float,
    lines: list[str], size_mm: float = 1.0,
) -> list[Any]:
    """Build stacked BoardText lines for the sketch-layer how-to legend."""
    import kipy.board_types as bt  # local: optional dep
    from kipy.geometry import Vector2  # local: optional dep

    items: list[Any] = []
    step = max(float(size_mm), 0.5) * 1.7
    for i, line in enumerate(lines):
        t = bt.BoardText()
        t.value = f"{_LEGEND_TAG} {line}"
        t.position = Vector2.from_xy_mm(x_mm, y_mm + i * step)
        t.layer = layer_enum
        items.append(t)
    return items


def _legend_items_on_layer(board: Any, layer_enum: int) -> list[Any]:
    """Existing legend text lines on the layer (so we can replace them)."""
    out: list[Any] = []
    try:
        for t in board.get_text():
            if getattr(t, "layer", None) != layer_enum:
                continue
            if str(getattr(t, "value", "") or "").startswith(_LEGEND_TAG):
                out.append(t)
    except Exception:
        pass
    return out


# The how-to legend text (shared by the tool + the presence beacon).
_LEGEND_LINES = [
    f"{SKETCH_LAYER_DISPLAY_NAME} - Vorschlags-/Skizzen-Layer (kein Kupfer)",
    "Der Agent zeichnet hier Marker (Kreis/Kreuz/Label) + DRC-Befunde.",
    "Uebernehmen -> echtes Via: ipc_accept_markers   Liste: ipc_list_markers",
    "Loeschen: ipc_clear_markers   Vor Fertigung leeren: ipc_check_markers_before_save",
]

# Presence beacon runs once per server process. Disable with
# KICAD_MCP_SKETCH_PRESENCE=0 (or false/off/no).
_PRESENCE_DONE = False


def _presence_disabled() -> bool:
    return os.environ.get("KICAD_MCP_SKETCH_PRESENCE", "1").strip().lower() in (
        "0", "false", "off", "no",
    )


def ensure_mcp_presence(board: Any) -> None:
    """First-board-contact presence beacon — strictly NON-MUTATING on the
    board document. It only flips the sketch layer *visible* (a view setting),
    and only when the layer is already enabled in Board Setup.

    It must never enable layers or stamp the legend: both mark the board as
    modified, and because each chat turn is a fresh server process the user
    ended up with a perpetual "ungespeicherte Änderungen" prompt after merely
    *talking* to the MCP. Enable+legend now happen only when the agent
    actually draws (``_ensure_layer_enabled`` in the marker tools /
    ``ipc_draw_sketch_legend``) — i.e. when dirtying the board is the point.
    Runs **once per process**, best-effort (never raises); skipped entirely
    when ``KICAD_MCP_SKETCH_PRESENCE`` is set to 0/false/off/no.
    """
    global _PRESENCE_DONE
    if _PRESENCE_DONE:
        return
    _PRESENCE_DONE = True  # set first so a failure doesn't retry every connect
    if _presence_disabled():
        return
    try:
        layer_enum = _layer_to_enum(DEFAULT_MARKER_LAYER)
        if layer_enum is None:
            return
        enabled = [int(x) for x in board.get_enabled_layers()]
        if layer_enum not in enabled:
            return  # enabling would dirty the board — drawing tools only
        vis = [int(x) for x in board.get_visible_layers()]
        if layer_enum not in vis:
            board.set_visible_layers(vis + [layer_enum])
    except Exception:
        pass


def _reset_presence_for_tests() -> None:
    """Test hook: re-arm the once-per-process presence beacon."""
    global _PRESENCE_DONE
    _PRESENCE_DONE = False


def _iter_board_items(board: Any):
    """Yield items across the board collections used for ref/uuid lookup."""
    for getter in ("get_footprints", "get_tracks", "get_vias", "get_zones",
                   "get_shapes", "get_text"):
        fn = getattr(board, getter, None)
        if fn is None:
            continue
        try:
            yield from fn()
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Generic edits (G4) — by-uuid lookup, move, width
# ---------------------------------------------------------------------------


def _find_items_by_uuids(board: Any, uuids: list[str]) -> dict[str, Any]:
    """Return ``{uuid: item}`` for the given uuids found anywhere on the board."""
    want = {str(u).strip() for u in uuids if str(u).strip()}
    out: dict[str, Any] = {}
    if not want:
        return out
    for it in _iter_board_items(board):
        u = _safe_uuid(it)
        if u in want and u not in out:
            out[u] = it
            if len(out) == len(want):
                break
    return out


def _shift_item(item: Any, dx_mm: float, dy_mm: float) -> bool:
    """Translate an item by (dx, dy) mm in place. Handles position-based
    items (footprints/vias/text) and endpoint-based ones (tracks/segments).
    Returns True if anything moved."""
    from kipy.geometry import Vector2  # local: optional dep

    def _sh(vec):
        return Vector2.from_xy_mm(vec.x / 1_000_000 + dx_mm, vec.y / 1_000_000 + dy_mm)

    moved = False
    pos = getattr(item, "position", None)
    if pos is not None:
        item.position = _sh(pos)
        moved = True
    cen = getattr(item, "center", None)
    if cen is not None:
        item.center = _sh(cen)
        moved = True
    st = getattr(item, "start", None)
    en = getattr(item, "end", None)
    if st is not None and en is not None:
        item.start = _sh(st)
        item.end = _sh(en)
        moved = True
    return moved


def _run_cli_drc(pcb_path: str) -> dict[str, Any]:
    """Run ``kicad-cli pcb drc --format json`` on ``pcb_path`` (sync).

    Returns the parsed report dict (with ``violations`` / ``unconnected_items``
    / ``schematic_parity``) or ``{"error": ...}``. Used by the DRC session —
    the live-editor ``ipc_run_drc`` only opens the GUI dialog.
    """
    cli = find_kicad_cli()
    if not cli:
        return {"error": "kicad-cli not found."}
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "drc.json")
        try:
            proc = subprocess.run(
                [cli, "pcb", "drc", "--format", "json", "-o", out, pcb_path],
                capture_output=True, text=True, timeout=300, check=False,
            )
        except Exception as exc:
            return {"error": f"kicad-cli drc failed: {exc}"}
        if not os.path.isfile(out):
            return {"error": f"DRC produced no report ({proc.stderr[:160]})"}
        try:
            with open(out, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            return {"error": f"could not parse DRC report: {exc}"}


def _violation_pos(viol: dict) -> Optional[list[float]]:
    """First item position of a DRC violation in mm, or None."""
    for it in viol.get("items", []):
        pos = it.get("pos")
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            return [round(float(pos["x"]), 4), round(float(pos["y"]), 4)]
    return None


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register_ipc_interact_tools(mcp) -> None:
    """Register the live selection tools (G1 read, G2 set)."""

    @mcp.tool()
    def ipc_get_selection() -> dict[str, Any]:
        """Read what the user has currently selected in the live PCB editor.

        This is the core "discuss my selection" tool: the user clicks items
        in KiCad, then asks about them in chat. Returns a compact list — type,
        reference, uuid, net, layer, position, bbox — enriched per item.

        Read-only; needs a ``.kicad_pcb`` open in the PCB Editor (open one
        with ``ipc_open_kicad`` first if needed). An empty selection returns
        ``{success, count: 0, items: [], note: "Nichts selektiert"}`` rather
        than an error.

        Use this when the user says "this", "the selected", "what I clicked",
        or asks about parts without naming them. For a named element use
        ``ipc_inspect_item`` instead.
        """
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        # Single-primitive selection can hit the kipy "KiCad is busy and cannot
        # respond" bug — retry it via the central session layer (Task A).
        from kicad_mcp.utils import ipc_session
        try:
            sel = ipc_session.call_with_retry(
                board.get_selection, "get_selection")
        except Exception as exc:
            return {"success": False,
                    "error": f"get_selection failed (after retries): {exc}"}
        items = [_serialize_item(board, it) for it in (sel or [])]
        if not items:
            return {
                "success": True,
                "count": 0,
                "items": [],
                "note": "Nichts selektiert",
            }
        return {"success": True, "count": len(items), "items": items}

    @mcp.tool()
    def ipc_inspect_item(ref_or_uuid: str) -> dict[str, Any]:
        """Inspect one element by reference (``"R12"``) or uuid, including
        what it is electrically connected to.

        Use this when the user names a specific part rather than selecting it —
        "Was hängt an R12?", "Zeig mir Details zu U8". It complements
        ``ipc_get_selection`` (which reads whatever is highlighted in the GUI).
        For a footprint it returns the serialised item plus its ``pads``
        (pad number → net) and the distinct ``nets`` it touches — the
        meaningful "what is this wired to" view. For a track/via/pad it
        returns a ``connected`` list (other items on the same nets via kipy
        ``get_connected_items``). Needs a ``.kicad_pcb`` open in the PCB
        Editor (open one with ``ipc_open_kicad`` first).

        Args:
            ref_or_uuid: A footprint reference (``"R12"``, ``"U8"``) or the
                item's KIID uuid string. Matched against footprint references
                first, then against item uuids across the board collections.
        """
        if not ref_or_uuid or not str(ref_or_uuid).strip():
            return {"success": False, "error": "ref_or_uuid must be non-empty."}
        target = str(ref_or_uuid).strip()
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        found = None
        for it in _iter_board_items(board):
            if _safe_uuid(it) == target:
                found = it
                break
            rf = getattr(it, "reference_field", None)
            if rf is not None and _field_text(rf) == target:
                found = it
                break
        if found is None:
            return {
                "success": False,
                "error": f"No item with reference/uuid {target!r} found.",
            }

        result = {"success": True, "item": _serialize_item(board, found)}
        pad_nets = _footprint_pad_nets(found)
        if pad_nets is not None:
            # Footprint: the pad→net map is the meaningful connectivity view
            # (get_connected_items rejects a footprint argument).
            result["pads"] = pad_nets
            result["nets"] = sorted({p["net"] for p in pad_nets if p["net"]})
        else:
            try:
                connected = board.get_connected_items(found)
                result["connected"] = [
                    _serialize_item(board, c) for c in (connected or [])
                ]
            except Exception:
                result["connected"] = []
        return result

    @mcp.tool()
    def ipc_select_items(
        refs: Optional[list[str]] = None,
        uuids: Optional[list[str]] = None,
        net: str = "",
        item_type: str = "",
        layer: str = "",
    ) -> dict[str, Any]:
        """Set the user's selection in the live PCB editor (native highlight).

        Filters combine (AND): give any of ``refs`` (footprint references),
        ``uuids``, ``net`` (all copper items on that net), ``item_type``
        (footprint/track/via/zone/text), ``layer`` (layer name). The matching
        items are added to the editor selection so the user can scroll to
        them — KiCad highlights them natively.

        Use this for "Markiere alle GND-Vias", "Selektiere R12", "Zeig mir die
        Tracks auf In1". Clears any prior selection first. Needs a board open.

        Args:
            refs: Footprint references to select (``["R12", "U8"]``).
            uuids: Item KIID uuid strings to select.
            net: Select every item carrying this net name (``"GND"``).
            item_type: Select every item of this kind — ``footprint``,
                ``track``, ``via``, ``zone``, ``text``.
            layer: Select every item on this layer name (``"In1.Cu"``).

        Returns ``{success, selected_count, note}``.
        """
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        ref_set = {str(r).strip() for r in (refs or []) if str(r).strip()}
        uuid_set = {str(u).strip() for u in (uuids or []) if str(u).strip()}
        want_net = net.strip()
        want_type = item_type.strip().lower()
        want_layer = layer.strip()

        if not (ref_set or uuid_set or want_net or want_type or want_layer):
            return {
                "success": False,
                "error": "Give at least one filter (refs/uuids/net/item_type/layer).",
            }

        matched = []
        for it in _iter_board_items(board):
            if uuid_set and _safe_uuid(it) in uuid_set:
                matched.append(it)
                continue
            if ref_set:
                rf = getattr(it, "reference_field", None)
                if rf is not None and _field_text(rf) in ref_set:
                    matched.append(it)
                    continue
            if want_net:
                netobj = getattr(it, "net", None)
                if netobj is not None and getattr(netobj, "name", None) == want_net:
                    matched.append(it)
                    continue
            if want_type and _friendly_type(it) == want_type:
                matched.append(it)
                continue
            if want_layer and _layer_name(board, getattr(it, "layer", None)) == want_layer:
                matched.append(it)
                continue

        if not matched:
            return {
                "success": True,
                "selected_count": 0,
                "note": "Kein Element passte auf die Filter.",
            }
        try:
            board.clear_selection()
            board.add_to_selection(matched)
        except Exception as exc:
            return {"success": False, "error": f"selection update failed: {exc}"}
        return {
            "success": True,
            "selected_count": len(matched),
            "note": f"{len(matched)} Element(e) selektiert.",
        }

    @mcp.tool()
    def ipc_clear_selection() -> dict[str, Any]:
        """Clear the current selection in the live PCB editor.

        Use this after ``ipc_select_items`` to deselect, or before setting a
        fresh selection so nothing from a previous step lingers. It does not
        delete anything — it only drops the editor's selection highlight, so
        it is always safe to call. Needs a ``.kicad_pcb`` open in the PCB
        Editor (open one with ``ipc_open_kicad`` first); on an empty board or
        with nothing selected it is a no-op that still reports success.
        """
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        try:
            board.clear_selection()
        except Exception as exc:
            return {"success": False, "error": f"clear_selection failed: {exc}"}
        return {"success": True, "note": "Selektion geleert."}

    # ----------------------------------------------------------- markers (G3)

    @mcp.tool()
    def ipc_draw_markers(
        markers: str, layer: str = DEFAULT_MARKER_LAYER
    ) -> dict[str, Any]:
        """Draw suggestion markers (circle / cross / label) on the MCP sketch
        layer (User.9, rename its display to "MCP.Skizze") in the live PCB
        editor — graphics only, no copper; the agent's proposal sketch.

        Use this to point the user at places on the board: "here are the 3 vias
        I'd add", "this clearance is tight". Each marker gets a sequential ID
        (``M1``, ``M2`, …) encoded in its text so ``ipc_list_markers`` /
        ``ipc_clear_markers`` can address it. The marker layer is auto-enabled
        if needed (default ``User.9``; it stays enabled after a clear). Undoable
        in KiCad like any edit. Don't draw markers on copper layers — pass a
        user/comment layer.

        Args:
            markers: JSON list of ``{x_mm, y_mm, type?, label_text?, size_mm?}``.
                ``type`` is ``circle`` (default), ``cross`` or ``label``;
                ``size_mm`` defaults to 1.5.
            layer: Marker layer name (default ``User.9``).

        Returns:
            ``{success, drawn: [{id, type, position_mm}], layer,
            layer_enabled, count}``.
        """
        try:
            spec = json.loads(markers)
        except Exception as exc:
            return {"success": False, "error": f"Invalid markers JSON: {exc}"}
        if not isinstance(spec, list) or not spec:
            return {"success": False, "error": "markers must be a non-empty JSON list."}
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {"success": False, "error": f"Unknown layer {layer!r}."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            layer_enabled = _ensure_layer_enabled(board, layer_enum)
        except Exception as exc:
            return {"success": False, "error": f"could not enable {layer}: {exc}"}

        existing = _scan_marker_texts(board, layer_enum)
        next_n = 1 + max(
            (int(m["id"][1:]) for m in existing if m["id"][1:].isdigit()), default=0
        )

        all_items: list[Any] = []
        drawn: list[dict[str, Any]] = []
        for i, mk in enumerate(spec):
            if not isinstance(mk, dict):
                return {"success": False, "error": f"marker #{i} is not an object."}
            kind = str(mk.get("type", "circle")).strip().lower()
            if kind not in ("circle", "cross", "label"):
                return {
                    "success": False,
                    "error": f"marker #{i}: type must be circle/cross/label.",
                }
            try:
                x = float(mk["x_mm"]); y = float(mk["y_mm"])
            except (KeyError, TypeError, ValueError):
                return {"success": False, "error": f"marker #{i} needs numeric x_mm/y_mm."}
            mid = f"M{next_n + i}"
            all_items += _build_marker_items(
                layer_enum, mid, x, y, kind,
                str(mk.get("label_text", "")), float(mk.get("size_mm", 1.5)),
            )
            drawn.append({"id": mid, "type": kind, "position_mm": [round(x, 4), round(y, 4)]})

        try:
            commit = board.begin_commit()
            board.create_items(all_items)
            board.push_commit(commit, f"kicad-mcp draw_markers ({len(drawn)})")
        except Exception as exc:
            return {"success": False, "error": f"create_items failed: {exc}"}

        return {
            "success": True,
            "drawn": drawn,
            "count": len(drawn),
            "layer": layer,
            "layer_enabled": layer_enabled,
        }

    @mcp.tool()
    def ipc_list_markers(layer: str = DEFAULT_MARKER_LAYER) -> dict[str, Any]:
        """List the MCP suggestion markers currently on the board.

        Use this to see what markers exist before accepting/clearing them, or
        to remind the user what was proposed. Reads the ID-tagged texts on the
        marker layer. Needs a board open.

        Args:
            layer: Marker layer name (default ``User.9``).

        Returns:
            ``{success, count, markers: [{id, label, position_mm}], layer}``.
        """
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {"success": False, "error": f"Unknown layer {layer!r}."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        found = _scan_marker_texts(board, layer_enum)
        markers = [
            {"id": m["id"], "label": m["label"], "position_mm": m["position_mm"]}
            for m in sorted(found, key=lambda m: int(m["id"][1:]) if m["id"][1:].isdigit() else 0)
        ]
        return {"success": True, "count": len(markers), "markers": markers, "layer": layer}

    @mcp.tool()
    def ipc_clear_markers(
        ids: Optional[list[str]] = None, layer: str = DEFAULT_MARKER_LAYER
    ) -> dict[str, Any]:
        """Remove MCP suggestion markers from the sketch layer (all, or by ID).

        Use this to clean up after a discussion ("clear the markers") or to
        drop specific ones ("remove M2 and M4"). With no ``ids`` it removes
        every ``M<n>`` marker (text + co-located shape); with ``ids`` only those.
        The how-to legend (``ipc_draw_sketch_legend``) is left intact. Undoable.
        Needs a board open.

        Args:
            ids: Marker IDs to remove (``["M1", "M3"]``). Omit to clear all.
            layer: Sketch layer name (default ``User.9`` / "MCP.Skizze").

        Returns:
            ``{success, removed_count, removed_ids, layer}``.
        """
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {"success": False, "error": f"Unknown layer {layer!r}."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        id_set = {str(i).strip() for i in ids} if ids else None
        marks = _scan_marker_texts(board, layer_enum)
        target = marks if id_set is None else [m for m in marks if m["id"] in id_set]
        try:
            shapes = [s for s in board.get_shapes()
                      if getattr(s, "layer", None) == layer_enum]
        except Exception:
            shapes = []
        victims: list[Any] = []
        removed_ids: list[str] = []
        for m in target:
            victims.append(m["_obj"])
            removed_ids.append(m["id"])
            px, py = (m["position_mm"] or [None, None])
            if px is None:
                continue
            # shapes co-located with this marker's anchor (within 3 mm) — the
            # marker's circle/cross. Legend text (non-M<n>) is never touched.
            for s in shapes:
                sp = _xy_mm(getattr(s, "center", None)) or _xy_mm(getattr(s, "start", None))
                if sp and abs(sp[0] - px) <= 3.0 and abs(sp[1] - py) <= 3.0:
                    victims.append(s)
        if not victims:
            return {"success": True, "removed_count": 0, "removed_ids": [], "layer": layer}
        try:
            commit = board.begin_commit()
            board.remove_items(victims)
            board.push_commit(commit, f"kicad-mcp clear_markers ({len(removed_ids)})")
        except Exception as exc:
            return {"success": False, "error": f"remove_items failed: {exc}"}
        return {
            "success": True,
            "removed_count": len(removed_ids),
            "removed_ids": removed_ids,
            "layer": layer,
        }

    @mcp.tool()
    def ipc_check_markers_before_save(
        layer: str = DEFAULT_MARKER_LAYER
    ) -> dict[str, Any]:
        """Warn if MCP suggestion markers are still on the board.

        Use this before a git commit / handing the board back: leftover markers
        are graphics that shouldn't ship. Returns a warning flag + the IDs so
        you can prompt the user to ``ipc_clear_markers`` first. Needs a board open.

        Args:
            layer: Marker layer name (default ``User.9``).

        Returns:
            ``{success, marker_count, warn, ids, layer}``.
        """
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {"success": False, "error": f"Unknown layer {layer!r}."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        ids = [m["id"] for m in _scan_marker_texts(board, layer_enum)]
        return {
            "success": True,
            "marker_count": len(ids),
            "warn": len(ids) > 0,
            "ids": ids,
            "layer": layer,
        }

    @mcp.tool()
    def ipc_draw_sketch_legend(
        layer: str = DEFAULT_MARKER_LAYER,
        x_mm: float = 10.0,
        y_mm: float = 10.0,
        size_mm: float = 1.0,
    ) -> dict[str, Any]:
        """Stamp a short how-to legend onto the MCP sketch layer (User.9).

        Use this once per board so the sketch layer is self-documenting: a
        human opening the PCB sees on the layer what it is and how the
        agent's proposal markers work. The legend text is tagged so
        ``ipc_clear_markers`` leaves it intact (it only removes ``M<n>``
        markers). Re-running replaces the previous legend. Needs a board open.

        Args:
            layer: Sketch layer name (default ``User.9`` / "MCP.Skizze").
            x_mm, y_mm: Top-left of the legend block (board mm).
            size_mm: Text size (default 1.0 mm).

        Returns:
            ``{success, lines, layer, position_mm}``.
        """
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {"success": False, "error": f"Unknown layer {layer!r}."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        try:
            _ensure_layer_enabled(board, layer_enum)
        except Exception:
            pass

        lines = list(_LEGEND_LINES)
        old = _legend_items_on_layer(board, layer_enum)
        new = _build_legend_items(layer_enum, x_mm, y_mm, lines, size_mm)
        try:
            commit = board.begin_commit()
            if old:
                board.remove_items(old)
            board.create_items(new)
            board.push_commit(commit, "kicad-mcp sketch legend")
        except Exception as exc:
            return {"success": False, "error": f"create_items failed: {exc}"}
        return {
            "success": True,
            "lines": lines,
            "layer": layer,
            "position_mm": [round(float(x_mm), 4), round(float(y_mm), 4)],
        }

    # ------------------------------------------------------- generic edits (G4)

    def _new_via(board, x_mm, y_mm, net_name, size_mm, drill_mm):
        """Build a through-via item (caller commits). Returns (via, error)."""
        import kipy.board_types as bt  # local: optional dep
        from kipy.geometry import Vector2  # local: optional dep
        v = bt.Via()
        v.position = Vector2.from_xy_mm(float(x_mm), float(y_mm))
        if net_name:
            net = _find_net(board, net_name)
            if net is None:
                return None, f"net {net_name!r} not found on board"
            v.net = net
        # A default Via() has diameter/drill 0 and KiCad keeps it at 0 → a
        # degenerate via. ALWAYS set both, using the board default when the
        # caller passes 0.
        def_d, def_k = _board_default_via_nm(board)
        v.diameter = (
            int(round(float(size_mm) * 1_000_000)) if size_mm and float(size_mm) > 0
            else def_d
        )
        v.drill_diameter = (
            int(round(float(drill_mm) * 1_000_000)) if drill_mm and float(drill_mm) > 0
            else def_k
        )
        return v, None

    @mcp.tool()
    def ipc_create_via(
        x_mm: float, y_mm: float, net: str = "",
        size_mm: float = 0.0, drill_mm: float = 0.0,
    ) -> dict[str, Any]:
        """Create a through via at a position in the live PCB editor.

        Use this when the user asks for a stitching/transition via at a spot —
        "set a GND via here". For turning suggestion markers into real vias use
        ``ipc_accept_markers`` instead. Undoable. Needs a board open.

        Args:
            x_mm, y_mm: Via centre (board mm).
            net: Net name to put the via on (``"GND"``); empty = unassigned.
            size_mm: Via diameter; 0 = board default.
            drill_mm: Drill diameter; 0 = board default.

        Returns:
            ``{success, net, position_mm, size_mm, drill_mm}``.
        """
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        via, verr = _new_via(board, x_mm, y_mm, net.strip(), size_mm, drill_mm)
        if verr:
            return {"success": False, "error": verr}
        try:
            commit = board.begin_commit()
            board.create_items([via])
            board.push_commit(commit, "kicad-mcp create_via")
        except Exception as exc:
            return {"success": False, "error": f"create via failed: {exc}"}
        return {
            "success": True, "net": net.strip(),
            "position_mm": [round(float(x_mm), 4), round(float(y_mm), 4)],
            "size_mm": size_mm, "drill_mm": drill_mm,
        }

    @mcp.tool()
    def ipc_accept_markers(
        ids: list[str], net: str,
        size_mm: float = 0.0, drill_mm: float = 0.0,
        layer: str = DEFAULT_MARKER_LAYER,
    ) -> dict[str, Any]:
        """Turn suggestion markers into real vias, then remove those markers.

        Use this for the "accept the proposal" step: ``ipc_draw_markers``
        proposes via spots, the user approves some ("M1 und M3 übernehmen"),
        and this drops a real via at each accepted marker's position and clears
        them. Don't use it for arbitrary vias — that's ``ipc_create_via``.
        Undoable. Needs a board open.

        Args:
            ids: Marker IDs to accept (``["M1", "M3"]``).
            net: Net for the created vias (``"GND"``).
            size_mm / drill_mm: Via size / drill; 0 = board default.
            layer: Marker layer (default ``User.9``).

        Returns:
            ``{success, vias_created, accepted_ids, net}``.
        """
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {"success": False, "error": f"Unknown layer {layer!r}."}
        if not ids:
            return {"success": False, "error": "ids must be non-empty."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        id_set = {str(i).strip() for i in ids}
        marks = [m for m in _scan_marker_texts(board, layer_enum) if m["id"] in id_set]
        if not marks:
            return {"success": False, "error": "none of the given marker IDs were found."}
        vias = []
        for m in marks:
            pos = m["position_mm"]
            if not pos:
                continue
            via, verr = _new_via(board, pos[0], pos[1], net.strip(), size_mm, drill_mm)
            if verr:
                return {"success": False, "error": verr}
            vias.append(via)
        # remove the accepted markers' texts + co-located shapes
        victims = [m["_obj"] for m in marks]
        try:
            shapes = [s for s in board.get_shapes()
                      if getattr(s, "layer", None) == layer_enum]
        except Exception:
            shapes = []
        for m in marks:
            px, py = (m["position_mm"] or [None, None])
            if px is None:
                continue
            for s in shapes:
                sp = _xy_mm(getattr(s, "center", None)) or _xy_mm(getattr(s, "start", None))
                if sp and abs(sp[0] - px) <= 3.0 and abs(sp[1] - py) <= 3.0:
                    victims.append(s)
        try:
            commit = board.begin_commit()
            if vias:
                board.create_items(vias)
            board.remove_items(victims)
            board.push_commit(commit, f"kicad-mcp accept_markers ({len(vias)})")
        except Exception as exc:
            return {"success": False, "error": f"accept failed: {exc}"}
        return {
            "success": True, "vias_created": len(vias),
            "accepted_ids": [m["id"] for m in marks], "net": net.strip(),
        }

    @mcp.tool()
    def ipc_set_track_width(uuids: list[str], width_mm: float) -> dict[str, Any]:
        """Set the width of one or more tracks (by uuid) in the live editor.

        Use this for "make these traces 0.5 mm" after selecting/finding them
        (uuids come from ``ipc_get_selection`` / ``ipc_inspect_item``). Only
        items that have a width (tracks) are changed. Undoable. Needs a board open.

        Args:
            uuids: Track KIID uuid strings.
            width_mm: New width in mm.

        Returns:
            ``{success, changed, skipped}``.
        """
        if not uuids:
            return {"success": False, "error": "uuids must be non-empty."}
        if float(width_mm) <= 0:
            return {"success": False, "error": "width_mm must be > 0."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        items = _find_items_by_uuids(board, uuids)
        changed, skipped = [], []
        w_nm = int(round(float(width_mm) * 1_000_000))
        for u, it in items.items():
            if getattr(it, "width", None) is None:
                skipped.append(u)
                continue
            it.width = w_nm
            changed.append(it)
        if not changed:
            return {"success": True, "changed": 0, "skipped": list(items.keys())}
        try:
            commit = board.begin_commit()
            board.update_items(changed)
            board.push_commit(commit, f"kicad-mcp set_track_width {width_mm}")
        except Exception as exc:
            return {"success": False, "error": f"update failed: {exc}"}
        return {"success": True, "changed": len(changed), "skipped": skipped}

    @mcp.tool()
    def ipc_move_items(
        uuids: list[str], dx_mm: float = 0.0, dy_mm: float = 0.0,
    ) -> dict[str, Any]:
        """Translate items (by uuid) by a delta in the live editor.

        Use this for "nudge these 0.5 mm right" / "shift the selection". Works
        on footprints, tracks, vias, shapes and text. For an absolute footprint
        pose use ``ipc_set_footprint_pose``. Undoable. Needs a board open.

        Args:
            uuids: Item KIID uuid strings (from selection/inspect).
            dx_mm, dy_mm: Delta in mm (KiCad Y is down).

        Returns:
            ``{success, moved, not_found}``.
        """
        if not uuids:
            return {"success": False, "error": "uuids must be non-empty."}
        if dx_mm == 0 and dy_mm == 0:
            return {"success": False, "error": "dx_mm/dy_mm are both zero — nothing to move."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        items = _find_items_by_uuids(board, uuids)
        moved = [it for u, it in items.items() if _shift_item(it, float(dx_mm), float(dy_mm))]
        not_found = [u for u in uuids if str(u).strip() not in items]
        if not moved:
            return {"success": True, "moved": 0, "not_found": not_found}
        try:
            commit = board.begin_commit()
            board.update_items(moved)
            board.push_commit(commit, f"kicad-mcp move_items ({dx_mm},{dy_mm})")
        except Exception as exc:
            return {"success": False, "error": f"move failed: {exc}"}
        return {"success": True, "moved": len(moved), "not_found": not_found}

    @mcp.tool()
    def ipc_remove_items(uuids: list[str]) -> dict[str, Any]:
        """Delete items (by uuid) from the board in the live editor.

        Use this to remove tracks/vias/footprints the user points at by uuid
        (from ``ipc_get_selection``). Destructive but undoable. Needs a board open.

        Args:
            uuids: Item KIID uuid strings.

        Returns:
            ``{success, removed, not_found}``.
        """
        if not uuids:
            return {"success": False, "error": "uuids must be non-empty."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        items = _find_items_by_uuids(board, uuids)
        victims = list(items.values())
        not_found = [u for u in uuids if str(u).strip() not in items]
        if not victims:
            return {"success": True, "removed": 0, "not_found": not_found}
        try:
            commit = board.begin_commit()
            board.remove_items(victims)
            board.push_commit(commit, f"kicad-mcp remove_items ({len(victims)})")
        except Exception as exc:
            return {"success": False, "error": f"remove failed: {exc}"}
        return {"success": True, "removed": len(victims), "not_found": not_found}

    # --------------------------------------------------------- DRC session (G5)

    @mcp.tool()
    def ipc_drc_session_start(
        pcb_path: str = "",
        max_markers: int = 25,
        include_unconnected: bool = True,
        layer: str = DEFAULT_MARKER_LAYER,
    ) -> dict[str, Any]:
        """Run DRC on the live board and pin the problems as markers.

        Use this to start a guided DRC fix session. It saves the live PCB,
        runs headless ``kicad-cli`` DRC, then drops a cross
        marker (G3) at each violation — capped at ``max_markers`` so a board
        with hundreds of issues doesn't get swamped. Returns counts by severity
        and type plus the marked violations (with their item uuids, so you can
        ``ipc_select_items(uuids=…)`` to zoom one in, fix it with the G4 edit
        tools, then call this again to re-check). Needs a board open.

        Args:
            pcb_path: ``.kicad_pcb`` to check; empty = derive from the open
                document.
            max_markers: Max markers to draw (default 25).
            include_unconnected: Also mark ``unconnected_items`` (default True).
            layer: Marker layer (default ``User.9``).

        Returns:
            ``{success, total, severity_counts, type_counts, marked,
            markers: [{id, type, severity, position_mm, item_uuids}], pcb_path}``.
        """
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {"success": False, "error": f"Unknown layer {layer!r}."}
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        # Save the live board so DRC checks the current state.
        try:
            board.save()
        except Exception as exc:
            return {"success": False, "error": f"could not save board for DRC: {exc}"}

        path = to_local_path(pcb_path) if pcb_path else ""
        if not path:
            try:
                path = to_local_path(board.name)
            except Exception:
                path = ""
        if not path or not os.path.isfile(path):
            return {
                "success": False,
                "error": "pcb_path not given and could not be derived; pass it explicitly.",
            }

        report = _run_cli_drc(path)
        if "error" in report:
            return {"success": False, "error": report["error"]}

        groups = list(report.get("violations", []))
        if include_unconnected:
            groups += list(report.get("unconnected_items", []))
        groups += list(report.get("schematic_parity", []))

        sev_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        for v in groups:
            sev_counts[v.get("severity", "?")] = sev_counts.get(v.get("severity", "?"), 0) + 1
            type_counts[v.get("type", "?")] = type_counts.get(v.get("type", "?"), 0) + 1

        # Errors first, then warnings; mark up to max_markers.
        ordered = sorted(groups, key=lambda v: 0 if v.get("severity") == "error" else 1)
        try:
            _ensure_layer_enabled(board, layer_enum)
        except Exception:
            pass
        existing = _scan_marker_texts(board, layer_enum)
        next_n = 1 + max(
            (int(m["id"][1:]) for m in existing if m["id"][1:].isdigit()), default=0
        )

        all_items: list[Any] = []
        markers: list[dict[str, Any]] = []
        for v in ordered[: max(0, int(max_markers))]:
            pos = _violation_pos(v)
            if pos is None:
                continue
            mid = f"M{next_n + len(markers)}"
            label = f"{v.get('severity', '?')}:{v.get('type', '?')}"
            all_items += _build_marker_items(layer_enum, mid, pos[0], pos[1], "cross", label, 1.5)
            markers.append({
                "id": mid, "type": v.get("type"), "severity": v.get("severity"),
                "position_mm": pos,
                "item_uuids": [it.get("uuid") for it in v.get("items", []) if it.get("uuid")],
            })

        if all_items:
            try:
                commit = board.begin_commit()
                board.create_items(all_items)
                board.push_commit(commit, f"kicad-mcp drc markers ({len(markers)})")
            except Exception as exc:
                return {"success": False, "error": f"could not draw DRC markers: {exc}"}

        return {
            "success": True,
            "total": len(groups),
            "severity_counts": sev_counts,
            "type_counts": type_counts,
            "marked": len(markers),
            "markers": markers,
            "pcb_path": path,
        }

    # ---------------------------------------------------------- session status (G6)

    @mcp.tool()
    def ipc_session_status(layer: str = DEFAULT_MARKER_LAYER) -> dict[str, Any]:
        """Summarise the live interaction state: open markers + current selection.

        Use this as the "where are we" overview before deciding the next step —
        how many suggestion/DRC markers are still on the board (with their IDs)
        and what the user currently has selected. A cheap, read-only roll-up
        (it does not re-run DRC). Needs a board open.

        Args:
            layer: Marker layer to count (default ``User.9``).

        Returns:
            ``{success, markers: {count, ids}, selection: {count, types},
            next_steps}``.
        """
        layer_enum = _layer_to_enum(layer)
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        marker_ids = []
        if layer_enum is not None:
            marker_ids = [m["id"] for m in _scan_marker_texts(board, layer_enum)]
        try:
            sel = board.get_selection() or []
        except Exception:
            sel = []
        sel_types: dict[str, int] = {}
        for it in sel:
            t = _friendly_type(it)
            sel_types[t] = sel_types.get(t, 0) + 1

        hints = []
        if marker_ids:
            hints.append(f"{len(marker_ids)} Skizze-Marker auf {layer} — übernehmen (ipc_accept_markers) oder löschen (ipc_clear_markers).")
        if sel:
            hints.append(f"{len(sel)} item(s) selected — inspect/edit via G4 tools.")
        if not hints:
            hints.append("Nothing pending. Run ipc_drc_session_start to surface DRC issues.")

        return {
            "success": True,
            "markers": {"count": len(marker_ids), "ids": marker_ids},
            "selection": {"count": len(sel), "types": sel_types},
            "next_steps": hints,
        }
