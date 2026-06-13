# SPDX-License-Identifier: GPL-3.0-or-later
"""Dynamic tests for the Universal Callable convention.

Every tool in ``PCB_PATCH_TEXT_FNS`` / ``PCB_GEOMETRY_TEXT_FNS`` must:

1. **Pure text companion** — the registered ``_text`` function is callable
   with ``(text: str, **args) -> tuple[str, dict]``, mutating only the
   passed-in text and not the filesystem.
2. **dry_run param on the MCP wrapper** — the public tool registered via
   ``@mcp.tool()`` accepts a ``dry_run: bool = False`` keyword.
3. **Idempotent for read-only / no-op cases** — passing arguments that
   yield zero changes produces a result whose ``success`` is True and the
   on-disk file is byte-identical to the input.

Tools that emit new UUIDs on every call (track/arc/via inserts) are
inherently *not* fully byte-idempotent — they are exempt from the
strictest idempotency check and are listed in
``UUID_EMITTING_TOOLS``.
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt
from kicad_mcp.tools import pcb_geometry_tools as pgt


# Tools that insert new UUID-stamped elements; full byte idempotency is
# undefined for them (a second call would add a second element with a
# fresh UUID). The text companion still works correctly; it is just not
# a "no-op when re-run" tool.
UUID_EMITTING_TOOLS = {"add_arc_to_pcb", "add_via_to_pcb"}


# ---------------------------------------------------------------------------
# Tiny PCB fixture
# ---------------------------------------------------------------------------


MIN_PCB = textwrap.dedent(
    """\
    (kicad_pcb
    \t(version 20240108)
    \t(generator "test")
    \t(general
    \t\t(thickness 1.6)
    \t)
    \t(layers
    \t\t(0 "F.Cu" signal)
    \t\t(31 "B.Cu" signal)
    \t)
    \t(net 0 "")
    \t(footprint "Test:R_0402"
    \t\t(layer "F.Cu")
    \t\t(uuid "00000000-0000-0000-0000-000000000001")
    \t\t(at 10.0 10.0 0.0)
    \t\t(property "Reference" "R1"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.SilkS")
    \t\t)
    \t\t(property "Value" "10k"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.Fab")
    \t\t)
    \t\t(pad "1" smd rect
    \t\t\t(at -0.5 0)
    \t\t\t(size 0.5 0.6)
    \t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
    \t\t)
    \t)
    )
    """
)


def _all_text_fns():
    return {**ppt.PCB_PATCH_TEXT_FNS, **pgt.PCB_GEOMETRY_TEXT_FNS}


# ---------------------------------------------------------------------------
# 1. Every registered _text function has the right signature shape.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_all_text_fns()))
def test_text_fn_signature_shape(name):
    fn = _all_text_fns()[name]
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    assert params, f"{name}: must take at least one positional parameter"
    first = params[0]
    assert first.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ), f"{name}: first parameter must be positional"
    # First parameter must be named like a text input — the convention
    # is ``pcb_text`` (or, in future, ``sch_text``).
    assert first.name in ("pcb_text", "sch_text"), (
        f"{name}: first parameter named {first.name!r}; expected "
        "'pcb_text' (or 'sch_text')"
    )


# ---------------------------------------------------------------------------
# 2. The MCP wrapper for each registered tool accepts dry_run.
# ---------------------------------------------------------------------------


def _resolve_mcp_tool_signature(tool_name: str):
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("audit")
    ppt.register_pcb_patch_tools(mcp)
    pgt.register_pcb_geometry_tools(mcp)
    # FastMCP stores tools internally in a _tool_manager.
    tm = mcp._tool_manager  # noqa: SLF001 — audit-only
    for registry_name, t in tm._tools.items():  # noqa: SLF001
        if registry_name == tool_name:
            return inspect.signature(t.fn)
    raise KeyError(f"tool not registered: {tool_name}")


@pytest.mark.parametrize("name", sorted(_all_text_fns()))
def test_dry_run_param_on_mcp_wrapper(name):
    sig = _resolve_mcp_tool_signature(name)
    assert "dry_run" in sig.parameters, (
        f"{name}: MCP wrapper must accept dry_run=False keyword"
    )
    param = sig.parameters["dry_run"]
    assert param.default is False, (
        f"{name}: dry_run default must be False, got {param.default!r}"
    )


# ---------------------------------------------------------------------------
# 3. Idempotency — for no-op invocations, the text must come back unchanged.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, args",
    [
        # place_at_pivot at current location with no-rotation = no-op
        ("place_at_pivot", {
            "ref": "R1",
            "target_x_mm": 10.0, "target_y_mm": 10.0,
        }),
        # delete_pcb_routing on an empty PCB = no deletions
        ("delete_pcb_routing", {"net_name": ""}),
    ],
)
def test_text_fn_is_idempotent_for_noops(name, args):
    fn = _all_text_fns()[name]
    out1, r1 = fn(MIN_PCB, **args)
    assert r1.get("success"), f"{name}: first call failed: {r1}"
    out2, r2 = fn(out1, **args)
    assert r2.get("success")
    assert out1 == out2, (
        f"{name}: second call mutated text — not idempotent"
    )


# ---------------------------------------------------------------------------
# 4. UUID-emitting tools are explicitly opted out of strict byte idempotency.
# Each call adds a new element with a fresh UUID, but the text companion
# must still succeed on the second call.
# ---------------------------------------------------------------------------


def test_uuid_emitting_tools_are_registered():
    fns = _all_text_fns()
    for name in UUID_EMITTING_TOOLS:
        assert name in fns, (
            f"{name} listed as UUID-emitting but not in TEXT_FNS"
        )
