# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal helpers for ``circuit_block_tools``.

Two responsibilities:

1. **Template loader** — reads a ``training/templates/schematic/<id>.json``
   file and extracts its (optional) ``block_definition`` section.
2. **Layer-S invokers** — call the existing patcher tools
   (``add_schematic_symbols`` / ``add_power_symbols`` / ``connect_pins``)
   in-process, returning the same dicts they normally do over the MCP
   wire. This keeps Layer-T orchestration honest: every effect on the
   schematic still flows through the *same* code paths the LLM would
   exercise via direct tool calls.

Implementation note: we use an ephemeral FastMCP container per call to
register the patcher tools, then invoke them via ``call_tool`` (the
same path ``test_sch_patch_tools._call`` uses). This avoids depending
on Layer-S internals beyond their MCP-tool surface.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP


# ---------------------------------------------------------------------------
# Template loader
# ---------------------------------------------------------------------------


def _templates_dir() -> str:
    """Resolve the schematic templates directory, repo-relative."""
    here = os.path.dirname(os.path.abspath(__file__))
    # tools/ → kicad_mcp/ → training/templates/schematic
    return os.path.normpath(os.path.join(
        here, "..", "training", "templates", "schematic"
    ))


def load_template_block_definition(
    template_id: str,
) -> tuple[Optional[dict], Optional[str]]:
    """Load the ``block_definition`` section from a template file.

    Returns ``(block_def, error_message)``. ``block_def`` is None when:
      * the template file is missing → error_message names the path
      * the template has no ``block_definition`` key (stub-only)
        → block_def None, error_message None (caller decides to refuse)
      * the file exists and has a ``block_definition`` → returned dict
    """
    path = os.path.join(_templates_dir(), f"{template_id}.json")
    if not os.path.isfile(path):
        return None, f"Template not found: {path!r}"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        return None, f"Failed to parse {path!r}: {exc}"
    bd = data.get("block_definition")
    if not isinstance(bd, dict) or not bd:
        return None, None
    return bd, None


# ---------------------------------------------------------------------------
# Layer-S invokers
# ---------------------------------------------------------------------------


def _ephemeral_sch_server() -> FastMCP:
    """Build a per-call FastMCP container with sch_patch_tools registered."""
    from kicad_mcp.tools.sch_patch_tools import register_sch_patch_tools
    m = FastMCP("layer_t_adapter")
    register_sch_patch_tools(m)
    return m


def _call_tool_sync(server: FastMCP, name: str, kwargs: dict) -> dict[str, Any]:
    """Synchronous adapter for FastMCP's async call_tool().

    Works in three contexts:
      * plain script (no running event loop): asyncio.run directly
      * pytest harness (asyncio test fixtures): uses a worker thread
        with its own event loop so we don't collide with the outer
        loop
      * inside the live FastMCP server itself, where the parent
        ``apply_circuit_block`` is being awaited by the dispatcher:
        also uses a worker thread

    Mirrors the convention used by ``tests/test_sch_patch_tools.py::_call``.
    """
    def _run() -> Any:
        return asyncio.run(server.call_tool(name, kwargs))

    try:
        # Detect a running event loop — if there is one, we must not call
        # asyncio.run() in the same thread.
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if in_loop:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            result = ex.submit(_run).result()
    else:
        result = _run()

    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    if isinstance(result, dict):
        return result
    # FastMCP may wrap with metadata; defensively unwrap.
    return {"success": False, "error": f"Unexpected call_tool result type: {type(result)}"}


def invoke_add_schematic_symbols(
    sch_path: str, parts: list[dict]
) -> dict[str, Any]:
    """Wrapper around the ``add_schematic_symbols`` Layer-S tool."""
    server = _ephemeral_sch_server()
    return _call_tool_sync(
        server,
        "add_schematic_symbols",
        {"sch_path": sch_path, "parts": json.dumps(parts), "group_id": "circuit_block"},
    )


def invoke_add_power_symbols(
    sch_path: str, anchors: list[dict]
) -> dict[str, Any]:
    """Wrapper around ``add_power_symbols``."""
    server = _ephemeral_sch_server()
    return _call_tool_sync(
        server,
        "add_power_symbols",
        {"sch_path": sch_path, "anchors": json.dumps(anchors), "group_id": "circuit_block"},
    )


def invoke_connect_pins(
    sch_path: str, connections: list[dict], mode: str = "wire"
) -> dict[str, Any]:
    """Wrapper around ``connect_pins`` (Manhattan wires by default)."""
    server = _ephemeral_sch_server()
    return _call_tool_sync(
        server,
        "connect_pins",
        {
            "sch_path": sch_path,
            "connections": json.dumps(connections),
            "mode": mode,
            "group_id": "circuit_block",
        },
    )
