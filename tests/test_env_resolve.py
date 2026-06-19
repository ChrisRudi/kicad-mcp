# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the dynamic dependency resolution engine (plugin.env_resolve).

Pure logic: KiCad->kipy coupling, the environment fingerprint and the reinstall
decision, plus the guarded detection wrappers (injectable runner)."""

from __future__ import annotations

from types import SimpleNamespace

from plugin import env_resolve as er


class TestKicadMajorMinor:
    def test_full_version(self):
        assert er.kicad_major_minor("10.0.1") == "10.0"

    def test_two_parts(self):
        assert er.kicad_major_minor("11.0") == "11.0"

    def test_single_part(self):
        assert er.kicad_major_minor("9") == "9"

    def test_empty_and_none(self):
        assert er.kicad_major_minor("") == ""
        assert er.kicad_major_minor(None) == ""

    def test_numeric_head_only(self):
        assert er.kicad_major_minor("10.0.1-rc1") == "10.0"


class TestKipySpec:
    def test_known_kicad_pins(self):
        assert er.kipy_spec("10.0.1") == "kicad-python==0.7.1"

    def test_unknown_kicad_unpinned(self):
        assert er.kipy_spec("11.0.0") == "kicad-python"

    def test_none_unpinned(self):
        assert er.kipy_spec(None) == "kicad-python"


class TestResolvePipSpecs:
    BASE = ["fastmcp", "mcp", "pandas", "pyyaml", "defusedxml", "jsonschema",
            "kicad-python"]

    def test_pins_kicad_python_for_known_kicad(self):
        out = er.resolve_pip_specs(self.BASE, "10.0.1")
        assert out[-1] == "kicad-python==0.7.1"
        assert out[:-1] == self.BASE[:-1]   # everything else untouched, in order

    def test_unknown_kicad_leaves_unpinned(self):
        assert er.resolve_pip_specs(self.BASE, "12.3.4") == self.BASE

    def test_only_kicad_python_is_touched(self):
        out = er.resolve_pip_specs(["pandas", "kicad-python"], "10.0.0")
        assert out == ["pandas", "kicad-python==0.7.1"]


class TestSpecName:
    def test_plain(self):
        assert er._spec_name("fastmcp") == "fastmcp"

    def test_pinned(self):
        assert er._spec_name("kicad-python==0.7.1") == "kicad-python"

    def test_ranges(self):
        assert er._spec_name("numpy>=1.26") == "numpy"
        assert er._spec_name("pandas~=2.0") == "pandas"


class TestPythonTag:
    def test_shape(self):
        tag = er.python_tag()
        assert isinstance(tag, str) and tag
        assert "-" in tag   # impl+version, then platform/machine


class TestFingerprint:
    def test_stable_for_same_inputs(self):
        a = er.environment_fingerprint("10.0.1", "cp311-linux-x86_64", "1.2.3")
        b = er.environment_fingerprint("10.0.1", "cp311-linux-x86_64", "1.2.3")
        assert a == b and len(a) == 16

    def test_changes_on_kicad_change(self):
        a = er.environment_fingerprint("10.0.1", "t", "c")
        b = er.environment_fingerprint("11.0.0", "t", "c")
        assert a != b

    def test_changes_on_claude_change(self):
        a = er.environment_fingerprint("10.0", "t", "1.0.0")
        b = er.environment_fingerprint("10.0", "t", "2.0.0")
        assert a != b

    def test_patch_differences_collapse(self):
        a = er.environment_fingerprint("10.0.1", "t", "c")
        b = er.environment_fingerprint("10.0.9", "t", "c")
        assert a == b

    def test_none_py_tag_does_not_crash(self):
        assert len(er.environment_fingerprint("10.0", None, "c")) == 16


class TestNeedsReinstall:
    def test_no_record(self):
        assert er.needs_reinstall(None, "abc") is True
        assert er.needs_reinstall("", "abc") is True

    def test_differ(self):
        assert er.needs_reinstall("abc", "def") is True

    def test_same(self):
        assert er.needs_reinstall("abc", "abc") is False


class TestDetect:
    def test_kicad_version_never_raises(self):
        # outside KiCad pcbnew is absent -> None; must never raise
        result = er.detect_kicad_version()
        assert result is None or isinstance(result, str)

    def test_claude_version_parsed(self):
        def _run(cmd, **kw):
            return SimpleNamespace(stdout="1.2.3 (Claude Code)\n", stderr="",
                                   returncode=0)
        assert er.detect_claude_version(_run=_run) == "1.2.3 (Claude Code)"

    def test_claude_version_empty_is_none(self):
        def _run(cmd, **kw):
            return SimpleNamespace(stdout="  \n", stderr="", returncode=0)
        assert er.detect_claude_version(_run=_run) is None

    def test_claude_version_failure_is_none(self):
        def _boom(cmd, **kw):
            raise FileNotFoundError("no claude on PATH")
        assert er.detect_claude_version(_run=_boom) is None
