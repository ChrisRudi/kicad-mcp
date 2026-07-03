# SPDX-License-Identifier: GPL-3.0-or-later
"""SPICE-Ausführung: ngspice im Batch-Modus für das Simulations-Super-Feature.

KiCad kann ngspice *starten*, aber weder die Frage noch das Ergebnis
interpretieren — das macht der Agent. Dieses Tool ist bewusst dumm: es führt
ein fertiges SPICE-Deck aus (der Agent baut es aus der Netzliste) und gibt die
rohen Werte + Fehler strukturiert zurück. Kein eigener Netlist-Builder, kein
Modell-Raten — Deck-Bau ist LLM-Arbeit, Ausführung ist dieses Tool.

ngspice-Discovery: ``KICAD_MCP_NGSPICE``-Env → ``ngspice`` im PATH → neben
``kicad-cli`` im KiCad-bin-Ordner (Windows-Installationen legen dort
``ngspice.exe`` ab, wenn vorhanden). Fehlt ngspice, kommt ein klarer
Installationshinweis statt eines Tracebacks.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Optional

from fastmcp import Context, FastMCP

from kicad_mcp.utils.path_env import kicad_paths, to_local_path

NGSPICE_ENV = "KICAD_MCP_NGSPICE"
OUTPUT_TAIL_CHARS = 4000

# "v(out) = 2.500000e+00" / "vout = 2.5" — ngspice op/print value lines.
_VALUE_RE = re.compile(
    r"^\s*([A-Za-z_][\w.()@\[\]#+-]*)\s*=\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*$")


def find_ngspice(_which=shutil.which) -> Optional[str]:
    """Locate the ngspice binary (env override → PATH → KiCad bin dir)."""
    override = os.environ.get(NGSPICE_ENV, "").strip()
    if override and os.path.isfile(override):
        return override
    for name in ("ngspice", "ngspice.exe"):
        found = _which(name)
        if found:
            return found
    cli = (kicad_paths() or {}).get("kicad_cli", "")
    if cli:
        bindir = os.path.dirname(cli)
        for name in ("ngspice.exe", "ngspice"):
            cand = os.path.join(bindir, name)
            if os.path.isfile(cand):
                return cand
    return None


def build_ngspice_cmd(ngspice: str, cir_path: str) -> list:
    """argv for one batch run (``-b``: run the deck, print, exit)."""
    return [ngspice, "-b", cir_path]


def parse_ngspice_output(text: str) -> dict[str, Any]:
    """Structure ngspice's batch output: value pairs, errors, warnings."""
    values: dict = {}
    errors: list = []
    warnings: list = []
    for line in (text or "").splitlines():
        m = _VALUE_RE.match(line)
        if m:
            try:
                values[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
            continue
        low = line.strip().lower()
        if low.startswith("error") or "fatal" in low:
            errors.append(line.strip())
        elif low.startswith("warning"):
            warnings.append(line.strip())
    return {"values": values, "errors": errors, "warnings": warnings}


def register_sim_tools(mcp: FastMCP) -> None:
    """Register SPICE simulation tools with the MCP server."""

    @mcp.tool()
    def run_spice_sim(
        netlist: str = "",
        netlist_path: str = "",
        timeout_s: float = 120.0,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Run a complete SPICE deck in ngspice batch mode and return values + errors.

        Use this when the user wants REAL simulation numbers (operating
        point, gain, corner frequency): build the deck from the schematic
        netlist (``extract_schematic_netlist``), include the analyses
        (``.op``/``.ac``/``.tran``) and ``.print``/``.measure`` lines, then
        execute here. The deck must be self-contained (models included or
        simplified) — this tool runs it verbatim, it does not fix decks.
        Rendert nicht; ändert nichts am Projekt.

        Args:
            netlist: The SPICE deck as text (preferred). Mutually exclusive
                with ``netlist_path``.
            netlist_path: Path to an existing ``.cir``/``.sp`` file to run.
            timeout_s: Kill the run after this many seconds (default 120).

        Returns:
            ``{success, ngspice, returncode, values: {name: number},
            errors: [..], warnings: [..], output}`` — ``values`` holds the
            printed results (e.g. ``v(out)``), ``output`` the tail of the raw
            log. Without ngspice installed: ``{success: False, error:
            "<Installationshinweis>"}``.
        """
        netlist_path = to_local_path(netlist_path) if netlist_path else ""
        if netlist_path and not os.path.isfile(netlist_path):
            return {"success": False,
                    "error": f"Netlist file not found: {netlist_path}"}
        if bool(netlist) == bool(netlist_path):
            return {"success": False,
                    "error": "Provide exactly one of netlist (text) or "
                             "netlist_path."}

        ngspice = find_ngspice()
        if not ngspice:
            return {"success": False, "error": (
                "ngspice nicht gefunden — Simulation braucht das "
                "ngspice-Binary. Installieren (Windows: ngspice.com, "
                "Linux: 'apt install ngspice') oder Pfad via "
                f"{NGSPICE_ENV} setzen. Alternative ohne ngspice: das Deck "
                "aus der Antwort in LTspice/KiCad-Simulator einfügen.")}

        cir_path = netlist_path
        tmp_file = None
        if not cir_path:
            fd, tmp_file = tempfile.mkstemp(suffix=".cir", prefix="kicad_mcp_")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(netlist if netlist.endswith("\n") else netlist + "\n")
            cir_path = tmp_file
        try:
            proc = subprocess.run(
                build_ngspice_cmd(ngspice, cir_path), capture_output=True,
                text=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired:
            return {"success": False, "ngspice": ngspice, "error": (
                f"ngspice-Abbruch nach {int(timeout_s)}s — Analyse zu groß "
                "oder Deck konvergiert nicht (.options / kleinere .tran "
                "probieren).")}
        except Exception as exc:
            return {"success": False, "ngspice": ngspice,
                    "error": f"ngspice-Start fehlgeschlagen: {exc}"}
        finally:
            if tmp_file:
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass

        output = (proc.stdout or "") + (proc.stderr or "")
        parsed = parse_ngspice_output(output)
        ok = proc.returncode == 0 and not parsed["errors"]
        result: dict[str, Any] = {
            "success": ok,
            "ngspice": ngspice,
            "returncode": proc.returncode,
            "values": parsed["values"],
            "errors": parsed["errors"],
            "warnings": parsed["warnings"],
            "output": output[-OUTPUT_TAIL_CHARS:],
        }
        if not ok and not parsed["errors"]:
            result["errors"] = [f"ngspice exit {proc.returncode}"]
        return result
