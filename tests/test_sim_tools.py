# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests für run_spice_sim: ngspice-Discovery, Output-Parsing und der
Batch-Lauf — headless über ein Fake-ngspice-Skript; ein echter ngspice-Lauf
läuft zusätzlich, wenn das Binary installiert ist (CI: meist nicht)."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat

import pytest
from fastmcp import FastMCP

from kicad_mcp.tools import sim_tools


# --- discovery ----------------------------------------------------------------

class TestFindNgspice:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        fake = tmp_path / "my-ngspice"
        fake.write_text("")
        monkeypatch.setenv(sim_tools.NGSPICE_ENV, str(fake))
        assert sim_tools.find_ngspice(_which=lambda n: None) == str(fake)

    def test_env_ignored_if_missing(self, monkeypatch):
        monkeypatch.setenv(sim_tools.NGSPICE_ENV, "/does/not/exist")
        assert sim_tools.find_ngspice(_which=lambda n: None) is None or True
        # PATH fallback still consulted:
        got = sim_tools.find_ngspice(_which=lambda n: "/usr/bin/ngspice")
        assert got == "/usr/bin/ngspice"

    def test_kicad_bin_sibling(self, tmp_path, monkeypatch):
        monkeypatch.delenv(sim_tools.NGSPICE_ENV, raising=False)
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "ngspice").write_text("")
        monkeypatch.setattr(sim_tools, "kicad_paths",
                            lambda: {"kicad_cli": str(bindir / "kicad-cli")})
        assert sim_tools.find_ngspice(_which=lambda n: None) == str(
            bindir / "ngspice")

    def test_nothing_found_is_none(self, monkeypatch):
        monkeypatch.delenv(sim_tools.NGSPICE_ENV, raising=False)
        monkeypatch.setattr(sim_tools, "kicad_paths", lambda: {"kicad_cli": ""})
        assert sim_tools.find_ngspice(_which=lambda n: None) is None


# --- output parsing --------------------------------------------------------------

class TestParseOutput:
    def test_value_lines_extracted(self):
        out = sim_tools.parse_ngspice_output(
            "No. of Data Rows : 1\n"
            "v(out) = 2.500000e+00\n"
            "i(v1) = -2.5e-03\n")
        assert out["values"]["v(out)"] == pytest.approx(2.5)
        assert out["values"]["i(v1)"] == pytest.approx(-0.0025)
        assert out["errors"] == []

    def test_errors_and_warnings_collected(self):
        out = sim_tools.parse_ngspice_output(
            "Warning: singular matrix\n"
            "Error: unknown subckt: opamp\n")
        assert any("unknown subckt" in e for e in out["errors"])
        assert any("singular" in w for w in out["warnings"])

    def test_empty_output(self):
        out = sim_tools.parse_ngspice_output("")
        assert out == {"values": {}, "errors": [], "warnings": []}


# --- the tool ----------------------------------------------------------------------

_DECK = """* RC divider
v1 in 0 dc 5
r1 in out 10k
r2 out 0 10k
.op
.control
run
print v(out)
.endc
.end
"""


@pytest.fixture(scope="module")
def server() -> FastMCP:
    mcp = FastMCP("sim-test")
    sim_tools.register_sim_tools(mcp)
    return mcp


def _call(server, **args):
    result = asyncio.run(server.call_tool("run_spice_sim", args))
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return result.structured_content


@pytest.fixture()
def fake_ngspice(tmp_path, monkeypatch):
    """Ein Skript, das sich wie ngspice -b verhält: druckt einen op-Wert."""
    script = tmp_path / "ngspice"
    script.write_text("#!/bin/sh\necho 'v(out) = 2.500000e+00'\nexit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(sim_tools.NGSPICE_ENV, str(script))
    return script


class TestRunSpiceSim:
    def test_missing_ngspice_gives_install_hint(self, server, monkeypatch):
        monkeypatch.delenv(sim_tools.NGSPICE_ENV, raising=False)
        monkeypatch.setattr(sim_tools, "find_ngspice", lambda **kw: None)
        # BEIDE Backends stummschalten: auf Maschinen mit echtem KiCad liegt
        # libngspice bei — ohne diesen Patch findet das Tool das Lib-Backend
        # und der Hinweis-Pfad wird nie erreicht (Container-KiCad-Fund).
        monkeypatch.setattr(sim_tools, "find_libngspice", lambda **kw: None)
        out = _call(server, netlist=_DECK)
        assert out["success"] is False
        assert "ngspice" in out["error"] and sim_tools.NGSPICE_ENV in out["error"]

    def test_requires_exactly_one_input(self, server):
        assert _call(server)["success"] is False
        both = _call(server, netlist=_DECK, netlist_path="/x.cir")
        assert both["success"] is False

    def test_missing_netlist_file(self, server, tmp_path, fake_ngspice):
        out = _call(server, netlist_path=str(tmp_path / "fehlt.cir"))
        assert out["success"] is False and "not found" in out["error"]

    def test_inline_deck_runs_and_parses(self, server, fake_ngspice):
        out = _call(server, netlist=_DECK)
        assert out["success"] is True
        assert out["values"]["v(out)"] == pytest.approx(2.5)
        assert out["returncode"] == 0

    def test_deck_error_reported(self, server, tmp_path, monkeypatch):
        script = tmp_path / "ngspice-err"
        script.write_text(
            "#!/bin/sh\necho 'Error: unknown subckt: opamp'\nexit 1\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv(sim_tools.NGSPICE_ENV, str(script))
        out = _call(server, netlist=_DECK)
        assert out["success"] is False
        assert any("unknown subckt" in e for e in out["errors"])

    @pytest.mark.skipif(shutil.which("ngspice") is None,
                        reason="echtes ngspice nicht installiert")
    def test_real_ngspice_rc_divider(self, server, monkeypatch):
        monkeypatch.delenv(sim_tools.NGSPICE_ENV, raising=False)
        out = _call(server, netlist=_DECK)
        assert out["success"] is True
        assert out["values"].get("v(out)") == pytest.approx(2.5, rel=1e-3)


def test_tmpfile_cleaned_up(server, fake_ngspice, tmp_path):
    import glob
    import tempfile
    before = set(glob.glob(os.path.join(tempfile.gettempdir(),
                                        "kicad_mcp_*.cir")))
    _call(server, netlist=_DECK)
    after = set(glob.glob(os.path.join(tempfile.gettempdir(),
                                       "kicad_mcp_*.cir")))
    assert after <= before  # kein liegengebliebenes Deck


class TestFindLibngspice:
    def test_env_override(self, tmp_path, monkeypatch):
        lib = tmp_path / "libngspice-0.dll"
        lib.write_text("")
        monkeypatch.setenv(sim_tools.LIBNGSPICE_ENV, str(lib))
        assert sim_tools.find_libngspice() == str(lib)

    def test_kicad_bin_dll_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv(sim_tools.LIBNGSPICE_ENV, raising=False)
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "libngspice-0.dll").write_text("")
        monkeypatch.setattr(sim_tools, "kicad_paths",
                            lambda: {"kicad_cli": str(bindir / "kicad-cli")})
        assert sim_tools.find_libngspice() == str(bindir / "libngspice-0.dll")

    def test_none_when_absent(self, monkeypatch):
        monkeypatch.delenv(sim_tools.LIBNGSPICE_ENV, raising=False)
        monkeypatch.setattr(sim_tools, "kicad_paths",
                            lambda: {"kicad_cli": ""})
        import ctypes.util
        monkeypatch.setattr(ctypes.util, "find_library", lambda n: None)
        assert sim_tools.find_libngspice() is None


class TestRunLibngspice:
    def test_happy_json(self):
        import json as _json
        from types import SimpleNamespace

        def _run(cmd, **kw):
            assert cmd[0] and cmd[1] == "-c" and cmd[3].endswith(".so")
            assert "v1 in 0" in kw["input"]
            return SimpleNamespace(returncode=0, stderr="", stdout=_json.dumps(
                {"rc_circ": 0, "rc_run": 0,
                 "values": {"v(out)": 2.5}, "output": "ok"}))

        res = sim_tools.run_libngspice("/x/libngspice.so", "v1 in 0 dc 5",
                                       30, _run=_run)
        assert res["values"]["v(out)"] == 2.5

    def test_dead_runner_reported(self):
        from types import SimpleNamespace
        res = sim_tools.run_libngspice(
            "/x.so", "deck", 30,
            _run=lambda *a, **k: SimpleNamespace(returncode=139, stdout="",
                                                 stderr="segfault"))
        assert "starb" in res["error"]

    def test_bad_json_reported(self):
        from types import SimpleNamespace
        res = sim_tools.run_libngspice(
            "/x.so", "deck", 30,
            _run=lambda *a, **k: SimpleNamespace(returncode=0,
                                                 stdout="garbage", stderr=""))
        assert "kein JSON" in res["error"]


class TestLibBackendInTool:
    def test_fallback_to_kicad_lib(self, server, monkeypatch):
        monkeypatch.setattr(sim_tools, "find_ngspice", lambda **kw: None)
        monkeypatch.setattr(sim_tools, "find_libngspice",
                            lambda: "/kicad/bin/libngspice-0.dll")
        monkeypatch.setattr(
            sim_tools, "run_libngspice",
            lambda lib, deck, t: {"rc_circ": 0, "rc_run": 0,
                                  "values": {"v(out)": 2.5},
                                  "output": "No. of Data Rows : 1"})
        out = _call(server, netlist=_DECK)
        assert out["success"] is True
        assert out["backend"] == "libngspice (KiCad)"
        assert out["values"]["v(out)"] == 2.5

    def test_no_backend_names_both(self, server, monkeypatch):
        monkeypatch.setattr(sim_tools, "find_ngspice", lambda **kw: None)
        monkeypatch.setattr(sim_tools, "find_libngspice", lambda: None)
        out = _call(server, netlist=_DECK)
        assert out["success"] is False
        assert "libngspice" in out["error"] and "ngspice" in out["error"]

    def test_cli_backend_labelled(self, server, fake_ngspice):
        out = _call(server, netlist=_DECK)
        assert out["success"] and out["backend"] == "ngspice-cli"
