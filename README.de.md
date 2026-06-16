# KiCad 10 MCP-Server

Ein umfassender [Model-Context-Protocol](https://modelcontextprotocol.io/)-(MCP-)Server für **KiCad 10**. Vereint Projektanalyse, Validierung, CLI-basierte Exporte und — als Alleinstellungsmerkmal — die **Erzeugung von Schaltplänen und Leiterplatten aus JSON-Spezifikationen**.

Funktioniert mit Claude Desktop, Claude Code und jedem anderen MCP-kompatiblen Client.

> Englische Originalfassung: [README.md](README.md). Bei abweichenden Tool-Listen ist die englische Datei die Quelle.

---

## Was diesen Server unterscheidet

Kein anderer KiCad-MCP-Server kann KiCad-Schaltpläne und -Leiterplatten aus einer strukturierten Spezifikation **erzeugen**. Die meisten Server analysieren nur bestehende Dateien — dieser baut sie auch.

Zusätzlich konvertiert er **ESPHome-YAML**-Konfigurationen direkt in komplette KiCad-Projekte: Sensoren, Busse, Pull-up-Widerstände, Entkoppelkondensatoren, alles automatisch verdrahtet.

**91 Tools** (plus `restart_mcp_child` aus dem optionalen Supervisor-Wrapper, siehe unten) verteilt auf dreizehn Kategorien: Analyse, Validierung, Export, Generierung, ESPHome-/LTspice-Konvertierung, Autorouting, Projektmanagement, Diagnose und fünf Headless-/Live-Editing-Schichten:

- **PCB-Text-Patcher** (F8-Äquivalent ohne GUI),
- **PCB-Geometrie + Routing**,
- **Intelligente Footprint-Suche** über die mitgelieferte KiCad-Bibliothek,
- **IPC-API-Bridge** zur laufenden KiCad-GUI (Routing, Zonen, Footprint-Pose, Save/Revert/DRC, Schaltplan-Job-Exporte),
- **Schaltplan-Patch-Schicht** für inkrementelles Editieren bestehender `.kicad_sch`-Dateien (Bauteile, Drähte, Labels mit chip-bewussten Outward-Stubs + Justify, Pin-zu-Pin-Verbindungen mit BBox- und Label-Kollisionsvermeidung, Gruppen-Transformationen, freie Rotation, Region-/Typ-basiertes Löschen, sowie ein reiner Python-Annotator).

---

## Credits & Herkunft

Dieses Projekt baut auf der Arbeit anderer auf und erweitert sie deutlich:

### [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) — Fundament

Die Grundarchitektur des Servers — FastMCP-Integration, asynchrone Tool-Registrierung, Projekt-Discovery, DRC mit History-Tracking, BOM-Analyse, Netlistenextraktion, Mustererkennung und Sicherheitslayer (Pfad-Validierung, sicherer Subprocess-Runner). Ursprünglich Upstream unter MIT; dieser Fork als Ganzes steht unter GPL-3.0-or-later (siehe [Lizenz](#lizenz)).

**Übernommene Tools:** `list_projects`, `open_project`, `get_project_structure`, `validate_project`, `run_drc_check`, `get_drc_history_tool`, `analyze_bom`, `export_bom_csv`, `extract_project_netlist`, `extract_schematic_netlist`, `find_component_connections`, `analyze_schematic_connections`, `identify_circuit_patterns`, `analyze_project_circuit_patterns`, `generate_pcb_thumbnail`, `generate_project_thumbnail`.

### [Seeed-Studio/kicad-mcp](https://github.com/nicholasgasior/kicad-mcp) — Inspiration

Lieferte das konzeptionelle Modell für ERC-Integration, Schaltplan-/PCB-Analyse, Pin-Funktions-Inferenz und Hierarchie-Sheet-Behandlung. Die Implementierungen hier sind komplett neu geschrieben gegen unseren eigenen S-Expression-Parser statt Seeeds Parser-Abhängigkeiten.

**Übernommene Konzepte:** ERC via `kicad-cli`, Komponenten-Listing mit Filtern, Pin-Funktion-Analyse (I2C/SPI/UART/GPIO-Erkennung), Sub-Sheet-Erkennung mit Root-Schaltplan-Umleitung.

### Neu — speziell für dieses Projekt entwickelt

**S-Expression-Engine** (`kicad_mcp/utils/sexpr_parser.py`, `kicad_mcp/generators/sexpr.py`)
- Parser, der jede `.kicad_sch` / `.kicad_pcb` in einen Python-Baum einliest
- Builder, der einrückungs-bewusste, KiCad-kompatible S-Expressions mit deterministischen UUIDs schreibt

**Schaltplan- & PCB-Generierung** (`kicad_mcp/generators/`)
- `schematic_builder.py` — Erzeugt `.kicad_sch` aus Bauteile-/Netzlisten-JSON: `lib_symbols`, Symbol-Instanzen, globale/lokale Net-Labels
- `pcb_builder.py` — Erzeugt `.kicad_pcb`: Board-Outlines (Rechteck, Kreis, Eurokarte), Layer-Setup, Footprint-Platzierung mit Pad-Net-Zuweisung, GND-Kupferflächen, Befestigungslöcher, JLCPCB-Designregeln
- `validator.py` — Validiert Eingaben vor der Generierung: doppelte Refs, Pin-Typen, hängende Netze, Pin-Konflikte, Board-Constraints

**CLI-Export-Suite** (`kicad_mcp/tools/cli_export_tools.py`)
- 8 Export-Tools als Wrapper für `kicad-cli`: Gerber, Drill, STEP, PDF, SVG, POS, 3D-Render, Board-Stats
- Automatische WSL-Pfad-Umsetzung für Windows-`kicad-cli.exe`

**Analyse-Tools** (`kicad_mcp/tools/schematic_tools.py`, `pcb_tools.py`, `pin_tools.py`, `erc_tools.py`)
- Schaltplan-Analyse: Komponenten-Listing, Symbol-Details, Regex-Suche, Metadaten
- PCB-Analyse: Footprint-Listing, Net-Analyse mit Track-/Via-Zähler, Track-Suche pro Net
- Pin-Analyse: MCU-Familien-Erkennung (ESP32, STM32, ATmega, nRF52, RP2040, SAMD), Schnittstellen-Inferenz aus Net-Namen
- ERC: `kicad-cli`-Integration mit JSON-Output, Severity-Filter, Sub-Sheet-Behandlung


**ESPHome YAML → KiCad** (`kicad_mcp/generators/esphome_parser.py`, `kicad_mcp/tools/esphome_tools.py`)
- Parst ESPHome-YAML, extrahiert ESP-Chip, I2C/SPI/UART-Busse, Sensoren, Aktoren
- Komponenten-Datenbank: BME280, BME680, DHT22, DS18B20, SHT31, BH1750, VL53L0X, MPU6050, ADS1115, SSD1306, WS2812B
- Generiert automatisch Pull-ups für I2C, Entkoppelkondensatoren je IC, Power-Netze
- Unterstützt ESP32, ESP32-S3, ESP32-C3, ESP8266
- Vollständiger Round-trip: YAML rein → `.kicad_sch` + `.kicad_pcb` + `.kicad_pro` raus

**SPICE-Simulation** (`kicad_mcp/generators/spice_models.py`)
- Automatische Zuweisung von KiCad-10-Simulations-Properties (`Sim.Device`, `Sim.Params`, `Sim.Pins`)
- Deckt Passive (R, C, L, D, LED), BJTs, MOSFETs, Operationsverstärker, Spannungs-/Stromquellen
- ICs erhalten SUBCKT-Platzhalter mit Auto-Mapping der Pins
- Opt-in via `simulation=True` an den Generierungs-Tools

**MINT-Wheel-Integration** (in `MINT_Wheel/adapters/`)
- `electrical_adapter.py` nutzt die `kicad_mcp`-Generatoren statt das alte `kicad_gen.py`
- `schematic_analysis_adapter.py` validiert generierte Schaltpläne gegen die `product.json`-Spec

**Cross-Environment-Layer** (`kicad_mcp/utils/path_env.py`)
- Single-Source-of-Truth für Umgebungs-Detektion (Windows / WSL / Linux / macOS)
- Bidirektionale Pfad-Konversion (`/mnt/c/...` ↔ `C:\...`), damit ein WSL-Agent transparent gegen einen Windows-Server reden kann
- Jedes MCP-Tool, das einen `pcb_path`/`schematic_path`/`project_path`/`output_path`-Parameter akzeptiert, normalisiert ihn am Eingang via `to_local_path()` — egal, ob der Pfad WSL- oder Windows-Stil hat
- Zentrale KiCad-Install-Discovery (`kicad_cli`, `footprints`, `symbols`, gebündeltes Python) mit Override-Variablen `KICAD_BIN`, `KICAD_LIB_ROOT`, `KICAD_SYMBOL_ROOT`, `KICAD_PYTHON_PATH`
- Verifiziert per dynamischem Test (`tests/test_all_tools_dynamic.py`), der jedes registrierte `@mcp.tool` automatisch auf Pfad-Normalisierung prüft

**Code-Qualität**
- Pylint-Konfiguration zentral in `pyproject.toml` (`[tool.pylint."MESSAGES CONTROL"]`) mit Begründung pro deaktiviertem Cluster
- Repo-weiter Lint-Status: 0 Errors / 0 Warnings (130 Python-Dateien)

---

## Tool-Katalog (91 Tools)

### Projektmanagement
| Tool | Beschreibung |
|------|--------------|
| `list_projects` | KiCad-Projekte auf der Platte finden |
| `open_project` | Projekt öffnen |
| `get_project_structure` | Datei-Struktur eines Projekts ausgeben |
| `validate_project` | Projekt-Integrität prüfen |
| `kicad_mcp_doctor` | One-Shot-Health-Check (KiCad-Install, `kicad-cli`, kipy, Footprint-Index, native Libs) |

### Schaltplan-Analyse
| Tool | Beschreibung |
|------|--------------|
| `list_schematic_components` | Bauteile auflisten mit Typ-/Wert-Filter |
| `get_symbol_details` | Detail-Info zu einem Bauteil per Reference |
| `search_symbols` | Regex-Suche über Reference, Wert, Bibliothek |
| `get_schematic_info` | Metadaten, Statistik, hierarchische Sheets |
| `analyze_schematic_connections` | Verbindungs-Analyse |
| `find_component_connections` | Verbindungen eines Bauteils verfolgen |

### PCB-Analyse
| Tool | Beschreibung |
|------|--------------|
| `list_pcb_footprints` | Footprints mit Position, Layer, Pad-Anzahl |
| `analyze_pcb_nets` | Netzliste mit Track-/Via-Zähler pro Net |
| `find_tracks_by_net` | Alle Track-Segmente eines bestimmten Netzes |

### Pin- und Muster-Analyse
| Tool | Beschreibung |
|------|--------------|
| `analyze_pin_functions` | I2C, SPI, UART, GPIO, ADC, PWM, USB, JTAG, CAN aus Net-Namen ableiten |
| `detect_pin_conflicts` | Mögliche Pin-Konflikte aufdecken |
| `identify_circuit_patterns` | Übliche Schaltungstopologien erkennen |
| `analyze_project_circuit_patterns` | Projekt-weite Muster-Analyse |

### Validierung
| Tool | Beschreibung |
|------|--------------|
| `run_erc` | Electrical Rules Check via `kicad-cli` (JSON-Output) |
| `get_erc_violations` | Gefilterte ERC-Ergebnisse nach Severity |
| `run_drc_check` | Design Rules Check mit History-Tracking |
| `get_drc_history_tool` | DRC-Trend über Zeit |
| `validate_design` | JSON-Design-Spec validieren ohne Files zu erzeugen |

### Export (via `kicad-cli`)
| Tool | Beschreibung |
|------|--------------|
| `export_gerbers` | Gerber-Fertigungsdaten |
| `export_drill` | Bohrdaten |
| `export_step` | 3D-STEP-Modell |
| `export_pdf` | PDF (Schaltplan oder PCB) |
| `export_svg` | SVG (Schaltplan oder PCB) |
| `export_png` | PNG-Raster (cairosvg-Pipeline; bootstrappt auf Windows automatisch die KiCad-`cairo-2`-DLL) |
| `export_pos` | Bauteilpositionen / Pick-and-Place |
| `render_3d` | 3D-Render (PNG, beliebiger Winkel) |
| `get_board_stats` | Board-Statistik als JSON |
| `generate_pcb_thumbnail` | Schnelle PCB-Vorschau |
| `generate_project_thumbnail` | Projekt-Vorschau |
| `analyze_bom` / `export_bom_csv` | Bill of Materials |
| `extract_project_netlist` / `extract_schematic_netlist` | Netzlisten-Extraktion (mit voller Pin-Konnektivität via `kicad-cli` als Primärpfad) |

### Generierung (alleinstellungsmerkmal)
| Tool | Beschreibung |
|------|--------------|
| `generate_project` | Komplettes `.kicad_pro` + `.kicad_sch` + `.kicad_pcb` aus JSON |
| `generate_schematic` | Nur Schaltplan |
| `generate_pcb` | Nur PCB |
| `generate_from_netlist` | `.kicad_sch` + `.kicad_pcb` aus `.net`-Netlist (KiCad-sexpr) |

### ESPHome-Konvertierung (alleinstellungsmerkmal)
| Tool | Beschreibung |
|------|--------------|
| `esphome_to_kicad` | ESPHome-YAML in komplettes KiCad-Projekt umwandeln |
| `list_esphome_components` | Alle unterstützten ESPHome-Plattformen mit KiCad-Mapping |

### LTspice-Konvertierung
| Tool | Beschreibung |
|------|--------------|
| `convert_ltspice_to_kicad` | LTspice-`.asc` in ein KiCad-Projekt (Schaltplan + PCB-Stub) konvertieren |

### PCB-Patch (Headless, kein SWIG, keine KiCad-GUI)
| Tool | Beschreibung |
|------|--------------|
| `patch_pcb_nets_from_netlist` | F8-Äquivalent: Net-Tagging — bestehende PCB-Net-Indizes bleiben, nur fehlende werden angehängt |
| `resolve_pcb_footprints` | `[lib:fp]`-Platzhalter durch echte `.kicad_mod`-Daten ersetzen |
| `validate_footprints` | Schaltplan-Pin-Zuweisungen gegen tatsächliche PCB-Pad-Namen abgleichen |
| `rotate_pcb` | Komplette Platine um `(0,0)` drehen (pcbnew-API; kein FootprintLoad nötig) |

### PCB-Geometrie & Routing (Headless)
| Tool | Beschreibung |
|------|--------------|
| `compute_pad_world_positions` | Absolute Welt-Koordinaten jedes Pads — rotation- und `B.Cu`-flip-bewusst |
| `add_track_to_pcb` | Pad-zu-Pad-Track einsetzen (optional Layer-Wechsel-Via), flip-bewusst |
| `add_zone_pour_to_pcb` | Kupferpour-Zone auf Kupferlage einer Net binden |

### Footprint-Suche (Index der gebündelten KiCad-Bibliothek)
| Tool | Beschreibung |
|------|--------------|
| `index_kicad_footprints` | JSON-Index über `share/kicad/footprints/*.pretty/*.kicad_mod` (re)bauen |
| `search_footprints` | Fuzzy-/Substring-Suche über alle indizierten Footprints |
| `find_footprint_by_specs` | Filter nach Pad-Anzahl, Package-Familie, Body-Bbox; Ranking nach Body-Größe |
| `suggest_builtin_for_custom` | Zu einem `custom.kicad_mod` die nächsten Built-ins mit Confidence-Score vorschlagen |

### IPC-API (Live-Bridge zur KiCad-GUI)
| Tool | Beschreibung |
|------|--------------|
| `ipc_check_status` | Diagnose: kipy installiert? KiCad erreichbar? Board offen? |
| `ipc_install_kipy` | `kicad-python`-Client in den aktiven Interpreter installieren |
| `ipc_get_open_documents` | Schaltpläne + PCBs auflisten, die in KiCad gerade offen sind |
| `ipc_get_pad_world_pos` | Welt-Koordinaten eines Pads via IPC (umgeht SWIG) |
| `ipc_set_footprint_pose` | Footprint live verschieben/drehen (absolut oder Delta; ein Undo-Schritt) |
| `ipc_route_pin_to_pin` | Track (+ optionale Via) zwischen zwei Pads — live im laufenden PCB-Editor |
| `ipc_add_zone_pour` | Kupferpour-Zone live einsetzen, an Net + Polygon gebunden |
| `ipc_route_power_ring` | Convenience: breiter Power-Track durch eine Bauteilfolge |
| `ipc_save` | Direkter `SaveDocument`-IPC-Befehl (PCB only — silent, kein Dialog) |
| `ipc_save_all` | Speichern aller offenen Dokumente, die die IPC-Bridge erreicht |
| `ipc_revert` | Direkter `RevertDocument` (PCB only — silent, Modified-Flag wird vor Reload geleert) |
| `ipc_save_via_action` | Fallback-Save via `RunAction("common.Control.save")` — funktioniert für SCH, öffnet aber den „Save?"-Dialog |
| `ipc_revert_via_action` | Fallback-Revert via `RunAction("common.Control.revert")` — Dialog wie oben |
| `ipc_run_drc` | DRC im laufenden PCB-Editor (PCB only) |
| `ipc_run_erc` | Stub. Eeschema registriert in 10.0.x kein `RunAction` — siehe `run_erc` (CLI-basiert). Tracking: KiCad #2077 |
| `ipc_export_schematic` | **Lebendigen** Schaltplan-Speicherinhalt nach Disk schreiben — Formate: `svg`, `pdf`, `dxf`, `ps`, `netlist`, `bom` (wrappt `RunSchematicJobExport*`). Mit `run_erc` zusammen ergibt das einen Live-State-ERC |

### Schaltplan-Patch (inkrementell, headless)
| Tool | Beschreibung |
|------|--------------|
| `compute_pin_world_positions_sch` | Pin-Welt-Koordinaten jedes platzierten Symbols (rotation-/mirror-bewusst) |
| `list_schematic_groups` | Alle vorhandenen `kicad-mcp.group`-IDs auflisten |
| `get_schematic_bbox` | Bounding-Box (mm) für Refs / Gruppe / ganzen Schaltplan |
| `add_schematic_symbols` | Bulk-Insert von Symbolen in bestehenden `.kicad_sch` (Auto-Resolve + Embedding der `lib_symbols`; snappt `_Small`/`Device:C/R/L/CP`-Centers automatisch auf das Halbgrid, damit Pins auf 2.54 mm landen) |
| `add_schematic_wire` | Draht-Segmente in beliebigen Winkeln einsetzen |
| `add_schematic_label` | Lokale / globale / hierarchische Labels (mit optionalem `justify="left"/"right"`) |
| `connect_pins` | Pin-zu-Pin-Manhattan-Drähte oder globale Label-Paare (`mode="label"`) |
| `validate_schematic_patch` | Vorflug-Check: Ref-Kollisionen, fehlende `lib_id`s |
| `annotate_schematic` | Pure-Python-Annotator: vergibt Sequenz-Nummern an `R?` / `C?` / nicht-konforme `#PWR_*`-Refs (Lücken-füllend; `force_renumber` für volles Re-Annotate). Updated sowohl `(property "Reference" …)` als auch verschachtelte `(reference "X")`-Instance-Einträge. |
| `move_schematic_group` | Jedes Symbol mit Gruppen-Tag um Delta verschieben |
| `rotate_schematic_group` | Rigid-Rotation um Schwerpunkt / freien Pivot — freier Winkel mit 90°-Snap + Toleranz / `force=True`-Override |
| `delete_schematic_items` | Items entfernen via `refs`, `group_id` oder `types` + `region` (mm-BBox) — Labels/Drähte/Junctions können regionsbasiert gelöscht werden, obwohl sie kein Gruppen-Tag tragen |

### Interne Diagnose & Benchmarks
| Tool | Beschreibung |
|------|--------------|
| `benchmark_schematic` | Schaltplan aus `parts` + `nets` erzeugen und Quality-Metriken ausgeben (Wire-zu-Label-Verhältnis, Score) — vom Iterations-Loop genutzt |
| `benchmark_loop` | Voller Benchmark-Zyklus: Projekt erzeugen, SVG exportieren, scoren, Bericht ausgeben |

---

## Schnellstart

### Voraussetzungen

- **KiCad 10.0** (der Server läuft unter KiCads gebündeltem Python — keine separate Python-Installation nötig)
- Beliebiger MCP-Client (Claude Code, Claude Desktop, Cursor, Windsurf, VS Code, …)

### Installation (One-Shot)

```bash
git clone <dieses-repo>
cd kicad-mcp

# Linux / WSL / macOS:
./install.sh

# Windows (PowerShell):
.\install.ps1
```

Der Installer (a) prüft, ob KiCad 10 erreichbar ist, (b) registriert den Server bei Claude Code, falls die `claude`-CLI vorhanden ist, und (c) druckt fertige JSON-Snippets für jeden anderen Client aus.

Wird KiCad 10 nicht gefunden, bricht der Installer mit einer klaren Fehlermeldung ab. Workaround: `KICAD_PYTHON_PATH` auf den absoluten Pfad zu `python.exe` im KiCad-`bin/`-Ordner setzen.

### Optional: Chat-Plugin direkt in KiCad (PCM „Aus Datei installieren")

Neben dem MCP-Server (für externe Clients) liefert dieses Repo ein **KiCad-Action-Plugin** — eine Toolbar-Schaltfläche im PCB-Editor, die ein angedocktes Chat-Panel „Claude für KiCad" öffnet. Das Panel ist mit einer gebündelten Kopie dieses Servers verdrahtet, sodass jede Nachricht eine headless Claude-Runde gegen das **aktuell geöffnete Board** ausführt.

Installiert wird es über KiCads Plugin and Content Manager — **nicht** über GitHubs automatisch erzeugte Repo-ZIP (die packt alles in einen `<repo>-<branch>/`-Ordner und wird vom PCM abgelehnt). Stattdessen das eigens gebaute PCM-Archiv verwenden:

1. `claude_kicad-<version>-pcm.zip` beschaffen:
   - vom letzten [GitHub-Release](https://github.com/ChrisRudi/kicad-mcp/releases) **herunterladen** (wird automatisch angehängt), **oder**
   - selbst **bauen**: `python make_pcm_zip.py` → schreibt `dist/claude_kicad-<version>-pcm.zip` (reine Standardbibliothek, kein KiCad nötig).
2. In KiCad: **Plugin and Content Manager → Aus Datei installieren… → die ZIP auswählen.**
3. KiCad neu starten. Im PCB-Editor erscheint eine neue Toolbar-Schaltfläche; ein Klick öffnet den Chat. Beim ersten Start installiert die Einrichtungs-Checkliste des Panels die Python-Abhängigkeiten des Servers und hilft bei der Anmeldung in Claude Code.

Der Chat benötigt die [Claude-Code-CLI](https://claude.ai/code) auf dem System und KiCads aktivierte IPC-API (die Einrichtungs-Checkliste bietet für beides eine Ein-Klick-Lösung).

### Andere MCP-Clients konfigurieren

Der Installer druckt am Ende fertige JSON-Snippets für Claude Desktop, Cursor, Windsurf, VS Code, Continue.dev und Zed aus. Jeder Eintrag nutzt `start_mcp_wsl.sh` (Linux/WSL/macOS) bzw. `start_mcp.bat` (Windows) als Launch-Befehl und zeigt auf dieses Verzeichnis.

### Optional: `.env`

Pro-Projekt-Einstellungen (Suchpfade, eigener CLI-Pfad) in `.env` neben `main.py`:

```
KICAD_SEARCH_PATHS=~/Documents/KiCad,~/Projects
# KICAD_CLI_PATH nur, wenn die Auto-Detection fehlschlägt
```

### Logs

Server-Logs landen in `~/.kicad-mcp/logs/kicad-mcp.log`. Der vollständige Pfad wird bei jedem Start auf stderr ausgegeben.

### Manueller Start (zum Debuggen)

```bash
# Linux / WSL:
./start_mcp_wsl.sh

# Windows:
start_mcp.bat
```

---

## Process-Supervisor (Crash-Recovery + In-Band-Restart)

`_tasks/mcp_supervisor/` liefert einen optionalen schmalen Python-Wrapper, der zwischen dem Host (Claude Code, Cline usw.) und dem echten `kicad-mcp`-stdio-Server sitzt. Spricht in beiden Richtungen reines, neueline-framed JSON-RPC — **keine Protokoll-Erweiterung, voll MCP-spec-kompatibel**.

Was er bringt:
* **Auto-Respawn nach Crash.** Child-Segfault / OOM / `os._exit` → Supervisor erkennt EOF, sendet synthetische `-32603` an alle pendenten Requests, spawnt einen neuen Child. Der Host bleibt verbunden.
* **`restart_mcp_child`-Tool**, in jede `tools/list`-Antwort injiziert. Forciert ein sauberes Kill+Respawn (~5 ms). Nutzbar, wenn der Child zwar lebt, aber logisch festsitzt: stale kipy-Session, leaked `pcbnew.BOARD`, hängender KiCad-IPC. Ruft sich aus jedem MCP-Client wie ein normales Tool auf.

Aktivierung: in der Client-Config auf den Wrapper statt das direkte Skript zeigen:

```jsonc
"kicad": {
  "type": "stdio",
  "command": "bash",
  "args": ["/pfad/zu/kicad-mcp/_tasks/mcp_supervisor/start_mcp_supervisor.sh"]
}
```

Rückbau: `args[0]` zurück auf `start_mcp_wsl.sh` setzen. Der Supervisor lässt nichts zurück (keine Daemons, keine Sockets, keine PID-Files).

End-to-End-Tests:

```bash
python3 _tasks/mcp_supervisor/test_supervisor.py            # ~1 s, Fake-Child
python3 _tasks/mcp_supervisor/test_with_real_kicad.py       # ~30 s, echter kicad-mcp
```

Beide sollten am Ende `ALL OK` ausgeben. Details: `_tasks/mcp_supervisor/README.md`.

---

## Eingabeformat für die Generierung

Die Generierungs-Tools nehmen JSON-Strings entgegen. Beispiel:

```json
{
  "parts": [
    {
      "ref": "U1",
      "name": "ESP32-C6",
      "footprint": "RF_Module:ESP32-C6-MINI-1",
      "value": "ESP32-C6",
      "pins": [
        {"num": 1, "name": "VCC", "type": "power_in"},
        {"num": 2, "name": "GND", "type": "power_in"},
        {"num": 3, "name": "IO17", "type": "bidirectional"}
      ]
    },
    {
      "ref": "R1",
      "name": "R",
      "footprint": "Resistor_SMD:R_0805_2012Metric",
      "value": "10k",
      "pins": [
        {"num": 1, "name": "1", "type": "passive"},
        {"num": 2, "name": "2", "type": "passive"}
      ]
    }
  ],
  "nets": [
    {"name": "VCC", "type": "power", "connections": ["U1:VCC", "R1:1"]},
    {"name": "GND", "type": "power", "connections": ["U1:GND", "R1:2"]},
    {"name": "SDA", "type": "signal", "connections": ["U1:IO17"]}
  ],
  "board": {
    "shape": "rectangle",
    "width": 50,
    "depth": 30,
    "layers": 2,
    "thickness": 1.6
  }
}
```

**Pin-Typen:** `input`, `output`, `bidirectional`, `tri_state`, `passive`, `power_in`, `power_out`, `open_collector`, `open_emitter`, `free`, `unspecified`, `no_connect`

**Board-Formen:** `rectangle`, `circle`, `euro_divider` (mit `euro_type`: `3U`, `6U`, `half_euro`)

---

## ESPHome-YAML zu KiCad

Eine ESPHome-YAML einkippen und ein komplettes KiCad-Projekt rauskriegen:

```yaml
esphome:
  name: weather-station

esp32:
  board: esp32dev

i2c:
  sda: 21
  scl: 22

sensor:
  - platform: bme280
    address: 0x76
  - platform: bh1750
    address: 0x23

binary_sensor:
  - platform: gpio
    pin: GPIO5
    name: "Motion"

light:
  - platform: neopixelbus
    pin: GPIO16
    num_leds: 30
```

Das Tool generiert automatisch:
- **ESP32-WROOM-32E** mit korrektem Pinout
- **BME280** + **BH1750** auf I2C-Bus mit Pull-ups (4,7 kΩ)
- **WS2812B**-NeoPixel-Anschluss an GPIO16
- **100 nF**-Entkoppelkondensatoren für jeden IC
- Bewegungssensor-GPIO-Verbindung
- Alle Power-Netze (3V3, GND) korrekt verdrahtet
- Vollständiges `.kicad_sch` + `.kicad_pcb` + `.kicad_pro`

**Unterstützte Plattformen:** BME280, BME680, DHT22, DS18B20, SHT31, BH1750, VL53L0X, MPU6050, ADS1115, SSD1306, WS2812B — weitere lassen sich einfach in die Komponenten-Datenbank ergänzen.

`simulation=True` zusätzlich angeben, um SPICE-Modell-Properties für simulationsfähige Schaltpläne mit auszugeben.

---

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v --no-cov
# 322 Tests passed (1 environment-abhängiger Test schlägt fehl, wenn der KiCad-IPC-Bus
# nicht erreichbar ist — er erwartet einen bestimmten Fehler-String, der von der
# tatsächlichen "Connection refused"-Meldung abweicht; in CI ohne KiCad zu erwarten)
```

Highlight: **`tests/test_all_tools_dynamic.py`** baut den echten Server und prüft jedes registrierte `@mcp.tool` automatisch auf Description, Eindeutigkeit, Pfad-Normalisierung (`to_local_path`-Aufruf je `_path`-/`_dir`-Parameter) und einen Empty-Call-Sanity-Test. Neue Tools sind sofort abgedeckt; eine fehlende Pfad-Normalisierung schlägt sofort an.

---

## Projekt-Struktur

```
kicad-mcp/
  main.py                           Entry-Point
  pyproject.toml                    Dependencies & Config
  kicad_mcp/
    server.py                       FastMCP-Server (91 Tools registriert)
    config.py                       Plattform-spezifische KiCad-Pfade
    context.py                      Lifespan-Management
    tools/                          MCP-Tool-Implementierungen
      erc_tools.py                  ERC via kicad-cli
      cli_export_tools.py           9 Export-Tools (gerber/drill/step/pdf/svg/png/pos/3d/board-stats)
      schematic_tools.py            4 Schaltplan-Analyse-Tools
      pcb_tools.py                  3 PCB-Analyse-Tools
      pin_tools.py                  2 Pin-Analyse-Tools
      generation_tools.py           7 Generation- + Benchmark-Tools
      esphome_tools.py              2 ESPHome-Konvertier-Tools
      ltspice_tools.py              1 LTspice → KiCad-Konverter
      ipc_tools.py                  17 IPC-Live-Bridge-Tools (Routing/Zonen/Pose/Save/Revert/DRC/Live-SCH-Export)
      sch_patch_tools.py            12 Schaltplan-Patch-Tools (Phase S, inkl. annotate_schematic)
      pcb_patch_tools.py            4 PCB-Text-Patcher-Tools (Phase A)
      pcb_geometry_tools.py         3 PCB-Geometrie-/Routing-Tools (Phase E)
      footprint_search_tools.py     4 Footprint-Library-Such-Tools (Phase D)
      [+ Basis: drc, bom, netlist, pattern, export, project, analysis, validation]
    generators/                     Generierungs-Engine
      sexpr.py                      S-Expression-Builder
      schematic_builder.py          .kicad_sch-Generator
      pcb_builder.py                .kicad_pcb-Generator
      validator.py                  Eingabe-Validierung
      spice_models.py               SPICE-Modell-Auto-Zuweisung
      esphome_parser.py             ESPHome-YAML → parts/nets-Konverter
    utils/
      sexpr_parser.py               S-Expression-Parser
      path_env.py                   Cross-Environment Pfad-Konversion + KiCad-Discovery
      wsl_path.py                   WSL-Pfad-Konversion (Legacy-Helper)
      kicad_cli.py                  CLI-Detection & -Aufruf
    resources/                      MCP-Ressourcen (read-only)
    prompts/                        MCP-Prompt-Templates
  tests/                            322 Unit-Tests (alle grün; 1 schlägt nur ohne erreichbaren KiCad-IPC-Bus fehl)
```

---

## Lizenz

**GPL-3.0-or-later** — siehe [LICENSE](LICENSE).

Dieses Projekt lädt KiCads `pcbnew`-Python-Modul in-process (PCB-Geometrie,
Konnektivität, Via-Analyse). `pcbnew` ist Teil von KiCad und steht unter
GPL-3.0; die In-Process-Kombination macht das Gesamtwerk GPL-3.0-or-later.
Begründung und Drittkomponenten in [NOTICE](NOTICE).

Teile stammen aus dem Upstream-Projekt [kicad-mcp](https://github.com/lamaalrajih/kicad-mcp)
von Lama Al Rajih, ursprünglich unter MIT-Lizenz (GPL-kompatibel) — der
Original-Hinweis bleibt in [LICENSE.MIT](LICENSE.MIT) erhalten.
