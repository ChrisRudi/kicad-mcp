# SPDX-License-Identifier: GPL-3.0-or-later
"""
CLI-based export tools for KiCad using kicad-cli.

Provides Gerber, STEP, PDF, SVG, Drill, Position, 3D Render, and Board Stats exports.
"""

import json
import os
from pathlib import Path
import subprocess
from typing import Any

from fastmcp import Context, FastMCP

from kicad_mcp.utils.kicad_cli import KiCadCLIError, get_kicad_cli_path
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.wsl_path import to_windows_path


def _pcb_has_aux_axis_origin(pcb_path: str) -> bool:
    """Return True when the PCB defines a **non-zero** ``aux_axis_origin``
    (a.k.a. drill/place file origin) in its ``(setup …)`` block.

    Fab houses (JLCPCB, PCBWay, …) expect Gerber + Drill + Pick&Place to
    all reference the same origin. When the user has bothered to set an
    aux origin in KiCad (Place → Drill/Place File Origin), we honour it
    in the exports below. When the origin is absent or zero (= still the
    page corner), we fall back to the page-origin default that
    ``kicad-cli`` produces without a flag — same behaviour as before the
    auto-detect.
    """
    try:
        with open(pcb_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    import re as _re
    m = _re.search(
        r"\(aux_axis_origin\s+([\d.\-]+)\s+([\d.\-]+)\s*\)", text,
    )
    if not m:
        return False
    try:
        x = float(m.group(1))
        y = float(m.group(2))
    except ValueError:
        return False
    return (x != 0.0) or (y != 0.0)


def _run_cli(args: list[str], timeout: int = 60) -> dict[str, Any]:
    """Run a kicad-cli command and return result.

    Args:
        args: Command arguments (after kicad-cli)
        timeout: Timeout in seconds

    Returns:
        Dict with success, stdout, stderr
    """
    try:
        cli_path = get_kicad_cli_path(required=True)
    except KiCadCLIError as e:
        return {"success": False, "error": str(e)}

    cmd = [cli_path] + args

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _ensure_cairo_dll_searchable() -> None:
    """Make KiCad's ``cairo-2.dll`` discoverable by cairocffi.

    cairocffi looks for libraries named ``libcairo-2.dll`` /
    ``libcairo.so.2`` / ``libcairo.2.dylib`` — it does NOT try the
    ``cairo-2.dll`` filename that KiCad ships. Workaround:

    1. Locate KiCad's ``cairo-2.dll`` (in ``bin/`` next to the CLI).
    2. Mirror it under ``~/.kicad-mcp/native_libs/libcairo-2.dll``.
    3. Register that directory via :func:`os.add_dll_directory` so the
       OS resolver finds it when cairocffi performs ``dlopen``.

    Idempotent — only copies if the mirror is missing or out of date.
    Safe no-op outside Windows.
    """
    if os.name != "nt":
        return
    add_dll = getattr(os, "add_dll_directory", None)
    if not callable(add_dll):
        return
    try:
        from kicad_mcp.utils.path_env import kicad_paths

        cli = kicad_paths().get("kicad_cli", "")
    except Exception:
        cli = ""
    bin_dir = os.path.dirname(cli) if cli else ""
    if not bin_dir or not os.path.isdir(bin_dir):
        return
    src = os.path.join(bin_dir, "cairo-2.dll")
    if not os.path.isfile(src):
        return
    mirror_dir = os.path.join(
        os.path.expanduser("~"), ".kicad-mcp", "native_libs"
    )
    os.makedirs(mirror_dir, exist_ok=True)
    dst = os.path.join(mirror_dir, "libcairo-2.dll")
    try:
        if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
            import shutil as _sh

            _sh.copy2(src, dst)
    except Exception:
        pass
    try:
        add_dll(mirror_dir)
    except Exception:
        pass
    try:
        # Also register KiCad's bin directory itself for future native deps.
        add_dll(bin_dir)
    except Exception:
        pass


def _ensure_cairosvg():
    """Import cairosvg, auto-installing it on first failure and registering
    KiCad's ``cairo-2.dll`` directory as a DLL-search location so cairocffi
    can find the native cairo library. Idempotent.
    """
    _ensure_cairo_dll_searchable()
    try:
        import cairosvg  # type: ignore
        return cairosvg
    except ImportError:
        pass
    import importlib
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "cairosvg"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "cairosvg auto-install failed. "
            f"pip stderr: {(proc.stderr or '').strip()[:300]}"
        )
    _ensure_cairo_dll_searchable()
    return importlib.import_module("cairosvg")


def _svg_to_png(svg_path: str, scale: float = 2.0) -> bytes:
    """Convert an SVG file to PNG bytes using cairosvg.

    Auto-installs cairosvg into the running interpreter on first call if
    the package is missing — no manual ``pip install`` step needed.
    """
    cairosvg = _ensure_cairosvg()
    try:
        return cairosvg.svg2png(url=svg_path, scale=scale)
    except OSError as e:
        if "cairo" in str(e).lower():
            raise RuntimeError(
                f"Cairo library not found: {e}. "
                f"Set KICAD_INSTALL_DIR to your KiCad installation."
            ) from e
        raise


def register_cli_export_tools(mcp: FastMCP) -> None:
    """Register CLI export tools with the MCP server."""

    @mcp.tool()
    async def export_gerbers(
        pcb_path: str,
        output_dir: str = "",
        use_drill_file_origin: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export production-ready Gerber files for every copper/silkscreen/mask layer of a PCB.

        Use this for any "give me the gerbers", "fab files", "send to JLCPCB"
        request. Don't shell out to ``kicad-cli pcb export gerbers``: this
        wrapper handles WSL→Windows path conversion (``kicad-cli.exe``
        rejects ``/mnt/c/...`` paths), creates the output directory, sets
        the trailing slash KiCad-CLI requires, and lists the produced files
        in the response.

        Origin handling (Footgun in ``CLAUDE.md`` §Coord-Systems #7): fab
        houses expect Gerber + Drill + POS to reference the **same**
        origin. By default this tool auto-detects a non-zero
        ``aux_axis_origin`` in the PCB and passes
        ``--use-drill-file-origin`` to ``kicad-cli`` accordingly — pair
        with ``export_drill`` and ``export_pos`` which apply the same
        auto-detect. Pass ``use_drill_file_origin=False`` only when you
        explicitly want page-origin Gerbers (rare).

        Pair with ``export_drill`` to get a complete fabrication package.

        Args:
            pcb_path: ``.kicad_pcb`` file (WSL or Windows path).
            output_dir: Optional output directory; defaults to a
                ``gerbers/`` folder next to the PCB.
            use_drill_file_origin: If True (default) and the PCB has a
                non-zero ``aux_axis_origin``, emit Gerbers referenced to
                it. Set to False to force page-origin.

        Returns:
            ``{success, output_dir, origin, files: [...]}`` where
            ``origin`` is ``"aux"`` or ``"page"`` to report which one
            was used.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.exists(pcb_path):
            return {"success": False, "error": f"PCB file not found: {pcb_path}"}

        output_dir = to_local_path(output_dir) if output_dir else ""
        if not output_dir:
            output_dir = os.path.join(os.path.dirname(pcb_path), "gerbers")
        os.makedirs(output_dir, exist_ok=True)

        if ctx:
            ctx.info(f"Exporting Gerbers from {os.path.basename(pcb_path)}")

        cmd = [
            "pcb", "export", "gerbers",
            "--output", to_windows_path(output_dir) + "\\",
        ]
        origin_used = "page"
        if use_drill_file_origin and _pcb_has_aux_axis_origin(pcb_path):
            cmd.append("--use-drill-file-origin")
            origin_used = "aux"
        cmd.append(to_windows_path(pcb_path))
        result = _run_cli(cmd)

        if result["success"]:
            files = [f for f in os.listdir(output_dir) if not f.startswith(".")]
            return {
                "success": True,
                "output_dir": output_dir,
                "origin": origin_used,
                "files": files,
            }

        return {"success": False, "error": result.get("stderr", result.get("error", "Unknown error"))}

    @mcp.tool()
    async def export_drill(
        pcb_path: str,
        output_dir: str = "",
        use_drill_file_origin: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export Excellon drill files (PTH + NPTH) from a PCB.

        Use this together with ``export_gerbers`` to make a complete fab
        package. Don't run ``kicad-cli`` manually — same path-conversion
        and output-directory plumbing as the other export tools.

        Origin handling: as in ``export_gerbers``, the tool auto-detects a
        non-zero ``aux_axis_origin`` in the PCB and passes
        ``--drill-origin plot`` to align the drill file with the aux-
        referenced Gerbers. Mismatched origins between Gerber and Drill
        is the Fab-house bug listed as Footgun #7 in ``CLAUDE.md`` —
        keeping the default ``True`` makes the bug impossible.

        Args:
            pcb_path: ``.kicad_pcb`` file.
            output_dir: Optional output directory; defaults to ``gerbers/``
                next to the PCB (same convention as ``export_gerbers``, so
                everything lands in one folder).
            use_drill_file_origin: If True (default) and the PCB has a
                non-zero ``aux_axis_origin``, emit Drill referenced to
                it. Set to False to force absolute (page) origin.

        Returns:
            ``{success, output_dir, origin, files: [...drl files]}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.exists(pcb_path):
            return {"success": False, "error": f"PCB file not found: {pcb_path}"}

        output_dir = to_local_path(output_dir) if output_dir else ""
        if not output_dir:
            output_dir = os.path.join(os.path.dirname(pcb_path), "gerbers")
        os.makedirs(output_dir, exist_ok=True)

        if ctx:
            ctx.info(f"Exporting drill files from {os.path.basename(pcb_path)}")

        cmd = [
            "pcb", "export", "drill",
            "--output", to_windows_path(output_dir) + "\\",
        ]
        origin_used = "page"
        if use_drill_file_origin and _pcb_has_aux_axis_origin(pcb_path):
            cmd.extend(["--drill-origin", "plot"])
            origin_used = "aux"
        cmd.append(to_windows_path(pcb_path))
        result = _run_cli(cmd)

        if result["success"]:
            files = [f for f in os.listdir(output_dir) if f.endswith((".drl", ".DRL"))]
            return {
                "success": True,
                "output_dir": output_dir,
                "origin": origin_used,
                "files": files,
            }

        return {"success": False, "error": result.get("stderr", result.get("error", "Unknown error"))}

    @mcp.tool()
    async def export_step(
        pcb_path: str,
        output_path: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export a STEP 3D model of the assembled board (board outline + 3D-mapped footprints).

        Use this when the user wants the 3D-CAD model for mechanical
        review, enclosure design, or to embed in a manufacturing report.
        Don't shell out — STEP export is slow (KiCad walks every footprint
        for its 3D mapping), so this tool uses a 120 s timeout and reports
        success even if KiCad's exit-code is non-zero but the file landed
        on disk (a common kicad-cli quirk).

        For a fast 2D preview use ``render_3d`` (raytraced PNG) or
        ``generate_pcb_thumbnail`` (SVG-based PNG).

        Args:
            pcb_path: ``.kicad_pcb`` file.
            output_path: Optional output ``.step`` path (default: PCB-stem).step).

        Returns:
            ``{success, output_path}`` or ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.exists(pcb_path):
            return {"success": False, "error": f"PCB file not found: {pcb_path}"}

        output_path = to_local_path(output_path) if output_path else ""
        if not output_path:
            output_path = os.path.splitext(pcb_path)[0] + ".step"

        if ctx:
            ctx.info(f"Exporting STEP from {os.path.basename(pcb_path)}")

        result = _run_cli([
            "pcb", "export", "step",
            "--output", to_windows_path(output_path),
            to_windows_path(pcb_path),
        ], timeout=120)

        if result["success"] or os.path.exists(output_path):
            return {"success": True, "output_path": output_path}

        return {"success": False, "error": result.get("stderr", result.get("error", "Unknown error"))}

    @mcp.tool()
    async def export_pdf(
        file_path: str,
        output_path: str = "",
        file_type: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export a print-ready PDF of either a schematic or a PCB.

        Use this when the user wants a viewable / printable document. The
        same tool handles both ``.kicad_sch`` and ``.kicad_pcb`` —
        auto-detect uses the file extension; pass ``file_type`` only when
        the extension is missing.

        For an editable vector format use ``export_svg`` instead. For a
        rasterised preview use ``export_png`` (which goes via SVG and
        requires cairosvg — auto-installed lazily).

        Args:
            file_path: ``.kicad_sch`` or ``.kicad_pcb``.
            output_path: Optional output ``.pdf`` path.
            file_type: ``"sch"`` / ``"pcb"`` — only needed if extension
                is non-standard.

        Returns:
            ``{success, output_path}`` or ``{success: False, error}``.
        """
        file_path = to_local_path(file_path)
        if not os.path.exists(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}

        if not file_type:
            if file_path.endswith(".kicad_sch"):
                file_type = "sch"
            elif file_path.endswith(".kicad_pcb"):
                file_type = "pcb"
            else:
                return {"success": False, "error": "Cannot determine file type. Use file_type='sch' or 'pcb'."}

        output_path = to_local_path(output_path) if output_path else ""
        if not output_path:
            output_path = os.path.splitext(file_path)[0] + ".pdf"

        if ctx:
            ctx.info(f"Exporting PDF from {os.path.basename(file_path)}")

        result = _run_cli([
            file_type, "export", "pdf",
            "--output", to_windows_path(output_path),
            to_windows_path(file_path),
        ])

        if result["success"] or os.path.exists(output_path):
            return {"success": True, "output_path": output_path}

        return {"success": False, "error": result.get("stderr", result.get("error", "Unknown error"))}

    @mcp.tool()
    async def export_svg(
        file_path: str,
        output_path: str = "",
        file_type: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export an editable SVG of either a schematic or a PCB (visible-copper layers + silkscreen + mask + edge).

        Use this when the user wants a vector image they can edit in
        Inkscape, embed in docs, or post-process. Don't call ``kicad-cli``
        — this wrapper smooths over two CLI quirks:

        * for ``sch``, ``--output`` is a *directory*, and KiCad names the
          file ``<source_stem>.svg`` inside; we pass the parent and then
          rename;
        * for ``pcb``, ``--output`` is a *file* but ``--layers`` is
          **mandatory** — we pass a sensible default
          (``F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts``).

        For a rasterised result use ``export_png``.

        Args:
            file_path: ``.kicad_sch`` or ``.kicad_pcb``.
            output_path: Optional output ``.svg`` path.
            file_type: Optional override (``"sch"`` / ``"pcb"``).

        Returns:
            ``{success, output_path}``.
        """
        file_path = to_local_path(file_path)
        if not os.path.exists(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}

        if not file_type:
            if file_path.endswith(".kicad_sch"):
                file_type = "sch"
            elif file_path.endswith(".kicad_pcb"):
                file_type = "pcb"
            else:
                return {"success": False, "error": "Cannot determine file type. Use file_type='sch' or 'pcb'."}

        output_path = to_local_path(output_path) if output_path else ""
        if not output_path:
            output_path = os.path.splitext(file_path)[0] + ".svg"

        if ctx:
            ctx.info(f"Exporting SVG from {os.path.basename(file_path)}")

        # kicad-cli quirks:
        # * SCH:  --output points to a *directory*; the file lands as
        #   <dir>/<source_stem>.svg. Pass parent dir, then rename.
        # * PCB:  --output is a *file path* but --layers is mandatory.
        if file_type == "sch":
            output_dir = os.path.dirname(os.path.abspath(output_path)) or "."
            os.makedirs(output_dir, exist_ok=True)
            cli_args = [
                file_type, "export", "svg",
                "--output", to_windows_path(output_dir),
                to_windows_path(file_path),
            ]
        else:  # pcb
            output_dir = os.path.dirname(os.path.abspath(output_path)) or "."
            os.makedirs(output_dir, exist_ok=True)
            cli_args = [
                file_type, "export", "svg",
                "--layers", "F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts",
                "--output", to_windows_path(output_path),
                to_windows_path(file_path),
            ]

        result = _run_cli(cli_args)

        # Locate the produced file. For SCH, kicad-cli wrote
        # <output_dir>/<stem>.svg — move it to the requested output_path
        # if those differ.
        if file_type == "sch":
            produced = os.path.join(output_dir, Path(file_path).stem + ".svg")
            if os.path.isfile(produced) and os.path.abspath(produced) != os.path.abspath(output_path):
                try:
                    os.replace(produced, output_path)
                except OSError:
                    pass
            actual_output_path = output_path if os.path.isfile(output_path) else produced
        else:
            actual_output_path = output_path

        if result["success"] or os.path.exists(actual_output_path):
            return {"success": True, "output_path": actual_output_path}

        return {"success": False, "error": result.get("stderr", result.get("error", "Unknown error"))}

    @mcp.tool()
    async def export_png(
        file_path: str,
        output_path: str = "",
        scale: float = 2.0,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export a PNG image from a KiCad schematic (.kicad_sch) or PCB (.kicad_pcb).

        USE THIS TOOL when the user asks for a PNG, image, screenshot, or picture
        of a schematic or PCB. This is the PRIMARY image export tool. Do NOT use
        export_svg and convert manually — this tool handles everything internally.

        Args:
            file_path: Path to .kicad_sch or .kicad_pcb file
            output_path: Output .png file path (default: same name with .png)
            scale: PNG scale factor (default: 2.0 for high-res)
            ctx: MCP context

        Returns:
            Export result with output file path
        """
        file_path = to_local_path(file_path)
        if not os.path.exists(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}

        output_path = to_local_path(output_path) if output_path else ""
        if not output_path:
            output_path = os.path.splitext(file_path)[0] + ".png"

        # Step 1: Export SVG via kicad-cli (internal, not user-visible)
        svg_request_path = os.path.splitext(file_path)[0] + "_tmp_export.svg"
        svg_result = await export_svg(file_path, svg_request_path, ctx=None)
        if not svg_result.get("success"):
            return {"success": False, "error": f"SVG export failed: {svg_result.get('error', 'unknown')}"}

        svg_path = svg_result["output_path"]

        # Step 2: Convert SVG → PNG
        try:
            png_data = _svg_to_png(svg_path, scale)
            with open(output_path, "wb") as f:
                f.write(png_data)
        except Exception as e:
            return {"success": False, "error": f"PNG conversion failed: {e}"}
        finally:
            if os.path.isfile(svg_path):
                os.remove(svg_path)
            svg_dir = Path(svg_request_path)
            if svg_dir.is_dir():
                import contextlib
                with contextlib.suppress(OSError):
                    svg_dir.rmdir()

        if os.path.exists(output_path):
            if ctx:
                ctx.info(f"PNG exported: {os.path.basename(output_path)}")
            return {"success": True, "output_path": output_path}

        return {"success": False, "error": "PNG file was not created"}

    @mcp.tool()
    async def export_pos(
        pcb_path: str,
        output_path: str = "",
        units: str = "mm",
        use_drill_file_origin: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export the component position (pick & place) file for assembly houses.

        Use this when the user needs the centroid coordinates + rotation
        of every SMD component for a fab/assembly order (JLCPCB, PCBWay,
        in-house pick-and-place). Don't shell out — same path conversion
        as the other exports.

        Two CLI defaults are overridden to match standard fab practice
        (see ``CLAUDE.md`` §Coord-Systems Footgun #7):

        * **Units default to mm** (KiCad-CLI's bare default is inch).
          Almost every modern assembly house wants mm POS; pass
          ``units="in"`` only if your fab insists on imperial.
        * **Aux-origin auto-detected** to match the companion Gerber and
          Drill exports.

        For just the BOM (without coordinates) use ``export_bom_csv``.

        Args:
            pcb_path: ``.kicad_pcb`` file.
            output_path: Optional output ``.pos`` path.
            units: ``"mm"`` (default) or ``"in"``.
            use_drill_file_origin: If True (default) and the PCB has a
                non-zero ``aux_axis_origin``, reference centroids to it.

        Returns:
            ``{success, output_path, origin, units}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.exists(pcb_path):
            return {"success": False, "error": f"PCB file not found: {pcb_path}"}
        if units not in ("mm", "in"):
            return {
                "success": False,
                "error": f"units must be 'mm' or 'in', got {units!r}",
            }

        output_path = to_local_path(output_path) if output_path else ""
        if not output_path:
            output_path = os.path.splitext(pcb_path)[0] + ".pos"

        if ctx:
            ctx.info(f"Exporting position file from {os.path.basename(pcb_path)}")

        cmd = [
            "pcb", "export", "pos",
            "--output", to_windows_path(output_path),
            "--units", units,
        ]
        origin_used = "page"
        if use_drill_file_origin and _pcb_has_aux_axis_origin(pcb_path):
            cmd.append("--use-drill-file-origin")
            origin_used = "aux"
        cmd.append(to_windows_path(pcb_path))
        result = _run_cli(cmd)

        if result["success"] or os.path.exists(output_path):
            return {
                "success": True,
                "output_path": output_path,
                "origin": origin_used,
                "units": units,
            }

        return {"success": False, "error": result.get("stderr", result.get("error", "Unknown error"))}

    @mcp.tool()
    async def render_3d(
        pcb_path: str,
        output_path: str = "",
        side: str = "top",
        width: int = 1600,
        height: int = 900,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Raytraced 3D PNG of the assembled board from a chosen view side.

        Use this when the user wants a "looks like the real thing" image of
        the populated board (for pitches, datasheets, packaging). Don't
        shell out — KiCad's render command takes specific flags
        (``--side``, ``--width``, ``--height``); this wrapper handles them.

        For a flat 2D outline preview, ``generate_pcb_thumbnail`` is faster
        (no raytracing). For a fab-grade 3D model, use ``export_step``.

        Args:
            pcb_path: ``.kicad_pcb`` file.
            output_path: Optional output ``.png`` path.
            side: ``"top"`` / ``"bottom"`` / ``"front"`` / ``"back"`` /
                ``"left"`` / ``"right"``.
            width, height: PNG resolution in pixels.

        Returns:
            ``{success, output_path}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.exists(pcb_path):
            return {"success": False, "error": f"PCB file not found: {pcb_path}"}

        output_path = to_local_path(output_path) if output_path else ""
        if not output_path:
            output_path = os.path.splitext(pcb_path)[0] + f"_3d_{side}.png"

        if ctx:
            ctx.info(f"Rendering 3D view ({side}) of {os.path.basename(pcb_path)}")

        result = _run_cli([
            "pcb", "render",
            "--output", to_windows_path(output_path),
            "--side", side,
            "--width", str(width),
            "--height", str(height),
            to_windows_path(pcb_path),
        ], timeout=120)

        if result["success"] or os.path.exists(output_path):
            return {"success": True, "output_path": output_path}

        return {"success": False, "error": result.get("stderr", result.get("error", "Unknown error"))}

    @mcp.tool()
    async def get_board_stats(
        pcb_path: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Comprehensive board metadata: dimensions, copper area, pad/via counts, drill table, component density.

        Use this as the **first** read on an unfamiliar PCB to know what
        you're looking at — it's a one-call summary that ``analyze_pcb_nets``
        + ``list_pcb_footprints`` would only partially recover. Don't shell
        out ``kicad-cli pcb export stats`` yourself: the CLI writes to a
        temp file rather than stdout, this tool handles the temp-file
        plumbing and returns parsed JSON.

        Args:
            pcb_path: ``.kicad_pcb`` file.

        Returns:
            ``{success, pcb_path, stats: {metadata, board, pads, vias,
            components, drill_holes}}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.exists(pcb_path):
            return {"success": False, "error": f"PCB file not found: {pcb_path}"}

        if ctx:
            ctx.info(f"Getting board stats for {os.path.basename(pcb_path)}")

        # kicad-cli pcb export stats --format json outputs to stdout
        try:
            cli_path = get_kicad_cli_path(required=True)
        except KiCadCLIError as e:
            return {"success": False, "error": str(e)}

        try:
            # kicad-cli writes stats to a file, not stdout — use temp file
            stats_path = os.path.splitext(pcb_path)[0] + "_statistics.json"
            result = subprocess.run(
                [cli_path, "pcb", "export", "stats", "--format", "json",
                 "--output", to_windows_path(stats_path),
                 to_windows_path(pcb_path)],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if os.path.exists(stats_path):
                with open(stats_path, encoding="utf-8") as f:
                    stats = json.load(f)
                os.unlink(stats_path)
                return {"success": True, "pcb_path": pcb_path, "stats": stats}

            return {
                "success": False,
                "error": f"Stats file not created. stderr: {result.stderr}",
            }

        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON from kicad-cli: {e}"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Stats command timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
