# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the chat<->board cross-probe linking. The pure tokenizer is
fully tested; the kipy side is exercised with fake board/client objects (the
real kipy import only happens inside KiCad).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin import board_links


class TestBusyRetry:
    """Once the MCP server holds KiCad's IPC, the panel's cross-probe calls hit
    'KiCad is busy'. They must retry, not silently drop every link."""

    def test_call_retries_busy_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(board_links.time, "sleep", lambda *_a: None)
        calls = {"n": 0}

        def _flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("KiCad is busy and cannot respond")
            return "ok"

        assert board_links.call(_flaky) == "ok" and calls["n"] == 3

    def test_call_reraises_non_busy_immediately(self, monkeypatch):
        monkeypatch.setattr(board_links.time, "sleep", lambda *_a: None)
        calls = {"n": 0}

        def _boom():
            calls["n"] += 1
            raise ValueError("kaputt")  # NB: must not contain 'busy'

        with pytest.raises(ValueError):
            board_links.call(_boom)
        assert calls["n"] == 1  # no retry on a non-busy error

    def test_board_targets_survives_transient_busy(self, monkeypatch):
        monkeypatch.setattr(board_links.time, "sleep", lambda *_a: None)
        state = {"n": 0}
        fps = [SimpleNamespace(reference_field=SimpleNamespace(
            text=SimpleNamespace(value="R1")))]

        def _busy_once():
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("KiCad is busy")
            return fps

        board = SimpleNamespace(
            get_footprints=_busy_once,
            get_nets=lambda: [],
            get_enabled_layers=lambda: [])
        refs, _nets, _layers = board_links.board_targets(board)
        assert refs == {"R1"}  # retried, link survived


# -- disk fallback (no kipy) --------------------------------------------------

class TestBoardTargetsFromFile:
    """When live IPC can't resolve the board (multi-instance), the panel parses
    refs/nets/layers straight from the .kicad_pcb so links still render."""

    _PCB = (
        '(kicad_pcb (version 20240108)\n'
        '  (layers\n'
        '    (0 "F.Cu" signal)\n'
        '    (31 "B.Cu" signal)\n'
        '    (37 "F.SilkS" user)\n'
        '  )\n'
        '  (net 0 "")\n'
        '  (net 1 "GND")\n'
        '  (net 2 "+3V3")\n'
        '  (net 3 VCC)\n'
        '  (footprint "R_0402"\n'
        '    (property "Reference" "R_GATE_PD1" (at 0 0))\n'
        '    (pad "1" smd (net 1 "GND"))\n'
        '  )\n'
        '  (footprint "R_0402"\n'
        '    (property "Reference" "R_FAULT1" (at 1 1))\n'
        '  )\n'
        ')\n'
    )

    def test_parses_refs_nets_layers(self, tmp_path):
        p = tmp_path / "board.kicad_pcb"
        p.write_text(self._PCB, encoding="utf-8")
        refs, nets, layers = board_links.board_targets_from_file(str(p))
        assert refs == {"R_GATE_PD1", "R_FAULT1"}
        assert {"GND", "+3V3", "VCC"} <= nets and "" not in nets
        assert {"F.Cu", "B.Cu", "F.SilkS"} <= layers

    def test_missing_file_yields_empty(self):
        assert board_links.board_targets_from_file("/nope/x.kicad_pcb") == (
            set(), set(), set())

    def test_fallback_output_linkifies(self, tmp_path):
        p = tmp_path / "b.kicad_pcb"
        p.write_text(self._PCB, encoding="utf-8")
        refs, nets, layers = board_links.board_targets_from_file(str(p))
        segs = board_links.tokenize("| R_GATE_PD1 | 100k | auf F.Cu, Netz GND |",
                                    refs, nets, layers)
        links = {t for _c, t in segs if t}
        assert ("ref", "R_GATE_PD1") in links
        assert ("net", "GND") in links
        assert ("layer", "F.Cu") in links


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


class TestPinLinks:
    def test_pin_token_links_to_ref_and_pin(self):
        segs = board_links.tokenize("Pin U1B.33 ist heiß",
                                    known_refs={"U1B"})
        assert ("U1B.33", ("pin", ("U1B", "33"))) in segs
        assert "".join(c for c, _ in segs) == "Pin U1B.33 ist heiß"

    def test_pin_takes_precedence_over_bare_ref(self):
        # the whole U1B.33 must be ONE pin link, not a ref "U1B" + ".33"
        segs = board_links.tokenize("U1B.33", known_refs={"U1B"})
        assert segs == [("U1B.33", ("pin", ("U1B", "33")))]

    def test_bare_ref_still_links_without_pin(self):
        segs = board_links.tokenize("U1B treiben", known_refs={"U1B"})
        assert ("U1B", ("ref", "U1B")) in segs

    def test_alpha_pin_names(self):
        segs = board_links.tokenize("J3.A1 prüfen", known_refs={"J3"})
        assert ("J3.A1", ("pin", ("J3", "A1"))) in segs

    def test_unknown_ref_pin_not_linked(self):
        segs = board_links.tokenize("X9.7 nicht am Board", known_refs={"U1"})
        assert all(t is None for _, t in segs)

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

def _fp(ref, pads=()):
    pad_objs = [SimpleNamespace(number=n, id=f"{ref}-{n}") for n in pads]
    return SimpleNamespace(
        reference_field=SimpleNamespace(text=SimpleNamespace(value=ref)),
        definition=SimpleNamespace(pads=pad_objs))


class _PinBoard:
    def __init__(self, footprints):
        self._fps = footprints
        self.selection = None
        self.cleared = False

    def get_footprints(self):
        return list(self._fps)

    def clear_selection(self):
        self.cleared = True

    def add_to_selection(self, items):
        self.selection = list(items)


class TestSelectPin:
    def test_selects_matching_pad_and_zooms(self):
        board = _PinBoard([_fp("U1B", pads=["1", "33", "GND"])])
        client = _FakeClient()
        n = board_links.select_pin(client, board, "U1B", "33")
        assert n == 1 and board.cleared
        assert board.selection[0].id == "U1B-33" and client.actions

    def test_unknown_pin_selects_nothing(self):
        board = _PinBoard([_fp("U1B", pads=["1", "2"])])
        client = _FakeClient()
        assert board_links.select_pin(client, board, "U1B", "99") == 0
        assert board.selection is None and not client.actions

    def test_unknown_ref(self):
        board = _PinBoard([_fp("U1B", pads=["1"])])
        client = _FakeClient()
        assert board_links.select_pin(client, board, "X9", "1") == 0


class _FakeBoard:
    def __init__(self, refs=(), nets=(), net_items=None, layers=()):
        self._fps = [_fp(r) for r in refs]
        self._nets = [SimpleNamespace(name=n) for n in nets]
        self._net_items = net_items or {}
        self._layers = list(layers)  # BoardLayer enum ints
        self.selection = None
        self.cleared = False
        self.active_layer = None

    def get_footprints(self):
        return list(self._fps)

    def get_enabled_layers(self):
        return list(self._layers)

    def set_active_layer(self, enum_int):
        self.active_layer = enum_int

    def get_layer_name(self, enum_int):
        return f"name-of-{enum_int}"

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
    def test_collects_refs_nets_and_layers(self):
        # 3, 34 → F.Cu, B.Cu via the real BoardLayer enum
        board = _FakeBoard(refs=["R1", "U2"], nets=["GND", "VCC"],
                           layers=[3, 34])
        refs, nets, layers = board_links.board_targets(board)
        assert refs == {"R1", "U2"} and nets == {"GND", "VCC"}
        assert layers == {"F.Cu", "B.Cu"}

    def test_survives_partial_failures(self):
        board = _FakeBoard(refs=["R1"], nets=["GND"])
        board.get_nets = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        refs, nets, layers = board_links.board_targets(board)
        assert refs == {"R1"} and nets == set() and layers == set()


class TestLayerLinks:
    def test_tokenize_links_known_layer(self):
        segs = board_links.tokenize("Route auf F.Cu, nicht B.Cu",
                                    known_refs=set(), known_layers={"F.Cu"})
        assert ("F.Cu", ("layer", "F.Cu")) in segs
        plain = "".join(c for c, t in segs if t is None)
        assert "B.Cu" in plain  # not a known layer → stays plain

    def test_enum_canonical_round_trip(self):
        assert board_links._enum_to_canonical(3) == "F.Cu"
        assert board_links._canonical_to_enum("F.Cu") == 3
        assert board_links._canonical_to_enum("User.9") == 61

    def test_set_active_layer_calls_kipy(self):
        board = _FakeBoard(layers=[3])
        gui = board_links.set_active_layer(board, "F.Cu")
        assert board.active_layer == 3 and gui == "name-of-3"

    def test_set_active_layer_unresolvable(self):
        board = _FakeBoard()
        assert board_links.set_active_layer(board, "Nope.99") is None
        assert board.active_layer is None


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


class TestConnectDiagnostics:
    """connect() must turn the multi-instance API state (KiCad reachable but
    GetOpenDocuments unhandled because two instances share the socket) into an
    actionable BoardUnavailable — that is what makes the chat's 'ⓘ Links aus: …'
    line tell the user to close the extra KiCad instead of showing a raw
    ApiError. Reproduced live against KiCad 10.0.1; here driven with a fake
    kipy so it stays headless."""

    @staticmethod
    def _fake_kipy(get_board):
        import types
        mod = types.ModuleType("kipy")
        mod.KiCad = lambda timeout_ms=0: SimpleNamespace(get_board=get_board)
        return mod

    def test_multi_instance_raises_actionable(self, monkeypatch):
        import sys

        def _no_handler():
            raise RuntimeError(
                "KiCad returned error: no handler available for request of "
                "type kiapi.common.commands.GetOpenDocuments")

        monkeypatch.setitem(sys.modules, "kipy", self._fake_kipy(_no_handler))
        monkeypatch.setattr(board_links.time, "sleep", lambda *_a: None)
        with pytest.raises(board_links.BoardUnavailable) as ei:
            board_links.connect()
        assert "Instanz" in str(ei.value)  # actionable, user-facing text

    def test_unexpected_error_is_not_wrapped(self, monkeypatch):
        import sys

        def _other():
            raise ValueError("etwas ganz anderes")  # no board marker

        monkeypatch.setitem(sys.modules, "kipy", self._fake_kipy(_other))
        monkeypatch.setattr(board_links.time, "sleep", lambda *_a: None)
        with pytest.raises(ValueError):
            board_links.connect()

    def test_success_returns_client_and_board(self, monkeypatch):
        import sys
        sentinel = object()
        monkeypatch.setitem(sys.modules, "kipy",
                            self._fake_kipy(lambda: sentinel))
        client, board = board_links.connect()
        assert board is sentinel and client is not None
