# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the pure startup-banner builders (Dok 2). No wx/kipy here —
the panel wires these strings in; the logic lives in plain functions."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from plugin import banner, i18n


@pytest.fixture(autouse=True)
def _force_german():
    """Banner-Wortlaut ist seit dem 1. Linux-Smoke sprachabhängig; diese
    Tests prüfen die deutsche Quelle, also die Sprache deterministisch
    setzen (globaler i18n-Zustand kann von Nachbar-Tests auf 'en' stehen)."""
    prev = i18n.get_lang()
    i18n.set_lang("de")
    yield
    i18n.set_lang(prev)


class TestRecommendMailto:
    def test_is_a_mailto_with_no_recipient(self):
        href = banner.recommend_mailto()
        parts = urlsplit(href)
        assert parts.scheme == "mailto" and parts.path == ""

    def test_subject_and_body_url_encoded(self):
        q = parse_qs(urlsplit(banner.recommend_mailto()).query)
        assert "KiCad" in q["subject"][0]
        assert banner.REPO_URL in q["body"][0]

    def test_idempotent(self):
        assert banner.recommend_mailto() == banner.recommend_mailto()


class TestInteractionGuide:
    def test_mentions_clickable_and_limits(self):
        guide = banner.interaction_guide()
        assert "klickbar" in guide
        assert "Auswahl einbeziehen" in guide  # P1 affordance documented
        assert "3D-Viewer" in guide            # the "not possible" limit shown


class TestSummaryLines:
    def test_renders_counts_and_prefix(self):
        summary = {"footprints": 4, "nets": 2, "layers": ["F.Cu", "B.Cu"],
                   "by_prefix": {"R": 2, "U": 1, "C": 1}}
        lines = banner.summary_lines(summary)
        text = "\n".join(lines)
        assert "Footprints   4" in text and "Netze   2" in text
        assert "Lagen   2" in text and "F.Cu" in text
        assert "R:2" in text and "U:1" in text

    def test_size_line_present_when_extent_given(self):
        lines = banner.summary_lines(
            {"footprints": 0, "nets": 0, "layers": [], "by_prefix": {}},
            extent_mm=(58.0, 42.0))
        assert any("58.0 × 42.0 mm" in ln for ln in lines)


class TestBannerI18n:
    """1. Linux-Smoke-Befund: Banner/Guide blieben Deutsch im EN-Modus."""

    def test_summary_and_guide_translate_to_english(self):
        i18n.set_lang("en")
        try:
            guide = banner.interaction_guide()
            assert "How to work with me" in guide
            assert "clickable" in guide and "klickbar" not in guide
            text = "\n".join(banner.summary_lines(
                {"footprints": 4, "nets": 2, "layers": ["F.Cu"],
                 "by_prefix": {"R": 2}}))
            assert "Nets   2" in text and "Netze" not in text
            assert "Board" in text and "Layers" in text
        finally:
            i18n.set_lang("de")

    def test_size_line_dropped_without_extent(self):
        lines = banner.summary_lines(
            {"footprints": 1, "nets": 0, "layers": [], "by_prefix": {"R": 1}})
        assert all("mm" not in ln for ln in lines)

    def test_no_prefix_line_when_empty(self):
        lines = banner.summary_lines(
            {"footprints": 0, "nets": 0, "layers": [], "by_prefix": {}})
        assert all("Bestückung" not in ln for ln in lines)
