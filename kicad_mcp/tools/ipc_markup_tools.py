# SPDX-License-Identifier: GPL-3.0-or-later
"""Markup-layer → copper pipeline (live IPC).

The user sketches routing intent as plain graphic lines/arcs on a markup layer
(default ``User.9``) in the PCB editor; this reads that geometry over IPC and
lays equivalent copper **tracks** onto a target copper layer in one undoable
step. Closed polygons / circles are intentionally out of scope here (they map
to zones — a separate tool); this stage handles open paths only.

Coordinates stay in KiCad's internal nanometre integers end to end; the only
unit conversion is the user-facing ``width_mm`` at the input boundary. The
whole create is wrapped in one ``begin_commit``/``push_commit`` so it is a
single Ctrl+Z. Source and target layers are fully parameterised — nothing is
hard-coded beyond the ``User.9`` default.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("kicad_mcp.ipc_markup_tools")

DEFAULT_SOURCE_LAYER = "User.9"
DEFAULT_TARGET_LAYER = "F.Cu"
DEFAULT_WIDTH_MM = 0.25


def _mm_to_nm(mm: float) -> int:
    """User-facing mm → internal nm int (the one input-boundary conversion)."""
    return int(round(float(mm) * 1_000_000))


def register_ipc_markup_tools(mcp: FastMCP) -> None:
    """Register the markup→copper IPC tools on the MCP server."""

    @mcp.tool()
    def ipc_markup_to_tracks(
        source_layer: str = DEFAULT_SOURCE_LAYER,
        target_layer: str = DEFAULT_TARGET_LAYER,
        width_mm: float = DEFAULT_WIDTH_MM,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Convert markup graphics on a source layer into copper tracks.

        Reads every graphic line and arc the user drew on ``source_layer``
        (default User.9) of the OPEN board and lays an equivalent copper Track
        / ArcTrack on ``target_layer`` with the given width. The tracks are
        created net-less (assign a net later in KiCad). Closed polygons and
        circles are skipped here — they belong to a zones step. The whole
        batch is one undo step.

        Use this when the user has sketched routing as plain lines/arcs on a
        markup layer and says "mach daraus Leiterbahnen" / "auf F.Cu legen".
        Needs a .kicad_pcb open in the PCB editor with the IPC API enabled.

        Args:
            source_layer: Markup layer name to read (e.g. "User.9", "User.1").
            target_layer: Destination copper layer name (e.g. "F.Cu", "In1.Cu").
            width_mm: Track width in millimetres (converted to nm internally).
            dry_run: If True, only count what WOULD be created; no board change.

        Returns:
            A dict ``{success, created, by_type, skipped, source_layer,
            target_layer, width_mm, dry_run}`` — ``created`` is the number of
            tracks made, ``by_type`` splits segments vs arcs, ``skipped`` counts
            unsupported shapes (polygons/circles). On error
            ``{success: False, error: "<text>"}``.
        """
        from kicad_mcp.tools.ipc_tools import _connect_kicad, _layer_to_enum
        from kicad_mcp.utils import ipc_session

        src_enum = _layer_to_enum(source_layer)
        if src_enum is None:
            return {"success": False,
                    "error": f"Unknown source layer '{source_layer}'."}
        dst_enum = _layer_to_enum(target_layer)
        if dst_enum is None:
            return {"success": False,
                    "error": f"Unknown target layer '{target_layer}'."}
        if width_mm <= 0:
            return {"success": False,
                    "error": f"width_mm must be > 0 (got {width_mm})."}

        try:
            _client, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            from kipy.board_types import ArcTrack, Track  # type: ignore
        except Exception as exc:  # pragma: no cover - kipy always present live
            return {"success": False, "error": f"kipy import failed: {exc}"}

        try:
            shapes = ipc_session.call_with_retry(
                board.get_shapes, "get_shapes")
        except Exception as exc:
            return {"success": False, "error": f"get_shapes failed: {exc}"}

        width_nm = _mm_to_nm(width_mm)
        on_src = [s for s in shapes
                  if int(getattr(s, "layer", -1)) == int(src_enum)]

        new_items: list = []
        n_seg = n_arc = skipped = 0
        for shape in on_src:
            kind = type(shape).__name__
            if kind == "BoardSegment":
                track = Track()
                track.start = shape.start  # already nm Vector2 — no conversion
                track.end = shape.end
                track.width = width_nm
                track.layer = dst_enum
                new_items.append(track)
                n_seg += 1
            elif kind == "BoardArc":
                arc = ArcTrack()
                arc.start = shape.start
                arc.mid = shape.mid
                arc.end = shape.end
                arc.width = width_nm
                arc.layer = dst_enum
                new_items.append(arc)
                n_arc += 1
            else:
                skipped += 1  # polygons / circles → zones step (out of scope)

        result = {
            "success": True,
            "created": len(new_items),
            "by_type": {"segments": n_seg, "arcs": n_arc},
            "skipped": skipped,
            "source_layer": source_layer,
            "target_layer": target_layer,
            "width_mm": width_mm,
            "dry_run": dry_run,
        }
        if dry_run or not new_items:
            if not new_items:
                result["note"] = (
                    f"Keine Linien/Arcs auf '{source_layer}' gefunden "
                    f"(uebersprungen: {skipped})."
                )
            return result

        try:
            commit = board.begin_commit()
            board.create_items(new_items)
            board.push_commit(commit, "kicad-mcp ipc_markup_to_tracks")
        except Exception as exc:
            try:
                board.drop_commit(commit)
            except Exception:
                pass
            return {"success": False, "error": f"create failed: {exc}"}

        log.info("markup→tracks: %d created (%d seg, %d arc) %s→%s @%.3fmm",
                 len(new_items), n_seg, n_arc, source_layer, target_layer,
                 width_mm)
        return result
