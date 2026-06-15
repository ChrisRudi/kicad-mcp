# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard the KiCad PCM archive's metadata.json against the schema rules that
KiCad enforces — so the "archive contains no valid metadata.json" bug (an
SPDX license string the PCM enum rejects) can never silently come back.

Validates the metadata SHAPE without needing jsonschema or the network: the
exact rules taken from KiCad's pcm.v1 schema.
"""

from __future__ import annotations

import importlib.util
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "make_pcm_zip", os.path.join(_ROOT, "make_pcm_zip.py"))
make_pcm_zip = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(make_pcm_zip)


# from KiCad pcm.v1.schema.json
_REQUIRED_TOP = {"name", "description", "description_full", "identifier",
                 "type", "author", "license", "resources", "versions"}
_STATUS_ENUM = {"stable", "testing", "development", "deprecated"}
_LICENSE_ALLOWED = {"GPL", "GPL-1.0", "GPL-2.0", "GPL-3.0", "LGPL",
                    "LGPL-2.1", "LGPL-3.0", "MIT", "Apache-2.0"}
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z][-a-zA-Z0-9.]{0,98}[a-zA-Z0-9]$")
_KICAD_VER_RE = re.compile(r"^\d{1,2}(\.\d{1,2}(\.\d{1,2})?)?$")


class TestMetadata:
    def _meta(self):
        return make_pcm_zip._metadata("0.0.1", 12345)

    def test_all_required_top_level_fields(self):
        meta = self._meta()
        assert _REQUIRED_TOP <= set(meta), \
            f"missing: {_REQUIRED_TOP - set(meta)}"

    def test_license_is_in_pcm_enum_not_spdx(self):
        # the bug: "GPL-3.0-or-later" (SPDX) is rejected by PCM
        meta = self._meta()
        assert meta["license"] in _LICENSE_ALLOWED
        assert "or-later" not in meta["license"]

    def test_identifier_pattern(self):
        assert _IDENTIFIER_RE.match(self._meta()["identifier"])

    def test_type_is_plugin(self):
        assert self._meta()["type"] == "plugin"

    def test_version_entry_minimum_fields(self):
        v = self._meta()["versions"][0]
        assert {"version", "status", "kicad_version"} <= set(v)
        assert v["status"] in _STATUS_ENUM
        assert _KICAD_VER_RE.match(v["kicad_version"])

    def test_version_flows_from_argument(self):
        assert make_pcm_zip._metadata("9.9.9", 1)["versions"][0]["version"] \
            == "9.9.9"


class TestBuildLayout:
    def test_zip_has_pcm_layout(self, tmp_path, monkeypatch):
        import zipfile
        out = make_pcm_zip.build()
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
        roots = {n.split("/")[0] for n in names}
        assert "metadata.json" in names          # at the ROOT
        assert "plugins" in roots                # the plugin package
        assert any(n.startswith("plugins/") and n.endswith("claude_action.py")
                   for n in names)
