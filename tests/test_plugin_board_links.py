# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the chat<->board cross-probe linking. The pure tokenizer is
fully tested; the kipy side is exercised with fake board/client objects (the
real kipy import only happens inside KiCad).
"""

from __future__ import annotations

from types import SimpleNamespace

from plugin import board_links


# -- pure: tokenize -----------------------------------------------------------

class TestTokenize:
    def test_links_only_known_refs(self):
        segs = board_links.tokenize("R12 ist groß, R99 nicht auf dem Board",
                                    known_refs={"R12"})
        # R12 becomes a link, R99 (not on board) stays plain text
        assert ("R12", ("ref", "R12")) in segs
        plain = "".join(c for c, t in segs if t is None)
        assert "R99" in plain
        assert "".join(c for c, _ in segs) == "R12 ist groß, R99 nicht auf dem Board"

    def test_no_substring_false_positive(self):
        # R1 must NOT match inside R12 or inside a net name R1_OUT
        segs = board_links.tokenize("R12 und R1_OUT", known_refs={"R1"})
        assert all(t is None for _, t in segs)  # nothing linked

    def test_longest_token_wins(self):
        segs = board_links.tokenize("GND_1 fix", known_refs=set(),
                                    known_nets={"GND", "GND_1"})
        assert ("GND_1", ("net", "GND_1")) in segs

    def test_ref_beats_net_on_tie(self):
        segs = board_links.tokenize("NET1 da", known_refs={"NET1"},
                                    known_nets={"NET1"})
        assert ("NET1", ("ref", "NET1")) in segs

    def test_round_trips_text_exactly(self):
        text = "Die Vias auf GND und R5 sowie U10 prüfen."
        segs = board_links.tokenize(text, known_refs={"R5", "U10"},
                                    known_nets={"GND"})
        assert "".join(c for c, _ in segs) == text
        linked = {v for _, t in segs if t for (_k, v) in [t]}
        assert linked == {"R5", "U10", "GND"}

    def test_empty_targets_is_all_plain(self):
        assert board_links.tokenize("nix hier", known_refs=set()) == [
            ("nix hier", None)]


class TestCoordinateLinks:
    def test_parenthesized_pair_links(self):
        segs = board_links.tokenize("Via bei (120.5, 84.0) liegt frei",
                                    known_refs=set())
        assert ("(120.5, 84.0)", ("coord", (120.5, 84.0))) in segs
        assert "".join(c for c, _ in segs) == "Via bei (120.5, 84.0) liegt frei"

    def test_mm_units_and_negative(self):
        segs = board_links.tokenize("Ecke (-3.2 mm, 10 mm)", known_refs=set())
        coord = next(t for _, t in segs if t)
        assert coord == ("coord", (-3.2, 10.0))

    def test_bare_comma_in_prose_not_linked(self):
        # no parentheses -> not a coordinate (avoids false positives)
        segs = board_links.tokenize("erst R1, dann R2", known_refs=set())
        assert all(t is None for _, t in segs)

    def test_coords_need_no_board_data(self):
        segs = board_links.tokenize("Punkt (5, 6)", known_refs=set(),
                                    known_nets=set())
        kinds = [kind for _, t in segs if t for (kind, _v) in [t]]
        assert "coord" in kinds

    def test_refs_and_coords_together(self):
        segs = board_links.tokenize("R5 sitzt bei (40, 30)", known_refs={"R5"})
        kinds = [kind for _, t in segs if t for (kind, _v) in [t]]
        assert kinds == ["ref", "coord"]


# -- kipy side with fakes -----------------------------------------------------

def _fp(ref):
    return SimpleNamespace(
        reference_field=SimpleNamespace(text=SimpleNamespace(value=ref)))


class _FakeBoard:
    def __init__(self, refs=(), nets=(), net_items=None):
        self._fps = [_fp(r) for r in refs]
        self._nets = [SimpleNamespace(name=n) for n in nets]
        self._net_items = net_items or {}
        self.selection = None
        self.cleared = False

    def get_footprints(self):
        return list(self._fps)

    def get_nets(self):
        return list(self._nets)

    def get_items_by_net(self, net):
        return self._net_items.get(net.name, [])

    def clear_selection(self):
        self.cleared = True
        self.selection = []

    def add_to_selection(self, items):
        self.selection = list(items)


class _FakeClient:
    def __init__(self):
        self.actions = []

    def run_action(self, action):
        self.actions.append(action)


class TestBoardTargets:
    def test_collects_refs_and_nets(self):
        board = _FakeBoard(refs=["R1", "U2"], nets=["GND", "VCC"])
        refs, nets = board_links.board_targets(board)
        assert refs == {"R1", "U2"} and nets == {"GND", "VCC"}

    def test_survives_partial_failures(self):
        board = _FakeBoard(refs=["R1"], nets=["GND"])
        board.get_nets = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        refs, nets = board_links.board_targets(board)
        assert refs == {"R1"} and nets == set()


class TestSelect:
    def test_select_ref_highlights_and_zooms(self):
        board = _FakeBoard(refs=["R1", "R2"])
        client = _FakeClient()
        n = board_links.select(client, board, "ref", "R2")
        assert n == 1 and board.cleared
        assert board.selection and board_links._ref_of(board.selection[0]) == "R2"
        assert client.actions  # zoom action attempted

    def test_select_net_uses_items_by_net(self):
        items = ["a", "b", "c"]
        board = _FakeBoard(nets=["GND"], net_items={"GND": items})
        client = _FakeClient()
        n = board_links.select(client, board, "net", "GND")
        assert n == 3 and board.selection == items

    def test_no_match_clears_only(self):
        board = _FakeBoard(refs=["R1"])
        client = _FakeClient()
        n = board_links.select(client, board, "ref", "X9")
        assert n == 0 and board.cleared
        assert not client.actions  # nothing to zoom to

    def test_zoom_can_be_disabled(self):
        board = _FakeBoard(refs=["R1"])
        client = _FakeClient()
        board_links.select(client, board, "ref", "R1", zoom=False)
        assert not client.actions

    def test_zoom_falls_back_to_second_action(self):
        board = _FakeBoard(refs=["R1"])
        calls = []

        def _run(action):
            calls.append(action)
            if len(calls) == 1:
                raise RuntimeError("unknown action")

        client = SimpleNamespace(run_action=_run)
        board_links.select(client, board, "ref", "R1")
        assert calls == list(board_links._ZOOM_ACTIONS)


def _pos_item(x_mm, y_mm):
    return SimpleNamespace(position=SimpleNamespace(
        x=int(x_mm * 1_000_000), y=int(y_mm * 1_000_000)))


class _CoordBoard:
    def __init__(self, footprints=(), vias=(), pads=()):
        self._fps, self._vias, self._pads = footprints, vias, pads
        self.selection = None
        self.cleared = False

    def get_footprints(self):
        return list(self._fps)

    def get_vias(self):
        return list(self._vias)

    def get_pads(self):
        return list(self._pads)

    def clear_selection(self):
        self.cleared = True

    def add_to_selection(self, items):
        self.selection = list(items)


class TestSelectCoord:
    def test_picks_nearest_element_and_zooms(self):
        far = _pos_item(0, 0)
        near = _pos_item(50.2, 30.1)
        board = _CoordBoard(footprints=[far], vias=[near])
        client = _FakeClient()
        d = board_links.select_coord(client, board, 50.0, 30.0)
        assert d is not None and d < 0.5
        assert board.selection == [near] and client.actions

    def test_nothing_within_radius_returns_none(self):
        board = _CoordBoard(footprints=[_pos_item(0, 0)])
        client = _FakeClient()
        d = board_links.select_coord(client, board, 200.0, 200.0,
                                     radius_mm=5.0)
        assert d is None and board.cleared
        assert board.selection is None and not client.actions

    def test_radius_boundary(self):
        board = _CoordBoard(vias=[_pos_item(3.0, 4.0)])  # 5 mm from origin
        client = _FakeClient()
        assert board_links.select_coord(client, board, 0.0, 0.0,
                                        radius_mm=5.0) is not None
        assert board_links.select_coord(client, board, 0.0, 0.0,
                                        radius_mm=4.9) is None
