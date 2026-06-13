# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the chat panel's Claude-Code look (pure theme module —
no wx/KiCad needed; the wx dialog itself only runs inside KiCad).
"""

from __future__ import annotations

import re

from plugin import chat_theme

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class TestPalette:
    def test_all_palette_colors_are_hex(self):
        for color in (chat_theme.BACKGROUND, chat_theme.SURFACE,
                      chat_theme.FOREGROUND, chat_theme.CLAUDE_ORANGE,
                      chat_theme.DIM, chat_theme.ERROR_RED):
            assert _HEX.match(color), color

    def test_dark_terminal_background(self):
        # "schwarzes Fenster": the background must be near-black, the
        # foreground light — otherwise it isn't the CLI look.
        assert int(chat_theme.BACKGROUND[1:3], 16) < 0x40
        assert int(chat_theme.FOREGROUND[1:3], 16) > 0xC0


class TestRoles:
    def test_all_roles_complete(self):
        for role in ("user", "claude", "error", "banner"):
            style = chat_theme.style_for(role)
            assert style["prefix"].strip()
            assert _HEX.match(style["prefix_color"])
            assert _HEX.match(style["text_color"])

    def test_unknown_role_falls_back_to_claude(self):
        assert chat_theme.style_for("???") == chat_theme.style_for("claude")

    def test_user_dimmed_claude_bright(self):
        # CLI feel: own input is muted, Claude's answer is the bright text.
        assert chat_theme.style_for("user")["text_color"] == chat_theme.DIM
        assert (chat_theme.style_for("claude")["text_color"]
                == chat_theme.FOREGROUND)


class TestSpinner:
    def test_label_cycles_frames_and_wraps(self):
        n = len(chat_theme.SPINNER_FRAMES)
        assert chat_theme.spinner_label(0).startswith(
            chat_theme.SPINNER_FRAMES[0])
        assert chat_theme.spinner_label(1).startswith(
            chat_theme.SPINNER_FRAMES[1])
        # after a full cycle the frame wraps (the seconds keep counting)
        assert chat_theme.spinner_label(n).startswith(
            chat_theme.SPINNER_FRAMES[0])

    def test_label_counts_elapsed_seconds(self):
        assert "(0s)" in chat_theme.spinner_label(0, interval_ms=150)
        assert "(1s)" in chat_theme.spinner_label(7, interval_ms=150)
        assert "(12s)" in chat_theme.spinner_label(80, interval_ms=150)

    def test_label_mentions_busy_text(self):
        assert chat_theme.STATUS_BUSY in chat_theme.spinner_label(3)
