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

import re
from typing import Any, Optional

# Chars that may sit inside a designator/net token; used in the link lookarounds
# so "R1" never matches inside "R12" or "DR1" or a net like "R1_OUT".
_BOUNDARY = r"[\w/.+\-]"


def _link_regex(tokens) -> Optional["re.Pattern"]:
    """A regex matching any of ``tokens`` as a standalone word (longest-first,
    so ``GND_1`` wins over ``GND``). None if there is nothing to match."""
    toks = sorted({t for t in tokens if t}, key=len, reverse=True)
    if not toks:
        return None
    alts = "|".join(re.escape(t) for t in toks)
    return re.compile(rf"(?<!{_BOUNDARY})(?:{alts})(?!{_BOUNDARY})")


def tokenize(text: str, known_refs, known_nets=()) -> list[tuple]:
    """Split ``text`` into ``(chunk, target)`` segments.

    ``target`` is ``None`` for plain text, or ``("ref", "R12")`` /
    ``("net", "GND")`` for a clickable token. Only tokens present in
    ``known_refs`` / ``known_nets`` become links (refs win ties), so every
    link maps to a real board element.
    """
    ref_set = {str(r) for r in (known_refs or []) if str(r)}
    net_set = {str(n) for n in (known_nets or []) if str(n)}
    rx = _link_regex(ref_set | net_set)
    if rx is None:
        return [(text, None)] if text else []
    segs: list[tuple] = []
    pos = 0
    for m in rx.finditer(text):
        if m.start() > pos:
            segs.append((text[pos:m.start()], None))
        tok = m.group(0)
        kind = "ref" if tok in ref_set else "net"
        segs.append((tok, (kind, tok)))
        pos = m.end()
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


def board_targets(board: Any) -> tuple[set, set]:
    """The sets of footprint references and net names on the live board."""
    refs: set = set()
    nets: set = set()
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
    return refs, nets


def _zoom_to_selection(client: Any) -> None:
    for action in _ZOOM_ACTIONS:
        try:
            client.run_action(action)
            return
        except Exception:
            continue


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
