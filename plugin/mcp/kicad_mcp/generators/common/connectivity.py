# SPDX-License-Identifier: GPL-3.0-or-later
"""
Net-graph utilities and MST construction.

Extracted from auto_place.py and schematic_builder.py.

Callers:
  - auto_place.py          (_net_refs)
  - schematic_builder.py   (_build_mst_edges — fuer Wire-Routing MST)
  - auto_place.py          (_build_mst_edges — via schematic_builder lazy import fuer
                             smart-rotation pin-link routing)
"""

from collections import defaultdict


def _net_refs(nets: list[dict]) -> dict[str, set[str]]:
    """net_name -> set of refs on that net."""
    result: dict[str, set[str]] = {}
    for net in nets:
        refs = set()
        for conn in net.get("connections", []):
            ref = conn.split(":")[0]
            if ref:
                refs.add(ref)
        result[net["name"]] = refs
    return result


def _build_mst_edges(
    pins: list[tuple],
) -> list[tuple[int, int]]:
    """Build Minimum Spanning Tree edges for a set of pins.

    Uses Prim's algorithm with Manhattan distance.
    Returns list of (index_a, index_b) pairs.
    """
    n = len(pins)
    if n <= 1:
        return []
    if n == 2:
        return [(0, 1)]

    in_tree = {0}
    edges = []

    while len(in_tree) < n:
        best_dist = float("inf")
        best_edge = (0, 0)
        for i in in_tree:
            for j in range(n):
                if j in in_tree:
                    continue
                dx = abs(pins[i][0] - pins[j][0])
                dy = abs(pins[i][1] - pins[j][1])
                dist = dx + dy  # Manhattan
                if dist < best_dist:
                    best_dist = dist
                    best_edge = (i, j)
        edges.append(best_edge)
        in_tree.add(best_edge[1])

    return edges


def _build_connection_graph(
    nets: list[dict],
) -> tuple[dict[str, list[tuple[str, str, str]]], dict[str, int]]:
    """Build full connectivity graph from nets.

    Returns:
        connections: ref → [(net_name, other_ref, net_type), ...]
        conn_count:  ref → total connection count (edges)
    """
    connections: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    conn_count: dict[str, int] = defaultdict(int)

    for net in nets:
        net_name = net["name"]
        net_type = net.get("type", "signal")
        # sorted(): members ist eine STRING-Menge — ihre Iterations-Reihenfolge
        # wechselt mit PYTHONHASHSEED pro Prozess. Unsortiert war die gesamte
        # Platzierung (Kanten-/Zähl-Reihenfolge → Tie-Breaks) von Lauf zu Lauf
        # verschieden: „flaky" Optimizer-Tests, springende Galerie-badness.
        members = sorted({conn.split(":")[0]
                          for conn in net.get("connections", [])
                          if conn.split(":")[0]})
        for ref in members:
            for other in members:
                if other != ref:
                    connections[ref].append((net_name, other, net_type))
            conn_count[ref] += len(members) - 1

    return dict(connections), dict(conn_count)
