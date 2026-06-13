# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the netlist-parser CLI fallback path (Bug 2).

The full integration with running ``kicad-cli`` is exercised in the
existing netlist_tools tests; this file targets the s-expr parsing
itself with a hand-rolled fixture so it works without KiCad
installed.
"""

from __future__ import annotations

from kicad_mcp.utils.netlist_parser import _extract_netlist_via_cli, extract_netlist


SAMPLE_NET_SEXPR = '''(export (version "E")
  (design
    (source "test.kicad_sch")
    (date "2026-04-29")
    (tool "kicad-cli"))
  (components
    (comp (ref "R1")
      (value "10k")
      (footprint "Resistor_SMD:R_0402_1005Metric")
      (libsource (lib "Device") (part "R") (description "Resistor")))
    (comp (ref "C1")
      (value "100n")
      (footprint "Capacitor_SMD:C_0402_1005Metric")
      (libsource (lib "Device") (part "C") (description "Capacitor"))))
  (nets
    (net (code "1") (name "/VOUT")
      (node (ref "R1") (pin "2") (pinfunction "~") (pintype "passive"))
      (node (ref "C1") (pin "1") (pinfunction "~") (pintype "passive")))
    (net (code "2") (name "GND")
      (node (ref "C1") (pin "2") (pinfunction "~") (pintype "passive")))))
'''


class TestCliNetlistParse:
    def test_returns_none_without_cli(self, monkeypatch):
        # Force the cli-discovery to fail
        from kicad_mcp.utils import kicad_cli as cli_mod
        monkeypatch.setattr(cli_mod, "get_kicad_cli_path",
                            lambda required=False: (_ for _ in ()).throw(cli_mod.KiCadCLIError("nope")))
        result = _extract_netlist_via_cli("/nonexistent.kicad_sch")
        assert result is None

    def test_parses_components_and_nets(self, tmp_path, monkeypatch):
        # Bypass the kicad-cli call by having the function read our pre-written
        # netlist file via a fake subprocess.run.
        import kicad_mcp.utils.netlist_parser as np
        from kicad_mcp.utils import kicad_cli as cli_mod, wsl_path

        monkeypatch.setattr(cli_mod, "get_kicad_cli_path", lambda required=False: "kicad-cli")
        # to_windows_path identity for the test (avoid path mangling)
        monkeypatch.setattr(wsl_path, "to_windows_path", lambda p: p)

        class FakeResult:
            returncode = 0
            stderr = ""

        def fake_run(cmd, **kwargs):
            out_idx = cmd.index("--output")
            out_path = cmd[out_idx + 1]
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE_NET_SEXPR)
            return FakeResult()

        monkeypatch.setattr(np.subprocess, "run", fake_run)

        sch = tmp_path / "test.kicad_sch"
        sch.write_text("(kicad_sch)", encoding="utf-8")

        result = _extract_netlist_via_cli(str(sch))
        assert result is not None
        assert result["partial"] is False
        assert result["source"] == "kicad-cli"
        assert result["component_count"] == 2
        assert "R1" in result["components"]
        assert result["components"]["R1"]["value"] == "10k"
        assert result["components"]["R1"]["lib_id"] == "Device:R"

        # Nets — both VOUT and GND must appear with correct pin nodes
        assert "/VOUT" in result["nets"]
        vout = result["nets"]["/VOUT"]
        assert len(vout) == 2
        refs = {(p["ref"], p["pin"]) for p in vout}
        assert ("R1", "2") in refs
        assert ("C1", "1") in refs
        # pintype carried through
        assert all(p.get("pintype") == "passive" for p in vout)

    def test_extract_netlist_falls_back_to_label_parser(self, tmp_path, monkeypatch):
        # If CLI returns None (e.g. no kicad-cli), the legacy
        # SchematicParser fallback must still work and report partial=True.
        import kicad_mcp.utils.netlist_parser as np
        monkeypatch.setattr(np, "_extract_netlist_via_cli", lambda p: None)

        sch = tmp_path / "minimal.kicad_sch"
        sch.write_text("(kicad_sch (version 20231120))", encoding="utf-8")

        result = extract_netlist(str(sch))
        assert result.get("partial") is True
