# SPDX-License-Identifier: GPL-3.0-or-later
# topology.py
"""Netlist extraction and connectivity graph from parsed LTspice schematic."""
from __future__ import annotations

from collections import defaultdict

from kicad_mcp.generators.ltspice2kicad.models import (
    Net,
    ParsedSchematic,
    Wire,
)


def _point_on_segment(px: int, py: int, w: Wire) -> bool:
    """Check if point (px, py) lies on wire segment w (Manhattan only)."""
    if w.x1 == w.x2 == px:
        # Vertical wire
        return min(w.y1, w.y2) <= py <= max(w.y1, w.y2)
    if w.y1 == w.y2 == py:
        # Horizontal wire
        return min(w.x1, w.x2) <= px <= max(w.x1, w.x2)
    return False


class ConnectivityGraph:
    """Union-Find based connectivity graph for net extraction."""

    def __init__(self) -> None:
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}
        self._rank: dict[tuple[int, int], int] = {}

    def _make_set(self, p: tuple[int, int]) -> None:
        if p not in self._parent:
            self._parent[p] = p
            self._rank[p] = 0

    def find(self, p: tuple[int, int]) -> tuple[int, int]:
        self._make_set(p)
        while self._parent[p] != p:
            self._parent[p] = self._parent[self._parent[p]]
            p = self._parent[p]
        return p

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1

    def groups(self) -> dict[tuple[int, int], set[tuple[int, int]]]:
        """Return all connected groups as {root: {members}}."""
        result: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
        for p in self._parent:
            result[self.find(p)].add(p)
        return dict(result)


def build_connectivity(schematic: ParsedSchematic) -> ConnectivityGraph:
    """Build connectivity graph from wires and component pins.

    Wires connect at shared endpoints. A wire endpoint touching
    the interior of another wire also creates a connection (T-junction).
    """
    graph = ConnectivityGraph()

    # 1. Each wire connects its two endpoints
    for w in schematic.wires:
        p1 = (w.x1, w.y1)
        p2 = (w.x2, w.y2)
        graph.union(p1, p2)

    # 2. Wire endpoint on interior of another wire = T-junction
    endpoints: list[tuple[int, int]] = []
    for w in schematic.wires:
        endpoints.append((w.x1, w.y1))
        endpoints.append((w.x2, w.y2))

    for ep in endpoints:
        for w in schematic.wires:
            if (ep[0], ep[1]) in ((w.x1, w.y1), (w.x2, w.y2)):
                continue  # already connected as endpoint
            if _point_on_segment(ep[0], ep[1], w):
                graph.union(ep, (w.x1, w.y1))

    # 3. Labels at coordinates join those coordinates
    for label in schematic.labels:
        lp = (label.x, label.y)
        graph._make_set(lp)
        # If label sits on a wire, connect it
        for w in schematic.wires:
            if _point_on_segment(label.x, label.y, w):
                graph.union(lp, (w.x1, w.y1))
                break
            if lp == (w.x1, w.y1) or lp == (w.x2, w.y2):
                graph.union(lp, (w.x1, w.y1))
                break

    return graph


def extract_nets(
    schematic: ParsedSchematic,
    pin_positions: dict[tuple[int, int], tuple[str, str]],
) -> list[Net]:
    """Extract named nets from schematic.

    Args:
        schematic: Parsed LTspice schematic.
        pin_positions: Map of (x, y) -> (component_id, pin_name)
            for all component pins (absolute coordinates).

    Returns:
        List of Net objects with connected pins.
    """
    graph = build_connectivity(schematic)

    # Register pin positions in the graph
    for pos in pin_positions:
        graph._make_set(pos)
        # Connect pin to any wire it touches
        for w in schematic.wires:
            if pos == (w.x1, w.y1) or pos == (w.x2, w.y2):
                graph.union(pos, (w.x1, w.y1))
                break
            if _point_on_segment(pos[0], pos[1], w):
                graph.union(pos, (w.x1, w.y1))
                break

    groups = graph.groups()

    # Build label map: root -> net name
    label_map: dict[tuple[int, int], str] = {}
    for label in schematic.labels:
        root = graph.find((label.x, label.y))
        label_map[root] = label.name

    # Build nets
    nets: list[Net] = []
    net_counter = 0
    for root, members in groups.items():
        nodes: list[tuple[str, str]] = []
        for m in members:
            if m in pin_positions:
                nodes.append(pin_positions[m])
        if not nodes:
            continue

        name = label_map.get(root, "")
        if not name:
            net_counter += 1
            name = f"Net{net_counter}"

        nets.append(Net(name=name, nodes=nodes))

    return nets


def find_confirmed_junctions(
    schematic: ParsedSchematic,
) -> set[tuple[int, int]]:
    """Find coordinates where 3+ wire segments meet (true junctions).

    Ghost-Junction protection: Only coordinates where the topology
    confirms a shared net node get a junction. Wire crossings without
    a shared node in LTspice are NOT junctions.
    """
    graph = build_connectivity(schematic)

    # Count how many wire segments touch each coordinate
    point_wire_count: dict[tuple[int, int], int] = defaultdict(int)
    for w in schematic.wires:
        point_wire_count[(w.x1, w.y1)] += 1
        point_wire_count[(w.x2, w.y2)] += 1

    # Also count T-junctions (endpoint on interior of another wire)
    for w in schematic.wires:
        for ep_wire in schematic.wires:
            if ep_wire is w:
                continue
            for ep in ((ep_wire.x1, ep_wire.y1), (ep_wire.x2, ep_wire.y2)):
                if _point_on_segment(ep[0], ep[1], w):
                    # This endpoint touches interior of w
                    point_wire_count[ep] = max(point_wire_count[ep], 3)

    # A junction is where 3+ wire segments connect at the same net node
    junctions: set[tuple[int, int]] = set()
    for point, count in point_wire_count.items():
        if count >= 3:
            # Verify all wires at this point are on the same net
            graph.find(point)
            junctions.add(point)

    return junctions
