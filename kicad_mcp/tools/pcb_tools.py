# SPDX-License-Identifier: GPL-3.0-or-later
"""
PCB analysis tools for KiCad .kicad_pcb files.

Uses the pcbnew Python API when available (fast, reliable, format-independent).
Falls back to S-expression text parsing when pcbnew is not importable.
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
import importlib.util
import logging
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP

from kicad_mcp.utils.path_env import to_local_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pcbnew-based extraction (preferred, lazy-imported)
# ---------------------------------------------------------------------------
# The pcbnew module takes ~25s to initialize. To keep MCP server startup fast
# (so health checks don't time out), we probe availability cheaply via
# find_spec() and defer the actual import until the first PCB-tool call.

_HAS_PCBNEW = importlib.util.find_spec("pcbnew") is not None
if _HAS_PCBNEW:
    logger.info("pcbnew module found — will lazy-import on first PCB-tool call")
else:
    logger.info("pcbnew not available — falling back to S-expression parser")

_pcbnew = None  # module cache, populated on first _get_pcbnew() call


def _get_pcbnew():
    """Import and cache the pcbnew module on first call. Raises ImportError
    if pcbnew is not available.

    On Windows, when KiCad's GUI is running concurrently, ``import pcbnew``
    re-initializes wxApp inside this Python process and triggers a wxWidgets
    debug-assert dialog ("Multiple wxApp instances"). Suppress those dialogs
    via ``WXSUPPRESS_DIALOGS=1`` before the import. The behaviour is identical
    to clicking "Continue" on the assert; pcbnew still loads correctly.
    """
    global _pcbnew
    if _pcbnew is None:
        # wxPython, when bundled with pcbnew, raises a debug-assert dialog
        # on Windows for double-wxApp-init even though the parent KiCad GUI
        # is in a different OS process. Disable wx asserts before the import.
        try:
            import wx as _wx  # type: ignore
            _wx.DisableAsserts()  # pylint: disable=no-member
        except Exception:
            pass  # wx not yet importable — pcbnew import below pulls it in
        logger.info("Importing pcbnew (this may take ~25s on first call)...")
        import pcbnew as _mod
        # After pcbnew is in, retry DisableAsserts in case wx was lazy-loaded.
        try:
            import wx as _wx2  # type: ignore
            _wx2.DisableAsserts()  # pylint: disable=no-member
        except Exception:
            pass
        _pcbnew = _mod
        logger.info("pcbnew imported successfully")
    return _pcbnew


def _load_board(pcb_path: str):
    """Load a board via pcbnew. Returns a BOARD object."""
    pcbnew = _get_pcbnew()
    return pcbnew.LoadBoard(pcb_path)


def _pcbnew_extract_footprints(board) -> list[dict[str, Any]]:
    pcbnew = _get_pcbnew()
    footprints = []
    for fp in board.GetFootprints():
        pos = fp.GetPosition()
        footprints.append({
            "reference": fp.GetReference(),
            "value": fp.GetValue(),
            "footprint_id": str(fp.GetFPID().GetUniStringLibItemName()),
            "layer": board.GetLayerName(fp.GetLayer()),
            "position": [pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)],
            "rotation": fp.GetOrientationDegrees(),
            "pad_count": fp.GetPadCount(),
        })
    return footprints


def _pcbnew_extract_nets(board) -> list[dict[str, Any]]:
    nets = []
    netinfo = board.GetNetInfo()
    for i in range(board.GetNetCount()):
        net = netinfo.GetNetItem(i)
        nets.append({"number": net.GetNetCode(), "name": net.GetNetname()})
    return nets


def _pcbnew_extract_tracks(board) -> list[dict[str, Any]]:
    pcbnew = _get_pcbnew()
    tracks = []
    for trk in board.GetTracks():
        if trk.GetClass() == "PCB_VIA":
            continue
        start = trk.GetStart()
        end = trk.GetEnd()
        tracks.append({
            "start": [pcbnew.ToMM(start.x), pcbnew.ToMM(start.y)],
            "end": [pcbnew.ToMM(end.x), pcbnew.ToMM(end.y)],
            "width": pcbnew.ToMM(trk.GetWidth()),
            "layer": board.GetLayerName(trk.GetLayer()),
            "net": trk.GetNetCode(),
        })
    return tracks


def _pcbnew_extract_vias(board) -> list[dict[str, Any]]:
    pcbnew = _get_pcbnew()
    vias = []
    for trk in board.GetTracks():
        if trk.GetClass() != "PCB_VIA":
            continue
        pos = trk.GetPosition()
        vias.append({
            "position": [pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)],
            "size": pcbnew.ToMM(trk.GetWidth()),
            "net": trk.GetNetCode(),
        })
    return vias


def _pcbnew_board_dimensions(board) -> dict[str, float]:
    pcbnew = _get_pcbnew()
    bbox = board.GetBoardEdgesBoundingBox()
    return {
        "width": round(pcbnew.ToMM(bbox.GetWidth()), 2),
        "height": round(pcbnew.ToMM(bbox.GetHeight()), 2),
    }


# ---------------------------------------------------------------------------
# S-expression fallback (no pcbnew needed)
# ---------------------------------------------------------------------------

from kicad_mcp.utils.sexpr_parser import find_node, find_nodes, parse_sexpr  # noqa: E402


def _parse_pcb(pcb_path: str) -> list:
    """Parse a .kicad_pcb file into S-expression tree."""
    with open(pcb_path, encoding="utf-8") as f:
        return parse_sexpr(f.read())


def _sexpr_extract_footprints(tree: list) -> list[dict[str, Any]]:
    """Extract footprint data from PCB tree."""
    footprints = []

    for fp in find_nodes(tree, "footprint"):
        if len(fp) < 2:
            continue

        fp_id = str(fp[1])

        at_node = find_node(fp, "at")
        x = float(at_node[1]) if at_node and len(at_node) > 1 else 0.0
        y = float(at_node[2]) if at_node and len(at_node) > 2 else 0.0
        rotation = float(at_node[3]) if at_node and len(at_node) > 3 else 0.0

        layer_node = find_node(fp, "layer")
        layer = str(layer_node[1]) if layer_node and len(layer_node) > 1 else ""

        reference = ""
        value = ""
        for prop in find_nodes(fp, "property"):
            if len(prop) >= 3:
                if str(prop[1]) == "Reference":
                    reference = str(prop[2])
                elif str(prop[1]) == "Value":
                    value = str(prop[2])

        if not reference:
            for txt in find_nodes(fp, "fp_text"):
                if len(txt) >= 3 and str(txt[1]) == "reference":
                    reference = str(txt[2])
                elif len(txt) >= 3 and str(txt[1]) == "value":
                    value = str(txt[2])

        pads = find_nodes(fp, "pad")
        pad_count = len(pads)

        footprints.append({
            "reference": reference,
            "value": value,
            "footprint_id": fp_id,
            "layer": layer,
            "position": [x, y],
            "rotation": rotation,
            "pad_count": pad_count,
        })

    return footprints


def _sexpr_extract_nets(tree: list) -> list[dict[str, Any]]:
    nets = []
    for net in find_nodes(tree, "net"):
        if len(net) >= 3:
            nets.append({
                "number": int(net[1]) if str(net[1]).isdigit() else 0,
                "name": str(net[2]),
            })
    return nets


def _sexpr_extract_tracks(tree: list) -> list[dict[str, Any]]:
    tracks = []
    for seg in find_nodes(tree, "segment"):
        start = find_node(seg, "start")
        end = find_node(seg, "end")
        width_node = find_node(seg, "width")
        layer_node = find_node(seg, "layer")
        net_node = find_node(seg, "net")

        tracks.append({
            "start": [float(start[1]), float(start[2])] if start and len(start) > 2 else [0, 0],
            "end": [float(end[1]), float(end[2])] if end and len(end) > 2 else [0, 0],
            "width": float(width_node[1]) if width_node and len(width_node) > 1 else 0,
            "layer": str(layer_node[1]) if layer_node and len(layer_node) > 1 else "",
            "net": int(net_node[1]) if net_node and len(net_node) > 1 and str(net_node[1]).isdigit() else 0,
        })
    return tracks


def _sexpr_extract_vias(tree: list) -> list[dict[str, Any]]:
    vias = []
    for via in find_nodes(tree, "via"):
        at_node = find_node(via, "at")
        size_node = find_node(via, "size")
        net_node = find_node(via, "net")
        vias.append({
            "position": [float(at_node[1]), float(at_node[2])] if at_node and len(at_node) > 2 else [0, 0],
            "size": float(size_node[1]) if size_node and len(size_node) > 1 else 0,
            "net": int(net_node[1]) if net_node and len(net_node) > 1 and str(net_node[1]).isdigit() else 0,
        })
    return vias


def _sexpr_board_dimensions(tree: list) -> dict[str, float]:
    min_x, min_y = float("inf"), float("inf")
    max_x, max_y = float("-inf"), float("-inf")

    for tag in ("gr_line", "gr_rect", "gr_arc"):
        for elem in find_nodes(tree, tag):
            layer_node = find_node(elem, "layer")
            if not layer_node or str(layer_node[1]) != "Edge.Cuts":
                continue

            start = find_node(elem, "start")
            end = find_node(elem, "end")

            for pt in (start, end):
                if pt and len(pt) > 2:
                    x, y = float(pt[1]), float(pt[2])
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)

    if min_x == float("inf"):
        return {"width": 0, "height": 0}

    return {"width": round(max_x - min_x, 2), "height": round(max_y - min_y, 2)}


# ---------------------------------------------------------------------------
# Unified extraction: pcbnew first, sexpr fallback
# ---------------------------------------------------------------------------

def _extract_all(pcb_path: str) -> dict[str, Any]:
    """Extract footprints, nets, tracks, vias, dimensions from a PCB file."""
    if _HAS_PCBNEW:
        try:
            board = _load_board(pcb_path)
            return {
                "footprints": _pcbnew_extract_footprints(board),
                "nets": _pcbnew_extract_nets(board),
                "tracks": _pcbnew_extract_tracks(board),
                "vias": _pcbnew_extract_vias(board),
                "dimensions": _pcbnew_board_dimensions(board),
                "zones": len(board.Zones()),
                "backend": "pcbnew",
            }
        except Exception as e:
            logger.warning("pcbnew extraction failed, falling back to sexpr: %s", e)

    # Fallback: S-expression parsing
    tree = _parse_pcb(pcb_path)
    return {
        "footprints": _sexpr_extract_footprints(tree),
        "nets": _sexpr_extract_nets(tree),
        "tracks": _sexpr_extract_tracks(tree),
        "vias": _sexpr_extract_vias(tree),
        "dimensions": _sexpr_board_dimensions(tree),
        "zones": len(find_nodes(tree, "zone")),
        "backend": "sexpr",
    }


def register_pcb_tools(mcp: FastMCP) -> None:
    """Register PCB analysis tools with the MCP server."""

    @mcp.tool()
    async def list_pcb_footprints(
        pcb_path: str,
        layer_filter: str = "",
        refs: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List every footprint in a ``.kicad_pcb`` with reference, value, layer, position, rotation, pad count.

        Use this whenever the user asks "what's on this board", "which
        components are on B.Cu", or you need a reference catalogue before a
        routing/patching operation. **Don't** ``Read`` the PCB file and
        regex over ``(footprint …)`` blocks — that misses nested ``at``
        coordinates, doesn't resolve flip math, and is wrong for any
        footprint placed on B.Cu (mirror semantics). This tool uses pcbnew
        when available (correct math) and falls back to S-expression
        parsing otherwise.

        Sibling tools: pad-level positions → ``ipc_get_pad_world_pos`` (live)
        or ``compute_pad_world_positions`` (disk); per-net tracks →
        ``find_tracks_by_net``; net summary → ``analyze_pcb_nets``.

        Args:
            pcb_path: ``.kicad_pcb`` file (WSL or Windows path).
            layer_filter: Optional layer name like ``"F.Cu"`` / ``"B.Cu"``.
            refs: Optional comma-separated references to limit to
                (e.g. ``"U1,R5"``) — scopes the result and its token size.

        Returns:
            ``{success, pcb_path, count, footprints: [{reference, value,
            footprint_id, layer, position, rotation, pad_count}, …], backend}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not Path(pcb_path).exists():
            return {"success": False, "error": f"PCB file not found: {pcb_path}"}

        try:
            data = _extract_all(pcb_path)
            footprints = data["footprints"]

            if layer_filter:
                footprints = [f for f in footprints if f["layer"] == layer_filter]

            ref_filter = {r.strip() for r in refs.split(",") if r.strip()}
            if ref_filter:
                footprints = [f for f in footprints
                              if f["reference"] in ref_filter]

            return {
                "success": True,
                "pcb_path": pcb_path,
                "count": len(footprints),
                "footprints": footprints,
                "backend": data["backend"],
            }
        except Exception as e:
            return {"success": False, "error": f"Error parsing PCB: {e}"}

    @mcp.tool()
    async def analyze_pcb_nets(
        pcb_path: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Summarize every net in a ``.kicad_pcb`` with track + via counts and board dimensions.

        Use this as the **first** read on an unfamiliar PCB — it tells you
        how many nets exist, which are routed (track/via > 0), which are
        still floating, plus the board's overall size. Don't open the file
        and grep ``(net …)`` lines: that gives you the net table but not
        the routing density per net, and ignores ``(via …)`` blocks
        entirely.

        For per-net detail (which segments belong to net X) follow up with
        ``find_tracks_by_net``. For component lists use
        ``list_pcb_footprints``.

        Args:
            pcb_path: ``.kicad_pcb`` file (WSL or Windows path).

        Returns:
            ``{success, pcb_path, board_dimensions, total_nets, total_tracks,
            total_vias, total_zones, nets: [{name, number, tracks, vias}, …],
            backend}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not Path(pcb_path).exists():
            return {"success": False, "error": f"PCB file not found: {pcb_path}"}

        try:
            data = _extract_all(pcb_path)
            nets = data["nets"]
            tracks = data["tracks"]
            vias = data["vias"]

            # Count tracks/vias per net
            net_track_counts: dict[int, int] = {}
            for t in tracks:
                net_track_counts[t["net"]] = net_track_counts.get(t["net"], 0) + 1

            net_via_counts: dict[int, int] = {}
            for v in vias:
                net_via_counts[v["net"]] = net_via_counts.get(v["net"], 0) + 1

            net_summary = []
            for n in nets:
                num = n["number"]
                if num == 0:
                    continue  # skip unconnected net
                net_summary.append({
                    "name": n["name"],
                    "number": num,
                    "tracks": net_track_counts.get(num, 0),
                    "vias": net_via_counts.get(num, 0),
                })

            return {
                "success": True,
                "pcb_path": pcb_path,
                "board_dimensions": data["dimensions"],
                "total_nets": len(nets) - 1,  # exclude net 0
                "total_tracks": len(tracks),
                "total_vias": len(vias),
                "total_zones": data["zones"],
                "nets": net_summary,
                "backend": data["backend"],
            }
        except Exception as e:
            return {"success": False, "error": f"Error parsing PCB: {e}"}

    @mcp.tool()
    async def find_tracks_by_net(
        pcb_path: str,
        net_name: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return every track segment + via on a specific net (geometry, layer, width, drill).

        Use this when the user asks "where does net X go", or to verify a
        routing pattern (e.g. Net-Tie + Stacked-Via). Don't iterate the PCB
        file looking for matching ``(net N)`` tags inside ``(segment …)``
        blocks — net-numbering can be sparse and via-blocks live separately
        from segment-blocks; this tool does the lookup-and-merge correctly.

        For a high-level net summary first, call ``analyze_pcb_nets``. For
        the *footprint*-side connections of a net (which pad on which
        component) use ``find_component_connections``.

        Args:
            pcb_path: ``.kicad_pcb`` file.
            net_name: Exact net name as written in KiCad (e.g. ``"GND"``,
                ``"/SEG4/CO_B"``). Hierarchical labels include the leading
                slash.

        Returns:
            ``{success, net_name, net_number, track_count, via_count,
            tracks: [{start, end, width, layer, net}, …],
            vias: [{position, size, net}, …], backend}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not Path(pcb_path).exists():
            return {"success": False, "error": f"PCB file not found: {pcb_path}"}

        try:
            data = _extract_all(pcb_path)
            nets = data["nets"]
            tracks = data["tracks"]
            vias = data["vias"]

            # Find net number by name
            net_num = None
            for n in nets:
                if n["name"] == net_name:
                    net_num = n["number"]
                    break

            if net_num is None:
                available = [n["name"] for n in nets if n["number"] != 0][:20]
                return {
                    "success": False,
                    "error": f"Net '{net_name}' not found",
                    "available_nets": available,
                }

            net_tracks = [t for t in tracks if t["net"] == net_num]
            net_vias = [v for v in vias if v["net"] == net_num]

            return {
                "success": True,
                "net_name": net_name,
                "net_number": net_num,
                "track_count": len(net_tracks),
                "via_count": len(net_vias),
                "tracks": net_tracks[:100],
                "vias": net_vias[:50],
                "backend": data["backend"],
            }
        except Exception as e:
            return {"success": False, "error": f"Error parsing PCB: {e}"}
