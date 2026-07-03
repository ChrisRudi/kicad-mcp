# Versionsübersicht — Claude für KiCad (Plugin)

Was jede Version gebracht hat, in einfacher Sprache. Neueste zuerst.
Aktuelle Version: **0.6.0**

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
