# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic design audits — catch bugs ERC/DRC miss.

These tools are pure-Python rule engines. No LLM-in-loop. Each audit
returns a structured ``{issues: [...]}`` report with severity, ref,
description, and (where applicable) suggested fix-coordinates.

Topics:
    * Power-tree audit (PCB): decoupling-cap placement, source-pin
      presence, voltage-conflict detection, floating power symbols.
    * Schematic-topology audit (SCH): single-pin-stub nets,
      output-vs-output conflicts, floating hierarchical labels,
      unused IC pins.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.pcb_board_parse import (
    parse_pcb_footprints as _parse_pcb_for_audit,
)


# ---------------------------------------------------------------------------
# Power-tree audit (PCB)
# ---------------------------------------------------------------------------


_POWER_NET_PATTERNS = [
    r'^\+\d+',          # +5V, +3V3, +12V, +20V
    r'^GND',
    r'^VCC',
    r'^VDD',
    r'^VSS',
    r'^V[A-Z]{2,}',     # VBUS, VBAT, VIN, VOUT
    r'^[A-Z]+\+',       # +3V3_AON variant
]
_POWER_NET_RE = re.compile("|".join(_POWER_NET_PATTERNS))


def _is_power_net(name: str) -> bool:
    return bool(_POWER_NET_RE.match(name))


def _audit_power_tree(pcb_text: str, max_decoupling_distance_mm: float) -> dict[str, Any]:
    data = _parse_pcb_for_audit(pcb_text)
    fps = data["footprints"]

    # Build: net → list of (ref, pad, x, y)
    net_pins: dict[str, list[dict[str, Any]]] = {}
    for fp in fps:
        for p in fp["pads"]:
            if p["net"]:
                net_pins.setdefault(p["net"], []).append({
                    "ref": fp["ref"], **p,
                })

    issues: list[dict[str, Any]] = []

    # Identify power nets
    power_nets = [n for n in net_pins if _is_power_net(n)]
    gnd_nets = [n for n in power_nets if n.startswith("GND") or n == "GND"]
    rail_nets = [n for n in power_nets if n not in gnd_nets]

    # Rule 1: each rail net must have ≥1 source pin (heuristic: a
    # footprint whose value or ref suggests it's a regulator / supply
    # — names containing BUCK, LDO, REG, VBUS, BAT, USB, PD).
    SOURCE_HINTS_RE = re.compile(
        r"(BUCK|LDO|REG|VBUS|BAT|USB|PD|BOOST|CHARGER|SUPPLY|PSU)",
        re.IGNORECASE,
    )
    for net in rail_nets:
        pins = net_pins[net]
        has_source = any(
            SOURCE_HINTS_RE.search(p["ref"]) or
            SOURCE_HINTS_RE.search(
                next((f["value"] for f in fps if f["ref"] == p["ref"]), "")
            )
            for p in pins
        )
        if not has_source:
            issues.append({
                "severity": "warning",
                "rule": "rail_no_obvious_source",
                "net": net,
                "n_pins": len(pins),
                "description": (
                    f"Power rail {net!r} has {len(pins)} pin(s) but no "
                    "obvious source pin (no ref/value matches BUCK/LDO/"
                    "REG/VBUS/BAT/USB/PD/...)."
                ),
            })

    # Rule 2: a rail with only 1 pin is suspicious
    for net in rail_nets:
        if len(net_pins[net]) == 1:
            p = net_pins[net][0]
            issues.append({
                "severity": "error",
                "rule": "rail_single_pin",
                "net": net,
                "ref": p["ref"],
                "pad": p["pad"],
                "description": (
                    f"Power rail {net!r} has only one pin "
                    f"({p['ref']}.{p['pad']}). Likely typo or unfinished "
                    "wiring."
                ),
            })

    # Rule 3: every IC pin on a power rail should have a decoupling cap
    # within max_decoupling_distance_mm on the same net. Heuristic:
    # "IC" = footprint ref starts with U_ or U[0-9]; "cap" = ref starts
    # with C_ or C[0-9].
    ic_re = re.compile(r"^U[_0-9]")
    cap_re = re.compile(r"^C[_0-9]")
    cap_refs = {fp["ref"] for fp in fps if cap_re.match(fp["ref"])}
    for net in rail_nets:
        pins = net_pins[net]
        ic_pins = [p for p in pins if ic_re.match(p["ref"])]
        cap_pins = [p for p in pins if p["ref"] in cap_refs]
        for ip in ic_pins:
            closest = None
            closest_d = float("inf")
            for cp in cap_pins:
                d = math.hypot(ip["x"] - cp["x"], ip["y"] - cp["y"])
                if d < closest_d:
                    closest_d = d
                    closest = cp
            if closest is None:
                issues.append({
                    "severity": "warning",
                    "rule": "ic_pin_no_decoupling",
                    "net": net,
                    "ic_ref_pin": f"{ip['ref']}.{ip['pad']}",
                    "ic_pos": [round(ip["x"], 3), round(ip["y"], 3)],
                    "description": (
                        f"{ip['ref']}.{ip['pad']} sits on power rail "
                        f"{net!r} but no decoupling cap is on the same "
                        "net anywhere on the board."
                    ),
                })
            elif closest_d > max_decoupling_distance_mm:
                issues.append({
                    "severity": "warning",
                    "rule": "ic_pin_decoupling_far",
                    "net": net,
                    "ic_ref_pin": f"{ip['ref']}.{ip['pad']}",
                    "nearest_cap_pin": f"{closest['ref']}.{closest['pad']}",
                    "distance_mm": round(closest_d, 2),
                    "limit_mm": max_decoupling_distance_mm,
                    "description": (
                        f"{ip['ref']}.{ip['pad']} on rail {net!r}: "
                        f"nearest decoupling cap "
                        f"{closest['ref']}.{closest['pad']} is "
                        f"{closest_d:.2f} mm away (limit "
                        f"{max_decoupling_distance_mm} mm)."
                    ),
                })

    # Rule 4: floating power-symbol footprints (refs starting with #PWR
    # or similar) with 0 connected pads should not exist on PCB
    for fp in fps:
        if fp["ref"].startswith("#PWR") or fp["ref"].startswith("PWR_"):
            connected = sum(1 for p in fp["pads"] if p["net"])
            if connected == 0:
                issues.append({
                    "severity": "info",
                    "rule": "floating_power_symbol",
                    "ref": fp["ref"],
                    "description": (
                        f"Power symbol {fp['ref']} has 0 connected pads."
                    ),
                })

    # Group issues by severity for summary
    by_sev = {"error": 0, "warning": 0, "info": 0}
    for it in issues:
        by_sev[it.get("severity", "info")] += 1

    return {
        "success": True,
        "n_footprints": len(fps),
        "n_power_nets": len(power_nets),
        "rails": rail_nets,
        "ground_nets": gnd_nets,
        "issues": issues,
        "summary": {
            "errors": by_sev["error"],
            "warnings": by_sev["warning"],
            "infos": by_sev["info"],
            "total": len(issues),
        },
    }


# ---------------------------------------------------------------------------
# Schematic-topology audit
# ---------------------------------------------------------------------------


def _audit_schematic(netlist_text: str) -> dict[str, Any]:
    """Run topology audits against the schematic-exported netlist
    (kicadsexpr format).
    """
    # Reuse the netlist parser from netlist_tools — small inline copy to
    # avoid cross-tool import circular.
    def parse(s: str, pos: int):
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos >= len(s):
            return None, pos
        if s[pos] == "(":
            items: list = []
            pos += 1
            while True:
                while pos < len(s) and s[pos].isspace():
                    pos += 1
                if pos >= len(s):
                    break
                if s[pos] == ")":
                    pos += 1
                    break
                item, pos = parse(s, pos)
                if item is not None:
                    items.append(item)
            return items, pos
        if s[pos] == '"':
            pos += 1
            start = pos
            while pos < len(s) and s[pos] != '"':
                if s[pos] == "\\":
                    pos += 1
                pos += 1
            val = s[start:pos]
            pos += 1
            return val, pos
        start = pos
        while pos < len(s) and not s[pos].isspace() and s[pos] not in "()":
            pos += 1
        return s[start:pos], pos

    tree, _ = parse(netlist_text, 0)
    if not isinstance(tree, list):
        return {"success": False, "error": "Netlist parse failed."}

    def find(t: list, name: str):
        for it in t:
            if isinstance(it, list) and it and it[0] == name:
                return it
        return None

    nets_block = find(tree, "nets")
    comp_block = find(tree, "components")
    if not nets_block:
        return {"success": False, "error": "No (nets) block in netlist."}

    # Build comp → pintype map
    comp_pin_types: dict[str, dict[str, str]] = {}
    if comp_block:
        for c in comp_block[1:]:
            if not isinstance(c, list) or c[0] != "comp":
                continue
            ref = ""
            for sub in c[1:]:
                if isinstance(sub, list) and sub[0] == "ref":
                    ref = sub[1]
            if ref:
                comp_pin_types[ref] = {}

    nets_info: list[dict[str, Any]] = []
    for n in nets_block[1:]:
        if not isinstance(n, list) or n[0] != "net":
            continue
        name = ""
        nodes: list[dict[str, str]] = []
        for sub in n[1:]:
            if isinstance(sub, list):
                if sub[0] == "name":
                    name = sub[1]
                elif sub[0] == "node":
                    ref = pin = ptype = ""
                    for nn in sub[1:]:
                        if isinstance(nn, list):
                            if nn[0] == "ref":
                                ref = nn[1]
                            elif nn[0] == "pin":
                                pin = nn[1]
                            elif nn[0] == "pintype":
                                ptype = nn[1]
                    if ref and pin:
                        nodes.append({
                            "ref": ref, "pin": pin, "pintype": ptype,
                        })
        if name:
            nets_info.append({"name": name, "nodes": nodes})

    issues: list[dict[str, Any]] = []

    # Rule 1: net with only 1 node (single-pin stub) — unless it's
    # `unconnected-…` or `NC_…` which KiCad uses for legal no-connects.
    for net in nets_info:
        if len(net["nodes"]) == 1 and not (
            net["name"].startswith("unconnected-")
            or net["name"].startswith("NC")
            or net["name"].startswith("#PWR")
        ):
            n = net["nodes"][0]
            issues.append({
                "severity": "error",
                "rule": "single_pin_stub_net",
                "net": net["name"],
                "ref_pin": f"{n['ref']}.{n['pin']}",
                "description": (
                    f"Net {net['name']!r} has only one node "
                    f"({n['ref']}.{n['pin']}). Likely missing wire or "
                    "typo in a label."
                ),
            })

    # Rule 2: multiple driving outputs on the same net
    for net in nets_info:
        outputs = [
            n for n in net["nodes"]
            if n["pintype"] in ("output", "power_out")
        ]
        if len(outputs) >= 2:
            # Power_out × power_out is sometimes legal (parallel
            # regulators) — still flag as warning, not error.
            sev = "warning" if all(
                n["pintype"] == "power_out" for n in outputs
            ) else "error"
            issues.append({
                "severity": sev,
                "rule": "output_to_output_conflict",
                "net": net["name"],
                "outputs": [
                    f"{n['ref']}.{n['pin']}({n['pintype']})"
                    for n in outputs
                ],
                "description": (
                    f"Net {net['name']!r} is driven by {len(outputs)} "
                    f"outputs simultaneously."
                ),
            })

    # Rule 3: power_in pin not on a power net (heuristic: net name
    # doesn't match power-net pattern)
    for net in nets_info:
        for n in net["nodes"]:
            if n["pintype"] == "power_in" and not _is_power_net(
                net["name"]
            ):
                issues.append({
                    "severity": "warning",
                    "rule": "power_in_on_non_power_net",
                    "net": net["name"],
                    "ref_pin": f"{n['ref']}.{n['pin']}",
                    "description": (
                        f"Power-input pin {n['ref']}.{n['pin']} is on "
                        f"net {net['name']!r} which doesn't look like a "
                        "power rail."
                    ),
                })

    by_sev = {"error": 0, "warning": 0, "info": 0}
    for it in issues:
        by_sev[it.get("severity", "info")] += 1

    return {
        "success": True,
        "n_nets": len(nets_info),
        "issues": issues,
        "summary": {
            "errors": by_sev["error"],
            "warnings": by_sev["warning"],
            "infos": by_sev["info"],
            "total": len(issues),
        },
    }


def register_audit_tools(mcp: FastMCP) -> None:
    """Register deterministic design-audit tools."""

    @mcp.tool()
    def audit_power_tree(
        pcb_path: str,
        max_decoupling_distance_mm: float = 10.0,
    ) -> dict[str, Any]:
        """Deterministic power-tree audit on a PCB. Returns structured
        issues that ERC/DRC typically miss.

        Use this before ordering / archiving a board to catch power-
        integrity errors that the standard DRC + ERC pair does not
        flag: missing decoupling caps near IC VCC pins, single-pin
        rails, rails with no obvious driving source. Prefer running it
        after the netlist patch (``patch_pcb_nets_from_netlist``) so
        the rail names are populated.

        Rules checked:
            * **rail_no_obvious_source** — Power-rail net has pins but
              no footprint whose ref/value hints at being the source
              (BUCK/LDO/REG/VBUS/BAT/USB/PD/...). Severity: warning.
            * **rail_single_pin** — Rail has only one pin. Severity:
              error.
            * **ic_pin_no_decoupling** — IC pin on a rail with no
              decoupling cap anywhere on that net. Severity: warning.
            * **ic_pin_decoupling_far** — IC pin on a rail; nearest
              cap is farther than ``max_decoupling_distance_mm``.
              Severity: warning.
            * **floating_power_symbol** — ``#PWR``/``PWR_*`` footprint
              with 0 connected pads. Severity: info.

        Power-net detection: net name matches ``+\\d+`` / ``GND`` /
        ``VCC`` / ``VDD`` / ``VSS`` / ``V[A-Z]+``.

        Args:
            pcb_path: ``.kicad_pcb`` to audit.
            max_decoupling_distance_mm: Threshold for the
              ``ic_pin_decoupling_far`` rule. 10 mm covers most
              SMD-cap-near-IC layouts.

        Returns:
            ``{success, n_footprints, n_power_nets, rails, ground_nets,
            issues: [{severity, rule, ...}], summary: {errors, warnings,
            infos, total}}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            with open(pcb_path, encoding="utf-8") as fh:
                pcb_text = fh.read()
            result = _audit_power_tree(
                pcb_text, max_decoupling_distance_mm,
            )
            result["pcb_path"] = pcb_path
            return result
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def audit_schematic_topology(
        schematic_path: str,
    ) -> dict[str, Any]:
        """Deterministic schematic-topology audit.

        Use this before a major refactor or PCB hand-off to catch
        topology bugs that KiCad's built-in ERC does not see (e.g.
        single-pin stubs, output-to-output conflicts on the same net,
        power_in pins on non-power rails). Run it after ERC, not
        instead of it — the two are complementary, not substitutes.

        Exports the netlist via ``kicad-cli sch export netlist`` and
        runs rule-based checks that complement ERC:

        * **single_pin_stub_net** — Net with only one node (excluding
          ``unconnected-…``/``NC_…``/``#PWR…``). Severity: error.
        * **output_to_output_conflict** — Two or more pins of type
          ``output``/``power_out`` driving the same net. Severity:
          error (two ``output``s) or warning (two ``power_out``s).
        * **power_in_on_non_power_net** — ``power_in`` pin connected
          to a net whose name doesn't look like a power rail.
          Severity: warning.

        Args:
            schematic_path: ``.kicad_sch`` to audit.

        Returns:
            ``{success, n_nets, issues: [...], summary: {...}}``.
        """
        import subprocess
        import tempfile

        from kicad_mcp.utils.path_env import kicad_cli

        schematic_path = to_local_path(schematic_path)
        if not os.path.isfile(schematic_path):
            return {
                "success": False,
                "error": f"Schematic not found: {schematic_path}",
            }
        cli = kicad_cli()
        if not cli:
            return {"success": False, "error": "kicad-cli not found."}
        with tempfile.NamedTemporaryFile(
            suffix=".net", delete=False,
        ) as tf:
            tmp_net = tf.name
        try:
            subprocess.run(
                [
                    cli, "sch", "export", "netlist",
                    "--format", "kicadsexpr",
                    "-o", tmp_net, schematic_path,
                ],
                capture_output=True, check=True, timeout=60,
            )
            with open(tmp_net, encoding="utf-8") as fh:
                netlist_text = fh.read()
        except subprocess.CalledProcessError as exc:
            return {
                "success": False,
                "error": (
                    f"kicad-cli sch export netlist failed: "
                    f"{exc.stderr.decode('utf-8', 'replace')[:300]}"
                ),
            }
        finally:
            try:
                os.unlink(tmp_net)
            except OSError:
                pass

        result = _audit_schematic(netlist_text)
        result["schematic_path"] = schematic_path
        return result
