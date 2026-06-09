# KiCad 10 MCP Server

A comprehensive [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server for **KiCad 10** electronic design automation. Combines project analysis, validation, CLI-based exports, and — uniquely — **schematic and PCB generation from JSON specifications**.

Works with Claude Desktop, Claude Code, and any other MCP-compatible client.

> Deutsche Übersetzung dieser README: [README.de.md](README.de.md).

---

## What Makes This Different

No other KiCad MCP server can **generate** KiCad schematics and PCBs from a structured specification. Most servers only analyze existing files. This one creates them too.

It also converts **ESPHome YAML** configs directly into KiCad projects — sensors, buses, pull-ups, decoupling caps, all wired up automatically.

**91 tools** (plus `restart_mcp_child` from the optional supervisor wrapper, see below) across thirteen categories: analysis, validation, export, generation, ESPHome / LTspice conversion, autorouting, project management, diagnostics, and five headless / live-editing layers — PCB text-patcher (F8-equivalent without GUI), PCB geometry + routing, intelligent footprint search across the bundled KiCad library, IPC-API bridge for the running KiCad GUI (route, zone, foot­print pose, save / revert / DRC, schematic-job exports), and a **schematic-patch layer** for incremental editing of existing `.kicad_sch` files (add symbols, wires, labels with chip-aware outward stub + justify, connect pins with bbox + label collision avoidance, group transforms, free rotation, region/type-based delete, and pure-Python annotation).

---

## Credits & Origins

This project builds on the work of others and adds significant new functionality:

### [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) — Foundation

The core server architecture, including FastMCP integration, async tool registration, project discovery, DRC with history tracking, BOM analysis, netlist extraction, circuit pattern recognition, and the security layer (path validation, secure subprocess runner). Originally MIT licensed upstream; this fork as a whole is GPL-3.0-or-later (see [License](#license)).

**Tools from this base:** `list_projects`, `open_project`, `get_project_structure`, `validate_project`, `run_drc_check`, `get_drc_history_tool`, `analyze_bom`, `export_bom_csv`, `extract_project_netlist`, `extract_schematic_netlist`, `find_component_connections`, `analyze_schematic_connections`, `identify_circuit_patterns`, `analyze_project_circuit_patterns`, `generate_pcb_thumbnail`, `generate_project_thumbnail`

### [Seeed-Studio/kicad-mcp](https://github.com/nicholasgasior/kicad-mcp) — Inspiration

The Seeed-Studio server provided the conceptual model for ERC integration, schematic/PCB analysis tools, pin function inference, and hierarchical sheet handling. The implementations here are rewritten from scratch using our own S-expression parser instead of Seeed's parser dependencies.

**Concepts adopted:** ERC via kicad-cli, component listing with filters, pin function analysis (I2C/SPI/UART/GPIO detection), sub-sheet detection and root schematic redirection.

### New — Built for This Project

Everything below was developed specifically for this server:

**S-Expression Engine** (`kicad_mcp/utils/sexpr_parser.py`, `kicad_mcp/generators/sexpr.py`)
- Parser that reads any .kicad_sch / .kicad_pcb file into a Python tree
- Builder that writes indent-aware, KiCad-compatible S-expressions with deterministic UUIDs

**Schematic & PCB Generation** (`kicad_mcp/generators/`)
- `schematic_builder.py` — Generates .kicad_sch from parts + nets JSON: lib_symbols, symbol instances, global/local net labels
- `pcb_builder.py` — Generates .kicad_pcb: board outlines (rectangle, circle, Euro card), layer setup, footprint placement with pad-to-net assignment, GND copper pour, mounting holes, JLCPCB design rules
- `validator.py` — Validates input before generation: duplicate refs, pin types, dangling nets, pin conflicts, board constraints

**CLI Export Suite** (`kicad_mcp/tools/cli_export_tools.py`)
- 8 export tools wrapping kicad-cli: Gerber, drill, STEP, PDF, SVG, position, 3D render, board stats
- Automatic WSL path conversion for Windows kicad-cli.exe

**Analysis Tools** (`kicad_mcp/tools/schematic_tools.py`, `pcb_tools.py`, `pin_tools.py`, `erc_tools.py`)
- Schematic analysis: component listing, symbol details, regex search, metadata extraction
- PCB analysis: footprint listing, net analysis with track/via counts, track search by net
- Pin analysis: MCU family detection (ESP32, STM32, ATmega, nRF52, RP2040, SAMD), interface inference from net names
- ERC: kicad-cli integration with JSON output, severity filtering, sub-sheet handling

**ESPHome YAML to KiCad** (`kicad_mcp/generators/esphome_parser.py`, `kicad_mcp/tools/esphome_tools.py`)
- Parses ESPHome YAML and extracts ESP chip, I2C/SPI/UART buses, sensors, actuators
- Component database: BME280, BME680, DHT22, DS18B20, SHT31, BH1750, VL53L0X, MPU6050, ADS1115, SSD1306, WS2812B
- Auto-generates pull-up resistors for I2C, decoupling caps for every IC, power nets
- Supports ESP32, ESP32-S3, ESP32-C3, ESP8266
- Full round-trip: YAML in → .kicad_sch + .kicad_pcb + .kicad_pro out

**SPICE Simulation Support** (`kicad_mcp/generators/spice_models.py`)
- Automatic assignment of KiCad 10 simulation properties (Sim.Device, Sim.Params, Sim.Pins)
- Covers passives (R, C, L, D, LED), BJTs, MOSFETs, op-amps, voltage/current sources
- ICs get SUBCKT placeholders with auto-mapped pins
- Opt-in via `simulation=True` flag on generation tools

**MINT Wheel Integration** (in `MINT_Wheel/adapters/`)
- `electrical_adapter.py` uses kicad_mcp generators instead of the old kicad_gen.py
- `schematic_analysis_adapter.py` validates generated schematics against product.json spec

**Cross-Environment Support** (`kicad_mcp/utils/path_env.py`)
- Single source of truth for environment detection (Windows / WSL / Linux / macOS)
- Bidirectional path conversion (e.g. `/mnt/c/...` ↔ `C:\...`) so a WSL agent can talk to a Windows-side server transparently
- Every MCP tool that accepts a `pcb_path` / `schematic_path` / `project_path` / `output_path` parameter now normalizes it via `to_local_path()` at the function entry point — pass either WSL- or Windows-style paths, the server resolves them
- Centralised KiCad install discovery (`kicad_cli`, `footprints`, `symbols`, bundled Python) with `KICAD_BIN` / `KICAD_LIB_ROOT` / `KICAD_SYMBOL_ROOT` / `KICAD_PYTHON_PATH` overrides

**Code Quality**
- Pylint configuration centralized in `pyproject.toml` (`[tool.pylint."MESSAGES CONTROL"]`) with rationale per disabled cluster
- Repo-wide lint status: 0 errors / 0 warnings (130 Python files)

---

## Tool Catalog (91 Tools)

### Project Management
| Tool | Description |
|------|-------------|
| `list_projects` | Find KiCad projects on disk |
| `open_project` | Open a project |
| `get_project_structure` | Show project file structure |
| `validate_project` | Validate project integrity |
| `kicad_mcp_doctor` | One-shot health check (KiCad install, kicad-cli, kipy, footprint index, native libs) |

### Schematic Analysis
| Tool | Description |
|------|-------------|
| `list_schematic_components` | List components with type/value filters |
| `get_symbol_details` | Detailed info for a component by reference |
| `search_symbols` | Regex search across reference, value, library |
| `get_schematic_info` | Metadata, statistics, hierarchical sheets |
| `analyze_schematic_connections` | Connection analysis |
| `find_component_connections` | Trace component connections |

### PCB Analysis
| Tool | Description |
|------|-------------|
| `list_pcb_footprints` | Footprints with position, layer, pad count |
| `analyze_pcb_nets` | Net list with track/via counts per net |
| `find_tracks_by_net` | All track segments for a specific net |

### Pin & Pattern Analysis
| Tool | Description |
|------|-------------|
| `analyze_pin_functions` | Infer I2C, SPI, UART, GPIO, ADC, PWM, USB, JTAG, CAN from net names |
| `detect_pin_conflicts` | Find potential pin conflicts |
| `identify_circuit_patterns` | Recognize common circuit topologies |
| `analyze_project_circuit_patterns` | Project-wide pattern analysis |

### Validation
| Tool | Description |
|------|-------------|
| `run_erc` | Electrical Rules Check via kicad-cli (JSON output) |
| `get_erc_violations` | Filtered ERC results by severity |
| `run_drc_check` | Design Rules Check with history tracking |
| `get_drc_history_tool` | DRC trend over time |
| `validate_design` | Validate a JSON design spec without generating files |

### Export (via kicad-cli)
| Tool | Description |
|------|-------------|
| `export_gerbers` | Gerber manufacturing files |
| `export_drill` | Drill files |
| `export_step` | 3D STEP model |
| `export_pdf` | PDF (schematic or PCB) |
| `export_svg` | SVG (schematic or PCB) |
| `export_png` | PNG raster (cairosvg pipeline; auto-bootstraps the KiCad cairo-2 DLL on Windows) |
| `export_pos` | Component position / pick-and-place |
| `render_3d` | 3D render (PNG, any angle) |
| `get_board_stats` | Board statistics as JSON |
| `generate_pcb_thumbnail` | Quick PCB preview |
| `generate_project_thumbnail` | Project preview |
| `analyze_bom` / `export_bom_csv` | Bill of Materials |
| `extract_project_netlist` / `extract_schematic_netlist` | Netlist extraction |

### Generation (Unique to This Server)
| Tool | Description |
|------|-------------|
| `generate_project` | Complete .kicad_pro + .kicad_sch + .kicad_pcb from JSON |
| `generate_schematic` | Schematic only |
| `generate_pcb` | PCB only |
| `generate_from_netlist` | Generate `.kicad_sch` + `.kicad_pcb` from a `.net` netlist (KiCad sexpr) |

### ESPHome Conversion (Unique to This Server)
| Tool | Description |
|------|-------------|
| `esphome_to_kicad` | Convert ESPHome YAML to complete KiCad project (schematic + PCB) |
| `list_esphome_components` | Show all supported ESPHome platforms with KiCad mappings |

### LTspice Conversion
| Tool | Description |
|------|-------------|
| `convert_ltspice_to_kicad` | Convert an LTspice `.asc` schematic into a KiCad project (schematic + PCB stub) |

### PCB Patch (Headless, no SWIG, no KiCad GUI)
| Tool | Description |
|------|-------------|
| `patch_pcb_nets_from_netlist` | F8-equivalent net-tagging — preserves existing PCB net indices, only appends missing nets |
| `resolve_pcb_footprints` | Replace `[lib:fp]`-tagged placeholders with the real `.kicad_mod` from the library |
| `validate_footprints` | Cross-check schematic pin assignments against actual PCB pad names; reports mismatches before routing |
| `rotate_pcb` | Rotate the entire board around `(0,0)` (uses pcbnew API; no FootprintLoad involved) |

### PCB Geometry & Routing (Headless)
| Tool | Description |
|------|-------------|
| `compute_pad_world_positions` | Absolute world coordinates of every pad, accounting for footprint rotation and `B.Cu` flip |
| `add_track_to_pcb` | Insert a pad-to-pad track (optional layer-change via) with flip-aware geometry |
| `add_zone_pour_to_pcb` | Add a copper-pour zone bound to a net on a copper layer |

### Footprint Search (Bundled KiCad Library Index)
| Tool | Description |
|------|-------------|
| `index_kicad_footprints` | Build / refresh the on-disk JSON index over `share/kicad/footprints/*.pretty/*.kicad_mod` |
| `search_footprints` | Fuzzy + substring search across all indexed footprint names |
| `find_footprint_by_specs` | Filter by pad count, package family, body bounding box; rank by body-size proximity |
| `suggest_builtin_for_custom` | Given a custom `.kicad_mod`, suggest the closest built-in alternatives with confidence scores |

### IPC API (Live KiCad GUI Bridge)
| Tool | Description |
|------|-------------|
| `ipc_check_status` | Diagnose: kipy installed? KiCad reachable? Board open? |
| `ipc_install_kipy` | Auto-install the `kicad-python` client into the active interpreter |
| `ipc_get_open_documents` | List schematics + PCBs currently open in KiCad |
| `ipc_get_pad_world_pos` | Read world coordinates of any pad via the IPC API (bypasses SWIG) |
| `ipc_set_footprint_pose` | Move / rotate a footprint live (absolute or delta; one undo step in KiCad's history) |
| `ipc_route_pin_to_pin` | Add a track (and optional via) between two pads — live in the running PCB editor |
| `ipc_add_zone_pour` | Add a copper-pour zone bound to a net + polygon |
| `ipc_route_power_ring` | Convenience: wide power track through a sequence of components |
| `ipc_save` | Direct `SaveDocument` IPC command (PCB only — silent, no confirm dialog) |
| `ipc_save_all` | Save every open document the IPC bridge can reach |
| `ipc_revert` | Direct `RevertDocument` IPC command (PCB only — silent, clears modified flag before reload) |
| `ipc_save_via_action` | Fallback Save via `RunAction("common.Control.save")` — works for SCH, but pops the "Save?" dialog |
| `ipc_revert_via_action` | Fallback Revert via `RunAction("common.Control.revert")` — works for SCH, pops dialog |
| `ipc_run_drc` | Run DRC on the live PCB via the IPC API (PCB only) |
| `ipc_run_erc` | Stub. Eeschema does not register `RunAction` in 10.0.x — use `run_erc` (CLI-based) instead. Tracking: KiCad #2077 |
| `ipc_export_schematic` | Export the **live** schematic (unsaved in-memory state) to disk — formats: `svg`, `pdf`, `dxf`, `ps`, `netlist`, `bom`. Wraps `RunSchematicJobExport*`. Combine with `run_erc` for live-state ERC |

### Schematic Patch (Incremental, Headless)
| Tool | Description |
|------|-------------|
| `compute_pin_world_positions_sch` | Pin world coordinates of every placed symbol (rotation/mirror-aware) |
| `list_schematic_groups` | Enumerate the `kicad-mcp.group` IDs present on the schematic |
| `get_schematic_bbox` | Bounding box (mm) for refs / a group / the whole schematic |
| `add_schematic_symbols` | Bulk-insert symbols into existing `.kicad_sch` (auto-resolves and embeds `lib_symbols`; auto-snaps `_Small`/`Device:C/R/L/CP` centres to the half-grid so pins land on 2.54 mm) |
| `add_schematic_wire` | Insert wire segments at arbitrary angles |
| `add_schematic_label` | Insert local / global / hierarchical labels (with optional `justify="left"/"right"`) |
| `connect_pins` | Pin-to-pin Manhattan-routed wires, or global-label pairs (`mode="label"`) |
| `validate_schematic_patch` | Pre-flight: ref collisions, missing lib_ids |
| `annotate_schematic` | Pure-Python annotator: assigns sequential numbers to `R?` / `C?` / non-conforming `#PWR_*` refs (gap-fill; `force_renumber` for full re-annotate). Updates both `(property "Reference" …)` and nested `(reference "X")` instance entries. |
| `move_schematic_group` | Translate every symbol tagged with a group id |
| `rotate_schematic_group` | Rigid rotation around centroid / custom pivot — free angle with 90° snap + tolerance/`force=True` override |
| `delete_schematic_items` | Remove items by `refs`, by `group_id`, or by `types` + `region` (mm bbox) — labels/wires/junctions can be region-deleted even though they carry no group tag |

### Internal Diagnostics & Benchmarks
| Tool | Description |
|------|-------------|
| `benchmark_schematic` | Generate a schematic from `parts` + `nets` and emit quality metrics (wire-to-label ratio, score) — used by the iterative-improvement loop |
| `benchmark_loop` | Full benchmark cycle: generate project, export SVG, score, report |

---

## Quick Start

### Prerequisites

- **KiCad 10.0** (the server runs under KiCad's bundled Python — no separate
  Python install needed)
- Any MCP client (Claude Code, Claude Desktop, Cursor, Windsurf, VS Code, …)

### Install (one-shot)

```bash
git clone <this-repo>
cd kicad-mcp

# Linux / WSL / macOS:
./install.sh

# Windows (PowerShell):
.\install.ps1
```

The installer (a) verifies KiCad 10 is reachable, (b) registers the server
with Claude Code if the `claude` CLI is installed, and (c) prints ready-to-
paste JSON for every other client.

If KiCad 10 isn't found, the installer stops with a clear error. Set
`KICAD_PYTHON_PATH` to the absolute path of `python.exe` inside your
KiCad `bin/` directory as a workaround.

### Configure other MCP clients

The installer prints ready-to-paste JSON snippets for Claude Desktop,
Cursor, Windsurf, VS Code, Continue.dev, and Zed at the end of its run.
Each entry uses `start_mcp_wsl.sh` (Linux/WSL/macOS) or `start_mcp.bat`
(Windows) as the launch command and points at this directory.

### Optional: `.env`

Per-project settings (search paths, custom CLI path) go in `.env` next to
`main.py`:

```
KICAD_SEARCH_PATHS=~/Documents/KiCad,~/Projects
# KICAD_CLI_PATH only needed if auto-detection fails
```

### Logs

Server logs are written to `~/.kicad-mcp/logs/kicad-mcp.log`. The full path
is printed to stderr on every launch.

### Run manually (for debugging)

```bash
# Linux / WSL:
./start_mcp_wsl.sh

# Windows:
start_mcp.bat
```

---

## Process Supervisor (Crash Recovery + In-Band Restart)

`_tasks/mcp_supervisor/` ships an optional thin Python wrapper that sits
between the host (Claude Code, Cline, etc.) and the real `kicad-mcp`
stdio server. Speaks pure JSON-RPC newline-framed stdio in both
directions — **no protocol extension, fully MCP-spec-compatible**.

What it adds:
* **Auto-respawn on crash.** Child segfault / OOM / `os._exit` → supervisor
  detects EOF, sends synthetic `-32603` to any pending request, spawns a
  new child. Host stays connected.
* **`restart_mcp_child` tool**, injected into every `tools/list` reply.
  Forces a clean kill+respawn (~5 ms). Use when the child is alive but
  logically wedged: stale kipy session, leaked `pcbnew.BOARD`, hung KiCad
  IPC. Callable from any MCP client like any other tool.

Activate by pointing your client config at the wrapper instead of the
direct script:

```jsonc
"kicad": {
  "type": "stdio",
  "command": "bash",
  "args": ["/path/to/kicad-mcp/_tasks/mcp_supervisor/start_mcp_supervisor.sh"]
}
```

To revert: change `args[0]` back to `start_mcp_wsl.sh`. The supervisor
leaves no state behind (no daemons, no sockets, no PID files).

End-to-end tests:

```bash
python3 _tasks/mcp_supervisor/test_supervisor.py            # ~1 s,  fake child
python3 _tasks/mcp_supervisor/test_with_real_kicad.py       # ~30 s, real kicad-mcp
```

Both should print `ALL OK`. Details: `_tasks/mcp_supervisor/README.md`.

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
  "board": {
    "shape": "rectangle",
    "width": 50,
    "depth": 30,
    "layers": 2,
    "thickness": 1.6
  }
}
```

**Pin types:** `input`, `output`, `bidirectional`, `tri_state`, `passive`, `power_in`, `power_out`, `open_collector`, `open_emitter`, `free`, `unspecified`, `no_connect`

**Board shapes:** `rectangle`, `circle`, `euro_divider` (with `euro_type`: `3U`, `6U`, `half_euro`)

---

## ESPHome YAML to KiCad

Paste an ESPHome YAML config and get a complete KiCad project:

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

The tool automatically generates:
- **ESP32-WROOM-32E** with correct pinout
- **BME280** + **BH1750** on I2C bus with pull-ups (4.7k)
- **WS2812B** NeoPixel connector on GPIO16
- **100nF decoupling caps** for every IC
- Motion sensor GPIO connection
- All power nets (3V3, GND) properly connected
- Complete .kicad_sch + .kicad_pcb + .kicad_pro

**Supported platforms:** BME280, BME680, DHT22, DS18B20, SHT31, BH1750, VL53L0X, MPU6050, ADS1115, SSD1306, WS2812B — more can be added to the component database.

Add `simulation=True` to include SPICE model assignments for simulation-ready schematics.

---

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v --no-cov
# 225 tests passing (1 environment-dependent test fails when KiCad IPC bus
# is not reachable — the test asserts a specific error string that diverges
# from the live "Connection refused" message; expected for CI without KiCad)
```

---

## Project Structure

```
kicad-mcp/
  main.py                           Entry point
  pyproject.toml                    Dependencies & config
  kicad_mcp/
    server.py                       FastMCP server (91 tools registered)
    config.py                       Platform-specific KiCad paths
    context.py                      Lifespan management
    tools/                          MCP tool implementations
      erc_tools.py                  ERC via kicad-cli
      cli_export_tools.py           9 export tools (gerber/drill/step/pdf/svg/png/pos/3d/board-stats)
      schematic_tools.py            4 schematic analysis tools
      pcb_tools.py                  3 PCB analysis tools
      pin_tools.py                  2 pin analysis tools
      generation_tools.py           7 generation + benchmark tools
      esphome_tools.py              2 ESPHome conversion tools
      ltspice_tools.py              1 LTspice → KiCad converter
      ipc_tools.py                  17 IPC live-bridge tools (route / zone / pose / save / revert / DRC / live SCH export)
      sch_patch_tools.py            12 schematic-patch tools (Phase S, incl. annotate_schematic)
      pcb_patch_tools.py            4 PCB text-patcher tools (Phase A)
      pcb_geometry_tools.py         3 PCB geometry / routing tools (Phase E)
      footprint_search_tools.py     4 footprint-library search tools (Phase D)
      [+ base: drc, bom, netlist, pattern, export, project, analysis, validation]
    generators/                     Generation engine
      sexpr.py                      S-Expression builder
      schematic_builder.py          .kicad_sch generator
      pcb_builder.py                .kicad_pcb generator
      validator.py                  Input validation
      spice_models.py               SPICE model auto-assignment
      esphome_parser.py             ESPHome YAML → parts/nets converter
    utils/
      sexpr_parser.py               S-Expression parser
      wsl_path.py                   WSL path conversion
      kicad_cli.py                  CLI detection & execution
    resources/                      MCP resources (read-only)
    prompts/                        MCP prompt templates
  tests/                            225 unit tests (all pass; 1 fails only when KiCad IPC bus is unreachable)
```

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
