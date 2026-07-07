# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-Mehrsprachigkeit für das Plugin-GUI (Deutsch/Englisch).

Design: **Deutsch ist die Quellsprache** — alle Strings im Code bleiben
deutsch, ``tr()`` übersetzt beim Rendern über einen Katalog nach Englisch.
Fehlt ein Eintrag, bleibt der deutsche Text stehen (graceful degradation
statt Platzhalter-Salat); der Katalog wächst inkrementell.

Sprachwahl (AUTO): explizite Einstellung (``settings.json`` → language) →
KiCads eigene Sprach-Einstellung (``kicad_common.json`` → system.language) →
OS-Locale → Englisch. Deutschsprachige Umgebung ⇒ ``de``, alles andere ⇒
``en``. Die Antwortsprache des Agenten folgt derselben Wahl
(``claude_bridge`` hängt eine Sprachanweisung an den System-Prompt).

Pure/stdlib (kein wx) — headless testbar.
"""

from __future__ import annotations

import json
import locale
import os
from typing import Optional

LANG_AUTO = "auto"
LANG_DE = "de"
LANG_EN = "en"

_current: Optional[str] = None  # resolved language, cached per process


def _lang_from_kicad_common(common_path: Optional[str]) -> Optional[str]:
    """KiCads eingestellte Sprache aus kicad_common.json (oder None)."""
    if not common_path or not os.path.isfile(common_path):
        return None
    try:
        with open(common_path, encoding="utf-8") as fh:
            data = json.load(fh)
        raw = str(((data.get("system") or {}).get("language")) or "").strip()
    except Exception:
        return None
    if not raw or raw.lower() in ("default", ""):
        return None
    return LANG_DE if raw.lower().startswith("de") else LANG_EN


def _lang_from_locale() -> str:
    for probe in (os.environ.get("LC_ALL"), os.environ.get("LANG")):
        if probe:
            return LANG_DE if probe.lower().startswith("de") else LANG_EN
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    return LANG_DE if str(loc).lower().startswith("de") else LANG_EN


def detect_lang(setting: str = LANG_AUTO,
                common_path: Optional[str] = None) -> str:
    """Resolve ``de``/``en``: Einstellung → KiCad-Sprache → Locale → en."""
    setting = (setting or LANG_AUTO).strip().lower()
    if setting in (LANG_DE, LANG_EN):
        return setting
    return _lang_from_kicad_common(common_path) or _lang_from_locale()


def set_lang(lang: str) -> None:
    """Fix the process-wide language (called once at panel start)."""
    global _current
    _current = LANG_DE if (lang or "").lower().startswith("de") else LANG_EN


def get_lang() -> str:
    global _current
    if _current is None:
        _current = detect_lang()
    return _current


def reply_language_name() -> str:
    """Der Sprachname für die Agent-Anweisung („Antworte auf …")."""
    return "Deutsch" if get_lang() == LANG_DE else "English"


def tr(text: str) -> str:
    """German source string → current language (EN via catalog, else as-is)."""
    if get_lang() == LANG_DE:
        return text
    return _EN.get(text, text)


# --------------------------------------------------------------------------- #
# Katalog Deutsch → Englisch. Quellsprache = exakt der String im Code.
# Fehlende Einträge fallen sichtbar (aber funktional) auf Deutsch zurück.
# --------------------------------------------------------------------------- #
_EN: dict = {
    # --- Chat-Chrome -----------------------------------------------------
    "Frag Claude etwas über dieses Board …":
        "Ask Claude about this board …",
    "Senden": "Send",
    "Stopp": "Stop",
    "🆕 Neu": "🆕 New",
    "Neue Unterhaltung beginnen (der bisherige Verlauf bleibt sichtbar, "
    "aber Claude startet ohne Kontext)":
        "Start a fresh conversation (the transcript stays visible, but "
        "Claude starts without context)",
    "⚙ Option wählen …": "⚙ Choose option …",
    "🔗 Auswahl einbeziehen": "🔗 Include selection",
    "Hängt deine aktuelle Editor-Auswahl als Kontext an jede getippte "
    "Nachricht — 'das hier'/'die markierten' funktioniert dann ohne "
    "Referenzen zu tippen. Die ✨-Buttons nutzen die Auswahl immer "
    "(markiert = nur darauf, sonst boardweit).":
        "Attaches your current editor selection as context to every typed "
        "message — 'this'/'the selected ones' then works without typing "
        "references. The ✨ buttons always use the selection "
        "(selected = scoped, otherwise board-wide).",
    "✨ Super-Features": "✨ Super features",
    "⏹ Abgebrochen.": "⏹ Cancelled.",
    "⏳ Es läuft noch ein Zug — danach nochmal klicken.":
        "⏳ A turn is still running — click again afterwards.",
    "🎯 Wirkt auf deine Auswahl: ": "🎯 Acting on your selection: ",
    "🎯 Wirkt boardweit (keine Auswahl im Editor)":
        "🎯 Acting board-wide (nothing selected in the editor)",
    "Unterhaltung aus letzter Sitzung fortgesetzt.":
        "Resumed the conversation from your last session.",
    "Neue Unterhaltung begonnen.": "Started a new conversation.",
    "Projekt geöffnet: ": "Project opened: ",
    "Weiter": "Next",
    "Ablauf beenden": "stop guided flow",
    "Ablauf stoppen": "stop flow",
    "Referenz: Platine 0 DRC & Schaltplan datenblatt-geprüft":
        "Reference: board 0 DRC & schematic datasheet-verified",
    "Platine 0 DRC": "board 0 DRC",
    "Platine in Arbeit": "board in progress",
    "Schaltplan datenblatt-geprüft": "schematic datasheet-verified",
    "Schaltplan nicht datenblatt-geprüft": "schematic not datasheet-verified",
    "In Arbeit — Platine & Schaltplan noch nicht vorzeigbar":
        "In progress — board & schematic not yet presentable",
    "Die Skills laufen jetzt automatisch nacheinander — jeder als echter "
    "Tool-Aufruf, den du selbst so machen könntest. „✋ Stoppen“ hält an; "
    "jeder Skill bleibt einzeln per ✨-Button nutzbar.":
        "The skills now run automatically one after another — each a real "
        "tool call you could make yourself. “✋ Stop” halts; every skill "
        "stays available individually via its ✨ button.",
    "Demo-Ablauf abgeschlossen — alle Skills sind durch.":
        "Demo flow complete — all skills have run.",
    "Geführter Ablauf beendet — jeder Skill bleibt einzeln per "
    "✨-Button nutzbar.":
        "Guided flow stopped — every skill remains available via its "
        "✨ button.",
    "Klicke unten den „Weiter“-Chip — er startet die Schritte der Reihe "
    "nach; jeder ist auch einzeln per ✨-Button auslösbar.":
        "Click the \u201eNext\u201c chip below — it runs the steps in order; "
        "each one can also be triggered individually via its ✨ button.",
    "Projekt gewechselt: ": "Switched project: ",
    "📋 kopiert": "📋 copied",
    "Aktive Optionen: ": "Active options: ",
    "— Optionen zurücksetzen": "— Reset options",
    "formuliert die Antwort …": "writing the reply …",
    "MCP verbunden — Claude liest dein Board …":
        "MCP connected — Claude is reading your board …",
    "⚠ MCP NICHT verbunden!": "⚠ MCP NOT connected!",
    "Tool-Ergebnis erhalten …": "tool result received …",
    "Bereit.": "Ready.",

    # --- Ampel-Zeile ------------------------------------------------------
    "MCP": "MCP",
    "IPC": "IPC",
    "ngspice": "ngspice",
    "Status des Tool-Servers (letzter Zug)": "Tool-server status (last turn)",
    "Live-Verbindung zur KiCad-GUI (Links/Selektion)":
        "Live connection to the KiCad GUI (links/selection)",
    "SPICE-Simulator gefunden? (für 📈 Simulation)":
        "SPICE simulator found? (for 📈 Simulation)",
    "Klick: Einrichtung/Diagnose öffnen": "Click: open setup/diagnosis",
    "Diagnose: Einrichtung / Update öffnen":
        "Diagnosis: open Setup / Update",

    # --- Banner-Zusammenfassung + Status/Buttons (1. Linux-Smoke-Lücken) ----
    "Platine": "Board",
    "Footprints": "Footprints",
    "Netze": "Nets",
    "Lagen": "Layers",
    "Bestückung": "Population",
    "Größe": "Size",
    "(ⓘ aus Edge.Cuts, best effort)": "(ⓘ from Edge.Cuts, best effort)",
    "↶ Rückgängig": "↶ Undo",
    "Letzte Board-Änderung rückgängig (KiCad Ctrl+Z)":
        "Undo the last board change (KiCad Ctrl+Z)",
    # Einrichtungs-Knopfleiste
    "Erneut prüfen": "Re-check",
    "Update prüfen": "Check update",
    "Diagnose": "Diagnostics",
    "Chat starten": "Start chat",
    # Startup-Banner
    "Gefällt dir das Plugin? → ": "Enjoying the plugin? → ",
    "Empfiehl es einem Freund ✉": "Recommend it to a friend ✉",
    "📍 alle markieren": "📍 select all",
    "verbunden mit": "connected to",
    "kein Board erkannt": "no board detected",
    # Demo-Knopf
    "▶ Demo": "▶ Demo",
    "Baut die Testschaltung automatisch vor: Idee → Schaltplan → "
    "Berechnung → Platine. Ohne Eingabe, ohne Modell-Kontingent.":
        "Builds the test circuit automatically: idea → schematic → "
        "calculation → board. No input, no model quota.",
    "Demo — baut die Testschaltung": "Demo — building the test circuit",
    "In KiCad öffnen: Datei → Öffnen →": "Open in KiCad: File → Open →",
    # KI-Backend-Auswahl
    "KI-Backend:": "AI backend:",
    "Claude Code (Anthropic)": "Claude Code (Anthropic)",
    "Codex (OpenAI) — experimentell": "Codex (OpenAI) — experimental",
    "Welches Agenten-CLI die Anfragen bearbeitet. Claude Code ist "
    "erprobt; weitere MCP-fähige CLIs sind experimentell.":
        "Which agent CLI handles requests. Claude Code is proven; other "
        "MCP-capable CLIs are experimental.",

    # --- Gruppen der Super-Feature-Leiste --------------------------------
    "🔎 Verstehen & Prüfen": "🔎 Understand & Check",
    "🧶 Layout & Skizze": "🧶 Layout & Sketch",
    "⚡ Elektrik & Norm": "⚡ Electrical & Standards",
    "🏭 Fertigung & Kosten": "🏭 Manufacturing & Cost",
    "📈 Simulation": "📈 Simulation",
    "🪄 Kreativ & Brücken": "🪄 Creative & Bridges",

    # --- Feature-Labels ----------------------------------------------------
    "🧶 Entwirren": "🧶 Untangle",
    "🚌 Bus-Radar": "🚌 Bus radar",
    "📄 Datenblatt-Abgleich": "📄 Datasheet diff",
    "🛡️ Design-Wächter": "🛡️ Design guard",
    "🔎 Test-Punkt-Wächter": "🔎 Test-point guard",
    "🔀 Pin-Tausch": "🔀 Pin swap",
    "💡 Board erklären": "💡 Explain board",
    "🧭 Netz-Navigator": "🧭 Net navigator",
    "📐 Ausrichten & Anordnen": "📐 Align & arrange",
    "⊙ Polar-Board": "⊙ Polar board",
    "🖊️ Skizzen-Layer": "🖊️ Sketch layer",
    "✏️ Skizzen-Dirigent": "✏️ Sketch conductor",
    "👁️ Mitdenken-Modus": "👁️ Watch mode",
    "🔥 Stromtragfähigkeit": "🔥 Ampacity",
    "⌚ Quarz-Load-Caps": "⌚ Crystal load caps",
    "🔩 Via-Optimierung": "🔩 Via optimizer",
    "🌡️ Thermik": "🌡️ Thermals",
    "🌡️ Betriebstemperatur": "🌡️ Operating temp",
    "📐 Slew-Rate": "📐 Slew rate",
    "〰️ Impedanz": "〰️ Impedance",
    "🏭 DFM-Check": "🏭 DFM check",
    "💰 Kosten-Schätzer": "💰 Cost estimator",
    "🧬 SPICE-Modelle": "🧬 SPICE models",
    "💰 BOM-Konsolidierung": "💰 BOM consolidation",
    "🏭 Fab-Standardteile": "🏭 Fab preferred parts",
    "🛒 Bauteil-Sourcing": "🛒 Part sourcing",
    "📷 Foto→Schaltung": "📷 Photo→schematic",
    "📄 Datenblatt→Schaltung": "📄 Datasheet→circuit",
    "⚡ Sicherheitsabstände": "⚡ Safety spacing",
    "🔌 Schutzklassen": "🔌 Protection classes",
    "💾 Firmware-Pinmap": "💾 Firmware pinmap",
    "📉 MLCC-Derating": "📉 MLCC derating",
    "🔤 Silk-Aufräumen": "🔤 Silk cleanup",

    # --- Einstellungen (Setup-Dialog) -------------------------------------
    "Einstellungen": "Settings",
    "Sprache:": "Language:",
    "Automatisch": "Automatic",
    "Transport:": "Transport:",
    "stdio (Server pro Nachricht)": "stdio (server per message)",
    "Warm-Server (http, empfohlen nach Validierung)":
        "Warm server (http, recommended once validated)",
    "ngspice-Pfad:": "ngspice path:",
    "(leer = automatisch suchen)": "(empty = auto-detect)",
    "Max. Schritte pro Nachricht:": "Max steps per message:",
    "Einstellungen speichern": "Save settings",
    "Gespeichert — gilt ab dem nächsten Chat-Zug.":
        "Saved — takes effect from the next chat turn.",

    # --- E2E-Test ----------------------------------------------------------
    "🧪 E2E-Test": "🧪 E2E test",
    "🧪 E2E-Test läuft …": "🧪 E2E test running …",
    "Schließen": "Close",

    # --- Systemtest (standalone, ohne Claude) -------------------------------
    "🔬 Systemtest": "🔬 System test",
    "🔬 Systemtest läuft …": "🔬 System test running …",
    "Prüft die Maschinerie OHNE Claude (kein Kontingent): erzeugt "
    "ein Demo-Board aus der eingebauten Vorlage und testet Server, "
    "Generatoren und Werkzeuge lokal — dauert ~1 Minute.":
        "Checks the machinery WITHOUT Claude (no quota): generates a demo "
        "board from the built-in template and tests server, generators and "
        "tools locally — takes ~1 minute.",
    "✅ Alles grün.": "✅ All green.",
    "❌ Es gibt rote Schritte — Report ansehen:":
        "❌ Some steps are red — see the report:",
    "Alle Super-Features automatisch gegen das offene Board testen "
    "(ohne Board-Änderung) und einen Report schreiben — dauert je "
    "nach Board 15-45 Minuten.":
        "Automatically test every super feature against the open board "
        "(no board changes) and write a report — takes 15-45 minutes "
        "depending on the board.",
    "Alle {n} Super-Features werden nacheinander als echte "
    "Claude-Züge gegen das offene Board getestet — OHNE "
    "Board-Änderung (Testmodus stoppt vor jedem Go). Das "
    "dauert typischerweise 15-45 Minuten und verbraucht "
    "entsprechend Claude-Kontingent. Starten?":
        "All {n} super features will run one after another as real "
        "Claude turns against the open board — WITHOUT board changes "
        "(test mode stops before every Go). This typically takes 15-45 "
        "minutes and uses Claude quota accordingly. Start?",
    "Report geschrieben:": "Report written:",
    "→ Diese Datei dem Entwicklungs-Agenten geben — er "
    "liest sie zurück und verbessert die Prompts.":
        "→ Hand this file to the development agent — it reads it back "
        "and improves the prompts.",
}
