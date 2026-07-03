# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-End-Loop durchs Produkt: alle Super-Features gegen das echte Board.

Die Feature-Buttons sind nur Dispatcher ihrer Registry-Prompts — also braucht
der Selbsttest keine GUI-Klicks: er iteriert ``superfeatures.FEATURES`` und
schickt jeden kanonischen Prompt als echten Chat-Zug durch den ECHTEN
Produktpfad (claude_bridge → MCP-Server → offenes Board → Antwort). Pro
Feature entsteht ein Verdikt + Messwerte; am Ende ein Report als Markdown
(fürs Zurücklesen durch den Entwicklungs-Agenten, der daraus die Prompts
verbessert) und als JSON (maschinenlesbar) unter ``<Projekt>/.kicad-mcp/``.

Sicherheit: Der ``[E2E-TESTMODUS]``-Zusatz verbietet jede Mutation — Features
enden mit Plan + ``[[CHOICES]]`` (das Go-Gate wird also mitgetestet, aber nie
ausgelöst) oder mit einer ehrlichen „Voraussetzung fehlt"-Meldung, die als
KORREKTES Verhalten gewertet wird. Das Board bleibt unangetastet.

Kern pur (``ask`` injectable) — headless testbar; der 🧪-Button lebt im
Einrichtungs-Fenster.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from . import claude_bridge, i18n, superfeatures
from .version import __version__

# Testmodus-Zusatz an jeden Feature-Prompt: nie mutieren, knapp bleiben,
# fehlende Voraussetzungen ehrlich melden (= PASS, nicht FAIL).
E2E_SUFFIX = (
    "\n\n[E2E-TESTMODUS] Dies ist ein automatischer Produkt-Selbsttest. "
    "(a) Führe KEINE Board- oder Datei-Mutation aus — auch nicht nach "
    "eigenem Ermessen; beende stattdessen vor jeder Änderung mit deinem "
    "Plan und einer letzten Zeile [[CHOICES: Go|Abbrechen]]. "
    "(b) Fehlt eine Voraussetzung (keine Auswahl, kein Datenblatt-PDF, kein "
    "Foto, kein ngspice), sage das in 1-2 Sätzen und stoppe — das ist im "
    "Test das KORREKTE Verhalten. "
    "(c) Bleib knapp: höchstens ~10 Tool-Aufrufe, keine Renderings."
)

# Verdikte.
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

# Budget pro Feature — der Selbsttest soll das Verhalten zeigen, nicht die
# Aufgabe zu Ende bringen.
MAX_TURNS_PER_FEATURE = 15
IDLE_TIMEOUT_S = 120.0
MAX_SECONDS_PER_FEATURE = 420.0


@dataclass
class FeatureResult:
    key: str
    label: str
    verdict: str = FAIL
    tag: str = ""            # kurze Einordnung: plan+go / fragt-nach / …
    seconds: float = 0.0
    tools: List[str] = field(default_factory=list)
    mcp_status: str = ""
    error: str = ""
    choices: List[str] = field(default_factory=list)
    reply_excerpt: str = ""


def build_test_prompt(feat) -> str:
    """Der exakte Button-Prompt + Testmodus-Zusatz."""
    return (feat.prompt or "") + E2E_SUFFIX


def judge(result: dict, tools: list, choices: list) -> "tuple[str, str]":
    """Heuristisches Verdikt (verdict, tag) für einen Feature-Durchlauf.

    PASS-Formen: Plan mit Go-Gate erreicht (CHOICES), ehrliche Nachfrage /
    „Voraussetzung fehlt", oder ein Bericht nach echten Tool-Aufrufen.
    WARN: Antwort kam, aber ohne einen einzigen MCP-Tool-Aufruf und ohne
    Frage — das Feature hat vermutlich nur geredet. FAIL: Bridge-/MCP-Fehler.
    """
    if not result.get("ok"):
        return FAIL, "bridge-fehler"
    if str(result.get("mcp_status") or "").startswith("failed"):
        return FAIL, "mcp-nicht-verbunden"
    text = (result.get("text") or "").strip()
    if not text:
        return FAIL, "leere-antwort"
    if choices:
        return PASS, "plan+go-gate"
    if text.rstrip().endswith("?"):
        return PASS, "fragt-nach"
    if tools:
        return PASS, "bericht"
    return WARN, "keine-tools-benutzt"


def run_feature(feat, plan, ask: Callable = claude_bridge.ask,
                on_line: Optional[Callable[[str], None]] = None) -> FeatureResult:
    """Ein Feature einmal durch den echten Produktpfad schicken."""
    res = FeatureResult(key=feat.key, label=feat.label)
    tools: list = []
    t0 = time.perf_counter()
    try:
        out = ask(
            build_test_prompt(feat),
            project_dir=plan.run_cwd,
            mcp_config_path=plan.config_arg_path,
            session_id=None,               # jedes Feature frisch — kein Kontext-Bleed
            claude_cmd=plan.claude_cmd,
            extra_args=["--max-turns", str(MAX_TURNS_PER_FEATURE)],
            idle_timeout=IDLE_TIMEOUT_S,
            max_seconds=MAX_SECONDS_PER_FEATURE,
            language=i18n.reply_language_name(),
            on_tool=lambda name, _inp: tools.append(name),
            on_status=(lambda s: on_line(f"    {s}")) if on_line else None,
        )
    except Exception as exc:  # der Loop darf an keinem Feature sterben
        res.seconds = round(time.perf_counter() - t0, 1)
        res.error = f"{type(exc).__name__}: {exc}"
        res.tag = "runner-exception"
        return res
    res.seconds = round(time.perf_counter() - t0, 1)
    res.tools = tools
    res.mcp_status = out.get("mcp_status") or ""
    res.error = out.get("error") or ""
    text, choices = claude_bridge.parse_choices(out.get("text") or "")
    res.choices = choices
    res.reply_excerpt = text.strip()[:600]
    res.verdict, res.tag = judge(out, tools, choices)
    return res


def run_all(plan, features=None, ask: Callable = claude_bridge.ask,
            on_line: Optional[Callable[[str], None]] = None) -> List[FeatureResult]:
    """Alle (SHIPPED-)Features sequenziell testen; ``on_line`` streamt Fortschritt."""
    feats = features if features is not None else [
        f for f in superfeatures.all_features()
        if f.status == superfeatures.SHIPPED]
    results: List[FeatureResult] = []
    for i, feat in enumerate(feats, 1):
        if on_line:
            on_line(f"[{i}/{len(feats)}] {feat.label} …")
        res = run_feature(feat, plan, ask=ask, on_line=on_line)
        if on_line:
            on_line(f"    → {res.verdict} ({res.tag}, {res.seconds}s, "
                    f"{len(res.tools)} Tools)")
        results.append(res)
    return results


def render_report(results: List[FeatureResult], meta: dict) -> str:
    """Der Markdown-Report — geschrieben, um von einem Agenten ZURÜCKGELESEN
    zu werden: Kopf mit Umgebung, Verdikt-Tabelle, dann je Feature die
    Details (Tools, Fehler, Antwort-Auszug), FAIL/WARN zuerst."""
    lines = [
        f"# E2E-Report — Claude für KiCad v{meta.get('version', __version__)}",
        "",
        f"- Datum: {meta.get('date', '')}",
        f"- Board: {meta.get('board', '(unbekannt)')}",
        f"- Transport: {meta.get('transport', '')}",
        f"- Sprache: {meta.get('language', '')}",
        f"- Features getestet: {len(results)}",
        "",
        "> Testmodus: keine Mutation — Go-Gates enden beim Plan; "
        "'Voraussetzung fehlt'-Meldungen zählen als korrektes Verhalten.",
        "",
        "| Feature | Verdikt | Einordnung | Dauer | Tools | Fehler |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        err = (r.error or "").replace("|", "/")[:60]
        lines.append(f"| {r.label} | {r.verdict} | {r.tag} | {r.seconds}s "
                     f"| {len(r.tools)} | {err} |")
    counts = {v: sum(1 for r in results if r.verdict == v)
              for v in (PASS, WARN, FAIL)}
    lines += ["", f"**Summe:** {counts[PASS]} PASS · {counts[WARN]} WARN · "
                  f"{counts[FAIL]} FAIL", "", "---", "", "## Details", ""]
    order = {FAIL: 0, WARN: 1, PASS: 2}
    for r in sorted(results, key=lambda x: order.get(x.verdict, 3)):
        lines.append(f"### {r.label} — {r.verdict} ({r.tag})")
        lines.append(f"- Dauer: {r.seconds}s · MCP: {r.mcp_status or '—'}")
        lines.append("- Tools: " + (", ".join(r.tools) or "—"))
        if r.choices:
            lines.append("- Angebotene Entscheidung: " + " | ".join(r.choices))
        if r.error:
            lines.append(f"- Fehler: {r.error}")
        if r.reply_excerpt:
            lines.append("")
            lines.append("```")
            lines.append(r.reply_excerpt)
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


def write_report(project_dir: str, results: List[FeatureResult],
                 meta: dict) -> "tuple[str, str]":
    """MD + JSON nach ``<Projekt>/.kicad-mcp/`` schreiben; Pfade zurück."""
    out_dir = os.path.join(project_dir, ".kicad-mcp")
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "e2e_report.md")
    json_path = os.path.join(out_dir, "e2e_report.json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_report(results, meta))
    payload: dict[str, Any] = {"meta": meta, "results": [vars(r) for r in results]}
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return md_path, json_path
