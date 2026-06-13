# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for enabling KiCad's IPC API (api.enable_server in kicad_common.json):
read, idempotent enable, key preservation, and the not-found path.
"""

from __future__ import annotations

import json

from plugin import ipc_setup


class TestReadIpcEnabled:
    def test_true(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text('{"api": {"enable_server": true}}')
        assert ipc_setup.read_ipc_enabled(str(f)) is True

    def test_false(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text('{"api": {"enable_server": false}}')
        assert ipc_setup.read_ipc_enabled(str(f)) is False

    def test_key_absent_is_none(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text('{"api": {}}')
        assert ipc_setup.read_ipc_enabled(str(f)) is None

    def test_missing_file_is_none(self, tmp_path):
        assert ipc_setup.read_ipc_enabled(str(tmp_path / "nope.json")) is None
        assert ipc_setup.read_ipc_enabled(None) is None

    def test_bad_json_is_none(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text("not json")
        assert ipc_setup.read_ipc_enabled(str(f)) is None


class TestEnsureIpcEnabled:
    def test_flips_false_to_true_and_reports_changed(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text(json.dumps({"api": {"enable_server": False},
                                 "appearance": {"foo": 1}}))
        res = ipc_setup.ensure_ipc_enabled(str(f))
        assert res["found"] and res["was_enabled"] is False and res["changed"]
        data = json.loads(f.read_text())
        assert data["api"]["enable_server"] is True
        # unrelated keys preserved
        assert data["appearance"] == {"foo": 1}

    def test_already_true_is_noop(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text(json.dumps({"api": {"enable_server": True}}))
        res = ipc_setup.ensure_ipc_enabled(str(f))
        assert res["was_enabled"] is True and res["changed"] is False

    def test_creates_api_section_if_absent(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text(json.dumps({"system": {"x": 1}}))
        res = ipc_setup.ensure_ipc_enabled(str(f))
        assert res["changed"] is True
        data = json.loads(f.read_text())
        assert data["api"]["enable_server"] is True
        assert data["system"] == {"x": 1}

    def test_preserves_interpreter_path(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text(json.dumps(
            {"api": {"enable_server": False, "interpreter_path": "/k/pyw"}}))
        ipc_setup.ensure_ipc_enabled(str(f))
        data = json.loads(f.read_text())
        assert data["api"]["interpreter_path"] == "/k/pyw"

    def test_missing_file_reports_not_found(self, tmp_path):
        res = ipc_setup.ensure_ipc_enabled(str(tmp_path / "nope.json"))
        assert res["found"] is False and res["changed"] is False and res["error"]

    def test_none_path_reports_not_found(self):
        res = ipc_setup.ensure_ipc_enabled(None)
        assert res["found"] is False and res["changed"] is False


class TestFindKicadCommon:
    def test_explicit_dir_hit(self, tmp_path):
        d = tmp_path / "10.0"; d.mkdir()
        f = d / "kicad_common.json"; f.write_text("{}")
        assert ipc_setup.find_kicad_common(str(d)) == str(f)

    def test_explicit_dir_miss_falls_through_to_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ipc_setup, "_config_roots",
                            lambda: [str(tmp_path / "empty")])
        assert ipc_setup.find_kicad_common(str(tmp_path / "no-such-dir")) is None

    def test_glob_picks_highest_version(self, tmp_path, monkeypatch):
        root = tmp_path / "kicad"
        for ver in ("9.0", "10.0"):
            d = root / ver; d.mkdir(parents=True)
            (d / "kicad_common.json").write_text("{}")
        monkeypatch.setattr(ipc_setup, "_config_roots", lambda: [str(root)])
        got = ipc_setup.find_kicad_common()
        assert got.endswith("kicad_common.json") and "10.0" in got
