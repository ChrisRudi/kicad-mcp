# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the options-dropdown logic: curated switches are only offered
when the installed CLI actually supports them (parsed from ``claude --help``),
and picking one merges into the free-text field without duplicating flags."""

from __future__ import annotations

from types import SimpleNamespace

from plugin import claude_options as co

_HELP = """
Usage: claude [options] [command] [prompt]

Options:
  --model <model>            Model for the current session
  --fallback-model <model>   Fallback when overloaded
  --fast                     Fast mode
  --mcp-config <configs...>  Load MCP servers
  --verbose                  Override verbose mode
  -h, --help                 Display help
"""


class TestParsing:
    def test_flags_extracted(self):
        flags = co.parse_supported_flags(_HELP)
        assert {"--model", "--fallback-model", "--fast",
                "--mcp-config", "--verbose", "--help"} <= flags

    def test_empty_text_no_flags(self):
        assert co.parse_supported_flags("") == set()
        assert co.parse_supported_flags(None) == set()


class TestAvailableOptions:
    def test_only_supported_curated_offered(self):
        options = co.available_options(_HELP)
        switches = [s for _l, s in options]
        assert "--model sonnet" in switches
        assert "--fast" in switches
        # reserved flags never appear even though the CLI supports them
        assert all(co.switch_flag(s) not in co.RESERVED_FLAGS
                   for s in switches)

    def test_unsupported_cli_hides_entry(self):
        # a help text without --fast (older CLI) must not offer fast mode
        no_fast = _HELP.replace("  --fast                     Fast mode\n", "")
        switches = [s for _l, s in co.available_options(no_fast)]
        assert "--fast" not in switches
        assert "--model sonnet" in switches

    def test_empty_help_offers_nothing(self):
        assert co.available_options("") == []


class TestApplySwitch:
    def test_append_to_empty(self):
        assert co.apply_switch("", "--model sonnet") == "--model sonnet"

    def test_same_flag_replaced_not_duplicated(self):
        assert co.apply_switch("--model sonnet", "--model opus") == "--model opus"

    def test_unrelated_switches_survive(self):
        got = co.apply_switch("--fast --model sonnet", "--model haiku")
        assert got == "--fast --model haiku"

    def test_valueless_flag_replaced(self):
        assert co.apply_switch("--fast", "--fast") == "--fast"

    def test_unbalanced_quotes_appends_safely(self):
        got = co.apply_switch('--model "son', "--fast")
        assert got.endswith("--fast")

    def test_empty_switch_is_noop(self):
        assert co.apply_switch("--fast", "") == "--fast"


class TestReadHelpAndCache:
    def test_read_help_captures_both_streams(self):
        def _run(cmd, **kw):
            assert cmd[-1] == "--help"
            return SimpleNamespace(stdout="OUT ", stderr="ERR", returncode=0)

        assert co.read_help_text(["claude"], _run=_run) == "OUT ERR"

    def test_read_help_never_raises(self):
        def _run(cmd, **kw):
            raise OSError("not installed")

        assert co.read_help_text(["claude"], _run=_run) == ""
        assert co.read_help_text([], _run=_run) == ""

    def test_cached_options_runs_help_once(self, monkeypatch):
        monkeypatch.setattr(co, "_HELP_CACHE", {})
        calls = []

        def _run(cmd, **kw):
            calls.append(cmd)
            return SimpleNamespace(stdout=_HELP, stderr="", returncode=0)

        first = co.cached_options(["my-claude"], _run=_run)
        second = co.cached_options(["my-claude"], _run=_run)
        assert first == second and first
        assert len(calls) == 1
