# SPDX-License-Identifier: GPL-3.0-or-later
"""
Warm connectivity daemon — pcbnew + stdlib ONLY.

A long-lived process spawned by ``connectivity_tools``. It keeps loaded
``BOARD`` objects in memory (cached by path + mtime) so connectivity
queries amortise the expensive pcbnew load/fill across calls:

  * **① Warm board cache.** The first query on a dense, copper-poured
    board pays the full ``LoadBoard`` + zone-fill cost once; every later
    query on the unchanged file reuses the in-memory board (cache hit) and
    returns fast. Previously every ``check_connectivity`` call spawned a
    cold process and re-filled from scratch — the 240 s wall-clock on big
    mainboards.
  * **② Scoped / optional fill.** Zone fill is the dominant cost on a
    poured board. ``overview`` can run with ``fill=False`` for a fast,
    pour-blind ratsnest pass. ``pad`` / ``whatif`` fill *only the relevant
    net's* zones (a net's electrical cluster depends only on that net's
    copper), not the whole board, and the fill state is cached so a repeat
    query on the same net skips the fill entirely.

Protocol: newline-delimited JSON on stdin (requests) / stdout (responses,
each prefixed with ``@@CONN@@`` so stray pcbnew chatter can't be mistaken
for a response). One request → one response.

Imports NOTHING from ``kicad_mcp`` / ``mcp`` — that would drag in the
package ``__init__`` (→ server → all tools, ~3 s) on startup. Run by file
path, not ``-m``.

Read/what-if model: ``whatif`` removes the nearest element from the
*in-memory* board only — the daemon never writes to disk, and it drops the
mutated board from the cache (and signals the client to recycle) so the
next call reloads a pristine board.

``run()`` below is the cold, in-process equivalent (one load per call, no
cache) kept for direct/unit-test use and re-exported by
``connectivity_tools`` as ``_run_in_process``.
"""
import json
import os
import sys
import time
import traceback
from typing import Any

MARK = "@@CONN@@"
MAX_CACHE = 5            # LRU board cache size
_MM = 1_000_000          # nm per mm

# pcbnew is imported lazily (by main() in the daemon, or by _load_board in
# the in-process path) — never at module load, so the heavy-import scan and
# pkgutil walks in the test-suite stay cheap.
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


# ---------------------------------------------------------------------------
# pad / cluster helpers
# ---------------------------------------------------------------------------
def _pad_id(p: Any) -> str:
    fp = p.GetParentFootprint()
    ref = fp.GetReference() if fp else "?"
    return f"{ref}.{p.GetNumber()}"


def _all_pads(board: Any) -> list:
    out = []
    for fp in board.GetFootprints():
        out.extend(fp.Pads())
    return out


def _cluster_pad_ids(conn: Any, pad: Any) -> set:
    """All pads in the same electrical cluster as ``pad`` (same net)."""
    net = pad.GetNetCode()
    ids = {_pad_id(pad)}
    for it in conn.GetConnectedItems(pad):  # single-arg overload
        if it.GetClass() == "PAD" and it.GetNetCode() == net:
            ids.add(_pad_id(it))
    return ids


def _net_clusters(conn: Any, pads: list, netcode: int) -> list:
    """List of clusters (each a set of pad-ids) for one net."""
    remaining = {_pad_id(p): p for p in pads if p.GetNetCode() == netcode}
    clusters = []
    while remaining:
        _, seed = next(iter(remaining.items()))
        ids = (_cluster_pad_ids(conn, seed) & set(remaining)) | {_pad_id(seed)}
        clusters.append(ids)
        for i in ids:
            remaining.pop(i, None)
    return clusters


def _find_pad(board: Any, ref_pad: str):
    if "." not in ref_pad:
        return None, f"ref_pad must be 'REF.PAD', got '{ref_pad}'"
    ref, num = ref_pad.split(".", 1)
    fp = board.FindFootprintByReference(ref)
    if not fp:
        return None, f"Footprint {ref} not found"
    for p in fp.Pads():
        if p.GetNumber() == num:
            return p, None
    return None, f"Pad {ref_pad} not found"


def _nearest_track_or_via(board: Any, x_mm: float, y_mm: float):
    tgt = pcbnew.VECTOR2I(int(x_mm * _MM), int(y_mm * _MM))
    best, best_d2 = None, None
    for t in board.GetTracks():  # includes vias and tracks
        pts = [t.GetPosition()] if t.GetClass() == "PCB_VIA" else [t.GetStart(), t.GetEnd()]
        for pt in pts:
            d2 = (pt.x - tgt.x) ** 2 + (pt.y - tgt.y) ** 2
            if best_d2 is None or d2 < best_d2:
                best_d2, best = d2, t
    dist_mm = (best_d2 ** 0.5 / _MM) if best is not None else None
    return best, dist_mm


def validate(mode: str, ref_pad: str, x_mm, y_mm):
    """Cheap argument validation. Returns an error-dict or ``None``."""
    if mode not in ("overview", "pad", "whatif"):
        return {"success": False, "error": f"unknown mode '{mode}' (use overview|pad|whatif)"}
    if mode == "pad" and not ref_pad:
        return {"success": False, "error": "pad mode requires ref_pad (e.g. 'D_TVS1.2')"}
    if mode == "whatif" and (x_mm is None or y_mm is None):
        return {"success": False, "error": "whatif mode requires x_mm and y_mm"}
    return None


# ---------------------------------------------------------------------------
# board load + (scoped) fill
# ---------------------------------------------------------------------------
def _load_board(path: str):
    """``LoadBoard`` + ``BuildConnectivity``, NO zone fill (fill is scoped /
    deferred to the query that needs it)."""
    _import_pcbnew()
    _real = sys.stdout
    sys.stdout = sys.stderr
    try:
        board = pcbnew.LoadBoard(path)
        board.BuildConnectivity()
    finally:
        sys.stdout = _real
    return board


def _fill_all(board) -> bool:
    """Fill every zone, then rebuild connectivity. Returns False on error."""
    _real = sys.stdout
    sys.stdout = sys.stderr
    try:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.BuildConnectivity()
        return True
    except Exception:  # pragma: no cover - depends on board zones
        return False
    finally:
        sys.stdout = _real


def _fill_net(board, netcode: int) -> bool:
    """Fill only the zones on ``netcode`` (a net's cluster depends only on
    its own copper), then rebuild connectivity. Falls back to a full fill if
    pcbnew rejects the zone subset."""
    _real = sys.stdout
    sys.stdout = sys.stderr
    try:
        zones = [z for z in board.Zones() if z.GetNetCode() == netcode]
        if zones:
            try:
                pcbnew.ZONE_FILLER(board).Fill(zones)
            except TypeError:  # SWIG vector<ZONE*> rejected a Python list
                pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.BuildConnectivity()
        return True
    except Exception:  # pragma: no cover - depends on board zones
        return False
    finally:
        sys.stdout = _real


# ---------------------------------------------------------------------------
# per-mode compute (operate on a loaded + appropriately-filled board)
# ---------------------------------------------------------------------------
def _compute_overview(board) -> dict:
    conn = board.GetConnectivity()
    conn.RecalculateRatsnest()
    unconnected = conn.GetUnconnectedCount(False)
    pads = _all_pads(board)
    nets = board.GetNetInfo()
    fragmented = []
    for code in range(nets.GetNetCount()):
        ni = nets.GetNetItem(code)
        if not ni or code == 0:
            continue
        cl = _net_clusters(conn, pads, code)
        if len(cl) > 1:
            sizes = sorted((len(c) for c in cl), reverse=True)
            fragmented.append({"net": ni.GetNetname(), "clusters": len(cl), "group_sizes": sizes})
    fragmented.sort(key=lambda r: r["clusters"], reverse=True)
    return {
        "success": True, "mode": "overview",
        "unconnected_items": unconnected, "net_count": nets.GetNetCount() - 1,
        "fragmented_net_count": len(fragmented), "fragmented_nets": fragmented,
    }


def _compute_pad(board, ref_pad: str) -> dict:
    pad, err = _find_pad(board, ref_pad)
    if err:
        return {"success": False, "error": err}
    conn = board.GetConnectivity()
    items = conn.GetConnectedItems(pad)
    from collections import Counter

    by_class = dict(Counter(it.GetClass() for it in items))
    ids = sorted({_pad_id(it) for it in items if it.GetClass() == "PAD" and it.GetNetCode() == pad.GetNetCode()})
    return {
        "success": True, "mode": "pad", "ref_pad": ref_pad, "net": pad.GetNetname(),
        "cluster_item_count": len(items), "cluster_items_by_class": by_class,
        "cluster_pad_count": len(ids), "cluster_pads": ids[:100], "truncated": len(ids) > 100,
    }


def _compute_whatif(board, x_mm: float, y_mm: float, ref_pad: str) -> dict:
    """Remove the nearest via/track in memory and report orphaned pads.
    Mutates the in-memory board (caller must drop it from any cache)."""
    item, dist_mm = _nearest_track_or_via(board, x_mm, y_mm)
    if item is None:
        return {"success": False, "error": "no via/track found on board"}
    conn = board.GetConnectivity()
    kind, net, netcode = item.GetClass(), item.GetNetname(), item.GetNetCode()
    pads = _all_pads(board)
    before = _net_clusters(conn, pads, netcode)
    main_before = max(before, key=len) if before else set()
    board.Remove(item)
    board.BuildConnectivity()
    conn2 = board.GetConnectivity()
    after = _net_clusters(conn2, _all_pads(board), netcode)
    main_after = max(after, key=len) if after else set()
    orphaned = sorted(main_before - main_after)
    result = {
        "success": True, "mode": "whatif", "element": kind, "net": net,
        "distance_mm": round(dist_mm, 4) if dist_mm is not None else None,
        "clusters_before": len(before), "clusters_after": len(after),
        "orphaned_pads": orphaned, "load_bearing": bool(orphaned),
    }
    if ref_pad:
        p, err = _find_pad(board, ref_pad)
        if not err:
            result["ref_pad_cluster_after"] = len(_cluster_pad_ids(conn2, p))
    return result


# ---------------------------------------------------------------------------
# cold in-process path (one load per call, no cache) — for tests / direct use
# ---------------------------------------------------------------------------
def run(pcb_path: str, mode: str = "overview", ref_pad: str = "",
        x_mm: float | None = None, y_mm: float | None = None,
        fill: bool = True) -> dict[str, Any]:
    """One-shot connectivity computation: load, fill (scoped to the mode),
    compute. No caching — see the daemon path for the warm version."""
    err = validate(mode, ref_pad, x_mm, y_mm)
    if err:
        return err
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    try:
        board = _load_board(pcb_path)
    except Exception as e:
        return {"success": False, "error": f"Failed to load board: {e}"}

    if mode == "overview":
        filled = _fill_all(board) if fill else False
        res = _compute_overview(board)
        res["zones_filled"] = filled
        return res

    if mode == "pad":
        pad, perr = _find_pad(board, ref_pad)
        if perr:
            return {"success": False, "error": perr}
        filled = _fill_net(board, pad.GetNetCode())
        res = _compute_pad(board, ref_pad)
        if res.get("success"):
            res["zones_filled"] = filled
        return res

    # whatif
    item, _d = _nearest_track_or_via(board, x_mm, y_mm)
    if item is None:
        return {"success": False, "error": "no via/track found on board"}
    filled = _fill_net(board, item.GetNetCode())
    res = _compute_whatif(board, x_mm, y_mm, ref_pad)
    if res.get("success"):
        res["zones_filled"] = filled
    return res


# ---------------------------------------------------------------------------
# warm daemon: board cache + scoped fill
# ---------------------------------------------------------------------------
_CACHE: "dict[str, dict]" = {}   # path -> {mtime, board, filled_all, filled_nets, dirty}
_LOADS = 0


def _get_entry(path: str):
    """Return (entry, cache_hit). Loads (no fill) on miss; LRU-bumps on hit."""
    global _LOADS
    st = os.stat(path)
    ent = _CACHE.get(path)
    if ent is not None and ent["mtime"] == st.st_mtime_ns and not ent.get("dirty"):
        _CACHE[path] = _CACHE.pop(path)  # LRU bump
        return ent, True
    board = _load_board(path)
    _LOADS += 1
    ent = {"mtime": st.st_mtime_ns, "board": board,
           "filled_all": False, "filled_nets": set(), "dirty": False}
    _CACHE[path] = ent
    while len(_CACHE) > MAX_CACHE:
        _CACHE.pop(next(iter(_CACHE)))
    return ent, False


def _ensure_fill(ent: dict, netcode: int | None) -> bool:
    """Top up the cached board's fill state. ``netcode=None`` fills all
    zones; an int fills just that net's zones. Idempotent per (board, net)."""
    board = ent["board"]
    if netcode is None:
        if ent["filled_all"]:
            return True
        ok = _fill_all(board)
        ent["filled_all"] = True   # attempted (stays True even if no zones)
        ent["filled_nets"] = set()  # subsumed by the full fill
        return ok
    if ent["filled_all"] or netcode in ent["filled_nets"]:
        return True
    ok = _fill_net(board, netcode)
    ent["filled_nets"].add(netcode)
    return ok


def _do_conn(req: dict, mode: str) -> dict:
    path = req.get("pcb_path", "")
    ref_pad = req.get("ref_pad", "") or ""
    x_mm, y_mm = req.get("x_mm"), req.get("y_mm")
    want_fill = bool(req.get("fill", True))
    if not os.path.isfile(path):
        return {"ok": False, "error": f"PCB not found: {path}"}
    err = validate(mode, ref_pad, x_mm, y_mm)
    if err:
        return {"ok": False, "error": err["error"]}
    t0 = time.time()
    try:
        ent, hit = _get_entry(path)
    except Exception as e:
        return {"ok": False, "error": f"Failed to load board: {e}",
                "traceback": traceback.format_exc()[-2000:], "loads": _LOADS}
    board = ent["board"]
    mutated = False
    try:
        if mode == "overview":
            if want_fill:
                _ensure_fill(ent, None)
            filled = ent["filled_all"]
            res = _compute_overview(board)
        elif mode == "pad":
            pad, perr = _find_pad(board, ref_pad)
            if perr:
                return {"ok": False, "error": perr, "loads": _LOADS, "cache_hit": hit}
            filled = _ensure_fill(ent, pad.GetNetCode())
            res = _compute_pad(board, ref_pad)
        else:  # whatif
            item, _d = _nearest_track_or_via(board, x_mm, y_mm)
            if item is None:
                return {"ok": False, "error": "no via/track found on board",
                        "loads": _LOADS, "cache_hit": hit}
            filled = _ensure_fill(ent, item.GetNetCode())
            res = _compute_whatif(board, x_mm, y_mm, ref_pad)
            mutated = True  # whatif removed an element from the in-memory board
    except Exception as e:
        _CACHE.pop(path, None)
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:], "loads": _LOADS, "mutated": True}

    if mutated:
        _CACHE.pop(path, None)  # board no longer pristine → reload next call
    if not res.get("success"):
        return {"ok": False, "error": res.get("error", "compute failed"),
                "loads": _LOADS, "cache_hit": hit, "mutated": mutated}
    res = {k: v for k, v in res.items() if k != "success"}
    return {"ok": True, **res, "zones_filled": filled, "cache_hit": hit,
            "loads": _LOADS, "mutated": mutated,
            "elapsed_ms": round((time.time() - t0) * 1000, 1)}


def _handle(req: dict) -> dict:
    op = req.get("op")
    if op == "ping":
        return {"ok": True, "pong": True, "loads": _LOADS}
    if op == "status":
        return {"ok": True, "loads": _LOADS,
                "cached": [{"path": p, "mtime_ns": e["mtime"], "filled_all": e["filled_all"],
                            "filled_nets": len(e["filled_nets"]), "dirty": e.get("dirty", False)}
                           for p, e in _CACHE.items()]}
    if op == "reset":
        p = req.get("pcb_path")
        if p:
            _CACHE.pop(p, None)
        else:
            _CACHE.clear()
        return {"ok": True, "cleared": p or "all"}
    if op in ("overview", "pad", "whatif"):
        return _do_conn(req, op)
    return {"ok": False, "error": f"unknown op '{op}'"}


def main():
    _import_pcbnew()  # subprocess entry — pay pcbnew's init once, up front
    sys.stderr.write("connectivity_worker ready\n")
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
        # The client (connectivity_tools) owns recycling: after a response
        # flagged ``mutated`` (a what-if poisons the pcbnew interpreter so
        # repeated LoadBoard returns un-typed objects), carrying
        # 'SwigPyObject', or once loads hit the cap, it KILLS this process
        # and respawns on its next request.


if __name__ == "__main__":
    main()
