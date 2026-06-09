# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``update_pcb_from_schematic`` — the F8-headless equivalent."""

from __future__ import annotations

import asyncio
import re
import textwrap

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


PCB_EXISTING = textwrap.dedent(
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
    \t(footprint "Resistor_SMD:R_0402"
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
    \t(footprint "Resistor_SMD:R_0402"
    \t\t(layer "F.Cu")
    \t\t(uuid "00000000-0000-0000-0000-000000000002")
    \t\t(at 20.0 10.0 0.0)
    \t\t(property "Reference" "R2"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.SilkS")
    \t\t)
    \t\t(property "Value" "1k"
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


# Schematic netlist text — already in kicadsexpr format (what kicad-cli
# would normally produce). We bypass the kicad-cli call via monkey-patch.
# Scenarios covered: R1 value changed (10k → 4k7), R2 footprint changed
# (R_0402 → R_0603), R3 added entirely, R0 orphaned (in PCB but not sch
# — actually R0 doesn't exist; we test orphan via remove_orphans).
NETLIST_TEXT = textwrap.dedent(
    """\
    (export (version "E")
      (design)
      (components
        (comp (ref "R1")
          (value "4k7")
          (footprint "Resistor_SMD:R_0402")
          (libsource (lib "Device") (part "R")))
        (comp (ref "R2")
          (value "1k")
          (footprint "Resistor_SMD:R_0603")
          (libsource (lib "Device") (part "R")))
        (comp (ref "R3")
          (value "100k")
          (footprint "Resistor_SMD:R_0402")
          (libsource (lib "Device") (part "R")))
      )
      (nets
        (net (code "1") (name "VCC")
          (node (ref "R1") (pin "1"))
          (node (ref "R2") (pin "1"))
        )
        (net (code "2") (name "GND")
          (node (ref "R3") (pin "1"))
        )
      )
    )
    """
)


KICAD_MOD_R_0402 = textwrap.dedent(
    """\
    (footprint "R_0402"
      (layer "F.Cu")
      (at 0 0)
      (property "Reference" "REF**"
        (at 0 0 0)
        (layer "F.SilkS"))
      (property "Value" "Val**"
        (at 0 0 0)
        (layer "F.Fab"))
      (pad "1" smd rect (at -0.5 0) (size 0.5 0.6) (layers "F.Cu" "F.Mask" "F.Paste"))
      (pad "2" smd rect (at 0.5 0) (size 0.5 0.6) (layers "F.Cu" "F.Mask" "F.Paste"))
    )
    """
)
KICAD_MOD_R_0603 = textwrap.dedent(
    """\
    (footprint "R_0603"
      (layer "F.Cu")
      (at 0 0)
      (property "Reference" "REF**"
        (at 0 0 0)
        (layer "F.SilkS"))
      (property "Value" "Val**"
        (at 0 0 0)
        (layer "F.Fab"))
      (pad "1" smd rect (at -0.8 0) (size 0.8 0.95) (layers "F.Cu" "F.Mask" "F.Paste"))
      (pad "2" smd rect (at 0.8 0) (size 0.8 0.95) (layers "F.Cu" "F.Mask" "F.Paste"))
    )
    """
)


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "update.kicad_pcb"
    p.write_text(PCB_EXISTING, encoding="utf-8")
    return str(p)


@pytest.fixture
def sch_path(tmp_path):
    # We do not actually need a real .kicad_sch since we monkey-patch
    # the netlist extraction — but the file must exist for the path
    # check.
    p = tmp_path / "sch.kicad_sch"
    p.write_text("(kicad_sch)\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def library_root(tmp_path):
    """Build a fake KiCad library root with two footprint definitions."""
    lib_root = tmp_path / "lib"
    pretty = lib_root / "Resistor_SMD.pretty"
    pretty.mkdir(parents=True)
    (pretty / "R_0402.kicad_mod").write_text(
        KICAD_MOD_R_0402, encoding="utf-8",
    )
    (pretty / "R_0603.kicad_mod").write_text(
        KICAD_MOD_R_0603, encoding="utf-8",
    )
    return str(lib_root)


@pytest.fixture
def mcp_server():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    ppt.register_pcb_patch_tools(mcp)
    return mcp


@pytest.fixture
def stub_netlist(monkeypatch):
    """Bypass the kicad-cli invocation by stubbing _extract_netlist_text."""
    monkeypatch.setattr(
        ppt, "_extract_netlist_text", lambda _sch: NETLIST_TEXT,
    )


def _call(mcp, name, **kwargs):
    async def _do():
        result = await mcp.call_tool(name, kwargs)
        if isinstance(result, tuple):
            return result[1] if len(result) > 1 else result[0]
        return result
    return asyncio.run(_do())


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Diff scenarios
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_reports_diff(
        self, mcp_server, pcb_path, sch_path, library_root, stub_netlist,
    ):
        before = _read(pcb_path)
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path, schematic_path=sch_path,
            library_root=library_root, dry_run=True,
        )
        assert out["success"] is True
        assert out["dry_run"] is True
        assert out["added"] == ["R3"]
        assert {"ref": "R1", "from": "10k", "to": "4k7"} in out["updated_values"]
        assert {
            "ref": "R2", "from": "Resistor_SMD:R_0402",
            "to": "Resistor_SMD:R_0603",
        } in out["updated_footprints"]
        # File unchanged
        assert _read(pcb_path) == before


class TestApplyChanges:
    def test_apply_value_update(
        self, mcp_server, pcb_path, sch_path, library_root, stub_netlist,
    ):
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path, schematic_path=sch_path,
            library_root=library_root,
            add_new=False, update_footprints=False, sync_nets=False,
        )
        assert out["success"] is True
        text = _read(pcb_path)
        assert '(property "Value" "4k7"' in text
        # R2's value is unchanged.
        assert '(property "Value" "1k"' in text

    def test_apply_footprint_swap(
        self, mcp_server, pcb_path, sch_path, library_root, stub_netlist,
    ):
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path, schematic_path=sch_path,
            library_root=library_root,
            add_new=False, update_values=False, sync_nets=False,
        )
        assert out["success"] is True
        text = _read(pcb_path)
        # R2 was R_0402, must now be R_0603.
        # The footprint *header* lib id is rewritten via _patch_loaded_footprint
        # though it preserves position. We assert the new pad layout exists.
        assert re.search(
            r'\(property "Reference" "R2"[\s\S]*?'
            r'\(pad "1"[^()]*?\(at -0\.8 0\)',
            text,
        ), "R2's pad layout must reflect the R_0603 library entry"

    def test_apply_add_new(
        self, mcp_server, pcb_path, sch_path, library_root, stub_netlist,
    ):
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path, schematic_path=sch_path,
            library_root=library_root,
            update_values=False, update_footprints=False,
            sync_nets=False,
            stage_position_x_mm=250.0, stage_position_y_mm=50.0,
        )
        assert out["success"] is True
        assert "R3" in out["added"]
        text = _read(pcb_path)
        assert '(property "Reference" "R3"' in text
        # The new footprint sits at the staging origin (first slot).
        assert re.search(
            r'\(footprint[\s\S]*?\(at 250\.000000 50\.000000',
            text,
        )

    def test_orphan_removal_opt_in(
        self, mcp_server, pcb_path, sch_path, library_root, monkeypatch,
    ):
        # Make a schematic that omits R2 entirely.
        sch_only_r1 = textwrap.dedent(
            """\
            (export (version "E")
              (components
                (comp (ref "R1")
                  (value "10k")
                  (footprint "Resistor_SMD:R_0402")
                  (libsource (lib "Device") (part "R")))
              )
              (nets)
            )
            """
        )
        monkeypatch.setattr(
            ppt, "_extract_netlist_text", lambda _sch: sch_only_r1,
        )
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path, schematic_path=sch_path,
            library_root=library_root,
            add_new=False, sync_nets=False,
            remove_orphans=False,
        )
        # Default: R2 is reported as orphan but NOT removed.
        assert out["success"] is True
        assert out["removed"] == []
        assert '(property "Reference" "R2"' in _read(pcb_path)

        # Run again with remove_orphans=True.
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path, schematic_path=sch_path,
            library_root=library_root,
            add_new=False, sync_nets=False,
            remove_orphans=True,
        )
        assert out["success"] is True
        assert out["removed"] == ["R2"]
        assert '(property "Reference" "R2"' not in _read(pcb_path)

    def test_missing_library_reported(
        self, mcp_server, pcb_path, sch_path, library_root, monkeypatch,
    ):
        # Schematic asks for a footprint not in the library.
        sch = textwrap.dedent(
            """\
            (export (version "E")
              (components
                (comp (ref "Q1")
                  (value "BC547")
                  (footprint "Package_TO_SOT_THT:NotARealFP")
                  (libsource (lib "Transistor_BJT") (part "BC547")))
              )
              (nets)
            )
            """
        )
        monkeypatch.setattr(
            ppt, "_extract_netlist_text", lambda _sch: sch,
        )
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path, schematic_path=sch_path,
            library_root=library_root,
            update_values=False, update_footprints=False, sync_nets=False,
        )
        assert out["success"] is True
        assert "Package_TO_SOT_THT:NotARealFP" in out["missing_libraries"]
        assert out["added"] == []


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_pcb(self, mcp_server, tmp_path, sch_path):
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=str(tmp_path / "no.kicad_pcb"),
            schematic_path=sch_path,
        )
        assert out["success"] is False
        assert "pcb not found" in out["error"].lower()

    def test_missing_sch(self, mcp_server, pcb_path, tmp_path):
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path,
            schematic_path=str(tmp_path / "no.kicad_sch"),
        )
        assert out["success"] is False
        assert "schematic not found" in out["error"].lower()

    def test_cli_failure(
        self, mcp_server, pcb_path, sch_path, library_root, monkeypatch,
    ):
        monkeypatch.setattr(
            ppt, "_extract_netlist_text", lambda _sch: None,
        )
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path, schematic_path=sch_path,
            library_root=library_root,
        )
        assert out["success"] is False
        assert "kicad-cli" in out["error"].lower()

    def test_missing_library_root(
        self, mcp_server, pcb_path, sch_path, monkeypatch,
    ):
        monkeypatch.setattr(
            ppt, "_default_kicad_lib_root", lambda: "/no/such/place",
        )
        out = _call(
            mcp_server, "update_pcb_from_schematic",
            pcb_path=pcb_path, schematic_path=sch_path,
        )
        assert out["success"] is False
        assert "library root" in out["error"].lower()
