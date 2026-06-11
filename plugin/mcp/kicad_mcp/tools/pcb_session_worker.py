# SPDX-License-Identifier: GPL-3.0-or-later
"""
Warm-board pcbnew session daemon — pcbnew + stdlib ONLY.

A long-lived process spawned by ``pcb_session_tools``. It keeps loaded +
zone-filled ``BOARD`` objects in memory (cached by path + mtime) and runs
arbitrary analysis code against them in milliseconds. The first query on
a board pays ~1 s (LoadBoard + Fill); every later query on the unchanged
board is ~ms — the 100× win.

Protocol: newline-delimited JSON on stdin (requests) / stdout (responses,
each prefixed with ``@@PCBEVAL@@`` so stray pcbnew chatter can't be
mistaken for a response). One request → one response. Requests are
serialised by the client, so responses come back in order.

Imports NOTHING from ``kicad_mcp`` / ``mcp`` — that would drag in the
package ``__init__`` (→ server → all tools, ~3 s) on startup. Run by file
path, not ``-m``.

Read/what-if model: code may mutate the in-memory board for what-if
analysis, but the daemon never writes to disk. Real edits stay with the
(format-preserving, batchable) text-patch tools.
"""
import io
import json
import os
import sys
import time
import traceback

MARK = "@@PCBEVAL@@"
MAX_CACHE = 5           # LRU board cache size
_DEFAULT_MAX_CHARS = 8000

# pcbnew is imported lazily by main(), NOT at module load. Importing this
# file must stay cheap: tooling that merely inspects ``tools/*`` (the
# test-suite's heavy-import scan, pkgutil walks) must not pay pcbnew's
# multi-second SWIG init. The worker only ever runs as a subprocess via
# ``python pcb_session_worker.py``, where main() pulls pcbnew in before
# serving the first request. Functions below reference ``pcbnew`` as a
# module global, resolved at call time.
pcbnew = None  # type: ignore[assignment]


def _import_pcbnew() -> None:
    """Import pcbnew into the module global once, with stdout muted.

    pcbnew prints "Adding duplicate image handler …" to stdout on import;
    route that to stderr so the ``@@PCBEVAL@@`` protocol stream stays clean.
    """
    global pcbnew
    if pcbnew is not None:
        return
    _real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        import pcbnew as _pcbnew  # noqa: E402
    finally:
        sys.stdout = _real_stdout
    pcbnew = _pcbnew

_CACHE: "dict[str, dict]" = {}   # path -> {mtime, board, filled, ctx}
_LOADS = 0


# ---------------------------------------------------------------------------
# board cache
# ---------------------------------------------------------------------------
def _board_sig(board):
    """Cheap fingerprint to detect in-memory mutation (what-if edits)."""
    try:
        return (len(board.GetFootprints()), len(board.GetTracks()))
    except Exception:
        return None


def _load(path: str):
    """Return (entry, cache_hit). Loads+fills+builds-connectivity on miss."""
    global _LOADS
    st = os.stat(path)
    ent = _CACHE.get(path)
    if ent is not None and ent["mtime"] == st.st_mtime_ns and not ent.get("dirty"):
        _CACHE[path] = _CACHE.pop(path)  # LRU bump
        return ent, True
    # capture pcbnew chatter during load
    old = sys.stdout
    sys.stdout = sys.stderr
    load_ms = {}
    try:
        _t = time.time()
        board = pcbnew.LoadBoard(path)
        load_ms["load_board"] = round((time.time() - _t) * 1000, 1)
        filled = True
        _t = time.time()
        try:
            pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        except Exception:
            filled = False
        load_ms["zone_fill"] = round((time.time() - _t) * 1000, 1)
        _t = time.time()
        board.BuildConnectivity()
        load_ms["build_connectivity"] = round((time.time() - _t) * 1000, 1)
    finally:
        sys.stdout = old
    try:
        load_ms["n_zones"] = len(list(board.Zones()))
        load_ms["n_tracks"] = board.GetTracks().size() if hasattr(board.GetTracks(), "size") else len(board.GetTracks())
        load_ms["n_footprints"] = len(board.GetFootprints())
    except Exception:
        pass
    load_ms["total"] = round(sum(v for k, v in load_ms.items()
                                 if k in ("load_board", "zone_fill", "build_connectivity")), 1)
    # real stderr (the load block redirected stdout->stderr above); DEVNULL'd by
    # the daemon in production, but visible when the worker runs standalone.
    try:
        sys.__stderr__.write("pcb_session_worker LOAD %s -> %r\n"
                             % (os.path.basename(path), load_ms))
        sys.__stderr__.flush()
    except Exception:
        pass
    _LOADS += 1
    ent = {"mtime": st.st_mtime_ns, "board": board, "filled": filled,
           "ctx": {}, "dirty": False, "sig": _board_sig(board), "load_ms": load_ms}
    _CACHE[path] = ent
    while len(_CACHE) > MAX_CACHE:
        _CACHE.pop(next(iter(_CACHE)))
    return ent, False


# ---------------------------------------------------------------------------
# helper library exposed to eval'd code
# ---------------------------------------------------------------------------
def _make_namespace(board, ent):
    import math

    CX, CY = 148.5, 105.0  # reference board centre; override in code if needed
    MM = 1_000_000

    def _arc_pts(t, n=40):
        s, m, e = t.GetStart(), t.GetMid(), t.GetEnd()
        ax, ay, bx, by, cx, cy = (s.x / MM, s.y / MM, m.x / MM, m.y / MM, e.x / MM, e.y / MM)
        d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(d) < 1e-12:
            return [(ax, ay), (cx, cy)]
        ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
        uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
        r = math.hypot(ax - ux, ay - uy)
        a0 = math.atan2(ay - uy, ax - ux)
        a2 = math.atan2(cy - uy, cx - ux)
        sp = a2 - a0
        while sp <= -math.pi:
            sp += 2 * math.pi
        while sp > math.pi:
            sp -= 2 * math.pi
        return [(ux + r * math.cos(a0 + sp * i / n), uy + r * math.sin(a0 + sp * i / n)) for i in range(n + 1)]

    def _track_pts(t):
        if t.GetClass() == "PCB_ARC":
            return _arc_pts(t)
        s, e = t.GetStart(), t.GetEnd()
        return [(s.x / MM + (e.x - s.x) / MM * i / 8, s.y / MM + (e.y - s.y) / MM * i / 8) for i in range(9)]

    def lname(layer):
        return board.GetLayerName(layer)

    def find_pad(ref, num):
        fp = board.FindFootprintByReference(ref)
        if not fp:
            return None
        for p in fp.Pads():
            if p.GetNumber() == str(num):
                return p
        return None

    def world_pos(ref, num):
        p = find_pad(ref, num)
        return None if p is None else (p.GetCenter().x / MM, p.GetCenter().y / MM)

    def fp_pads(ref):
        fp = board.FindFootprintByReference(ref)
        if not fp:
            return []
        out = []
        for p in fp.Pads():
            c = p.GetCenter()
            out.append({"pad": p.GetNumber(), "x": round(c.x / MM, 4), "y": round(c.y / MM, 4),
                        "net": p.GetNetname(),
                        "layers": [lname(l) for l in p.GetLayerSet().CuStack()]})
        return out

    def pads_on_net(net):
        out = []
        for fp in board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetname() == net:
                    c = p.GetCenter()
                    out.append({"ref": f"{fp.GetReference()}.{p.GetNumber()}",
                                "x": round(c.x / MM, 4), "y": round(c.y / MM, 4),
                                "layers": [lname(l) for l in p.GetLayerSet().CuStack()]})
        return out

    def cluster_of(ref, num):
        p = find_pad(ref, num)
        if p is None:
            return None
        conn = board.GetConnectivity()
        ids = set()
        for it in conn.GetConnectedItems(p):
            if it.GetClass() == "PAD" and it.GetNetCode() == p.GetNetCode():
                f = it.GetParentFootprint()
                ids.add(f"{f.GetReference()}.{it.GetNumber()}" if f else "?")
        ids.add(f"{ref}.{num}")
        return sorted(ids)

    def what_touches(x, y, r=0.4, layers=None, exclude_net=None):
        """Copper (track/arc/via/pad) within r mm of (x,y), arc-accurate."""
        hits = []
        for t in board.GetTracks():
            net = t.GetNetname()
            if exclude_net and net == exclude_net:
                continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                d = math.hypot(pos.x / MM - x, pos.y / MM - y)
                lay = "via"
            else:
                ln = lname(t.GetLayer())
                if layers and ln not in layers:
                    continue
                d = min(math.hypot(px - x, py - y) for px, py in _track_pts(t))
                lay = ln
            if d <= r:
                hits.append({"kind": t.GetClass(), "net": net, "layer": lay, "dist": round(d, 4)})
        for fp in board.GetFootprints():
            for p in fp.Pads():
                if exclude_net and p.GetNetname() == exclude_net:
                    continue
                c = p.GetCenter()
                d = math.hypot(c.x / MM - x, c.y / MM - y)
                if d <= r:
                    hits.append({"kind": "pad", "ref": f"{fp.GetReference()}.{p.GetNumber()}",
                                 "net": p.GetNetname(), "dist": round(d, 4)})
        return sorted(hits, key=lambda h: h["dist"])

    def nearest_copper(x, y, layers=None, exclude_net=None):
        h = what_touches(x, y, r=5.0, layers=layers, exclude_net=exclude_net)
        return h[0] if h else None

    def rt(x, y):
        return (math.hypot(x - CX, y - CY), math.degrees(math.atan2(y - CY, x - CX)))

    def xy(r, theta_deg):
        a = math.radians(theta_deg)
        return (CX + r * math.cos(a), CY + r * math.sin(a))

    def ring_radius(n, r_outer=30.0, step=0.55):
        return r_outer - (n - 1) * step

    def fill():
        ent["dirty"] = True
        try:
            pcbnew.ZONE_FILLER(board).Fill(board.Zones())
            board.BuildConnectivity()
            return True
        except Exception:
            return False

    def unconnected():
        conn = board.GetConnectivity()
        conn.RecalculateRatsnest()
        return conn.GetUnconnectedCount(False)

    def nets():
        ni = board.GetNetInfo()
        return [ni.GetNetItem(c).GetNetname() for c in range(ni.GetNetCount())
                if ni.GetNetItem(c) and c != 0]

    def helpers():
        """Self-doc: every pre-bound name with signature → return shape.
        Run ``result = helpers()`` whenever unsure — it lives with the code,
        so it never goes stale."""
        return {
            "board": "pcbnew BOARD (loaded + zone-filled); mutate freely for what-if (NOT saved to disk)",
            "pcbnew": "the pcbnew module", "math": "stdlib math", "MM": "1e6 (nm per mm)",
            "CX, CY": "board centre (148.5, 105); reassign for non-reference boards",
            "ctx": "dict persisting across pcb_eval calls on the same board",
            "world_pos(ref, num)": "→ [x_mm, y_mm] of a pad, or None (flip-aware)",
            "find_pad(ref, num)": "→ the pcbnew PAD object, or None",
            "fp_pads(ref)": "→ [{pad, x, y, net, layers:[..]}] for a footprint",
            "pads_on_net(net)": "→ [{ref:'R.N', x, y, layers}] every pad on a net",
            "cluster_of(ref, num)": "→ sorted ['R.N',..] pads electrically joined to this pad (connectivity), or None",
            "what_touches(x, y, r=0.4, layers=None, exclude_net=None)":
                "→ [{kind, net, layer, dist}] copper within r mm (ARC-ACCURATE; pads give 'ref'); sorted by dist",
            "nearest_copper(x, y, layers=None, exclude_net=None)": "→ nearest what_touches hit, or None",
            "rt(x, y)": "→ (radius, theta_deg) from (CX,CY)",
            "xy(r, theta_deg)": "→ (x, y) on a polar ring",
            "ring_radius(n, r_outer=30.0, step=0.55)": "→ radius of polar ring N (counted from outside)",
            "fill()": "re-fill zones + rebuild connectivity (marks board dirty → daemon recycles after)",
            "unconnected()": "→ int ratsnest/unconnected-item count (whole board)",
            "nets()": "→ [net names]",
            "result": "set this to your JSON-able answer; print(...) is captured separately",
        }

    return {
        "pcbnew": pcbnew, "board": board, "math": math, "ctx": ent["ctx"],
        "CX": CX, "CY": CY, "MM": MM,
        "lname": lname, "find_pad": find_pad, "world_pos": world_pos,
        "fp_pads": fp_pads, "pads_on_net": pads_on_net, "cluster_of": cluster_of,
        "what_touches": what_touches, "nearest_copper": nearest_copper,
        "rt": rt, "xy": xy, "ring_radius": ring_radius,
        "fill": fill, "unconnected": unconnected, "nets": nets,
        "helpers": helpers,
        "result": None,
    }


def _safe_json(value, max_chars):
    """Best-effort JSON-serialise; truncate large results."""
    try:
        s = json.dumps(value, default=str)
    except Exception:
        s = json.dumps(str(value))
    if len(s) <= max_chars:
        return json.loads(s), False
    if isinstance(value, list):
        head = value[:50]
        try:
            return {"_truncated_list": True, "shown": len(head), "total": len(value),
                    "head": json.loads(json.dumps(head, default=str))}, True
        except Exception:
            pass
    return {"_truncated": True, "preview": s[:max_chars]}, True


def _do_eval(req):
    path = req.get("pcb_path", "")
    code = req.get("code", "")
    max_chars = int(req.get("max_chars", _DEFAULT_MAX_CHARS))
    if not os.path.isfile(path):
        return {"ok": False, "error": f"PCB not found: {path}"}
    t0 = time.time()
    try:
        ent, hit = _load(path)
    except Exception as e:
        return {"ok": False, "error": f"load failed: {e}", "traceback": traceback.format_exc()[-2000:]}
    ns = _make_namespace(ent["board"], ent)
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    err = None
    tb = None
    try:
        # Intentional eval substrate — same trust boundary as Bash (which the agent already has).
        exec(code, ns)  # noqa: S102  # pylint: disable=exec-used
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        tb = traceback.format_exc()[-4000:]
    finally:
        sys.stdout = old
    out = buf.getvalue()
    if len(out) > max_chars:
        out = out[-max_chars:]
    # Detect in-memory mutation (what-if edits) or SWIG degradation → drop the
    # cache entry so the NEXT call reloads a clean board. Keeps warm reuse safe.
    new_sig = _board_sig(ent["board"])
    mutated = (new_sig is None or new_sig != ent.get("sig")
               or bool(tb and "SwigPyObject" in tb))
    if mutated:
        _CACHE.pop(path, None)
    if err is not None:
        return {"ok": False, "error": err, "traceback": tb, "stdout": out,
                "cache_hit": hit, "loads": _LOADS, "mutated": mutated}
    res, truncated = _safe_json(ns.get("result"), max_chars)
    return {"ok": True, "result": res, "result_truncated": truncated, "stdout": out,
            "cache_hit": hit, "loads": _LOADS, "zones_filled": ent["filled"],
            "mutated": mutated, "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "load_ms": (None if hit else ent.get("load_ms"))}


def _handle(req):
    op = req.get("op")
    if op == "ping":
        return {"ok": True, "pong": True, "loads": _LOADS}
    if op == "status":
        return {"ok": True, "loads": _LOADS,
                "cached": [{"path": p, "mtime_ns": e["mtime"], "filled": e["filled"],
                            "dirty": e.get("dirty", False)} for p, e in _CACHE.items()]}
    if op == "reset":
        p = req.get("pcb_path")
        if p:
            _CACHE.pop(p, None)
        else:
            _CACHE.clear()
        return {"ok": True, "cleared": p or "all"}
    if op == "eval":
        return _do_eval(req)
    return {"ok": False, "error": f"unknown op '{op}'"}


def main():
    _import_pcbnew()  # subprocess entry — pay pcbnew's init once, up front
    sys.stderr.write("pcb_session_worker ready\n")
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
        # NOTE: the client (pcb_session_tools) owns recycling. After a
        # response flagged ``mutated`` (a what-if poisons the pcbnew
        # interpreter so badly even the next LoadBoard returns un-typed
        # objects), or one carrying 'SwigPyObject', or once loads hit the
        # cap, the client KILLS this process synchronously and respawns on
        # its next request. Self-exiting here instead would race the
        # client's next write (write-to-dying-daemon → hang).


if __name__ == "__main__":
    main()
