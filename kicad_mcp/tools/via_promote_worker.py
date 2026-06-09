# SPDX-License-Identifier: GPL-3.0-or-later
"""
Warm via-promotion daemon — pcbnew + stdlib ONLY.

A long-lived process spawned by ``via_promote_impl``. It keeps loaded +
zone-filled ``BOARD`` objects in memory (cached by path + mtime) so the
analysis amortises the expensive ``LoadBoard`` + zone-fill across calls —
the typical ``dry_run`` (report) → ``dry_run=False`` (apply) flow, or a
board-wide promotion sweep, re-uses one warm filled board instead of
re-loading and re-filling each time. (Previously each call spawned a cold
process and re-filled from scratch — the 240 s wall-clock on dense boards.)

The analysis is **read-only**: it fills zones and tests each blind/buried
via's pad circle against other-net copper, but never mutates the board, so
the cached board stays pristine and is reused as-is. The actual layer
rewrite (apply) is a surgical text-patch in ``via_promote_tools``; it
changes the file on disk, so the next analysis sees a new mtime and
reloads automatically.

Imports NOTHING from ``kicad_mcp`` / ``mcp`` — that would drag in the
package ``__init__`` (→ server → all tools, ~3 s) on startup. Run by file
path, not ``-m``.

Purpose: find blind/buried vias that could become plain through (F–B)
vias — cheaper and JLC-standard — *without* introducing a clearance
violation. Zones are filled first so the check sees the real copper.

``run()`` below is the cold, in-process equivalent (one load per call, no
cache) kept for direct/unit-test use and re-exported by
``via_promote_tools`` as ``_run_in_process``.
"""
import json
import os
import sys
import time
import traceback
from typing import Any

MARK = "@@VIAPROMO@@"
MAX_CACHE = 5            # LRU board cache size
_MM = 1_000_000          # nm per mm

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
    (board, fill_ok). The analysis needs the whole board's copper filled,
    so (unlike connectivity) the fill is always board-wide."""
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


def _cu_stack(board) -> list:
    """Ordered list of enabled copper layer ids (F.Cu … B.Cu)."""
    return list(board.GetEnabledLayers().CuStack())


def _via_span(board, via, cu: list) -> list:
    """Copper layer ids the via currently occupies (inclusive slice of
    the stack between its top and bottom layer)."""
    top, bot = via.TopLayer(), via.BottomLayer()
    try:
        i, j = cu.index(top), cu.index(bot)
    except ValueError:
        return [top, bot]
    lo, hi = sorted((i, j))
    return cu[lo:hi + 1]


def _via_radius_iu(via) -> int:
    """Via copper-pad radius in internal units. GetWidth() MUST get a
    layer argument on KiCad-10 padstacks or it throws a blocking assert."""
    try:
        w = via.GetWidth(via.TopLayer())
    except Exception:
        w = via.GetDrillValue() + 200000  # drill + 0.2 mm fallback
    return int(w // 2)


def _other_items_on_layer(board, layer: int, skip_net: int):
    """Yield (item, effective_shape) for every copper item on ``layer``
    whose net differs from ``skip_net``. Filled zones included."""

    def _shape(it):
        try:
            return it.GetEffectiveShape(layer)
        except Exception:
            return None

    for t in board.GetTracks():               # tracks + vias
        if t.GetNetCode() == skip_net:
            continue
        if not t.IsOnLayer(layer):
            continue
        s = _shape(t)
        if s is not None:
            yield t, s
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == skip_net:
                continue
            if not p.IsOnLayer(layer):
                continue
            s = _shape(p)
            if s is not None:
                yield p, s
    for z in board.Zones():
        if z.GetNetCode() == skip_net:
            continue
        if not z.IsOnLayer(layer):
            continue
        s = _shape(z)
        if s is not None:
            yield z, s


def _eff_shape(it, layer):
    try:
        return it.GetEffectiveShape(layer)
    except Exception:
        return None


def _collide(shape, circle, clr_iu) -> bool:
    if shape is None:
        return False
    try:
        return bool(shape.Collide(circle, int(clr_iu)))
    except Exception:
        return False


def _pads_on_layer(board, layer):
    """Yield (footprint, pad) for every pad present on ``layer``."""
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.IsOnLayer(layer):
                yield fp, p


def _is_smd_pad(p) -> bool:
    """True for a surface pad with no plated hole — a through via landing in
    it would wick solder, so it must be a filled+capped via-in-pad (POFV)."""
    try:
        attr = p.GetAttribute()
        smd = {pcbnew.PAD_ATTRIB_SMD}
        if hasattr(pcbnew, "PAD_ATTRIB_CONN"):
            smd.add(pcbnew.PAD_ATTRIB_CONN)
        if attr in smd:
            return True
    except Exception:
        pass
    try:  # fallback: no drill → surface pad
        return p.GetDrillSizeX() == 0
    except Exception:
        return False


def _pad_ref(fp, p) -> str:
    try:
        return f"{fp.GetReference()}.{p.GetPadName()}"
    except Exception:
        return "<pad>"


def _layer_name(board, layer: int) -> str:
    return board.GetLayerName(layer)


def _analyse_board(board, fill_ok: bool, clearance_mm: float) -> dict[str, Any]:
    """Analyse every via on a loaded + filled board; report which
    blind/buried ones can go through. Pure read — never mutates ``board``."""
    clr_iu = int(float(clearance_mm) * _MM)
    cu = _cu_stack(board)

    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    total = len(vias)
    already_through = 0
    promotable: list[dict] = []
    needs_pofv: list[dict] = []
    blocked: list[dict] = []
    fcu, bcu = (cu[0], cu[-1]) if cu else (pcbnew.F_Cu, pcbnew.B_Cu)
    through_key = f"{_layer_name(board, fcu)}/{_layer_name(board, bcu)}"
    promoted_uuids: set = set()
    pofv_uuids: set = set()

    for via in vias:
        is_through = via.GetViaType() == pcbnew.VIATYPE_THROUGH
        span = set(_via_span(board, via, cu))
        newly = [L for L in cu if L not in span] if not is_through else []
        if is_through or not newly:
            already_through += 1
            continue

        pos = via.GetPosition()
        circle = pcbnew.SHAPE_CIRCLE(pos, _via_radius_iu(via))
        net = via.GetNetCode()

        # (1) clearance vs other-net tracks/vias/zones on newly-added layers
        blockers = []
        for L in newly:
            for item, shape in _other_items_on_layer(board, L, net):
                # Call Collide on the item's generic SHAPE — SHAPE_CIRCLE's
                # own Collide only accepts a SEG in the SWIG binding, but
                # the base SHAPE.Collide(SHAPE, clearance) handles the
                # circle. (clearance in internal units; True if within.)
                try:
                    hit = shape.Collide(circle, clr_iu)
                except Exception:
                    hit = False
                if hit:
                    blockers.append({
                        "layer": _layer_name(board, L),
                        "blocker_net": item.GetNetname() or "<none>",
                        "blocker_class": item.GetClass(),
                    })
                    break  # one blocker per layer is enough

        # (2) pad overlap on BOTH outer (through-endpoint) layers — checked
        #     regardless of the via's current span, so a pad on a layer the
        #     blind via already touched is not missed:
        #       other-net pad within clearance   -> short  (block)
        #       own-net SMD pad the via sits in   -> needs POFV (via-in-pad)
        pad_shorts = []
        in_pads = []
        for L in (fcu, bcu):
            for fp, p in _pads_on_layer(board, L):
                shp = _eff_shape(p, L)
                if shp is None:
                    continue
                if p.GetNetCode() != net:
                    if _collide(shp, circle, clr_iu):
                        pad_shorts.append({
                            "layer": _layer_name(board, L),
                            "pad": _pad_ref(fp, p),
                            "blocker_net": p.GetNetname() or "<none>",
                        })
                elif _is_smd_pad(p) and _collide(shp, circle, 0):
                    in_pads.append({
                        "layer": _layer_name(board, L),
                        "pad": _pad_ref(fp, p),
                    })

        rec = {
            "uuid": via.m_Uuid.AsString(),
            "x_mm": round(pos.x / _MM, 4),
            "y_mm": round(pos.y / _MM, 4),
            "net": via.GetNetname() or "<none>",
            "from_layers": [_layer_name(board, L) for L in sorted(span)],
        }
        if blockers or pad_shorts:
            if blockers:
                rec["blocked_on"] = blockers
            if pad_shorts:
                rec["pad_shorts"] = pad_shorts
            blocked.append(rec)
        elif in_pads:
            rec["adds_layers"] = [_layer_name(board, L) for L in newly]
            rec["in_pads"] = in_pads
            needs_pofv.append(rec)
            pofv_uuids.add(rec["uuid"])
        else:
            rec["adds_layers"] = [_layer_name(board, L) for L in newly]
            promotable.append(rec)
            promoted_uuids.add(rec["uuid"])

    # --- manufacturing-tier summary (distinct blind/buried span types) ---
    def _tier(promote_uuids: set) -> dict:
        spans: dict[str, int] = {}
        for v in vias:
            promoted = (v.m_Uuid.AsString() in promote_uuids
                        or v.GetViaType() == pcbnew.VIATYPE_THROUGH)
            key = through_key if promoted else \
                f"{_layer_name(board, v.TopLayer())}/{_layer_name(board, v.BottomLayer())}"
            spans[key] = spans.get(key, 0) + 1
        bb = {k: n for k, n in spans.items() if k != through_key}
        return {"spans": spans, "blind_buried_types": len(bb),
                "blind_buried_vias": sum(bb.values())}

    return {
        "success": True,
        "zones_filled": fill_ok,
        "clearance_mm": float(clearance_mm),
        "total_vias": total,
        "already_through": already_through,
        "promotable_count": len(promotable),
        "needs_pofv_count": len(needs_pofv),
        "blocked_count": len(blocked),
        "promotable": promotable,
        "needs_pofv": needs_pofv,
        "blocked": blocked,
        "tier_before": _tier(set()),
        "tier_after_promotable": _tier(promoted_uuids),
        "tier_after_with_pofv": _tier(promoted_uuids | pofv_uuids),
    }


def run(pcb_path: str, clearance_mm: float = 0.2) -> dict[str, Any]:
    """Cold, in-process analysis: load + fill + analyse, no caching. Kept
    for direct / unit-test use; the daemon path is the warm version."""
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    try:
        board, fill_ok = _load(pcb_path)
    except Exception as e:
        return {"success": False, "error": f"Failed to load board: {e}"}
    return _analyse_board(board, fill_ok, clearance_mm)


# ---------------------------------------------------------------------------
# warm daemon: board cache (read-only analysis → no eviction needed)
# ---------------------------------------------------------------------------
_CACHE: "dict[str, dict]" = {}   # path -> {mtime, board, fill_ok}
_LOADS = 0


def _get_entry(path: str):
    """Return (entry, cache_hit). Loads+fills on miss; LRU-bumps on hit.
    The analysis is read-only, so a cached board is reused as-is."""
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


def _do_analyse(req: dict) -> dict:
    path = req.get("pcb_path", "")
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
        res = _analyse_board(ent["board"], ent["fill_ok"], clearance_mm)
    except Exception as e:
        _CACHE.pop(path, None)
        # mutated=True forces the client to recycle the process — defensive in
        # case a partial analysis left pcbnew SWIG state degraded.
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:], "loads": _LOADS, "mutated": True}
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
    if op == "analyse":
        return _do_analyse(req)
    return {"ok": False, "error": f"unknown op '{op}'"}


def main():
    _import_pcbnew()  # subprocess entry — pay pcbnew's init once, up front
    sys.stderr.write("via_promote_worker ready\n")
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
            resp = {"ok": False, "error": "daemon error", "traceback": traceback.format_exc()[-2000:]}
        resp["id"] = rid
        sys.stdout.write(MARK + json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
