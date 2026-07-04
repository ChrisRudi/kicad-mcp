# SPDX-License-Identifier: GPL-3.0-or-later
"""„Schaltung als Vorlage" — der Nutzer zeichnet, der MCP merkt sich und baut.

Kern der Idee: KiCad 10 kann keinen Schaltplan schreiben (leere IPC-API), also
liest der MCP einen selbst gezeichneten ``.kicad_sch`` ein, legt ihn als
benannte Vorlage ab und generiert daraus auf Wunsch ein Board. Getestet: der
reine Speicher (pfadsicher, Round-Trip, Kompakt-Konversion) sowie der volle
Weg speichern→listen→bauen gegen echtes KiCad (self-skip ohne kicad-cli).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from kicad_mcp.utils import circuit_templates as store


@pytest.fixture(autouse=True)
def _tmp_store(monkeypatch, tmp_path):
    monkeypatch.setenv(store.TEMPLATE_DIR_ENV, str(tmp_path / "tpl"))


class TestStore:
    def test_safe_name_slug(self):
        assert store.safe_name("Mein LDO") == "mein_ldo"
        assert store.safe_name("../etc/passwd") == "etc_passwd"
        assert store.safe_name("") == "unbenannt"

    def test_save_load_roundtrip(self):
        spec = {"components": [{"ref": "R1", "value": "1k",
                                "footprint": "R_0603"}],
                "nets": [{"name": "N", "pins": ["R1.1"]}],
                "description": "test"}
        path = store.save("Mein Block", spec)
        assert path.endswith("mein_block.json")
        got = store.load("Mein Block")
        assert got["name"] == "Mein Block"
        assert got["components"][0]["ref"] == "R1"
        # Slug-Zugriff funktioniert auch
        assert store.load("mein_block")["description"] == "test"

    def test_load_unknown_is_none(self):
        assert store.load("gibtsnicht") is None

    def test_list_templates(self):
        store.save("A", {"components": [{"ref": "R1"}], "nets": [],
                         "description": "erste"})
        store.save("B", {"components": [{"ref": "C1"}, {"ref": "C2"}],
                         "nets": [{"name": "n"}]})
        tpls = {t["slug"]: t for t in store.list_templates()}
        assert tpls["a"]["components"] == 1 and tpls["a"]["description"] == "erste"
        assert tpls["b"]["components"] == 2 and tpls["b"]["nets"] == 1

    def test_to_compact_converts_pin_separator(self):
        spec = {"components": [{"ref": "R1", "value": "1k"}],
                "nets": [{"name": "VCC", "type": "power",
                          "pins": ["R1.1", "U1.3"]}]}
        parts, nets = store.to_compact(spec)
        assert parts == [{"ref": "R1", "value": "1k"}]
        assert nets == [{"name": "VCC", "connections": ["R1:1", "U1:3"],
                         "type": "power"}]


def _has_cli() -> bool:
    from kicad_mcp.utils.path_env import kicad_cli
    return bool(kicad_cli())


@pytest.mark.skipif(not _has_cli(), reason="kicad-cli nötig (Netzlisten-Export)")
class TestTools:
    """Voller Weg gegen echtes KiCad: ein Board generieren, es als Schaltplan
    'zeichnen lassen', speichern, wieder bauen."""

    def _server(self):
        from kicad_mcp.server import create_server
        return create_server()

    def _drawn_schematic(self, srv, tmp_path):
        """Ein echtes .kicad_sch erzeugen (steht für den 'gezeichneten')."""
        from kicad_mcp import selftest
        spec = selftest.load_spec()
        out = asyncio.run(srv.call_tool("generate_project", {
            "output_dir": str(tmp_path / "drawn"),
            "parts": json.dumps(spec["parts"]),
            "nets": json.dumps(spec["nets"]),
            "board": json.dumps(spec.get("board") or {}),
            "project_name": "drawn"})).structured_content
        return out["files"]["schematic"]

    def _call(self, srv, name, args):
        return asyncio.run(srv.call_tool(name, args)).structured_content

    def test_save_list_build_roundtrip(self, tmp_path):
        srv = self._server()
        sch = self._drawn_schematic(srv, tmp_path)

        saved = self._call(srv, "save_circuit_template",
                           {"schematic_path": sch, "name": "LDO Test",
                            "description": "5V→3V3"})
        assert saved["success"] and saved["components"] >= 5

        lst = self._call(srv, "list_circuit_templates", {})
        assert any(t["slug"] == "ldo_test" for t in lst["templates"])

        built = self._call(srv, "build_circuit_template",
                           {"name": "LDO Test",
                            "output_dir": str(tmp_path / "built")})
        assert built["success"]
        assert os.path.isfile(built["files"]["schematic"])
        assert os.path.isfile(built["files"]["pcb"])
        # das gebaute Board ist wirklich bestückt
        pcb = open(built["files"]["pcb"], encoding="utf-8").read()
        assert pcb.count("(footprint ") >= 5

    def test_build_unknown_template_lists_available(self, tmp_path):
        srv = self._server()
        r = self._call(srv, "build_circuit_template",
                       {"name": "gibtsnicht", "output_dir": str(tmp_path)})
        assert r["success"] is False and "nicht gefunden" in r["error"]

    def test_save_missing_schematic(self, tmp_path):
        srv = self._server()
        r = self._call(srv, "save_circuit_template",
                       {"schematic_path": str(tmp_path / "weg.kicad_sch"),
                        "name": "x"})
        assert r["success"] is False and "nicht gefunden" in r["error"]
