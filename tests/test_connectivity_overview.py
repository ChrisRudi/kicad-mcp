# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless tests for the connectivity overview clustering (P2 refactor).

_compute_overview / _net_clusters operate on duck-typed pcbnew objects, so we
can exercise the grouping + fragmentation logic with fakes — no KiCad runtime
needed. This pins the O(pads+conn) rewrite to the same output the old
O(nets×pads) version produced: nets with <2 pads are skipped, and a net whose
pads fall into two electrically-disjoint groups is reported as fragmented.
"""

from __future__ import annotations

import kicad_mcp.tools.connectivity_worker as cw


class FakeFP:
    def __init__(self, ref, pads):
        self._ref = ref
        self._pads = pads
        for p in pads:
            p.fp = self

    def GetReference(self):
        return self._ref

    def Pads(self):
        return list(self._pads)


class FakePad:
    def __init__(self, num, netcode):
        self._num = num
        self._net = netcode
        self.fp = None

    def GetClass(self):
        return "PAD"

    def GetNumber(self):
        return self._num

    def GetNetCode(self):
        return self._net

    def GetParentFootprint(self):
        return self.fp


class FakeConn:
    """links: dict mapping a pad -> list of same-net pads it connects to."""
    def __init__(self, links, unconnected=0):
        self._links = links
        self._unconnected = unconnected

    def RecalculateRatsnest(self):
        pass

    def GetUnconnectedCount(self, _flag):
        return self._unconnected

    def GetConnectedItems(self, pad):
        return self._links.get(pad, [])


class FakeNetItem:
    def __init__(self, name):
        self._name = name

    def GetNetname(self):
        return self._name


class FakeNetInfo:
    def __init__(self, names):
        # names: dict netcode -> netname (0 = unconnected pseudo-net)
        self._names = names

    def GetNetCount(self):
        return len(self._names)

    def GetNetItem(self, code):
        name = self._names.get(code)
        return FakeNetItem(name) if name is not None else None


class FakeBoard:
    def __init__(self, fps, conn, netinfo):
        self._fps = fps
        self._conn = conn
        self._netinfo = netinfo

    def GetFootprints(self):
        return list(self._fps)

    def GetConnectivity(self):
        return self._conn

    def GetNetInfo(self):
        return self._netinfo


def _build():
    # net 1: single pad R1.1 -> cannot fragment (skipped)
    # net 2: R1.2, R2.1, R2.2 -> R1.2<->R2.1 linked, R2.2 isolated => 2 clusters
    r1_1 = FakePad("1", 1)
    r1_2 = FakePad("2", 2)
    r2_1 = FakePad("1", 2)
    r2_2 = FakePad("2", 2)
    fps = [FakeFP("R1", [r1_1, r1_2]), FakeFP("R2", [r2_1, r2_2])]
    links = {r1_2: [r2_1], r2_1: [r1_2], r2_2: []}
    conn = FakeConn(links, unconnected=1)
    netinfo = FakeNetInfo({0: "", 1: "VCC", 2: "GND"})
    return FakeBoard(fps, conn, netinfo)


def test_overview_reports_fragmented_net():
    out = cw._compute_overview(_build())
    assert out["success"] is True
    assert out["unconnected_items"] == 1
    assert out["net_count"] == 2  # GetNetCount()-1 (excludes net 0)
    assert out["fragmented_net_count"] == 1
    frag = out["fragmented_nets"][0]
    assert frag["net"] == "GND"
    assert frag["clusters"] == 2
    assert frag["group_sizes"] == [2, 1]  # {R1.2,R2.1} and {R2.2}


def test_overview_skips_single_pad_and_fully_connected_nets():
    # net 2 fully connected: all three link to each other -> one cluster
    r1_1 = FakePad("1", 1)
    r1_2 = FakePad("2", 2)
    r2_1 = FakePad("1", 2)
    r2_2 = FakePad("2", 2)
    fps = [FakeFP("R1", [r1_1, r1_2]), FakeFP("R2", [r2_1, r2_2])]
    net2 = [r1_2, r2_1, r2_2]
    links = {p: [q for q in net2 if q is not p] for p in net2}
    board = FakeBoard(fps, FakeConn(links), FakeNetInfo({0: "", 1: "VCC", 2: "GND"}))
    out = cw._compute_overview(board)
    assert out["fragmented_net_count"] == 0
    assert out["fragmented_nets"] == []


def test_net_clusters_groups_by_identity():
    board = _build()
    pads = cw._all_pads(board)
    pad_ids = {id(p): cw._pad_id(p) for p in pads}
    net2 = [p for p in pads if p.GetNetCode() == 2]
    clusters = cw._net_clusters(board.GetConnectivity(), net2, pad_ids)
    sizes = sorted((len(c) for c in clusters), reverse=True)
    assert sizes == [2, 1]
