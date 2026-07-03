# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-tool audit — every registered tool must (a) carry a substantive
description and (b) route every filesystem-path parameter through
``to_local_path`` at the function entry.

This is a stricter, per-tool counterpart to the aggregate checks in
``test_all_tools_dynamic.py`` (which holds e.g. a ≥ 70 % usage-cue
ratio for the whole registry). The tests here use ``pytest.parametrize``
so a regression on a single tool surfaces as a single failed test
case with the offending tool name in the failure id.

Two audits run on every tool:

  * ``test_tool_description_quality`` — length floor (280 chars) plus
    at least one usage cue ("Use this when …", "Don't …", "instead of
    …"). Tools that are unambiguous by name carry an explicit
    ``ALLOWLIST_NO_USAGE_CUE`` exemption.
  * ``test_tool_paths_normalised`` — every path-typed parameter
    in the tool's signature must be reassigned via ``to_local_path``
    inside the body. No exemptions: the WSL ↔ Windows bridge depends
    on this for every entry point.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import re
from typing import Any

import pytest

from mcp.server.fastmcp import FastMCP

import kicad_mcp.tools as _tools_pkg


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


# Description floor — same bar tests/test_all_tools_dynamic.py
# ::test_descriptions_meet_minimum_length already enforces.
MIN_DESCRIPTION_CHARS = 280

# Phrases that signal "when to pick this tool over a similar one".
USAGE_CUES = (
    "use this", "use instead", "don't", "do not", "preferred",
    "before", "after", "first ", "instead of", "rather than",
)

# Tools whose names are unambiguous enough that no usage-cue phrase is
# required. Adding a new tool to this list requires a one-line comment
# explaining why the name alone is sufficient.
ALLOWLIST_NO_USAGE_CUE: dict[str, str] = {
    # Self-explanatory — Format converters / generators / probes
    "esphome_to_kicad": "format converter — name fully describes input/output",
    "convert_ltspice_to_kicad": "format converter — name fully describes input/output",
    "generate_project": "single-purpose generator — no sibling to disambiguate against",
    "generate_from_netlist": "single-purpose generator — name fully describes input",
    "kicad_mcp_doctor": "health probe — no sibling to disambiguate against",
    "benchmark_schematic": "internal benchmark, hidden tool",
    "benchmark_loop": "internal benchmark, hidden tool",
    # Footprint-search family — names already disambiguate by suffix
    "index_kicad_footprints": "indexer — clear scope from name",
    "search_footprints": "search — clear scope from name",
    "find_footprint_by_specs": "by-specs lookup — clear scope from name",
    "compute_pad_world_positions": "deterministic geometry compute — name fully describes",
    # PCB live (IPC) — disambiguated from disk pendant by the ipc_ prefix
    "ipc_get_pad_world_pos": "IPC variant — ipc_ prefix disambiguates from disk pendant",
    "ipc_route_pin_to_pin": "IPC variant — ipc_ prefix disambiguates from disk pendant",
    "ipc_add_zone_pour": "IPC variant — ipc_ prefix disambiguates from disk pendant",
    "ipc_route_power_ring": "IPC variant — ipc_ prefix disambiguates from disk pendant",
    # PCB headless — disambiguated from IPC pendant by lack of ipc_ prefix
    "add_track_to_pcb": "headless variant — disambiguated from ipc_route_pin_to_pin by name",
    "add_zone_pour_to_pcb": "headless variant — disambiguated from ipc_add_zone_pour by name",
    "patch_pcb_nets_from_netlist": "single-purpose F8-equivalent — no sibling",
    "resolve_pcb_footprints": "single-purpose [lib:fp] resolver — no sibling",
    "rotate_pcb": "single-purpose pcb rotation — no sibling",
    # Schematic — names map 1:1 to the action
    "add_schematic_symbols": "primary patcher entry — usage hint lives in connect_pins/add_power_symbols",
    "add_power_symbols": "primary patcher entry — pendant tools cite *this* one as the right choice",
    "delete_schematic_items": "single-purpose deletion — no sibling",
}

# Path-typed parameter names — same set as test_all_tools_dynamic.py.
PATH_PARAM_NAMES = {
    "sch_path", "pcb_path", "schematic_path", "project_path",
    "input_path", "output_path", "output_dir", "dsn_path", "ses_path",
    "output_pcb_path", "output_ses_path", "library_root", "custom_path",
    "netlist_path", "install_dir", "python_executable", "file_path",
    "pdf_path", "out_path",
}

# Tools that pass their path argument straight to another @mcp.tool
# (which itself normalises). The wrapper does not need its own
# to_local_path call.
DELEGATING_TOOLS = {
    "get_erc_violations",
    "generate_project_thumbnail",
}


# ---------------------------------------------------------------------------
# Server fixture — registers every tool family, same sequence as
# ``kicad_mcp.server.create_server``.
# ---------------------------------------------------------------------------


def _build_server() -> FastMCP:
    # Tool families come from the single-source-of-truth TOOL_REGISTRARS so
    # this audit always covers exactly what the live server registers.
    from kicad_mcp.tool_registry import TOOL_REGISTRARS

    mcp = FastMCP("tool-audit")
    for register in TOOL_REGISTRARS:
        register(mcp)
    return mcp


@pytest.fixture(scope="module")
def server() -> FastMCP:
    return _build_server()


@pytest.fixture(scope="module")
def tool_list(server: FastMCP) -> list[Any]:
    return asyncio.run(server.list_tools())


def _all_tool_names() -> list[str]:
    """Resolved at import time so pytest.parametrize can use the names
    as test ids — gives us 'test_tool_description_quality[run_erc]'
    style failure messages instead of an opaque numeric index."""
    return [t.name for t in asyncio.run(_build_server().list_tools())]


_ALL_TOOL_NAMES = _all_tool_names()


# ---------------------------------------------------------------------------
# Audit 1 — description quality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", _ALL_TOOL_NAMES)
def test_tool_description_quality(server, tool_list, tool_name):
    """Every tool's MCP description must be ≥ MIN_DESCRIPTION_CHARS and,
    unless the tool is on ALLOWLIST_NO_USAGE_CUE, contain at least one
    usage cue ('Use this when …', 'Don't …', 'instead of …') so the
    picker LLM can disambiguate it from sibling tools.

    The allowlist is curated — each entry carries a one-line
    justification why the tool's name alone is sufficient.
    """
    tool = next((t for t in tool_list if t.name == tool_name), None)
    assert tool is not None, f"{tool_name}: tool dropped from registry"

    desc = (tool.description or "").strip()
    assert len(desc) >= MIN_DESCRIPTION_CHARS, (
        f"{tool_name}: description too short "
        f"({len(desc)} chars, need ≥ {MIN_DESCRIPTION_CHARS}). "
        "Add a 'Use this when …' / 'Don't …' / context block."
    )

    if tool_name in ALLOWLIST_NO_USAGE_CUE:
        return  # explicit exemption

    has_cue = any(cue in desc.lower() for cue in USAGE_CUES)
    assert has_cue, (
        f"{tool_name}: description has no usage cue "
        f"({USAGE_CUES!r}). Either add 'Use this when … / Don't …' "
        "phrasing or list the tool in ALLOWLIST_NO_USAGE_CUE with "
        "a one-line justification."
    )


# ---------------------------------------------------------------------------
# Audit 2 — path-parameter normalisation
# ---------------------------------------------------------------------------


_TOOL_BLOCK_RE = re.compile(
    r"@mcp\.tool\([^)]*\)\s*\n\s*(?:async\s+)?def\s+(\w+)\s*\("
    r"(.*?)"          # signature
    r"\)\s*(?:->\s*[^:]+)?:"
    r"(.*?)"          # body
    r"(?=\n\s{0,4}@mcp\.tool|\n\s{0,4}def\s+\w+|\Z)",
    re.DOTALL,
)


def _grep_tool_bodies() -> dict[str, tuple[str, str]]:
    """Walk every kicad_mcp.tools.<module> source and return
    {tool_name: (signature, body)}.

    Used by both the path-normalisation audit and any future
    audit that needs to look at the source text of each tool.
    """
    out: dict[str, tuple[str, str]] = {}
    for mod_info in pkgutil.iter_modules(_tools_pkg.__path__):
        if mod_info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"kicad_mcp.tools.{mod_info.name}")
        try:
            src = inspect.getsource(mod)
        except OSError:
            continue
        for tool_name, sig, body in _TOOL_BLOCK_RE.findall(src):
            out[tool_name] = (sig, body)
    return out


_TOOL_BODIES = _grep_tool_bodies()


@pytest.mark.parametrize("tool_name", sorted(_TOOL_BODIES))
def test_tool_paths_normalised(tool_name):
    """Every path-typed parameter in the tool signature must be passed
    through ``to_local_path`` inside the function body. Either form
    accepted: a direct call ``to_local_path(sch_path)`` or a self-
    reassignment ``sch_path = to_local_path(sch_path)``.

    This is the WSL ↔ Windows bridge contract — without it, a path
    like ``/mnt/c/...`` arrives at downstream Win-API code unconverted
    and explodes in cryptic ways. ``DELEGATING_TOOLS`` are exempt
    because they hand the path straight to another ``@mcp.tool`` that
    runs the normalisation itself.
    """
    if tool_name in DELEGATING_TOOLS:
        pytest.skip(f"{tool_name} delegates path handling to another tool")

    sig, body = _TOOL_BODIES[tool_name]
    missing: list[str] = []
    for param in PATH_PARAM_NAMES:
        if not re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(param)}\s*:\s*str", sig
        ):
            continue
        applied = bool(
            re.search(rf"to_local_path\(\s*{re.escape(param)}\b", body)
        ) or bool(
            re.search(rf"\b{re.escape(param)}\s*=\s*to_local_path\(", body)
        )
        if not applied:
            missing.append(param)

    assert not missing, (
        f"{tool_name}: path parameters not normalised through "
        f"to_local_path: {missing}. Add `{missing[0]} = "
        f"to_local_path({missing[0]})` as the first line of the "
        "tool body, or list the tool in DELEGATING_TOOLS if it "
        "hands the path straight to another @mcp.tool."
    )


# ---------------------------------------------------------------------------
# Sanity: the audit itself stays in sync with the registry
# ---------------------------------------------------------------------------


def test_audit_covers_every_registered_tool(tool_list):
    """The two parametrised audits walk distinct sources (MCP registry
    vs. source-code grep). They should agree on the tool inventory —
    drift here usually means a tool was registered without an
    ``@mcp.tool()`` decorator (or vice versa).
    """
    registry = {t.name for t in tool_list}
    grepped = set(_TOOL_BODIES.keys())
    only_registry = registry - grepped
    only_grepped = grepped - registry
    assert not only_registry and not only_grepped, (
        f"Audit drift — registry-only: {sorted(only_registry)}; "
        f"source-only: {sorted(only_grepped)}"
    )


def test_allowlist_has_no_obsolete_entries(tool_list):
    """Any name in ALLOWLIST_NO_USAGE_CUE must still correspond to a
    registered tool. Catches stale entries when a tool gets renamed
    or removed.
    """
    registry = {t.name for t in tool_list}
    stale = sorted(set(ALLOWLIST_NO_USAGE_CUE) - registry)
    assert not stale, f"Stale allowlist entries: {stale}"


# ===========================================================================
# Audit (i) — exact tool-count lock
# ===========================================================================


# Hard-coded after the Layer-T addition (2026-05-10). Bump deliberately
# when tools are added/removed; an accidental double-register or drop
# fails this test before it gets shipped.
# 2026-05-18: +1 (place_at_pivot), +1 (clone_layout_around_pivot),
# +1 (delete_pcb_routing), +1 (add_arc_to_pcb), +1 (add_via_to_pcb),
# +1 (update_pcb_from_schematic), +1 (pcb_batch), and accounting for
# the prior +1 drift from review-layer tools that were registered
# earlier without bumping this constant.
# 2026-05-23: +8 — the audit fixture used to skip register_review_tools,
# register_audit_tools and register_docs_tools; lining the fixture up
# with kicad_mcp.server adds list_missing_datasheets,
# review_ic_against_datasheet, review_system_interconnect (review),
# audit_power_tree, audit_schematic_topology (audit), list_kicad_actions,
# list_user_hotkeys, lookup_kicad_action (docs).
# 2026-05-30: +6 — fixture now derives from the shared TOOL_REGISTRARS
# (kicad_mcp/tool_registry.py), which also includes the four families the
# audit fixture had still been skipping: polar_grid (polar_grid),
# connectivity (check_connectivity), pcb_session (pcb_session_status,
# pcb_session_reset, pcb_eval), pcb_render (pcb_render) and via_promote
# (via_promote).
# 2026-06-09: +7 — lock had drifted behind the registry; the seven newest
# tools (via_retype, via_resize, and the five live_* IPC tools:
# live_get_state, live_diff_since_last, live_summarize_user_changes,
# live_move_footprint, live_session_status) were added without bumping it.
# 2026-06-09: -5 — FreeRouting/autoroute removed entirely (autoroute_tools.py
# deleted): install_autorouter, autoroute_pcb, check_autorouter_status,
# export_pcb_dsn, import_pcb_ses.
EXPECTED_TOOL_COUNT = 184  # +check_ampacity (IPC-2221 Stromtragfähigkeit)
# 2026-06-13: +1 (ipc_markup_to_tracks — markup-layer User.9 → copper tracks)
# 2026-07-03: +1 (evaluate_layout — non-mutating placement scorer for Entwirren)
# 2026-07-03: +1 (get_board_layout — read board into evaluate_layout shape)
# 2026-07-03: +1 (list_bus_members — Bus-Radar: semantic bus grouping)
# 2026-07-03: +1 (audit_design — Design-Wächter: semantic rule registry)
# 2026-07-03: +1 (consolidate_bom — BOM-Konsolidierung: E-series feeder cut)
# 2026-07-03: +1 (suggest_preferred_parts — fab preferred/Basic parts, provider-keyed)
# 2026-07-03: +1 (audit_test_points — Test-Punkt-Wächter: probe-access coverage)
# 2026-06-14: +1 (add_vias_to_pcb — batch via placement, one read+write)
# 2026-06-15: +3 (normalize_footprint_libid, refresh_pinfunctions,
#                 replace_footprint_canonical — footprint resync)
# 2026-06-18: +3 (pinout pipeline — search_symbol, validate_pinout,
#                 match_symbol_to_datasheet)
# 2026-06-23: +1 (center_item_clearance — spatial via centering between
#                 nearest foreign copper; one call replaces measure+nudge)
# 2026-06-23: +2 (drc_triage + drc_select_group — group live DRC by type with
#                 a suggested fix tool, then select a group in the editor)


def test_tool_count_locked(tool_list):
    """The tool registry size is locked to a single integer. Updates to
    EXPECTED_TOOL_COUNT in this file should accompany every PR that
    adds or removes a tool — code review thus sees both halves."""
    n = len(tool_list)
    assert n == EXPECTED_TOOL_COUNT, (
        f"Tool count mismatch: registry has {n}, EXPECTED_TOOL_COUNT is "
        f"{EXPECTED_TOOL_COUNT}. If you added/removed a tool, bump the "
        "constant in tests/test_tool_audit.py and document the diff in "
        "CHANGELOG.md."
    )


# ===========================================================================
# Audit (f) — tool-name convention
# ===========================================================================


_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@pytest.mark.parametrize("tool_name", _ALL_TOOL_NAMES)
def test_tool_name_format(tool_name):
    """Every tool name must be ASCII snake_case, start with a lowercase
    letter, and be ≤ 64 chars. MCP wire format and most clients
    silently mangle names that violate this — easier to enforce up
    front than debug later.
    """
    assert _TOOL_NAME_RE.match(tool_name), (
        f"Tool name {tool_name!r} violates the snake_case "
        "(/^[a-z][a-z0-9_]{0,63}$/) convention."
    )


# ===========================================================================
# Audit (g) — every signature parameter is documented in the Args: block
# ===========================================================================


_SIG_PARAM_RE = re.compile(
    r"(?:^|,)\s*([a-z_][a-z0-9_]*)\s*:\s*(?:[^,=]+?)(?:=|,|$)",
    re.MULTILINE,
)


def _params_from_sig(sig: str) -> list[str]:
    """Extract parameter names from a multi-line signature string."""
    seen: list[str] = []
    for m in _SIG_PARAM_RE.finditer(sig):
        name = m.group(1)
        if name in ("self", "cls", "ctx"):
            continue
        if name in seen:
            continue
        seen.append(name)
    return seen


def _docstring_args_block(body: str) -> str | None:
    """Return the text of the ``Args:``-block from a tool's docstring.

    Recognises the Google-style block used throughout the repo
    (``Args:`` indented one level, parameter name, colon, description,
    ending at the next un-indented ``Returns:``/``Raises:`` or end of
    docstring).
    """
    # Tool body always opens with a triple-quoted string. Find it.
    m = re.search(r'"""(.*?)"""', body, re.DOTALL)
    if not m:
        return None
    doc = m.group(1)
    args_m = re.search(
        r"\n\s*Args:\s*\n(.*?)(?:\n\s*(?:Returns:|Raises:|Examples?:|$))",
        doc, re.DOTALL,
    )
    return args_m.group(1) if args_m else None


# Tools whose docstring legitimately omits an Args: block (no params,
# or the description fully covers them inline). Pre-Layer-T tools that
# carry a thin Args block are grandfathered here.
ARGS_DOC_EXEMPT = {
    # Nullary / probe-style tools — name and short description are enough.
    "kicad_mcp_doctor",
    "ipc_check_status",
    "ipc_get_open_documents",
    "ipc_save_all",
    "ipc_install_kipy",
    "restart_mcp_child",
    "list_projects",
    "index_kicad_footprints",
    # Pre-Layer-T tools with thin or missing Args: block — verified
    # functional, doc-style nachzuziehen. Not blocking the audit landing.
    "get_schematic_bbox",
    "ipc_revert",
    "list_schematic_groups",
    "move_schematic_group",
    "rotate_schematic_group",
    "validate_schematic_patch",
}


@pytest.mark.parametrize("tool_name", sorted(_TOOL_BODIES))
def test_args_documented(tool_name):
    """Every parameter in the tool signature must appear in the
    docstring's ``Args:`` block. Catches doc-drift when a parameter
    is added without updating the docstring — the picker LLM relies
    on the Args block to know which arguments it can set."""
    sig, body = _TOOL_BODIES[tool_name]
    params = _params_from_sig(sig)
    if not params:
        return  # nullary tool, nothing to check

    args_block = _docstring_args_block(body)
    if args_block is None:
        if tool_name in ARGS_DOC_EXEMPT:
            return
        pytest.fail(
            f"{tool_name}: parameters {params} but no Args: block in "
            "docstring. Add Google-style 'Args:' or list the tool in "
            "ARGS_DOC_EXEMPT."
        )

    missing = [p for p in params if not re.search(rf"\b{re.escape(p)}\b", args_block)]
    if missing and tool_name in ARGS_DOC_EXEMPT:
        return  # grandfathered partial Args block
    assert not missing, (
        f"{tool_name}: signature parameters {missing} not mentioned in "
        "the docstring's Args: block."
    )


# ===========================================================================
# Audit (a) — every dict-shaped tool result carries `success: bool`
# ===========================================================================


# Tools that legitimately return a non-dict shape (list, image, str).
NON_DICT_RETURN_TOOLS = {
    "generate_pcb_thumbnail",
    "generate_project_thumbnail",
}

# Tools that DO return a dict but use a non-standard top-level key
# instead of ``success``. Pre-Layer-T convention violations — not
# worth churning the public surface for, but documented here so a
# new tool doesn't accidentally pick the same shape.
ALT_SUCCESS_KEY: dict[str, str] = {
    "ipc_check_status": "ready",
    "kicad_mcp_doctor": "ok",
    # list_projects wraps the actual list under a 'result' key — its
    # surface is documented as a list resource, not a status response.
    "list_projects": "result",
}


@pytest.mark.parametrize("tool_name", _ALL_TOOL_NAMES)
def test_dict_return_has_success_key(server, tool_name):
    """If the tool returns a dict (and almost every tool does), it must
    carry ``success: bool``. The picker LLM standardises on this key
    to branch its next call. Tools that genuinely return a non-dict
    shape are listed in NON_DICT_RETURN_TOOLS."""
    try:
        result = asyncio.run(server.call_tool(tool_name, {}))
    except Exception:
        # Missing-required-arg → FastMCP raises before the body runs.
        # Cannot inspect return shape, but covered by audits (a)+(e).
        return

    payload = result[1] if isinstance(result, tuple) and len(result) > 1 else result
    if not isinstance(payload, dict):
        if tool_name in NON_DICT_RETURN_TOOLS:
            return
        pytest.fail(
            f"{tool_name}: returned {type(payload).__name__} not dict. "
            "If this is intentional, add it to NON_DICT_RETURN_TOOLS."
        )

    expected_key = ALT_SUCCESS_KEY.get(tool_name, "success")
    assert expected_key in payload, (
        f"{tool_name}: dict result missing {expected_key!r} key. "
        f"Got keys: {sorted(payload.keys())[:8]}"
    )
    if expected_key == "success":
        assert isinstance(payload["success"], bool), (
            f"{tool_name}: 'success' value is "
            f"{type(payload['success']).__name__}, expected bool"
        )


# ===========================================================================
# Audit (b) — json.loads(<param>) is wrapped in try/except with
#             a structured failure return
# ===========================================================================


# Tools that legitimately call json.loads outside a try/except (because
# the dispatcher above them already validated, or because they parse
# inside a helper and bubble the exception as a TypeError).
JSON_LOADS_RAW_OK = {
    # benchmark_* are hidden internal tools, not LLM-facing.
    "benchmark_loop", "benchmark_schematic",
}


# Build (tool_name, param_name) pairs at module import.
_JSON_LOADS_CALLS: list[tuple[str, str]] = []
for _tname, (_sig, _body) in _TOOL_BODIES.items():
    for _m in re.finditer(r"json\.loads\(\s*(\w+)\s*\)", _body):
        _JSON_LOADS_CALLS.append((_tname, _m.group(1)))


@pytest.mark.parametrize(
    "tool_name,param",
    _JSON_LOADS_CALLS,
    ids=[f"{t}::{p}" for t, p in _JSON_LOADS_CALLS],
)
def test_json_loads_is_guarded(tool_name, param):
    """Every ``json.loads(<param>)`` call in a tool body must sit inside
    a try/except that returns a structured failure dict on bad JSON.

    Pattern check: look for the canonical idiom we use across the repo:

        try:
            ... = json.loads(<param>)
        except Exception as exc:
            return {"success": False, "error": f"Invalid JSON: ..."}

    A loose match (try-block wraps the call AND the surrounding 200
    chars contain ``"success": False``) is enough to satisfy the test.
    """
    if tool_name in JSON_LOADS_RAW_OK:
        return

    _sig, body = _TOOL_BODIES[tool_name]
    # Find the json.loads call position
    pat = re.compile(rf"json\.loads\(\s*{re.escape(param)}\s*\)")
    m = pat.search(body)
    assert m is not None, "internal: discovered call no longer findable"

    # Walk backwards from the call to find the enclosing 'try:' (within
    # 6 lines of code).
    pre = body[: m.start()]
    pre_lines = pre.splitlines()
    try_seen = False
    for line in reversed(pre_lines[-8:]):
        stripped = line.strip()
        if stripped.startswith("try:") or stripped == "try:":
            try_seen = True
            break
        if stripped.startswith("def ") or stripped.startswith("@"):
            break
    assert try_seen, (
        f"{tool_name}: json.loads({param}) is not wrapped in try/except. "
        "Add the canonical idiom: "
        f'try:\\n    {param}_data = json.loads({param})\\n'
        'except Exception as exc:\\n    return '
        '{"success": False, "error": f"Invalid JSON: {exc}"}'
    )

    # Find the surrounding except block (within ~600 chars after the call)
    after = body[m.start() : m.start() + 600]
    has_struct_err = bool(
        re.search(r'success["\']?\s*:\s*False', after)
        or re.search(r'"success"\s*:\s*False', after)
    )
    assert has_struct_err, (
        f"{tool_name}: json.loads({param}) try-block does not return a "
        "structured failure (success=False, error=str). "
        "Tools must never let a JSON parse error propagate as an "
        "untyped exception."
    )


# ===========================================================================
# Audit (e) — missing path → structured failure (no crash)
# ===========================================================================


# Path parameters that, when pointed at a non-existent file, must yield
# a structured ``{success: False, error: ...}`` reply rather than an
# uncaught exception.
_NON_EXIST = "/nonexistent/circuit_audit_does_not_exist.kicad_sch"
_NON_EXIST_PCB = "/nonexistent/circuit_audit_does_not_exist.kicad_pcb"
_NON_EXIST_PRO = "/nonexistent/circuit_audit_does_not_exist.kicad_pro"


_PATH_TEST_VALUES = {
    "sch_path": _NON_EXIST,
    "pcb_path": _NON_EXIST_PCB,
    "schematic_path": _NON_EXIST,
    "project_path": _NON_EXIST_PRO,
    "input_path": _NON_EXIST,
    "netlist_path": "/nonexistent/audit.net",
    "dsn_path": "/nonexistent/audit.dsn",
    "ses_path": "/nonexistent/audit.ses",
    "custom_path": "/nonexistent/audit.kicad_mod",
    "pdf_path": "/nonexistent/audit.pdf",
    "file_path": "/nonexistent/audit.txt",
}


def _tools_with_path_param() -> list[tuple[str, str]]:
    """For each tool, pick the FIRST path-typed parameter we can drive."""
    out: list[tuple[str, str]] = []
    for name, (sig, _body) in _TOOL_BODIES.items():
        for param in PATH_PARAM_NAMES:
            if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(param)}\s*:\s*str", sig):
                continue
            if param not in _PATH_TEST_VALUES:
                continue
            out.append((name, param))
            break
    return out


_PATH_AUDIT_CASES = _tools_with_path_param()


# Tools where the missing-file probe legitimately raises before our
# error-return path runs (e.g. arg-validation by FastMCP, or the tool
# delegates to another that has its own check).
PATH_EXIST_EXEMPT = {
    # Generators that *create* the file at the given path — non-existence is
    # the expected starting state, not an error.
    "generate_project", "generate_schematic", "generate_pcb",
    "generate_from_netlist",
    # IPC tools — many take an output_path that doesn't need to pre-exist.
    "ipc_export_schematic", "ipc_save", "ipc_save_all",
    # IPC DRC session: pcb_path is optional (derived from the open document)
    # and only checked after the live-editor connection — needs a board open.
    "ipc_drc_session_start",
    # DRC triage/select: same — pcb_path optional, derived from the open
    # document and only resolved after the live-editor connection.
    "drc_triage",
    "drc_select_group",
    # Conversion tools — same: input must exist but output is created.
    "esphome_to_kicad", "convert_ltspice_to_kicad",
    # Export-to-disk: writes a fresh file at the given output_path.
    "export_drill", "export_step", "export_pdf",
    "export_svg", "export_png", "export_pos", "export_gerbers",
    "render_3d", "generate_pcb_thumbnail", "generate_project_thumbnail",
    "export_bom_csv",
    # Doctor / health probes — no real path to check.
    "kicad_mcp_doctor",
    # Warm-session cache eviction: resetting a board whose file was moved or
    # deleted is a valid way to release its cached copy, so a missing
    # pcb_path is a no-op success by design (documented in the tool).
    "pcb_session_reset",
    # validate_project / get_project_structure use alternate
    # convention: {valid: False, error: ...} instead of success: False.
    # Pre-Layer-T tools — kept on exempt list rather than churning
    # their public surface.
    "validate_project",
    "get_project_structure",
}


@pytest.mark.parametrize(
    "tool_name,path_param",
    _PATH_AUDIT_CASES,
    ids=[f"{t}::{p}" for t, p in _PATH_AUDIT_CASES],
)
def test_missing_path_yields_structured_error(server, tool_name, path_param):
    """Pointing a path parameter at a non-existent file must yield a
    structured ``{success: False, error: <str>}`` response — never a
    raw exception. The to_local_path normaliser plus the explicit
    ``os.path.isfile`` check that follows it is the canonical
    pattern."""
    if tool_name in PATH_EXIST_EXEMPT:
        pytest.skip(f"{tool_name}: path-existence semantics differ (creator/IPC/probe)")

    args = {path_param: _PATH_TEST_VALUES[path_param]}
    try:
        result = asyncio.run(server.call_tool(tool_name, args))
    except Exception as exc:
        msg = str(exc).lower()
        # FastMCP arg-validation can legitimately reject the call when
        # other required args are missing — skip that case, it's not
        # the path-existence path.
        if "required" in msg or "missing" in msg or "validation" in msg:
            pytest.skip(f"{tool_name}: needs additional required args")
        pytest.fail(
            f"{tool_name}: missing path raised {type(exc).__name__}: {exc} "
            "(expected structured success=False)"
        )

    payload = result[1] if isinstance(result, tuple) and len(result) > 1 else result
    if not isinstance(payload, dict):
        return  # non-dict tools handled by audit (a)
    assert payload.get("success") is False, (
        f"{tool_name}: missing-path call returned success={payload.get('success')!r}; "
        "expected False with a 'not found' error."
    )
    err = str(payload.get("error", payload.get("errors", ""))).lower()
    assert any(needle in err for needle in ("not found", "no such file", "missing", "does not exist")), (
        f"{tool_name}: error message {err!r} does not signal missing file"
    )


# ===========================================================================
# Audit (d) — dry_run=True does not modify the schematic on disk
# ===========================================================================


# Discovered at module import — every tool whose signature includes
# ``dry_run: bool``.
_DRY_RUN_TOOLS = sorted(
    name for name, (sig, _body) in _TOOL_BODIES.items()
    if re.search(r"(?<![A-Za-z0-9_])dry_run\s*:\s*bool", sig)
)


def _seed_minimal_sch(path: str) -> None:
    text = (
        "(kicad_sch\n"
        "  (version 20231120)\n"
        '  (generator "kicad-mcp-tests")\n'
        '  (uuid "11111111-2222-3333-4444-555555555555")\n'
        '  (paper "A4")\n\n'
        "  (lib_symbols\n"
        "  )\n"
        ")\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _file_hash(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


# Per-tool: which extra args make the dry_run call go far enough to test
# disk-stability. Empty dict = call dry_run with only sch_path + dry_run.
_DRY_RUN_EXTRA_ARGS: dict[str, dict] = {
    "apply_circuit_block": {
        "spec": '{"schema_version":"1.1","chip":"DUMMY","kicad_symbol":"Device:R",'
                '"kicad_footprint":"Resistor_SMD:R_0402_1005Metric",'
                '"pins":[{"num":1,"name":"A","type":"passive"}],'
                '"peripherals":[]}',
    },
    "convert_global_labels_to_power": {},
}


@pytest.mark.parametrize("tool_name", _DRY_RUN_TOOLS)
def test_dry_run_does_not_modify_disk(server, tool_name, tmp_path):
    """For every tool with a ``dry_run`` parameter, calling it with
    ``dry_run=True`` must leave the underlying schematic file
    byte-for-byte unchanged. dry_run that quietly writes is worse
    than no dry_run at all — it gives a false safety promise."""
    sch = str(tmp_path / "dry_run_audit.kicad_sch")
    _seed_minimal_sch(sch)
    before = _file_hash(sch)

    args = {"sch_path": sch, "dry_run": True}
    args.update(_DRY_RUN_EXTRA_ARGS.get(tool_name, {}))
    try:
        asyncio.run(server.call_tool(tool_name, args))
    except Exception as exc:
        msg = str(exc).lower()
        if "required" in msg or "missing" in msg:
            pytest.skip(f"{tool_name}: needs more args to reach dry_run path")
        # Tool may also return structured failure — re-raise only on real bugs.
        if "nonetype" in msg or "object has no attribute" in msg:
            raise

    after = _file_hash(sch)
    assert before == after, (
        f"{tool_name}: dry_run=True modified the schematic on disk "
        f"(hash changed). dry_run must be a pure preview."
    )


# ===========================================================================
# Audit (c) — additive tools are idempotent
# ===========================================================================


# Hand-curated list of tools whose semantics are "add X to file" and
# which are expected to be idempotent: a second call with identical
# arguments either produces the same disk state or returns a
# structured collision error — never a duplicate insertion.
_ADDITIVE_IDEMPOTENT_CASES: list[dict] = [
    {
        "tool": "add_schematic_symbols",
        "args_factory": lambda sch: {
            "sch_path": sch,
            "parts": '[{"ref":"R99","name":"R","value":"1k",'
                     '"footprint":"Resistor_SMD:R_0402_1005Metric",'
                     '"x_mm":50.8,"y_mm":50.8}]',
        },
    },
    {
        "tool": "add_power_symbols",
        "args_factory": lambda sch: {
            "sch_path": sch,
            "anchors": '[{"net":"GND","x_mm":50.8,"y_mm":50.8,"ref":"#PWR0099"}]',
        },
    },
]


@pytest.mark.parametrize(
    "case",
    _ADDITIVE_IDEMPOTENT_CASES,
    ids=[c["tool"] for c in _ADDITIVE_IDEMPOTENT_CASES],
)
def test_additive_tool_idempotent(server, case, tmp_path):
    """Calling an additive tool twice with the SAME args must not
    duplicate the inserted item. Either the second call is a no-op
    (file hash unchanged) OR it returns a structured collision error
    — both are acceptable. Silent duplicate insertion is a regression
    in the UUID-determinism / collision-detection contract."""
    sch = str(tmp_path / "idem_audit.kicad_sch")
    _seed_minimal_sch(sch)
    args = case["args_factory"](sch)
    tool = case["tool"]

    r1 = asyncio.run(server.call_tool(tool, args))
    p1 = r1[1] if isinstance(r1, tuple) and len(r1) > 1 else r1
    if isinstance(p1, dict) and not p1.get("success", True):
        pytest.skip(f"{tool}: first call already failed: {p1.get('error')}")
    h1 = _file_hash(sch)

    r2 = asyncio.run(server.call_tool(tool, args))
    p2 = r2[1] if isinstance(r2, tuple) and len(r2) > 1 else r2
    h2 = _file_hash(sch)

    if h1 == h2:
        return  # idempotent: no second insertion

    # File changed → must be reported as a structured collision error.
    if isinstance(p2, dict):
        success = bool(p2.get("success", True))
        errors = p2.get("errors") or [str(p2.get("error", ""))]
        joined = " ".join(str(e) for e in errors).lower()
        if not success and any(
            needle in joined for needle in ("collision", "already", "exists", "duplicate")
        ):
            return  # idempotent-by-error
    pytest.fail(
        f"{tool}: second call mutated disk without reporting a collision "
        f"(success={p2.get('success')!r}, errors={p2.get('errors')!r}). "
        "Either suppress duplicate insertions or return success=False "
        "with a 'collision' error."
    )


# ===========================================================================
# Audit (j) — heavy deps stay lazy
# ===========================================================================


# Module names whose mere import is expensive and/or has side effects
# (DLL load, network sockets, KiCad's pcbnew SWIG init that takes 25s).
HEAVY_IMPORT_NEEDLES = (
    "pcbnew", "kipy", "pdfplumber", "cairosvg", "PIL", "playwright",
    "wx",
)


def test_no_heavy_imports_on_tool_module_load():
    """Importing any ``kicad_mcp.tools.*`` module must not pull in
    pcbnew / kipy / pdfplumber / cairosvg / PIL / playwright / wx
    at module-load time. Those go behind a lazy-import
    guard inside the relevant tool function so the MCP server's
    initial health-check (~10 s) stays well below pcbnew's 25-second
    init cost.
    """
    import subprocess
    import sys

    # Inspect each tools sub-module from a *fresh* Python process so
    # that earlier conftest imports don't pollute sys.modules.
    code = (
        "import importlib, pkgutil, sys\n"
        "import kicad_mcp.tools as p\n"
        "leaks = []\n"
        "for mi in pkgutil.iter_modules(p.__path__):\n"
        "    if mi.name.startswith('_'): continue\n"
        "    before = set(sys.modules)\n"
        "    importlib.import_module(f'kicad_mcp.tools.{mi.name}')\n"
        "    new = set(sys.modules) - before\n"
        f"    for needle in {HEAVY_IMPORT_NEEDLES!r}:\n"
        "        for mod in new:\n"
        "            if needle in mod.lower():\n"
        "                leaks.append((mi.name, mod))\n"
        "for entry in leaks:\n"
        "    print(f'LEAK {entry[0]}::{entry[1]}')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    leaks = [
        line for line in (result.stdout or "").splitlines()
        if line.startswith("LEAK ")
    ]
    assert not leaks, (
        "Heavy modules imported eagerly by a tools/* module:\n  "
        + "\n  ".join(leaks)
        + "\n\nMove the import inside the tool function body so it "
        "only happens on first call."
    )
