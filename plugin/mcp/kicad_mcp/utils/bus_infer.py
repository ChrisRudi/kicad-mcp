# SPDX-License-Identifier: GPL-3.0-or-later
"""Bus inference — group individual nets into *buses* by meaning.

KiCad knows single nets (``SDA``, ``SCL``, ``SPI1_MOSI``); it has no idea that a
set of them forms the I²C bus to a sensor or the data bus of a memory. This pure
module infers that grouping from net names alone (no board runtime), which is
the semantic layer behind the "Bus-Radar" super-feature and the foundation for
group placement / group routing.

Three signals, from strongest to weakest:
  1. **Protocol vocabulary** — a set of known signal names (I²C = SDA+SCL, SPI =
     MOSI/MISO/SCK, …) sharing a common prefix (or bare) is that protocol's bus.
  2. **Numbered bus** — a stem with ≥3 numeric members (``D0..D7``, ``ADDR0..``).
  3. **Differential pair** — ``X_P``/``X_N`` or ``X+``/``X-``.

Pure/stdlib; unit-tested without KiCad.
"""

from __future__ import annotations

import re

# Protocol → its signal tokens (normalised, upper-case).
PROTOCOLS: dict[str, set] = {
    "I2C": {"SDA", "SCL"},
    "SPI": {"MOSI", "MISO", "SCK", "SCLK", "SS", "CS", "NCS", "CSN",
            "SDO", "SDI", "CIPO", "COPI"},
    "UART": {"TX", "RX", "TXD", "RXD", "RTS", "CTS"},
    "USB": {"DP", "DM"},
    "CAN": {"CANH", "CANL"},
    "SWD": {"SWDIO", "SWCLK", "SWO"},
    "JTAG": {"TCK", "TMS", "TDI", "TDO", "TRST"},
}
# Distinct signals needed before a group counts as that protocol's bus.
_MIN_SIGNALS = {"I2C": 2, "SPI": 2, "UART": 2, "USB": 2, "CAN": 2,
                "SWD": 2, "JTAG": 3}

_SEP = re.compile(r"[_\-.]")


def _norm(net: str) -> str:
    """Upper-case, strip a leading hierarchical ``/``, and fold the ``D+``/``D-``
    USB spelling onto ``DP``/``DM`` so both forms group together."""
    n = (net or "").strip().lstrip("/").upper()
    if n.endswith("D+"):
        n = n[:-2] + "DP"
    elif n.endswith("D-"):
        n = n[:-2] + "DM"
    return n


def _signal_and_prefix(net: str):
    """``(protocol, signal, prefix)`` if ``net`` is a known protocol signal, else
    None. ``SDA`` → ``("I2C","SDA","")``; ``I2C1_SDA`` → ``("I2C","SDA","I2C1")``."""
    n = _norm(net)
    for proto, sigs in PROTOCOLS.items():
        if n in sigs:
            return proto, n, ""
    parts = _SEP.split(n)
    if len(parts) >= 2:
        last = parts[-1]
        prefix = "_".join(parts[:-1])
        for proto, sigs in PROTOCOLS.items():
            if last in sigs:
                return proto, last, prefix
    return None


def _protocol_buses(nets: list) -> list:
    groups: dict = {}   # (proto, prefix) -> {"signals": set, "nets": [...]}
    for net in nets:
        hit = _signal_and_prefix(net)
        if not hit:
            continue
        proto, signal, prefix = hit
        g = groups.setdefault((proto, prefix), {"signals": set(), "nets": []})
        g["signals"].add(signal)
        g["nets"].append(net)
    out = []
    for (proto, prefix), g in groups.items():
        if len(g["signals"]) >= _MIN_SIGNALS.get(proto, 2):
            # Don't double a prefix that IS the protocol (USB_DP/USB_DM → "USB",
            # not "USB:USB"); keep a distinct prefix (SPI1_MOSI → "SPI1:SPI").
            label = proto if (not prefix or prefix == proto) else f"{prefix}:{proto}"
            out.append({"bus": label, "kind": proto,
                        "nets": sorted(g["nets"])})
    return out


def _numbered_buses(nets: list, taken: set) -> list:
    stems: dict = {}
    for net in nets:
        if net in taken:
            continue
        m = re.match(r"^(.*?)(\d+)$", _norm(net))
        if m and m.group(1):
            stems.setdefault(m.group(1), []).append(net)
    out = []
    for stem, members in stems.items():
        if len(members) >= 3:
            out.append({"bus": stem.rstrip("_-."), "kind": "numbered",
                        "nets": sorted(members)})
    return out


def _diff_pairs(nets: list, taken: set) -> list:
    bases: dict = {}   # base -> {"P": net, "N": net}
    for net in nets:
        if net in taken:
            continue
        n = _norm(net)
        base = pol = None
        if n.endswith("_P") or n.endswith("+"):
            base, pol = n[:-2] if n.endswith("_P") else n[:-1], "P"
        elif n.endswith("_N") or n.endswith("-"):
            base, pol = n[:-2] if n.endswith("_N") else n[:-1], "N"
        if base:
            bases.setdefault(base, {})[pol] = net
    out = []
    for base, pair in bases.items():
        if "P" in pair and "N" in pair:
            out.append({"bus": base.rstrip("_-."), "kind": "diffpair",
                        "nets": sorted(pair.values())})
    return out


def group_buses(net_names) -> list:
    """Infer buses from ``net_names``. Returns a list of
    ``{"bus": label, "kind": "I2C"|…|"numbered"|"diffpair", "nets": [...]}``,
    protocol buses first. A net belongs to at most one bus (protocol wins over
    numbered/diff)."""
    nets = [str(n) for n in net_names if str(n).strip()]
    proto = _protocol_buses(nets)
    taken = {n for b in proto for n in b["nets"]}
    numbered = _numbered_buses(nets, taken)
    taken |= {n for b in numbered for n in b["nets"]}
    diff = _diff_pairs(nets, taken)
    return proto + numbered + diff
