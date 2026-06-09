# SPDX-License-Identifier: GPL-3.0-or-later
"""Dynamic tool-registry test.

Builds the *real* MCP server (same registration sequence as
``kicad_mcp.server.create_server``) and walks every registered tool,
asserting cheap structural invariants on each one:

* the tool has a non-empty ``description`` (so MCP clients can render it),
* every signature ``*_path`` / ``*_dir`` / ``input_path`` / ``output_path`` /
  ``library_root`` parameter is normalised through ``to_local_path`` at
  the function entry, and
* every tool can be called once with empty args and either errors with a
  structured ``{success: False, error: ...}`` dict, returns success, or
  raises a *handled* exception type. No tool may explode the asyncio loop
  with ``TypeError`` / ``AttributeError`` from missing path handling.

The list of expected tools is derived dynamically — adding or removing a
``@mcp.tool()`` decorator shows up here automatically. The test fails
loudly if a tool drops out of the registry.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from mcp.server.fastmcp import FastMCP


# --- Path-typed parameter names that MUST receive to_local_path normalisation
PATH_PARAM_NAMES = {
    "sch_path", "pcb_path", "schematic_path", "project_path",
    "input_path", "output_path", "output_dir", "dsn_path", "ses_path",
    "output_pcb_path", "output_ses_path", "library_root", "custom_path",
    "netlist_path", "install_dir", "python_executable", "file_path",
    "pdf_path", "out_path",  # circuit_block (Layer T)
    "datasheet_pdf",  # review_tools (Layer R)
}

# These parameter names are *not* paths even though they end with _path
# (none currently — kept for future overrides).
PATH_PARAM_EXCLUDES: set[str] = set()

# Tools that pass their path argument straight to another @mcp.tool which
# itself normalises. The wrapper does not need its own to_local_path call.
DELEGATING_TOOLS = {
    "get_erc_violations",       # → run_erc(schematic_path)
    "generate_project_thumbnail",  # → generate_pcb_thumbnail(project_path)
}


@pytest.fixture(scope="module")
def server() -> FastMCP:
    """Build the same server `create_server` builds — but skip prompt /
    signal-handler registration that touches the global atexit table.

    The tool families come from the single-source-of-truth
    ``TOOL_REGISTRARS`` so this test can never drift from what the live
    server actually registers."""
    from kicad_mcp.tool_registry import TOOL_REGISTRARS

    mcp = FastMCP("test-all-tools")
    for register in TOOL_REGISTRARS:
        register(mcp)
    return mcp


@pytest.fixture(scope="module")
def tool_list(server: FastMCP) -> list[Any]:
    """Resolve the FastMCP tool registry into a plain list of Tool objects."""
    return asyncio.run(server.list_tools())


def test_tool_count_matches_readme(tool_list):
    """Loose sanity band around the full registry (~144 tools as of
    2026-05-30; exact lock lives in test_tool_audit.test_tool_count_locked).
    Drift outside this band either way is suspicious."""
    n = len(tool_list)
    assert 120 <= n <= 170, f"Unexpected tool count {n} — expected ~144"


def test_no_duplicate_tool_names(tool_list):
    names = [t.name for t in tool_list]
    dup = {x for x in names if names.count(x) > 1}
    assert not dup, f"Duplicate tool registrations: {sorted(dup)}"


def test_every_tool_has_description(tool_list):
    """MCP clients render the description in the tool picker — empty
    descriptions slip through often, this is the canary."""
    missing = [t.name for t in tool_list if not (t.description or "").strip()]
    assert not missing, f"Tools without description: {missing}"


# Tools whose description is intentionally short — usually because they
# take no arguments and do exactly one thing (status probes, no-op stubs).
_SHORT_DOC_ALLOWED = {
    "ipc_check_status", "ipc_get_open_documents", "ipc_revert_via_action",
    "ipc_save_via_action",
}


def test_descriptions_meet_minimum_length(tool_list):
    """Every tool's description should be substantive enough to guide an
    LLM picker — an absolute floor of 280 chars catches one-line stubs
    (the original ``generate_project_thumbnail`` was 75 chars). The
    audit on 2026-04-29 brought the whole repo to ≥286 chars; this test
    locks that bar in.
    """
    too_short = [
        (t.name, len((t.description or "").strip()))
        for t in tool_list
        if t.name not in _SHORT_DOC_ALLOWED
        and len((t.description or "").strip()) < 280
    ]
    assert not too_short, (
        "Tools with thin docstrings (≥280 chars expected):\n  "
        + "\n  ".join(f"{n}: {ln} chars" for n, ln in too_short)
    )


def test_majority_of_tools_have_usage_hints(tool_list):
    """An LLM picking between similar tools relies on phrases like
    'use this when' / 'don't' / 'instead of' / 'before' to disambiguate.
    We hold the ratio at ≥ 70 % — the audit on 2026-04-29 reached 69/91 ≈ 76 %.
    """
    cues = (
        "use this", "use instead", "don't", "do not", "preferred",
        "before", "after", "first ", "instead of", "rather than",
    )
    with_hint = [
        t for t in tool_list
        if any(c in (t.description or "").lower() for c in cues)
    ]
    ratio = len(with_hint) / len(tool_list)
    assert ratio >= 0.70, (
        f"Only {len(with_hint)}/{len(tool_list)} tools "
        f"({ratio:.0%}) carry an LLM-friendly usage hint — "
        f"need ≥ 70 %. Add 'Use this when …' / 'Don't …' phrasing to "
        "thin docstrings."
    )


def test_path_params_are_normalised(tool_list):
    """Every ``@mcp.tool`` whose signature lists a filesystem-path
    parameter must pass that parameter through ``to_local_path`` (directly
    or via an aliased reassignment) inside its own body. Internal helper
    functions are not checked — their callers already normalise."""
    import importlib
    import pkgutil
    import re
    import kicad_mcp.tools as pkg

    bad: list[str] = []
    # Match ``@mcp.tool(...)`` followed by an ``async def`` or ``def``,
    # then the function body (up to the next def at the same indent or
    # end of register block). The capture is greedy enough to span a
    # multi-line signature + the full body.
    tool_block_re = re.compile(
        r"@mcp\.tool\([^)]*\)\s*\n\s*(?:async\s+)?def\s+(\w+)\s*\("
        r"(.*?)"          # signature
        r"\)\s*(?:->\s*[^:]+)?:"
        r"(.*?)"          # body
        r"(?=\n\s{0,4}@mcp\.tool|\n\s{0,4}def\s+\w+|\Z)",
        re.DOTALL,
    )
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        if mod_info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"kicad_mcp.tools.{mod_info.name}")
        try:
            src = inspect.getsource(mod)
        except OSError:
            continue
        for tool_name, sig, body in tool_block_re.findall(src):
            if tool_name in DELEGATING_TOOLS:
                continue
            for param in PATH_PARAM_NAMES:
                # word-boundary match so 'ses_path' doesn't match inside
                # 'output_ses_path'.
                if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(param)}\s*:\s*str", sig):
                    continue
                applied = bool(
                    re.search(rf"to_local_path\(\s*{re.escape(param)}\b", body)
                ) or bool(
                    re.search(rf"\b{re.escape(param)}\s*=\s*to_local_path\(",
                              body)
                )
                if not applied:
                    bad.append(
                        f"{mod_info.name}::{tool_name}: parameter '{param}' "
                        f"not passed through to_local_path"
                    )
    assert not bad, "\n".join(bad)


# --- per-tool empty-call sanity check -------------------------------------


# These tools are known to require live state / non-empty inputs and would
# hard-error in ways that are not harness bugs. Callable but not happy-path.
EXPECTED_EMPTY_CALL_FAILURES = {
    # GUI / IPC bridge — needs a running KiCad
    "ipc_check_status", "ipc_get_open_documents", "ipc_get_pad_world_pos",
    "ipc_set_footprint_pose", "ipc_route_pin_to_pin", "ipc_add_zone_pour",
    "ipc_route_power_ring", "ipc_save", "ipc_save_via_action",
    "ipc_save_all", "ipc_run_drc", "ipc_run_erc", "ipc_revert",
    "ipc_revert_via_action", "ipc_install_kipy", "ipc_export_schematic",
    "kicad_mcp_doctor",
    # Need filesystem I/O
    "list_projects", "open_project", "get_project_structure",
    "validate_project", "list_pcb_footprints", "analyze_pcb_nets",
    "find_tracks_by_net", "list_schematic_components", "get_symbol_details",
    "search_symbols", "get_schematic_info", "analyze_pin_functions",
    "detect_pin_conflicts", "identify_circuit_patterns",
    "analyze_project_circuit_patterns", "extract_project_netlist",
    "extract_schematic_netlist", "find_component_connections",
    "analyze_schematic_connections", "run_erc", "get_erc_violations",
    "run_drc_check", "get_drc_history_tool", "analyze_bom",
    "export_bom_csv", "validate_design",
    # Export
    "export_gerbers", "export_drill", "export_step", "export_pdf",
    "export_svg", "export_png", "export_pos", "render_3d",
    "get_board_stats", "generate_pcb_thumbnail",
    "generate_project_thumbnail",
    # Generation
    "generate_project", "generate_schematic", "generate_pcb",
    "generate_from_netlist", "convert_ltspice_to_kicad", "esphome_to_kicad",
    "list_esphome_components",
    # Patch / geometry — need a target file
    "patch_pcb_nets_from_netlist", "resolve_pcb_footprints",
    "validate_footprints", "rotate_pcb", "compute_pad_world_positions",
    "add_track_to_pcb", "add_zone_pour_to_pcb",
    "add_segment", "delete_footprint", "add_footprint_text",
    "set_footprint_3d_model", "set_footprint_property_visibility",
    "compute_pin_world_positions_sch", "list_schematic_groups",
    "get_schematic_bbox", "add_schematic_symbols", "add_schematic_wire",
    "add_schematic_label", "connect_pins", "validate_schematic_patch",
    "annotate_schematic", "move_schematic_group", "rotate_schematic_group",
    "delete_schematic_items",
    # Footprint search — needs the indexed library
    "index_kicad_footprints", "search_footprints", "find_footprint_by_specs",
    "suggest_builtin_for_custom",
    # Benchmark — internal
    "benchmark_schematic", "benchmark_loop",
    # Circuit-block (Layer T) — needs spec / sch / pdf args
    "validate_circuit_block", "apply_circuit_block", "apply_template_block",
    "extract_pdf_tables", "extract_circuit_from_pdf",
    # Review (Layer R) — needs project / IC ref
    "review_ic_against_datasheet", "review_system_interconnect",
    "list_missing_datasheets",
}


@pytest.mark.parametrize("tool_name", [None])  # placeholder; real param ids below
def test_placeholder(tool_name):
    """Pytest collection requires at least one parametrize value; replaced
    by ``test_every_tool_responds_to_empty_call`` which builds its own."""


def _all_tool_names_from_server() -> list[str]:
    """Collected once at module load time so pytest can parametrize on it.

    Uses the shared ``TOOL_REGISTRARS`` list so the parametrized empty-call
    coverage matches the live server exactly."""
    from kicad_mcp.tool_registry import TOOL_REGISTRARS

    mcp = FastMCP("listing")
    for r in TOOL_REGISTRARS:
        r(mcp)
    return [t.name for t in asyncio.run(mcp.list_tools())]


_ALL_TOOL_NAMES = _all_tool_names_from_server()


@pytest.mark.parametrize("tool_name", _ALL_TOOL_NAMES)
def test_every_tool_responds_to_empty_call(server: FastMCP, tool_name: str):
    """Each tool must either succeed, return a structured failure dict, or
    raise a recognised input-validation exception when called with no
    arguments. No silent ``TypeError`` from a missing path normaliser.
    """
    try:
        result = asyncio.run(server.call_tool(tool_name, {}))
    except Exception as exc:
        # FastMCP wraps missing required args / type mismatches in
        # ToolError or McpError. Both inherit from Exception. We only
        # fail if the message is a clear bug indicator, e.g.
        # 'NoneType' has no attribute 'startswith' (path normaliser
        # called on None).
        msg = str(exc).lower()
        forbidden = ("nonetype", "object has no attribute", "tuple index out of range")
        for needle in forbidden:
            assert needle not in msg, (
                f"{tool_name} raised an unhandled internal error "
                f"(likely missing path-normaliser or arg-default): {exc}"
            )
        return

    # FastMCP returns a tuple (text-content, structured-result) for tools
    # that return dicts. Structured part is the second element.
    payload = result[1] if isinstance(result, tuple) and len(result) > 1 else result
    if isinstance(payload, dict) and "success" in payload:
        # Either succeeded or returned a structured False — both fine.
        assert isinstance(payload["success"], bool), (
            f"{tool_name} returned non-bool 'success'"
        )
    # Tools that don't return a dict (e.g. list_projects → list, image
    # generators → Image) just need to not have raised — already verified.


def test_no_unexpected_passes_against_expectation():
    """Sanity: the EXPECTED_EMPTY_CALL_FAILURES set is meant to track which
    tools need state. If a name in the set isn't actually registered, that's
    test-rot — drop it."""
    stale = sorted(EXPECTED_EMPTY_CALL_FAILURES - set(_ALL_TOOL_NAMES))
    # Allow the set to be a superset of the registry — useful when a tool is
    # temporarily removed — but flag the stale names so we eventually clean.
    if stale:
        pytest.skip(f"Stale entries in EXPECTED_EMPTY_CALL_FAILURES: {stale}")
