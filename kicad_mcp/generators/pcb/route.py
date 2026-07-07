# SPDX-License-Identifier: GPL-3.0-or-later
"""PCB-Autorouting: Grid-Suche auf zwei Lagen mit hartem Konfliktmodell.

Ersetzt den MST/L-Shape-Router, der bei Konflikt nur das „kleinere Übel"
wählte und Segmente/Vias notfalls QUER DURCH fremde Pads legte (Messlatte:
486× shorting_items, „Via [SW] auf Pad 2 [VIN] von U1"). Grundregel wie im
Schaltplan-Router: **nie ein fremdes Pad/Segment berühren** — ausweichen
oder per Via die Lage wechseln; findet sich kein Weg, bleibt das Netz
ehrlich offen (DRC zählt es), statt einen Kurzschluss zu zeichnen.

Modell:
  - Raster 0.635 mm, zwei Lagen (F.Cu/B.Cu), Züge orthogonal.
  - Hindernisse je Lage: fremde/netzlose Pads (echte Maße + Clearance +
    halbe Spurbreite), bereits geroutete Segmente/Vias, Board-Rand.
  - THT-/NPTH-Pads (Montagelöcher!) blockieren BEIDE Lagen.
  - Eigene Pads/Segmente des Netzes sind Ziele, keine Hindernisse —
    Mehr-Pad-Netze wachsen als Baum (Multi-Target-Suche → T-Abzweige).
  - Kosten: Schritt 10, Knick 6, Via 60 — kurze gerade Züge mit wenigen
    Vias, wie von Hand.

Callers:
    - kicad_mcp.generators.pcb.builder (_emit_routed_traces_from_placements)
"""

from __future__ import annotations

import heapq
import logging
import re

from ..common.constants import (
    LAYER_B,
    LAYER_F,
    POWER_TRACE_W,
    SIGNAL_TRACE_W,
    VIA_DRILL,
    VIA_SIZE,
)

logger = logging.getLogger(__name__)

_PITCH = 0.635          # Raster (¼ × 2.54) — fein genug für 0603-Gassen
_EDGE_MARGIN = 0.8      # Kupfer-Abstand zur Board-Kante (Edge-Clearance + Luft)
_CLEARANCE = 0.25       # Kupfer-Kupfer-Abstand: JLCPCB min 0.127 + Masken-
#                         Aufweitung (pad_to_mask 0.05, Brücken-Check prüft
#                         APERTUREN, nicht Kupfer) + Rundungs-Luft

_COST_STEP = 10
_COST_TURN = 6
_COST_VIA = 60

_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))

# Zellen, die ein Via-Zylinder (⌀0.8 + Clearance + fremde halbe Spurbreite
# ≈ Radius 0.85) auf BEIDEN Lagen frei braucht — mehr als die eine Zelle,
# sonst schneidet das Via die Nachbarspur (Buck-Messung: „Via [FB] &
# Track [VOUT]").
_VIA_CELLS = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))
_VIA_KEEPOUT = 0.85


class _Grid:
    """Belegtheit je Lage; Zellen tragen die Netz-Nummer des Belegers
    (−1 = für alle blockiert, z. B. netzlose Pads/Montagelöcher)."""

    def __init__(self, x1: float, y1: float, x2: float, y2: float):
        self.x0 = x1 + _EDGE_MARGIN
        self.y0 = y1 + _EDGE_MARGIN
        self.nx = max(2, int((x2 - x1 - 2 * _EDGE_MARGIN) / _PITCH) + 1)
        self.ny = max(2, int((y2 - y1 - 2 * _EDGE_MARGIN) / _PITCH) + 1)
        # occ[layer][iy*nx+ix] = None (frei) | net_num | -1 (hart gesperrt)
        self.occ: list[list] = [[None] * (self.nx * self.ny),
                                [None] * (self.nx * self.ny)]

    def cell(self, x: float, y: float) -> tuple[int, int]:
        return (round((x - self.x0) / _PITCH), round((y - self.y0) / _PITCH))

    def pos(self, ix: int, iy: int) -> tuple[float, float]:
        return (round(self.x0 + ix * _PITCH, 3),
                round(self.y0 + iy * _PITCH, 3))

    def inside(self, ix: int, iy: int) -> bool:
        return 0 <= ix < self.nx and 0 <= iy < self.ny

    def mark_rect(self, layer: int, cx: float, cy: float,
                  half_w: float, half_h: float, net: int) -> None:
        """Zellen im Rechteck für ``net`` belegen; fremd-über-fremd → −1."""
        ix1 = max(0, int((cx - half_w - self.x0) / _PITCH) - 1)
        ix2 = min(self.nx - 1, int((cx + half_w - self.x0) / _PITCH) + 1)
        iy1 = max(0, int((cy - half_h - self.y0) / _PITCH) - 1)
        iy2 = min(self.ny - 1, int((cy + half_h - self.y0) / _PITCH) + 1)
        for iy in range(iy1, iy2 + 1):
            py = self.y0 + iy * _PITCH
            if abs(py - cy) > half_h:
                continue
            base = iy * self.nx
            for ix in range(ix1, ix2 + 1):
                px = self.x0 + ix * _PITCH
                if abs(px - cx) > half_w:
                    continue
                cur = self.occ[layer][base + ix]
                if cur is None:
                    self.occ[layer][base + ix] = net
                elif cur != net:
                    self.occ[layer][base + ix] = -1

    def passable(self, layer: int, ix: int, iy: int, net: int) -> bool:
        cur = self.occ[layer][iy * self.nx + ix]
        return cur is None or cur == net


def _route_edge(grid: _Grid, start: list[tuple[int, int]],
                targets: set[tuple[int, int, int]], net: int,
                start_layers: tuple[int, ...] = (0,),
                max_pops: int = 200_000):
    """Multi-Start/Multi-Target-Suche (Dijkstra mit Knick-/Via-Kosten).

    ``start``: ALLE passierbaren Zellen der Start-Pad-Fläche — die
    Mittelzelle allein kann von der Aufblasung eines dicht benachbarten
    Fremd-Pads überstempelt sein (audio_amp: IN_NODE „nicht routbar",
    obwohl der Pad-Rand frei war).
    ``start_layers``: Lagen, auf denen das Start-Pad PHYSISCH existiert —
    SMD nur F.Cu, THT beide. Der Router darf sonst „gratis" auf B.Cu
    beginnen und hinterlässt eine Spur ohne Via-Anbindung ans Pad (die
    8 unconnected-Reste der ersten Buck-Messung).

    Returns Pfadliste [(ix, iy, layer), …] oder ``None`` (kein Weg)."""
    heap: list = []
    best: dict[tuple[int, int, int, int], int] = {}
    parent: dict = {}
    for (sx, sy) in start:
        for layer in start_layers:
            if grid.passable(layer, sx, sy, net):
                state = (sx, sy, layer, -1)
                best[state] = 0
                heapq.heappush(heap, (0, state))
    goal = None
    pops = 0
    while heap:
        cost, state = heapq.heappop(heap)
        if best.get(state) != cost:
            continue
        pops += 1
        if pops > max_pops:
            return None
        ix, iy, layer, dirno = state
        if (ix, iy, layer) in targets:
            goal = state
            break
        for nd, (dx, dy) in enumerate(_DIRS):
            nix, niy = ix + dx, iy + dy
            if not grid.inside(nix, niy):
                continue
            if not grid.passable(layer, nix, niy, net):
                continue
            ncost = cost + _COST_STEP + (_COST_TURN if dirno not in (-1, nd)
                                         else 0)
            nstate = (nix, niy, layer, nd)
            if ncost < best.get(nstate, 1 << 30):
                best[nstate] = ncost
                parent[nstate] = state
                heapq.heappush(heap, (ncost, nstate))
        other = 1 - layer
        via_ok = all(
            grid.inside(ix + dx, iy + dy)
            and grid.passable(0, ix + dx, iy + dy, net)
            and grid.passable(1, ix + dx, iy + dy, net)
            for dx, dy in _VIA_CELLS)
        if via_ok:
            ncost = cost + _COST_VIA
            nstate = (ix, iy, other, -1)
            if ncost < best.get(nstate, 1 << 30):
                best[nstate] = ncost
                parent[nstate] = state
                heapq.heappush(heap, (ncost, nstate))
    if goal is None:
        return None
    path = []
    st = goal
    while st is not None:
        path.append((st[0], st[1], st[2]))
        st = parent.get(st)
    path.reverse()
    return path


def _emit_path(grid: _Grid, path: list, net_num: int, trace_w: float,
               lines: list) -> None:
    """Pfadzellen → möglichst lange gerade Segmente; Via an Lagenwechseln."""
    if len(path) < 2:
        return
    seg_start = 0
    for i in range(1, len(path) + 1):
        boundary = i == len(path)
        if not boundary:
            xs, ys, _ls = path[seg_start]
            xp, yp, lp = path[i - 1]
            xi, yi, li = path[i]
            if li != lp:
                boundary = True          # Lagenwechsel → Via
            elif i - seg_start >= 2:
                if (xp - xs) * (yi - yp) != (yp - ys) * (xi - xp):
                    boundary = True      # Richtungswechsel → Segment beenden
        if boundary:
            xs, ys, _ls = path[seg_start]
            xp, yp, lp = path[i - 1]
            if (xs, ys) != (xp, yp):
                ax, ay = grid.pos(xs, ys)
                bx, by = grid.pos(xp, yp)
                layer = LAYER_F if lp == 0 else LAYER_B
                lines.append(_segment(ax, ay, bx, by, layer, trace_w,
                                      net_num))
            if i < len(path) and path[i][2] != lp:
                vx, vy = grid.pos(xp, yp)
                lines.append(_via(vx, vy, net_num))
                seg_start = i           # neuer Lauf auf der neuen Lage
            else:
                seg_start = i - 1       # Knick: Ecke gehört beiden Läufen


def route_pcb(
    pad_positions: dict[str, list[tuple[float, float, str]]],
    net_info: dict[str, tuple[int, str]],
    nets: list[dict],
    all_pads: list[tuple[float, float, float, float, int, bool]] | None = None,
    board_rect: tuple[float, float, float, float] | None = None,
) -> str:
    """Alle Netze konfliktfrei routen; Rückgabe S-Expression-Zeilen.

    Args:
        pad_positions: {net_name: [(x, y, "REF:pad"), …]} — zu verbindende Pads
        net_info: {net_name: (net_number, net_name)}
        nets: Original-Netzliste (type → Spurbreite)
        all_pads: JEDES Pad des Boards als (x, y, w, h, net_num, through) —
            das Hindernis-Modell (net_num ≤ 0 = für alle gesperrt).
        board_rect: (x1, y1, x2, y2) Board-Umriss — Kupfer bleibt innen.
    """
    if not board_rect:
        return ""
    net_types = {n["name"]: n.get("type", "signal") for n in nets}

    # Reihenfolge: Power zuerst (breit), dann kleine Netze, dann Name
    # Power zuerst (breite Spuren brauchen Platz), dann GROSSE Netze —
    # ein 3-Pad-Netz zuletzt findet seine Korridore zugebaut vor
    # (audio_amp: IN_NODE unroutbar, sobald alle 2-Pad-Netze vorher dran waren).
    order = sorted(
        (n for n in pad_positions
         if len(pad_positions[n]) >= 2 and n in net_info),
        key=lambda n: (0 if net_types.get(n) == "power" else 1,
                       -len(pad_positions[n]), n))

    lines, unrouted = _route_pass(board_rect, all_pads, pad_positions,
                                  net_info, net_types, order)
    # Rip-up-lite: scheitert ein Netz, wird es von früher gerouteten Netzen
    # zugebaut. EIN Rettungslauf auf frischem Grid mit den gescheiterten Netzen
    # ZUERST (deterministisch) — nur übernehmen, wenn strikt weniger offen
    # bleiben. Boards, die schon voll routen, lösen keinen Retry aus → ihre
    # Ausgabe bleibt byte-identisch (Determinismus/DRC unberührt).
    if unrouted:
        retry_order = ([n for n in order if n in unrouted]
                       + [n for n in order if n not in unrouted])
        lines2, unrouted2 = _route_pass(board_rect, all_pads, pad_positions,
                                        net_info, net_types, retry_order)
        if len(unrouted2) < len(unrouted):
            lines, unrouted = lines2, unrouted2

    logger.info("Router: %d Netze, %d Zeilen, %d Netze unroutbar",
                len(order), len(lines), len(unrouted))
    return "\n".join(lines)


def _route_pass(
    board_rect: tuple[float, float, float, float],
    all_pads: list | None,
    pad_positions: dict,
    net_info: dict,
    net_types: dict,
    order: list[str],
) -> tuple[list[str], set[str]]:
    """Ein vollständiger Routing-Durchgang auf einem FRISCHEN Grid in der
    gegebenen Netz-Reihenfolge. Rückgabe: (Zeilen, Menge unroutbarer Netz-Namen).
    Ausgelagert, damit ``route_pcb`` einen Rip-up-Rettungslauf mit anderer
    Reihenfolge fahren kann."""
    grid = _Grid(*board_rect)

    # Hindernis-Modell: Pads mit Clearance + halber Spurbreite aufblasen.
    infl = _CLEARANCE + max(POWER_TRACE_W, SIGNAL_TRACE_W) / 2
    for (px, py, w, h, net_num, through) in (all_pads or []):
        for layer in ((0, 1) if through else (0,)):
            grid.mark_rect(layer, px, py, w / 2 + infl, h / 2 + infl,
                           net_num if net_num > 0 else -1)

    lines: list[str] = []
    unrouted: set[str] = set()
    for net_name in order:
        net_num, _ = net_info[net_name]
        trace_w = (POWER_TRACE_W if net_types.get(net_name) == "power"
                   else SIGNAL_TRACE_W)
        pads = sorted(pad_positions[net_name],
                      key=lambda p: (p[0], p[1], p[2]))
        pad_cells = []
        for pad in pads:
            px, py = pad[0], pad[1]
            through = bool(pad[3]) if len(pad) > 3 else False
            pw = float(pad[4]) if len(pad) > 4 else 1.0
            ph = float(pad[5]) if len(pad) > 5 else 1.0
            cix, ciy = grid.cell(px, py)
            cix = max(0, min(grid.nx - 1, cix))
            ciy = max(0, min(grid.ny - 1, ciy))
            # ALLE Zellen der Pad-Fläche (Mitte zuerst) — die Mittelzelle
            # allein kann von einer Fremd-Pad-Aufblasung überstempelt sein.
            # (KEIN Escape über die Pad-Spitze hinaus: der Anschluss-Stummel
            # liefe bei 2-Pad-Bauteilen übers eigene Gegen-Pad — Kurzschluss.)
            cells = [(cix, ciy)]
            rx = max(0, int(pw / 2 / _PITCH))
            ry = max(0, int(ph / 2 / _PITCH))
            for dy in range(-ry, ry + 1):
                for dx in range(-rx, rx + 1):
                    c = (cix + dx, ciy + dy)
                    if c != (cix, ciy) and grid.inside(*c):
                        cells.append(c)
            pad_cells.append({"cell": (cix, ciy), "cells": cells,
                              "x": px, "y": py,
                              "layers": (0, 1) if through else (0,),
                              "attach": 0})

        # Baum wachsen lassen: erster Pad = Keim; jeder weitere verbindet
        # zur NÄCHSTEN bereits erreichten Zelle (Pad oder geroutete Spur).
        # Ziele nur auf Lagen, wo wirklich Kupfer liegt (SMD: nur F.Cu).
        seed = pad_cells[0]
        tree: set[tuple[int, int, int]] = {
            (c[0], c[1], layer)
            for c in seed["cells"] for layer in seed["layers"]}
        for pc in pad_cells[1:]:
            if any((c[0], c[1], layer) in tree
                   for c in pc["cells"] for layer in pc["layers"]):
                continue
            path = _route_edge(grid, pc["cells"], tree, net_num,
                               start_layers=pc["layers"])
            if path is None:
                unrouted.add(net_name)
                logger.info("Netz %s: Pad (%.2f, %.2f) nicht routbar",
                            net_name, pc["x"], pc["y"])
                continue
            pc["attach"] = path[0][2]
            pc["cell"] = path[0][0:2]      # Stub dockt an der echten Startzelle an
            if (path[-1][0], path[-1][1], path[-1][2]) in {
                    (c[0], c[1], la) for c in seed["cells"]
                    for la in seed["layers"]}:
                seed["attach"] = path[-1][2]
                seed["cell"] = path[-1][0:2]
            _emit_path(grid, path, net_num, trace_w, lines)
            half = trace_w / 2 + _CLEARANCE
            prev_layer = None
            for (ix, iy, layer) in path:
                x, y = grid.pos(ix, iy)
                grid.mark_rect(layer, x, y, half, half, net_num)
                tree.add((ix, iy, layer))
                if prev_layer is not None and prev_layer != layer:
                    grid.mark_rect(0, x, y, _VIA_KEEPOUT, _VIA_KEEPOUT,
                                   net_num)
                    grid.mark_rect(1, x, y, _VIA_KEEPOUT, _VIA_KEEPOUT,
                                   net_num)
                prev_layer = layer

        # Anschluss-Stummel: exakte Pad-Mitte → Rasterzelle, auf der Lage,
        # auf der die Spur andockt (gleiches Netz, endet IM Pad).
        for pc in pad_cells:
            gx, gy = grid.pos(*pc["cell"])
            if abs(gx - pc["x"]) > 0.01 or abs(gy - pc["y"]) > 0.01:
                layer = LAYER_F if pc["attach"] == 0 else LAYER_B
                lines.append(_segment(pc["x"], pc["y"], gx, gy, layer,
                                      trace_w, net_num))

    return lines, unrouted


def _segment(x1: float, y1: float, x2: float, y2: float,
             layer: str, width: float, net_num: int) -> str:
    return (f'  (segment (start {x1:.3f} {y1:.3f}) (end {x2:.3f} {y2:.3f}) '
            f'(width {width}) (layer "{layer}") (net {net_num}))')


def _via(x: float, y: float, net_num: int) -> str:
    return (f'  (via (at {x:.3f} {y:.3f}) (size {VIA_SIZE}) (drill {VIA_DRILL}) '
            f'(layers "{LAYER_F}" "{LAYER_B}") (net {net_num}))')


def _extract_segment_coords(seg_str: str) -> tuple[float, float, float, float, str] | None:
    """Extract (x1,y1,x2,y2,layer) from a segment S-expression string."""
    start = re.search(r'\(start ([\d.]+) ([\d.]+)\)', seg_str)
    end = re.search(r'\(end ([\d.]+) ([\d.]+)\)', seg_str)
    layer = re.search(r'\(layer "([^"]+)"\)', seg_str)
    if start and end and layer:
        return (float(start.group(1)), float(start.group(2)),
                float(end.group(1)), float(end.group(2)),
                layer.group(1))
    return None
