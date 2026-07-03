# Claude für KiCad — Chat-Copilot im PCB-Editor + 186-Tool-MCP-Server

**Claude als dockbares Chat-Panel direkt in KiCad 10** — mit klickbaren
Board-Links, 34 Ein-Klick-Super-Features und einem warmen lokalen MCP-Server —
plus derselbe Server als klassischer [Model Context Protocol](https://modelcontextprotocol.io/)-Endpunkt
für Claude Code, Claude Desktop, Cursor und jeden anderen MCP-Client.

> Deutsche Übersetzung dieser README: [README.de.md](README.de.md).
> (This README is bilingual-pragmatic: product sections in German — the
> plugin UI language — tool/API sections in English.)

---

## What Makes This Different

Most KiCad MCP servers are tool collections. This one is a **product**:

- **💬 Chat dockt in den PCB-Editor** — ein Toolbar-Button öffnet „Claude für
  KiCad" als natives AUI-Panel. Jede Nachricht läuft als headless
  Claude-Code-Turn gegen das **offene Board** (keine API-Keys — dein
  Claude-Code-Login).
- **🔗 Klickbare Antworten (Cross-Probe)** — Referenzen, Netze, Pins, Layer und
  Koordinaten in der Antwort sind Links: Klick markiert + zoomt im Editor.
  Änderungen kommen mit Quittung („✎ geändert: … · 📍 zeigen") und sichtbarem
  **Undo**.
- **✨ 34 Super-Features als Ein-Klick-Buttons** — Dinge, die KiCad
  *prinzipiell* nicht kann, weil sie Bedeutung/Norm-/Datenblatt-Wissen
  brauchen: Entwirren mit Geister-Vorschau, Datenblatt-Abgleich, semantischer
  Design-Wächter, Stromtragfähigkeit (IPC-2221), Schutzklassen (IEC
  61140/60664), echte SPICE-Simulation, BOM-Konsolidierung, DFM-Check … —
  Roadmap + ehrliche Grenzen in [docs/superfeatures.md](docs/superfeatures.md).
- **🔥 Warm-Server** — optional läuft der 186-Tool-Server **einmal pro
  KiCad-Sitzung** als lokaler HTTP-Dienst statt pro Nachricht neu zu starten
  (`KICAD_MCP_TRANSPORT=http`): kein Kaltstart, kein
  „MCP nicht verbunden"-Wackler; Health-Check pro Zug, Auto-Restart,
  Bearer-Token, sauberer Teardown.
- **📐 Generierung, nicht nur Analyse** — Schaltplan + PCB aus
  JSON-Spezifikationen, komplette Projekte aus **ESPHome-YAML**, LTspice-Import,
  Datenblatt-PDF → Applikations-Schaltungsblock.
- **🧭 Ehrlichkeits-Disziplin** — Normwerte (IEC 60664, Fab-Kataloge) leben als
  **datierte, kuratierte Snapshots** im Werkzeug statt im Modellgedächtnis;
  Mutationen laufen nur nach ausdrücklichem **Go**; jede v1-Grenze wird im
  Bericht benannt („Vorprüfung, keine Zertifizierung").
- **🛡️ Gehärtet gegen die fiesen Fälle** — Geister-Editor-Abwehr mit
  Registry + Selbstheilung der Links, Busy-Backoff statt Fehlstart,
  Prozess-Aufräumen bei KiCad-Exit, WSL↔Windows-Pfad-Transparenz.

---

## Die Super-Features (Auszug)

Jeder Button dispatcht einen kanonischen, geführten Auftrag — mit der aktuellen
Editor-**Auswahl** als Wirkbereich (nichts markiert = ganzes Board). Vollständige
Liste + v1-Grenzen: [docs/superfeatures.md](docs/superfeatures.md).

| Button | Was er tut | Fundament |
|---|---|---|
| 🧶 Entwirren | Ratsnest-Entkreuzung: im Kopf lösen, Score vorher/nachher, **Geister-Vorschau auf MCP.Skizze**, ein Batch-Move nach Go | `evaluate_layout`-Scorer |
| 📄 Datenblatt-Abgleich | Beschaltung eines ICs gegen sein PDF (Entkopplung, Pins, fehlende Bauteile) | `review_ic_against_datasheet` |
| 🛡️ Design-Wächter | Semantischer ERC: I²C ohne Pull-ups, Quarz ohne Load-Caps, Entkopplungs-Nähe | `audit_design`-Regel-Registry |
| 🔥 Stromtragfähigkeit | Track-Breiten gegen Design-Strom (IPC-2221, Innenlagen strenger) | `check_ampacity` (#184) |
| 🔌 Schutzklassen | Isolationskonzept nach IEC 61140: geforderte Kriech-/Luftstrecken je Spannungsgrenze aus datiertem Norm-Snapshot vs. Ist-Abstände | `get_safety_spacing` (#186) |
| 📈 Simulation | Echte ngspice-Ausführung des vom Agenten gebauten SPICE-Decks; analytischer Fallback | `run_spice_sim` (#185) |
| 💰 BOM-Konsolidierung / 🏭 Fab-Standardteile | E-Reihen-Standardisierung, No-Load-Fee-Vorzugsteile inkl. Ersparnis | `consolidate_bom`, `suggest_preferred_parts` |
| 🔎 Test-Punkt-Wächter | Kritische Netze ohne Prüfpunkt-Zugang, Abdeckung in % | `audit_test_points` |
| 👁️ Mitdenken-Review | Bewertet deine letzten Hand-Änderungen am offenen Board | `live_summarize_user_changes` |

Dazu: Bus-Radar, Board erklären, Netz-Navigator, Pin-Tausch (Vorschläge),
Ausrichten & Anordnen, Polar-Board, Skizzen-Layer/-Dirigent, Thermik,
Betriebstemperatur, Slew-Rate, Impedanz, DFM-Check, Kosten-Schätzer,
SPICE-Modelle, Bauteil-Sourcing, Foto→Schaltung, Sicherheitsabstände,
Firmware-Pinmap, MLCC-Derating, Silk-Aufräumen, Datenblatt→Schaltung.

---

## Install

### A) Das KiCad-Plugin (empfohlen — Chat + alles oben)

1. Hole `claude_kicad-<version>-pcm.zip`:
   - **Download** aus dem neuesten [GitHub Release](https://github.com/ChrisRudi/kicad-mcp/releases), **oder**
   - **selbst bauen:** `python make_pcm_zip.py` → `dist/claude_kicad-<version>-pcm.zip`
     (pure stdlib, kein KiCad nötig). Alternativ: `install_plugin.bat` /
     `install_plugin.sh` installieren direkt aus dem Repo-Checkout.
2. KiCad: **Plugin- und Content-Manager → Aus Datei installieren…** → Zip wählen.
   (Nicht das GitHub-Auto-Zip nehmen — PCM lehnt den `<repo>-<branch>/`-Wrapper ab.)
3. KiCad neu starten → neuer Toolbar-Button im PCB-Editor. Der erste Klick
   öffnet die Einrichtungs-Checkliste: installiert die Python-Abhängigkeiten in
   einen plugin-eigenen `_deps`-Ordner, aktiviert KiCads IPC-API und hilft beim
   Claude-Code-Login. Danach: Board öffnen, Button klicken, chatten.

Voraussetzungen: **KiCad 10.0**, [Claude Code CLI](https://claude.ai/code)
(Login per Subscription, kein API-Key). Updates liefert der eingebaute
Self-Updater (Versionsanzeige im Panel-Titel; Historie: [plugin/VERSIONS.md](plugin/VERSIONS.md)).

### B) Der MCP-Server für externe Clients

```bash
git clone <this-repo>
cd kicad-mcp

# Linux / WSL / macOS:
./install.sh
# Windows (PowerShell):
.\install.ps1
```

Der Installer prüft KiCad 10, registriert den Server bei Claude Code (falls
`claude` installiert) und druckt Copy-Paste-JSON für Claude Desktop, Cursor,
Windsurf, VS Code, Continue.dev und Zed. Start-Kommandos:
`start_mcp_wsl.sh` (Linux/WSL/macOS) bzw. `start_mcp.bat` (Windows).
Der Server läuft unter **KiCads gebündeltem Python** — keine separate
Python-Installation nötig. Falls KiCad nicht gefunden wird:
`KICAD_PYTHON_PATH` auf die `python.exe` im KiCad-`bin/` setzen.

---

## Warm-Server & Umgebungs-Schalter

Standard-Transport ist `stdio` (Claude startet den Server pro Nachricht).
Mit `KICAD_MCP_TRANSPORT=http` hält das Plugin **einen** persistenten lokalen
Server pro KiCad-Sitzung warm (strikt `127.0.0.1`, zufälliges Bearer-Token,
Pidfile-Verwaltung, Health-Check je Zug, `atexit`-Teardown). Rollback ist
jederzeit ein Env-Wort.

| Variable | Wirkung |
|---|---|
| `KICAD_MCP_TRANSPORT` | `stdio` (Default) oder `http` (Warm-Server) |
| `KICAD_MCP_HTTP_HOST` / `_PORT` / `_TOKEN` | HTTP-Bind + Token (setzt normal das Plugin) |
| `KICAD_MCP_NGSPICE` | Pfad zum ngspice-Binary für `run_spice_sim` (sonst PATH/KiCad-bin) |
| `KICAD_MCP_NO_AUTO_OPEN` | `1` = Server darf nie selbst einen Editor starten (setzt das Plugin automatisch — verhindert Geister-Instanzen) |
| `KICAD_MCP_IPC_TIMEOUT_MS` | kipy-Timeout (Default 15000) |
| `KICAD_MCP_MAX_TURNS` | Agentic-Turn-Limit pro Nachricht (Default 80, 0 = aus) |
| `KICAD_MCP_CONNECT_RETRIES` | Auto-Retry bei MCP-Kaltstart-Fehlschlag (Default 1) |
| `KICAD_PYTHON_PATH` / `KICAD_MCP_ROOT` / `KICAD_BIN` | Pfad-Overrides (KiCad-Python, Server-Code, kicad-cli) |
| `KICAD_MCP_STATE_DIR` | Ablage des Warm-Server-Pidfiles |
| `KICAD_CLAUDE_ALLOW_WSL` | Opt-in: Windows-KiCad + Claude in WSL (Bridge) |

Diagnose: Der **Diagnose-Knopf** im Einrichtungs-Panel sammelt Pfade, Versionen,
Transport-Status (läuft der Warm-Server? PID, Port, Uptime, MCP-Ping) und die
echte Server-Probe in einen kopierbaren Report.

---

## Tool-Familien (186 Tools)

Single Source of Truth ist [`kicad_mcp/tool_registry.py`](kicad_mcp/tool_registry.py)
(der Drift-Wächter `tests/test_tool_audit.py` pinnt die Zahl); jede
Tool-Docstring erklärt „wann dieses, wann das Nachbar-Tool". Die Familien:

| Familie | Highlights |
|---|---|
| **Analyse (SCH/PCB)** | `list_pcb_footprints`, `analyze_pcb_nets`, `find_tracks_by_net`, `list_schematic_components`, Pin-/Pattern-Analyse, `get_board_stats` |
| **Validierung** | `run_erc` / `run_drc_check` (kicad-cli, headless), DRC-Historie, `validate_design` |
| **Semantik-Audits** | `audit_design` (Regel-Registry), `audit_power_tree`, `audit_test_points`, `list_bus_members`, `consolidate_bom`, `suggest_preferred_parts` |
| **Elektrik/Norm** | `check_ampacity` (IPC-2221), `get_safety_spacing` (IEC-60664-Snapshot), `run_spice_sim` (ngspice-Batch) |
| **Export** | Gerber, Drill, STEP, PDF/SVG/PNG, Pick&Place, 3D-Render, BOM/Netzliste |
| **Generierung** | `generate_project` / `generate_schematic` / `generate_pcb` aus JSON, `generate_from_netlist`, `esphome_to_kicad`, `convert_ltspice_to_kicad` |
| **PCB-Text-Patcher** (headless, F8-äquivalent) | Netz-Tagging aus Netzliste, Footprint-Resolve, Rotation, `pcb_batch` (N Edits, eine Write-Runde), `add_vias_to_pcb`, `place_at_pivot` |
| **PCB-Geometrie/Routing** | flip-/rotations-korrekte Welt-Koordinaten (`compute_pad_world_positions`), Tracks/Bögen/Zonen, `polar_grid` (Radial-Layouts) |
| **Schaltplan-Patcher** (inkrementell, headless) | Symbole/Wires/Labels einfügen, `connect_pins`, Gruppen-Transformationen, Annotation, `add_power_symbols` |
| **IPC-Live-Bridge** (laufende KiCad-GUI) | Tracks/Vias/Zonen/Pose live, Save/Revert, Selektion (`ipc_get_selection`), Marker/Skizzen-Layer (`ipc_draw_markers`, `ipc_markup_to_tracks`), Live-Diff (`live_summarize_user_changes`) |
| **Warm-Board-Daemons** | `pcb_eval` (Ad-hoc-Geometrie gegen warmes Board), `check_connectivity` (headless Ratsnest), `via_promote` (Blind/Buried→Through) |
| **Review/Datenblatt** | `review_ic_against_datasheet`, `review_system_interconnect`, `list_missing_datasheets`, `extract_circuit_from_pdf` → `apply_circuit_block` |
| **Rendering** | `pcb_render` (Region-PNGs statt Kopf-Geometrie) |
| **Footprint-Suche** | Index über die gebündelte KiCad-Bibliothek, Spec-basierte Suche, Alternativen-Vorschlag |
| **Projekt/Diagnose** | Projekt-Discovery, `kicad_mcp_doctor`, Benchmarks |

---

## Architektur in einem Absatz

Der Server läuft unter KiCads gebündeltem Python (kipy + pcbnew erreichbar) und
spricht wahlweise stdio oder streamable-HTTP. Drei Editier-Wege, je nach
Situation: **Text-Patcher** (chirurgische `.kicad_pcb`/`.kicad_sch`-Edits,
headless), **IPC-Live-Tools** (mutieren das In-Memory-Modell der offenen GUI —
Pflicht, wenn das Board offen ist; ein Disk-Write-Guard blockt Kollisionen)
und **Generatoren** (neue Dateien aus Specs). Ein zentraler kipy-Session-Layer
recycelt die Verbindung mit Health-Check und Busy-Backoff; Warm-Board-Daemons
amortisieren Load+Fill über wiederholte Abfragen; ein stat-revalidierter
File-Cache killt redundante Reads. Verhaltensregeln gegen
Toolcall-Explosionen (nicht nach jeder Mini-Änderung rendern, Batch vor
Einzeln, Result lesen statt Rücklesen) stehen in [CLAUDE.md](CLAUDE.md) und in
jeder Mutations-Docstring.

Bekannte KiCad-10-Grenze: Eeschema hat kein IPC-Save/RunAction (KiCad #2077) —
Schaltplan-Edits laufen dateibasiert bei geschlossenem Eeschema, Live-ERC über
`kicad-cli`. Der 3D-Viewer hat keine API.

---

## Credits & Origins

This project builds on the work of others and adds significant new functionality:

### [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) — Foundation

The core server architecture, including FastMCP integration, async tool
registration, project discovery, DRC with history tracking, BOM analysis,
netlist extraction, circuit pattern recognition, and the security layer (path
validation, secure subprocess runner). Originally MIT licensed upstream; this
fork as a whole is GPL-3.0-or-later (see [License](#license)).

### [Seeed-Studio/kicad-mcp](https://github.com/nicholasgasior/kicad-mcp) — Inspiration

Conceptual model for ERC integration, schematic/PCB analysis tools, pin
function inference, and hierarchical sheet handling. The implementations here
are rewritten from scratch on our own S-expression parser.

### Built for This Project

Everything else: the KiCad chat plugin (docked panel, cross-probe links,
change receipts, super-feature bar, setup/diagnose, self-updater, warm-server
lifecycle), the S-expression engine, schematic/PCB generation from JSON,
ESPHome/LTspice converters, the PCB text-patcher + geometry/routing layers,
the schematic-patch layer, the IPC live bridge incl. sketch-layer tooling and
live diff, warm-board daemons, the semantic audit registry, the norm/physics
tools (`check_ampacity`, `run_spice_sim`, `get_safety_spacing` with dated
standards snapshots), footprint search, `pcb_render`, and the cross-environment
path layer (WSL ↔ Windows).

---

## Generation Input Format

The generation tools accept JSON strings. Example:

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
  "board": {"shape": "rectangle", "width": 50, "depth": 30, "layers": 2, "thickness": 1.6}
}
```

**Pin types:** `input`, `output`, `bidirectional`, `tri_state`, `passive`,
`power_in`, `power_out`, `open_collector`, `open_emitter`, `free`,
`unspecified`, `no_connect` · **Board shapes:** `rectangle`, `circle`,
`euro_divider` (`euro_type`: `3U`, `6U`, `half_euro`)

---

## ESPHome YAML to KiCad

Paste an ESPHome YAML config and get a complete KiCad project — ESP module
with correct pinout, sensors on their buses with pull-ups, decoupling caps per
IC, power nets, `.kicad_sch` + `.kicad_pcb` + `.kicad_pro`:

```yaml
esphome: {name: weather-station}
esp32: {board: esp32dev}
i2c: {sda: 21, scl: 22}
sensor:
  - {platform: bme280, address: 0x76}
  - {platform: bh1750, address: 0x23}
light:
  - {platform: neopixelbus, pin: GPIO16, num_leds: 30}
```

**Supported platforms:** BME280, BME680, DHT22, DS18B20, SHT31, BH1750,
VL53L0X, MPU6050, ADS1115, SSD1306, WS2812B — extendable via the component
database. Add `simulation=True` for SPICE-ready schematics.

---

## Process Supervisor (optional, stdio clients)

`_tasks/mcp_supervisor/` ships a thin JSON-RPC-transparent wrapper for
external stdio clients: auto-respawn on child crash + an injected
`restart_mcp_child` tool for wedged sessions. Point your client at
`start_mcp_supervisor.sh` instead of `start_mcp_wsl.sh`; details in
`_tasks/mcp_supervisor/README.md`. (The in-KiCad plugin doesn't need it — the
warm-server manager has its own health-check/restart cycle.)

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/          # ~2700 tests; KiCad-/kipy-gebundene skippen headless
pylint --rcfile=pyproject.toml --disable=C,R kicad_mcp tests   # CI-Gate: 0/0
sh scripts/setup-hooks.sh   # einmalig: pre-commit spiegelt kicad_mcp/ → plugin/mcp/
```

Konventionen für neue Tools (Pfad-Normalisierung, Registry, Batch-Companions,
Tests, Tool-Count-Wächter) stehen in [CLAUDE.md](CLAUDE.md). Logs:
`~/.kicad-mcp/logs/kicad-mcp.log`; IPC-Log neben dem Board
(`kicad_mcp_ipc.log`).

---

## License

**GPL-3.0-or-later** — see [LICENSE](LICENSE).

This project loads KiCad's `pcbnew` Python module in-process (for PCB geometry,
connectivity and via analysis). `pcbnew` is part of KiCad and licensed under
GPL-3.0; combining with it makes the project as a whole GPL-3.0-or-later. See
[NOTICE](NOTICE) for the rationale and third-party components.

Portions derive from the upstream [kicad-mcp](https://github.com/lamaalrajih/kicad-mcp)
by Lama Al Rajih, originally under the MIT License (GPL-compatible) — the
original notice is preserved in [LICENSE.MIT](LICENSE.MIT).
