# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for env_resolve: KiCad→kipy coupling, downgrade decision, fingerprint,
atomic _deps swap. All headless — no KiCad, no pip, no GUI."""

from __future__ import annotations

import os
from types import SimpleNamespace

from plugin import env_resolve as er


class TestParseVersion:
    def test_plain(self):
        assert er.parse_version("10.0.1") == (10, 0, 1)

    def test_two_part(self):
        assert er.parse_version("10.0") == (10, 0, 0)

    def test_decorated_build_string(self):
        # KiCad's GetBuildVersion() is decorated; we want the embedded version
        assert er.parse_version("(10.0.1-1-g...)") == (10, 0, 1)
        assert er.parse_version("10.0.1-unknown-rc2") == (10, 0, 1)

    def test_bare_pypi(self):
        assert er.parse_version("0.7.1") == (0, 7, 1)

    def test_none_and_garbage(self):
        assert er.parse_version(None) is None
        assert er.parse_version("") is None
        assert er.parse_version("no digits here") is None

    def test_version_le(self):
        assert er.version_le((0, 7, 1), (0, 7, 1))
        assert er.version_le((0, 7, 0), (0, 7, 1))
        assert not er.version_le((0, 8, 0), (0, 7, 1))
        assert er.version_le(None, (0, 1, 0))  # None sorts lowest


class TestMajorOf:
    def test_tuple(self):
        assert er.major_of((10, 0, 1)) == 10

    def test_int(self):
        assert er.major_of(9) == 9

    def test_none(self):
        assert er.major_of(None) is None


class TestKiCadDetection:
    def test_from_pcbnew_stub(self):
        stub = SimpleNamespace(GetBuildVersion=lambda: "10.0.1")
        assert er.detect_kicad_version(_pcbnew=stub) == (10, 0, 1)

    def test_pcbnew_raises_falls_back_to_path(self):
        def boom():
            raise RuntimeError("no gui")
        stub = SimpleNamespace(GetBuildVersion=boom)
        v = er.detect_kicad_version(
            _pcbnew=stub,
            kicad_py_path=r"C:\Program Files\KiCad\10.0\bin\python.exe")
        assert v == (10, 0, 0)

    def test_no_pcbnew_no_path(self):
        assert er.detect_kicad_version(_pcbnew=None) is None

    def test_path_posix_and_windows(self):
        assert er.parse_kicad_version_from_path(
            "/mnt/c/Program Files/KiCad/10.0/bin/python.exe") == (10, 0, 0)
        assert er.parse_kicad_version_from_path(
            r"C:\Program Files\KiCad\9.0\bin\python.exe") == (9, 0, 0)

    def test_path_without_version(self):
        assert er.parse_kicad_version_from_path("/usr/bin/python3") is None
        assert er.parse_kicad_version_from_path(None) is None


class TestCoupling:
    def test_kicad10_pins_071(self):
        assert er.coupled_kipy_version((10, 0, 1)) == "0.7.1"
        assert er.coupled_kipy_version(10) == "0.7.1"

    def test_unknown_major_is_none(self):
        assert er.coupled_kipy_version((99, 0, 0)) is None
        assert er.coupled_kipy_version(None) is None

    def test_kipy_spec_pinned_when_known(self):
        assert er.kipy_spec((10, 0, 0)) == "kicad-python==0.7.1"

    def test_kipy_spec_bare_when_unknown(self):
        # defensive: unknown KiCad keeps today's unpinned behaviour
        assert er.kipy_spec(None) == "kicad-python"


class TestResolvePipSpecs:
    BASE = ["fastmcp", "mcp", "pandas", "pyyaml", "defusedxml", "jsonschema",
            "kicad-python"]

    def test_pins_kipy_for_known_kicad(self):
        out = er.resolve_pip_specs((10, 0, 1), self.BASE)
        assert "kicad-python==0.7.1" in out
        assert "kicad-python" not in out  # the bare entry is gone
        # everything else passes through, order preserved
        assert out[:6] == self.BASE[:6]

    def test_leaves_bare_when_kicad_unknown(self):
        out = er.resolve_pip_specs(None, self.BASE)
        assert "kicad-python" in out and "==" not in "".join(out)

    def test_does_not_touch_non_kipy(self):
        out = er.resolve_pip_specs((10, 0, 0), self.BASE)
        for s in ["fastmcp", "mcp", "pandas", "pyyaml", "defusedxml",
                  "jsonschema"]:
            assert s in out

    def test_underscore_name_variant_is_recognized(self):
        out = er.resolve_pip_specs((10, 0, 0), ["fastmcp", "kicad_python"])
        assert "kicad-python==0.7.1" in out
        assert "kicad_python" not in out

    def test_already_pinned_entry_is_re_pinned(self):
        out = er.resolve_pip_specs((10, 0, 0), ["kicad-python==0.6.0"])
        assert out == ["kicad-python==0.7.1"]

    def test_adds_kipy_when_base_lacks_it(self):
        out = er.resolve_pip_specs((10, 0, 0), ["fastmcp"])
        assert out == ["fastmcp", "kicad-python==0.7.1"]

    def test_real_deps_pip_specs_integrate(self):
        from plugin import deps
        out = er.resolve_pip_specs((10, 0, 0), deps.PIP_SPECS)
        assert "kicad-python==0.7.1" in out
        assert deps.PIP_SPECS == ["fastmcp", "mcp", "pandas", "pyyaml",
                                  "defusedxml", "jsonschema", "kicad-python"]


def _mk_distinfo(deps_dir, version):
    os.makedirs(os.path.join(deps_dir, f"kicad_python-{version}.dist-info"))


def _site(tmp_path, *parts):
    """Make a nested site-packages dir (named so classify_kipy_location sees the
    intended class) and return it."""
    d = os.path.join(str(tmp_path), *parts)
    os.makedirs(d, exist_ok=True)
    return d


class TestClassifyLocation:
    def test_classes(self):
        assert er.classify_kipy_location(
            r"C:\Users\x\plugins\claude_kicad\_deps\kicad_python-0.7.1.dist-info"
        ) == "deps"
        assert er.classify_kipy_location(
            r"C:\Users\x\Documents\KiCad\10.0\3rdparty\Python311\site-packages"
            r"\kicad_python-0.7.1.dist-info") == "3rdparty"
        assert er.classify_kipy_location(
            r"C:\Program Files\KiCad\10.0\lib\site-packages"
            r"\kicad_python-0.7.1.dist-info") == "install"
        assert er.classify_kipy_location("/somewhere/else") == "other"


class TestBundledKipy:
    def test_reads_3rdparty(self, tmp_path):
        tp = _site(tmp_path, "Documents", "KiCad", "10.0", "3rdparty",
                   "Python311", "site-packages")
        _mk_distinfo(tp, "0.7.1")
        assert er.kicad_bundled_kipy_version([tp]) == "0.7.1"

    def test_install_wins_over_3rdparty(self, tmp_path):
        inst = _site(tmp_path, "Program Files", "KiCad", "10.0", "sp")
        _mk_distinfo(inst, "0.7.1")
        third = _site(tmp_path, "Documents", "KiCad", "10.0", "3rdparty", "sp")
        _mk_distinfo(third, "0.8.0")  # newer, but mutable → install still wins
        assert er.kicad_bundled_kipy_version([inst, third]) == "0.7.1"

    def test_ignores_plugin_deps(self, tmp_path):
        deps_dir = _site(tmp_path, "plugins", "claude_kicad", "_deps")
        _mk_distinfo(deps_dir, "0.9.0")
        assert er.kicad_bundled_kipy_version([deps_dir]) is None

    def test_none_when_no_kicad_copy(self, tmp_path):
        assert er.kicad_bundled_kipy_version([str(tmp_path)]) is None
        assert er.kicad_bundled_kipy_version(None) is None
        assert er.kicad_bundled_kipy_version([]) is None


class TestPlanKipyPin:
    def test_table_source_no_paths(self):
        p = er.plan_kipy_pin((10, 0, 1))
        assert p["source"] == "table" and p["spec"] == "kicad-python==0.7.1"
        assert p["warning"] == ""

    def test_table_wins_but_warns_on_pollution(self, tmp_path):
        third = _site(tmp_path, "Documents", "KiCad", "10.0", "3rdparty", "sp")
        _mk_distinfo(third, "0.8.0")  # mutable site polluted with a newer kipy
        p = er.plan_kipy_pin((10, 0, 0), [third])
        assert p["source"] == "table"          # table still authoritative
        assert p["spec"] == "kicad-python==0.7.1"
        assert p["warning"] and "0.8.0" in p["warning"]

    def test_table_no_warning_when_3rdparty_matches(self, tmp_path):
        third = _site(tmp_path, "Documents", "KiCad", "10.0", "3rdparty", "sp")
        _mk_distinfo(third, "0.7.1")
        p = er.plan_kipy_pin((10, 0, 0), [third])
        assert p["source"] == "table" and p["warning"] == ""

    def test_bundled_fallback_for_unknown_major(self, tmp_path):
        third = _site(tmp_path, "Documents", "KiCad", "11.0", "3rdparty", "sp")
        _mk_distinfo(third, "0.9.0")
        p = er.plan_kipy_pin((11, 0, 0), [third])
        assert p["source"] == "bundled"
        assert p["spec"] == "kicad-python==0.9.0"
        assert p["version"] == "0.9.0" and p["warning"]

    def test_unpinned_last_resort(self, tmp_path):
        p = er.plan_kipy_pin((11, 0, 0), [str(tmp_path)])
        assert p["source"] == "unpinned" and p["spec"] == "kicad-python"
        assert p["warning"]


class TestResolveWithFallback:
    def test_unknown_major_pins_to_bundled(self, tmp_path):
        third = _site(tmp_path, "Documents", "KiCad", "11.0", "3rdparty", "sp")
        _mk_distinfo(third, "0.9.0")
        out = er.resolve_pip_specs((11, 0, 0), ["fastmcp", "kicad-python"],
                                   search_paths=[third])
        assert "kicad-python==0.9.0" in out and "fastmcp" in out

    def test_unknown_major_no_paths_stays_unpinned(self):
        out = er.resolve_pip_specs((11, 0, 0), ["kicad-python"])
        assert out == ["kicad-python"]


class TestPinnedKipyIn:
    def test_extracts_version(self):
        assert er.pinned_kipy_in(["a", "kicad-python==0.7.1"]) == "0.7.1"

    def test_none_when_unpinned_or_absent(self):
        assert er.pinned_kipy_in(["kicad-python"]) is None
        assert er.pinned_kipy_in(["fastmcp"]) is None
        assert er.pinned_kipy_in(None) is None

    def test_fingerprint_records_bundled_pin(self):
        # unknown major, pin came from the bundled fallback -> fingerprint must
        # still record the version that actually landed (not "" from the table)
        fp = er.build_fingerprint((11, 0, 0), ["kicad-python==0.9.0"])
        assert fp["kipy"] == "0.9.0" and fp["kicad_major"] == 11


class TestInstalledKipy:
    def test_reads_distinfo(self, tmp_path):
        _mk_distinfo(str(tmp_path), "0.8.0")
        assert er.installed_kipy_version(str(tmp_path)) == "0.8.0"

    def test_none_when_absent(self, tmp_path):
        assert er.installed_kipy_version(str(tmp_path)) is None
        assert er.installed_kipy_version(None) is None

    def test_picks_highest_when_multiple(self, tmp_path):
        _mk_distinfo(str(tmp_path), "0.7.1")
        _mk_distinfo(str(tmp_path), "0.8.0")
        assert er.installed_kipy_version(str(tmp_path)) == "0.8.0"

    def test_dash_variant_distinfo(self, tmp_path):
        os.makedirs(os.path.join(str(tmp_path), "kicad-python-0.7.1.dist-info"))
        assert er.installed_kipy_version(str(tmp_path)) == "0.7.1"


class TestDowngradeDecision:
    def test_fresh_install(self, tmp_path):
        d = er.downgrade_decision((10, 0, 0), str(tmp_path))
        assert d["action"] == "install" and d["mismatch"] is True
        assert d["target"] == "0.7.1"

    def test_downgrade_when_too_new(self, tmp_path):
        _mk_distinfo(str(tmp_path), "0.8.0")  # newer than coupled 0.7.1
        d = er.downgrade_decision((10, 0, 0), str(tmp_path))
        assert d["action"] == "downgrade" and d["mismatch"] is True
        assert d["installed"] == "0.8.0" and d["target"] == "0.7.1"

    def test_upgrade_when_too_old(self, tmp_path):
        _mk_distinfo(str(tmp_path), "0.6.0")
        d = er.downgrade_decision((10, 0, 0), str(tmp_path))
        assert d["action"] == "upgrade" and d["mismatch"] is True

    def test_none_when_already_coupled(self, tmp_path):
        _mk_distinfo(str(tmp_path), "0.7.1")
        d = er.downgrade_decision((10, 0, 0), str(tmp_path))
        assert d["action"] == "none" and d["mismatch"] is False

    def test_unknown_kicad_forces_nothing(self, tmp_path):
        _mk_distinfo(str(tmp_path), "0.8.0")
        d = er.downgrade_decision((99, 0, 0), str(tmp_path))
        assert d["action"] == "none" and d["mismatch"] is False
        assert d["target"] is None


class TestFingerprint:
    def test_build_has_coupling_fields(self):
        fp = er.build_fingerprint((10, 0, 1), ["a", "kicad-python==0.7.1"],
                                  plugin_version="0.4.3")
        assert fp["kicad_version"] == "10.0.1"
        assert fp["kicad_major"] == 10
        assert fp["kipy"] == "0.7.1"
        assert fp["specs"] == ["a", "kicad-python==0.7.1"]  # sorted
        assert fp["plugin_version"] == "0.4.3"

    def test_write_then_read_roundtrip(self, tmp_path):
        fp = er.build_fingerprint((10, 0, 1), ["x"])
        assert er.write_fingerprint(str(tmp_path), fp) is True
        assert os.path.isfile(er.fingerprint_path(str(tmp_path)))
        assert er.read_fingerprint(str(tmp_path)) == fp

    def test_read_missing_is_none(self, tmp_path):
        assert er.read_fingerprint(str(tmp_path)) is None
        assert er.read_fingerprint(None) is None

    def test_read_corrupt_is_none(self, tmp_path):
        with open(er.fingerprint_path(str(tmp_path)), "w",
                  encoding="utf-8") as fh:
            fh.write("{not json")
        assert er.read_fingerprint(str(tmp_path)) is None

    def test_stale_when_missing(self, tmp_path):
        cur = er.build_fingerprint((10, 0, 0), ["x"])
        assert er.fingerprint_stale(str(tmp_path), cur) is True

    def test_fresh_when_major_and_kipy_match(self, tmp_path):
        cur = er.build_fingerprint((10, 0, 1), ["x"])
        er.write_fingerprint(str(tmp_path), cur)
        # patch differs, but major+kipy identical -> not stale
        later = er.build_fingerprint((10, 0, 9), ["y"])
        assert er.fingerprint_stale(str(tmp_path), later) is False

    def test_stale_when_kipy_changes(self, tmp_path):
        er.write_fingerprint(str(tmp_path),
                             {"kicad_major": 10, "kipy": "0.6.0"})
        cur = er.build_fingerprint((10, 0, 0), ["x"])  # kipy 0.7.1
        assert er.fingerprint_stale(str(tmp_path), cur) is True


class TestAtomicSwap:
    def test_swaps_into_place(self, tmp_path):
        new = tmp_path / "_deps.new"
        new.mkdir()
        (new / "kipy.py").write_text("new")
        dest = tmp_path / "_deps"
        dest.mkdir()
        (dest / "old.py").write_text("old")

        res = er.atomic_swap_dir(str(new), str(dest))
        assert res["ok"] is True
        assert (dest / "kipy.py").read_text() == "new"
        assert not (dest / "old.py").exists()
        assert not new.exists()  # consumed
        assert not (tmp_path / "_deps.old").exists()  # backup cleaned

    def test_swap_into_empty_dest(self, tmp_path):
        new = tmp_path / "_deps.new"
        new.mkdir()
        (new / "kipy.py").write_text("new")
        dest = tmp_path / "_deps"  # does not exist yet

        res = er.atomic_swap_dir(str(new), str(dest))
        assert res["ok"] is True and (dest / "kipy.py").read_text() == "new"

    def test_missing_source_is_soft_error(self, tmp_path):
        dest = tmp_path / "_deps"
        dest.mkdir()
        (dest / "live.py").write_text("live")
        res = er.atomic_swap_dir(str(tmp_path / "nope"), str(dest))
        assert res["ok"] is False and res["error"]
        assert (dest / "live.py").read_text() == "live"  # untouched

    def test_failed_promote_rolls_back_old_deps(self, tmp_path, monkeypatch):
        # Simulate the second os.replace (promote) failing AFTER the old tree
        # was moved aside -> the no-brick guarantee must restore the old _deps.
        new = tmp_path / "_deps.new"
        new.mkdir()
        (new / "new.py").write_text("new")
        dest = tmp_path / "_deps"
        dest.mkdir()
        (dest / "live.py").write_text("live")

        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(a, b):
            calls["n"] += 1
            if calls["n"] == 2:  # the promote step
                raise OSError("disk full")
            return real_replace(a, b)

        fake_os = SimpleNamespace(
            path=os.path, replace=flaky_replace)
        res = er.atomic_swap_dir(str(new), str(dest), _os=fake_os)
        assert res["ok"] is False and "Swap fehlgeschlagen" in res["error"]
        # old _deps rolled back into place -> NOT bricked
        assert dest.is_dir() and (dest / "live.py").read_text() == "live"

    def test_clears_stale_backup_before_swap(self, tmp_path):
        new = tmp_path / "_deps.new"
        new.mkdir()
        (new / "n.py").write_text("n")
        dest = tmp_path / "_deps"
        dest.mkdir()
        stale = tmp_path / "_deps.old"  # leftover from an interrupted run
        stale.mkdir()
        (stale / "junk.py").write_text("junk")

        res = er.atomic_swap_dir(str(new), str(dest))
        assert res["ok"] is True
        assert not stale.exists()
