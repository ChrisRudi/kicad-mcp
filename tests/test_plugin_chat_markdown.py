# SPDX-License-Identifier: GPL-3.0-or-later
"""Markdown-Rendering fürs Chat-Panel (0.8.7): pure Segment-Logik.

Der harte Vertrag ist die LINK-INVARIANTE: parse() ändert nur Marker, nie
Wortlaut — der zusammengefügte Segment-Text ist der Originaltext ohne
Marker, damit ``board_links.tokenize`` pro Segment weiterhin jede
Referenz/jedes Netz findet (Board-Links MÜSSEN funktionieren, User-Auflage).
"""

from __future__ import annotations

from plugin import chat_markdown as md
from plugin import chat_theme, superfeatures


def _joined(text: str) -> str:
    return "".join(seg for seg, _ in md.parse(text))


class TestInline:
    def test_bold_is_marker_free_and_styled(self):
        segs = md.parse("Das ist **wichtig** hier.")
        assert ("wichtig", md.BOLD) in segs
        assert _joined("Das ist **wichtig** hier.") == "Das ist wichtig hier."

    def test_inline_code_keeps_wording(self):
        segs = md.parse("Netz `GND` prüfen")
        assert ("GND", md.CODE) in segs
        assert _joined("Netz `GND` prüfen") == "Netz GND prüfen"

    def test_bold_board_ref_stays_tokenizable(self):
        # **R12** → Segment "R12" — erst OHNE Marker kann der Tokenizer matchen
        segs = md.parse("**R12** ist zu heiß")
        assert segs[0] == ("R12", md.BOLD)

    def test_underscores_and_single_asterisk_untouched(self):
        # Netznamen wie GND_3V3 und Mathe wie 3*4 dürfen NIE zerrissen werden
        assert _joined("GND_3V3 und 3*4=12") == "GND_3V3 und 3*4=12"
        assert all(s == md.TEXT for _t, s in md.parse("GND_3V3 und 3*4=12"))

    def test_code_span_may_contain_bold_markers(self):
        segs = md.parse("nutze `a ** b` dafür")
        assert ("a ** b", md.CODE) in segs


class TestBlocks:
    def test_heading_stripped_and_styled(self):
        segs = md.parse("## Übersicht\nText")
        assert segs[0] == ("Übersicht", md.HEADING)
        assert _joined("## Übersicht\nText") == "Übersicht\nText"

    def test_bullets_become_dots(self):
        segs = md.parse("- erstens\n* zweitens")
        assert segs[0] == ("• ", md.TEXT)
        assert ("erstens", md.TEXT) in segs
        assert ("• ", md.TEXT) in segs[2:]

    def test_fenced_block_is_raw_and_fences_vanish(self):
        text = "vorher\n```python\nx = R12  # kein Link\n```\nnachher"
        segs = md.parse(text)
        styles = {s for _t, s in segs}
        assert md.CODEBLOCK in styles
        block = [t for t, s in segs if s == md.CODEBLOCK]
        assert block == ["x = R12  # kein Link\n"]
        assert "```" not in _joined(text)

    def test_rule_renders_as_line(self):
        segs = md.parse("oben\n---\nunten")
        assert any(s == md.RULE and "─" in t for t, s in segs)

    def test_plain_text_passthrough(self):
        text = "Zeile eins\nZeile zwei\n"
        assert _joined(text) == text
        assert all(s == md.TEXT for _t, s in md.parse(text))

    def test_no_empty_segments(self):
        assert all(t for t, _s in md.parse("**a**`b`\n\n- c\n# d"))


class TestThemeCoupling:
    def test_every_segment_style_has_a_theme_entry(self):
        for style in (md.TEXT, md.BOLD, md.HEADING, md.CODE, md.CODEBLOCK,
                      md.RULE):
            assert style in chat_theme.MARKDOWN_STYLES

    def test_every_category_has_a_color(self):
        # Gruppenfarben-Guard: neue Kategorie ⇒ Farbe ergänzen (chat_theme)
        for cat_key, _label in superfeatures.CATEGORIES:
            assert cat_key in chat_theme.CATEGORY_COLORS