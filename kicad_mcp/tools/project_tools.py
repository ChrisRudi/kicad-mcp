# SPDX-License-Identifier: GPL-3.0-or-later
"""
Project management tools for KiCad.
"""
import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.file_utils import get_project_files, load_project_json
from kicad_mcp.utils.kicad_utils import find_kicad_projects, open_kicad_project
from kicad_mcp.utils.path_env import to_local_path

# Get PID for logging
# _PID = os.getpid()

def register_project_tools(mcp: FastMCP) -> None:
    """Register project management tools with the MCP server.
    
    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    def list_projects() -> list[dict[str, Any]]:
        """Find every ``.kicad_pro`` project on the user's machine.

        Use this when the user mentions "my projects" / "available projects"
        without naming a path. Don't run ``find`` / ``Get-ChildItem`` yourself —
        this tool walks the well-known KiCad project locations (configurable
        via ``KICAD_USER_PROJECTS_DIR``), de-duplicates symlinks, and returns
        each hit with its parent directory and last-modified timestamp.

        Returns:
            List of project dicts with ``name``, ``path``, ``directory``,
            ``modified``.
        """
        logging.info("Executing list_projects tool...")
        projects = find_kicad_projects()
        logging.info(f"list_projects tool returning {len(projects)} projects.")
        return projects

    @mcp.tool()
    def get_project_structure(project_path: str) -> dict[str, Any]:
        """Resolve a ``.kicad_pro`` to its full file family + project metadata.

        Use this whenever you need the schematic / PCB / netlist / DRC report
        paths for a given project — don't re-derive them by stem-matching
        siblings of the ``.kicad_pro``: KiCad does not require all family
        members to share a stem (multi-sheet, archive copies, sub-projects).
        This tool reads the on-disk ``.kicad_pro`` JSON and returns every
        related file the project actually references.

        Accepts WSL or Windows paths (auto-normalized).

        Args:
            project_path: Path to the ``.kicad_pro`` file.

        Returns:
            ``{name, path, directory, files: {schematic, pcb, ...}, metadata}``
            or ``{error}`` if the path does not exist.
        """
        project_path = to_local_path(project_path)
        if not os.path.exists(project_path):
            return {"error": f"Project not found: {project_path}"}

        project_dir = os.path.dirname(project_path)
        project_name = os.path.basename(project_path)[:-10]  # Remove .kicad_pro extension

        # Get related files
        files = get_project_files(project_path)

        # Get project metadata
        metadata = {}
        project_data = load_project_json(project_path)
        if project_data and "metadata" in project_data:
            metadata = project_data["metadata"]

        return {
            "name": project_name,
            "path": project_path,
            "directory": project_dir,
            "files": files,
            "metadata": metadata
        }

    @mcp.tool()
    def open_project(project_path: str) -> dict[str, Any]:
        """Launch the KiCad project manager and load ``project_path``.

        Use this when the user wants to *interact* with a project in the GUI
        (so subsequent ``ipc_*`` tools have an editor to talk to). Don't
        ``subprocess.run("kicad", ...)`` manually — this tool resolves the
        right binary on Windows / WSL / Linux / macOS, falls back to
        ``cmd /c start`` on Windows for non-blocking launch, and reports
        success / failure as a structured dict.

        For headless work on the same project, prefer the disk-based tools
        (``get_project_structure``, ``run_drc_check``, …) — no GUI needed.

        Args:
            project_path: Path to the ``.kicad_pro`` file.

        Returns:
            ``{success, project, command}`` or ``{success: False, error}``.
        """
        return open_kicad_project(to_local_path(project_path))
