# SPDX-License-Identifier: GPL-3.0-or-later
"""
Analysis and validation tools for KiCad projects.
"""
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.path_env import to_local_path


def register_analysis_tools(mcp: FastMCP) -> None:
    """Register analysis and validation tools with the MCP server.
    
    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    def validate_project(project_path: str) -> dict[str, Any]:
        """Sanity-check that a ``.kicad_pro`` is loadable and complete.

        Use this as the first step when you receive an unknown project path
        from the user. Verifies: file exists, JSON parses, schematic + PCB
        siblings are present. Don't reach for ``json.load`` + manual
        existence checks — this returns a structured ``issues`` list the LLM
        can act on directly.

        Args:
            project_path: Path to the ``.kicad_pro`` file.

        Returns:
            ``{success: bool, valid: bool, path, issues: [...] | None,
            files_found: [...]}`` — ``success`` follows the project-wide tool
            convention (did the check run), ``valid`` is the domain verdict.
        """
        project_path = to_local_path(project_path)
        if not os.path.exists(project_path):
            return {"success": False, "valid": False,
                    "error": f"Project not found: {project_path}"}

        issues = []
        files = get_project_files(project_path)

        # Check for essential files
        if "pcb" not in files:
            issues.append("Missing PCB layout file")

        if "schematic" not in files:
            issues.append("Missing schematic file")

        # Validate project file
        try:
            with open(project_path, encoding="utf-8") as f:
                import json
                json.load(f)
        except json.JSONDecodeError:
            issues.append("Invalid project file format (JSON parsing error)")
        except Exception as e:
            issues.append(f"Error reading project file: {str(e)}")

        return {
            "success": True,  # the check ran; `valid` carries the verdict
            "valid": len(issues) == 0,
            "path": project_path,
            "issues": issues if issues else None,
            "files_found": list(files.keys())
        }

