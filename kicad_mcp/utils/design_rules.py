# SPDX-License-Identifier: GPL-3.0-or-later
"""Design-Wächter — the persistent rule registry (single source of truth).

Semantic design checks that KiCad's ERC does NOT do live here as registered
rules. Each rule is one entry in ``RULES`` (key, title, severity, check-fn), so
new rules are added in *one* place — the same Single-Source pattern the codebase
uses for ``TOOL_REGISTRARS`` / ``superfeatures.FEATURES``. The board is parsed
*once* into a shared :class:`BoardContext`; every rule reads that context, so
adding rules costs no extra I/O.

Composes existing helpers (``pcb_board_parse``, ``bus_infer``,
``placement_eval.is_power_net``) — no re-parsing, per the synergy rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from kicad_mcp.utils import bus_infer
from kicad_mcp.utils.pcb_board_parse import parse_pcb_footprints
from kicad_mcp.utils.placement_eval import is_power_net


@dataclass
class BoardContext:
    """The board parsed once, in the shape every rule needs."""
    footprints: list
    fp_by_ref: dict          # ref -> footprint
    net_pins: dict           # net -> [(ref, pad), …]
    power: set               # power/ground net names
    buses: list              # bus_infer.group_buses(nets)


def build_context(pcb_text: str) -> BoardContext:
    """Parse a ``.kicad_pcb`` once into the shared rule context."""
    parsed = parse_pcb_footprints(pcb_text)
    fps = parsed["footprints"]
    fp_by_ref: dict = {}
    net_pins: dict = {}
    for fp in fps:
        fp_by_ref[fp["ref"]] = fp
        for pad in fp["pads"]:
            if pad["net"]:
                net_pins.setdefault(pad["net"], []).append((fp["ref"], pad["pad"]))
    n = len(fps)
    power = {net for net in net_pins if is_power_net(net, len(net_pins[net]), n)}
    buses = bus_infer.group_buses(sorted(net_pins))
    return BoardContext(fps, fp_by_ref, net_pins, power, buses)


def _is_ground(net: str) -> bool:
    return net.strip().lstrip("/").upper().startswith("GND")


def _bridges(ctx: BoardContext, net: str, ref_prefix: str,
             to_nets) -> bool:
    """True if a two-terminal part whose ref starts with ``ref_prefix`` has one
    pad on ``net`` and another pad on a net in ``to_nets`` (e.g. a resistor from
    a signal to a rail = a pull-up; a cap from a signal to GND = a load cap)."""
    for ref, _pad in ctx.net_pins.get(net, []):
        if not ref.upper().startswith(ref_prefix):
            continue
        fp = ctx.fp_by_ref.get(ref)
        if not fp:
            continue
        other = {p["net"] for p in fp["pads"] if p["net"] and p["net"] != net}
        if other & to_nets:
            return True
    return False


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #

def _check_i2c_pullups(ctx: BoardContext) -> list:
    """I²C is open-drain → SDA and SCL each need a pull-up to a supply. KiCad's
    ERC never checks this."""
    issues = []
    for b in ctx.buses:
        if b["kind"] != "I2C":
            continue
        for net in b["nets"]:
            if not _bridges(ctx, net, "R", ctx.power):
                issues.append({
                    "rule": "i2c_missing_pullup", "severity": "warning",
                    "bus": b["bus"], "net": net,
                    "description": (
                        f"I²C net {net!r} (bus {b['bus']}) has no detectable "
                        "pull-up resistor to a supply — I²C is open-drain and "
                        "needs pull-ups on SDA and SCL."),
                })
    return issues


_XTAL_VALUE_RE = re.compile(r"(MHZ|KHZ|XTAL|CRYST|OSC|RESON)", re.IGNORECASE)


def _check_crystal_load_caps(ctx: BoardContext) -> list:
    """Every crystal terminal (XIN/XOUT) needs a load capacitor to ground. A
    missing/asymmetric load cap is a classic silent bring-up failure ERC misses.
    (The exact value against the crystal's CL is a follow-up once CL is known.)"""
    issues = []
    for fp in ctx.footprints:
        ref = fp["ref"]
        is_xtal = ref.upper().startswith("Y") or _XTAL_VALUE_RE.search(fp.get("value", ""))
        if not is_xtal:
            continue
        signal_nets = sorted({p["net"] for p in fp["pads"]
                              if p["net"] and p["net"] not in ctx.power})
        if not signal_nets:
            continue
        for net in signal_nets:
            if not _bridges(ctx, net, "C",
                            {n for n in ctx.net_pins if _is_ground(n)}):
                issues.append({
                    "rule": "crystal_missing_load_cap", "severity": "warning",
                    "ref": ref, "net": net,
                    "description": (
                        f"Crystal {ref} terminal on net {net!r} has no load "
                        "capacitor to ground — each XIN/XOUT needs one "
                        "(C = 2·(CL − Cstray))."),
                })
    return issues


@dataclass
class Rule:
    key: str
    title: str
    severity: str
    check: Callable[[BoardContext], list]


# The persistent registry. Add a rule here and it runs everywhere.
RULES: tuple[Rule, ...] = (
    Rule("i2c_pullups", "I²C-Bus ohne Pull-ups", "warning", _check_i2c_pullups),
    Rule("crystal_load_caps", "Quarz ohne Load-Caps", "warning",
         _check_crystal_load_caps),
)


def run_rules(ctx: BoardContext, only=None) -> list:
    """Run every registered rule (or just those whose key is in ``only``) against
    ``ctx`` and return the combined issue list."""
    issues: list = []
    for rule in RULES:
        if only and rule.key not in only:
            continue
        try:
            issues.extend(rule.check(ctx))
        except Exception as exc:  # a bad rule must not sink the whole audit
            issues.append({"rule": rule.key, "severity": "info",
                           "description": f"rule {rule.key} failed: {exc}"})
    return issues


def rule_catalog() -> list[dict[str, Any]]:
    """The registered rules as data (key/title/severity) — for listing."""
    return [{"key": r.key, "title": r.title, "severity": r.severity}
            for r in RULES]
