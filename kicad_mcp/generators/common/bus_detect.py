# SPDX-License-Identifier: GPL-3.0-or-later
"""
Bus signal detection for schematic and PCB placement.

Detects common bus protocols (SPI, I2C, UART, JTAG, CAN, USB, I2S)
from net names and groups them. Bus-aware placement aligns connector
pins with IC pins for parallel routing.

Callers:
  - schematic/defrag_place.py  (bus-aware connector placement)
  - pcb/place.py               (bus-aware footprint placement)
"""

from dataclasses import dataclass, field
import re


@dataclass
class BusGroup:
    """A group of nets that form a bus between two components."""
    bus_type: str           # "SPI", "I2C", "UART", etc.
    net_names: list[str]    # ["MISO", "MOSI", "SCK"]
    ref_a: str              # e.g. "U1" (IC)
    ref_b: str              # e.g. "J2" (connector)
    pin_pairs: list[tuple[str, str]] = field(default_factory=list)
    # [(ref_a_pin, ref_b_pin), ...] for alignment


# Bus patterns: net name regex → bus type
_BUS_PATTERNS = [
    # SPI
    (re.compile(r"(?i)^(MISO|MOSI|SCK|SCLK|SS|CS|NSS|SPI_)"), "SPI"),
    # I2C
    (re.compile(r"(?i)^(SDA|SCL|I2C_)"), "I2C"),
    # UART
    (re.compile(r"(?i)^(TX|RX|TXD|RXD|UART_)"), "UART"),
    # JTAG/SWD
    (re.compile(r"(?i)^(TDI|TDO|TMS|TCK|TRST|SWDIO|SWCLK|SWO)"), "JTAG"),
    # CAN
    (re.compile(r"(?i)^(CANH|CANL|CAN_)"), "CAN"),
    # USB
    (re.compile(r"(?i)^(D\+|D-|USB_|VBUS|DP|DM)"), "USB"),
    # I2S
    (re.compile(r"(?i)^(BCLK|LRCLK|DIN|DOUT|I2S_|MCLK)"), "I2S"),
    # Parallel data/address
    (re.compile(r"(?i)^[DA]\d+$"), "PARALLEL"),
]


def detect_bus_type(net_name: str) -> str | None:
    """Return bus type for a net name, or None."""
    for pattern, bus_type in _BUS_PATTERNS:
        if pattern.match(net_name):
            return bus_type
    return None


def find_bus_groups(nets: list[dict]) -> list[BusGroup]:
    """Find all bus groups in the netlist.

    A bus group is a set of signal nets with bus-like names that
    connect the same two components.
    """
    # Collect bus-type nets with their endpoint refs
    bus_nets: dict[str, list[tuple[str, str, set[str]]]] = {}  # bus_type → [(net_name, bus_type, {refs})]

    for net in nets:
        if net.get("type") == "power":
            continue
        name = net["name"]
        bt = detect_bus_type(name)
        if not bt:
            continue
        refs = set()
        for conn in net.get("connections", []):
            ref = conn.split(":")[0]
            if ref:
                refs.add(ref)
        if len(refs) >= 2:
            bus_nets.setdefault(bt, []).append((name, bt, refs))

    # Group nets of same bus type that share the same ref pair
    groups: list[BusGroup] = []
    for bt, net_list in bus_nets.items():
        # Find pairs of refs that appear in multiple nets of this bus type
        pair_nets: dict[tuple[str, str], list[str]] = {}
        for name, _, refs in net_list:
            ref_list = sorted(refs)
            for i, ra in enumerate(ref_list):
                for rb in ref_list[i + 1:]:
                    pair = (ra, rb)
                    pair_nets.setdefault(pair, []).append(name)

        for (ra, rb), names in pair_nets.items():
            if len(names) >= 2:  # at least 2 signals = bus
                # Collect pin pairs for alignment
                pin_pairs = []
                for name in names:
                    net = next(n for n in nets if n["name"] == name)
                    pin_a = pin_b = ""
                    for conn in net.get("connections", []):
                        ref, pin = conn.split(":", 1) if ":" in conn else (conn, "")
                        if ref == ra:
                            pin_a = pin
                        elif ref == rb:
                            pin_b = pin
                    if pin_a and pin_b:
                        pin_pairs.append((pin_a, pin_b))

                groups.append(BusGroup(
                    bus_type=bt,
                    net_names=names,
                    ref_a=ra,
                    ref_b=rb,
                    pin_pairs=pin_pairs,
                ))

    return groups
