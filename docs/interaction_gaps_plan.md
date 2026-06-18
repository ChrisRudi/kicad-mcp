<!-- Plan/Spezifikation. STATUS: freigegeben, noch NICHT implementiert. -->
<!-- Erstellt 2026-06-18 aus der Lücken-Analyse der Interaktions-Oberfläche. -->

# Plan: Interaktions-Lücken schließen (User-Sicht zuerst)

**Status:** freigegeben, **noch nicht implementiert**. Vorlage für spätere
Implementierungs-Aufträge. Betrifft v. a. das KiCad-Plugin-Chat-Panel
(`plugin/chat_dialog.py`, `plugin/board_links.py`); nutzt vorhandene MCP-Tools.
Self-contained.

## 0. Leitprinzip — die Nutzer-Sicht

Der Panel-Nutzer ist Elektroniker vor seinem **realen, offenen Board**, nicht
Entwickler. Er denkt in „dieses Bauteil hier", „dieses Netz", „warum hängt das
nicht zusammen" — nicht in Tool-Namen oder Refs, die er erst abtippen muss.
Maßstab jeder Lücke: **Spart sie dem Nutzer einen Medienbruch** (Editor ↔ Chat ↔
Tippen)? Heute ist die Brücke einseitig: der Chat kann ins Board zeigen, aber das
Board kann nicht in den Chat „sprechen". Genau dort liegt der größte gefühlte
Mangel.

## 1. Ist-Stand

- **Forward verdrahtet:** Klick auf Link im Chat → select+zoom für
  Ref/Netz/Layer/Pin/Koordinate (`chat_dialog.py:438 _select_worker`,
  `board_links.py` select*/set_active_layer).
- **Reverse gar nicht:** Panel liest die Editor-Selektion nie, obwohl das Tool
  existiert (`ipc_interact_tools.py:505 ipc_get_selection`).
- Reiche, aber nur agent-seitige Tools: `ipc_inspect_item` (`:546`),
  `ipc_draw_markers` (`:720`), `ipc_select_items` (`:608`),
  `ipc_markup_to_tracks` (`ipc_markup_tools.py:39`).

## 2. Priorisierte Lücken (Hebel ÷ Aufwand, je aus Nutzer-Sicht)

### P1 — „Markier es im Editor, dann frag mich dazu" (Reverse-Selektion)
**User-Story:** Ich klicke im PCB-Editor auf ein Bauteil/Netz und frage im Panel
„*was ist das?*", „*warum ist das hier?*", „*ist das mit GND verbunden?*" —
**ohne** die Referenz abzutippen.
**Heute:** unmöglich im Panel; der Nutzer muss die Ref selbst kennen und tippen.
**Ansatz:** Button/Hinweis „🔗 Auswahl einbeziehen". Beim Senden `ipc_get_selection`
(`:505`) lesen, die Selektion als kompakten Kontext dem Prompt voranstellen
(„Nutzer hat selektiert: U3, Netz GND-Pad U3.4 …"). Polling beim Klick auf Senden,
kein Push nötig.
**Aufwand:** klein (Tool existiert). **Hebel:** sehr hoch — kehrt die Einbahn um.
**Risiko:** Selektion leer/Mehrdeutig → Hinweis statt Fehler.

### P2 — „Zeig mir mehr als nur hinspringen" (Aktionen pro Link)
**User-Story:** Ich klicke auf `GND` und will wählen: *nur highlighten* / *hinzoomen*
/ *andere ausblenden* / *Eigenschaften zeigen* — nicht immer dieselbe Aktion.
**Heute:** ein Klick = eine feste Aktion (select+zoom, `_select_worker:458`).
**Ansatz:** Rechtsklick auf einen Link → kleines Kontextmenü; Einträge mappen auf
vorhandene Tools/kipy-Calls (highlight = `select` ohne clear, Eigenschaften =
`ipc_inspect_item`). 
**Aufwand:** mittel (wx-Kontextmenü + Dispatch-Erweiterung). **Hebel:** mittel-hoch.

### P3 — „Sag mir, was es ist, ohne zu tippen" (Klick → Inspect zurück)
**User-Story:** Ein Klick auf `U1.33` zeigt mir **direkt im Panel** Pad-Netz, Typ,
Lage — nicht nur Sprung im Editor.
**Heute:** Klick zoomt nur; `ipc_inspect_item` (`:546`) bleibt agent-only.
**Ansatz:** nach select zusätzlich `ipc_inspect_item` aufrufen, Ergebnis in die
Status-/Antwortzeile schreiben (`_flash_status` existiert, `:465`).
**Aufwand:** klein. **Hebel:** mittel — beantwortet die häufigste Rückfrage sofort.

### P4 — „Zeig's mir auf dem Board" (Marker-Affordanz)
**User-Story:** Claude schreibt „die 3 kleinsten GND-Vias sind …" — ich will einen
Klick „**auf dem Board markieren**", der sie sichtbar anzeichnet.
**Heute:** nur Agent kann `ipc_draw_markers` (`:720`); die Antwort bietet keine
Affordanz.
**Ansatz:** wenn eine Antwort mehrere Board-Elemente nennt, eine Zeile
„📍 alle markieren" rendern → `ipc_draw_markers`/`select_items` auf die im Text
erkannten Targets.
**Aufwand:** mittel. **Hebel:** mittel (stark bei „finde/zeige"-Fragen).

### P5 — „Sammle meine Klicks" (Mehrfach-Selektion)
**User-Story:** Ich klicke nacheinander `R1`, `R2`, `C5` und will sie **zusammen**
selektiert haben (z. B. zum Verschieben).
**Heute:** jeder Klick löscht die vorige Auswahl (`board_links.py:408/439`).
**Ansatz:** Modifier-Klick (Strg) = `add_to_selection` ohne `clear_selection`.
**Aufwand:** klein. **Hebel:** niedrig-mittel.

## 3. Erwartungs-Management: was bewusst NICHT geht

Damit der Nutzer nicht auf Unmögliches wartet (CLAUDE.md / KiCad 10):
- **Hover/„worauf zeige ich gerade"** — KiCad exponiert Maus/Statusleiste nicht;
  Ersatz ist die GUI-Selektion (= P1).
- **Schaltplan-Live-Links (Eeschema)** — keine Schematic-IPC-API.
- **3D-Viewer steuern** — keine API.
Diese gehören als kurzer „nicht möglich"-Hinweis in die Interaktionsanleitung
(`docs/startup_panel_plan.md`), damit die Grenze sichtbar ist.

## 4. Tests / Konventionen

- Reverse-Selektion: reine Funktion „Selektion → Kontext-String" headless testbar
  (gemockte `ipc_get_selection`-Antwort), leere Selektion → leerer Kontext.
- Kontextmenü/Inspect/Marker: Dispatch über injizierbare Hooks testen (kein echtes
  KiCad), wie schon bei den board_links-Tests (`tests/test_plugin_board_links.py`).
- wx-Rendering dünn halten; Logik in reine Funktionen.
- Plugin-Code, **kein** neues MCP-Tool nötig (alle Bausteine existieren).

## 5. Offene Entscheidung

Reihenfolge der Umsetzung. Empfehlung aus Nutzer-Sicht: **P1 zuerst** (kehrt die
Einbahnstraße um, kleinster Aufwand, größter „endlich"-Effekt), dann P3
(Inspect-zurück) als billiger Begleiter, danach P2/P4. P5 optional.
