# SPDX-License-Identifier: GPL-3.0-or-later
"""Live-IPC gegen einen ECHTEN laufenden KiCad-Editor (die „Mitarbeiter"-Schicht).

Was der Selftest nicht kann: prüfen, dass die ``ipc_*``-Tools und der
Cross-Probe (Editor-Selektion ↔ Produkt) gegen ein wirklich laufendes
Eeschema/pcbnew funktionieren. Dieses Modul startet über
:mod:`tests.live_ipc_harness` einen echten pcbnew-Prozess unter Xvfb und
fährt den realen Produktpfad (``server.call_tool("ipc_*")``) dagegen.

Opt-in + selbst-skippend: braucht Linux-Container mit Xvfb/pcbnew/xdotool,
kipy UND ``KICAD_MCP_LIVE_IPC=1``. Auf jeder anderen Umgebung (Windows-Dev,
Standard-CI-Job) skippt die ganze Datei — der Live-Editor ist zu schwer für
jeden Lauf; der dedizierte CI-Job ``tests-kicad`` setzt das Flag.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from tests.live_ipc_harness import LiveEditor, tools_present

pytestmark = pytest.mark.skipif(
    os.environ.get("KICAD_MCP_LIVE_IPC") != "1" or not tools_present(),
    reason="Live-IPC: KICAD_MCP_LIVE_IPC=1 + Xvfb/pcbnew/xdotool nötig")

_SPEC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "kicad_mcp", "resources", "data", "selftest_board.json")


def _make_board(tmp_dir: str) -> str:
    """Das Selftest-Demo-Board erzeugen (spec→pcb) und den .kicad_pcb-Pfad
    liefern — dieselbe Vorlage wie der Standalone-Selftest."""
    from kicad_mcp import selftest
    from kicad_mcp.server import create_server
    spec = selftest.load_spec(_SPEC)
    srv = create_server()
    out = asyncio.run(srv.call_tool("generate_project", {
        "output_dir": tmp_dir,
        "parts": json.dumps(spec["parts"]),
        "nets": json.dumps(spec["nets"]),
        "board": json.dumps(spec.get("board") or {}),
        "project_name": "live_ipc_demo",
    })).structured_content
    return out["files"]["pcb"]


def _call(srv, name, args):
    return asyncio.run(srv.call_tool(name, args)).structured_content


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    board = _make_board(str(tmp_path_factory.mktemp("live")))
    with LiveEditor(board) as (kicad, _display):
        yield kicad


class TestLiveIpc:
    def test_status_sees_running_editor(self, live):
        from kicad_mcp.server import create_server
        srv = create_server()
        st = _call(srv, "ipc_check_status", {})
        assert st["kicad_reachable"] is True
        assert st["board_open"] is True
        assert st["ready"] is True

    def test_open_documents_reports_the_board(self, live):
        from kicad_mcp.server import create_server
        srv = create_server()
        docs = _call(srv, "ipc_get_open_documents", {})
        assert docs["success"] is True
        assert any("live_ipc_demo" in p["filename"] for p in docs["pcbs"])

    def test_selection_cross_probe_round_trip(self, live):
        """DER Mitarbeiter-Kernpfad: im Editor markieren → Produkt liest es.

        Das ist „was ist das?"/„Auswahl einbeziehen" end-to-end gegen ein
        echtes laufendes KiCad — bisher nur auf der Nutzer-Maschine prüfbar.
        """
        from kicad_mcp.server import create_server
        srv = create_server()

        board = live.get_board()
        u1 = next(f for f in board.get_footprints()
                  if f.reference_field.text.value == "U1")
        board.add_to_selection([u1])

        sel = _call(srv, "ipc_get_selection", {})
        assert sel["success"] is True
        assert sel["count"] == 1
        item = sel["items"][0]
        assert item["reference"] == "U1"
        assert item["type"] == "footprint"

        board.remove_from_selection([u1])
        empty = _call(srv, "ipc_get_selection", {})
        assert empty["count"] == 0
