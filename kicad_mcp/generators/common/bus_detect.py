# SPDX-License-Identifier: GPL-3.0-or-later
"""
Bus-aware grouping for schematic placement.

Which nets form a bus is decided by ONE source in the project:
``utils/bus_infer.group_buses`` (protocol vocabulary, numbered buses,
diff pairs — the same inference behind the Bus-Radar super-feature).
This module only adds what placement needs on top: for each inferred
bus, the component PAIRS that share ≥2 of its nets, plus their pin
pairs for connector/IC alignment.

Callers:
  - schematic/defrag_place.py  (bus-aware connector placement)
"""

from dataclasses import dataclass, field

from ...utils.bus_infer import group_buses


@dataclass
class BusGroup:
    """A group of nets that form a bus between two components."""
    bus_type: str           # "I2C", "SPI1:SPI", "TXD" (numbered stem), ...
    net_names: list[str]    # ["MISO", "MOSI", "SCK"]
    ref_a: str              # e.g. "U1" (IC)
    ref_b: str              # e.g. "J2" (connector)
    pin_pairs: list[tuple[str, str]] = field(default_factory=list)
    # [(ref_a_pin, ref_b_pin), ...] for alignment


def find_bus_groups(nets: list[dict]) -> list[BusGroup]:
    """Find all bus groups in the netlist.

    A bus group is a set of nets of ONE inferred bus (see module
    docstring) that connect the same two components.
    """
    signal_nets = [n for n in nets if n.get("type") != "power"]

    # Netzname → Bus-Label, aus der EINEN Inferenz (bus_infer)
    bus_label: dict[str, str] = {}
    for bus in group_buses([n["name"] for n in signal_nets]):
        for name in bus["nets"]:
            bus_label[name] = bus["bus"]

    # Bus-Netze mit ihren Endpunkt-Refs einsammeln
    bus_nets: dict[str, list[tuple[str, set[str]]]] = {}  # label → [(net, {refs})]
    for net in signal_nets:
        label = bus_label.get(net["name"])
        if not label:
            continue
        refs = {conn.split(":")[0] for conn in net.get("connections", [])
                if conn.split(":")[0]}
        if len(refs) >= 2:
            bus_nets.setdefault(label, []).append((net["name"], refs))

    # Netze desselben Busses gruppieren, die dasselbe Ref-Paar teilen
    groups: list[BusGroup] = []
    for label, net_list in bus_nets.items():
        pair_nets: dict[tuple[str, str], list[str]] = {}
        for name, refs in net_list:
            ref_list = sorted(refs)
            for i, ra in enumerate(ref_list):
                for rb in ref_list[i + 1:]:
                    pair_nets.setdefault((ra, rb), []).append(name)

        for (ra, rb), names in pair_nets.items():
            if len(names) >= 2:  # mindestens 2 Signale = Bus
                # Pin-Paare für die Ausrichtung einsammeln
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
                    bus_type=label,
                    net_names=names,
                    ref_a=ra,
                    ref_b=rb,
                    pin_pairs=pin_pairs,
                ))

    return groups
