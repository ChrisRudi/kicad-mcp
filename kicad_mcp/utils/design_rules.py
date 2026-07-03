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

import math
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
    pad_xy: dict             # (ref, pad) -> (x_mm, y_mm) world coords


def build_context(pcb_text: str) -> BoardContext:
    """Parse a ``.kicad_pcb`` once into the shared rule context."""
    parsed = parse_pcb_footprints(pcb_text)
    fps = parsed["footprints"]
    fp_by_ref: dict = {}
    net_pins: dict = {}
    pad_xy: dict = {}
    for fp in fps:
        fp_by_ref[fp["ref"]] = fp
        for pad in fp["pads"]:
            pad_xy[(fp["ref"], pad["pad"])] = (pad["x"], pad["y"])
            if pad["net"]:
                net_pins.setdefault(pad["net"], []).append((fp["ref"], pad["pad"]))
    n = len(fps)
    power = {net for net in net_pins if is_power_net(net, len(net_pins[net]), n)}
    buses = bus_infer.group_buses(sorted(net_pins))
    return BoardContext(fps, fp_by_ref, net_pins, power, buses, pad_xy)


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


def _supplies(ctx: BoardContext) -> set:
    """Power nets that are not ground — the rails a decoupling cap sits on."""
    return {n for n in ctx.power if not _is_ground(n)}


_DECOUPLE_NEAR_MM = 3.0  # a bypass cap beyond this from its IC pin is "far"


def _check_decoupling(ctx: BoardContext) -> list:
    """Every IC supply pin wants a decoupling cap *close* to it. This reads the
    same intent as ``audit_power_tree`` but board-wide: for each IC (ref ^U)
    power pin, is there a cap (ref ^C) bridging that rail to ground, and is its
    nearest pad within a few mm? KiCad's ERC checks neither presence nor
    proximity."""
    issues = []
    supplies = _supplies(ctx)
    grounds = {n for n in ctx.net_pins if _is_ground(n)}
    for fp in ctx.footprints:
        ref = fp["ref"]
        if not ref.upper().startswith("U"):
            continue
        for pad in fp["pads"]:
            net = pad["net"]
            if net not in supplies:
                continue
            # caps that bridge this rail to ground, with a pad on this rail
            best_dist = None
            best_cap = ""
            for cref, cpad in ctx.net_pins.get(net, []):
                if not cref.upper().startswith("C"):
                    continue
                cfp = ctx.fp_by_ref.get(cref)
                if not cfp or not ({p["net"] for p in cfp["pads"]} & grounds):
                    continue
                cxy = ctx.pad_xy.get((cref, cpad))
                if cxy is None:
                    continue
                d = math.hypot(pad["x"] - cxy[0], pad["y"] - cxy[1])
                if best_dist is None or d < best_dist:
                    best_dist, best_cap = d, cref
            if best_dist is None:
                issues.append({
                    "rule": "ic_pin_no_decoupling", "severity": "warning",
                    "ref": ref, "pad": pad["pad"], "net": net,
                    "description": (
                        f"{ref} supply pin {pad['pad']} on rail {net!r} has no "
                        "decoupling capacitor to ground — add a bypass cap "
                        "(typ. 100nF) next to the pin."),
                })
            elif best_dist > _DECOUPLE_NEAR_MM:
                issues.append({
                    "rule": "ic_pin_decoupling_far", "severity": "info",
                    "ref": ref, "pad": pad["pad"], "net": net,
                    "cap": best_cap, "distance_mm": round(best_dist, 2),
                    "description": (
                        f"{ref} supply pin {pad['pad']} on rail {net!r}: nearest "
                        f"bypass cap {best_cap} is {best_dist:.1f} mm away "
                        f"(>{_DECOUPLE_NEAR_MM:.0f} mm) — move it closer to cut "
                        "supply-loop inductance."),
                })
    return issues


_RESET_NET_RE = re.compile(r"(?:^|[/_])(N?RST|N?RESET|MR|POR)(?:_?N?|B)?$",
                           re.IGNORECASE)


def _check_reset_pullup(ctx: BoardContext) -> list:
    """An active-low reset line (NRST/RESET/…) floats without a pull-up and
    can reset randomly on noise. Info-level, because a supervisor/debug-probe
    may drive it — but a bare, un-pulled reset net is worth flagging. ERC
    can't know a net is a reset."""
    issues = []
    supplies = _supplies(ctx)
    for net in ctx.net_pins:
        base = net.strip().lstrip("/")
        if not _RESET_NET_RE.search(base) or net in ctx.power:
            continue
        if not _bridges(ctx, net, "R", supplies):
            issues.append({
                "rule": "reset_no_pullup", "severity": "info",
                "net": net,
                "description": (
                    f"Reset net {net!r} has no detectable pull-up resistor to a "
                    "supply — an active-low reset should be pulled high (unless a "
                    "supervisor/debug probe drives it)."),
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
    Rule("decoupling", "IC-Pin ohne/entfernte Entkopplung", "warning",
         _check_decoupling),
    Rule("reset_pullup", "Reset-Netz ohne Pull-up", "info",
         _check_reset_pullup),
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
