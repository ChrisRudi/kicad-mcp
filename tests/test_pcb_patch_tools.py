# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for kicad_mcp.tools.pcb_patch_tools.

Uses minimal hand-written ``.kicad_pcb`` and ``.net`` fixtures so the tests
do not depend on any real KiCad project on disk and run on any CI host.

The tools all run as plain text-patchers (no SWIG ``pcbnew`` import) except
``rotate_pcb`` which delegates to ``pcbnew``; that test is marked to skip if
the bindings are not importable in the current Python.
"""

import importlib
import re
from pathlib import Path

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt


# ---------------------------------------------------------------------------
# Test fixtures (minimal valid S-expression structures)
# ---------------------------------------------------------------------------

MIN_PCB = """\
(kicad_pcb
\t(version 20240108)
\t(generator "test")
\t(general
\t\t(thickness 1.6)
\t)
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(31 "B.Cu" signal)
\t\t(44 "Edge.Cuts" user)
\t)
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
\t\t(pad "2" smd rect
\t\t\t(at 0.5 0)
\t\t\t(size 0.5 0.6)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t)
\t)
\t(footprint "Test:U_DIP8"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-000000000002")
\t\t(at 20.0 20.0 0.0)
\t\t(property "Reference" "U1"
\t\t\t(at 0 0 0)
\t\t\t(layer "F.SilkS")
\t\t)
\t\t(property "Value" "GenericIC"
\t\t\t(at 0 0 0)
\t\t\t(layer "F.Fab")
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at -1.0 -1.0)
\t\t\t(size 0.6 1.5)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t)
\t\t(pad "2" smd rect
\t\t\t(at -0.6 -1.0)
\t\t\t(size 0.6 1.5)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t)
\t)
)
"""


# Same shape as MIN_PCB but with the footprint-header `(at …)` BEFORE
# `(uuid …)` — that is the order `generate_project` writes. Without this
# fixture the depth-walker header detection in `flip_footprint_to_layer`
# is not exercised on the at-first ordering.
MIN_PCB_AT_FIRST_HEADER = """\
(kicad_pcb
\t(version 20240108)
\t(generator "test")
\t(general
\t\t(thickness 1.6)
\t)
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(31 "B.Cu" signal)
\t\t(44 "Edge.Cuts" user)
\t)
\t(footprint "Test:R_0402"
\t\t(layer "F.Cu")
\t\t(at 10.0 10.0 0.0)
\t\t(uuid "00000000-0000-0000-0000-000000000001")
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
\t\t(pad "2" smd rect
\t\t\t(at 0.5 0)
\t\t\t(size 0.5 0.6)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t)
\t)
)
"""


MIN_NETLIST = """\
(export
\t(version "E")
\t(components
\t\t(comp (ref "R1") (value "10k"))
\t\t(comp (ref "U1") (value "GenericIC"))
\t)
\t(nets
\t\t(net
\t\t\t(code "1")
\t\t\t(name "/SIG_A")
\t\t\t(class "Default")
\t\t\t(node (ref "R1") (pin "1"))
\t\t\t(node (ref "U1") (pin "1"))
\t\t)
\t\t(net
\t\t\t(code "2")
\t\t\t(name "GND")
\t\t\t(class "Default")
\t\t\t(node (ref "R1") (pin "2"))
\t\t\t(node (ref "U1") (pin "2"))
\t\t)
\t)
)
"""


# Minimal placeholder PCB used by resolve_pcb_footprints; the Value carries the
# ``[lib:fp]`` tag the tool searches for. Library root has to be passed
# explicitly (mock).
PLACEHOLDER_PCB = """\
(kicad_pcb
\t(version 20240108)
\t(generator "test")
\t(general (thickness 1.6))
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(31 "B.Cu" signal)
\t)
\t(footprint ""
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-000000000010")
\t\t(at 5.0 5.0 0.0)
\t\t(property "Reference" "C1"
\t\t\t(at 0 0 0)
\t\t\t(layer "F.SilkS")
\t\t)
\t\t(property "Value" "100n [TestLib:C_0402]"
\t\t\t(at 0 0 0)
\t\t\t(layer "F.Fab")
\t\t)
\t)
)
"""


# Minimal .kicad_mod content used to verify the resolver. The lib name maps to
# a directory ``TestLib.pretty/`` in the mocked library root.
MIN_KICAD_MOD = """\
(footprint "C_0402"
\t(version 20240108)
\t(generator "test")
\t(layer "F.Cu")
\t(at 0 0 0)
\t(property "Reference" "REF**"
\t\t(at 0 -1 0)
\t\t(layer "F.SilkS")
\t)
\t(property "Value" "C_0402"
\t\t(at 0 1 0)
\t\t(layer "F.Fab")
\t)
\t(pad "1" smd rect
\t\t(at -0.5 0)
\t\t(size 0.5 0.6)
\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t)
\t(pad "2" smd rect
\t\t(at 0.5 0)
\t\t(size 0.5 0.6)
\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t)
)
"""


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text(MIN_PCB, encoding="utf-8")
    return str(p)


@pytest.fixture
def pcb_path_at_first(tmp_path):
    """PCB fixture with the footprint-header `(at …)` BEFORE `(uuid …)` —
    matches what `generate_project` emits and exercises the depth-walker
    header detection (the regex-based version missed this order)."""
    p = tmp_path / "board_at_first.kicad_pcb"
    p.write_text(MIN_PCB_AT_FIRST_HEADER, encoding="utf-8")
    return str(p)


@pytest.fixture
def netlist_path(tmp_path):
    p = tmp_path / "board.net"
    p.write_text(MIN_NETLIST, encoding="utf-8")
    return str(p)


@pytest.fixture
def placeholder_pcb_path(tmp_path):
    p = tmp_path / "placeholder.kicad_pcb"
    p.write_text(PLACEHOLDER_PCB, encoding="utf-8")
    return str(p)


@pytest.fixture
def mock_library(tmp_path):
    """Create a minimal KiCad-style library: ``<root>/TestLib.pretty/C_0402.kicad_mod``."""
    lib_dir = tmp_path / "lib"
    pretty = lib_dir / "TestLib.pretty"
    pretty.mkdir(parents=True)
    (pretty / "C_0402.kicad_mod").write_text(MIN_KICAD_MOD, encoding="utf-8")
    return str(lib_dir)


# ---------------------------------------------------------------------------
# Pure-function tests (no MCP server roundtrip)
# ---------------------------------------------------------------------------


class TestParseNetlist:
    def test_node_map_extracted(self):
        node_map, all_nets = ppt._parse_netlist_node_map(MIN_NETLIST)
        assert node_map[("R1", "1")] == "/SIG_A"
        assert node_map[("U1", "2")] == "GND"
        assert sorted(all_nets) == ["/SIG_A", "GND"]

    def test_pins_per_ref_sets(self):
        per_ref = ppt._parse_netlist_pins_per_ref(MIN_NETLIST)
        assert per_ref["R1"] == {"1", "2"}
        assert per_ref["U1"] == {"1", "2"}

    def test_empty_netlist_returns_empty(self):
        node_map, all_nets = ppt._parse_netlist_node_map("(export (nets ))")
        assert node_map == {}
        assert all_nets == []


class TestPatchPcbNets:
    def test_patch_inserts_net_defs_and_tags(self):
        new_text, n_patched, n_total, n_nets = ppt._patch_pcb_nets(
            MIN_PCB, MIN_NETLIST
        )
        assert n_patched == 4   # all four pads got a net tag
        assert n_total == 4
        assert n_nets == 2
        # Net defs inserted
        assert '(net 0 "")' in new_text
        assert '(net 1 "/SIG_A")' in new_text
        assert '(net 2 "GND")' in new_text
        # Pads carry the right net references
        r1_block = _find_footprint_block(new_text, "R1")
        assert r1_block is not None
        assert '(net 1 "/SIG_A")' in r1_block
        assert '(net 2 "GND")' in r1_block

    def test_unknown_pad_left_alone(self):
        # Add a third pad on R1 that the netlist doesn't reference
        modified = MIN_PCB.replace(
            '(pad "2" smd rect',
            '(pad "3" smd rect (at 1 0) (size 0.5 0.6) '
            '(layers "F.Cu" "F.Mask" "F.Paste"))\n\t\t(pad "2" smd rect',
            1,
        )
        _new_text, n_patched, n_total, _ = ppt._patch_pcb_nets(
            modified, MIN_NETLIST
        )
        # Three pads in R1 (1, 2, 3) + two in U1 = 5 total, but only 4 known
        assert n_total == 5
        assert n_patched == 4

    def test_existing_net_defs_are_preserved(self):
        # PCB already has a net table that uses different indices than the
        # naive 1..N assignment a fresh netlist would produce. Re-running the
        # patcher must NOT duplicate net entries nor change the indices of
        # nets that are already defined — otherwise pads with old indices
        # end up tagged with the wrong name.
        pcb_with_nets = MIN_PCB.replace(
            "\t(layers\n",
            "\t(layers\n",  # no-op anchor; insert defs after layers block
        ).replace(
            ")\n\t(footprint",
            ')\n\t(net 0 "")\n\t(net 5 "/SIG_A")\n\t(net 7 "GND")\n\t(footprint',
            1,
        )
        new_text, _, _, n_added = ppt._patch_pcb_nets(pcb_with_nets, MIN_NETLIST)
        # Top-level net defs (single-tab indent) must each appear exactly once.
        top_defs = re.findall(
            r'^\t\(net (\d+) "([^"]*)"\)\s*$', new_text, re.MULTILINE
        )
        assert top_defs.count(("5", "/SIG_A")) == 1
        assert top_defs.count(("7", "GND")) == 1
        # No /SIG_A or GND under any other index in the top-level table.
        assert {idx for idx, name in top_defs if name == "/SIG_A"} == {"5"}
        assert {idx for idx, name in top_defs if name == "GND"} == {"7"}
        # Pads must reference the EXISTING indices, not freshly assigned ones.
        r1_block = _find_footprint_block(new_text, "R1")
        assert r1_block is not None
        assert '(net 5 "/SIG_A")' in r1_block
        assert '(net 7 "GND")' in r1_block
        # nets_added is the count of NEW nets (zero — both already there).
        assert n_added == 0

    def test_partial_existing_nets_only_appends_missing(self):
        # PCB already has /SIG_A but not GND — the patch must reuse the
        # existing index for /SIG_A and append GND with a fresh index.
        pcb = MIN_PCB.replace(
            ")\n\t(footprint",
            ')\n\t(net 0 "")\n\t(net 1 "/SIG_A")\n\t(footprint',
            1,
        )
        new_text, _, _, n_added = ppt._patch_pcb_nets(pcb, MIN_NETLIST)
        top_defs = re.findall(
            r'^\t\(net (\d+) "([^"]*)"\)\s*$', new_text, re.MULTILINE
        )
        assert ("1", "/SIG_A") in top_defs
        # GND was missing — appended at next index (2).
        assert ("2", "GND") in top_defs
        assert top_defs.count(("1", "/SIG_A")) == 1
        assert top_defs.count(("2", "GND")) == 1
        assert n_added == 1
        r1 = _find_footprint_block(new_text, "R1")
        assert r1 is not None
        assert '(net 1 "/SIG_A")' in r1
        assert '(net 2 "GND")' in r1


class TestPadBlockHelpers:
    def test_iter_pad_blocks_finds_all(self):
        # Footprint of R1 has two pads
        fp_block = _find_footprint_block(MIN_PCB, "R1")
        assert fp_block is not None
        spans = ppt._iter_pad_blocks(fp_block)
        assert len(spans) == 2

    def test_patch_pad_replaces_existing_net(self):
        original = (
            '(pad "1" smd rect (at 0 0) (size 0.5 0.5) '
            '(layers "F.Cu") (net 7 "OLD"))'
        )
        new, changed = ppt._patch_pad_with_net(original, '(net 9 "NEW")')
        assert changed is True
        assert '(net 9 "NEW")' in new
        assert "OLD" not in new

    def test_patch_pad_inserts_when_missing(self):
        original = (
            '(pad "1" smd rect\n'
            '\t\t\t(at 0 0)\n'
            '\t\t\t(size 0.5 0.5)\n'
            '\t\t\t(layers "F.Cu")\n'
            '\t\t)'
        )
        new, changed = ppt._patch_pad_with_net(original, '(net 3 "VCC")')
        assert changed is True
        assert '(net 3 "VCC")' in new

    def test_patch_pad_replaces_string_form_tag_with_indexed(self):
        # A pad on a string-form board (carries (net "OLD") short form).
        # Repatching with an indexed (net N "NEW") tag must replace the
        # whole net subexpression, not append a second one.
        original = (
            '(pad "1" smd rect (at 0 0) (size 0.5 0.5) '
            '(layers "F.Cu") (net "OLD"))'
        )
        new, changed = ppt._patch_pad_with_net(original, '(net 4 "NEW")')
        assert changed is True
        assert new.count("(net ") == 1
        assert '(net 4 "NEW")' in new
        assert "OLD" not in new

    def test_patch_pcb_nets_on_string_form_board(self):
        """End-to-end: an reference-style string-form PCB (no top-level net
        table, pads tagged with the short ``(net "name")``) must come out
        of ``_patch_pcb_nets`` with pads correctly retagged in the
        **same** short form — no synthetic ``(net N "name")`` table at
        the top, no pad rewritten to indexed form."""
        # Take MIN_PCB (indexed-form template) and rewrite every pad's
        # net-less state into a string-form-like skeleton: no net table
        # line at the top. The pads have no (net …) yet — same starting
        # point as fresh placement.
        string_pcb = MIN_PCB
        # Ensure no (net N "name") table entries exist anywhere.
        assert not re.search(r'^\s*\(net\s+\d+\s+"', string_pcb, re.MULTILINE)
        # Drop in one (net "SIG_X") ref so the format detector classifies
        # this as string-form (otherwise it defaults to indexed for blank
        # boards — see pcb_net_format()).
        string_pcb = string_pcb.replace(
            '(layers\n', '(layers\n', 1,
        )
        # Surgical insertion: add a stray (net "EXISTING") inside R1's
        # first pad so the detector sees a string-form ref.
        string_pcb = string_pcb.replace(
            '(layers "F.Cu" "F.Mask" "F.Paste")\n\t\t)',
            '(layers "F.Cu" "F.Mask" "F.Paste")\n\t\t\t(net "EXISTING")\n\t\t)',
            1,
        )

        # Run the netlist patcher.
        new_text, pads_patched, total_pads, nets_added = ppt._patch_pcb_nets(
            string_pcb, MIN_NETLIST,
        )

        # No synthetic table inserted.
        assert not re.search(r'^\s*\(net\s+\d+\s+"', new_text, re.MULTILINE)
        # nets_added is 0 on string-form boards (no table to grow).
        assert nets_added == 0
        # Pads got the short form, not the indexed long form.
        assert '(net "/SIG_A")' in new_text
        assert '(net "GND")' in new_text
        assert not re.search(r'\(net\s+\d+\s+"/SIG_A"\)', new_text)
        assert not re.search(r'\(net\s+\d+\s+"GND"\)', new_text)
        # All four netlist pads were patched.
        assert pads_patched == 4
        assert total_pads == 4

    def test_patch_pad_replaces_indexed_form_tag_with_string(self):
        # The inverse — should also work, since the helper now matches
        # either net-tag form when locating the existing one.
        original = (
            '(pad "1" smd rect (at 0 0) (size 0.5 0.5) '
            '(layers "F.Cu") (net 7 "OLD"))'
        )
        new, changed = ppt._patch_pad_with_net(original, '(net "NEW")')
        assert changed is True
        assert new.count("(net ") == 1
        assert '(net "NEW")' in new
        assert "OLD" not in new


class TestResolveFootprints:
    def test_resolves_tagged_placeholder(self, mock_library):
        new_text, replaced, missing = ppt._resolve_pcb_footprints(
            PLACEHOLDER_PCB, mock_library
        )
        assert replaced == 1
        assert missing == []
        # Tag must be gone, real pads must be present
        assert "[TestLib:C_0402]" not in new_text
        assert '(pad "1"' in new_text
        assert '(pad "2"' in new_text
        # Reference + Value re-applied
        assert '(property "Reference" "C1"' in new_text
        # The clean Value (tag stripped) should be the leading text "100n"
        assert re.search(r'\(property "Value" "100n"', new_text)

    def test_missing_library_reported(self, tmp_path):
        _new_text, replaced, missing = ppt._resolve_pcb_footprints(
            PLACEHOLDER_PCB, str(tmp_path)
        )
        assert replaced == 0
        assert missing == ["TestLib:C_0402"]


class TestValidateFootprints:
    def test_perfect_match_against_min_pcb(self, pcb_path, netlist_path):
        # Use the registered MCP tool's underlying logic
        with open(pcb_path, encoding="utf-8") as fh:
            pcb_text = fh.read()
        with open(netlist_path, encoding="utf-8") as fh:
            net_text = fh.read()
        sch = ppt._parse_netlist_pins_per_ref(net_text)
        pcb = ppt._parse_pcb_pads_per_ref(pcb_text)
        # Both refs known; both have pads {"1","2"}
        assert sch["R1"] == {"1", "2"}
        assert pcb["R1"][1] == {"1", "2"}
        assert sch["U1"] == {"1", "2"}
        assert pcb["U1"][1] == {"1", "2"}


# ---------------------------------------------------------------------------
# MCP-tool-roundtrip tests via FastMCP (registers the tools, then calls them
# the same way the protocol would).
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_with_patch_tools():
    """Spin up a fresh FastMCP instance and register the patch tools on it."""
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    ppt.register_pcb_patch_tools(mcp)
    return mcp


def _call_tool(mcp, name, **kwargs):
    """Synchronously invoke an MCP-registered tool and return its dict."""
    import asyncio

    async def _do():
        result = await mcp.call_tool(name, kwargs)
        # FastMCP returns a tuple (content_list, structured) on >=2.10
        if isinstance(result, tuple):
            return result[1] if len(result) > 1 else result[0]
        return result

    return asyncio.run(_do())


class TestMCPRoundtrip:
    def test_patch_tool_runs(self, mcp_with_patch_tools, pcb_path, netlist_path):
        out = _call_tool(
            mcp_with_patch_tools, "patch_pcb_nets_from_netlist",
            pcb_path=pcb_path, netlist_path=netlist_path,
        )
        assert out["success"] is True
        assert out["pads_patched"] == 4
        assert out["nets_added"] == 2

    def test_validate_tool_runs(self, mcp_with_patch_tools, pcb_path, netlist_path):
        out = _call_tool(
            mcp_with_patch_tools, "validate_footprints",
            pcb_path=pcb_path, netlist_path=netlist_path,
        )
        assert out["success"] is True
        assert out["perfect_match"] == 2
        assert out["mismatches"] == []
        assert out["pcb_only"] == []
        assert out["schematic_only"] == []

    def test_resolve_tool_runs(
        self, mcp_with_patch_tools, placeholder_pcb_path, mock_library
    ):
        out = _call_tool(
            mcp_with_patch_tools, "resolve_pcb_footprints",
            pcb_path=placeholder_pcb_path, library_root=mock_library,
        )
        assert out["success"] is True
        assert out["replaced"] == 1
        assert out["missing"] == []

    def test_patch_tool_missing_pcb(self, mcp_with_patch_tools, tmp_path):
        out = _call_tool(
            mcp_with_patch_tools, "patch_pcb_nets_from_netlist",
            pcb_path=str(tmp_path / "nope.kicad_pcb"),
            netlist_path=str(tmp_path / "nope.net"),
        )
        assert out["success"] is False
        assert "not found" in out["error"].lower()


# ---------------------------------------------------------------------------
# flip_footprint_to_layer — verify the F↔B flip preserves world positions
# of pads via the canonical pcb_local_to_world reader, which is the contract
# KiCad's own reader enforces (FOOTPRINT::Flip with FLIP_DIRECTION::LEFT_RIGHT
# → X-mirror in the file). Pre-2026-05-23 the tool Y-mirrored and produced
# vertically-flipped pads on B.Cu.
# ---------------------------------------------------------------------------


class TestFlipFootprintToLayer:
    def test_flip_to_bcu_preserves_pad_world_positions(
        self, mcp_with_patch_tools, pcb_path,
    ):
        from kicad_mcp.utils.pcb_geometry import pcb_local_to_world

        # R1 on F.Cu, anchor (10,10) 0°, pad 1 local (-0.5, 0), pad 2 (0.5, 0).
        # World positions before flip: (9.5, 10) and (10.5, 10).
        pre_world_p1 = pcb_local_to_world((10.0, 10.0), 0.0, -0.5, 0.0, flipped=False)
        pre_world_p2 = pcb_local_to_world((10.0, 10.0), 0.0, 0.5, 0.0, flipped=False)
        assert pre_world_p1 == pytest.approx((9.5, 10.0))
        assert pre_world_p2 == pytest.approx((10.5, 10.0))

        out = _call_tool(
            mcp_with_patch_tools, "flip_footprint_to_layer",
            pcb_path=pcb_path, ref="R1", target_layer="B.Cu",
        )
        assert out["success"] is True
        assert out["from_layer"] == "F.Cu"
        assert out["to_layer"] == "B.Cu"

        # Re-read the file, parse R1's pads, and verify each pad's world
        # position matches the pre-flip world position once the canonical
        # reader applies its B.Cu X-mirror.
        text = Path(pcb_path).read_text(encoding="utf-8")
        # Layer header must be B.Cu now.
        r1_span_start = text.find('"R1"')
        r1_fp_start = text.rfind("(footprint", 0, r1_span_start)
        # The next (layer "...") inside the footprint block is the FP layer.
        layer_m = re.search(r'\(layer\s+"([^"]+)"\)', text[r1_fp_start:r1_fp_start + 200])
        assert layer_m is not None and layer_m.group(1) == "B.Cu"

        # Pad local coords. Look at the two `(pad "1" smd rect ... (at X Y))`
        # entries within R1's block.
        block_end = text.find(")", text.find("(pad", r1_fp_start))  # crude but enough
        # Read the file slice from R1's footprint header.
        r1_block_end = r1_fp_start
        depth = 0
        for i in range(r1_fp_start, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    r1_block_end = i + 1
                    break
        r1_block = text[r1_fp_start:r1_block_end]
        pad_at = re.findall(
            r'\(pad\s+"([^"]+)"[^()]*(?:\([^()]*\)[^()]*)*?'
            r'\(at\s+([-\d.]+)\s+([-\d.]+)',
            r1_block,
        )
        assert len(pad_at) == 2, f"expected 2 pads in R1, got: {pad_at}"
        pad_local: dict[str, tuple[float, float]] = {
            name: (float(x), float(y)) for name, x, y in pad_at
        }

        # File now holds the X-mirrored local coords: pad 1 (0.5, 0),
        # pad 2 (-0.5, 0). Y unchanged.
        assert pad_local["1"] == pytest.approx((0.5, 0.0))
        assert pad_local["2"] == pytest.approx((-0.5, 0.0))

        # Round-trip through the canonical B.Cu reader → world coords must
        # match the pre-flip world coords. This is the contract.
        post_world_p1 = pcb_local_to_world(
            (10.0, 10.0), 0.0, pad_local["1"][0], pad_local["1"][1], flipped=True,
        )
        post_world_p2 = pcb_local_to_world(
            (10.0, 10.0), 0.0, pad_local["2"][0], pad_local["2"][1], flipped=True,
        )
        assert post_world_p1 == pytest.approx(pre_world_p1)
        assert post_world_p2 == pytest.approx(pre_world_p2)

    def test_flip_idempotent_round_trip(self, mcp_with_patch_tools, pcb_path):
        """Flip F→B then B→F must restore the file byte-for-byte (up to
        whitespace), modulo float-precision rounding."""
        before = Path(pcb_path).read_text(encoding="utf-8")
        _call_tool(
            mcp_with_patch_tools, "flip_footprint_to_layer",
            pcb_path=pcb_path, ref="R1", target_layer="B.Cu",
        )
        _call_tool(
            mcp_with_patch_tools, "flip_footprint_to_layer",
            pcb_path=pcb_path, ref="R1", target_layer="F.Cu",
        )
        after = Path(pcb_path).read_text(encoding="utf-8")
        # Numerical equivalence: ignore precision-formatting differences
        # (the flip emits `:.6f`; the original fixture uses bare integers
        # like ``0``). Normalise every numeric literal — including bare
        # integers — to a rounded float so both representations collapse
        # to the same canonical string.
        def _norm(s: str) -> str:
            return re.sub(
                r"(?<![A-Za-z_])-?\d+(?:\.\d+)?",
                lambda m: f"{float(m.group(0)):.4f}",
                s,
            )
        assert _norm(before) == _norm(after)

    def test_flip_noop_when_already_on_target_layer(
        self, mcp_with_patch_tools, pcb_path,
    ):
        out = _call_tool(
            mcp_with_patch_tools, "flip_footprint_to_layer",
            pcb_path=pcb_path, ref="R1", target_layer="F.Cu",
        )
        assert out["success"] is True
        assert out.get("note", "").startswith("no-op")
        assert out["pads_flipped"] == 0

    def test_anchor_preserved_when_at_precedes_uuid(
        self, mcp_with_patch_tools, pcb_path_at_first,
    ):
        """Regression: the regex-based header detector matched only
        ``(uuid …) (at …)`` and missed ``(at …) (uuid …)`` (what
        ``generate_project`` writes). On that ordering it returned
        ``header_at = None`` and the mirror pass ran over the anchor,
        flipping the footprint's world position. The depth-walker
        detector handles both orders.
        """
        from kicad_mcp.utils.pcb_geometry import pcb_local_to_world

        # Pre-flip: anchor (10, 10), pad 1 (-0.5, 0) → world (9.5, 10).
        pre_world_p1 = pcb_local_to_world(
            (10.0, 10.0), 0.0, -0.5, 0.0, flipped=False,
        )
        pre_world_p2 = pcb_local_to_world(
            (10.0, 10.0), 0.0, 0.5, 0.0, flipped=False,
        )

        out = _call_tool(
            mcp_with_patch_tools, "flip_footprint_to_layer",
            pcb_path=pcb_path_at_first, ref="R1", target_layer="B.Cu",
        )
        assert out["success"] is True
        assert out["to_layer"] == "B.Cu"

        text = Path(pcb_path_at_first).read_text(encoding="utf-8")
        # Footprint header `(at …)` must still read 10.0, 10.0 — NOT -10.0.
        header_m = re.search(
            r'\(footprint[^\n]*\n[^()]*\(layer[^)]+\)\s*\(at\s+'
            r'([-\d.]+)\s+([-\d.]+)', text,
        )
        assert header_m is not None, "expected footprint header (at …)"
        anchor_x = float(header_m.group(1))
        anchor_y = float(header_m.group(2))
        assert anchor_x == pytest.approx(10.0), \
            f"anchor X must remain 10.0, got {anchor_x}"
        assert anchor_y == pytest.approx(10.0)

        # Pads must be X-mirrored locally so the canonical reader (which
        # applies its own X-mirror for B.Cu) lands them at the same world
        # position as before the flip.
        r1_fp_start = text.find("(footprint")
        r1_block_end = _find_block_end_via_depth(text, r1_fp_start)
        r1_block = text[r1_fp_start:r1_block_end]
        pad_at = re.findall(
            r'\(pad\s+"([^"]+)"[^()]*(?:\([^()]*\)[^()]*)*?'
            r'\(at\s+([-\d.]+)\s+([-\d.]+)',
            r1_block,
        )
        pad_local = {n: (float(x), float(y)) for n, x, y in pad_at}
        # Each pad-local X must be flipped.
        assert pad_local["1"] == pytest.approx((0.5, 0.0))
        assert pad_local["2"] == pytest.approx((-0.5, 0.0))
        # And the world position via the canonical B.Cu reader matches.
        post_world_p1 = pcb_local_to_world(
            (10.0, 10.0), 0.0, pad_local["1"][0], pad_local["1"][1],
            flipped=True,
        )
        post_world_p2 = pcb_local_to_world(
            (10.0, 10.0), 0.0, pad_local["2"][0], pad_local["2"][1],
            flipped=True,
        )
        assert post_world_p1 == pytest.approx(pre_world_p1)
        assert post_world_p2 == pytest.approx(pre_world_p2)

    def test_flip_idempotent_round_trip_at_first(
        self, mcp_with_patch_tools, pcb_path_at_first,
    ):
        """Same idempotency contract as the at-second fixture: flip F→B
        then B→F must restore the file (numerically). Guards against
        regressions where the at-first path mirrored the anchor on the
        forward pass and then mirrored it AGAIN on the reverse pass,
        coincidentally restoring the X but corrupting any rotation."""
        before = Path(pcb_path_at_first).read_text(encoding="utf-8")
        _call_tool(
            mcp_with_patch_tools, "flip_footprint_to_layer",
            pcb_path=pcb_path_at_first, ref="R1", target_layer="B.Cu",
        )
        _call_tool(
            mcp_with_patch_tools, "flip_footprint_to_layer",
            pcb_path=pcb_path_at_first, ref="R1", target_layer="F.Cu",
        )
        after = Path(pcb_path_at_first).read_text(encoding="utf-8")

        def _norm(s: str) -> str:
            return re.sub(
                r"(?<![A-Za-z_])-?\d+(?:\.\d+)?",
                lambda m: f"{float(m.group(0)):.4f}",
                s,
            )
        assert _norm(before) == _norm(after)


def _find_block_end_via_depth(text: str, start: int) -> int:
    """Helper used only by the at-first fixture tests above."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


# ---------------------------------------------------------------------------
# _patch_fp_pose — pad rotation must be additive to the library-rotation,
# not overwriting. Pre-fix behaviour: every pad's rot was overwritten with
# the footprint rotation, destroying any non-zero library rotation
# (chamfered QFN corner pads, 45°-rotated SMT pads, etc.).
# ---------------------------------------------------------------------------


PCB_WITH_ROTATED_LIB_PAD = """\
(kicad_pcb
\t(version 20240108)
\t(generator "test")
\t(general (thickness 1.6))
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(31 "B.Cu" signal)
\t)
\t(footprint "Test:QFN_corner"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-000000000003")
\t\t(at 0.0 0.0 0)
\t\t(property "Reference" "U_QFN"
\t\t\t(at 0 0 0)
\t\t\t(layer "F.SilkS")
\t\t)
\t\t(property "Value" "QFN"
\t\t\t(at 0 0 0)
\t\t\t(layer "F.Fab")
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at -1.0 -1.0 45)
\t\t\t(size 0.6 0.4)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t)
\t\t(pad "2" smd rect
\t\t\t(at 1.0 -1.0)
\t\t\t(size 0.6 0.4)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t)
\t)
)
"""


class TestPatchFpPoseAdditiveRotation:
    def test_rotation_is_additive_to_lib_pad_rot(self):
        # Footprint header rot 0° → rotate to 30°. Pad 1 has lib-rot 45°,
        # so its on-disk rot after the patch must be 45 + 30 = 75°. Pad 2
        # has no rot tag (= 0°), so post-rotate must read 30°.
        new_block, _n_pads = ppt._patch_fp_pose(
            PCB_WITH_ROTATED_LIB_PAD,
            new_anchor=(0.0, 0.0), new_rot=30.0,
        )
        pad1_m = re.search(
            r'\(pad\s+"1"[^()]*(?:\([^()]*\)[^()]*)*?'
            r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)',
            new_block,
        )
        pad2_m = re.search(
            r'\(pad\s+"2"[^()]*(?:\([^()]*\)[^()]*)*?'
            r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)',
            new_block,
        )
        assert pad1_m is not None and pad2_m is not None
        pad1_rot = float(pad1_m.group(3)) if pad1_m.group(3) else 0.0
        pad2_rot = float(pad2_m.group(3)) if pad2_m.group(3) else 0.0
        assert pad1_rot == pytest.approx(75.0), \
            f"pad 1 (lib 45° + 30° delta) should be 75°, got {pad1_rot}"
        assert pad2_rot == pytest.approx(30.0), \
            f"pad 2 (lib 0° + 30° delta) should be 30°, got {pad2_rot}"

    def test_zero_rotation_omits_rot_token(self):
        # Footprint at 30°. Reset to 0° → pad 2 (lib 0°) should drop its
        # rot token entirely (KiCad's own writer does the same). Pad 1
        # keeps its 45° lib-rot.
        # Build a 30° pre-state by patching the fixture.
        pre, _ = ppt._patch_fp_pose(
            PCB_WITH_ROTATED_LIB_PAD, (0.0, 0.0), 30.0,
        )
        post, _ = ppt._patch_fp_pose(pre, (0.0, 0.0), 0.0)
        # Pad 2 should be back to its lib state (no rot token).
        pad2_m = re.search(
            r'\(pad\s+"2"[^()]*(?:\([^()]*\)[^()]*)*?'
            r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)',
            post,
        )
        assert pad2_m is not None
        assert pad2_m.group(3) is None, \
            f"pad 2 rot token should be omitted at 0°, got {pad2_m.group(3)}"
        # Pad 1 back to lib's 45°.
        pad1_m = re.search(
            r'\(pad\s+"1"[^()]*(?:\([^()]*\)[^()]*)*?'
            r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)',
            post,
        )
        assert pad1_m is not None
        assert float(pad1_m.group(3)) == pytest.approx(45.0)


# ---------------------------------------------------------------------------
# rotate_pcb requires pcbnew bindings; skip cleanly if unavailable.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("pcbnew") is None,
    reason="pcbnew bindings not available in this Python interpreter",
)
class TestRotatePcb:
    def test_rotate_smoke(self, pcb_path):
        out = ppt._try_pcbnew_rotate(pcb_path, 0.0)
        # 0° is a no-op but must succeed and report counts
        assert out["success"] is True
        assert out["footprints_rotated"] >= 2


# ---------------------------------------------------------------------------
# Internal helpers used by the tests (no production code).
# ---------------------------------------------------------------------------


def _find_footprint_block(text: str, ref: str) -> str | None:
    """Tiny test helper: extract the (footprint …) block for ``ref``."""
    pattern = rf'\(property "Reference" "{re.escape(ref)}"'
    m = re.search(pattern, text)
    if not m:
        return None
    fp_start = text.rfind("(footprint", 0, m.start())
    if fp_start < 0:
        return None
    fp_end = ppt._find_block_end(text, fp_start)
    return text[fp_start:fp_end]
