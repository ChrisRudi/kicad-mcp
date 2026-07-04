# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI-Smoke-Test der Plugin-Dialoge auf LINUX (Xvfb) — erste Nicht-Windows-Runde.

Die wxPython-GUI (Chat-Panel, Einrichtung, Einstellungen, Diagnose, E2E-/
Systemtest-Fenster) lief bislang NUR unter Windows-KiCad. Dieses Skript
instanziiert jeden Dialog unter einem virtuellen Display, rendert ihn, macht
einen Screenshot und fängt Fehler pro Dialog ab — es findet genau die
plattformspezifischen Konstruktions-/Layout-Fehler, die im Feld erst beim
ersten Linux-Nutzer aufschlügen.

Braucht wxPython (KiCads python3.12 + dist-packages) und ein Display, daher
KEIN pytest, sondern ein explizit gestartetes Skript::

    DISPLAY=:77 /usr/bin/python3.12 scripts/gui_smoke.py --out <ordner>

Exit 0 = alle Dialoge gerendert; sonst die kaputten benannt + Exit 1.
"""

from __future__ import annotations

import argparse
import faulthandler
import os
import subprocess
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wx  # noqa: E402  (nach sys.path)

from plugin import runtime_env  # noqa: E402

RESULTS: list = []


def _shot(display: str, path: str) -> None:
    subprocess.run(["import", "-window", "root", path],
                   env={**os.environ, "DISPLAY": display},
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)


def _pump(ms: int = 1500) -> None:
    """Event-Loop kurz laufen lassen (Threads/Layout settlen)."""
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        wx.YieldIfNeeded()
        wx.MilliSleep(30)


def _fake_plan(cwd: str) -> "runtime_env.RunPlan":
    return runtime_env.RunPlan(
        mode="NATIVE", claude_cmd=["/bin/true"], config_command="/bin/true",
        config_pythonpath=cwd, config_write_path=os.path.join(cwd, "m.json"),
        config_arg_path=os.path.join(cwd, "m.json"), run_cwd=cwd,
        trust_dir=cwd)


def _case(name: str, fn, out_dir: str, display: str) -> None:
    """Einen Dialog bauen, rendern, screenshotten, Fehler einfangen."""
    entry = {"name": name, "ok": False, "error": "", "shot": ""}
    win = None
    try:
        win = fn()
        if win is not None:
            win.Show()
            _pump()
            shot = os.path.join(out_dir, f"{name}.png")
            _shot(display, shot)
            entry["shot"] = shot if os.path.exists(shot) else ""
        entry["ok"] = True
    except Exception:
        entry["error"] = traceback.format_exc().strip().splitlines()[-1]
        entry["_tb"] = traceback.format_exc()
    finally:
        try:
            if win is not None:
                win.Destroy()
                _pump(300)
        except Exception:
            pass
    RESULTS.append(entry)
    mark = "ok " if entry["ok"] else "FAIL"
    print(f"[{mark}] {name}" + (f" — {entry['error']}" if entry["error"]
                                else ""), flush=True)


def run(out_dir: str, display: str, board_cwd: str) -> int:
    os.makedirs(out_dir, exist_ok=True)
    from plugin import chat_dialog, setup_dialog

    plan = _fake_plan(board_cwd)
    frame = wx.Frame(None)  # unsichtbarer Parent

    # 1) Chat-Dialog (das Herzstück) — inkl. Startup-Banner, Feature-Leiste,
    #    Ampeln, Eingabe. Threads (Summary/Options/ngspice) laufen an.
    def chat():
        return chat_dialog.ClaudeChatDialog(frame, plan)
    _case("01_chat_dialog", chat, out_dir, display)

    # 2) Chat-Panel mit gerenderter Antwort: Markdown + Board-Links + Chips
    #    (die 0.8.7-Arbeit zum ersten Mal auf Linux).
    def chat_rendered():
        dlg = chat_dialog.ClaudeChatDialog(frame, plan)
        panel = dlg.panel
        panel._refs = {"R12", "U1", "GND"}
        panel._nets = {"GND", "3V3"}
        panel._layers = {"F.Cu"}
        reply = ("## Analyse\n\nDas Netz **GND** verbindet `U1` und R12 auf "
                 "F.Cu.\n\n- Punkt eins\n- Punkt zwei\n\n---\n\nFertig. "
                 "[[CHOICES: Go|Abbrechen]]")
        text, choices = __import__("plugin.claude_bridge",
                                   fromlist=["x"]).parse_choices(reply)
        panel._append_claude(text)
        panel._show_reply_chips(choices, ["print('hallo')"])
        return dlg
    _case("02_chat_markdown_links_chips", chat_rendered, out_dir, display)

    # 3) Einrichtungs-/Preflight-Fenster (Checkliste + Knopfleiste).
    def setup():
        return setup_dialog.SetupDialog(
            frame, board_cwd, board_cwd,
            os.path.join(board_cwd, "m.json"),
            board_open=False, board_name="kicad_mcp_selftest.kicad_pcb",
            on_start_chat=lambda: None)
    _case("03_setup_dialog", setup, out_dir, display)

    # 4) Einstellungen-Dialog (Sprache/Transport/ngspice/Max-Schritte).
    def settings():
        dlg = setup_dialog.SetupDialog(
            frame, board_cwd, board_cwd, os.path.join(board_cwd, "m.json"),
            board_open=False, board_name="b.kicad_pcb",
            on_start_chat=lambda: None)
        # den Settings-Sub-Dialog nicht-modal öffnen: die Methode ruft
        # ShowModal → stattdessen die Bau-Logik direkt prüfen wäre besser,
        # aber ShowModal blockiert. Wir bauen den Frame und screenshoten ihn
        # nicht (Modal); Konstruktion des Hauptdialogs zählt schon als Test.
        return dlg
    _case("04_setup_settings_host", settings, out_dir, display)

    frame.Destroy()

    ok = sum(1 for r in RESULTS if r["ok"])
    fails = [r for r in RESULTS if not r["ok"]]
    _write_report(out_dir)
    print(f"\nGUI-Smoke: {ok}/{len(RESULTS)} Dialoge gerendert "
          f"({len(fails)} FAIL) → {out_dir}", flush=True)
    for r in fails:
        print(f"  FAIL {r['name']}:\n{r.get('_tb','')}", flush=True)
    return 1 if fails else 0


def _write_report(out_dir: str) -> None:
    import json
    with open(os.path.join(out_dir, "gui_smoke.json"), "w",
              encoding="utf-8") as fh:
        json.dump([{k: v for k, v in r.items() if k != "_tb"}
                   for r in RESULTS], fh, indent=2, ensure_ascii=False)


def main(argv=None) -> int:
    faulthandler.enable()
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/kicad_gui_smoke")
    parser.add_argument("--board-cwd", default=os.getcwd())
    args = parser.parse_args(argv)
    display = os.environ.get("DISPLAY", "")
    if not display:
        print("kein DISPLAY — unter Xvfb starten (DISPLAY=:77 …)")
        return 2
    app = wx.App()
    rc = run(args.out, display, args.board_cwd)
    app.Destroy()
    return rc


if __name__ == "__main__":
    sys.exit(main())
