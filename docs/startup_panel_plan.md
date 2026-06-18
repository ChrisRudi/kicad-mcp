<!-- Plan/Spezifikation. STATUS: freigegeben, noch NICHT implementiert. -->
<!-- Erstellt 2026-06-18 aus der Diskussion "Panel-Start: Zusammenfassung, Anleitung, Version, Empfehlung". -->

# Plan: Panel-Start — Platinen-Zusammenfassung, Interaktionsanleitung, Version, Empfehlungs-Mailto

**Status:** freigegeben, **noch nicht implementiert**. Vorlage für den späteren
Implementierungs-Auftrag. Betrifft das KiCad-Plugin-Chat-Panel
(`plugin/chat_dialog.py`), **kein** MCP-Tool. Self-contained.

## 0. Ziel

Beim Öffnen des Chat-Panels sofort (ohne Claude-Turn) zeigen:
1. **Banner** mit Versions-Anzeige + klickbarer **Empfehlungs-Mailto** (Plugin an
   Freunde weiterempfehlen).
2. **Platinen-Zusammenfassung** (Zählwerte des offenen Boards).
3. **Interaktionsanleitung** (wie man mit Panel + Links arbeitet).

Ersetzt den heutigen statischen Einzeiler-Banner (`chat_dialog.py:139-144`).

## 1. Ist-Stand (Belege)

- Panel-Init `ChatPanel.__init__` (`chat_dialog.py:40`); statischer Banner
  (`:139-144`); Eingabefokus zuletzt (`:145`).
- Board-Vokabular wird heute erst NACH dem ersten Reply geladen
  (`_worker` → `board_targets` / `board_targets_from_file`, `:283-307`);
  `self._refs/_nets/_layers` (`:55-57`).
- Klickbare Links: Char-Range→Target in `self._links` (`:58`), Klick-Dispatch
  `_on_output_click` (selektiert Board-Element / setzt Layer).
- Version: `from .version import __version__` (`chat_dialog.py:24`), bereits im
  Fenstertitel (`:475`).
- Vokabular-Quelle: `board_links.board_targets()` live (`board_links.py:249`) /
  `board_targets_from_file()` Disk (`:293`); `BoardUnavailable`/Diagnose-Zeile
  „ⓘ Links aus: …" (`chat_dialog.py:381`).

## 2. Mock (kompletter Panel-Start)

```
kicad-mcp  v0.3.5  ·  verbunden mit motor_driver.kicad_pcb

Gefällt dir das Plugin? → Empfiehl es einem Freund   ✉      (klickbar, mailto)

Platine
  Footprints   42       Netze   28       Lagen   4 (F.Cu, In1.Cu, In2.Cu, B.Cu)
  Bestückung   U:3  R:18  C:14  D:4  J:3
  Größe        58.0 × 42.0 mm        (ⓘ aus Edge.Cuts, best effort)

So arbeitest du mit mir
  • Orange unterstrichene Namen sind klickbar: R12, GND, F.Cu, U1.33,
    (120.5, 84.0) → wählt + zoomt das Element im PCB-Editor.
  • Beispiele:  „wie viele GND-Vias?"  ·  „markier die 3 kleinsten Cs"
  • ⚑ unten = Claude-Optionen (z. B. --model sonnet) · „Stopp" bricht ab.

❯ Frag Claude etwas über dieses Board …
```

## 3. Bausteine

### 3.1 Versions-Anzeige
`v{__version__}` (schon importiert, `:24`) als dimmer Text in der ersten
Banner-Zeile neben dem Board-Dateinamen. Kein neuer State.

### 3.2 Empfehlungs-Mailto (schön formuliert, klickbar)
- Reine Funktion `recommend_mailto() -> str` (neu, in `board_links.py` oder einem
  kleinen `plugin/banner.py`), baut den `mailto:`-String, Felder URL-encodiert
  via `urllib.parse.quote`. Kein Empfänger → Nutzer trägt den Freund ein.
- **Subject:** „KiCad + Claude — das solltest du kennen"
- **Body:** „Hi! Ich nutze kicad-mcp — damit redet man im KiCad-PCB-Editor direkt
  mit Claude: Bauteile/Netze finden, markieren, Routing prüfen, ganze Blöcke aus
  Datenblättern bauen. Klickbare Links springen direkt aufs Element im Editor.
  Open Source (GPL-3.0): https://github.com/ChrisRudi/kicad-mcp — viel Spaß!"
- Ergebnis: `mailto:?subject=…&body=…`.

### 3.3 Platinen-Zusammenfassung
- Reine Funktion `board_summary(refs, nets, layers) -> dict` (neu, in
  `board_links.py`), headless unit-testbar:
  `{footprints:int, nets:int, layers:[str], by_prefix:{"U":3,"R":18,…}}`.
  Bestückung = refs nach Prefix `^[A-Za-z]+` gruppiert + gezählt.
- Datenquelle: `board_targets()` live, sonst `board_targets_from_file()`
  (`board_links.py:249/293`).
- **Board-Größe (best effort):** aus `Edge.Cuts`-Bbox (kipy-Bounding-Box oder
  `.kicad_pcb`-Text). Nicht ermittelbar → Zeile weglassen, kein Fehler.

### 3.4 Interaktionsanleitung
Statischer Text (s. Mock): Link-Klick erklärt, 2 Beispiel-Prompts, ⚑-Feld,
Stopp-Button.

## 4. Implementierung (Plugin-Code)

- **Klick-Dispatch erweitern:** neue Target-Art `("url", "<href>")` zusätzlich zu
  board-Targets; im Klick-Handler `webbrowser.open(href)` (öffnet auch `mailto:`
  über das OS). Board-Targets unverändert.
- **Render-Reihenfolge beim Start:**
  1. Banner-Zeile (Version + Dateiname) — synchron, instant.
  2. Empfehlungs-Mailto-Span — synchron, instant.
  3. Interaktionsanleitung — synchron, instant.
  4. Platinen-Zusammenfassung — **asynchron** im Hintergrund-Thread (wie der
     Link-Refresh `:283-307`), Ergebnis per `wx.CallAfter` nachgerendert →
     UI blockiert nie, auch bei kurz „busy"er Live-IPC.
- **Nebeneffekt nutzen:** die beim Summary geladenen refs/nets/layers gleich in
  `self._refs/_nets/_layers` setzen → schon die ERSTE Antwort ist verlinkbar
  (heute erst ab der zweiten).
- **Graceful degrade:** `BoardUnavailable`/leeres Set → Summary-Block weglassen,
  Banner + Anleitung trotzdem zeigen, plus bestehende „ⓘ Links aus: …"-Diagnose.

## 5. Abgrenzung / Entscheidungen

- **Kein Claude-Call beim Start** — rein deskriptiv (Zählwerte), keine Analyse;
  sonst Latenz/Kosten bei jedem Panel-Open.
- **Mailto ist der einzige neue externe Klick** — nur OS-`mailto:`-Handoff, kein
  Netzcall, kein Tracking.
- Board-Größe gleich mit rein (best effort) — Default ja.

## 6. Tests

- `recommend_mailto`: enthält `mailto:`, korrekt URL-encodierte subject/body,
  GitHub-URL im Body.
- `board_summary`: Prefix-Gruppierung (U/R/C/D/J…), Zählwerte, leere Sets → Nullen.
- Klick-Dispatch: `("url",href)`-Target ruft den Browser-Open-Hook (injizierbar,
  kein echter Browser im Test); board-Targets weiterhin unverändert.
- (wx-Rendering bleibt dünn und ungetestet; Logik in den reinen Funktionen.)

## 7. Offene Punkte

- Genaue Mailto-Formulierung final abstimmen (Subject/Body oben als Vorschlag).
- Ablageort der reinen Funktionen: `board_links.py` (nah am Vokabular) vs. neues
  `plugin/banner.py`. Default-Vorschlag: `board_links.py` für `board_summary`
  (nutzt board_targets), `plugin/banner.py` für `recommend_mailto` + Banner-Text.
