# SPDX-License-Identifier: GPL-3.0-or-later
"""SPICE-Ausführung: ngspice im Batch-Modus für das Simulations-Super-Feature.

KiCad kann ngspice *starten*, aber weder die Frage noch das Ergebnis
interpretieren — das macht der Agent. Dieses Tool ist bewusst dumm: es führt
ein fertiges SPICE-Deck aus (der Agent baut es aus der Netzliste) und gibt die
rohen Werte + Fehler strukturiert zurück. Kein eigener Netlist-Builder, kein
Modell-Raten — Deck-Bau ist LLM-Arbeit, Ausführung ist dieses Tool.

Zwei Backends, in dieser Reihenfolge:
1. **ngspice-CLI** (``KICAD_MCP_NGSPICE``-Env → PATH → neben ``kicad-cli``) —
   klassischer ``-b``-Batch-Lauf.
2. **KiCads mitgeliefertes libngspice** (die Bibliothek, mit der Eeschemas
   eigener Simulator läuft: ``libngspice-0.dll`` im KiCad-bin) — geladen per
   ctypes in einem ISOLIERTEN Kindprozess, damit ein Konvergenz-Absturz der
   Bibliothek nie den (warmen) MCP-Server mitreißt. Damit braucht Simulation
   auf einer normalen KiCad-Installation KEINE Extra-Software. Warum nicht
   Eeschemas Simulator direkt? Eeschema hat in KiCad 10 keine IPC-API und
   kicad-cli kein sim-Kommando — die GUI-Simulation ist für Automation
   unerreichbar, ihre Bibliothek nicht.
Fehlt beides, kommt ein klarer Installationshinweis statt eines Tracebacks.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
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


LIBNGSPICE_ENV = "KICAD_MCP_LIBNGSPICE"

_LIB_CANDIDATES = ("libngspice-0.dll", "ngspice.dll", "libngspice.so.0",
                   "libngspice.so", "libngspice.0.dylib", "libngspice.dylib")


def find_libngspice() -> Optional[str]:
    """KiCads mitgelieferte ngspice-Bibliothek (oder eine System-Instanz).

    Env-Override → KiCad-bin (neben ``kicad-cli``) und dessen ``../lib`` →
    ``ctypes.util.find_library``. Windows-KiCad legt ``libngspice-0.dll``
    direkt ins bin/ (Eeschemas Simulator nutzt genau diese Datei).
    """
    override = os.environ.get(LIBNGSPICE_ENV, "").strip()
    if override and os.path.isfile(override):
        return override
    cli = (kicad_paths() or {}).get("kicad_cli", "")
    if cli:
        bindir = os.path.dirname(cli)
        for root in (bindir, os.path.join(os.path.dirname(bindir), "lib")):
            for name in _LIB_CANDIDATES:
                cand = os.path.join(root, name)
                if os.path.isfile(cand):
                    return cand
    try:
        import ctypes.util
        found = ctypes.util.find_library("ngspice")
    except Exception:
        found = None
    return found or None


# Kindprozess-Runner für libngspice: ctypes-Load, Deck laden, "run", skalare
# Vektoren des Ergebnis-Plots als JSON zurück. Als ISOLIERTER Prozess, weil
# (a) ngspice bei bösen Decks abstürzen kann (darf nie den Server töten) und
# (b) die Bibliothek pro Prozess nur EINE Sitzung sauber unterstützt.
# ControlledExit-Callback ist Pflicht: ohne ihn beendet ngspice bei .end/quit
# den GANZEN Prozess über exit().
_LIB_RUNNER = r'''
import ctypes, json, sys
lib_path = sys.argv[1]
deck = sys.stdin.read()
out_lines = []
SENDCHAR = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                            ctypes.c_void_p)
EXITCB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_bool,
                          ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)

def _collect(msg, _id, _user):
    try:
        out_lines.append((msg or b"").decode("utf-8", "replace"))
    except Exception:
        pass
    return 0

send_char = SENDCHAR(_collect)
send_stat = SENDCHAR(lambda m, i, u: 0)
no_exit = EXITCB(lambda status, imm, quit_, _id, _u: 0)

lib = ctypes.CDLL(lib_path)
lib.ngSpice_Init(send_char, send_stat, no_exit, None, None, None, None)
lines = deck.splitlines() or ["* leer"]
arr = (ctypes.c_char_p * (len(lines) + 1))()
for i, line in enumerate(lines):
    arr[i] = line.encode("utf-8", "replace")
arr[len(lines)] = None
rc_circ = lib.ngSpice_Circ(arr)
rc_run = lib.ngSpice_Command(b"run")

class VecInfo(ctypes.Structure):
    _fields_ = [("v_name", ctypes.c_char_p), ("v_type", ctypes.c_int),
                ("v_flags", ctypes.c_short),
                ("v_realdata", ctypes.POINTER(ctypes.c_double)),
                ("v_compdata", ctypes.c_void_p), ("v_length", ctypes.c_int)]

lib.ngSpice_CurPlot.restype = ctypes.c_char_p
lib.ngSpice_AllVecs.restype = ctypes.POINTER(ctypes.c_char_p)
lib.ngSpice_AllVecs.argtypes = [ctypes.c_char_p]
lib.ngSpice_Get_Vec_Info.restype = ctypes.POINTER(VecInfo)
lib.ngSpice_Get_Vec_Info.argtypes = [ctypes.c_char_p]
values = {}
try:
    plot = lib.ngSpice_CurPlot() or b""
    vecs = lib.ngSpice_AllVecs(plot)
    i = 0
    while vecs and vecs[i]:
        name = vecs[i].decode("utf-8", "replace")
        full = plot.decode("utf-8", "replace") + "." + name
        info = lib.ngSpice_Get_Vec_Info(full.encode("utf-8"))
        if info and info.contents.v_length == 1 and info.contents.v_realdata:
            values[name] = info.contents.v_realdata[0]
        i += 1
except Exception as exc:  # Vektoren sind Bonus — Output reicht als Fallback
    out_lines.append("Warning: vector read failed: %s" % exc)
print(json.dumps({"rc_circ": int(rc_circ), "rc_run": int(rc_run),
                  "values": values,
                  "output": "\n".join(out_lines)[-8000:]}))
'''


def run_libngspice(lib_path: str, deck: str, timeout_s: float,
                   _run=subprocess.run) -> dict[str, Any]:
    """Ein Deck über KiCads libngspice im Kindprozess ausführen.

    Rückgabe wie das JSON des Runners (``rc_circ``/``rc_run``/``values``/
    ``output``) plus ``error`` bei Start-/Parse-Problemen. Nie raisen.
    """
    try:
        proc = _run([sys.executable, "-c", _LIB_RUNNER, lib_path],
                    input=deck if deck.endswith("\n") else deck + "\n",
                    capture_output=True, text=True, timeout=timeout_s,
                    check=False)
    except subprocess.TimeoutExpired:
        return {"error": (f"libngspice-Abbruch nach {int(timeout_s)}s — "
                          "Analyse zu groß oder Deck konvergiert nicht.")}
    except Exception as exc:
        return {"error": f"libngspice-Start fehlgeschlagen: {exc}"}
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        tail = (proc.stderr or "")[-500:]
        return {"error": f"libngspice-Runner starb (exit {proc.returncode}): "
                         f"{tail}"}
    try:
        return json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception:
        return {"error": "libngspice-Runner lieferte kein JSON.",
                "output": (proc.stdout or "")[-2000:]}


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

        deck_text = netlist
        if netlist_path and not deck_text:
            with open(netlist_path, encoding="utf-8", errors="replace") as fh:
                deck_text = fh.read()

        ngspice = find_ngspice()
        if not ngspice:
            # Backend 2: KiCads mitgeliefertes libngspice (Eeschemas eigener
            # Simulator-Kern) — keine Extra-Installation nötig.
            lib_path = find_libngspice()
            if lib_path:
                res = run_libngspice(lib_path, deck_text, timeout_s)
                if res.get("error") and "values" not in res:
                    return {"success": False, "ngspice": lib_path,
                            "backend": "libngspice",
                            "error": res["error"],
                            "output": res.get("output", "")}
                output = res.get("output", "")
                parsed = parse_ngspice_output(output)
                values = dict(parsed["values"])
                values.update(res.get("values") or {})
                ok = not parsed["errors"] and int(res.get("rc_circ", 0)) >= 0
                result: dict[str, Any] = {
                    "success": ok and bool(values or not parsed["errors"]),
                    "ngspice": lib_path,
                    "backend": "libngspice (KiCad)",
                    "returncode": int(res.get("rc_run", 0)),
                    "values": values,
                    "errors": parsed["errors"],
                    "warnings": parsed["warnings"],
                    "output": output[-OUTPUT_TAIL_CHARS:],
                }
                return result
            return {"success": False, "error": (
                "Kein SPICE-Backend gefunden: weder ein ngspice-Binary "
                f"(PATH/{NGSPICE_ENV}) noch KiCads libngspice-Bibliothek "
                f"(KiCad-bin/{LIBNGSPICE_ENV}). Auf einer normalen "
                "KiCad-10-Installation liegt libngspice im bin-Ordner — "
                "prüfe KICAD_BIN. Alternative: das Deck aus der Antwort in "
                "LTspice/den KiCad-Simulator einfügen.")}

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
            "backend": "ngspice-cli",
            "returncode": proc.returncode,
            "values": parsed["values"],
            "errors": parsed["errors"],
            "warnings": parsed["warnings"],
            "output": output[-OUTPUT_TAIL_CHARS:],
        }
        if not ok and not parsed["errors"]:
            result["errors"] = [f"ngspice exit {proc.returncode}"]
        return result
