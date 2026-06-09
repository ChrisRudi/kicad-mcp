# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the smaller IPC-tool changes shipped this session.

Covers:
  * Phase 1 — ``ipc_run_erc`` now returns a structured stub instead of
    firing a RunAction that has no chance of working in 10.0.x.
  * Phase 3 — ``ipc_export_schematic`` argument validation (format,
    netlist_format).
  * Phase 4 — ``ipc_revert(doc_type="schematic")`` falls back to the
    Close+Open path; verify it degrades gracefully when kipy doesn't
    expose those proto messages.
"""


# pylint: disable=no-name-in-module  # generated kipy protobuf modules
from __future__ import annotations

import pytest

from kicad_mcp.tools import ipc_tools


@pytest.fixture
def mcp_with_ipc_tools():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    ipc_tools.register_ipc_tools(mcp)
    return mcp


def _call(mcp, name, **kwargs):
    import asyncio

    async def _do():
        result = await mcp.call_tool(name, kwargs)
        return result[1] if isinstance(result, tuple) and len(result) > 1 else result

    return asyncio.run(_do())


# ---------------------------------------------------------------------------
# Phase 1 — ipc_run_erc stub
# ---------------------------------------------------------------------------


class TestIpcRunErcStub:
    def test_returns_structured_stub(self, mcp_with_ipc_tools):
        out = _call(mcp_with_ipc_tools, "ipc_run_erc")
        assert out["success"] is False
        assert out["stub"] is True
        assert out["use_instead"] == "run_erc"
        assert "2077" in out["tracking_issue"]


# ---------------------------------------------------------------------------
# Phase 3 — ipc_export_schematic argument validation
# ---------------------------------------------------------------------------


class TestExportSchematicArgs:
    def test_unknown_format(self, mcp_with_ipc_tools, monkeypatch):
        # Short-circuit the auto-open hook so the format-check fires.
        monkeypatch.setattr(ipc_tools, "_require_editor", lambda *_a, **_kw: None)
        out = _call(
            mcp_with_ipc_tools,
            "ipc_export_schematic",
            output_path="/tmp/x.foo",
            format="not-a-format",
        )
        assert out["success"] is False
        assert "unknown format" in out["error"]

    def test_unknown_netlist_format(self, mcp_with_ipc_tools, monkeypatch):
        pytest.importorskip("kipy")

        # Build a *real* DocumentSpecifier so CopyFrom accepts it; the
        # netlist_format validation is downstream of the proto wiring,
        # so we have to get past that wiring in the test.
        from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
            DocumentSpecifier,
            DocumentType,
        )
        real_doc = DocumentSpecifier()
        real_doc.type = DocumentType.DOCTYPE_SCHEMATIC

        monkeypatch.setattr(ipc_tools, "_require_editor", lambda *_a, **_kw: None)

        class _FakeClient:
            def get_open_documents(self, _doc_type):
                return [real_doc]

        import kipy  # type: ignore
        monkeypatch.setattr(kipy, "KiCad", lambda *_a, **_kw: _FakeClient())

        out = _call(
            mcp_with_ipc_tools,
            "ipc_export_schematic",
            output_path="/tmp/x.net",
            format="netlist",
            netlist_format="bogus-not-real",
        )
        assert out["success"] is False
        assert (
            "netlist_format" in out["error"]
            or "kicad_sexpr" in out["error"]
        )


# ---------------------------------------------------------------------------
# Phase 4 — Close+Open SCH revert when proto messages missing
# ---------------------------------------------------------------------------


class TestSchRevertCloseOpenPathway:
    """``ipc_revert(doc_type="schematic")`` falls back to a Close+Open
    pair through ``API_HANDLER_COMMON``. The Close/Open proto messages
    are not yet shipped with the kipy bundled in KiCad 10.0.x — the
    tool must report a clean structured error rather than crash.

    The Close+Open helper is a closure inside ``register_ipc_tools``,
    so we exercise it via the public tool.
    """

    def test_kipy_unreachable_returns_clean_error(
        self, mcp_with_ipc_tools, monkeypatch
    ):
        # Bypass the auto-open hook so we land in the Close+Open helper.
        monkeypatch.setattr(ipc_tools, "_require_editor", lambda *_a, **_kw: None)

        if pytest.importorskip("kipy"):
            import kipy  # type: ignore

            def _fail(*_a, **_kw):
                raise RuntimeError("Failed to connect to KiCad")

            monkeypatch.setattr(kipy, "KiCad", _fail)

        out = _call(mcp_with_ipc_tools, "ipc_revert", doc_type="schematic")
        assert out["success"] is False
        # Either the connection errored, or kipy doesn't expose the
        # Open/Close commands — both are acceptable degradation paths.
        text = out.get("error", "")
        assert any(
            kw in text
            for kw in ("Cannot reach", "does not expose", "kipy", "schematic")
        ), f"unexpected error text: {text!r}"
