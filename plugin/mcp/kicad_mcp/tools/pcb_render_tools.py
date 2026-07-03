# SPDX-License-Identifier: GPL-3.0-or-later
"""
Cropped-region PCB rendering → PNG, so an agent can *see* a layout area
(like a layouter does) instead of reasoning blind from coordinates.

Pipeline: ``kicad-cli pcb export svg`` (vector, whole board, fast, cached
by path+mtime+layers) → set the SVG ``viewBox`` to the requested region →
rasterise that region at high DPI with cairosvg. This renders ONLY the
crop sharply (not the whole board down-scaled then cut).

The board-coordinate → SVG-coordinate offset is the Edge.Cuts geometry
bounding box (page-size-mode 2 fits the SVG to exactly that), parsed from
the file — no pcbnew needed here.
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.kicad_cli import get_kicad_cli_path
from kicad_mcp.utils.path_env import to_local_path

_DEFAULT_LAYERS = "F.Cu,In1.Cu,In2.Cu,B.Cu,Edge.Cuts,F.SilkS,F.Fab"
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "kicad_mcp_render")


def _edge_bbox(pcb_text: str):
    """(minx, miny, maxx, maxy) in mm from Edge.Cuts geometry."""
    xs, ys = [], []
    # gr_circle / gr_arc / gr_line / gr_rect on Edge.Cuts: collect their pts
    for m in re.finditer(r'\(gr_(circle|arc|line|rect|poly)\b(.*?)\)\s*(?=\(gr_|\(footprint|\Z)',
                         pcb_text, re.S):
        body = m.group(2)
        if '"Edge.Cuts"' not in body:
            continue
        pts = [(float(a), float(b)) for a, b in
               re.findall(r'\((?:center|start|end|mid|xy)\s+([\-\d.]+)\s+([\-\d.]+)\)', body)]
        if m.group(1) == "circle" and len(pts) >= 2:
            cx, cy = pts[0]
            r = ((pts[1][0]-cx)**2 + (pts[1][1]-cy)**2) ** 0.5
            xs += [cx-r, cx+r]
            ys += [cy-r, cy+r]
        else:
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _ensure_svg(pcb_path: str, layers: str) -> tuple[str, tuple]:
    """Export (and cache by path+mtime+layers) the whole-board SVG.
    Returns (svg_path, edge_bbox)."""
    with open(pcb_path, encoding="utf-8") as _fh:
        text = _fh.read()
    bbox = _edge_bbox(text)
    if bbox is None:
        raise ValueError("could not determine Edge.Cuts bounding box")
    os.makedirs(_CACHE_DIR, exist_ok=True)
    key = hashlib.md5(f"{os.path.abspath(pcb_path)}|{os.stat(pcb_path).st_mtime_ns}|{layers}"
                      .encode()).hexdigest()
    svg_path = os.path.join(_CACHE_DIR, key + ".svg")
    if not os.path.isfile(svg_path):
        cli = get_kicad_cli_path()
        subprocess.run([cli, "pcb", "export", "svg", "--layers", layers,
                        "--page-size-mode", "2", "-o", svg_path, pcb_path],
                       capture_output=True, text=True, timeout=120, check=False)
        if not os.path.isfile(svg_path):
            raise RuntimeError("kicad-cli did not produce an SVG")
    return svg_path, bbox


def _load_cairosvg():
    """cairosvg needs cairo's native DLLs; KiCad ships them in its bin dir
    (alongside python.exe). Put that dir on PATH so cairocffi's
    find_library resolves cairo-2.dll + its dependencies."""
    kbin = os.path.dirname(sys.executable)
    if kbin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = kbin + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(kbin)
    except (AttributeError, OSError):
        pass
    import cairosvg
    return cairosvg


def _render(pcb_path: str, cx: float, cy: float, window_mm: float,
            px: int, layers: str, out_path: str) -> dict:
    svg_path, (minx, miny, _, _) = _ensure_svg(pcb_path, layers)
    cairosvg = _load_cairosvg()
    with open(svg_path, encoding="utf-8") as _fh:
        svg = _fh.read()
    vx, vy = cx - minx - window_mm / 2.0, cy - miny - window_mm / 2.0
    svg = re.sub(r'width="[^"]*mm" height="[^"]*mm" viewBox="[^"]*"',
                 f'width="{px}" height="{px}" '
                 f'viewBox="{vx:.4f} {vy:.4f} {window_mm:.4f} {window_mm:.4f}"',
                 svg, count=1)
    cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=out_path,
                     background_color="white")
    return {"png_path": out_path, "size_bytes": os.path.getsize(out_path),
            "window_mm": window_mm, "px": px,
            "region_board_mm": {"x": [round(cx-window_mm/2, 3), round(cx+window_mm/2, 3)],
                                "y": [round(cy-window_mm/2, 3), round(cy+window_mm/2, 3)]}}


def register_pcb_render_tools(mcp: FastMCP) -> None:
    """Register the cropped-region renderer."""

    @mcp.tool()
    def pcb_render(pcb_path: str, center_x_mm: float, center_y_mm: float,
                   window_mm: float = 10.0, px: int = 900,
                   layers: str = _DEFAULT_LAYERS, out_path: str = "") -> dict[str, Any]:
        """Render a cropped square region of a PCB to a PNG you can view.

        Use this to *see* a layout area instead of reasoning blind from
        coordinates — e.g. to judge an escape/stub angle, clearances, or
        how routing fans around a pad. After the call, open ``png_path``
        with the image-reading tool to look at it. Renders only the crop
        sharply (vector SVG → region viewBox → rasterise), not the whole
        board down-scaled.

        Colours follow the KiCad theme: F.Cu copper, the inner layers, the
        GND pour, silkscreen and fab outlines/refs all appear. For a tight
        look use a small ``window_mm`` (e.g. 4–6). The SVG is cached per
        (file, mtime, layers) so repeated renders of the same board are
        fast.

        Args:
            pcb_path: Path to the ``.kicad_pcb`` (WSL or Windows form).
            center_x_mm: Board-coordinate X centre of the crop, in mm.
            center_y_mm: Board-coordinate Y centre of the crop, in mm.
            window_mm: Side length of the square crop in mm. Default 10.
            px: Output pixel size (square). Default 900 (≈ px/window mm res).
            layers: Comma-separated KiCad layer names to include. Default
                F.Cu,In1.Cu,In2.Cu,B.Cu,Edge.Cuts,F.SilkS,F.Fab.
            out_path: Where to write the PNG. Default: a temp file.

        Returns:
            ``{success, png_path, size_bytes, window_mm, px,
            region_board_mm}`` — or ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if not out_path:
            out_path = os.path.join(_CACHE_DIR, "render.png")
            os.makedirs(_CACHE_DIR, exist_ok=True)
        else:
            out_path = to_local_path(out_path)
        try:
            res = _render(pcb_path, float(center_x_mm), float(center_y_mm),
                          float(window_mm), int(px), layers, out_path)
        except Exception as e:
            return {"success": False, "error": f"render failed: {e}"}
        return {"success": True, **res}
