# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke + integration tests for the schematic-patch toolchain.

Covers the round-trip Add → Group-Transform → Delete on a tiny generated
schematic, plus the read-only probes (``compute_pin_world_positions_sch``,
``list_schematic_groups``, ``get_schematic_bbox``).

These tests do **not** require KiCad to be running — only the
kicad-cli (for an initial schematic seed) and access to the bundled
KiCad symbol library. If those are missing the test self-skips.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil

import pytest

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools.sch_patch_tools import register_sch_patch_tools


pytestmark = pytest.mark.skipif(
    not shutil.which("kicad-cli")
    and not os.path.isfile(
        r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
    ),
    reason="kicad-cli not found",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_schematic(path: str) -> None:
    """Write a minimal valid ``.kicad_sch`` for the patch tools to work on."""
    text = (
        "(kicad_sch\n"
        "  (version 20231120)\n"
        '  (generator "kicad-mcp-tests")\n'
        '  (uuid "11111111-2222-3333-4444-555555555555")\n'
        '  (paper "A4")\n'
        "\n"
        "  (lib_symbols\n"
        "  )\n"
        ")\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def seeded_sch(tmp_path):
    p = str(tmp_path / "seed.kicad_sch")
    _seed_schematic(p)
    return p


@pytest.fixture
def server():
    m = FastMCP("test")
    register_sch_patch_tools(m)
    return m


def _call(server: FastMCP, name: str, **kwargs):
    """Synchronously invoke an MCP tool and return the structured result."""
    result = asyncio.run(server.call_tool(name, kwargs))
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return result


# ---------------------------------------------------------------------------
# Read-only probes on an empty schematic
# ---------------------------------------------------------------------------


class TestReadEmpty:
    def test_pin_world_positions_empty(self, server, seeded_sch):
        r = _call(server, "compute_pin_world_positions_sch", sch_path=seeded_sch)
        assert r["success"] is True
        assert r["symbol_count"] == 0
        assert r["pin_count"] == 0

    def test_list_groups_empty(self, server, seeded_sch):
        r = _call(server, "list_schematic_groups", sch_path=seeded_sch)
        assert r["success"] is True
        assert r["group_count"] == 0

    def test_bbox_empty_returns_error(self, server, seeded_sch):
        r = _call(server, "get_schematic_bbox", sch_path=seeded_sch)
        assert r["success"] is False  # nothing to bound


# ---------------------------------------------------------------------------
# Add → Validate → Group → Transform → Delete round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def _three_resistors(self, group: str = "g1") -> str:
        # All x/y on the 1.27 mm placement grid so the tool's defensive
        # snap-to-grid does not shift the anchors. Values picked to land
        # on both 1.27 mm and 2.54 mm grids (multiples of 2.54).
        return json.dumps(
            [
                {
                    "ref": "R10",
                    "name": "R",
                    "value": "1k",
                    "footprint": "Resistor_SMD:R_0402_1005Metric",
                    "x_mm": 50.8,
                    "y_mm": 50.8,
                },
                {
                    "ref": "R11",
                    "name": "R",
                    "value": "2k",
                    "footprint": "Resistor_SMD:R_0402_1005Metric",
                    "x_mm": 60.96,
                    "y_mm": 50.8,
                },
                {
                    "ref": "R12",
                    "name": "R",
                    "value": "3k",
                    "footprint": "Resistor_SMD:R_0402_1005Metric",
                    "x_mm": 71.12,
                    "y_mm": 50.8,
                },
            ]
        )

    def test_add_inserts_three_in_group(self, server, seeded_sch):
        r = _call(
            server,
            "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=self._three_resistors(),
            group_id="g1",
        )
        if not r["success"] and r.get("errors"):
            # Skip when KiCad symbol cache cannot find Device:R (no library).
            pytest.skip(f"library lookup unavailable: {r['errors']}")
        assert sorted(r["inserted"]) == ["R10", "R11", "R12"]
        # Device:R was embedded into lib_symbols
        assert "Device:R" in r["lib_symbols_added"]

    def test_validate_collision_after_add(self, server, seeded_sch):
        _call(
            server,
            "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=self._three_resistors(),
            group_id="g1",
        )
        r = _call(
            server,
            "validate_schematic_patch",
            sch_path=seeded_sch,
            parts=json.dumps([{"ref": "R10", "name": "R"}]),
        )
        assert r["collisions"] == ["R10"]

    def test_groups_listed_after_add(self, server, seeded_sch):
        added = _call(
            server,
            "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=self._three_resistors(),
            group_id="g1",
        )
        if not added["success"]:
            pytest.skip(f"add failed: {added.get('errors')}")
        r = _call(server, "list_schematic_groups", sch_path=seeded_sch)
        assert r["group_count"] == 1
        assert sorted(r["groups"]["g1"]["refs"]) == ["R10", "R11", "R12"]

    def test_bbox_of_group(self, server, seeded_sch):
        added = _call(
            server,
            "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=self._three_resistors(),
            group_id="g1",
        )
        if not added["success"]:
            pytest.skip(f"add failed: {added.get('errors')}")
        r = _call(server, "get_schematic_bbox", sch_path=seeded_sch, group_id="g1")
        assert r["success"]
        # x spans from R10 (50.8) to R12 (71.12); the bbox includes pin
        # endpoints which extend by ~3.81 mm each side per the lib_symbol
        # Device:R.
        assert r["bbox_mm"]["xmin"] <= 50.8
        assert r["bbox_mm"]["xmax"] >= 71.12

    def test_rotate_clean_90(self, server, seeded_sch):
        added = _call(
            server,
            "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=self._three_resistors(),
            group_id="g1",
        )
        if not added["success"]:
            pytest.skip(f"add failed: {added.get('errors')}")
        r = _call(
            server,
            "rotate_schematic_group",
            sch_path=seeded_sch,
            group_id="g1",
            angle_deg=90,
            tolerance_deg=5,
            force=False,
        )
        assert r["success"]
        assert r["max_residual_deg"] == pytest.approx(0.0)
        assert r["items_rotated"] == 3

    def test_rotate_30_without_force_errors(self, server, seeded_sch):
        added = _call(
            server,
            "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=self._three_resistors(),
            group_id="g1",
        )
        if not added["success"]:
            pytest.skip(f"add failed: {added.get('errors')}")
        r = _call(
            server,
            "rotate_schematic_group",
            sch_path=seeded_sch,
            group_id="g1",
            angle_deg=30,
            tolerance_deg=5,
            force=False,
        )
        assert r["success"] is False
        assert r["max_residual_deg"] == pytest.approx(30.0, abs=1e-6)

    def test_rotate_30_force_warns(self, server, seeded_sch):
        added = _call(
            server,
            "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=self._three_resistors(),
            group_id="g1",
        )
        if not added["success"]:
            pytest.skip(f"add failed: {added.get('errors')}")
        r = _call(
            server,
            "rotate_schematic_group",
            sch_path=seeded_sch,
            group_id="g1",
            angle_deg=30,
            force=True,
        )
        assert r["success"]
        # 3 symbols got a 30° residual warning each
        assert sum("residual" in w.lower() for w in r.get("warnings", [])) == 3

    def test_move_translates_three_symbols(self, server, seeded_sch):
        added = _call(
            server,
            "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=self._three_resistors(),
            group_id="g1",
        )
        if not added["success"]:
            pytest.skip(f"add failed: {added.get('errors')}")
        r = _call(
            server,
            "move_schematic_group",
            sch_path=seeded_sch,
            group_id="g1",
            dx_mm=10,
            dy_mm=-5,
        )
        assert r["success"]
        assert r["items_moved"] == 3

    def test_delete_group_removes_symbols(self, server, seeded_sch):
        added = _call(
            server,
            "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=self._three_resistors(),
            group_id="g1",
        )
        if not added["success"]:
            pytest.skip(f"add failed: {added.get('errors')}")
        r = _call(
            server,
            "delete_schematic_items",
            sch_path=seeded_sch,
            group_id="g1",
        )
        assert r["success"]
        assert r["deleted_count"] == 3
        # Group is gone
        r2 = _call(server, "list_schematic_groups", sch_path=seeded_sch)
        assert r2["group_count"] == 0


# ---------------------------------------------------------------------------
# Wire / label helpers
# ---------------------------------------------------------------------------


class TestWireAndLabel:
    def test_add_wire(self, server, seeded_sch):
        r = _call(
            server,
            "add_schematic_wire",
            sch_path=seeded_sch,
            segments=json.dumps([[10.0, 10.0, 30.0, 10.0]]),
        )
        assert r["success"]
        assert r["segments_added"] == 1

    def test_add_label_global(self, server, seeded_sch):
        r = _call(
            server,
            "add_schematic_label",
            sch_path=seeded_sch,
            text="UART_TX",
            x_mm=10.0,
            y_mm=10.0,
            kind="global",
        )
        assert r["success"]
        assert r["kind"] == "global"

    def test_add_label_rejects_power_net_text(self, server, seeded_sch):
        # Power-net names must use add_power_symbols, not global labels.
        r = _call(
            server,
            "add_schematic_label",
            sch_path=seeded_sch,
            text="VCC",
            x_mm=10.0,
            y_mm=10.0,
            kind="global",
        )
        assert r["success"] is False
        assert "power" in r["error"].lower()
        assert r.get("suggested_lib_id") == "power:VCC"

    def test_add_label_rejects_unknown_kind(self, server, seeded_sch):
        r = _call(
            server,
            "add_schematic_label",
            sch_path=seeded_sch,
            text="N1",
            x_mm=0.0,
            y_mm=0.0,
            kind="bogus",
        )
        assert r["success"] is False


# ---------------------------------------------------------------------------
# Annotate (Bug 3 / Bug 6 — pure-Python annotator)
# ---------------------------------------------------------------------------


def _seed_with_unannotated_symbols(path: str) -> None:
    """Seed a schematic with mixed annotated/unannotated refs so the
    annotator has something to do. Hand-rolled S-expr — no kicad-cli
    needed.
    """
    text = (
        "(kicad_sch\n"
        "  (version 20231120)\n"
        '  (generator "kicad-mcp-tests")\n'
        '  (uuid "11111111-2222-3333-4444-555555555555")\n'
        '  (paper "A4")\n'
        "  (lib_symbols\n"
        "  )\n"
        '  (symbol (lib_id "Device:R") (at 50 50 0)\n'
        '    (uuid "aaaa1111-0000-0000-0000-000000000000")\n'
        '    (property "Reference" "R10" (at 50 45 0))\n'
        '    (property "Value" "1k" (at 50 55 0))\n'
        "  )\n"
        '  (symbol (lib_id "Device:R") (at 60 50 0)\n'
        '    (uuid "aaaa2222-0000-0000-0000-000000000000")\n'
        '    (property "Reference" "R?" (at 60 45 0))\n'
        '    (property "Value" "2k" (at 60 55 0))\n'
        "  )\n"
        '  (symbol (lib_id "power:GND") (at 70 50 0)\n'
        '    (uuid "aaaa3333-0000-0000-0000-000000000000")\n'
        '    (property "Reference" "#PWR_CPU_GND" (at 70 45 0))\n'
        '    (property "Value" "GND" (at 70 55 0))\n'
        "  )\n"
        '  (symbol (lib_id "Device:C") (at 80 50 0)\n'
        '    (uuid "aaaa4444-0000-0000-0000-000000000000")\n'
        '    (property "Reference" "C?" (at 80 45 0))\n'
        '    (property "Value" "100n" (at 80 55 0))\n'
        '    (instances\n'
        '      (project "test" (path "/" (reference "C?") (unit 1)))\n'
        '    )\n'
        "  )\n"
        ")\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def annotated_seed(tmp_path):
    p = str(tmp_path / "annotate.kicad_sch")
    _seed_with_unannotated_symbols(p)
    return p


class TestAnnotateSchematic:
    def test_renumbers_unannotated(self, server, annotated_seed):
        r = _call(server, "annotate_schematic", sch_path=annotated_seed)
        assert r["success"]
        renamed = {x["old"]: x["new"] for x in r["renamed"]}
        # R? → R1 (annotator fills gaps; R10 is taken but 1-9 free)
        assert renamed.get("R?") == "R1"
        # #PWR_CPU_GND → #PWR0001 (no other #PWR refs)
        assert renamed.get("#PWR_CPU_GND") == "#PWR0001"
        # C? → C1 (no existing C in schematic)
        assert renamed.get("C?") == "C1"
        # R10 was already valid → skipped
        assert "R10" in r["skipped"]

    def test_instance_reference_also_updated(self, server, annotated_seed):
        _call(server, "annotate_schematic", sch_path=annotated_seed)
        with open(annotated_seed, encoding="utf-8") as fh:
            text = fh.read()
        # The instance section's (reference "C?") must have been rewritten
        assert '(reference "C?")' not in text
        assert '(reference "C1")' in text

    def test_idempotent_on_clean_schematic(self, server, annotated_seed):
        _call(server, "annotate_schematic", sch_path=annotated_seed)
        r2 = _call(server, "annotate_schematic", sch_path=annotated_seed)
        assert r2["success"]
        assert r2["renamed"] == []  # nothing to do the second time

    def test_delete_by_region_kills_wires(self, server, seeded_sch):
        # Add three wires — two inside the region, one outside. Coordinates
        # picked on the 1.27 mm placement grid so add_schematic_wire's
        # defensive snap leaves them untouched.
        _call(
            server,
            "add_schematic_wire",
            sch_path=seeded_sch,
            segments=json.dumps([
                [10.16, 10.16, 30.48, 10.16],   # inside
                [15.24, 15.24, 25.4, 15.24],    # inside
                [60.96, 60.96, 81.28, 60.96],   # outside
            ]),
        )
        r = _call(
            server,
            "delete_schematic_items",
            sch_path=seeded_sch,
            types=["wire"],
            region={"x": 0.0, "y": 0.0, "w": 50.0, "h": 50.0},
        )
        assert r["success"]
        assert r["deleted_count"] == 2
        # The outside one survives
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        assert "60.96 60.96" in text or "(xy 60.96 60.96)" in text

    def test_delete_requires_some_selector(self, server, seeded_sch):
        r = _call(server, "delete_schematic_items", sch_path=seeded_sch)
        assert r["success"] is False
        assert "No matching items" in r["error"]

    def test_add_power_symbols_by_net(self, server, seeded_sch):
        r = _call(
            server,
            "add_power_symbols",
            sch_path=seeded_sch,
            anchors=json.dumps([
                {"net": "GND", "x_mm": 50, "y_mm": 50, "rotation_deg": 0},
                {"net": "+3V3", "x_mm": 60, "y_mm": 50, "rotation_deg": 180},
            ]),
        )
        assert r["success"], r.get("errors")
        assert len(r["inserted"]) == 2
        # auto-numbered #PWR refs
        for ref in r["inserted"]:
            assert ref.startswith("#PWR")
        # power:GND + power:+3V3 lib symbols embedded
        assert "power:GND" in r["lib_symbols_added"]
        assert "power:+3V3" in r["lib_symbols_added"]

    def test_add_power_symbols_unknown_net_errors(self, server, seeded_sch):
        r = _call(
            server,
            "add_power_symbols",
            sch_path=seeded_sch,
            anchors=json.dumps([{"net": "FOOBAR", "x_mm": 50, "y_mm": 50}]),
        )
        assert r["success"] is False
        assert r["errors"]

    def _seed_with_global_labels(self, sch_path: str) -> None:
        """Drop a handful of top-level labels into a seeded schematic.

        Mix: two power nets (``GND``, ``+3V3``), one signal label
        (``UART_TX``), and one local label (which the converter must
        ignore — only ``global_label`` blocks are touched).
        """
        with open(sch_path, encoding="utf-8") as fh:
            text = fh.read()
        new_blocks = (
            '  (global_label "GND" (shape bidirectional) (at 30 40 0)\n'
            '    (effects (font (size 1.27 1.27)))\n'
            '    (uuid "aaaa0001-0000-0000-0000-000000000001"))\n'
            '  (global_label "+3V3" (shape bidirectional) (at 50 40 180)\n'
            '    (effects (font (size 1.27 1.27)))\n'
            '    (uuid "aaaa0002-0000-0000-0000-000000000002"))\n'
            '  (global_label "UART_TX" (shape bidirectional) (at 70 40 0)\n'
            '    (effects (font (size 1.27 1.27)))\n'
            '    (uuid "aaaa0003-0000-0000-0000-000000000003"))\n'
            '  (label "INTERNAL_NET" (at 90 40 0)\n'
            '    (effects (font (size 1.27 1.27)))\n'
            '    (uuid "aaaa0004-0000-0000-0000-000000000004"))\n'
        )
        out = text.rstrip().rstrip(")") + new_blocks + ")\n"
        with open(sch_path, "w", encoding="utf-8") as fh:
            fh.write(out)

    def test_convert_global_labels_to_power_replaces_power_nets(
        self, server, seeded_sch
    ):
        self._seed_with_global_labels(seeded_sch)
        r = _call(
            server,
            "convert_global_labels_to_power",
            sch_path=seeded_sch,
        )
        assert r["success"], r.get("errors")
        assert r["dry_run"] is False
        # Both power-net globals replaced
        nets_replaced = {entry["net"] for entry in r["replaced"]}
        assert nets_replaced == {"GND", "+3V3"}
        # Conventional rotation: KiCad's stock power: lib-symbols carry
        # the glyph orientation themselves, so rotation 0 is canonical
        # for every family.
        rot_for = {e["net"]: e["rotation_deg"] for e in r["replaced"]}
        assert rot_for["GND"] == 0
        assert rot_for["+3V3"] == 0
        # Refs are auto-allocated #PWR
        for entry in r["replaced"]:
            assert entry["ref"].startswith("#PWR")
        # power lib symbols embedded
        assert "power:GND" in r["lib_symbols_added"]
        assert "power:+3V3" in r["lib_symbols_added"]
        # Global labels for power are gone, signal label survives
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        assert '(global_label "GND"' not in text
        assert '(global_label "+3V3"' not in text
        assert '(global_label "UART_TX"' in text
        assert '(label "INTERNAL_NET"' in text
        # Power-symbol instances actually present
        assert '(lib_id "power:GND")' in text
        assert '(lib_id "power:+3V3")' in text

    def test_convert_global_labels_to_power_dry_run_does_not_mutate(
        self, server, seeded_sch
    ):
        self._seed_with_global_labels(seeded_sch)
        with open(seeded_sch, encoding="utf-8") as fh:
            before = fh.read()
        r = _call(
            server,
            "convert_global_labels_to_power",
            sch_path=seeded_sch,
            dry_run=True,
        )
        assert r["success"]
        assert r["dry_run"] is True
        assert r["replaced"] == []
        nets_planned = {e["net"] for e in r["would_replace"]}
        assert nets_planned == {"GND", "+3V3"}
        with open(seeded_sch, encoding="utf-8") as fh:
            after = fh.read()
        assert before == after, "dry_run must not modify the schematic"

    def test_convert_global_labels_to_power_only_nets_filter(
        self, server, seeded_sch
    ):
        self._seed_with_global_labels(seeded_sch)
        r = _call(
            server,
            "convert_global_labels_to_power",
            sch_path=seeded_sch,
            only_nets="GND",
        )
        assert r["success"]
        assert {e["net"] for e in r["replaced"]} == {"GND"}
        assert any(
            s["net"] == "+3V3" and "only_nets" in s["reason"]
            for s in r["skipped"]
        )
        # +3V3 global label survives the filtered run
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        assert '(global_label "+3V3"' in text
        assert '(global_label "GND"' not in text

    def test_convert_global_labels_to_power_hides_pwr_reference(
        self, server, seeded_sch
    ):
        # Sanity: every emitted power-symbol carries (hide yes) on its
        # Reference property — the auto-allocated #PWRnnnn designator
        # is a KiCad implementation detail, not for the user.
        self._seed_with_global_labels(seeded_sch)
        r = _call(
            server,
            "convert_global_labels_to_power",
            sch_path=seeded_sch,
        )
        assert r["success"]
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        # Each power symbol's Reference block ends with (hide yes) before
        # the closing ')'. Check on a per-Reference basis: from each
        # `(property "Reference" "#PWR…"` opening we slice forward until
        # the matching close and assert (hide yes) is in the slice.
        for ref_start in [
            i for i in range(len(text))
            if text.startswith('(property "Reference" "#PWR', i)
        ]:
            depth = 0
            j = ref_start
            while j < len(text):
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            block = text[ref_start:j]
            assert "(hide yes)" in block, (
                "power symbol Reference must be hidden:\n" + block
            )

    def test_convert_global_labels_to_power_canonical_value(
        self, server, seeded_sch
    ):
        # Suffixed rails (+5V_SYS, VBUS_SYS) must collapse to the bare
        # KiCad rail value so every consumer ends up on one net.
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        block = (
            '  (global_label "+5V_SYS" (shape bidirectional) (at 30 40 180)\n'
            '    (effects (font (size 1.27 1.27)))\n'
            '    (uuid "cccc0001-0000-0000-0000-000000000001"))\n'
            '  (global_label "VBUS_SYS" (shape bidirectional) (at 50 40 180)\n'
            '    (effects (font (size 1.27 1.27)))\n'
            '    (uuid "cccc0002-0000-0000-0000-000000000002"))\n'
        )
        out = text.rstrip().rstrip(")") + block + ")\n"
        with open(seeded_sch, "w", encoding="utf-8") as fh:
            fh.write(out)
        r = _call(
            server,
            "convert_global_labels_to_power",
            sch_path=seeded_sch,
        )
        assert r["success"]
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        # Power symbols use the bare KiCad value, not the SYS suffix.
        assert '(property "Value" "+5V"' in text
        assert '(property "Value" "VBUS"' in text
        assert '(property "Value" "+5V_SYS"' not in text
        assert '(property "Value" "VBUS_SYS"' not in text

    def test_convert_global_labels_to_power_no_signal_labels_touched(
        self, server, seeded_sch
    ):
        # Only signal labels in the schematic — converter is a no-op.
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        block = (
            '  (global_label "UART_TX" (shape bidirectional) (at 30 40 0)\n'
            '    (effects (font (size 1.27 1.27)))\n'
            '    (uuid "bbbb0001-0000-0000-0000-000000000001"))\n'
        )
        out = text.rstrip().rstrip(")") + block + ")\n"
        with open(seeded_sch, "w", encoding="utf-8") as fh:
            fh.write(out)
        r = _call(
            server,
            "convert_global_labels_to_power",
            sch_path=seeded_sch,
        )
        assert r["success"]
        assert r["replaced"] == []
        assert r["lib_symbols_added"] == []

    def test_cascade_delete_removes_attached_wires_and_labels(self, server, seeded_sch):
        # Insert a resistor + a wire-stub from one of its pins to a label
        added = _call(
            server, "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=json.dumps([{
                "ref": "R99", "name": "R", "value": "1k",
                "footprint": "Resistor_SMD:R_0402_1005Metric",
                "x_mm": 50, "y_mm": 50,
            }]),
        )
        if not added["success"] and added.get("errors"):
            pytest.skip(f"library lookup unavailable: {added['errors']}")
        # Place wires at R99's pin hot-spots (Device:R has pins ±3.81 mm
        # vertically from its centre at the standard library scale)
        _call(
            server, "add_schematic_wire",
            sch_path=seeded_sch,
            segments=json.dumps([
                [50.0, 46.19, 53.0, 46.19],
                [50.0, 53.81, 53.0, 53.81],
            ]),
        )
        # Cascade delete the resistor
        r = _call(
            server, "delete_schematic_items",
            sch_path=seeded_sch,
            refs=["R99"],
            cascade=True,
        )
        assert r["success"]
        # cascade entries reported alongside the symbol
        cascaded = [d for d in r["deleted"] if d.startswith("cascade:")]
        # the two wires we placed should be cascaded — they sit at the
        # exact pin hot-spots of the deleted symbol
        assert any("wire" in c for c in cascaded), \
            f"Expected cascade wires in {r['deleted']}"

    def test_pin_uuids_emitted_on_insert(self, server, seeded_sch):
        added = _call(
            server, "add_schematic_symbols",
            sch_path=seeded_sch,
            parts=json.dumps([{
                "ref": "R98", "name": "R", "value": "10k",
                "footprint": "Resistor_SMD:R_0402_1005Metric",
                "x_mm": 80, "y_mm": 80,
            }]),
        )
        if not added["success"] and added.get("errors"):
            pytest.skip(f"library lookup unavailable: {added['errors']}")
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        # Find the R98 block and look for per-pin UUID entries
        m = re.search(r'\(symbol \(lib_id "Device:R"\).*?\(property "Reference" "R98".*?(\(pin "1" \(uuid "[a-f0-9-]+"\)\))', text, re.DOTALL)
        assert m is not None, "expected per-pin UUID entry for pin 1 on R98"
        # And the (instances ...) block with project name
        assert "(instances" in text and '(reference "R98")' in text

    def test_force_renumber_renames_all(self, server, annotated_seed):
        r = _call(
            server,
            "annotate_schematic",
            sch_path=annotated_seed,
            force_renumber=True,
        )
        assert r["success"]
        # With force every symbol gets a new number; 4 renames expected.
        assert len(r["renamed"]) == 4
        new_refs = sorted(x["new"] for x in r["renamed"])
        # Two resistors → R1, R2; one cap → C1; one power → #PWR0001
        assert "C1" in new_refs
        assert "#PWR0001" in new_refs
        assert sum(1 for x in new_refs if x.startswith("R")) == 2


# ---------------------------------------------------------------------------
# Property / flag editing — update_symbol_property
# ---------------------------------------------------------------------------


def _seeded_with_two_resistors(server: FastMCP, sch_path: str) -> bool:
    """Helper: add R10=1k and R11=2k to a seeded schematic.

    Returns False if Device:R lookup fails (signal to caller to skip the
    test rather than fail).
    """
    parts = json.dumps([
        {
            "ref": "R10",
            "name": "R",
            "value": "1k",
            "footprint": "Resistor_SMD:R_0402_1005Metric",
            "x_mm": 50.8,
            "y_mm": 50.8,
        },
        {
            "ref": "R11",
            "name": "R",
            "value": "2k",
            "footprint": "Resistor_SMD:R_0402_1005Metric",
            "x_mm": 60.96,
            "y_mm": 50.8,
        },
    ])
    r = _call(server, "add_schematic_symbols", sch_path=sch_path, parts=parts)
    return bool(r.get("success")) and not r.get("errors")


class TestUpdateSymbolProperty:
    def test_update_value_and_footprint(self, server, seeded_sch):
        if not _seeded_with_two_resistors(server, seeded_sch):
            pytest.skip("Device:R lookup unavailable in this env")
        r = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            value="22k",
            footprint="Resistor_SMD:R_0603_1608Metric",
        )
        assert r["success"], r
        assert r["not_found"] == []
        assert len(r["updated"]) == 1
        changed = r["updated"][0]
        assert changed["ref"] == "R10"
        assert changed["changed"]["Value"] == ["1k", "22k"]
        assert changed["changed"]["Footprint"][1] == "Resistor_SMD:R_0603_1608Metric"
        # Verify on disk
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        assert '"Value" "22k"' in text
        assert '"Footprint" "Resistor_SMD:R_0603_1608Metric"' in text
        # R11 untouched
        assert '"Value" "2k"' in text

    def test_update_flag_dnp(self, server, seeded_sch):
        if not _seeded_with_two_resistors(server, seeded_sch):
            pytest.skip("Device:R lookup unavailable in this env")
        r = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10", "R11"]),
            dnp="yes",
        )
        assert r["success"]
        assert len(r["updated"]) == 2
        for upd in r["updated"]:
            assert upd["changed"]["dnp"] == ["no", "yes"]
        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        # Both Rs now (dnp yes)
        assert text.count("(dnp yes)") >= 2

    def test_idempotent_second_call_no_change(self, server, seeded_sch):
        if not _seeded_with_two_resistors(server, seeded_sch):
            pytest.skip("Device:R lookup unavailable in this env")
        _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            value="22k",
        )
        r2 = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            value="22k",
        )
        assert r2["success"]
        assert r2["updated"] == []  # nothing to do the second time

    def test_unknown_ref_goes_to_not_found(self, server, seeded_sch):
        if not _seeded_with_two_resistors(server, seeded_sch):
            pytest.skip("Device:R lookup unavailable in this env")
        r = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R99", "R10"]),
            value="100k",
        )
        assert r["success"]
        assert r["not_found"] == ["R99"]
        assert len(r["updated"]) == 1
        assert r["updated"][0]["ref"] == "R10"

    def test_requires_some_update(self, server, seeded_sch):
        r = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
        )
        assert r["success"] is False
        assert "no property or flag updates" in r["error"]

    def test_invalid_flag_value_errors(self, server, seeded_sch):
        r = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            dnp="maybe",
        )
        assert r["success"] is False
        assert "must be 'yes' or 'no'" in r["error"]

    def test_hide_reference_inserts_when_missing(self, server, seeded_sch):
        if not _seeded_with_two_resistors(server, seeded_sch):
            pytest.skip("Device:R lookup unavailable in this env")
        # Sanity: freshly-added R10 has Reference without (hide ...)
        with open(seeded_sch, encoding="utf-8") as fh:
            before = fh.read()
        # Block containing R10's Reference Property
        ref_block_match = re.search(
            r'\(property\s+"Reference"\s+"R10".*?\)\s*\)',
            before,
            flags=re.DOTALL,
        )
        assert ref_block_match
        assert "(hide" not in ref_block_match.group(0)

        r = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            hide_reference="yes",
        )
        assert r["success"], r
        assert len(r["updated"]) == 1
        assert r["updated"][0]["changed"]["Reference.hide"] == ["(none)", "yes"]

        with open(seeded_sch, encoding="utf-8") as fh:
            after = fh.read()
        ref_block_after = re.search(
            r'\(property\s+"Reference"\s+"R10".*?\)\s*\)',
            after,
            flags=re.DOTALL,
        )
        assert ref_block_after
        assert "(hide yes)" in ref_block_after.group(0)

    def test_hide_value_toggles_existing(self, server, seeded_sch):
        if not _seeded_with_two_resistors(server, seeded_sch):
            pytest.skip("Device:R lookup unavailable in this env")
        # First pass: insert (hide yes) on Value
        _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            hide_value="yes",
        )
        # Second pass: flip it back to no — exercises the rewrite branch
        r = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            hide_value="no",
        )
        assert r["success"]
        assert r["updated"][0]["changed"]["Value.hide"] == ["yes", "no"]

        with open(seeded_sch, encoding="utf-8") as fh:
            text = fh.read()
        val_block = re.search(
            r'\(property\s+"Value"\s+"1k".*?\)\s*\)', text, flags=re.DOTALL
        )
        assert val_block
        assert "(hide no)" in val_block.group(0)
        assert "(hide yes)" not in val_block.group(0)

    def test_hide_is_idempotent(self, server, seeded_sch):
        if not _seeded_with_two_resistors(server, seeded_sch):
            pytest.skip("Device:R lookup unavailable in this env")
        _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            hide_reference="yes",
        )
        r2 = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            hide_reference="yes",
        )
        assert r2["success"]
        assert r2["updated"] == []

    def test_invalid_hide_value_errors(self, server, seeded_sch):
        r = _call(
            server,
            "update_symbol_property",
            sch_path=seeded_sch,
            refs=json.dumps(["R10"]),
            hide_reference="maybe",
        )
        assert r["success"] is False
        assert "must be 'yes' or 'no'" in r["error"]


# ---------------------------------------------------------------------------
# bulk_swap_symbol — regression for the `_reparse` crash (the method does
# not exist on SchematicDoc; the lazy tree is invalidated via _invalidate).
# ---------------------------------------------------------------------------


class TestBulkSwapSymbol:
    @staticmethod
    def _seed_with_r(path: str) -> None:
        text = (
            "(kicad_sch\n"
            "  (version 20231120)\n"
            '  (generator "kicad-mcp-tests")\n'
            '  (uuid "11111111-2222-3333-4444-555555555555")\n'
            '  (paper "A4")\n'
            "  (lib_symbols\n  )\n"
            '  (symbol (lib_id "Device:R") (at 50 50 0) (unit 1)\n'
            '    (uuid "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")\n'
            '    (property "Reference" "R1" (at 50 50 0))\n'
            '    (property "Value" "10k" (at 50 50 0))\n'
            "  )\n"
            ")\n"
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_swap_changes_lib_id_no_crash(self, server, tmp_path):
        p = str(tmp_path / "swap.kicad_sch")
        self._seed_with_r(p)
        out = _call(
            server, "bulk_swap_symbol", sch_path=p,
            old_lib_id="Device:R", new_lib_id="Device:R_Small",
        )
        assert out["success"] is True, out
        assert out["instances_swapped"] >= 1
        txt = open(p, encoding="utf-8").read()
        assert '(lib_id "Device:R_Small")' in txt
        assert '(lib_id "Device:R")' not in txt

    def test_dry_run_does_not_write(self, server, tmp_path):
        p = str(tmp_path / "swap_dry.kicad_sch")
        self._seed_with_r(p)
        before = open(p, encoding="utf-8").read()
        out = _call(
            server, "bulk_swap_symbol", sch_path=p,
            old_lib_id="Device:R", new_lib_id="Device:R_Small",
            dry_run=True,
        )
        assert out["success"] is True
        assert open(p, encoding="utf-8").read() == before

    def test_no_instances_is_clean_noop(self, server, seeded_sch):
        out = _call(
            server, "bulk_swap_symbol", sch_path=seeded_sch,
            old_lib_id="Device:R", new_lib_id="Device:R_Small",
        )
        assert out["success"] is True
        assert out["instances_swapped"] == 0

    @staticmethod
    def _seed_multiunit(path: str) -> None:
        """Schematic whose lib_symbols carries a parent + per-unit child
        symbols (``<bare>_<u>_<s>``) — the case that broke loading when
        only the parent was renamed."""
        text = (
            "(kicad_sch\n"
            "  (version 20231120)\n"
            '  (generator "t")\n'
            '  (uuid "11111111-2222-3333-4444-555555555555")\n'
            '  (paper "A4")\n'
            "  (lib_symbols\n"
            '    (symbol "Fake:DUAL"\n'
            '      (symbol "DUAL_0_1"\n'
            "        (rectangle (start -1 1) (end 1 -1)\n"
            "          (stroke (width 0) (type default)) (fill (type none)))\n"
            "      )\n"
            '      (symbol "DUAL_1_1"\n'
            '        (pin power_in line (at 0 -2 90) (length 1)\n'
            '          (name "GND" (effects (font (size 1 1))))\n'
            '          (number "1" (effects (font (size 1 1)))))\n'
            "      )\n"
            "    )\n"
            "  )\n"
            '  (symbol (lib_id "Fake:DUAL") (at 50 50 0) (unit 1)\n'
            '    (uuid "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")\n'
            '    (property "Reference" "U1" (at 50 50 0))\n'
            '    (property "Value" "DUAL" (at 50 50 0))\n'
            "  )\n"
            ")\n"
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_swap_renames_unit_child_symbols(self, server, tmp_path):
        # Regression: parent and per-unit child symbols must stay
        # name-consistent, otherwise KiCad can't load the schematic.
        p = str(tmp_path / "swap_units.kicad_sch")
        self._seed_multiunit(p)
        out = _call(
            server, "bulk_swap_symbol", sch_path=p,
            old_lib_id="Fake:DUAL", new_lib_id="Fake:DUALX",
        )
        assert out["success"] is True, out
        txt = open(p, encoding="utf-8").read()
        assert '(symbol "Fake:DUALX"' in txt          # parent renamed
        assert '(symbol "DUALX_0_1"' in txt           # units renamed
        assert '(symbol "DUALX_1_1"' in txt
        assert '(symbol "DUAL_0_1"' not in txt         # old unit names gone
        assert '(symbol "DUAL_1_1"' not in txt
        assert '(lib_id "Fake:DUALX")' in txt
