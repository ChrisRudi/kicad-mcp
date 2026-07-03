# SPDX-License-Identifier: GPL-3.0-or-later
"""Test-Punkt-Wächter — can this board be probed in bring-up / production test?

KiCad knows nets but not which nets *deserve* probe access. For flying-probe /
bed-of-nails test and for bring-up you need to reach the nets that matter — power
rails, reset, clock, bus lines. A critical net with no test point and no
connector is *blind*: you find out only when the board is in hand. ERC/DRC never
check this — they have no notion of net *importance*.

This ranks nets by importance (reusing ``design_rules`` power/reset/bus signals)
and checks each critical net for a probe-able access point (a ``TP*`` test point,
a ``TestPoint`` footprint, or a connector). Reports coverage % and the blind
critical nets. Pure/stdlib on top of the shared ``BoardContext`` — no re-parse.
"""

from __future__ import annotations

import re
from typing import Any

from kicad_mcp.utils import design_rules

# Nets whose name says "clock" — clocks want probe access for bring-up.
_CLOCK_RE = re.compile(r"(?:^|[/_])(CLK|XTAL|OSC|XIN|XOUT)(?:[/_]|$)|MHZ",
                       re.IGNORECASE)

# A dedicated test-point reference (TP1, TP12, …).
_TP_REF_RE = re.compile(r"^TP\d", re.IGNORECASE)

# Footprint ids that expose a net to a probe/plug.
_ACCESS_FPID_RE = re.compile(r"TestPoint|Connector|PinHeader|PinSocket",
                             re.IGNORECASE)

# Priority tiers, most-critical first; the CRITICAL set drives the coverage %.
_PRIORITY_ORDER = {"power": 0, "reset": 1, "clock": 2, "bus": 3, "signal": 4}
_CRITICAL = frozenset({"power", "reset", "clock", "bus"})


def _is_access_fp(fp: dict[str, Any]) -> bool:
    """True if this footprint is a probe access point (test point / connector)."""
    ref = (fp.get("ref") or "").upper()
    if _TP_REF_RE.match(ref) or ref.startswith("J"):
        return True
    return bool(_ACCESS_FPID_RE.search(fp.get("fpid", "")))


def _priority(net: str, ctx: "design_rules.BoardContext",
              bus_of: dict[str, str]) -> str:
    """Rank a (non-ground) net into a probe-importance tier."""
    if net in ctx.power:                      # power rail (ground filtered out)
        return "power"
    base = net.strip().lstrip("/")
    if design_rules._RESET_NET_RE.search(base):
        return "reset"
    if _CLOCK_RE.search(net):
        return "clock"
    if net in bus_of:
        return "bus"
    return "signal"


def evaluate_test_points(ctx: "design_rules.BoardContext",
                         include_signals: bool = False,
                         nets: set | None = None) -> dict[str, Any]:
    """Score probe-access coverage of the important nets on a board.

    Args:
        ctx: the shared board context (``design_rules.build_context``).
        include_signals: also list ordinary signal nets (they never count toward
            the critical coverage %, just get reported).
        nets: optional net-name filter (selection scope); ``None`` = all nets.

    Returns ``{coverage_pct, critical_total, critical_covered, critical_blind,
    blind_nets:[{net,priority}], nets:[{net,priority,covered,via,bus}]}``.
    """
    access_refs = {fp["ref"] for fp in ctx.footprints if _is_access_fp(fp)}
    bus_of: dict[str, str] = {}
    for b in ctx.buses:
        for n in b["nets"]:
            bus_of[n] = b["bus"]

    rows: list[dict[str, Any]] = []
    for net, pins in ctx.net_pins.items():
        if nets is not None and net not in nets:
            continue
        if design_rules._is_ground(net):     # ground is ubiquitous → not audited
            continue
        prio = _priority(net, ctx, bus_of)
        if prio == "signal" and not include_signals:
            continue
        covering = sorted({r for r, _pad in pins if r in access_refs})
        rows.append({
            "net": net, "priority": prio, "covered": bool(covering),
            "via": covering, "bus": bus_of.get(net, ""),
        })

    crit = [r for r in rows if r["priority"] in _CRITICAL]
    covered = sum(1 for r in crit if r["covered"])
    blind = [r for r in crit if not r["covered"]]
    cov_pct = round(100.0 * covered / len(crit), 1) if crit else 100.0

    rows.sort(key=lambda r: (r["covered"],
                             _PRIORITY_ORDER.get(r["priority"], 9), r["net"]))
    return {
        "coverage_pct": cov_pct,
        "critical_total": len(crit),
        "critical_covered": covered,
        "critical_blind": len(blind),
        "blind_nets": [{"net": r["net"], "priority": r["priority"]}
                       for r in sorted(blind, key=lambda r: (
                           _PRIORITY_ORDER.get(r["priority"], 9), r["net"]))],
        "nets": rows,
    }
