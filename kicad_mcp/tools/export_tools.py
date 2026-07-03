# SPDX-License-Identifier: GPL-3.0-or-later
"""
Export tools for KiCad projects.
"""
import asyncio
import logging
import os
import subprocess

from fastmcp import Context, FastMCP
from fastmcp.utilities.types import Image

from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.kicad_cli import find_kicad_cli
from kicad_mcp.utils.path_env import to_local_path

logger = logging.getLogger(__name__)

def register_export_tools(mcp: FastMCP) -> None:
    """Register export tools with the MCP server.

    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    async def generate_pcb_thumbnail(project_path: str, ctx: Context | None = None):
        """Quick PCB thumbnail (SVG via ``kicad-cli`` → PNG via cairosvg). Cached per ``mtime``.

        Use this when the user wants a *fast* visual preview of the board
        — UI placement, README image, dashboard tile. Don't bake a custom
        rendering pipeline: this tool resolves the PCB from a project,
        caches the result keyed on file mtime (subsequent calls are
        instant), and lazy-installs cairosvg on first use.

        For a photorealistic 3D render use ``render_3d``; for a 3D model
        use ``export_step``.

        Args:
            project_path: Path to ``.kicad_pro``.

        Returns:
            ``mcp.Image`` (PNG bytes) or ``None`` on failure.
        """
        try:
            # Access the context (with null check)
            app_context = None
            if ctx:
                app_context = ctx.request_context.lifespan_context

            project_path = to_local_path(project_path)
            logger.info(f"Generating thumbnail via CLI for project: {project_path}")

            if not os.path.exists(project_path):
                logger.warning(f"Project not found: {project_path}")
                if ctx:
                    await ctx.info(f"Project not found: {project_path}")
                return None

            # Get PCB file from project
            files = get_project_files(project_path)
            if "pcb" not in files:
                logger.warning("PCB file not found in project")
                if ctx:
                    await ctx.info("PCB file not found in project")
                return None

            pcb_file = files["pcb"]
            logger.info(f"Found PCB file: {pcb_file}")

            # Check cache
            cache_key = f"thumbnail_cli_{pcb_file}_{os.path.getmtime(pcb_file)}"
            if app_context and hasattr(app_context, 'cache') and cache_key in app_context.cache:
                logger.debug(f"Using cached CLI thumbnail for {pcb_file}")
                return app_context.cache[cache_key]

            if ctx:
                await ctx.report_progress(10, 100)
                await ctx.info(f"Generating thumbnail for {os.path.basename(pcb_file)} using kicad-cli")

            # Use command-line tools
            try:
                thumbnail = await generate_thumbnail_with_cli(pcb_file, ctx)
                if thumbnail:
                    # Cache the result if possible
                    if app_context and hasattr(app_context, 'cache'):
                        app_context.cache[cache_key] = thumbnail
                    logger.info("Thumbnail generated successfully via CLI.")
                    return thumbnail
                else:
                    logger.warning("generate_thumbnail_with_cli returned None")
                    if ctx:
                        await ctx.info("Failed to generate thumbnail using kicad-cli.")
                    return None
            except Exception as e:
                logger.error(f"Error calling generate_thumbnail_with_cli: {str(e)}", exc_info=True)
                if ctx:
                    await ctx.info(f"Error generating thumbnail with kicad-cli: {str(e)}")
                return None

        except asyncio.CancelledError:
            logger.info("Thumbnail generation cancelled")
            raise  # Re-raise to let MCP know the task was cancelled
        except Exception as e:
            logger.error(f"Unexpected error in thumbnail generation: {str(e)}")
            if ctx:
                await ctx.info(f"Error: {str(e)}")
            return None

    @mcp.tool()
    async def generate_project_thumbnail(project_path: str, ctx: Context | None = None):
        """Render a PCB thumbnail for a KiCad project — alias for ``generate_pcb_thumbnail``.

        Pass either a ``.kicad_pro`` or a ``.kicad_pcb`` path; the tool
        resolves the matching board and produces an Image object suitable
        for the MCP client to display inline. Use this for a quick visual
        preview before opening the project in KiCad.

        Don't render via ``export_svg`` + manual rasterisation — this tool
        already runs the cairo-bootstrapped SVG→PNG pipeline.

        Args:
            project_path: Path to ``.kicad_pro`` or ``.kicad_pcb``.

        Returns:
            ``Image`` object (PNG) or ``None`` on failure.
        """
        logger.info(f"generate_project_thumbnail called, redirecting to generate_pcb_thumbnail for {project_path}")
        return await generate_pcb_thumbnail(project_path, ctx)

# Helper functions for thumbnail generation
async def generate_thumbnail_with_cli(pcb_file: str, ctx: Context | None = None):
    """Generate PCB thumbnail using command line tools.

    Args:
        pcb_file: Path to the PCB file (.kicad_pcb)
        ctx: MCP context for progress reporting

    Returns:
        Image object containing the PCB thumbnail or None if generation failed
    """
    try:
        logger.info("Attempting to generate thumbnail using KiCad CLI tools")
        if ctx:
            await ctx.report_progress(20, 100)

        # --- Determine Output Path ---
        project_dir = os.path.dirname(pcb_file)
        project_name = os.path.splitext(os.path.basename(pcb_file))[0]
        output_file = os.path.join(project_dir, f"{project_name}_thumbnail.svg")
        # ---------------------------

        # Use centralized CLI detection
        kicad_cli = find_kicad_cli()
        if not kicad_cli:
            logger.warning("kicad-cli not found")
            return None

        if ctx:
            await ctx.report_progress(30, 100)
            await ctx.info("Using KiCad command line tools for thumbnail generation")

        # Build command for generating SVG from PCB using kicad-cli
        cmd = [
            kicad_cli,
            "pcb",
            "export",
            "svg",
            "--output", output_file,
            "--layers", "F.Cu,B.Cu,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts",
            pcb_file
        ]

        logger.info(f"Running command: {' '.join(cmd)}")
        if ctx:
            await ctx.report_progress(50, 100)

        # Run the command
        try:
            process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
            logger.debug(f"Command successful: {process.stdout}")

            if ctx:
                await ctx.report_progress(70, 100)

            # Check if the output file was created
            if not os.path.exists(output_file):
                logger.warning(f"Output file not created: {output_file}")
                return None

            # Convert SVG to PNG (SVGs from KiCad are too large for MCP transport)
            png_file = output_file.replace('.svg', '.png')
            try:
                from kicad_mcp.utils.svg_render import svg_to_png
                png_data = svg_to_png(output_file, scale=2.0)
                with open(png_file, 'wb') as pf:
                    pf.write(png_data)
                logger.info(f"Converted SVG to PNG: {png_file}")
            except Exception as conv_err:
                logger.error(f"SVG to PNG conversion failed: {conv_err}", exc_info=True)
                if ctx:
                    await ctx.info(f"SVG to PNG conversion failed: {conv_err}")
                return None

            with open(png_file, 'rb') as f:
                img_data = f.read()

            logger.info(f"Successfully generated thumbnail with CLI, size: {len(img_data)} bytes")
            if ctx:
                await ctx.report_progress(90, 100)
                await ctx.info(f"Thumbnail saved to: {png_file}")
            return Image(data=img_data, format="png")

        except subprocess.CalledProcessError as e:
            logger.error(f"Command '{' '.join(e.cmd)}' failed with code {e.returncode}")
            logger.error(f"Stderr: {e.stderr}")
            logger.error(f"Stdout: {e.stdout}")
            if ctx:
                await ctx.info(f"KiCad CLI command failed: {e.stderr or e.stdout}")
            return None
        except subprocess.TimeoutExpired:
            logger.warning(f"Command timed out after 30 seconds: {' '.join(cmd)}")
            if ctx:
                await ctx.info("KiCad CLI command timed out")
            return None
        except Exception as e:
            logger.error(f"Error running CLI command: {str(e)}", exc_info=True)
            if ctx:
                await ctx.info(f"Error running KiCad CLI: {str(e)}")
            return None

    except asyncio.CancelledError:
        logger.info("CLI thumbnail generation cancelled")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in CLI thumbnail generation: {str(e)}")
        if ctx:
            await ctx.info(f"Unexpected error: {str(e)}")
        return None
