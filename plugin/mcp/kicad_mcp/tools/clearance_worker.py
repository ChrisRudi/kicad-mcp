# SPDX-License-Identifier: GPL-3.0-or-later
"""Warm clearance-check daemon — pcbnew + stdlib ONLY.

The shared *clearance engine*: a long-lived process (spawned by
``clearance_tools``) that keeps loaded + zone-filled ``BOARD`` objects in
memory (cached by path + mtime) and answers "does this copper short
against a different net?" via KiCad's own geometry (``SHAPE.Collide``).

It generalises the per-via collision test that ``via_promote_worker`` does
for one optimisation into a reusable check every board-mutating tool can
call right after it edits copper:

  * **Targeted** (``items`` given) — build the SHAPE for each just-added /
    just-changed element (via circle, track segment) and collide it against
    other-net copper on the layers it occupies. Fast: only the new geometry
    is tested, not the whole board. This is the per-mutation effect-echo
    path.
  * **Board-wide** (``items`` empty) — a grid-binned different-net scan of
    every hard-copper item (tracks / vias / pads) so near-linear instead of
    O(n²). Zones are *filled* first (so the check sees real copper) but are
    not themselves subjects: a properly poured zone clears around foreign
    nets, so zone clearance proper belongs to the filler + full DRC.

The analysis is **read-only** — it fills zones and collides shapes but
never mutates the board, so a cached board is reused as-is until the file's
mtime changes (i.e. the mutating tool wrote it), which forces a reload.

Imports NOTHING from ``kicad_mcp`` / ``mcp`` (that would drag the package
``__init__`` → server → all tools, ~3 s, into startup). Run by file path,
never ``-m``. ``pcbnew`` is imported lazily so merely importing this module
(the heavy-import audit walks every ``tools/*``) stays cheap.

``run()`` is the cold, in-process equivalent (one load per call, no cache)
kept for direct / unit-test use and re-exported by ``clearance_tools`` as
``_run_in_process``.
"""
import json
import os
import sys
import time
import traceback
from typing import Any

MARK = "@@CLEARANCE@@"
MAX_CACHE = 5            # LRU board cache size
_MM = 1_000_000          # nm per mm
_MAX_VIOLATIONS = 200    # cap the payload on a badly-broken board
_GRID_MM = 5.0           # board-wide spatial-bin cell size

# pcbnew is imported lazily (by main() in the daemon, or by _load in the
# in-process path) — never at module load, so the heavy-import scan stays cheap.
pcbnew = None  # type: ignore[assignment]


def _import_pcbnew() -> None:
    """Import pcbnew into the module global once, muting its import chatter."""
    global pcbnew
    if pcbnew is not None:
        return
    _real = sys.stdout
    sys.stdout = sys.stderr
    try:
        import pcbnew as _p  # noqa: E402
    finally:
        sys.stdout = _real
    pcbnew = _p


def _load(pcb_path: str):
    """``LoadBoard`` + fill all zones + ``BuildConnectivity``. Returns
    (board, fill_ok). Zones are filled board-wide so the collision check
    sees the real poured copper (a track over a stale fill would otherwise
    look like a short)."""
    _import_pcbnew()
    _real = sys.stdout
    sys.stdout = sys.stderr
    try:
        board = pcbnew.LoadBoard(pcb_path)
        fill_ok = True
        try:
            pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        except Exception:  # pragma: no cover - depends on board zones
            fill_ok = False
        board.BuildConnectivity()
    finally:
        sys.stdout = _real
    return board, fill_ok


# ---------------------------------------------------------------------------
# layer / net helpers
# ---------------------------------------------------------------------------


def _cu_stack(board) -> list:
    """Ordered list of enabled copper layer ids (F.Cu … B.Cu)."""
    return list(board.GetEnabledLayers().CuStack())


def _name_to_layer(board) -> dict:
    """Map copper-layer NAME → id for the layers enabled on this board."""
    return {board.GetLayerName(L): L for L in _cu_stack(board)}


def _span_layers(cu: list, a: int, b: int) -> list:
    """Inclusive copper-stack slice between layer ids ``a`` and ``b`` — the
    layers a via on (a, b) actually occupies (inner layers included)."""
    try:
        i, j = cu.index(a), cu.index(b)
    except ValueError:
        return [a] if a == b else [a, b]
    lo, hi = sorted((i, j))
    return cu[lo:hi + 1]


def _net_code(board, net_name: str) -> int:
    """Resolve a net name to its board net code. Empty name → 0 (no-connect);
    an unknown name → -1 (matches nothing, so the subject is tested against
    every real net)."""
    if not net_name:
        return 0
    try:
        net = board.FindNet(net_name)
    except Exception:
        net = None
    if net is None:
        return -1
    return net.GetNetCode()


def _eff_shape(it, layer):
    try:
        return it.GetEffectiveShape(layer)
    except Exception:
        return None


def _collide(other_shape, subject_shape, clr_iu) -> bool:
    """True if ``subject_shape`` is within ``clr_iu`` of ``other_shape``.

    Collide is called on the OTHER item's generic SHAPE with the subject as
    the argument — SHAPE_CIRCLE's own Collide only accepts a SEG in the SWIG
    binding, but the base ``SHAPE.Collide(SHAPE, clearance)`` handles circle
    and segment subjects alike (clearance in internal units)."""
    if other_shape is None or subject_shape is None:
        return False
    try:
        return bool(other_shape.Collide(subject_shape, int(clr_iu)))
    except Exception:
        return False


def _ref_of(item) -> str:
    """Best-effort human label for a blocker item (REF.PAD for pads)."""
    try:
        if item.GetClass() == "PAD":
            fp = item.GetParent()
            ref = fp.GetReference() if fp is not None else "?"
            return f"{ref}.{item.GetPadName()}"
    except Exception:
        pass
    try:
        return item.GetNetname() or "<none>"
    except Exception:
        return "<item>"


def _copper_items_on_layer(board, layer: int, skip_net: int):
    """Yield (item, effective_shape) for every hard-copper item (tracks,
    vias, pads, filled zones) on ``layer`` whose net differs from
    ``skip_net``."""
    for t in board.GetTracks():               # tracks + vias
        if t.GetNetCode() == skip_net or not t.IsOnLayer(layer):
            continue
        s = _eff_shape(t, layer)
        if s is not None:
            yield t, s
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == skip_net or not p.IsOnLayer(layer):
                continue
            s = _eff_shape(p, layer)
            if s is not None:
                yield p, s
    for z in board.Zones():
        if z.GetNetCode() == skip_net or not z.IsOnLayer(layer):
            continue
        s = _eff_shape(z, layer)
        if s is not None:
            yield z, s


# ---------------------------------------------------------------------------
# Targeted check — collide the just-added items against other-net copper
# ---------------------------------------------------------------------------


def _vec(x_mm: float, y_mm: float):
    return pcbnew.VECTOR2I(int(round(float(x_mm) * _MM)),
                           int(round(float(y_mm) * _MM)))


def _subject_shape_and_layers(board, spec: dict, name2layer: dict):
    """Build (subject_shape, [layer_ids], at_dict, net_code) for one item
    spec, or (None, [], {}, 0) if unusable. Pure geometry, no collision.

    ``net_code`` is the subject's own net — items on it are skipped during
    collision (same net = not a short). For ``via_uuid`` it is read from the
    resolved board via; for ``via`` / ``seg`` from the spec's ``net`` name."""
    kind = spec.get("kind")
    cu = _cu_stack(board)
    if kind == "via":
        r_iu = int(round(float(spec.get("diameter_mm", 0.6)) * _MM / 2))
        shape = pcbnew.SHAPE_CIRCLE(_vec(spec["x_mm"], spec["y_mm"]), r_iu)
        pair = spec.get("layers") or ["F.Cu", "B.Cu"]
        a = name2layer.get(pair[0], cu[0] if cu else 0)
        b = name2layer.get(pair[-1], cu[-1] if cu else 0)
        return (shape, _span_layers(cu, a, b),
                {"x_mm": round(float(spec["x_mm"]), 4),
                 "y_mm": round(float(spec["y_mm"]), 4)},
                _net_code(board, spec.get("net", "")))
    if kind == "seg":
        w_iu = int(round(float(spec.get("width_mm", 0.25)) * _MM))
        shape = pcbnew.SHAPE_SEGMENT(
            _vec(spec["x1_mm"], spec["y1_mm"]),
            _vec(spec["x2_mm"], spec["y2_mm"]), w_iu)
        lid = name2layer.get(spec.get("layer", "F.Cu"))
        mx = (float(spec["x1_mm"]) + float(spec["x2_mm"])) / 2
        my = (float(spec["y1_mm"]) + float(spec["y2_mm"])) / 2
        return (shape, ([lid] if lid is not None else []),
                {"x_mm": round(mx, 4), "y_mm": round(my, 4)},
                _net_code(board, spec.get("net", "")))
    if kind == "via_uuid":
        want = spec.get("uuid")
        for t in board.GetTracks():
            if t.GetClass() != "PCB_VIA":
                continue
            if t.m_Uuid.AsString() != want:
                continue
            r_iu = _via_radius_iu(t)
            shape = pcbnew.SHAPE_CIRCLE(t.GetPosition(), r_iu)
            layers = _span_layers(cu, t.TopLayer(), t.BottomLayer())
            pos = t.GetPosition()
            return (shape, layers,
                    {"x_mm": round(pos.x / _MM, 4),
                     "y_mm": round(pos.y / _MM, 4)},
                    t.GetNetCode())
        return None, [], {}, 0
    return None, [], {}, 0


def _via_radius_iu(via) -> int:
    """Via copper-pad radius in internal units. GetWidth() MUST get a layer
    argument on KiCad-10 padstacks or it throws a blocking assert."""
    try:
        w = via.GetWidth(via.TopLayer())
    except Exception:
        try:
            w = via.GetDrillValue() + 200000  # drill + 0.2 mm fallback
        except Exception:
            w = 600000
    return int(w // 2)


def _check_items(board, items: list, clr_iu: int) -> list:
    """Collide every item spec against other-net copper; return violations."""
    name2layer = _name_to_layer(board)
    violations: list[dict] = []
    for i, spec in enumerate(items):
        if not isinstance(spec, dict):
            continue
        shape, layers, at, net_code = _subject_shape_and_layers(
            board, spec, name2layer)
        if shape is None or not layers:
            continue
        for L in layers:
            hit = None
            for item, oshape in _copper_items_on_layer(board, L, net_code):
                if _collide(oshape, shape, clr_iu):
                    hit = item
                    break
            if hit is not None:
                violations.append({
                    "item_index": i,
                    "item_kind": spec.get("kind"),
                    "at": at,
                    "layer": board.GetLayerName(L),
                    "net": spec.get("net", ""),
                    "blocker_net": (hit.GetNetname() or "<none>"),
                    "blocker_class": hit.GetClass(),
                    "blocker_ref": _ref_of(hit),
                })
                if len(violations) >= _MAX_VIOLATIONS:
                    return violations
    return violations


# ---------------------------------------------------------------------------
# Board-wide check — grid-binned different-net hard-copper collision scan
# ---------------------------------------------------------------------------


def _hard_copper(board) -> list:
    """List (item, layer_id, shape, net_code) for every hard-copper item
    (tracks, vias, pads) on each copper layer it occupies."""
    out: list = []
    cu = _cu_stack(board)
    for t in board.GetTracks():
        nc = t.GetNetCode()
        if t.GetClass() == "PCB_VIA":
            layers = _span_layers(cu, t.TopLayer(), t.BottomLayer())
        else:
            layers = [L for L in cu if t.IsOnLayer(L)]
        for L in layers:
            s = _eff_shape(t, L)
            if s is not None:
                out.append((t, L, s, nc))
    for fp in board.GetFootprints():
        for p in fp.Pads():
            nc = p.GetNetCode()
            for L in cu:
                if p.IsOnLayer(L):
                    s = _eff_shape(p, L)
                    if s is not None:
                        out.append((p, L, s, nc))
    return out


def _bbox_cells(shape) -> list:
    """Grid cells (col, row) the shape's bounding box spans (≤ a small cap so
    a board-spanning item doesn't explode the index)."""
    try:
        bb = shape.BBox()
        x0, y0 = bb.GetLeft(), bb.GetTop()
        x1, y1 = bb.GetRight(), bb.GetBottom()
    except Exception:
        return []
    step = int(_GRID_MM * _MM)
    c0, c1 = x0 // step, x1 // step
    r0, r1 = y0 // step, y1 // step
    if (c1 - c0) > 64 or (r1 - r0) > 64:   # huge item (long track / pour edge)
        # Index by a coarse anchor only; it will still be compared against
        # everything sharing that anchor — acceptable for the rare big item.
        return [(c0, r0)]
    return [(c, r) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1)]


def _check_board(board, clr_iu: int) -> list:
    """Grid-binned different-net collision scan over hard copper."""
    items = _hard_copper(board)
    # bucket index: (layer, cell) -> [item_idx]
    buckets: dict = {}
    cells_of: list = []
    for idx, (_it, L, s, _nc) in enumerate(items):
        cells = _bbox_cells(s)
        cells_of.append(cells)
        for cell in cells:
            buckets.setdefault((L, cell), []).append(idx)

    seen_pairs: set = set()
    violations: list[dict] = []
    for idx, (it, L, s, nc) in enumerate(items):
        cand: set = set()
        for (cc, rr) in cells_of[idx]:
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    cand.update(buckets.get((L, (cc + dc, rr + dr)), ()))
        for j in cand:
            if j <= idx:
                continue
            it2, L2, s2, nc2 = items[j]
            if L2 != L or nc2 == nc:
                continue
            if nc <= 0 and nc2 <= 0:        # both no-connect → not a short
                continue
            pair = (idx, j)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if _collide(s2, s, clr_iu):
                try:
                    bb = s.BBox()
                    ax = round((bb.GetLeft() + bb.GetRight()) / 2 / _MM, 4)
                    ay = round((bb.GetTop() + bb.GetBottom()) / 2 / _MM, 4)
                except Exception:
                    ax = ay = 0.0
                violations.append({
                    "at": {"x_mm": ax, "y_mm": ay},
                    "layer": board.GetLayerName(L),
                    "a_net": it.GetNetname() or "<none>",
                    "b_net": it2.GetNetname() or "<none>",
                    "a_class": it.GetClass(),
                    "b_class": it2.GetClass(),
                    "a_ref": _ref_of(it),
                    "b_ref": _ref_of(it2),
                })
                if len(violations) >= _MAX_VIOLATIONS:
                    return violations
    return violations


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


def _analyse_board(board, fill_ok: bool, items, clearance_mm: float) -> dict[str, Any]:
    """Run the targeted (items given) or board-wide (items empty) check on a
    loaded + filled board. Pure read — never mutates ``board``."""
    clr_iu = int(round(float(clearance_mm) * _MM))
    if items:
        violations = _check_items(board, items, clr_iu)
        mode = "targeted"
        checked = len(items)
    else:
        violations = _check_board(board, clr_iu)
        mode = "board"
        checked = 0
    return {
        "success": True,
        "zones_filled": fill_ok,
        "clearance_mm": float(clearance_mm),
        "mode": mode,
        "checked_items": checked,
        "ok": not violations,
        "violation_count": len(violations),
        "violations": violations,
    }


def run(pcb_path: str, items=None, clearance_mm: float = 0.2) -> dict[str, Any]:
    """Cold, in-process check: load + fill + analyse, no caching. Kept for
    direct / unit-test use; the daemon path is the warm version."""
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    try:
        board, fill_ok = _load(pcb_path)
    except Exception as e:
        return {"success": False, "error": f"Failed to load board: {e}"}
    return _analyse_board(board, fill_ok, items, clearance_mm)


# ---------------------------------------------------------------------------
# warm daemon: board cache (read-only analysis → reuse the loaded board)
# ---------------------------------------------------------------------------
_CACHE: "dict[str, dict]" = {}   # path -> {mtime, board, fill_ok}
_LOADS = 0


def _get_entry(path: str):
    """Return (entry, cache_hit). Loads+fills on miss; LRU-bumps on hit."""
    global _LOADS
    st = os.stat(path)
    ent = _CACHE.get(path)
    if ent is not None and ent["mtime"] == st.st_mtime_ns:
        _CACHE[path] = _CACHE.pop(path)  # LRU bump
        return ent, True
    board, fill_ok = _load(path)
    _LOADS += 1
    ent = {"mtime": st.st_mtime_ns, "board": board, "fill_ok": fill_ok}
    _CACHE[path] = ent
    while len(_CACHE) > MAX_CACHE:
        _CACHE.pop(next(iter(_CACHE)))
    return ent, False


def _do_check(req: dict) -> dict:
    path = req.get("pcb_path", "")
    items = req.get("items")
    clearance_mm = req.get("clearance_mm", 0.2)
    if not os.path.isfile(path):
        return {"ok": False, "error": f"PCB not found: {path}"}
    t0 = time.time()
    try:
        ent, hit = _get_entry(path)
    except Exception as e:
        return {"ok": False, "error": f"Failed to load board: {e}",
                "traceback": traceback.format_exc()[-2000:], "loads": _LOADS}
    try:
        res = _analyse_board(ent["board"], ent["fill_ok"], items, clearance_mm)
    except Exception as e:
        _CACHE.pop(path, None)
        # mutated=True forces the client to recycle the process — defensive in
        # case a partial analysis left pcbnew SWIG state degraded.
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:], "loads": _LOADS,
                "mutated": True}
    res = {k: v for k, v in res.items() if k != "success"}
    return {"ok": True, **res, "cache_hit": hit, "loads": _LOADS,
            "elapsed_ms": round((time.time() - t0) * 1000, 1)}


def _handle(req: dict) -> dict:
    op = req.get("op")
    if op == "ping":
        return {"ok": True, "pong": True, "loads": _LOADS}
    if op == "status":
        return {"ok": True, "loads": _LOADS,
                "cached": [{"path": p, "mtime_ns": e["mtime"], "fill_ok": e["fill_ok"]}
                           for p, e in _CACHE.items()]}
    if op == "reset":
        p = req.get("pcb_path")
        if p:
            _CACHE.pop(p, None)
        else:
            _CACHE.clear()
        return {"ok": True, "cleared": p or "all"}
    if op == "check":
        return _do_check(req)
    return {"ok": False, "error": f"unknown op '{op}'"}


def main():
    _import_pcbnew()  # subprocess entry — pay pcbnew's init once, up front
    sys.stderr.write("clearance_worker ready\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id")
        try:
            resp = _handle(req)
        except Exception:
            resp = {"ok": False, "error": "daemon error",
                    "traceback": traceback.format_exc()[-2000:]}
        resp["id"] = rid
        sys.stdout.write(MARK + json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
