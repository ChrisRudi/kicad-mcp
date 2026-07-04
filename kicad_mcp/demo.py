# SPDX-License-Identifier: GPL-3.0-or-later
"""Demo-Ablauf: aus einer Idee automatisch Schaltplan → Berechnung → Platine.

Der „Demo"-Knopf im Panel führt genau das vor, was auch der Systemtest baut
(``resources/data/selftest_board.json``) — aber sichtbar, Schritt für Schritt,
als Erstkontakt und Live-Beweis: was hier entsteht, ist erwiesen lauffähig.

Bewusst DETERMINISTISCH und OHNE LLM: der Ablauf ruft die echten
Generierungs-/Rechen-Tools direkt (in-process ``call_tool``), verbraucht also
kein Modell-Kontingent und läuft immer gleich. Die vier Schritte:

  1. Idee        — die fixe Spezifikation (5 V→3,3 V-Regler, LED, Testpunkt)
  2. Schaltplan  — ``generate_project`` erzeugt ``.kicad_sch`` + ``.kicad_pcb``
  3. Berechnung  — LED-Vorwiderstand aus den echten Spec-Werten nachrechnen
  4. Platine     — der ``.kicad_pcb``-Pfad (das Panel öffnet ihn im Editor)

Kern pur (``on_step``-Callback für Live-Narration, ``server`` injectable) —
headless testbar; der Knopf lebt im Chat-Panel.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Callable, List, Optional

from kicad_mcp.selftest import SPEC_PATH, load_spec

# Grüne LED: typische Durchlassspannung, für die Vorwiderstands-Rechnung.
_GREEN_LED_VF = 2.0
# Angenehmer LED-Strombereich (mA) — außerhalb: zu dunkel bzw. zu hell/heiß.
_LED_MIN_MA, _LED_MAX_MA = 1.0, 20.0


def _call(server, name: str, args: dict) -> dict:
    result = asyncio.run(server.call_tool(name, args))
    data = getattr(result, "structured_content", None)
    return data if isinstance(data, dict) else {"raw": data}


def _rail_voltage(spec: dict) -> float:
    """Die Ausgangsspannung aus dem Regler-Value ableiten (…-3.3 → 3.3)."""
    for part in spec.get("parts", []):
        val = str(part.get("value", ""))
        if "3.3" in val or "3V3" in val:
            return 3.3
    return 3.3


def _led_resistor_check(spec: dict) -> dict:
    """Den LED-Vorwiderstand aus den ECHTEN Spec-Werten nachrechnen.

    Findet R (value „1k") und die Versorgungsspannung, rechnet den LED-Strom
    I = (V_rail − V_f) / R und bewertet ihn gegen den sinnvollen Bereich.
    Rein, ohne I/O — die „semantische Schicht" an einem Mini-Beispiel.
    """
    rail = _rail_voltage(spec)
    r_ohms = None
    for part in spec.get("parts", []):
        if part.get("ref", "").startswith("R"):
            r_ohms = _parse_ohms(str(part.get("value", "")))
            break
    if not r_ohms:
        return {"ok": False, "text": "kein Vorwiderstand in der Spec gefunden"}
    current_ma = (rail - _GREEN_LED_VF) / r_ohms * 1000.0
    ok = _LED_MIN_MA <= current_ma <= _LED_MAX_MA
    verdict = "im sinnvollen Bereich" if ok else "außerhalb des Zielbereichs"
    return {
        "ok": ok,
        "rail_v": rail, "r_ohms": r_ohms, "vf_v": _GREEN_LED_VF,
        "current_ma": round(current_ma, 2),
        "text": (f"LED-Zweig: ({rail} V − {_GREEN_LED_VF} V) / "
                 f"{_fmt_ohms(r_ohms)} = {current_ma:.1f} mA — {verdict} "
                 f"({_LED_MIN_MA:.0f}–{_LED_MAX_MA:.0f} mA)."),
    }


def _parse_ohms(value: str) -> Optional[float]:
    """„1k"/„4.7k"/„220"/„1M" → Ohm (float), sonst None."""
    v = value.strip().lower().replace("ω", "").replace("ohm", "").strip()
    mult = 1.0
    for suffix, factor in (("k", 1e3), ("m", 1e6), ("r", 1.0)):
        if v.endswith(suffix):
            mult = factor
            v = v[:-1]
            break
    try:
        return float(v) * mult
    except ValueError:
        return None


def _fmt_ohms(ohms: float) -> str:
    if ohms >= 1e6:
        return f"{ohms/1e6:g} MΩ"
    if ohms >= 1e3:
        return f"{ohms/1e3:g} kΩ"
    return f"{ohms:g} Ω"


def run_demo(out_dir: str, server=None, spec_path: Optional[str] = None,
             on_step: Optional[Callable[[str], None]] = None) -> dict:
    """Den Demo-Ablauf fahren; Ergebnis-Dict mit Schritten + Board-Pfad.

    ``server`` wird bei Bedarf lazy gebaut (``create_server``); ``on_step``
    streamt je Schritt eine Zeile fürs Panel-Transkript. Wirft nie — Fehler
    landen als ``ok:False`` im jeweiligen Schritt.
    """
    if server is None:
        from kicad_mcp.server import create_server
        server = create_server()
    os.makedirs(out_dir, exist_ok=True)
    spec = load_spec(spec_path or SPEC_PATH)
    steps: List[dict] = []

    def emit(line: str) -> None:
        if on_step:
            on_step(line)

    # 1) Idee
    n_parts, n_nets = len(spec["parts"]), len(spec["nets"])
    steps.append({"key": "idee", "ok": True,
                  "title": "Idee",
                  "text": (f"{spec.get('description','').split('.')[0]} — "
                           f"{n_parts} Bauteile, {n_nets} Netze.")})
    emit(f"① Idee: {steps[-1]['text']}")

    # 2) Schaltplan (+ PCB in einem Rutsch)
    board_path = ""
    try:
        out = _call(server, "generate_project", {
            "output_dir": out_dir,
            "parts": json.dumps(spec["parts"]),
            "nets": json.dumps(spec["nets"]),
            "board": json.dumps(spec.get("board") or {}),
            "project_name": spec.get("project_name", "kicad_mcp_demo")})
        files = out.get("files") or {}
        sch_ok = bool(files.get("schematic") and
                      os.path.isfile(files["schematic"]))
        board_path = files.get("pcb", "")
        steps.append({"key": "schaltplan", "ok": sch_ok,
                      "title": "Schaltplan",
                      "text": (f"{os.path.basename(files.get('schematic',''))} "
                               "erzeugt — Regler, Cs, Vorwiderstand, LED, "
                               "Testpunkt, verdrahtet.")
                      if sch_ok else f"Fehler: {out.get('error') or out}"})
    except Exception as exc:  # pragma: no cover - defensiv
        steps.append({"key": "schaltplan", "ok": False, "title": "Schaltplan",
                      "text": f"{type(exc).__name__}: {exc}"})
    emit(f"② Schaltplan: {steps[-1]['text']}")

    # 3) Berechnung (rein, aus den Spec-Werten)
    calc = _led_resistor_check(spec)
    steps.append({"key": "berechnung", "ok": calc["ok"],
                  "title": "Berechnung", "text": calc["text"],
                  "detail": {k: calc[k] for k in
                             ("rail_v", "r_ohms", "vf_v", "current_ma")
                             if k in calc}})
    emit(f"③ Berechnung: {steps[-1]['text']}")

    # 4) Platine
    pcb_ok = bool(board_path and os.path.isfile(board_path))
    b = spec.get("board") or {}
    dims = (f"{b.get('width','?')}×{b.get('depth','?')} mm"
            if b else "")
    steps.append({"key": "platine", "ok": pcb_ok, "title": "Platine",
                  "text": (f"{os.path.basename(board_path)} · {dims} — "
                           "erzeugt." if pcb_ok
                           else "PCB nicht erzeugt.")})
    emit(f"④ Platine: {steps[-1]['text']}")

    return {
        "ok": all(s["ok"] for s in steps),
        "steps": steps,
        "board_path": board_path,
        "out_dir": out_dir,
    }


def summary_line(result: dict) -> str:
    """Eine Abschlusszeile fürs Transkript."""
    ok = sum(1 for s in result["steps"] if s["ok"])
    total = len(result["steps"])
    if result["ok"]:
        return (f"✅ Demo fertig ({ok}/{total} Schritte) — Schaltplan & "
                "Platine liegen im Projekt.")
    return f"⚠ Demo mit Problemen ({ok}/{total} Schritte) — Details oben."
