# Versionsübersicht — Claude für KiCad (Plugin)

Was jede Version gebracht hat, in einfacher Sprache. Neueste zuerst.
Aktuelle Version: **0.7.8**

---

## 🩹 Neu in 0.7.8 — „Kein eindeutiges Board" nach Folge-Abfragen behoben

- **Der Bug:** Nach mehreren Abfragen hintereinander starben plötzlich alle
  Klick-Links der früheren Antworten und unten stand „Kein eindeutiges Board"
  — obwohl das Board offen war. **Ursache:** War KiCads API während schneller
  Folge-Abfragen kurz beschäftigt, hielt der Tool-Server das für „kein Editor
  offen" und startete unsichtbar einen ZWEITEN Editor. Zwei Instanzen auf dem
  API-Bus → jede Board-Abfrage mehrdeutig → alle Links tot.
- **Dreifach behoben:** (1) Der Server wartet bei so einem kurzen Aussetzer
  jetzt mit Backoff, statt sofort zu starten. (2) Läuft der Chat im
  KiCad-Plugin, ist das Auto-Starten eines Editors komplett gesperrt
  (`KICAD_MCP_NO_AUTO_OPEN=1`, setzt das Plugin automatisch). (3) Taucht die
  Geister-Instanz doch auf, **heilen sich die Links selbst**: Beim nächsten
  Klick wird der vom Server gestartete Editor erkannt, beendet und die
  Verbindung neu aufgebaut.
- **Warum die alte Geister-Abwehr (0.2.20) das nicht fing:** Sie kannte nur
  Editoren, die in der Prozess-Registry standen — und ausgerechnet der
  Auto-Start-Pfad hat seine nie eingetragen. Jetzt trägt JEDER vom Server
  gestartete Editor sich ein (ein Wächter-Test erzwingt das), womit
  Aufräumen beim Schließen UND die neue Selbstheilung ihn finden.

---

## 🔌 Neu in 0.7.7 — Schutzklassen (34. Super-Feature, Normwerte als Werkzeug)

- **🔌 Schutzklassen ist live** (Werkzeug Nr. 186, `get_safety_spacing`): Prüft
  das Isolationskonzept deines Geräts — Schutzklasse I/II/III nach IEC 61140
  bestimmen, dann je Spannungsgrenze die **geforderten Kriech- und
  Luftstrecken** nachschlagen: Netz-Nennspannung + Überspannungskategorie →
  Stoßspannung → Luftstrecke; Arbeitsspannung + Verschmutzungsgrad +
  Materialgruppe (FR-4 = IIIa) → Kriechstrecke; Klasse II automatisch mit
  verstärkten Werten (Kriechweg ×2, Stoßspannungs-Stufe höher).
- **Die Normzahlen stecken als datierter Snapshot im Werkzeug** (IEC-60664-1-
  Tabellen F.1/F.2/F.4, gegen publizierte Normauszüge quergeprüft) — nicht im
  Modellgedächtnis. Beispiel 230-V-Netz, OVC II, FR-4: Basis 1,5 mm Luft /
  2,5 mm Kriechweg; Klasse II verstärkt 2,0 / 5,0 mm.
- Auch der ⚡ Sicherheitsabstände-Button nutzt jetzt diese Snapshot-Werte statt
  Gedächtnis-Richtwerte. Ehrlich bleibt: Ingenieurs-Vorprüfung, keine
  Zertifizierung — Produktnormen (62368-1, 60335-1, 60601-1) können abweichen.

---

## 📈 Neu in 0.7.6 — echte SPICE-Simulation + Geister-Vorschau beim Entwirren

- **📈 Simulation rechnet jetzt wirklich** (Werkzeug Nr. 185, `run_spice_sim`):
  Claude baut aus deinem Schaltplan ein SPICE-Deck und führt es mit **ngspice**
  aus (gefunden über PATH, KiCad-Ordner oder `KICAD_MCP_NGSPICE`) — echte
  Arbeitspunkte, Verstärkungen, Eckfrequenzen statt nur Abschätzung. Ist
  ngspice nicht installiert, sagt der Button das ehrlich und liefert die
  analytische Analyse plus das fertige Deck zum Kopieren.
- **🧶 Entwirren zeigt eine Geister-Vorschau:** Vor deinem Go erscheinen die
  geplanten Zielpositionen als Kreuz-Marker mit Bauteilnamen auf dem
  Skizzen-Layer („MCP.Skizze") — du siehst die neue Anordnung direkt auf dem
  Board. Nach Umsetzung oder Ablehnung wird die Vorschau automatisch
  weggeräumt.

---

## ✨ Neu in 0.7.5 — ALLE 33 Super-Features aktiv

Die komplette Leiste ist jetzt orange: jeder Button startet einen fertigen,
geführten Auftrag. Neu dazugekommen (v1):

- **🔀 Pin-Tausch** (Vorschläge; Umsetzung nur nach Go), **🧭 Netz-Navigator**,
  **📐 Ausrichten & Anordnen** (Plan → Go → ein Zug), **👁️ Mitdenken-Review**
  (bewertet deine letzten Handänderungen), **🔤 Silk-Aufräumen**.
- **⌚ Quarz-Load-Caps**, **🌡️ Thermik**, **🌡️ Betriebstemperatur**,
  **📐 Slew-Rate**, **〰️ Impedanz**, **📉 MLCC-Derating** — Physik mit
  offengelegten Annahmen; Datenblatt-Werte aus `docs/` oder per Nachfrage.
- **🏭 DFM-Check**, **💰 Kosten-Schätzer**, **⚡ Sicherheitsabstände**
  (IEC-62368-Vorprüfung), **💾 Firmware-Pinmap** (C-Header/DeviceTree/ESPHome
  zum Kopieren).
- **📈 Simulation** (analytisch + SPICE-Deck zum Kopieren — ehrlich: noch keine
  numerische SPICE-Ausführung), **🧬 SPICE-Modelle** und **🛒 Bauteil-Sourcing**
  (beide mit Web-Suche), **📄 Datenblatt→Schaltung** (PDF → Schaltungsblock,
  Einbau nach Go), **📷 Foto→Schaltung** (Foto ins Projekt legen, Pfad nennen).

Überall gilt: Mutationen nur nach deinem **Go**, Annahmen werden offengelegt,
bei Unsicherheit wird gefragt statt geraten, jede v1-Grenze steht im Bericht
(Details: `docs/superfeatures.md`, Abschnitt „Stand 0.7.5").

---

## 🔥 Neu in 0.7.4 — Stromtragfähigkeit (13. Super-Feature, neues Werkzeug)

- **🔥 Stromtragfähigkeit ist live** — das erste Feature mit einem eigens dafür
  gebauten Rechen-Werkzeug (`check_ampacity`, Werkzeug Nr. 184): Es prüft jede
  Leiterbahn-Breite gegen den Strom, den ihr Netz tragen soll (IPC-2221,
  einstellbarer Temperaturanstieg und Kupferdicke, Innenlagen strenger).
  Der Klick liefert erst das Breiten-Inventar, dann legt Claude seine
  Strom-Annahmen offen (und fragt bei Unsicherheit nach), dann kommt der
  Prüfbericht: welches Segment auf welchem Layer zu schmal ist und wie breit
  es sein müsste. Ehrliche Grenze: bewertet Leiterbahnen, noch keine
  Kupferflächen.

---

## 📄 Neu in 0.7.3 — vier weitere Super-Features aktiv (jetzt 12)

- **📄 Datenblatt-Abgleich** — IC markieren, klicken: die Beschaltung wird gegen
  das Datenblatt (`docs/<Value>.pdf` im Projekt) geprüft — Entkopplung,
  Pin-Beschaltung, fehlende externe Bauteile. Ohne Auswahl zeigt es erst,
  welche Datenblätter vorliegen und welche fehlen (mit Download-URL).
- **💡 Board erklären** — rekonstruiert Funktionsblöcke, Schnittstellen und
  Stromversorgung aus Netzliste + Bauteilen; mit Auswahl gezielt den
  markierten Teilschaltkreis.
- **⊙ Polar-Board** — zeigt die Polar-Grid-Parameter (Zentrum, Ringe, Speichen)
  und den Radial-Workflow für runde Boards; platziert wird erst auf dein Go.
- **🖊️ Skizzen-Layer** — zeigt, was auf dem gemeinsamen Skizzen-Layer (User.9)
  liegt, und bietet Legende zeichnen oder Leeren an (erst nach deinem Go).

---

## 🧶 Neu in 0.7.2 — Entwirren aktiv, klare Auswahl-Regel, Optionen-Dropdown

- **🧶 Entwirren ist live** (das achte aktive Super-Feature): Ein Klick liest das
  Board einmal, entwirrt die Bauteil-Platzierung **im Kopf** (geprüft am
  Kreuzungs-Scorer, ohne das Board anzufassen), zeigt dir den Plan mit
  Score vorher → nachher — und ordnet erst nach deinem **Go** alles in einem
  Zug an.
- **Eine Auswahl-Regel für alle Super-Features:** Nichts markiert → das Feature
  wirkt aufs **ganze Board**. Etwas markiert → das Panel zeigt beim Klick an,
  worauf der Zug wirkt („🎯 Wirkt auf deine Auswahl: R1, C3, U2") und das
  Feature beschränkt sich genau darauf. Der separate Button „Auswahl
  entwirren" ist damit überflüssig und entfällt.
- **Aktive Features sind jetzt ORANGE beschriftet** — auf einen Blick sichtbar,
  was echt klickbar ist; „kommt bald" bleibt gedimmt.
- **Claude-Optionen mit Dropdown:** Neben dem Freitextfeld gibt es jetzt eine
  Auswahl sinnvoller Schalter (Modell Sonnet/Opus/Haiku, Fast-Modus,
  Fallback-Modell). Die Liste wird **dynamisch** aus `claude --help` deiner
  installierten Version gefiltert — es wird nie ein Schalter angeboten, den
  deine CLI nicht kennt. Auswahl ersetzt einen vorhandenen Schalter gleichen
  Namens statt ihn zu doppeln.

---

## ✨ Neu in 0.7.1 — die ersten 7 Super-Features sind AKTIV

Die Super-Feature-Buttons unter dem Chat waren bisher alle „kommt bald". Jetzt
sind die sieben, deren Werkzeuge längst an Bord sind, **echt verdrahtet**: ein
Klick startet den fertigen Auftrag im Chat — mit deiner aktuellen KiCad-Auswahl
als Kontext (markierst du vorher Bauteile, wirkt das Feature nur darauf):

- **🛡️ Design-Wächter** — semantische Prüfung jenseits des ERC (Pull-ups,
  Load-Caps, Entkopplung).
- **🚌 Bus-Radar** — alle Teilnehmer + Pins je Bus (I²C, SPI, UART …).
- **🔎 Test-Punkt-Wächter** — kritische Netze ohne Prüfpunkt-Zugang, Abdeckung in %.
- **💰 BOM-Konsolidierung** — fast-gleiche R/C-Werte auf E-Reihen-Standard (nur Vorschlag).
- **🏭 Fab-Standardteile** — No-Load-Fee-Vorzugsteile (JLCPCB Basic …) + Ersparnis.
- **🔩 Via-Optimierung** — Report, welche Blind/Buried-Vias zu Through wandelbar
  sind; umgesetzt wird erst auf dein Go.
- **✏️ Skizzen-Dirigent** — deine Linien/Bögen auf User.9 werden per Klick zu
  Kupferbahnen auf F.Cu (ein Undo-Schritt; leerer Layer → ehrliche Meldung).

Die übrigen Buttons zeigen weiterhin ihren „kommt bald"-Pitch.

---

## 🔥 Neu in 0.7.0 — Warmer Tool-Server (opt-in)

- **Der Tool-Server kann jetzt dauerhaft warm laufen.** Bisher startet Claude
  den kicad-mcp-Server bei **jeder** Chat-Nachricht neu (stdio) — der Kaltstart
  ist genau die Quelle des „MCP nicht verbunden"-Wacklers. Neu: mit
  `KICAD_MCP_TRANSPORT=http` startet das Plugin den Server **einmal pro
  KiCad-Sitzung** als lokalen HTTP-Dienst (nur `127.0.0.1`, mit zufälligem
  Zugriffs-Token) und Claude **verbindet sich nur noch** — kein Spawn, kein
  Kaltstart pro Nachricht.
- **Selbstheilend:** Vor jedem Chat-Zug wird die Gesundheit geprüft; ein
  abgestürzter/hängender Server wird automatisch ersetzt, Waisen nach einem
  KiCad-Absturz beim nächsten Start aufgeräumt, und beim Schließen von KiCad
  wird der Server sauber beendet. Klappt der Warm-Start nicht, läuft der Zug
  automatisch im bisherigen stdio-Modus weiter.
- **Diagnose zeigt den Server-Status** (läuft? PID, Port, Uptime, echter
  MCP-Ping) — die Info, die bisher fehlte.
- **Standard bleibt vorerst `stdio`** (das bisherige Verhalten, unverändert).
  Nach Validierung auf echten Windows-Setups wird der Warm-Modus der Standard;
  Rückweg ist immer ein Env-Wort: `KICAD_MCP_TRANSPORT=stdio`.

---

## 🩹 Neu in 0.6.1 — MCP-Kaltstart heilt sich selbst

- **„MCP nicht verbunden" verschwindet meistens von allein.** Der Tool-Server
  wird pro Nachricht frisch gestartet; der **erste** Start nach Update/Neustart
  ist langsam (Windows Defender scannt jede neue `.pyd`) und lief manchmal in
  einen stillen Fehlschlag — Antwort dann **ohne** Board-Werkzeuge. Neu: schlägt
  der Start fehl, wird der Zug **automatisch einmal neu gestartet** (der zweite
  Start ist warm und klappt fast immer). Sicher, weil ein Zug ohne Tool-Server
  am Board nichts geändert hat. Abschaltbar via `KICAD_MCP_CONNECT_RETRIES=0`.

---

## 🆕 Neu in 0.6.0 — Super-Features (Bedeutung statt nur Geometrie)

Neue Werkzeuge, die Claude im Chat nutzen kann — Dinge, die KiCad selbst nie
könnte:

- **🛡️ Design-Wächter** (`audit_design`) — findet stille Fehler jenseits des ERC:
  I²C-Bus ohne Pull-ups, Quarz ohne Load-Caps, IC-Versorgungspin ohne (oder mit
  zu weit entfernter) Entkopplung, Reset ohne Pull-up.
- **🚌 Bus-Radar** (`list_bus_members`) — listet alle Teilnehmer + Pins eines
  Busses (I²C, SPI, UART …) als eine Bedeutungseinheit.
- **💰 BOM-Konsolidierung** (`consolidate_bom`) — legt fast-gleiche R/C-Werte auf
  E-Reihen-Standardwerte zusammen → weniger Feeder, günstigere Bestückung.
- **🏭 Fab-Standardteile** (`suggest_preferred_parts`) — mappt R/C aufs
  No-Load-Fee-Teil des Fertigers (JLCPCB Basic …) und schätzt die Ersparnis.
- **🔎 Test-Punkt-Wächter** (`audit_test_points`) — meldet kritische Netze
  (Versorgung, Reset, Clock, Bus) ohne Prüfpunkt-/Stecker-Zugang.

---

## Auf einen Blick — die wichtigsten Bausteine

| Bereich | Was es kann |
|---|---|
| 💬 **Chat im PCB-Editor** | Frag Claude in normaler Sprache über dein offenes Board; Antworten im KiCad-Look, andockbar |
| 🔗 **Klickbare Links** | Bauteile, Netze, Pins, Layer und Koordinaten in der Antwort anklicken → KiCad markiert & zoomt hin |
| ⚙️ **Live am Board** | Claude liest und ändert das offene Board direkt (über die kicad-mcp-Werkzeuge) |
| 🛡️ **Sicher & gemeinsam** | Claude pfuscht nicht an Dateien, beide Seiten können speichern, Prozesse räumen sauber auf |
| 🩺 **Selbst-Diagnose** | Einrichtungs-Check + „Diagnose"-Knopf finden Fehler und zeigen den echten Grund |

---

## 🔗 Klickbare Elemente im Chat (Cross-Probe)

- **0.2.26** — Links wieder zuverlässig, auch während Claude aktiv ist (Verbindungs-Konkurrenz mit dem Server abgefangen).
- **0.2.23** — **Bauteil-Pins** klickbar: `U1B.33` markiert Pin 33 von Bauteil U1B.
- **0.2.21** — **Layer-Namen** klickbar: Klick setzt den aktiven Layer in KiCad.
- **0.2.18** — **Koordinaten** klickbar: springt zum nächstgelegenen Element an dieser Stelle.
- **0.2.17** — Start: **Bauteile & Netze** in der Antwort werden Links → markieren + hinzoomen. Löst „ich finde das Teil auf der großen Platine nicht".

## 💬 Aussehen & Bedienung

- **0.2.22** — **Stopp-Knopf** (laufende Antwort abbrechen), **Optionen-Feld** für Claude-Schalter (z. B. `--model sonnet`), **Tool-Aufrufe** erscheinen live im Verlauf.
- **0.2.16** — **Live-Mitschrift** der Antwort + Abbruch nur noch bei echtem Stillstand statt nach starren 5 Minuten.
- **0.2.7** — Chat-Panel **dockt** wie ein echtes KiCad-Fenster an (anheften, abreißen, verschieben).
- **0.2.6** — Chat im **Claude-Code-Look** (dunkles Terminal, Monospace, Orange, Spinner). Kein schwarzes Konsolenfenster mehr.

## 🛡️ Sicherheit & gemeinsames Arbeiten

- **0.2.24** — Claude darf **keine Shell/Dateien** mehr anfassen (Sperre repariert + Windows-PowerShell ergänzt), Verhaltensregeln werden mitgegeben, **Schritt-Limit** gegen Endlosläufe.
- **0.2.20** — Claude- und Server-Prozesse werden beim **Schließen von KiCad sauber beendet** (kein Zombie).
- **0.2.19** — **Disk-Schutz**: solange das Board in KiCad offen ist, blockiert der Server Direkt-Datei-Änderungen (sonst Speicher-Konflikt). Mutationen laufen live über IPC — beide Seiten speichern stimmig.
- **0.2.11** — Claude bekommt **Datei-Schreibverbot** (Mutationen nur über die Board-Werkzeuge), und der Server-Start wird mit einem echten Handshake geprüft.
- **0.2.9** — Abhängigkeiten landen im **plugin-eigenen Ordner**; bloßes Reden mit dem Server markiert das Board **nicht mehr als geändert**.

## 🩺 Einrichtung, Diagnose & Stabilität

- **0.2.25** — **„MCP nicht verbunden"-Ursache gefunden**: Kaltstart-Timeout (Windows-Defender scannt die frischen Dateien). Timeout großzügig erhöht, Probe misst jetzt die echte Zeit.
- **0.2.15** — **Kern-Fix**: KiCads Python ignorierte eine Pfad-Variable → Server-Start umgestellt, läuft seitdem.
- **0.2.14** — **„Diagnose"-Knopf**: sammelt alle Pfade/Versionen/den echten Fehler in einen kopierbaren Report.
- **0.2.10–0.2.13** — Abhängigkeits-Installation selbst-diagnostizierend; präzise Meldung bei unvollständiger Installation; Zielordner sichtbar.
- **0.2.8** — Erkennt einen **nicht startenden Server**, statt still ohne Werkzeuge weiterzumachen.
- **0.2.0–0.2.5** — Grundgerüst: Plugin + gebündelter Server, Einrichtungs-Check, robuste KiCad-Python-Erkennung, Selbst-Update aus dem Repo.

---

## ⚙️ Server & Werkzeuge (kicad-mcp, ohne eigene Plugin-Nummer)

- **Ein-Klick-Installskripte** (`install_plugin.bat` / `.sh`) — Plugin direkt aus dem Repo installieren.
- **Agent-Regeln** in `CLAUDE.md` gegen „Toolcall-Explosion" (nicht nach jeder Mini-Änderung rendern, bündeln, bei Stillstand abbrechen).
- **`add_vias_to_pcb`** — viele Vias in einem Rutsch setzen (statt N Einzel-Calls).
- **`ipc_markup_to_tracks`** — von Hand gezeichnete Linien auf der Markup-Ebene (User.9) in Kupfer-Leiterbahnen umwandeln.
- **`ipc_get_selection`** — „was ist gerade in KiCad ausgewählt?" ins Gespräch holen.
- **Zentraler Verbindungs-Layer** — eine wiederverwendete, robuste IPC-Verbindung (schneller, Timeout konfigurierbar, Retry bei „KiCad busy", Datei-Log).

---

*Vollständige technische Details: siehe `CHANGELOG.md`.*
