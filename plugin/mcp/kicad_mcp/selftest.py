# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone-Systemtest OHNE Claude: ``python -m kicad_mcp.selftest``.

Der E2E-Lauf (Plugin, 🧪) testet das *Agent-Verhalten* — teuer (Kontingent),
langsam, braucht Claude. DIESER Test prüft die Produkt-*Maschinerie* in
Sekunden bis wenigen Minuten, komplett lokal: Er generiert sich sein eigenes
Demo-Projekt aus der gebündelten Spec (``resources/data/selftest_board.json``,
Spec → Schaltplan → Board) und schickt es durch die ECHTEN MCP-Tools
(Tool-Registry, Generatoren, Parser, Patch, Geometrie; pcbnew-Connectivity
und kicad-cli-DRC, wo verfügbar; zum Schluss der echte stdio-Handshake).

Orchestrierbar auf N Rechnern: kein Prompt, keine GUI, Interaktion nur bei
Fehlern — bei Erfolg EINE Zeile auf stdout, Exit-Code 0; bei Fehlern die
FAIL-Schritte + Exit-Code 1. Maschinenlesbarer Report (JSON + MD) landet im
``--out``-Verzeichnis. Beispiel-Fernaufruf::

    "<KiCad-Python>" -m kicad_mcp.selftest --out C:\\temp\\selftest

Der 🔬-Knopf im Einrichtungs-Fenster des Plugins ruft genau dieses Modul auf.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import warnings
from typing import Any, Callable, List, Optional


def peak_ram_mb() -> Optional[float]:
    """Peak-RAM (RSS/Working Set) dieses Prozesses in MB, plattformneutral.

    Beantwortet „warum braucht der Systemtest so viel Speicher?" mit einer
    MESSUNG im Report statt einer Vermutung. Best-effort: None, wenn die
    Plattform keinen billigen Weg bietet."""
    try:
        if os.name == "nt":
            import ctypes
            import ctypes.wintypes as wt

            class _PMC(ctypes.Structure):
                _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(pmc), pmc.cb)
            return round(pmc.PeakWorkingSetSize / 1e6, 1) if ok else None
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux: KB, macOS: Bytes
        return round(peak / (1e3 if sys.platform != "darwin" else 1e6), 1)
    except Exception:
        return None

SPEC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "resources", "data", "selftest_board.json")
HANDSHAKE_TIMEOUT_S = 120.0  # Kaltstart (pandas + 186 Tools) — großzügig

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class SkipStep(Exception):
    """Eine Voraussetzung fehlt (pcbnew, kicad-cli) — kein Fehler."""


def _call(server, name: str, args: dict) -> dict:
    """Ein MCP-Tool in-process aufrufen (fastmcp 3: ToolResult)."""
    result = asyncio.run(server.call_tool(name, args))
    data = result.structured_content
    return data if isinstance(data, dict) else {"raw": data}


def _expect_success(data: dict, what: str) -> dict:
    if not data.get("success", False):
        raise AssertionError(f"{what}: {data.get('error') or data}")
    return data


def load_spec(spec_path: Optional[str] = None) -> dict:
    with open(spec_path or SPEC_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# -- Schritte --------------------------------------------------------------------
# Jeder Schritt: fn(ctx) -> detail-dict (oder wirft; SkipStep = übersprungen).
# ctx sammelt server/spec/pfade über die Schritte hinweg.

def _step_server_tools(ctx: dict) -> dict:
    from kicad_mcp.server import create_server
    ctx["server"] = create_server()
    tools = asyncio.run(ctx["server"].list_tools())
    count = len(tools)
    if count < 100:  # grobe Untergrenze — eine kaputte Registry fällt hier um
        raise AssertionError(f"nur {count} Tools registriert")
    return {"tools": count}


def _step_generate_project(ctx: dict) -> dict:
    spec = ctx["spec"]
    out = _expect_success(_call(ctx["server"], "generate_project", {
        "output_dir": ctx["project_dir"],
        "parts": json.dumps(spec["parts"]),
        "nets": json.dumps(spec["nets"]),
        "board": json.dumps(spec.get("board") or {}),
        "project_name": spec.get("project_name", "kicad_mcp_selftest"),
    }), "generate_project")
    files = out.get("files") or {}
    for key in ("schematic", "pcb", "project"):
        path = files.get(key, "")
        if not (path and os.path.isfile(path)):
            raise AssertionError(f"{key}-Datei fehlt: {path!r}")
    ctx["sch_path"] = files["schematic"]
    ctx["pcb_path"] = files["pcb"]
    return {"files": files, "erc_clean": out.get("erc_clean")}


def _step_read_schematic(ctx: dict) -> dict:
    out = _expect_success(_call(ctx["server"], "list_schematic_components", {
        "schematic_path": ctx["sch_path"]}), "list_schematic_components")
    want = len(ctx["spec"]["parts"])
    got = len(out.get("components") or []) or out.get("count", 0)
    if got < want:
        raise AssertionError(f"{got} Symbole gelesen, {want} erwartet")
    return {"components": got}


def _step_netlist(ctx: dict) -> dict:
    out = _expect_success(_call(ctx["server"], "extract_schematic_netlist", {
        "schematic_path": ctx["sch_path"], "summary_only": True}),
        "extract_schematic_netlist")
    return {k: out.get(k) for k in ("component_count", "net_count") if k in out}


def _step_read_pcb(ctx: dict) -> dict:
    out = _expect_success(_call(ctx["server"], "list_pcb_footprints", {
        "pcb_path": ctx["pcb_path"]}), "list_pcb_footprints")
    want = len(ctx["spec"]["parts"])
    got = out.get("count", 0)
    if got < want:
        raise AssertionError(f"{got} Footprints gelesen, {want} erwartet")
    return {"footprints": got, "backend": out.get("backend")}


def _step_pad_geometry(ctx: dict) -> dict:
    ref = ctx["spec"]["parts"][1]["ref"]  # U1 — Footprint mit mehreren Pads
    out = _expect_success(_call(ctx["server"], "compute_pad_world_positions", {
        "pcb_path": ctx["pcb_path"], "refs": ref}),
        "compute_pad_world_positions")
    n = out.get("pad_count", 0)
    if n < 1:
        raise AssertionError(f"keine Pad-Koordinaten für {ref}")
    return {"ref": ref, "pads": n}


def _step_patch_via(ctx: dict) -> dict:
    out = _expect_success(_call(ctx["server"], "add_via_to_pcb", {
        "pcb_path": ctx["pcb_path"], "x_mm": 5.0, "y_mm": 5.0,
        "net_name": "GND"}), "add_via_to_pcb")
    with open(ctx["pcb_path"], encoding="utf-8") as fh:
        if "(via" not in fh.read():
            raise AssertionError("Via nicht in der .kicad_pcb gelandet")
    return {k: out.get(k) for k in ("net", "position", "success") if k in out}


def _step_connectivity(ctx: dict) -> dict:
    try:
        import pcbnew  # noqa: F401  pylint: disable=unused-import
    except Exception as exc:
        raise SkipStep(f"pcbnew nicht importierbar ({exc}) — Schritt braucht "
                       "KiCads Python") from exc
    out = _expect_success(_call(ctx["server"], "check_connectivity", {
        "pcb_path": ctx["pcb_path"]}), "check_connectivity")
    return {k: out.get(k) for k in ("unconnected_count", "net_count")
            if k in out}


def _step_kicad_cli_drc(ctx: dict) -> dict:
    import shutil
    cli = (shutil.which("kicad-cli") or shutil.which("kicad-cli.exe"))
    if not cli:
        raise SkipStep("kicad-cli nicht im PATH")
    out = _call(ctx["server"], "run_drc_check",
                {"project_path": ctx["pcb_path"]})
    # DRC-Verstöße auf dem ungerouteten Demo-Board sind ERWARTET — der
    # Schritt prüft, dass die kicad-cli-Kette LÄUFT, nicht dass sie 0 meldet.
    if "success" not in out:
        raise AssertionError(f"run_drc_check ohne success-Feld: {out}")
    return {"ran": True, "success": out.get("success"),
            "violations": out.get("violation_count", out.get("violations"))}


def _step_stdio_handshake(ctx: dict) -> dict:
    """Der Ernstfall: startet der Server so, wie Claude ihn startet?"""
    mcp_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (f"import sys; sys.path[:0] = [{mcp_root!r}]; "
            "from kicad_mcp.server import main; main()")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "kicad-mcp-selftest",
                                   "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    env = dict(os.environ)
    env["KICAD_MCP_TRANSPORT"] = "stdio"  # nie vom http-Modus anstecken lassen
    proc = subprocess.Popen(
        [sys.executable, "-c", code, "--transport", "stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env)
    try:
        stdout, stderr = proc.communicate(
            "".join(json.dumps(m) + "\n" for m in msgs),
            timeout=HANDSHAKE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise AssertionError(
            f"Server antwortet nicht in {int(HANDSHAKE_TIMEOUT_S)}s") from None
    if '"serverInfo"' not in (stdout or ""):
        tail = " | ".join((stderr or "").strip().splitlines()[-3:])
        raise AssertionError(f"kein initialize-Reply (exit {proc.returncode})"
                             + (f": {tail}" if tail else ""))
    if '"tools"' not in (stdout or ""):
        raise AssertionError("initialize ok, aber tools/list blieb aus")
    return {"handshake": "ok"}


STEPS: List[tuple] = [
    ("server+tool-registry", _step_server_tools),
    ("demo-projekt generieren (spec→sch→pcb)", _step_generate_project),
    ("schaltplan lesen", _step_read_schematic),
    ("netzliste extrahieren", _step_netlist),
    ("pcb lesen", _step_read_pcb),
    ("pad-geometrie (welt-koordinaten)", _step_pad_geometry),
    ("text-patch (via einfuegen)", _step_patch_via),
    ("connectivity (pcbnew, optional)", _step_connectivity),
    ("drc via kicad-cli (optional)", _step_kicad_cli_drc),
    ("stdio-handshake (wie claude startet)", _step_stdio_handshake),
]


def run_all(out_dir: str, spec_path: Optional[str] = None,
            include_handshake: bool = True,
            on_line: Optional[Callable[[str], None]] = None,
            steps: Optional[List[tuple]] = None) -> dict:
    """Alle Schritte fahren; Report-Dict zurück (und JSON+MD nach out_dir).

    ``steps`` ist injectable (Tests); Default sind die echten ``STEPS``."""
    os.makedirs(out_dir, exist_ok=True)
    ctx: dict = {"spec": load_spec(spec_path),
                 "project_dir": os.path.join(out_dir, "demo_projekt")}
    steps = [s for s in (steps if steps is not None else STEPS)
             if include_handshake or s[1] is not _step_stdio_handshake]
    results = []
    t_all = time.perf_counter()
    for name, fn in steps:
        entry: dict[str, Any] = {"name": name, "status": FAIL, "seconds": 0.0,
                                 "error": "", "detail": {}}
        t0 = time.perf_counter()
        try:
            entry["detail"] = fn(ctx) or {}
            entry["status"] = PASS
        except SkipStep as exc:
            entry["status"] = SKIP
            entry["error"] = str(exc)
        except Exception as exc:  # der Lauf stirbt an keinem Schritt
            entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["seconds"] = round(time.perf_counter() - t0, 2)
        results.append(entry)
        if on_line:
            mark = {"PASS": "ok", "SKIP": "übersprungen"}.get(
                entry["status"], "FEHLER")
            line = f"[{mark}] {name} ({entry['seconds']}s)"
            if entry["error"]:
                line += f" — {entry['error']}"
            on_line(line)
    report = {
        "meta": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "executable": sys.executable,
            "duration_s": round(time.perf_counter() - t_all, 1),
            # Gemessen, nicht geraten: der Lauf lädt einen vollen Server
            # (pandas + 186 Tools) und ggf. einen pcbnew-Worker — beides
            # endet mit dem Prozess. Die Zahl macht "braucht viel RAM?"
            # im Report diskutierbar (Feld-Frage 0.9.0).
            "peak_ram_mb": peak_ram_mb(),
        },
        "steps": results,
        "summary": {s: sum(1 for r in results if r["status"] == s)
                    for s in (PASS, FAIL, SKIP)},
    }
    _write_reports(out_dir, report)
    return report


def render_report(report: dict) -> str:
    """Der MD-Report — FAIL-Schritte zuerst, fürs Agent-Zurücklesen."""
    meta, summary = report["meta"], report["summary"]
    ram = meta.get("peak_ram_mb")
    lines = [
        "# kicad-mcp Systemtest (standalone, ohne Claude)", "",
        f"- Python: {meta['python']} — {meta['executable']}",
        f"- Plattform: {meta['platform']}",
        f"- Dauer: {meta['duration_s']}s"
        + (f" · Peak-RAM: {ram} MB (transient — endet mit dem Prozess)"
           if ram else ""),
        f"- **{summary[PASS]} PASS · {summary[FAIL]} FAIL · "
        f"{summary[SKIP]} SKIP**", "",
        "| Schritt | Status | Dauer | Fehler |", "|---|---|---|---|",
    ]
    order = {FAIL: 0, SKIP: 1, PASS: 2}
    for r in sorted(report["steps"], key=lambda x: order.get(x["status"], 3)):
        err = (r["error"] or "").replace("|", "/")[:100]
        lines.append(f"| {r['name']} | {r['status']} | {r['seconds']}s "
                     f"| {err} |")
    lines += ["", "## Details", ""]
    for r in report["steps"]:
        lines.append(f"- **{r['name']}** — {r['status']}"
                     + (f": `{json.dumps(r['detail'], ensure_ascii=False)}`"
                        if r["detail"] else ""))
    return "\n".join(lines)


def _write_reports(out_dir: str, report: dict) -> "tuple[str, str]":
    json_path = os.path.join(out_dir, "selftest_report.json")
    md_path = os.path.join(out_dir, "selftest_report.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_report(report))
    report["report_json"] = json_path
    report["report_md"] = md_path
    return json_path, md_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="kicad-mcp Systemtest ohne Claude (orchestrierbar; "
                    "Exit 0 = alles gruen)")
    parser.add_argument("--out", default="",
                        help="Report-/Arbeitsverzeichnis (Default: Temp)")
    parser.add_argument("--spec", default="",
                        help="eigene Board-Spec-JSON (Default: gebuendelt)")
    parser.add_argument("--verbose", action="store_true",
                        help="jeden Schritt live ausgeben (sonst nur Fehler)")
    parser.add_argument("--no-handshake", action="store_true",
                        help="stdio-Handshake-Schritt auslassen (schneller)")
    args = parser.parse_args(argv)

    # Bekanntes Tool-Code-Rauschen (sync gerufene async ctx.info) — im
    # Selftest-Output nur Alarm ohne Information; Fehler laufen über die
    # Schritt-Verdikte, nicht über Warnings.
    warnings.filterwarnings(
        "ignore", message=".*coroutine.*was never awaited.*",
        category=RuntimeWarning)

    out_dir = args.out or os.path.join(tempfile.gettempdir(),
                                       "kicad_mcp_selftest")
    lines: list = []

    def on_line(line: str) -> None:
        lines.append(line)
        if args.verbose:
            print(line, flush=True)

    report = run_all(out_dir, spec_path=args.spec or None,
                     include_handshake=not args.no_handshake,
                     on_line=on_line)
    failed = report["summary"][FAIL]
    if failed:
        # Interaktion nur bei Fehlern: die FAIL-Zeilen + Reportpfad
        if not args.verbose:
            for line in lines:
                if line.startswith("[FEHLER]"):
                    print(line)
        print(f"SELFTEST FEHLGESCHLAGEN: {failed} Schritt(e) rot "
              f"-> {report['report_md']}")
        return 1
    print(f"SELFTEST OK ({report['summary'][PASS]} PASS, "
          f"{report['summary'][SKIP]} SKIP, "
          f"{report['meta']['duration_s']}s) -> {report['report_md']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
