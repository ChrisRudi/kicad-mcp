# SPDX-License-Identifier: GPL-3.0-or-later
"""„Schaltung als Vorlage" — der Nutzer zeichnet, der MCP merkt sich und baut.

KiCad 10 hat keine Schaltplan-Schreib-API (empirisch: leerer Befehlssatz), also
zeichnet der Nutzer den Schaltplan selbst. Diese Tools LESEN einen gezeichneten
``.kicad_sch``, legen ihn als benannte Vorlage im persistenten Nutzer-Speicher
ab (`utils/circuit_templates`), und generieren daraus auf Wunsch ein komplettes
Projekt (Schaltplan + Platine) — mit auto-aufgelösten Pins/Footprints über die
bestehende Generatoren-Pipeline (`expand_netlist` → `build_schematic`/`build_pcb`).

Der Kreis: einmal schön zeichnen → gespeichert → beliebig oft als Board bauen
oder als Block wiederverwenden (Matcher/`apply_template_block`).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from kicad_mcp.tools.netlist_tools import _parse_netlist_to_spec
from kicad_mcp.utils import circuit_templates as store
from kicad_mcp.utils.path_env import kicad_cli, to_local_path


def _schematic_to_spec(schematic_path: str) -> dict:
    """Gezeichneten Schaltplan → Spec (components + nets) via kicad-cli-Netzliste.

    Nutzt KiCads eigene Netzlisten-Ausgabe (korrekt für Busse/Power/hierarchisch)
    plus den erprobten ``_parse_netlist_to_spec``. Wirft bei Fehlern."""
    cli = kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli nicht gefunden — für den Netzlisten-Export "
                           "nötig.")
    tmp = tempfile.NamedTemporaryFile(suffix=".net", delete=False)
    tmp.close()
    try:
        proc = subprocess.run(
            [cli, "sch", "export", "netlist", "--output", tmp.name,
             schematic_path],
            capture_output=True, text=True, timeout=120, check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                "kicad-cli netlist-Export fehlgeschlagen: "
                + (proc.stderr or proc.stdout or "")[:300])
        with open(tmp.name, encoding="utf-8") as fh:
            netlist_text = fh.read()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return _parse_netlist_to_spec(netlist_text, schematic_path)


def register_circuit_template_tools(mcp: FastMCP) -> None:
    """Register the draw-once / reuse circuit-template tools."""

    @mcp.tool()
    def save_circuit_template(
        schematic_path: str,
        name: str,
        description: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Einen selbst gezeichneten Schaltplan als benannte Vorlage im MCP
        speichern — der „ich zeichne, du merkst dir"-Schritt.

        Liest den ``.kicad_sch`` (Bauteile + Netze über KiCads Netzliste) und
        legt ihn persistent im Nutzer-Vorlagen-Speicher ab. Danach lässt sich
        die Vorlage mit ``build_circuit_template`` zu einem kompletten Projekt
        bauen oder als Block wiederverwenden.

        Use this when the user drew a circuit they want to keep and reuse — a
        favourite LDO front-end, an MCU reset block, whatever — instead of
        re-drawing it every project.

        Args:
            schematic_path: Pfad zur gezeichneten ``.kicad_sch`` (WSL/Windows).
            name: Anzeigename der Vorlage (frei; wird dateisicher geslugged).
            description: Optionaler Kurztext, wofür die Schaltung gut ist.

        Returns:
            ``{success, name, path, components, nets}`` — oder
            ``{success: False, error}``.
        """
        schematic_path = to_local_path(schematic_path)
        if not os.path.isfile(schematic_path):
            return {"success": False,
                    "error": f"Schaltplan nicht gefunden: {schematic_path}"}
        if not (name or "").strip():
            return {"success": False, "error": "name fehlt."}
        try:
            spec = _schematic_to_spec(schematic_path)
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        if not spec.get("components"):
            return {"success": False,
                    "error": "Kein Bauteil im Schaltplan gefunden — leer?"}
        spec["description"] = description
        spec["source_schematic"] = os.path.basename(schematic_path)
        path = store.save(name, spec)
        if ctx:
            ctx.info(f"Vorlage '{name}' gespeichert: {len(spec['components'])} "
                     f"Bauteile, {len(spec['nets'])} Netze")
        return {
            "success": True,
            "name": name,
            "path": path,
            "components": len(spec["components"]),
            "nets": len(spec["nets"]),
        }

    @mcp.tool()
    def list_circuit_templates() -> dict[str, Any]:
        """Alle gespeicherten Schaltungs-Vorlagen auflisten.

        Use this to see what circuits the user has captured so far (name,
        description, component/net counts) before building or reusing one.

        Returns:
            ``{success, count, templates: [{name, slug, description,
            components, nets}]}``.
        """
        tpls = store.list_templates()
        return {"success": True, "count": len(tpls), "templates": tpls}

    @mcp.tool()
    def build_circuit_template(
        name: str,
        output_dir: str,
        project_name: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Aus einer gespeicherten Vorlage ein komplettes KiCad-Projekt bauen —
        die „Magie": Schaltplan + Platine, Pins/Footprints auto-aufgelöst.

        Nimmt die mit ``save_circuit_template`` gemerkte Schaltung und
        generiert daraus Schaltplan (``.kicad_sch``) und Platine
        (``.kicad_pcb``) über die bestehende Generatoren-Pipeline
        (``expand_netlist`` → ``build_schematic``/``build_pcb``).

        Use this when the user says "build me that circuit" / "mach mir das
        Board von meiner Vorlage X" — no re-drawing, no re-typing the netlist.

        Args:
            name: Name (oder Slug) einer gespeicherten Vorlage.
            output_dir: Zielverzeichnis für die erzeugten Dateien.
            project_name: Projektname (leer = aus dem Vorlagen-Namen).

        Returns:
            ``{success, project_name, files: {schematic, pcb, project}}`` —
            oder ``{success: False, error}``.
        """
        output_dir = to_local_path(output_dir)
        spec = store.load(name)
        if spec is None:
            avail = ", ".join(t["slug"] for t in store.list_templates())
            return {"success": False,
                    "error": (f"Vorlage '{name}' nicht gefunden. "
                              f"Vorhanden: {avail or '(keine)'}")}
        parts, nets = store.to_compact(spec)
        if not parts or not nets:
            return {"success": False,
                    "error": "Vorlage hat keine Bauteile/Netze."}
        proj = store.safe_name(project_name or name)
        try:
            from kicad_mcp.generators.netlist_expander import expand_netlist
            from kicad_mcp.generators.pcb.builder import build_pcb
            from kicad_mcp.generators.schematic.builder import build_schematic
            exp_parts, exp_nets = expand_netlist(parts, nets)
            sch = build_schematic(exp_parts, exp_nets, proj)
            board = spec.get("board") or {"shape": "rectangle",
                                          "width": 40, "depth": 30, "layers": 2}
            pcb = build_pcb(exp_parts, exp_nets, board, proj)
        except Exception as exc:
            return {"success": False, "error": f"Generierung: {exc}"}
        os.makedirs(output_dir, exist_ok=True)
        sch_path = os.path.join(output_dir, f"{proj}.kicad_sch")
        pcb_path = os.path.join(output_dir, f"{proj}.kicad_pcb")
        pro_path = os.path.join(output_dir, f"{proj}.kicad_pro")
        with open(sch_path, "w", encoding="utf-8") as fh:
            fh.write(sch)
        with open(pcb_path, "w", encoding="utf-8") as fh:
            fh.write(pcb)
        if not os.path.exists(pro_path):
            with open(pro_path, "w", encoding="utf-8") as fh:
                json.dump({"board": {}, "meta": {"version": 1},
                           "sheets": [], "libraries": {}}, fh, indent=2)
        if ctx:
            ctx.info(f"Projekt aus Vorlage '{name}' gebaut: {proj}")
        return {
            "success": True,
            "project_name": proj,
            "files": {"schematic": sch_path, "pcb": pcb_path,
                      "project": pro_path},
        }
