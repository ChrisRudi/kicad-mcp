# kicad-mcp 1.0.0 — first public release

An MCP (Model Context Protocol) server that gives Claude — or any MCP client — **147 tools** to read, analyze, generate and **edit** KiCad schematics and PCBs, headless and without the GUI. Runs under KiCad 10's bundled Python (`pcbnew` + `kipy`).

## Highlights
- **147 tools** spanning the whole KiCad workflow — analysis, headless editing, live GUI editing, generation and review.
- **Headless, format-preserving editing** — surgical text-patches of `.kicad_pcb` / `.kicad_sch` (an F8-equivalent net sync, footprint placement, tracks/vias/zones/arcs), correct flip/rotation/arc math, batchable in one open/write round (`pcb_batch`).
- **Live editing of a running KiCad** via the IPC API (`kipy`) — write tracks/vias/zones into the open editor, plus a pull-only live diff that attributes agent vs. user changes.
- **Warm `pcbnew` daemons** — `pcb_eval`, `check_connectivity` and `via_promote` keep loaded + zone-filled boards in memory (cached by path+mtime), so repeated queries on a large board are fast instead of paying a cold load+fill each call.
- **Robust by design** — every path normalised across WSL ↔ Windows; a locked tool-count + dynamic registry audit guard against drift; **1576 tests** pass under KiCad's Python.

## What's inside (tool families)
- **Analysis** — footprints, nets, pins (MCU/bus inference), circuit-pattern recognition, BOM, netlist extraction.
- **Headless patch** — PCB net/footprint sync, schematic symbol/wire/label/group editing, power-symbol conversion.
- **Geometry & routing** — world coordinates, tracks/zones/arcs, `polar_grid` for circular boards, `via_promote` (blind/buried → through optimiser), `pcb_render` (see a layout region as PNG).
- **Live (IPC)** — pose/route/zone in the running editor, live diff, live ERC export.
- **Generation** — projects/schematics/PCBs from specs or netlists; ESPHome-YAML and LTspice converters; datasheet circuit-block composition and per-IC review material.
- **Verification** — headless ERC/DRC (kicad-cli), connectivity / ratsnest with "is this via load-bearing?".

## Requirements
- **KiCad 10.0** (uses its bundled Python: `pcbnew`, `kipy` 0.7.1).
- Optional: `pdfplumber` for datasheet-table extraction.

## Install & run
```bash
pip install -e .                 # under KiCad's bundled Python
# then start the server:
start_mcp.bat                    # Windows
./start_mcp_wsl.sh               # WSL / Linux / macOS
```

## License
**GPL-3.0-or-later.** This project loads KiCad's `pcbnew` (GPL-3.0) in-process, so the combined work is GPL. It is a fork of the MIT-licensed [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) — that original notice is preserved in `LICENSE.MIT`, and the rationale + third-party components are documented in `NOTICE`.

## Credits
Built on the foundation of [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) (MIT). Full change history in [`CHANGELOG.md`](CHANGELOG.md).
