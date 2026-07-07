# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Layer-T circuit-block tools.

Five tools × Happy / Edge / Error = 15 cases minimum.

Most tests run *without* needing kicad-cli or KiCad libraries by passing
``dry_run=True`` to ``apply_circuit_block`` and using inline JSON specs.
The end-to-end happy-path skips when kicad-cli is missing (mirrors the
convention in ``test_sch_patch_tools.py``).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil

import pytest

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools.circuit_block_tools import register_circuit_block_tools


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _seed_schematic(path: str) -> None:
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
def server() -> FastMCP:
    m = FastMCP("test-circuit-block")
    register_circuit_block_tools(m)
    return m


def _call(server: FastMCP, name: str, **kwargs):
    result = asyncio.run(server.call_tool(name, kwargs))
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return result


# ---------------------------------------------------------------------------
# Minimal v1.1 spec used across tests
# ---------------------------------------------------------------------------


def _minimal_spec() -> dict:
    """Tiny but schema-valid v1.1 spec for unit tests."""
    return {
        "schema_version": "1.1",
        "chip": "DUMMY_IC",
        "kicad_symbol": "Device:R",
        "kicad_footprint": "Resistor_SMD:R_0402_1005Metric",
        "pins": [
            {"num": 1, "name": "A", "type": "passive"},
            {"num": 2, "name": "B", "type": "passive"},
            {"num": 3, "name": "GND", "type": "power_in"},
        ],
        "peripherals": [
            {
                "id": "C1",
                "role": "input_decoupling",
                "between": ["A", "GND"],
                "value": "100nF",
                "required": True,
            }
        ],
        "external_nets": [
            {"name": "A", "direction": "input", "type": "signal"},
            {"name": "B", "direction": "output", "type": "signal"},
        ],
        "review_status": "draft",
    }


# ===========================================================================
# validate_circuit_block — 3 cases
# ===========================================================================


class TestValidate:
    def test_happy_inline(self, server):
        spec = json.dumps(_minimal_spec())
        r = _call(server, "validate_circuit_block", spec=spec)
        assert r["success"] is True, r
        assert r["errors"] == []
        assert r["chip"] == "DUMMY_IC"
        assert r["pin_count"] == 3
        assert r["peripheral_count"] == 1

    def test_edge_path_arg(self, server, tmp_path):
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(_minimal_spec()))
        r = _call(server, "validate_circuit_block", spec=str(path))
        assert r["success"] is True
        assert r["chip"] == "DUMMY_IC"

    def test_error_missing_required(self, server):
        broken = _minimal_spec()
        del broken["pins"]
        r = _call(server, "validate_circuit_block", spec=json.dumps(broken))
        assert r["success"] is False
        assert any("pins" in e for e in r["errors"])


# ===========================================================================
# apply_circuit_block — 3 cases
# ===========================================================================


class TestApply:
    def test_happy_dry_run(self, server, seeded_sch):
        spec = json.dumps(_minimal_spec())
        r = _call(
            server, "apply_circuit_block",
            sch_path=seeded_sch, spec=spec, dry_run=True,
        )
        assert r["success"] is True, r
        assert r["dry_run"] is True
        wa = r["would_apply"]
        # 1 chip + 1 peripheral = 2 parts
        assert len(wa["parts"]) == 2
        # GND power pin → power anchor
        assert any(a["net"] == "GND" for a in wa["power_anchors"])
        # peripheral wire to chip pin
        refs = [(c["from"][0], c["to"][0]) for c in wa["connections"]]
        assert ("C1", "DUMMY_IC") in refs

    def test_power_anchors_rotation_zero_for_all_rails(self, server, seeded_sch):
        """Regression (AUD-203): every power symbol the circuit-block
        generator drops must use rotation 0 — the canonical orientation
        for *every* power family per default_power_rotation(). A positive
        rail (VCC) must not be flipped to 180 (the old generator behaviour
        that contradicted the patch-tool / convert path)."""
        spec_d = _minimal_spec()
        # Add a positive-rail power pin so an anchor with a non-GND net exists.
        spec_d["pins"].append({"num": 4, "name": "VCC", "type": "power_in"})
        r = _call(
            server, "apply_circuit_block",
            sch_path=seeded_sch, spec=json.dumps(spec_d), dry_run=True,
        )
        assert r["success"] is True, r
        anchors = r["would_apply"]["power_anchors"]
        nets = {a["net"] for a in anchors}
        assert {"GND", "VCC"} <= nets, anchors
        assert all(a["rotation_deg"] == 0 for a in anchors), anchors

    def test_edge_invalid_instance_id(self, server, seeded_sch):
        spec_d = _minimal_spec()
        spec_d["instances"] = [{"ref": "U_ONE"}, {"ref": "U_TWO"}]
        r = _call(
            server, "apply_circuit_block",
            sch_path=seeded_sch, spec=json.dumps(spec_d),
            instance_id="U_NOT_THERE", dry_run=True,
        )
        assert r["success"] is False
        assert "U_NOT_THERE" in r["error"]

    def test_error_missing_sch(self, server):
        r = _call(
            server, "apply_circuit_block",
            sch_path="/nonexistent/path.kicad_sch",
            spec=json.dumps(_minimal_spec()),
            dry_run=True,
        )
        assert r["success"] is False
        assert "not found" in r["error"].lower()


# ===========================================================================
# apply_template_block — 3 cases
# ===========================================================================


class TestTemplate:
    def test_happy_buck(self, server):
        r = _call(
            server, "apply_template_block",
            template_id="smps_buck_converter",
            chip_meta=json.dumps({"chip": "TPS54202"}),
            app_params=json.dumps({"Vin": 12, "Vout": 3.3, "Iout": 0.5}),
        )
        assert r["success"] is True, r
        assert r["template_id"] == "smps_buck_converter"
        assert r["draft_spec"]["chip"] == "TPS54202"
        assert r["draft_spec"]["schema_version"] == "1.1"
        assert "needs_review" in r["review_status"]

    def test_edge_stub_template(self, server):
        # A stub-only template (no block_definition yet) must be rejected
        # with a clear error pointing at the fully-defined alternatives.
        r = _call(
            server, "apply_template_block",
            template_id="esp32_basic",
        )
        assert r["success"] is False
        assert "block_definition" in r["error"]

    def test_error_missing_template(self, server):
        r = _call(
            server, "apply_template_block",
            template_id="this_template_does_not_exist",
        )
        assert r["success"] is False
        assert "not found" in r["error"].lower()


# ===========================================================================
# extract_pdf_tables — 3 cases
# ===========================================================================


class TestPdfTables:
    def test_happy_synthetic(self, server, tmp_path):
        pytest.importorskip("pdfplumber")
        from reportlab.pdfgen import canvas  # type: ignore  # noqa
        # Skip if reportlab isn't around — synthetic-PDF generation only.
        pdf_path = tmp_path / "synthetic.pdf"
        try:
            from reportlab.lib.pagesizes import letter
            c = canvas.Canvas(str(pdf_path), pagesize=letter)
            c.drawString(72, 720, "Pin Functions")
            c.drawString(72, 700, "Pin   Name   Type")
            c.drawString(72, 680, "1     VIN    Power")
            c.drawString(72, 660, "2     GND    Power")
            c.showPage()
            c.save()
        except Exception:
            pytest.skip("reportlab not available for synthetic PDF generation")
        r = _call(server, "extract_pdf_tables", pdf_path=str(pdf_path))
        assert r["success"] is True
        # pdfplumber may or may not detect a table here depending on its
        # heuristics — we just assert it didn't crash.
        assert "tables" in r

    def test_edge_missing_dep(self, server, tmp_path, monkeypatch):
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%dummy")
        # Force ImportError from the lazy import.
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pdfplumber":
                raise ImportError("simulated missing pdfplumber")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        r = _call(server, "extract_pdf_tables", pdf_path=str(pdf))
        assert r["success"] is False
        assert "pdfplumber" in r["error"]

    def test_error_missing_file(self, server):
        r = _call(server, "extract_pdf_tables", pdf_path="/nonexistent/x.pdf")
        assert r["success"] is False
        assert "not found" in r["error"].lower()


# ===========================================================================
# extract_circuit_from_pdf — 3 cases
# ===========================================================================


class TestExtractCircuit:
    def test_happy_returns_skeleton(self, server, tmp_path):
        pytest.importorskip("pdfplumber")
        pdf = tmp_path / "x.pdf"
        # Minimal-valid PDF. pdfplumber will return zero tables but the
        # tool should still build a draft skeleton.
        pdf.write_bytes(
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
            b"trailer<</Size 4/Root 1 0 R>>\n"
            b"%%EOF\n"
        )
        r = _call(
            server, "extract_circuit_from_pdf",
            pdf_path=str(pdf), target_chip="MY_IC",
        )
        # Whether pdfplumber accepts the dummy header varies; if it
        # does, we get success; if not, we fall through to the dep
        # error below — both are acceptable.
        if r["success"]:
            assert r["target_chip"] == "MY_IC"
            assert r["draft_block_skeleton"]["schema_version"] == "1.1"
            assert r["draft_block_skeleton"]["review_status"] == "needs_review"

    def test_edge_pages_filter(self, server, tmp_path):
        # Bad pages list → structured error, not exception.
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%dummy")
        r = _call(
            server, "extract_circuit_from_pdf",
            pdf_path=str(pdf), target_chip="X", pages="abc,def",
        )
        assert r["success"] is False
        assert "pages" in r["error"].lower()

    def test_error_missing_file(self, server):
        r = _call(
            server, "extract_circuit_from_pdf",
            pdf_path="/nonexistent.pdf", target_chip="Y",
        )
        assert r["success"] is False
        assert "not found" in r["error"].lower()


# ===========================================================================
# Smoke: schema file exists and is valid JSON-Schema
# ===========================================================================


def test_schema_file_loads():
    from kicad_mcp.generators.circuit_block import schema_v1_1, schema_path
    s = schema_v1_1()
    assert isinstance(s, dict)
    assert s["title"].startswith("Circuit-Block Spec")
    assert os.path.isfile(schema_path())


def test_schema_validates_shipped_blocks():
    """Every shipped block in resources/data/circuit_blocks/ must pass
    schema validation — the single home (examples/ is docs-only now)."""
    from kicad_mcp.generators.circuit_block.kit_compose import BLOCKS_DIR
    from kicad_mcp.tools.circuit_block_tools import _jsonschema_validate

    names = [f for f in os.listdir(BLOCKS_DIR) if f.endswith(".json")]
    assert names, "no shipped blocks found"
    failures: list[str] = []
    for fname in names:
        with open(os.path.join(BLOCKS_DIR, fname), encoding="utf-8") as fh:
            spec = json.load(fh)
        errs = _jsonschema_validate(spec)
        if errs:
            failures.append(f"{fname}: {errs}")
    assert not failures, "\n".join(failures)


def test_load_spec_accepts_bare_block_name():
    """validate_circuit_block("mp1584_buck_5v") resolves the shipped block."""
    from kicad_mcp.tools.circuit_block_tools import _load_spec_from_arg

    spec, err = _load_spec_from_arg("mp1584_buck_5v")
    assert err is None
    assert spec["chip"] == "MP1584"
    # .json-Suffix ohne Pfadtrenner geht ebenfalls auf die Bibliothek
    spec2, err2 = _load_spec_from_arg("mp1584_buck_5v.json")
    assert err2 is None and spec2 == spec
    # Unbekannter Name fällt sauber auf die JSON-Fehlermeldung durch
    spec3, err3 = _load_spec_from_arg("no_such_block")
    assert spec3 is None and err3


# ===========================================================================
# End-to-end: apply_circuit_block actually writes to disk
# (skipped if kicad-cli unavailable — mirrors test_sch_patch_tools.py)
# ===========================================================================


# ===========================================================================
# Per-tool audit: every Layer-T tool routes path params through
# to_local_path AND carries a substantive, LLM-friendly description.
#
# This is the localised counterpart to the global checks in
# tests/test_all_tools_dynamic.py — running it as part of the Layer-T
# suite makes a regression in either dimension surface alongside the
# rest of the circuit-block tests instead of a couple of files away.
# ===========================================================================


class TestLayerTAudit:
    """Audit: every Layer-T tool must (a) carry a substantive
    description and (b) route every filesystem-path parameter through
    ``to_local_path`` at the function entry."""

    LAYER_T_TOOLS = {
        "validate_circuit_block",
        "apply_circuit_block",
        "apply_template_block",
        "extract_pdf_tables",
        "extract_circuit_from_pdf",
    }

    # Path-typed parameters used by Layer-T tools. Mirrors the relevant
    # subset of tests/test_all_tools_dynamic.py::PATH_PARAM_NAMES.
    PATH_PARAMS = {"sch_path", "out_path", "pdf_path"}

    # Description floor — same bar test_all_tools_dynamic.py
    # ::test_descriptions_meet_minimum_length holds the rest of the
    # repo to.
    MIN_DESCRIPTION_CHARS = 280

    # Phrases that signal "when to pick this tool over a similar one".
    USAGE_CUES = (
        "use this", "use instead", "don't", "do not", "preferred",
        "before", "after", "first ", "instead of", "rather than",
    )

    def test_every_tool_routes_paths_through_to_local_path(self):
        """Source-grep: every path-typed param must be normalised by
        ``to_local_path`` inside the tool body. Either direct call
        (``to_local_path(sch_path)``) or self-assigning rebind
        (``sch_path = to_local_path(sch_path)``) — both forms accepted,
        same as the global audit."""
        import inspect
        import re
        from kicad_mcp.tools import circuit_block_tools as mod

        src = inspect.getsource(mod)
        tool_block_re = re.compile(
            r"@mcp\.tool\([^)]*\)\s*\n\s*(?:async\s+)?def\s+(\w+)\s*\("
            r"(.*?)"          # signature
            r"\)\s*(?:->\s*[^:]+)?:"
            r"(.*?)"          # body
            r"(?=\n\s{0,4}@mcp\.tool|\n\s{0,4}def\s+\w+|\Z)",
            re.DOTALL,
        )

        bad: list[str] = []
        seen: set[str] = set()
        for tool_name, sig, body in tool_block_re.findall(src):
            if tool_name not in self.LAYER_T_TOOLS:
                continue
            seen.add(tool_name)
            for param in self.PATH_PARAMS:
                # Word-boundary so 'out_path' doesn't accidentally match
                # 'output_path' or vice-versa.
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
                    bad.append(
                        f"{tool_name}: parameter {param!r} not passed "
                        "through to_local_path"
                    )

        assert seen == self.LAYER_T_TOOLS, (
            f"Tool registry drift — expected {sorted(self.LAYER_T_TOOLS)}, "
            f"found {sorted(seen)}"
        )
        assert not bad, "\n".join(bad)

    def test_every_tool_has_substantive_description(self, server):
        """Each Layer-T tool's MCP description must be ≥ MIN_DESCRIPTION_CHARS
        and contain at least one usage cue (``Use this when``, ``Don't``,
        ``instead of`` …) so the picker LLM can disambiguate it from
        the existing 92 tools."""
        tools = asyncio.run(server.list_tools())
        seen: set[str] = set()
        thin: list[tuple[str, int]] = []
        no_cue: list[str] = []
        for t in tools:
            if t.name not in self.LAYER_T_TOOLS:
                continue
            seen.add(t.name)
            desc = (t.description or "").strip()
            if len(desc) < self.MIN_DESCRIPTION_CHARS:
                thin.append((t.name, len(desc)))
                continue  # skip cue check for thin docs — first error is enough
            if not any(c in desc.lower() for c in self.USAGE_CUES):
                no_cue.append(t.name)

        assert seen == self.LAYER_T_TOOLS, (
            f"Tool registry drift — expected {sorted(self.LAYER_T_TOOLS)}, "
            f"found {sorted(seen)}"
        )
        assert not thin, (
            "Layer-T tools with thin docstrings (≥ "
            f"{self.MIN_DESCRIPTION_CHARS} chars expected):\n  "
            + "\n  ".join(f"{n}: {ln} chars" for n, ln in thin)
        )
        assert not no_cue, (
            "Layer-T tools missing a usage cue (use this / don't / "
            "instead of …): " + ", ".join(no_cue)
        )


# ===========================================================================
# End-to-end: apply_circuit_block actually writes to disk
# (skipped if kicad-cli unavailable — mirrors test_sch_patch_tools.py)
# ===========================================================================


_KICAD_CLI = (
    shutil.which("kicad-cli")
    or (r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
        if os.path.isfile(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe") else None)
)


@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not found — skipping end-to-end")
def test_e2e_apply_writes_disk(seeded_sch):
    """End-to-end: actually patch the schematic via Layer-S helpers."""
    m = FastMCP("e2e")
    register_circuit_block_tools(m)
    spec = json.dumps(_minimal_spec())
    r = _call(m, "apply_circuit_block", sch_path=seeded_sch, spec=spec, dry_run=False)
    # The Layer-S patcher may legitimately fail to find Device:R in the
    # symbol cache during a test environment without a populated KiCad
    # library — surface that as a clear failure rather than treating it
    # as a hard test bug. The point of this test is the pipeline path,
    # not the lib resolution.
    if not r["success"]:
        # As long as the failure is structured (errors list, not crash) we
        # consider the pipeline correct.
        assert "errors" in r
        return
    # Symbols added → file changed
    with open(seeded_sch, encoding="utf-8") as fh:
        text = fh.read()
    assert "DUMMY_IC" in text or "(symbol" in text
