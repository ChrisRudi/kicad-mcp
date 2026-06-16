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
import time
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


def _pin_matches(text, ref_set) -> list[tuple]:
    """Match ``<ref>.<pin>`` (e.g. ``U1B.33`` = footprint U1B, pin 33) where
    ``<ref>`` is a known board reference. Target ``("pin", (ref, pin))``."""
    refs = sorted({r for r in ref_set if r}, key=len, reverse=True)
    if not refs:
        return []
    alts = "|".join(re.escape(r) for r in refs)
    rx = re.compile(
        rf"(?<!{_BOUNDARY})({alts})\.([A-Za-z0-9]+)(?!{_BOUNDARY})")
    return [(m.start(), m.end(), ("pin", (m.group(1), m.group(2))))
            for m in rx.finditer(text)]


def tokenize(text: str, known_refs, known_nets=(), known_layers=()) -> list[tuple]:
    """Split ``text`` into ``(chunk, target)`` segments.

    ``target`` is ``None`` for plain text, or a clickable target:
    ``("ref", "R12")`` / ``("net", "GND")`` / ``("layer", "F.Cu")`` /
    ``("pin", (ref, pin))`` for ``U1B.33`` (only for refs that exist on the
    board — refs win ties over nets, pins take a ``<ref>.<pin>`` span before a
    bare ref would) or ``("coord", (x_mm, y_mm))`` for a printed coordinate
    pair. Coordinate links need no board data.
    """
    ref_set = {str(r) for r in (known_refs or []) if str(r)}
    net_set = {str(n) for n in (known_nets or []) if str(n)}
    layer_set = {str(l) for l in (known_layers or []) if str(l)} - ref_set - net_set
    matches = (_pin_matches(text, ref_set)
               + _ref_net_matches(text, ref_set, net_set)
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


_CONNECT_TIMEOUT_MS = 15000   # default kipy 2000 ms is too short under load
_BUSY_RETRIES = 5
_BUSY_BACKOFF_S = 0.2


def _is_busy(exc: BaseException) -> bool:
    return "busy" in str(exc).lower()


def call(fn, retries: int = _BUSY_RETRIES):
    """Run one kipy call, retrying "KiCad is busy" with exponential backoff.

    KiCad serialises its whole API on one thread, so once the MCP server is
    connected the editor is often momentarily busy when the chat panel asks
    for refs/nets to build its links. "busy" is a fast rejection, not a real
    error — back off and retry instead of silently dropping every link.
    """
    last: Optional[BaseException] = None
    for i in range(retries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classified + re-raised below
            last = exc
            if _is_busy(exc) and i < retries - 1:
                time.sleep(_BUSY_BACKOFF_S * (2 ** i))
                continue
            raise
    assert last is not None
    raise last


class BoardUnavailable(RuntimeError):
    """connect() failed in a way the USER can fix — almost always several KiCad
    instances sharing one IPC socket (project manager + a standalone editor, or
    a leftover process), which makes ``GetOpenDocuments`` unhandled so no board
    resolves. Carries an actionable, already-user-facing message so the chat
    panel's "ⓘ Links aus: …" line says what to do instead of a raw ApiError.

    Verified live against KiCad 10.0.1: with two ``kicad.exe`` on the bus,
    ``get_board()`` raises ``ApiError(... no handler available for ...
    GetOpenDocuments)`` and every cross-probe link silently vanishes; with a
    single instance the exact same code returns refs/nets/layers/pins fine."""


# Substrings (lowercased) in the kipy/KiCad error that mean "the API is
# reachable but no single board resolves" — the multi-instance signature. NOT
# "busy"/"not ready" (those are transient and handled by call()'s retry).
_NO_BOARD_MARKERS = ("getopendocuments", "no handler", "no open document",
                     "not a board")


def connect():
    """Open an IPC client (generous timeout); returns ``(client, board)``.

    The 15 s timeout + busy-retry survive contention with the now-connected
    MCP server — the 2 s default silently failed every cross-probe link.

    Raises :class:`BoardUnavailable` (actionable message) when the API is
    reachable but no board resolves — the hallmark of multiple KiCad instances
    on one socket. That breaks every link and the only fix is the user closing
    the extra instance, so we surface it clearly instead of leaking a cryptic
    ApiError into the diagnostic line.
    """
    from kipy import KiCad  # lazy: only inside KiCad
    client = KiCad(timeout_ms=_CONNECT_TIMEOUT_MS)
    try:
        return client, call(client.get_board)
    except Exception as exc:
        if any(m in str(exc).lower() for m in _NO_BOARD_MARKERS):
            raise BoardUnavailable(
                "Kein eindeutiges Board über die KiCad-API erreichbar — meist "
                "laufen MEHRERE KiCad-Instanzen (Projektmanager + zweiter "
                "Editor, oder ein Rest-Prozess) auf einem Socket. Schließe "
                "zusätzliche KiCad-Fenster, sodass genau EIN Board im "
                "PCB-Editor offen ist."
            ) from exc
        raise


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
        for fp in call(board.get_footprints):
            r = _ref_of(fp)
            if r:
                refs.add(r)
    except Exception:
        pass
    try:
        for n in call(board.get_nets):
            name = (getattr(n, "name", "") or "").strip()
            if name:
                nets.add(name)
    except Exception:
        pass
    try:
        for enum_int in call(board.get_enabled_layers):
            canonical = _enum_to_canonical(enum_int)
            if canonical:
                layers.add(canonical)
    except Exception:
        pass
    return refs, nets, layers


# -- disk fallback (no kipy, no GUI) ------------------------------------------

# Footprint reference, both KiCad-10 s-expr (`(property "Reference" "R12" …)`)
# and the legacy `(fp_text reference R12 …)` form.
_RE_REF_PROP = re.compile(r'\(property\s+"Reference"\s+"([^"]+)"')
_RE_REF_FPTEXT = re.compile(r'\(fp_text\s+reference\s+"?([^"\s)]+)')
# Net declarations: `(net 5 "GND")` or unquoted `(net 5 GND)`; net 0 / "" skip.
_RE_NET_Q = re.compile(r'\(net\s+\d+\s+"([^"]*)"\s*\)')
_RE_NET_U = re.compile(r'\(net\s+\d+\s+([^\s")]+)\s*\)')
# Board layer table rows: `(0 "F.Cu" signal)` — digit-first distinguishes them
# from footprint `(layers "F.Cu" …)` (names only) and `(net …)` entries.
_RE_LAYER = re.compile(r'\(\d+\s+"([^"]+)"\s+\w+')


def board_targets_from_file(path: str) -> tuple[set, set, set]:
    """Disk fallback for :func:`board_targets`: parse footprint references, net
    names and layer names straight from the ``.kicad_pcb`` TEXT.

    Use this when the live IPC client can't resolve the board (the classic case
    is several KiCad instances on one socket → ``BoardUnavailable``) but the file
    the chat is about sits right there on disk — the very file the MCP server
    reads. Best-effort and forgiving: any read error yields empty sets so the
    caller degrades gracefully instead of crashing the reply. Linkifies the
    chat; the click-to-select path still needs live IPC.
    """
    refs: set = set()
    nets: set = set()
    layers: set = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception:
        return refs, nets, layers
    for m in _RE_REF_PROP.finditer(text):
        refs.add(m.group(1))
    for m in _RE_REF_FPTEXT.finditer(text):
        refs.add(m.group(1))
    refs = {r for r in refs if r and r not in ("~", "REF**")}
    for m in _RE_NET_Q.finditer(text):
        if m.group(1):
            nets.add(m.group(1))
    for m in _RE_NET_U.finditer(text):
        nets.add(m.group(1))
    nets.discard("")
    for m in _RE_LAYER.finditer(text):
        layers.add(m.group(1))
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
            items = call(fn)
        except Exception:
            continue
        for it in items:
            xy = _item_xy_mm(it)
            if xy is None:
                continue
            d = math.hypot(xy[0] - x_mm, xy[1] - y_mm)
            if best_d is None or d < best_d:
                best_d, best = d, it
    call(board.clear_selection)
    if best is not None and best_d is not None and best_d <= radius_mm:
        call(lambda: board.add_to_selection([best]))
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
    call(lambda: board.set_active_layer(enum_int))
    try:
        return call(lambda: board.get_layer_name(enum_int)) or layer_name
    except Exception:
        return layer_name


def _pads_of(footprint: Any) -> list:
    """The pads of a placed footprint (``definition.pads``; each has a board
    ``id`` so it is directly selectable)."""
    try:
        return list(footprint.definition.pads)
    except Exception:
        return []


def select_pin(client: Any, board: Any, ref: str, pin: str,
               zoom: bool = True) -> int:
    """Select+zoom the pad ``pin`` of footprint ``ref`` (e.g. U1B, "33").
    Returns 1 if the pad was found, else 0. Selection is by the pad's board
    id, so its local/board position never matters."""
    fp = next((f for f in call(board.get_footprints) if _ref_of(f) == ref),
              None)
    call(board.clear_selection)
    if fp is None:
        return 0
    target = next((p for p in _pads_of(fp)
                   if str(getattr(p, "number", "")) == str(pin)), None)
    if target is None:
        return 0
    call(lambda: board.add_to_selection([target]))
    if zoom:
        _zoom_to_selection(client)
    return 1


def select(client: Any, board: Any, kind: str, value: str,
           zoom: bool = True) -> int:
    """Select the element(s) for one link in the editor; returns the count.

    ``kind`` is ``"ref"`` (a footprint) or ``"net"`` (all copper on that net).
    Clears the prior selection, highlights the matches natively, and best-effort
    zooms the view onto them so they are findable on a huge board.
    """
    matched: list = []
    if kind == "ref":
        for fp in call(board.get_footprints):
            if _ref_of(fp) == value:
                matched.append(fp)
    elif kind == "net":
        net = next((n for n in call(board.get_nets)
                    if (getattr(n, "name", "") or "") == value), None)
        if net is not None:
            matched = list(call(lambda: board.get_items_by_net(net)))
    call(board.clear_selection)
    if matched:
        call(lambda: board.add_to_selection(matched))
        if zoom:
            _zoom_to_selection(client)
    return len(matched)
