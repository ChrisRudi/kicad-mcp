# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for kicad_mcp.tools.footprint_search_tools.

We build a tiny synthetic ``footprints/`` tree on disk so the tests do not
depend on a real KiCad install. The cache file is redirected into the temp
directory via monkeypatch so concurrent test runs cannot collide.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest

from kicad_mcp.tools import footprint_search_tools as fst


# ---------------------------------------------------------------------------
# Synthetic .kicad_mod content
# ---------------------------------------------------------------------------


C_0402_MOD = textwrap.dedent(
    """\
    (footprint "C_0402_1005Metric"
    \t(version 20240108)
    \t(generator "test")
    \t(layer "F.Cu")
    \t(descr "Capacitor SMD 0402")
    \t(tags "capacitor smd 0402")
    \t(at 0 0 0)
    \t(fp_line (start -0.6 -0.4) (end 0.6 -0.4) (layer "F.Fab"))
    \t(fp_line (start 0.6 -0.4) (end 0.6 0.4) (layer "F.Fab"))
    \t(fp_line (start 0.6 0.4) (end -0.6 0.4) (layer "F.Fab"))
    \t(fp_line (start -0.6 0.4) (end -0.6 -0.4) (layer "F.Fab"))
    \t(pad "1" smd rect (at -0.5 0) (size 0.5 0.6) (layers "F.Cu" "F.Mask" "F.Paste"))
    \t(pad "2" smd rect (at  0.5 0) (size 0.5 0.6) (layers "F.Cu" "F.Mask" "F.Paste"))
    )
    """
)

C_0402_HANDSOLDER_MOD = textwrap.dedent(
    """\
    (footprint "C_0402_1005Metric_Pad0.74x0.62mm_HandSolder"
    \t(version 20240108)
    \t(generator "test")
    \t(layer "F.Cu")
    \t(descr "Capacitor SMD 0402 hand-solder pads")
    \t(tags "capacitor smd 0402 handsolder")
    \t(at 0 0 0)
    \t(fp_line (start -0.7 -0.4) (end 0.7 -0.4) (layer "F.Fab"))
    \t(fp_line (start 0.7 -0.4) (end 0.7 0.4) (layer "F.Fab"))
    \t(fp_line (start 0.7 0.4) (end -0.7 0.4) (layer "F.Fab"))
    \t(fp_line (start -0.7 0.4) (end -0.7 -0.4) (layer "F.Fab"))
    \t(pad "1" smd rect (at -0.6 0) (size 0.74 0.62) (layers "F.Cu" "F.Mask" "F.Paste"))
    \t(pad "2" smd rect (at  0.6 0) (size 0.74 0.62) (layers "F.Cu" "F.Mask" "F.Paste"))
    )
    """
)

QFN28_MOD = textwrap.dedent(
    """\
    (footprint "QFN-28-1EP_5x5mm_P0.5mm_EP3.1x3.1mm_ThermalVias"
    \t(version 20240108)
    \t(generator "test")
    \t(layer "F.Cu")
    \t(descr "QFN-28 5x5mm, 0.5mm pitch, 3.1mm exposed pad")
    \t(tags "qfn 28 5x5 thermal")
    \t(at 0 0 0)
    \t(fp_line (start -2.5 -2.5) (end 2.5 -2.5) (layer "F.Fab"))
    \t(fp_line (start 2.5 -2.5) (end 2.5 2.5) (layer "F.Fab"))
    \t(fp_line (start 2.5 2.5) (end -2.5 2.5) (layer "F.Fab"))
    \t(fp_line (start -2.5 2.5) (end -2.5 -2.5) (layer "F.Fab"))
"""
    + "".join(
        f'\t(pad "{i + 1}" smd rect (at -2.5 {i * 0.5 - 1.5}) (size 0.6 0.3) '
        '(layers "F.Cu" "F.Mask" "F.Paste"))\n'
        for i in range(28)
    )
    + '\t(pad "29" smd rect (at 0 0) (size 3.1 3.1) (layers "F.Cu" "F.Mask" "F.Paste"))\n'
    ")\n"
)

SOT23_MOD = textwrap.dedent(
    """\
    (footprint "SOT-23"
    \t(version 20240108)
    \t(generator "test")
    \t(layer "F.Cu")
    \t(descr "SOT-23, 3 pin small outline")
    \t(tags "sot-23")
    \t(at 0 0 0)
    \t(fp_line (start -1.45 -0.85) (end 1.45 -0.85) (layer "F.Fab"))
    \t(fp_line (start 1.45 -0.85) (end 1.45 0.85) (layer "F.Fab"))
    \t(fp_line (start 1.45 0.85) (end -1.45 0.85) (layer "F.Fab"))
    \t(fp_line (start -1.45 0.85) (end -1.45 -0.85) (layer "F.Fab"))
    \t(pad "1" smd rect (at -1.0 -1.0) (size 0.6 0.7) (layers "F.Cu" "F.Mask" "F.Paste"))
    \t(pad "2" smd rect (at  1.0 -1.0) (size 0.6 0.7) (layers "F.Cu" "F.Mask" "F.Paste"))
    \t(pad "3" smd rect (at  0.0  1.0) (size 0.6 0.7) (layers "F.Cu" "F.Mask" "F.Paste"))
    )
    """
)


@pytest.fixture
def synthetic_lib(tmp_path, monkeypatch):
    """Create a tiny library tree + redirect cache to tmp_path."""
    root = tmp_path / "footprints"
    cap_lib = root / "Capacitor_SMD.pretty"
    cap_lib.mkdir(parents=True)
    (cap_lib / "C_0402_1005Metric.kicad_mod").write_text(C_0402_MOD, encoding="utf-8")
    (cap_lib / "C_0402_1005Metric_Pad0.74x0.62mm_HandSolder.kicad_mod").write_text(
        C_0402_HANDSOLDER_MOD, encoding="utf-8"
    )
    qfn_lib = root / "Package_DFN_QFN.pretty"
    qfn_lib.mkdir(parents=True)
    (qfn_lib / "QFN-28-1EP_5x5mm_P0.5mm_EP3.1x3.1mm_ThermalVias.kicad_mod").write_text(
        QFN28_MOD, encoding="utf-8"
    )
    sot_lib = root / "Package_TO_SOT_SMD.pretty"
    sot_lib.mkdir(parents=True)
    (sot_lib / "SOT-23.kicad_mod").write_text(SOT23_MOD, encoding="utf-8")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(fst, "CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(fst, "INDEX_FILE", str(cache_dir / "footprint_index.json"))
    return str(root)


# ---------------------------------------------------------------------------
# Helpers used to invoke MCP-decorated tools.
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_with_search():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    fst.register_footprint_search_tools(mcp)
    return mcp


def _call(mcp, name, **kwargs):
    import asyncio

    async def _do():
        result = await mcp.call_tool(name, kwargs)
        if isinstance(result, tuple):
            return result[1] if len(result) > 1 else result[0]
        return result

    return asyncio.run(_do())


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestParser:
    def test_parses_pad_count_and_body(self, tmp_path):
        path = tmp_path / "x.kicad_mod"
        path.write_text(C_0402_MOD, encoding="utf-8")
        rec = fst._parse_one_kicad_mod(str(path))
        assert rec is not None
        assert rec.pad_count == 2
        assert rec.smd_pads == 2
        assert rec.tht_pads == 0
        assert rec.body_w_mm == pytest.approx(1.2, abs=0.05)
        assert rec.body_h_mm == pytest.approx(0.8, abs=0.05)
        assert rec.package_family.startswith("0402") or rec.package_family.startswith("C")

    def test_qfn_pin_count(self, tmp_path):
        path = tmp_path / "qfn.kicad_mod"
        path.write_text(QFN28_MOD, encoding="utf-8")
        rec = fst._parse_one_kicad_mod(str(path))
        assert rec is not None
        # 28 normal pads + 1 exposed pad
        assert rec.pad_count == 29
        assert "QFN" in rec.package_family


class TestPackageFamilyDetection:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("C_0402_1005Metric", "0402"),
            ("R_0603_1608Metric", "0603"),
            ("QFN-28-1EP_5x5mm", "QFN28"),
            ("SOT-23-5", "SOT235"),
            ("HTSSOP-24-1EP", "HTSSOP24"),
            ("USB_C_Receptacle_GCT", "USBC"),
        ],
    )
    def test_known_families(self, name, expected):
        got = fst._detect_package_family(name)
        # Allow for slight formatting differences (dashes stripped).
        assert expected.replace("-", "") in got.replace("-", "")


# ---------------------------------------------------------------------------
# Indexing + cache behaviour
# ---------------------------------------------------------------------------


class TestIndex:
    def test_first_call_builds(self, mcp_with_search, synthetic_lib):
        out = _call(mcp_with_search, "index_kicad_footprints", library_root=synthetic_lib)
        assert out["success"] is True
        assert out["rebuilt"] is True
        assert out["record_count"] == 4
        assert os.path.isfile(out["index_path"])
        # Cache JSON is valid
        meta = json.loads(Path(out["index_path"]).read_text(encoding="utf-8"))
        assert meta["record_count"] == 4

    def test_second_call_uses_cache(self, mcp_with_search, synthetic_lib):
        first = _call(mcp_with_search, "index_kicad_footprints", library_root=synthetic_lib)
        second = _call(mcp_with_search, "index_kicad_footprints", library_root=synthetic_lib)
        assert first["rebuilt"] is True
        assert second["rebuilt"] is False
        assert second["record_count"] == first["record_count"]

    def test_force_rebuild(self, mcp_with_search, synthetic_lib):
        _call(mcp_with_search, "index_kicad_footprints", library_root=synthetic_lib)
        out = _call(
            mcp_with_search, "index_kicad_footprints",
            library_root=synthetic_lib, force_rebuild=True,
        )
        assert out["rebuilt"] is True

    def test_missing_root_reports_error(self, mcp_with_search, monkeypatch):
        monkeypatch.setattr(fst, "_default_kicad_lib_root", lambda: "")
        out = _call(mcp_with_search, "index_kicad_footprints")
        assert out["success"] is False
        assert "library_root" in out["error"]


# ---------------------------------------------------------------------------
# search_footprints
# ---------------------------------------------------------------------------


class TestSearch:
    def test_substring_match(self, mcp_with_search, synthetic_lib):
        out = _call(
            mcp_with_search, "search_footprints",
            query="0402", library_root=synthetic_lib,
        )
        assert out["success"] is True
        names = [r["name"] for r in out["results"]]
        # Both 0402 variants should show up
        assert any("0402" in n for n in names)

    def test_canonical_beats_handsolder(self, mcp_with_search, synthetic_lib):
        out = _call(
            mcp_with_search, "search_footprints",
            query="C_0402", library_root=synthetic_lib,
        )
        # The non-handsolder name should rank first.
        assert out["results"][0]["name"] == "C_0402_1005Metric"

    def test_qfn_search(self, mcp_with_search, synthetic_lib):
        out = _call(
            mcp_with_search, "search_footprints",
            query="QFN-28", library_root=synthetic_lib,
        )
        assert out["result_count"] >= 1
        assert "QFN-28" in out["results"][0]["name"]


# ---------------------------------------------------------------------------
# find_footprint_by_specs
# ---------------------------------------------------------------------------


class TestFindBySpecs:
    def test_pad_count_filter(self, mcp_with_search, synthetic_lib):
        out = _call(
            mcp_with_search, "find_footprint_by_specs",
            pad_count=3, library_root=synthetic_lib,
        )
        assert out["success"] is True
        # Only SOT-23 has 3 pads
        names = [r["name"] for r in out["results"]]
        assert names == ["SOT-23"]

    def test_package_filter(self, mcp_with_search, synthetic_lib):
        out = _call(
            mcp_with_search, "find_footprint_by_specs",
            package="QFN", library_root=synthetic_lib,
        )
        assert out["result_count"] == 1
        assert "QFN" in out["results"][0]["name"]

    def test_body_size_filter(self, mcp_with_search, synthetic_lib):
        out = _call(
            mcp_with_search, "find_footprint_by_specs",
            body_w_mm=5.0, body_h_mm=5.0, body_tolerance_mm=0.5,
            library_root=synthetic_lib,
        )
        # Only QFN-28 (5x5) qualifies.
        assert out["result_count"] == 1
        assert "QFN" in out["results"][0]["name"]

    def test_empty_query_rejected(self, mcp_with_search, synthetic_lib):
        out = _call(
            mcp_with_search, "find_footprint_by_specs",
            library_root=synthetic_lib,
        )
        assert out["success"] is False
        assert "at least one" in out["error"]


# ---------------------------------------------------------------------------
# suggest_builtin_for_custom
# ---------------------------------------------------------------------------


class TestSuggestForCustom:
    def test_recommends_canonical_0402(self, mcp_with_search, synthetic_lib, tmp_path):
        # Create a minimal "custom" 0402 with same body & 2 pads
        custom = tmp_path / "my_0402.kicad_mod"
        custom.write_text(
            C_0402_MOD.replace("C_0402_1005Metric", "MyHomemade_0402"),
            encoding="utf-8",
        )
        out = _call(
            mcp_with_search, "suggest_builtin_for_custom",
            custom_path=str(custom), library_root=synthetic_lib,
        )
        assert out["success"] is True
        assert out["candidates"][0]["pad_count"] == 2
        # The non-handsolder canonical FP should be the top suggestion.
        assert out["candidates"][0]["name"] == "C_0402_1005Metric"
        assert out["candidates"][0]["recommended_tag"] == \
            "[Capacitor_SMD:C_0402_1005Metric]"

    def test_recommends_qfn(self, mcp_with_search, synthetic_lib, tmp_path):
        custom = tmp_path / "my_qfn.kicad_mod"
        custom.write_text(
            QFN28_MOD.replace(
                "QFN-28-1EP_5x5mm_P0.5mm_EP3.1x3.1mm_ThermalVias", "Custom_QFN28"
            ),
            encoding="utf-8",
        )
        out = _call(
            mcp_with_search, "suggest_builtin_for_custom",
            custom_path=str(custom), library_root=synthetic_lib,
        )
        assert out["success"] is True
        assert out["candidates"][0]["pad_count"] == 29
        assert "QFN" in out["candidates"][0]["name"]
        # Confidence should be high since pad count + body size both match.
        assert out["candidates"][0]["confidence"] >= 0.8

    def test_inline_custom_text(self, mcp_with_search, synthetic_lib):
        out = _call(
            mcp_with_search, "suggest_builtin_for_custom",
            custom_text=C_0402_MOD, library_root=synthetic_lib,
        )
        assert out["success"] is True
        assert out["candidates"][0]["name"] == "C_0402_1005Metric"

    def test_missing_input_rejected(self, mcp_with_search, synthetic_lib):
        out = _call(
            mcp_with_search, "suggest_builtin_for_custom",
            library_root=synthetic_lib,
        )
        assert out["success"] is False

    def test_pad_count_mismatch_excluded(
        self, mcp_with_search, synthetic_lib, tmp_path,
    ):
        # Make a "custom" with 7 pads — none of our records have 7 pads.
        custom = tmp_path / "weird.kicad_mod"
        body = (
            "(footprint \"weird\"\n"
            "\t(version 20240108)\n"
            "\t(generator \"test\")\n"
            "\t(layer \"F.Cu\")\n"
            "\t(at 0 0 0)\n"
            "\t(fp_line (start -1 -1) (end 1 -1) (layer \"F.Fab\"))\n"
            "\t(fp_line (start 1 -1) (end 1 1) (layer \"F.Fab\"))\n"
            "\t(fp_line (start 1 1) (end -1 1) (layer \"F.Fab\"))\n"
            "\t(fp_line (start -1 1) (end -1 -1) (layer \"F.Fab\"))\n"
            + "".join(
                f'\t(pad "{i+1}" smd rect (at 0 {i*0.1}) (size 0.3 0.3) '
                '(layers "F.Cu" "F.Mask" "F.Paste"))\n'
                for i in range(7)
            )
            + ")"
        )
        custom.write_text(body, encoding="utf-8")
        out = _call(
            mcp_with_search, "suggest_builtin_for_custom",
            custom_path=str(custom), library_root=synthetic_lib,
        )
        assert out["success"] is True
        # No candidates — none of our synthetic FPs have 7 pads.
        assert out["candidates"] == []
