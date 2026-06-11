# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the provisional GitHub self-updater: version parsing/compare, the
update check (injected network), and applying a repo zip over the install dir.
"""

from __future__ import annotations

import io
import zipfile

from plugin import updater


class TestVersionLogic:
    def test_parse(self):
        assert updater.parse_version('__version__ = "1.2.3"') == "1.2.3"
        assert updater.parse_version("__version__='0.1.0'") == "0.1.0"
        assert updater.parse_version("nope") is None

    def test_version_tuple(self):
        assert updater.version_tuple("0.1.0") == (0, 1, 0)
        assert updater.version_tuple("10.0") == (10, 0)

    def test_is_newer(self):
        assert updater.is_newer("0.2.0", "0.1.0") is True
        assert updater.is_newer("0.1.0", "0.1.0") is False
        assert updater.is_newer("0.1.0", "0.2.0") is False
        assert updater.is_newer("0.10.0", "0.9.0") is True  # numeric, not str


class TestCheckForUpdate:
    def test_available(self):
        res = updater.check_for_update(
            "0.1.0", _get=lambda u: b'__version__ = "0.2.0"')
        assert res["ok"] and res["available"]
        assert res["local"] == "0.1.0" and res["remote"] == "0.2.0"

    def test_up_to_date(self):
        res = updater.check_for_update(
            "0.2.0", _get=lambda u: b'__version__ = "0.2.0"')
        assert res["ok"] and res["available"] is False

    def test_network_error_is_soft(self):
        def boom(_u):
            raise OSError("no net")
        res = updater.check_for_update("0.1.0", _get=boom)
        assert res["ok"] is False and "no net" in res["error"]

    def test_unparsable_remote(self):
        res = updater.check_for_update("0.1.0", _get=lambda u: b"garbage")
        assert res["ok"] is False and res["error"]


def _make_repo_zip(files: dict) -> bytes:
    """Build a GitHub-style zip: members under ``<repo>-<branch>/...``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(f"kicad-mcp-main/{path}", content)
    return buf.getvalue()


class TestApplyUpdate:
    def test_extracts_only_plugin_subtree(self, tmp_path):
        zb = _make_repo_zip({
            "plugin/version.py": '__version__ = "0.2.0"',
            "plugin/sub/extra.py": "x = 1",
            "kicad_mcp/server.py": "should not land",  # outside plugin/
            "README.md": "root readme",                 # outside plugin/
        })
        out = updater.apply_update(str(tmp_path), zb)
        assert not out["error"]
        assert (tmp_path / "version.py").read_text() == '__version__ = "0.2.0"'
        assert (tmp_path / "sub" / "extra.py").read_text() == "x = 1"
        # things outside plugin/ are NOT written
        assert not (tmp_path / "server.py").exists()
        assert not (tmp_path / "README.md").exists() or \
            "root readme" not in (tmp_path / "README.md").read_text()
        assert set(out["updated"]) == {"version.py", "sub/extra.py"}

    def test_skips_pycache(self, tmp_path):
        zb = _make_repo_zip({
            "plugin/version.py": "v",
            "plugin/__pycache__/version.cpython-311.pyc": "binary",
        })
        out = updater.apply_update(str(tmp_path), zb)
        assert out["updated"] == ["version.py"]
        assert not (tmp_path / "__pycache__").exists()

    def test_bad_zip_returns_error(self, tmp_path):
        out = updater.apply_update(str(tmp_path), b"not a zip")
        assert out["error"] and out["updated"] == []

    def test_overwrites_existing(self, tmp_path):
        (tmp_path / "version.py").write_text('__version__ = "0.1.0"')
        zb = _make_repo_zip({"plugin/version.py": '__version__ = "0.2.0"'})
        updater.apply_update(str(tmp_path), zb)
        assert (tmp_path / "version.py").read_text() == '__version__ = "0.2.0"'


class TestUrls:
    def test_points_at_users_repo(self):
        assert "ChrisRudi/kicad-mcp" in updater.RAW_VERSION_URL
        assert "ChrisRudi/kicad-mcp" in updater.ZIPBALL_URL
        assert updater.RAW_VERSION_URL.endswith("plugin/version.py")
