# SPDX-License-Identifier: GPL-3.0-or-later
"""Make board elements named in the chat clickable: turn footprint references
(``R12``, ``U8``) and net names that Claude mentions into links that, on click,
SELECT + zoom to that element in the live PCB editor.

Why: on a large multi-layer board a textual answer ("die drei kleinsten GND-Vias
sind …") doesn't help you FIND the thing — you can't see it. Linking the chat to
the editor's native selection/cross-probe solves that.

Two layers:
* PURE (headless-testable): ``tokenize`` splits a reply into plain/clickable
  segments using the set of references/nets that ACTUALLY exist on the board —
  so there are no false-positive links and a click always resolves.
* kipy (only inside KiCad): ``connect`` / ``board_targets`` / ``select`` talk to
  the running editor over IPC. Imports are lazy so the pure layer needs no kipy.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

# Chars that may sit inside a designator/net token; used in the link lookarounds
# so "R1" never matches inside "R12" or "DR1" or a net like "R1_OUT".
_BOUNDARY = r"[\w/.+\-]"

# A coordinate pair Claude/the MCP prints, e.g. "(120.5, 84.0)" or
# "(120.5 mm, 84 mm)". Parentheses-required keeps it false-positive-free
# (bare "1, 2" in prose must NOT linkify). Group 1/2 are the mm numbers.
_NUM = r"-?\d+(?:\.\d+)?"
_COORD_RE = re.compile(
    rf"\(\s*({_NUM})\s*(?:mm)?\s*,\s*({_NUM})\s*(?:mm)?\s*\)"
)


def _link_regex(tokens) -> Optional["re.Pattern"]:
    """A regex matching any of ``tokens`` as a standalone word (longest-first,
    so ``GND_1`` wins over ``GND``). None if there is nothing to match."""
    toks = sorted({t for t in tokens if t}, key=len, reverse=True)
    if not toks:
        return None
    alts = "|".join(re.escape(t) for t in toks)
    return re.compile(rf"(?<!{_BOUNDARY})(?:{alts})(?!{_BOUNDARY})")


def _ref_net_matches(text, ref_set, net_set) -> list[tuple]:
    rx = _link_regex(ref_set | net_set)
    if rx is None:
        return []
    out = []
    for m in rx.finditer(text):
        tok = m.group(0)
        kind = "ref" if tok in ref_set else "net"
        out.append((m.start(), m.end(), (kind, tok)))
    return out


def _coord_matches(text) -> list[tuple]:
    out = []
    for m in _COORD_RE.finditer(text):
        xy = (float(m.group(1)), float(m.group(2)))
        out.append((m.start(), m.end(), ("coord", xy)))
    return out


def _layer_matches(text, layer_set) -> list[tuple]:
    rx = _link_regex(layer_set)
    if rx is None:
        return []
    return [(m.start(), m.end(), ("layer", m.group(0)))
            for m in rx.finditer(text)]


def tokenize(text: str, known_refs, known_nets=(), known_layers=()) -> list[tuple]:
    """Split ``text`` into ``(chunk, target)`` segments.

    ``target`` is ``None`` for plain text, or a clickable target:
    ``("ref", "R12")`` / ``("net", "GND")`` / ``("layer", "F.Cu")`` (only for
    tokens that exist on the board — refs win ties over nets, layers are kept
    separate) or ``("coord", (x_mm, y_mm))`` for a printed coordinate pair.
    Coordinate links need no board data.
    """
    ref_set = {str(r) for r in (known_refs or []) if str(r)}
    net_set = {str(n) for n in (known_nets or []) if str(n)}
    layer_set = {str(l) for l in (known_layers or []) if str(l)} - ref_set - net_set
    matches = (_ref_net_matches(text, ref_set, net_set)
               + _layer_matches(text, layer_set)
               + _coord_matches(text))
    if not matches:
        return [(text, None)] if text else []
    matches.sort(key=lambda m: m[0])
    segs: list[tuple] = []
    pos = 0
    for start, end, target in matches:
        if start < pos:
            continue  # overlapping match (coord vs ref) — keep the first
        if start > pos:
            segs.append((text[pos:start], None))
        segs.append((text[start:end], target))
        pos = end
    if pos < len(text):
        segs.append((text[pos:], None))
    return segs


# -- kipy side (only available inside KiCad) ----------------------------------

# Best-effort zoom-to-selection actions, tried in order after selecting.
_ZOOM_ACTIONS = ("common.Control.zoomFitSelection",
                 "pcbnew.Control.zoomFitObjects")


def _ref_of(footprint: Any) -> Optional[str]:
    """The reference string of a kipy footprint (``Field.text.value``)."""
    fld = getattr(footprint, "reference_field", None)
    txt = getattr(fld, "text", None)
    val = getattr(txt, "value", txt)
    val = (val or "").strip() if isinstance(val, str) else val
    return val or None


def connect():
    """Open a fresh IPC client; returns ``(client, board)`` or raises."""
    from kipy import KiCad  # lazy: only inside KiCad
    client = KiCad()
    return client, client.get_board()


def _enum_to_canonical(enum_int: int) -> Optional[str]:
    """BoardLayer enum int → canonical name (3 → "F.Cu"), or None."""
    try:
        from kipy.proto.board.board_types_pb2 import (  # type: ignore  # pylint: disable=no-name-in-module
            BoardLayer,
        )
        name = BoardLayer.Name(int(enum_int))  # "BL_F_Cu"
    except Exception:
        return None
    if name.startswith("BL_"):
        return name[3:].replace("_", ".")
    return None


def _canonical_to_enum(name: str) -> Optional[int]:
    """Canonical layer name ("F.Cu") → BoardLayer enum int, or None."""
    try:
        from kipy.proto.board.board_types_pb2 import (  # type: ignore  # pylint: disable=no-name-in-module
            BoardLayer,
        )
        return BoardLayer.Value("BL_" + str(name).replace(".", "_"))
    except Exception:
        return None


def board_targets(board: Any) -> tuple[set, set, set]:
    """The sets of (footprint references, net names, enabled layer names) on
    the live board — used to linkify only tokens that really exist."""
    refs: set = set()
    nets: set = set()
    layers: set = set()
    try:
        for fp in board.get_footprints():
            r = _ref_of(fp)
            if r:
                refs.add(r)
    except Exception:
        pass
    try:
        for n in board.get_nets():
            name = (getattr(n, "name", "") or "").strip()
            if name:
                nets.add(name)
    except Exception:
        pass
    try:
        for enum_int in board.get_enabled_layers():
            canonical = _enum_to_canonical(enum_int)
            if canonical:
                layers.add(canonical)
    except Exception:
        pass
    return refs, nets, layers


def _zoom_to_selection(client: Any) -> None:
    for action in _ZOOM_ACTIONS:
        try:
            client.run_action(action)
            return
        except Exception:
            continue


def _item_xy_mm(item: Any) -> Optional[tuple]:
    pos = getattr(item, "position", None)
    x, y = getattr(pos, "x", None), getattr(pos, "y", None)
    if x is None or y is None:
        return None
    return (x / 1_000_000, y / 1_000_000)


def select_coord(client: Any, board: Any, x_mm: float, y_mm: float,
                 radius_mm: float = 8.0, zoom: bool = True) -> Optional[float]:
    """Navigate to a printed coordinate by selecting the nearest board element
    (footprint/via/pad) and zooming to it. Returns the distance in mm to that
    element, or None if nothing is within ``radius_mm`` (KiCad has no
    "center on point" API, so an anchor element is how we get the view there).
    """
    best = None
    best_d: Optional[float] = None
    for getter in ("get_footprints", "get_vias", "get_pads"):
        fn = getattr(board, getter, None)
        if fn is None:
            continue
        try:
            items = fn()
        except Exception:
            continue
        for it in items:
            xy = _item_xy_mm(it)
            if xy is None:
                continue
            d = math.hypot(xy[0] - x_mm, xy[1] - y_mm)
            if best_d is None or d < best_d:
                best_d, best = d, it
    board.clear_selection()
    if best is not None and best_d is not None and best_d <= radius_mm:
        board.add_to_selection([best])
        if zoom:
            _zoom_to_selection(client)
        return best_d
    return None


def set_active_layer(board: Any, layer_name: str) -> Optional[str]:
    """Make ``layer_name`` the active layer in the editor; returns its GUI
    name on success, or None if the name doesn't resolve. Verified kipy API:
    ``set_active_layer(int)`` + ``get_layer_name(int)``."""
    enum_int = _canonical_to_enum(layer_name)
    if enum_int is None:
        return None
    board.set_active_layer(enum_int)
    try:
        return board.get_layer_name(enum_int) or layer_name
    except Exception:
        return layer_name


def select(client: Any, board: Any, kind: str, value: str,
           zoom: bool = True) -> int:
    """Select the element(s) for one link in the editor; returns the count.

    ``kind`` is ``"ref"`` (a footprint) or ``"net"`` (all copper on that net).
    Clears the prior selection, highlights the matches natively, and best-effort
    zooms the view onto them so they are findable on a huge board.
    """
    matched: list = []
    if kind == "ref":
        for fp in board.get_footprints():
            if _ref_of(fp) == value:
                matched.append(fp)
    elif kind == "net":
        net = next((n for n in board.get_nets()
                    if (getattr(n, "name", "") or "") == value), None)
        if net is not None:
            matched = list(board.get_items_by_net(net))
    board.clear_selection()
    if matched:
        board.add_to_selection(matched)
        if zoom:
            _zoom_to_selection(client)
    return len(matched)
