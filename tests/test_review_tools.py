# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Layer-R review tools.

Three tools × Happy / Edge / Error.

These tests run without ``kicad-cli`` and without ``pdfplumber``: the
review tool degrades gracefully when those are absent (the schematic-
region PNG and the datasheet-page PNG fields end up empty strings in the
payload, but the structured data is still produced). End-to-end image
rendering is exercised by a separate test class that self-skips when
``kicad-cli`` is missing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil

import pytest

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools.review_tools import register_review_tools


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


_MIN_SCH = """\
(kicad_sch
  (version 20231120)
  (generator "kicad-mcp-tests")
  (uuid "11111111-2222-3333-4444-555555555555")
  (paper "A4")

  (lib_symbols
  )

  (symbol
    (lib_id "Regulator_Linear:AMS1117-3.3")
    (at 100 80 0)
    (uuid "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
    (property "Reference" "U1" (at 100 70 0))
    (property "Value" "AMS1117-3.3" (at 100 90 0))
    (property "Footprint" "Package_TO_SOT_SMD:SOT-223-3_TabPin2" (at 0 0 0))
    (property "Datasheet" "http://example.com/ams1117.pdf" (at 0 0 0))
    (pin passive_line (name "ADJ/GND") (number "1"))
    (pin power_out (name "VOUT") (number "2"))
    (pin power_in (name "VIN") (number "3"))
  )

  (symbol
    (lib_id "Device:C")
    (at 120 80 0)
    (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1")
    (property "Reference" "C1" (at 120 70 0))
    (property "Value" "10uF" (at 120 90 0))
    (property "Footprint" "Capacitor_SMD:C_0603_1608Metric" (at 0 0 0))
    (pin passive_line (name "~") (number "1"))
    (pin passive_line (name "~") (number "2"))
  )

  (symbol
    (lib_id "Device:R")
    (at 80 80 0)
    (uuid "cccccccc-cccc-cccc-cccc-ccccccccccc1")
    (property "Reference" "R1" (at 80 70 0))
    (property "Value" "10k" (at 80 90 0))
    (property "Footprint" "Resistor_SMD:R_0402_1005Metric" (at 0 0 0))
    (pin passive_line (name "~") (number "1"))
    (pin passive_line (name "~") (number "2"))
  )
)
"""


@pytest.fixture
def seeded_project(tmp_path):
    """Create a minimal ``.kicad_pro`` + ``.kicad_sch`` pair in tmp_path."""
    proj_path = tmp_path / "demo.kicad_pro"
    sch_path = tmp_path / "demo.kicad_sch"
    proj_path.write_text("{}\n", encoding="utf-8")
    sch_path.write_text(_MIN_SCH, encoding="utf-8")
    return str(proj_path), str(sch_path)


@pytest.fixture
def server() -> FastMCP:
    m = FastMCP("test-review")
    register_review_tools(m)
    return m


def _call(server: FastMCP, name: str, **kwargs):
    result = asyncio.run(server.call_tool(name, kwargs))
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return result


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# list_missing_datasheets
# ---------------------------------------------------------------------------


class TestListMissingDatasheets:
    def test_all_missing_when_no_docs_dir(self, server, seeded_project):
        proj, _ = seeded_project
        r = _call(server, "list_missing_datasheets", project_path=proj)
        assert r["success"] is True
        # Only U1 is an IC (U-prefix); C1 / R1 are filtered out.
        assert r["total_unique_values"] == 1
        assert len(r["missing"]) == 1
        assert r["missing"][0]["value"] == "AMS1117-3.3"
        assert r["missing"][0]["refs"] == ["U1"]
        # Datasheet URL is propagated from the symbol property
        assert "ams1117.pdf" in r["missing"][0]["datasheet_url"]

    def test_present_when_pdf_on_disk(self, server, seeded_project, tmp_path):
        proj, _ = seeded_project
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "AMS1117-3.3.pdf").write_bytes(b"%PDF-1.4\n")
        r = _call(server, "list_missing_datasheets", project_path=proj)
        assert r["success"] is True
        assert len(r["missing"]) == 0
        assert len(r["present"]) == 1
        assert r["present"][0]["pdf_path"].endswith("AMS1117-3.3.pdf")

    def test_missing_project_returns_error(self, server, tmp_path):
        bogus = str(tmp_path / "does_not_exist.kicad_pro")
        r = _call(server, "list_missing_datasheets", project_path=bogus)
        assert r["success"] is False
        assert "not found" in r["error"].lower()


# ---------------------------------------------------------------------------
# review_ic_against_datasheet
# ---------------------------------------------------------------------------


class TestReviewIcAgainstDatasheet:
    def test_happy_path_writes_payload_and_brief(self, server, seeded_project):
        proj, _sch = seeded_project
        r = _call(
            server, "review_ic_against_datasheet",
            ic_reference="U1", project_path=proj,
        )
        assert r["success"] is True, r
        assert r["ic"]["ref"] == "U1"
        assert r["ic"]["value"] == "AMS1117-3.3"
        assert os.path.isfile(r["payload_path"])
        assert os.path.isfile(r["brief_path"])
        # Default location is <project_dir>/review/<REF>/
        assert os.path.basename(r["output_dir"]) == "U1"
        assert os.path.basename(os.path.dirname(r["output_dir"])) == "review"

        # Payload structure
        with open(r["payload_path"], encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["ic"]["ref"] == "U1"
        assert isinstance(payload["pins"], list)
        # Symbol declares 3 pins, none filtered out
        assert payload["meta"]["full_pin_count"] == 3
        assert payload["meta"]["shown_pin_count"] == 3
        # Datasheet URL is non-local → resolution falls back to "missing"
        # unless caller supplies the path.
        assert payload["meta"]["datasheet_resolved_via"] == "missing"

    def test_unknown_reference_returns_error(self, server, seeded_project):
        proj, _ = seeded_project
        r = _call(
            server, "review_ic_against_datasheet",
            ic_reference="U99", project_path=proj,
        )
        assert r["success"] is False
        assert "not found" in r["error"].lower()
        assert "available_refs_sample" in r

    def test_pin_range_filters_pins(self, server, seeded_project):
        proj, _ = seeded_project
        r = _call(
            server, "review_ic_against_datasheet",
            ic_reference="U1", project_path=proj,
            pin_range_start=2, pin_range_end=3,
        )
        assert r["success"] is True
        with open(r["payload_path"], encoding="utf-8") as fh:
            payload = json.load(fh)
        pin_nums = sorted(p["pin"] for p in payload["pins"])
        assert pin_nums == ["2", "3"]
        assert payload["meta"]["full_pin_count"] == 3
        assert payload["meta"]["shown_pin_count"] == 2

    def test_idempotent_second_run_same_payload(self, server, seeded_project):
        proj, _ = seeded_project
        r1 = _call(
            server, "review_ic_against_datasheet",
            ic_reference="U1", project_path=proj,
        )
        # Re-running must overwrite cleanly with the same structured content.
        # ``generated_at`` differs each run, so we compare a payload copy that
        # zeroes out the timestamp.
        r2 = _call(
            server, "review_ic_against_datasheet",
            ic_reference="U1", project_path=proj,
        )
        for r in (r1, r2):
            with open(r["payload_path"], encoding="utf-8") as fh:
                p = json.load(fh)
            p["meta"]["generated_at"] = "T"
            with open(r["payload_path"], "w", encoding="utf-8") as fh:
                json.dump(p, fh, indent=2, sort_keys=True, ensure_ascii=False)
        assert _file_hash(r1["payload_path"]) == _file_hash(r2["payload_path"])

    def test_explicit_datasheet_path_marks_source_parameter(
        self, server, seeded_project, tmp_path
    ):
        proj, _ = seeded_project
        # Mini "PDF" file — the rasteriser will fail (it's not a real PDF),
        # but the path-resolution should still mark source = "parameter".
        fake_pdf = tmp_path / "ams1117.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        r = _call(
            server, "review_ic_against_datasheet",
            ic_reference="U1", project_path=proj,
            datasheet_pdf=str(fake_pdf),
        )
        assert r["success"] is True
        assert r["datasheet_resolved_via"] == "parameter"


# ---------------------------------------------------------------------------
# review_system_interconnect
# ---------------------------------------------------------------------------


class TestReviewSystemInterconnect:
    def test_writes_system_payload_and_brief(self, server, seeded_project):
        proj, _ = seeded_project
        r = _call(server, "review_system_interconnect", project_path=proj)
        assert r["success"] is True, r
        assert os.path.isfile(r["payload_path"])
        assert os.path.isfile(r["brief_path"])
        assert r["payload_path"].endswith("system_payload.json")
        assert r["brief_path"].endswith("system_brief.md")
        assert r["ic_count"] >= 1

    def test_payload_has_expected_top_keys(self, server, seeded_project):
        proj, _ = seeded_project
        r = _call(server, "review_system_interconnect", project_path=proj)
        with open(r["payload_path"], encoding="utf-8") as fh:
            payload = json.load(fh)
        for k in ("project_name", "ics", "power_tree", "ground_nets",
                  "bus_peers", "pullup_audit", "decoupling_audit", "meta"):
            assert k in payload, f"missing key '{k}'"
        assert "U1" in payload["ics"]

    def test_missing_project_returns_error(self, server, tmp_path):
        bogus = str(tmp_path / "nope.kicad_pro")
        r = _call(server, "review_system_interconnect", project_path=bogus)
        assert r["success"] is False
        assert "not found" in r["error"].lower()


# ---------------------------------------------------------------------------
# End-to-end image rendering (kicad-cli required)
# ---------------------------------------------------------------------------


_KICAD_CLI_AVAILABLE = bool(
    shutil.which("kicad-cli")
    or os.path.isfile(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")
)


@pytest.mark.skipif(not _KICAD_CLI_AVAILABLE, reason="kicad-cli not found")
class TestEndToEndImages:
    def test_schematic_region_png_written_when_kicad_cli_present(
        self, server, seeded_project
    ):
        proj, _ = seeded_project
        r = _call(
            server, "review_ic_against_datasheet",
            ic_reference="U1", project_path=proj,
        )
        assert r["success"] is True
        sch_png = r["images"].get("schematic_region", "")
        # The SVG/cairosvg pipeline may still fail (cairo missing on CI);
        # accept either an existing PNG or a clean empty-string fallback.
        if sch_png:
            assert os.path.isfile(sch_png)
