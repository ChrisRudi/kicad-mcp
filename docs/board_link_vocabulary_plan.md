<!-- Plan/Spezifikation. STATUS: IMPLEMENTIERT in v0.4.0 (Commit edba3e1, 2026-06-18). Historisches Planungsdokument. -->
<!-- Erstellt 2026-06-18 aus der Diskussion "Sprache der Chat-Links vereinheitlichen". -->

# Plan: Chat-Board-Links — Sprache mit KiCad-Benennungen vereinheitlichen

**Status:** freigegeben, **IMPLEMENTIERT** in v0.4.0 (Commit `edba3e1`,
2026-06-18; verifiziert 2026-06-30). Hebel 1 (Producer-Vertrag im
System-Prompt, `plugin/claude_bridge.py`) und Hebel 3 (sichere `tokenize`-
Normalisierung: Netz-Slash/Case, Pin-Prosa, Layer-Alias in
`plugin/board_links.py`) sind umgesetzt und in `tests/test_plugin_board_links.py`
abgedeckt. Hebel 2 ist nur eine Konventions-Notiz (Tools liefern bereits
kanonisch) — als optionale Doku-Ergänzung offen. Dokument bleibt als
historische Planungs-Vorlage erhalten. Self-contained.

## 0. Problem

Die Chat-LLM (KiCad-Plugin-Panel) beschreibt Board-Elemente jedes Mal mit leicht
abweichender Syntax. Für die Interaktion gibt es klickbare **Board-Links**: Refs/
Netze/Layer/Pins/Koordinaten im Antworttext werden anklickbar und selektieren +
zoomen das Element im laufenden PCB-Editor. Diese Links greifen aber nur, wenn die
LLM die **kanonische KiCad-Schreibweise wörtlich** trifft → bei Drift kein Link.
Ziel: die Sprache der LLM an die KiCad-Benennungen koppeln.

## 1. Ist-Stand (verifiziert, mit Belegen)

Mechanik in `plugin/board_links.py`, zwei Schichten:

- **Empfänger** `tokenize(text, refs, nets, layers)` (`board_links.py:89`): macht
  nur Tokens klickbar, die **exakt** im realen Board-Vokabular vorkommen.
  Erkannt: Footprint-Refs `R12` (Boundary-Lookaround `:27`), Netznamen `GND`,
  Layer `F.Cu`, Pins `<ref>.<pin>` → `U1.33` (`:76`), Koordinaten `(x, y)` mm
  (`:33`). Bewusst **false-positive-frei** („a click always resolves", `:13`).
- **Vokabular-Quelle (Single Source of Truth)**: `board_targets()` live über
  kipy (`:249`) bzw. `board_targets_from_file()` Disk-Fallback (`:293`).
- **Rendering**: `plugin/chat_dialog.py::_append_claude` (`:169-192`), Vokabular
  je Antwort frisch geladen (`:283-289`); gibt `rendered` (Link-Anzahl) zurück;
  `_link_counts` existiert (`:292`).
- **Producer-Steuerung**: `plugin/claude_bridge.py::BEHAVIOR_SYSTEM_PROMPT`
  (`:92`), injiziert via `--append-system-prompt` (`:144`). Regelt
  Render-Ökonomie/Tool-Nutzung — **enthält keine Benennungs-Regel**. Drift daher
  unbeschränkt.

Befund: Links existieren und funktionieren wie beschrieben. Der Empfänger
normalisiert **nichts**; er ist ein reiner Exakt-Matcher.

## 2. Drift-Fälle (wo kein Link entsteht)

| LLM schreibt | Board-Token | Verlinkt? |
|---|---|---|
| „R12", „U8" | `R12` | ✅ |
| „the ground net", „Masse", „supply" | `GND` | ❌ paraphrasiert |
| „/GND" (hierarch. Pfad) | `GND` | ❌ führender Slash |
| „top copper", „Top Layer" | `F.Cu` | ❌ |
| „pin 33 of U1", „VCC-Pin" | `U1.33` | ❌ nur `<ref>.<pin>` |
| „bei x=120, y=84" | `(120, 84)` | ❌ Klammern nötig |

## 3. Lösungsansatz: kanonisches Vokabular als Vertrag auf beiden Seiten

`board_targets()` ist die Wahrheit. Standardisieren = dieses Set zum Vertrag
machen — Producer dazu bringen, kanonisch zu schreiben, und Empfänger nur die
**sicheren** Alias-Klassen normalisieren lassen.

### Hebel 1 — Producer-Vertrag im System-Prompt (zuerst, risikolos)

`BEHAVIOR_SYSTEM_PROMPT` (`claude_bridge.py:92`) um eine Benennungs-Regel
ergänzen, sinngemäß:

> „Benenne Board-Elemente ausschließlich mit ihrem kanonischen KiCad-Token:
> Footprints als bare Reference (`R12`, `U8`); Netze mit dem EXAKTEN Netznamen
> aus der Tool-Ausgabe (nicht paraphrasieren, nicht übersetzen); Layer kanonisch
> (`F.Cu`, `B.Cu`, `In1.Cu`); Pins als `<ref>.<pin>` (`U1.33`); Koordinaten als
> `(x, y)` in mm. Übernimm Namen aus Tool-Ergebnissen WÖRTLICH."

Wirkt sofort, kein False-Positive-Risiko, ist exakt das vom Tokenizer erwartete
Format.

### Hebel 2 — Tools liefern kanonisch (Konventions-Notiz)

Beschreibende Tools (`list_pcb_footprints`, `analyze_pcb_nets`, Layer-Listing)
geben Namen exakt wie KiCad zurück (tun sie überwiegend). Konvention:
Element-Namen nie paraphrasieren → Hebel 1 zahlt voll ein (LLM echo't kanonisch).

### Hebel 3 — sichere, begrenzte Normalisierung in `tokenize`

Nur deterministische Alias-Klassen, Zero-False-Positive bewahren:

- **Netze:** führenden `/` strippen (`/GND`↔`GND`), case-insensitiv gegen das
  Netz-Set matchen. Sicher (nur reale Netze).
- **Pins:** zusätzlich `pin <n> of <ref>` / `<ref> pin <n>` erkennen (Ref muss im
  Set sein) → Target `("pin",(ref,n))`.
- **Layer:** kontrollierte Alias-Tabelle (`top/front copper→F.Cu`,
  `bottom→B.Cu`, …) **nur mit Qualifier** („copper"/„layer"), sonst
  false-positive auf das Alltagswort „top".
- **NICHT tun:** semantisches Netz-Mapping (`ground→GND`) und Ref-Aliasing →
  Fehlklicks.

## 4. Empfehlung & Reihenfolge

1. **Hebel 1** (Prompt-Erweiterung) — sofort, risikolos.
2. **Hebel 3** für die harten Alias-Klassen (Netz-`/`, Pin-Prosa, Layer mit
   Qualifier), mit Tests.
3. **Hebel 2** als Konventions-Notiz in CLAUDE.md / Tool-Docstrings.

Empfänger **nicht** semantisch raten lassen — die Auflösbarkeits-Garantie ist die
Stärke des Designs.

## 5. Mess-/Tuning-Hook

`_append_claude` gibt `rendered` zurück, `_link_counts` existiert
(`chat_dialog.py:292`). Damit Link-Yield pro Antwort loggen (z. B. „in Prosa N
Element-Erwähnungen, davon M verlinkt") und Prompt/Normalisierung empirisch
nachziehen.

## 6. Tests (`tests/test_plugin_board_links.py` erweitern)

- Netz-`/`-Strip + Case-Insensitivität (Treffer/kein Über-Treffer).
- Pin-Prosa `pin 33 of U1` / `U1 pin 33` → `("pin",("U1","33"))`; unbekannte Ref
  → kein Link.
- Layer-Alias mit Qualifier (`top copper`→`F.Cu`) vs. bares „top" in Prosa →
  KEIN Link (False-Positive-Guard).
- Regressions: bestehende exakte Refs/Netze/Layer/Koords weiter wie bisher;
  Zero-False-Positive-Invariante bleibt.

## 7. Offene Entscheidung

Umfang des ersten Wurfs: (a) nur Hebel 1 (Prompt), oder (b) Hebel 1 + 3
(Prompt + sichere `tokenize`-Normalisierung mit Tests). Default-Empfehlung: (b),
da die Prompt-Regel allein probabilistisch bleibt und die Alias-Klassen die
häufigsten Drift-Fälle deterministisch abfangen.
