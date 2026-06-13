# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the AUI-dock helper's pure parts (frame detection, pane
spec). The wx/AUI attach path only runs inside KiCad — wx is imported lazily
in :mod:`plugin.dock`, so importing the module headless must work.
"""

from __future__ import annotations

from plugin import dock


class TestFrameDetection:
    def test_native_frame_name_wins(self):
        # KiCad's locale-independent window name (PCB_EDIT_FRAME_NAME).
        assert dock.looks_like_pcb_editor("PcbFrame", "")

    def test_english_title_fallback(self):
        assert dock.looks_like_pcb_editor(
            "frame_123", "board.kicad_pcb — PCB Editor")

    def test_german_title_fallback(self):
        assert dock.looks_like_pcb_editor(
            "frame_123", "board.kicad_pcb — Leiterplatteneditor")

    def test_legacy_pcbnew_title(self):
        assert dock.looks_like_pcb_editor("", "Pcbnew — board.kicad_pcb")

    def test_schematic_editor_is_not_pcb(self):
        assert not dock.looks_like_pcb_editor(
            "SchematicFrame", "board.kicad_sch — Schematic Editor")

    def test_handles_none_title(self):
        assert not dock.looks_like_pcb_editor("other", None)


class TestPaneSpec:
    def test_pane_name_is_stable(self):
        # The AUI perspective is saved under this id — never rename casually.
        assert dock.PANE_NAME == "kicad_mcp_claude_chat"

    def test_sizes_are_sane(self):
        assert dock.PANE_MIN_SIZE[0] <= dock.PANE_BEST_SIZE[0]
        assert dock.PANE_MIN_SIZE[1] <= dock.PANE_BEST_SIZE[1]
