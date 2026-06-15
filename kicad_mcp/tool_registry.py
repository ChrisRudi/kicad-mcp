# SPDX-License-Identifier: GPL-3.0-or-later
"""Central registry of MCP registrar callables.

Single source of truth for which tool / resource / prompt families the
KiCad MCP server exposes. Imported by ``kicad_mcp.server.create_server``
*and* by the test-suite (``tests/test_all_tools_dynamic.py``,
``tests/test_tool_audit.py``) so the two can never drift again: adding a
family here wires it into the live server *and* into the dynamic coverage
tests in one edit.

Before this module existed the registrar list lived inline in
``server.py`` and was copy-pasted into both test files — the three copies
had already diverged (server registered 29 tool families, one test 23,
the other 25), silently dropping ``polar_grid`` / ``connectivity`` /
``pcb_session`` / ``pcb_render`` / ``audit`` / ``docs`` from test
coverage. Keep the list here and nowhere else.

Order is preserved for log readability; registration itself is
order-independent. Each entry is a ``register_*(mcp) -> None`` callable
that attaches a family of ``@mcp.tool()`` / resource / prompt handlers to
the passed FastMCP instance.
"""

from collections.abc import Callable

# --- Tool registrars -------------------------------------------------------
from kicad_mcp.tools.project_tools import register_project_tools
from kicad_mcp.tools.analysis_tools import register_analysis_tools
from kicad_mcp.tools.export_tools import register_export_tools
from kicad_mcp.tools.drc_tools import register_drc_tools
from kicad_mcp.tools.bom_tools import register_bom_tools
from kicad_mcp.tools.netlist_tools import register_netlist_tools
from kicad_mcp.tools.pattern_tools import register_pattern_tools
from kicad_mcp.tools.erc_tools import register_erc_tools
from kicad_mcp.tools.cli_export_tools import register_cli_export_tools
from kicad_mcp.tools.schematic_tools import register_schematic_tools
from kicad_mcp.tools.pcb_tools import register_pcb_tools
from kicad_mcp.tools.pcb_patch_tools import register_pcb_patch_tools
from kicad_mcp.tools.sch_patch_tools import register_sch_patch_tools
from kicad_mcp.tools.circuit_block_tools import register_circuit_block_tools
from kicad_mcp.tools.pcb_geometry_tools import register_pcb_geometry_tools
from kicad_mcp.tools.polar_grid_tools import register_polar_grid_tools
from kicad_mcp.tools.footprint_search_tools import register_footprint_search_tools
from kicad_mcp.tools.ipc_tools import register_ipc_tools
from kicad_mcp.tools.pin_tools import register_pin_tools
from kicad_mcp.tools.connectivity_tools import register_connectivity_tools
from kicad_mcp.tools.via_promote_tools import register_via_promote_tools
from kicad_mcp.tools.ipc_live_tools import register_ipc_live_tools
from kicad_mcp.tools.ipc_interact_tools import register_ipc_interact_tools
from kicad_mcp.tools.ipc_markup_tools import register_ipc_markup_tools
from kicad_mcp.tools.footprint_resync_tools import register_footprint_resync_tools
from kicad_mcp.tools.pcb_session_tools import register_pcb_session_tools
from kicad_mcp.tools.pcb_render_tools import register_pcb_render_tools
from kicad_mcp.tools.generation_tools import register_generation_tools
from kicad_mcp.tools.esphome_tools import register_esphome_tools
from kicad_mcp.tools.ltspice_tools import register_ltspice_tools
from kicad_mcp.tools.review_tools import register_review_tools
from kicad_mcp.tools.audit_tools import register_audit_tools
from kicad_mcp.tools.docs_tools import register_docs_tools

# --- Resource registrars ---------------------------------------------------
from kicad_mcp.resources.projects import register_project_resources
from kicad_mcp.resources.files import register_file_resources
from kicad_mcp.resources.drc_resources import register_drc_resources
from kicad_mcp.resources.bom_resources import register_bom_resources
from kicad_mcp.resources.netlist_resources import register_netlist_resources
from kicad_mcp.resources.pattern_resources import register_pattern_resources

# --- Prompt registrars -----------------------------------------------------
from kicad_mcp.prompts.templates import register_prompts
from kicad_mcp.prompts.drc_prompt import register_drc_prompts
from kicad_mcp.prompts.bom_prompts import register_bom_prompts
from kicad_mcp.prompts.pattern_prompts import register_pattern_prompts


# A registrar takes the FastMCP server and attaches handlers to it. The
# server is created from ``fastmcp.FastMCP`` in production and from
# ``mcp.server.fastmcp.FastMCP`` in tests; both are duck-compatible here,
# so the parameter is left untyped to avoid coupling to either import.
Registrar = Callable[..., None]

TOOL_REGISTRARS: list[Registrar] = [
    register_project_tools,
    register_analysis_tools,
    register_export_tools,
    register_drc_tools,
    register_bom_tools,
    register_netlist_tools,
    register_pattern_tools,
    register_erc_tools,
    register_cli_export_tools,
    register_schematic_tools,
    register_pcb_tools,
    register_pcb_patch_tools,
    register_sch_patch_tools,
    register_circuit_block_tools,
    register_pcb_geometry_tools,
    register_polar_grid_tools,
    register_footprint_search_tools,
    register_ipc_tools,
    register_pin_tools,
    register_connectivity_tools,
    register_via_promote_tools,
    register_ipc_live_tools,
    register_ipc_interact_tools,
    register_ipc_markup_tools,
    register_footprint_resync_tools,
    register_pcb_session_tools,
    register_pcb_render_tools,
    register_generation_tools,
    register_esphome_tools,
    register_ltspice_tools,
    register_review_tools,
    register_audit_tools,
    register_docs_tools,
]

RESOURCE_REGISTRARS: list[Registrar] = [
    register_project_resources,
    register_file_resources,
    register_drc_resources,
    register_bom_resources,
    register_netlist_resources,
    register_pattern_resources,
]

PROMPT_REGISTRARS: list[Registrar] = [
    register_prompts,
    register_drc_prompts,
    register_bom_prompts,
    register_pattern_prompts,
]


def register_all_tools(mcp) -> None:
    """Attach every tool family in ``TOOL_REGISTRARS`` to ``mcp``."""
    for register in TOOL_REGISTRARS:
        register(mcp)


def register_all_resources(mcp) -> None:
    """Attach every resource family in ``RESOURCE_REGISTRARS`` to ``mcp``."""
    for register in RESOURCE_REGISTRARS:
        register(mcp)


def register_all_prompts(mcp) -> None:
    """Attach every prompt family in ``PROMPT_REGISTRARS`` to ``mcp``."""
    for register in PROMPT_REGISTRARS:
        register(mcp)
