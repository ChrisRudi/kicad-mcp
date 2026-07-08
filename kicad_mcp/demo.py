# SPDX-License-Identifier: GPL-3.0-or-later
"""Demo-Ablauf: aus einer Idee automatisch Schaltplan → Berechnung → Platine.

Der „Demo"-Knopf im Panel führt genau das vor, was auch der Systemtest baut
(``resources/data/selftest_board.json``) — aber sichtbar, Schritt für Schritt,
als Erstkontakt und Live-Beweis: was hier entsteht, ist erwiesen lauffähig.

Bewusst DETERMINISTISCH und OHNE LLM: der Ablauf ruft die echten
Generierungs-/Rechen-Tools direkt (in-process ``call_tool``), verbraucht also
kein Modell-Kontingent und läuft immer gleich. Die Schritte sind als sichtbare
Tool-Kette gebaut (Feld-Wunsch „die Entstehung verfolgen") — jeder ``⚙``-Aufruf
ist ein echtes MCP-Tool, das der Nutzer genauso aus dem Chat rufen kann:

  1. Idee        — Steckbrief: WAS entsteht (voller Beschreibungstext +
                   Leitbauteile), damit man es VOR dem Bauen sieht
  2. Prüfen      — ``validate_design`` (Refs/Pin-Typen/Board-Maß, schreibt nichts)
  3. Schaltplan  — ``generate_schematic`` erzeugt ``.kicad_sch`` (+ ERC-Report)
  4. Berechnung  — LED-Vorwiderstand aus den echten Spec-Werten nachrechnen
  5. Platine     — ``generate_pcb`` erzeugt ``.kicad_pcb`` (Panel öffnet ihn)
  6. Bilder      — ``export_png`` rendert Schaltplan UND Platine als PNG, das
                   Panel öffnet sie (Ergebnis SEHEN, nicht nur Text lesen)

Narration in der ERSTEN Person („Ich zeichne den Schaltplan …") — der Nutzer
schaut zu, er wird nicht instruiert.

Die Zerlegung liefert byte-identische Dateien wie das frühere
``generate_project`` (alle Demo-Kits sind Einzelblatt; empirisch verifiziert).

Kern pur (``on_step``-Callback für Live-Narration, ``server`` injectable) —
headless testbar; der Knopf lebt im Chat-Panel.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Callable, List, Optional

from kicad_mcp.selftest import SPEC_PATH, load_spec

# Verzeichnis der Demo-Bausatz-Specs (die 10 Schaustück-Schaltungen).
DEMO_KITS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "resources", "data", "demo_kits")


def kit_spec_path(key: str) -> str:
    """Spec-Pfad eines Demo-Bausatzes (``<key>.json``) — leer, wenn es ihn
    (noch) nicht gibt."""
    path = os.path.join(DEMO_KITS_DIR, f"{key}.json")
    return path if os.path.isfile(path) else ""


# Grüne LED: typische Durchlassspannung, für die Vorwiderstands-Rechnung.
_GREEN_LED_VF = 2.0
# Angenehmer LED-Strombereich (mA) — außerhalb: zu dunkel bzw. zu hell/heiß.
_LED_MIN_MA, _LED_MAX_MA = 1.0, 20.0


def _call(server, name: str, args: dict) -> dict:
    result = asyncio.run(server.call_tool(name, args))
    data = getattr(result, "structured_content", None)
    return data if isinstance(data, dict) else {"raw": data}


def _tool_line(tool: str, detail: str) -> str:
    """Eine sichtbare Tool-Aufruf-Zeile fürs Transkript (Feld-Wunsch: „die
    Entstehung verfolgen"). Zeigt dem Nutzer, WELCHES echte MCP-Tool den
    Schritt macht — genau so ruft er es selbst aus dem Chat auf. Das Panel
    färbt ``⚙``-Zeilen gedämpft (wie Claudes eigene Tool-Zeilen)."""
    return f"⚙ {tool}  ·  {detail}"


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


def _spec_has_led(spec: dict) -> bool:
    """Hat die Spec einen LED-Zweig (für die Vorwiderstands-Rechnung)?"""
    for part in spec.get("parts", []):
        name = str(part.get("name", "")).upper()
        if "LED" in name or "LED" in str(part.get("footprint", "")).upper():
            return True
    return False


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


# Ref-Präfixe der „Leitbauteile" fürs Steckbrief: ICs, Halbleiter, Spulen,
# Quarze, Stecker — die tragen die Funktion; R/C sind Beiwerk.
_LEAD_PREFIXES = ("U", "Q", "D", "L", "Y", "J")


def _lead_parts(spec: dict, limit: int = 6) -> list[str]:
    """Die Leitbauteile eines Bausatzes als ``'Ref Value'`` — für den
    Steckbrief („was baue ich"). ICs/Halbleiter/Spulen/Quarze/Stecker zuerst,
    damit der Nutzer VOR dem Bauen sieht, worum es geht (nicht nur eine Zahl).
    Rein, ohne I/O."""
    lead = []
    for part in spec.get("parts", []):
        ref = str(part.get("ref", ""))
        if ref.startswith(_LEAD_PREFIXES):
            val = str(part.get("value", "")).strip()
            lead.append(f"{ref} {val}".strip())
        if len(lead) >= limit:
            break
    return lead


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

    # Gemeinsame Argumente für die Tool-Kette (JSON-Strings, wie ein LLM sie
    # aus dem Chat schicken würde — der Nutzer sieht dieselben Tools).
    parts_json = json.dumps(spec["parts"])
    nets_json = json.dumps(spec["nets"])
    board_cfg = spec.get("board") or {}
    board_json = json.dumps(board_cfg)
    name = spec.get("project_name", "kicad_mcp_demo")
    sch_path = os.path.join(out_dir, f"{name}.kicad_sch")
    pcb_path = os.path.join(out_dir, f"{name}.kicad_pcb")
    dims = (f"{board_cfg.get('width','?')}×{board_cfg.get('depth','?')} mm"
            if board_cfg else "")

    # 1) Idee — Steckbrief: WAS baue ich (voller Beschreibungstext + Leitteile),
    # damit der Nutzer VOR dem Bauen sieht, was entsteht — nicht nur eine Zahl.
    n_parts, n_nets = len(spec["parts"]), len(spec["nets"])
    desc = str(spec.get("description", "")).strip()
    lead = _lead_parts(spec)
    emit(f"① Das baue ich jetzt: {name}" + (f"  ·  {dims}" if dims else ""))
    if desc:
        emit(f"   {desc}")
    if lead:
        emit(f"   Kernbauteile: {' · '.join(lead)}")
    emit(f"   Umfang: {n_parts} Bauteile, {n_nets} Netze — ich lege Schaltplan "
         f"und Platine an und zeige sie dir am Ende als Bild.")
    steps.append({"key": "idee", "ok": True, "title": "Idee",
                  "text": (f"{desc.split('.')[0]} — {n_parts} Bauteile, "
                           f"{n_nets} Netze." if desc
                           else f"{n_parts} Bauteile, {n_nets} Netze.")})

    # 2) Prüfen — validate_design (sichtbarer Tool-Aufruf, schreibt nichts)
    emit(_tool_line("validate_design",
                    f"Spec prüfen ({n_parts} Bauteile, {n_nets} Netze)"))
    try:
        val = _call(server, "validate_design", {
            "parts": parts_json, "nets": nets_json, "board": board_json})
        v_ok = bool(val.get("valid"))
        v_text = ("Spec gültig — keine Konflikte (Refs, Pin-Typen, Board-Maß)."
                  if v_ok else
                  f"{val.get('error_count', '?')} Fehler: "
                  + "; ".join(val.get("errors", [])[:3]))
    except Exception as exc:  # pragma: no cover - defensiv
        v_ok, v_text = False, f"{type(exc).__name__}: {exc}"
    steps.append({"key": "pruefen", "ok": v_ok, "title": "Prüfen", "text": v_text})
    emit(f"② Ich prüfe die Spec: {v_text}")

    # 3) Schaltplan — generate_schematic (erzeugt .kicad_sch, .kicad_pro)
    emit(_tool_line("generate_schematic", f"→ {name}.kicad_sch"))
    try:
        out = _call(server, "generate_schematic", {
            "output_path": sch_path, "parts": parts_json,
            "nets": nets_json, "project_name": name})
        sch_ok = bool(out.get("success") and os.path.isfile(sch_path))
        erc = ""
        if "erc_clean" in out:
            erc = ("  · ERC 0 Fehler" if out.get("erc_clean") else
                   f"  · ERC {out.get('drc', {}).get('error_count', '?')} Fehler")
        sch_text = (f"{os.path.basename(sch_path)} erzeugt — {n_parts} Bauteile, "
                    f"{n_nets} Netze verdrahtet.{erc}") if sch_ok else \
            f"Fehler: {out.get('error') or out.get('errors') or out}"
    except Exception as exc:  # pragma: no cover - defensiv
        sch_ok, sch_text = False, f"{type(exc).__name__}: {exc}"
    steps.append({"key": "schaltplan", "ok": sch_ok, "title": "Schaltplan",
                  "text": sch_text})
    emit(f"③ Ich zeichne den Schaltplan: {sch_text}")

    # 4) Berechnung (rein, aus den Spec-Werten). Der Mini-Rechenschritt ist die
    # LED-Vorwiderstands-Prüfung; hat ein Bausatz keinen LED-Zweig, entfällt sie
    # neutral (kein Fehler) — die echte Rechnung übernehmen die Elektrik-Skills.
    if _spec_has_led(spec):
        calc = _led_resistor_check(spec)
        steps.append({"key": "berechnung", "ok": calc["ok"],
                      "title": "Berechnung", "text": calc["text"],
                      "detail": {k: calc[k] for k in
                                 ("rail_v", "r_ohms", "vf_v", "current_ma")
                                 if k in calc}})
    else:
        steps.append({"key": "berechnung", "ok": True, "title": "Berechnung",
                      "text": ("keine LED-Vorwiderstands-Prüfung nötig — die "
                               "Elektrik-Skills rechnen das Passende.")})
    emit(f"④ Ich rechne nach: {steps[-1]['text']}")

    # 5) Platine — generate_pcb (erzeugt .kicad_pcb)
    emit(_tool_line("generate_pcb",
                    f"→ {name}.kicad_pcb" + (f" ({dims})" if dims else "")))
    board_path = ""
    try:
        out = _call(server, "generate_pcb", {
            "output_path": pcb_path, "parts": parts_json, "nets": nets_json,
            "board": board_json, "project_name": name})
        board_path = out.get("output_path") or (
            pcb_path if os.path.isfile(pcb_path) else "")
        pcb_ok = bool(board_path and os.path.isfile(board_path))
        pcb_text = (f"{os.path.basename(board_path)} · {dims} — erzeugt."
                    if pcb_ok else f"Fehler: {out.get('error') or out}")
    except Exception as exc:  # pragma: no cover - defensiv
        pcb_ok, pcb_text = False, f"{type(exc).__name__}: {exc}"
    steps.append({"key": "platine", "ok": pcb_ok, "title": "Platine",
                  "text": pcb_text})
    emit(f"⑤ Ich route die Platine: {pcb_text}")

    # 6) Bilder — Schaltplan UND Platine als PNG rendern, damit der Nutzer das
    # Ergebnis SIEHT (nicht nur Text vorbeiziehen). Best-effort: fehlt kicad-cli
    # (headless ohne KiCad), bleibt der Schritt ok — die Dateien liegen ja im
    # Projekt. Das Panel öffnet die erzeugten PNGs anschließend.
    sch_png, pcb_png = _render_images(server, sch_path if sch_ok else "",
                                      board_path, emit)
    if sch_png and pcb_png:
        img_text = "Schaltplan- und Platinen-Bild erzeugt — ich öffne sie dir."
    elif sch_png or pcb_png:
        img_text = "Bild erzeugt — ich öffne es dir."
    else:
        img_text = ("Bild-Render hier nicht verfügbar (kicad-cli fehlt) — "
                    "Schaltplan & Platine liegen im Projekt.")
    steps.append({"key": "bilder", "ok": True, "title": "Bilder",
                  "text": img_text})
    emit(f"⑥ Ich rendere die Ansichten: {img_text}")

    return {
        "ok": all(s["ok"] for s in steps),
        "steps": steps,
        "board_path": board_path,
        "sch_png": sch_png,
        "pcb_png": pcb_png,
        "out_dir": out_dir,
    }


def _render_images(server, sch_path: str, board_path: str,
                   emit: Callable[[str], None]) -> tuple[str, str]:
    """Schaltplan- und Platinen-PNG über ``export_png`` (kicad-cli) rendern.
    Gibt ``(sch_png, pcb_png)`` zurück — leer, wenn ein Render nicht geht
    (kein kicad-cli, kein Rasterizer). Best-effort, wirft nie: die Bilder sind
    ein Bonus fürs Ansehen, kein Kern-Schritt."""
    def _png(src: str, what: str) -> str:
        if not (src and os.path.isfile(src)):
            return ""
        emit(_tool_line("export_png", f"{what} als Bild rendern"))
        try:
            out = _call(server, "export_png", {"file_path": src})
            return out.get("output_path", "") if out.get("success") else ""
        except Exception:  # pragma: no cover - kicad-cli/rasterizer fehlt
            return ""
    return _png(sch_path, "Schaltplan"), _png(board_path, "Platine")


def summary_line(result: dict) -> str:
    """Eine Abschlusszeile fürs Transkript."""
    ok = sum(1 for s in result["steps"] if s["ok"])
    total = len(result["steps"])
    if result["ok"]:
        return (f"✅ Demo fertig ({ok}/{total} Schritte) — Schaltplan & "
                "Platine liegen im Projekt.")
    return f"⚠ Demo mit Problemen ({ok}/{total} Schritte) — Details oben."


def main(argv=None) -> int:
    """CLI-Einstieg für den Plugin-Knopf: als Subprozess mit dem sys.path-
    Bootstrap gestartet (das Plugin-GUI-Python hat ``kicad_mcp`` nicht auf dem
    Pfad). Streamt je Schritt eine Zeile auf stdout; letzte Zeile = Board-Pfad.
    """
    import argparse
    import warnings
    warnings.filterwarnings("ignore", message=".*coroutine.*never awaited.*",
                            category=RuntimeWarning)
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--kit", default="",
                        help="Demo-Bausatz-Key (leer = Selftest-Board)")
    args = parser.parse_args(argv)
    spec_path = kit_spec_path(args.kit) if args.kit else None
    if args.kit and not spec_path:
        print(f"⚠ Bausatz '{args.kit}' hat keine Spec — nutze Selftest-Board.",
              flush=True)
    result = run_demo(args.out, spec_path=spec_path,
                      on_step=lambda line: print(line, flush=True))
    print(summary_line(result), flush=True)
    if result.get("board_path"):
        print("BOARD\t" + result["board_path"], flush=True)
    if result.get("sch_png"):
        print("SCH_PNG\t" + result["sch_png"], flush=True)
    if result.get("pcb_png"):
        print("PCB_PNG\t" + result["pcb_png"], flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
