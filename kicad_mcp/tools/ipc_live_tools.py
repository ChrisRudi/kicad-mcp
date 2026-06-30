# SPDX-License-Identifier: GPL-3.0-or-later
# ipc_live_tools.py
# Live IPC layer over a running KiCad 10 PCB editor (kipy). PULL-only: reads
# the *living* editor state (never the read-cache), diffs it against a daemon
# snapshot to surface manual user edits, and writes via retry-wrapped,
# agent:-tagged commits that are individually undoable in Local History.
# Inputs:  a running KiCad with an open PCB + IPC API enabled (verified by
#          verify_kicad_ipc.py). Env KICAD_MCP_LIVE_READONLY=1 disables writes.
# Outputs: MCP tools live_get_state / live_diff_since_last /
#          live_summarize_user_changes / live_move_footprint / live_session_status.
# Deps:    kipy (transport), stdlib. NEVER touches the PCB read-cache.

from __future__ import annotations

import glob
import json
import logging
import os
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools.ipc_live_diff import (
    attribute,
    cas_conflict,
    diff_snapshots,
    fp_signature,
    make_record,
    summarize_user,
    track_signature,
    via_signature,
)

log = logging.getLogger("kicad_mcp.ipc_live_tools")

_RETRY_ATTEMPTS = 4
_RETRY_BASE_S = 0.15
_DEFAULT_POLL_S = 30  # fallback when autosave is disabled


def _read_only() -> bool:
    return os.environ.get("KICAD_MCP_LIVE_READONLY", "").strip() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Daemon state — lives HERE, beside (never inside) the read-cache.
# ---------------------------------------------------------------------------

_STATE: dict[str, Any] = {
    "client": None,        # cached kipy.KiCad()
    "snapshot": {},        # {kiid: record} baseline for the diff
    "pending": {},         # {kiid: record} agent self-writes since last diff
    "board_id": None,      # identity string for board-change detection
    "last_pull": None,     # epoch seconds of last successful pull
}


# ---------------------------------------------------------------------------
# kipy connection + health
# ---------------------------------------------------------------------------


def _kicad():
    """Persistent kipy client; health-pings and transparently reconnects."""
    import kipy

    client = _STATE.get("client")
    if client is not None:
        try:
            client.get_version()
            return client
        except Exception:  # socket died -> reconnect below
            log.warning("live: IPC ping failed, reconnecting")
            _STATE["client"] = None
    _STATE["client"] = kipy.KiCad()
    return _STATE["client"]


def _board(k):
    return k.get_board()


def _board_identity(k, board) -> str:
    """Best-effort stable id of the open PCB for board-change detection.

    Prefers the open-document path/name; falls back to a coarse fingerprint
    so a different board still invalidates the snapshot.
    """
    for getter in ("get_open_documents",):
        try:
            docs = getattr(k, getter)()
            for d in (docs if isinstance(docs, (list, tuple)) else [docs]):
                for attr in ("board_filename", "filename", "name"):
                    val = getattr(d, attr, None)
                    if val:
                        return str(val)
                proj = getattr(d, "project", None)
                if proj is not None:
                    for attr in ("name", "path"):
                        val = getattr(proj, attr, None)
                        if val:
                            return str(val)
        except Exception:
            pass
    try:
        proj = board.get_project()
        for attr in ("name", "path"):
            val = getattr(proj, attr, None)
            if val:
                return str(val)
    except Exception:
        pass
    return "board:unknown"


def _retry(fn, label: str):
    """Retry a write-side IPC call with exponential backoff. KiCad serialises
    API + UI on one thread, so a transient failure means 'busy', not a race."""
    last = None
    for i in range(_RETRY_ATTEMPTS):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised after retries
            last = exc
            wait = _RETRY_BASE_S * (2 ** i)
            log.warning("live: %s attempt %d/%d failed (%s); retry in %.2fs",
                        label, i + 1, _RETRY_ATTEMPTS, exc, wait)
            time.sleep(wait)
    raise last


# ---------------------------------------------------------------------------
# Field extraction (uses the runtime-verified kipy field names)
# ---------------------------------------------------------------------------


def _kiid(item) -> str:
    kid = getattr(item, "id", None)
    val = getattr(kid, "value", None)
    return str(val) if val is not None else str(kid)


def _angle_deg(angle) -> float:
    for attr in ("degrees",):
        val = getattr(angle, attr, None)
        if val is not None:
            try:
                return float(val)
            except Exception:
                pass
    try:
        return float(angle)
    except Exception:
        return 0.0


def _net_name(net) -> str:
    return str(getattr(net, "name", "") or "")


def _layer_name(board, layer_id) -> Any:
    try:
        return board.get_layer_name(layer_id)
    except Exception:
        return layer_id


def build_snapshot(board) -> dict:
    """LIVE read of the editor (footprints + tracks + vias) -> {kiid: record}.

    Always reads fresh from kipy; the result is NEVER put in the read-cache.
    """
    snap: dict[str, dict] = {}
    for fp in board.get_footprints():
        pos = fp.position
        lname = _layer_name(board, fp.layer)
        snap[_kiid(fp)] = make_record(
            "footprint",
            fp_signature(pos.x, pos.y, _angle_deg(fp.orientation), fp.layer),
            x=pos.x, y=pos.y, layer=lname)
    for t in board.get_tracks():
        s, e = t.start, t.end
        net = _net_name(t.net)
        lname = _layer_name(board, t.layer)
        snap[_kiid(t)] = make_record(
            "track",
            track_signature(s.x, s.y, e.x, e.y, t.width, t.layer, net),
            x=s.x, y=s.y, layer=lname, net=net)
    for v in board.get_vias():
        p = v.position
        net = _net_name(v.net)
        drill = getattr(v, "drill_diameter", 0) or 0
        snap[_kiid(v)] = make_record(
            "via",
            via_signature(p.x, p.y, v.diameter, drill, net,
                          getattr(v, "type", None)),
            x=p.x, y=p.y, layer="via", net=net)
    return snap


def _board_center(snap: dict) -> tuple[int, int]:
    xs = [r["x"] for r in snap.values() if r.get("x") is not None]
    ys = [r["y"] for r in snap.values() if r.get("y") is not None]
    if not xs or not ys:
        return 0, 0
    return (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2


# ---------------------------------------------------------------------------
# Watch-then-pull: autosave cadence (read at runtime, never hardcoded)
# ---------------------------------------------------------------------------


def _kicad_common_paths() -> list[str]:
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "kicad"))
    roots.append(os.path.expanduser("~/.config/kicad"))
    out = []
    for root in roots:
        out.extend(glob.glob(os.path.join(root, "*", "kicad_common.json")))
        out.append(os.path.join(root, "kicad_common.json"))
    return out


def _persist_cadence() -> tuple[int | None, str]:
    """Editor persist cadence (seconds) for watch-then-pull, read at runtime.

    KiCad 10 dropped ``system.autosave_interval``; it now persists edits to
    Local History on a debounce. Lookup order: explicit autosave_interval ->
    Local History debounce (if enabled) -> None (caller falls back to interval
    polling). Returns ``(seconds_or_None, source)``.
    """
    for path in _kicad_common_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            log.warning("live: cannot read %s: %s", path, exc)
            continue
        sysd = data.get("system", {})
        if "autosave_interval" in sysd:
            return int(sysd["autosave_interval"]), "autosave_interval"
        if sysd.get("local_history_enabled"):
            return (int(sysd.get("local_history_debounce", 5)),
                    "local_history_debounce")
    return None, "default"


# ---------------------------------------------------------------------------
# Core pull + diff
# ---------------------------------------------------------------------------


def _pull_and_diff() -> dict:
    """Connect, verify board identity, build a fresh live snapshot, diff it
    against the baseline + pending agent writes, then re-baseline.
    """
    k = _kicad()
    board = _retry(lambda: _board(k), "get_board")
    ident = _board_identity(k, board)
    if _STATE["board_id"] is not None and ident != _STATE["board_id"]:
        log.warning("live: board changed (%s -> %s); snapshot invalidated",
                    _STATE["board_id"], ident)
        _STATE["snapshot"] = {}
        _STATE["pending"] = {}
    _STATE["board_id"] = ident

    new = _retry(lambda: build_snapshot(board), "read snapshot")
    old = _STATE["snapshot"]
    raw = diff_snapshots(old, new)
    attrib = attribute(raw, new, _STATE["pending"])

    cx, cy = _board_center(new or old)
    summary = summarize_user(attrib["user"], old, new, cx, cy)

    # re-baseline: the new live state becomes the snapshot, pending consumed.
    _STATE["snapshot"] = new
    _STATE["pending"] = {}
    _STATE["last_pull"] = time.time()
    return {"raw": raw, "attrib": attrib, "summary": summary,
            "snapshot_size": len(new), "board_id": ident}


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


def register_ipc_live_tools(mcp: FastMCP) -> None:
    """Register the live IPC tools. Additive; reads/writes the LIVE editor
    state only, never the file-based read-cache."""

    @mcp.tool()
    def live_get_state() -> dict[str, Any]:
        """Read the LIVE board state from the running KiCad editor (uncached).

        Reads footprints + tracks + vias straight from kipy and re-baselines
        the diff snapshot. This is the *living* editor state, not the last
        saved file (use the file-based tools for the saved world). Never
        cached, so it cannot go stale against the diff.

        Use this before a ``live_diff_since_last`` to set a fresh baseline,
        or whenever you suspect the diff snapshot drifted from the editor;
        for the saved-on-disk world use the file-based tools instead.

        Returns:
            ``{success, board_id, counts:{footprints,tracks,vias},
            snapshot_size, read_only}``.
        """
        try:
            k = _kicad()
            board = _retry(lambda: _board(k), "get_board")
            new = _retry(lambda: build_snapshot(board), "read snapshot")
            _STATE["snapshot"] = new
            _STATE["pending"] = {}
            _STATE["board_id"] = _board_identity(k, board)
            _STATE["last_pull"] = time.time()
            counts = {"footprints": 0, "tracks": 0, "vias": 0}
            for r in new.values():
                counts[r["t"] + "s"] = counts.get(r["t"] + "s", 0) + 1
            return {"success": True, "board_id": _STATE["board_id"],
                    "counts": counts, "snapshot_size": len(new),
                    "read_only": _read_only()}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"live_get_state: {exc}"}

    @mcp.tool()
    def live_diff_since_last() -> dict[str, Any]:
        """Diff the LIVE editor state against the last snapshot.

        Surfaces manual user edits (footprints/tracks/vias) made since the
        last pull, each attributed agent vs user: agent self-writes are
        masked via the pending-changes set written by live_move_footprint;
        everything else is a real user edit. Re-baselines afterwards, so two
        calls in a row show nothing the second time.

        Returns:
            ``{success, added/removed/changed (raw kiid lists),
            agent:{added,removed,changed}, user:{added,removed,changed},
            user_summary, snapshot_size, board_id}``. Call live_get_state
            once first to establish a baseline.
        """
        try:
            res = _pull_and_diff()
            return {"success": True, **res["raw"],
                    "agent": res["attrib"]["agent"],
                    "user": res["attrib"]["user"],
                    "user_summary": res["summary"],
                    "snapshot_size": res["snapshot_size"],
                    "board_id": res["board_id"]}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"live_diff_since_last: {exc}"}

    @mcp.tool()
    def live_summarize_user_changes() -> dict[str, Any]:
        """Plain-language hand-off of the user's edits since the last step.

        The human-AI hand-off point: instead of raw KIID lists, returns a
        sentence grouped by change type, layer and board quadrant, e.g.
        "User moved 3 footprints on F.Cu in the upper-left quadrant;
        re-routed 2 tracks on B.Cu in the lower-right quadrant." Performs a
        fresh pull+diff under the hood.

        Returns:
            ``{success, summary, user_change_count, board_id}``.
        """
        try:
            res = _pull_and_diff()
            u = res["attrib"]["user"]
            count = len(u["added"]) + len(u["removed"]) + len(u["changed"])
            return {"success": True, "summary": res["summary"],
                    "user_change_count": count, "board_id": res["board_id"]}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"live_summarize_user_changes: {exc}"}

    @mcp.tool()
    def live_move_footprint(reference: str, x_mm: float, y_mm: float,
                            dry_run: bool = True,
                            expect_sig: list | None = None) -> dict[str, Any]:
        """Move a footprint in the LIVE editor (visible immediately).

        Use this when KiCad is open and the move should appear at once in
        the GUI; for headless / on-disk edits use ``ipc_set_footprint_pose``
        or the file patchers instead.

        Write path: retry-wrapped (KiCad-busy -> backoff, not crash),
        committed with an ``agent:`` message so it is its own undoable Local
        History entry, and self-write-masked (the new position is recorded in
        the pending set so the next diff does NOT report it as a user edit).

        Live-collaboration safety (compare-and-swap): a real write is REFUSED
        when the footprint changed since the agent planned the move — i.e. the
        user moved it concurrently — so the agent never silently clobbers a
        user edit (KiCad has no item lock). The plan baseline comes from
        ``expect_sig`` if given, else from the last live snapshot
        (``live_get_state`` / ``live_diff_since_last``). Every result carries
        the current ``sig``; the intended flow is dry_run -> read ``sig`` ->
        confirm with ``expect_sig=sig``. On a clash the tool returns
        ``{success: False, conflict: True, who: "user", ...}`` instead of
        writing — re-read with ``live_get_state`` and re-plan.

        Args:
            reference: footprint reference designator (e.g. "U3").
            x_mm: target X position in mm (board coordinates).
            y_mm: target Y position in mm (board coordinates).
            dry_run: if True (default), report old->new position + affected
                nets WITHOUT writing. This is also the mechanical half of a
                human-in-the-loop confirm gate.
            expect_sig: the footprint signature the move was planned against
                (the ``sig`` from a prior dry_run). When set, the write is
                refused if the live signature no longer matches it and the
                change is not the agent's own.

        Returns:
            ``{success, dry_run, reference, old_mm, new_mm, nets, sig, wrote,
            committed}``, ``{success: False, conflict: True, who, baseline_sig,
            current_sig, ...}`` on a user clash, or ``{success: False, error}``.
        """
        if not dry_run and _read_only():
            return {"success": False, "read_only": True,
                    "error": "live layer is read-only "
                             "(KICAD_MCP_LIVE_READONLY set); write refused."}
        try:
            from kipy.geometry import Vector2
            k = _kicad()
            board = _retry(lambda: _board(k), "get_board")
            target = None
            for fp in _retry(board.get_footprints, "read footprints"):
                ref = getattr(getattr(fp, "reference_field", None), "text", None)
                if ref is not None and getattr(ref, "value", None) == reference:
                    target = fp
                    break
            if target is None:
                return {"success": False, "error": f"footprint {reference} not found"}
            old = target.position
            old_mm = [old.x / 1_000_000, old.y / 1_000_000]
            new_x = int(round(x_mm * 1_000_000))
            new_y = int(round(y_mm * 1_000_000))
            pads = getattr(getattr(target, "definition", None), "pads", None) or []
            nets = sorted({_net_name(p.net) for p in pads if _net_name(p.net)})
            cur_sig = fp_signature(old.x, old.y, _angle_deg(target.orientation),
                                   target.layer)
            if dry_run:
                return {"success": True, "dry_run": True, "reference": reference,
                        "old_mm": old_mm, "new_mm": [x_mm, y_mm], "nets": nets,
                        "sig": list(cur_sig), "wrote": False, "committed": False}

            # Compare-and-swap: never clobber a concurrent user edit. Baseline
            # is the agent's plan signature (expect_sig) or the last snapshot.
            kiid = _kiid(target)
            baseline = (tuple(expect_sig) if expect_sig is not None
                        else (_STATE["snapshot"].get(kiid) or {}).get("sig"))
            if cas_conflict(cur_sig, baseline, _STATE["pending"].get(kiid)):
                return {"success": False, "conflict": True, "who": "user",
                        "reference": reference, "wrote": False,
                        "baseline_sig": list(baseline),
                        "current_sig": list(cur_sig),
                        "error": (f"{reference} wurde seit deinem Plan im Editor "
                                  "geaendert (vermutlich vom User) — NICHT "
                                  "ueberschrieben. Mit live_get_state neu lesen "
                                  "und neu planen.")}

            def _write():
                target.position = Vector2.from_xy(new_x, new_y)
                committed = False
                try:
                    commit = board.begin_commit()
                    board.update_items([target])
                    board.push_commit(commit, f"agent: move {reference}")
                    committed = True
                except Exception:
                    board.update_items([target])  # fallback: still applies
                return committed

            committed = _retry(_write, f"move {reference}")
            # self-write masking: record intended new state so the next diff
            # attributes this to the agent, not the user.
            new_rec = make_record(
                "footprint",
                fp_signature(new_x, new_y, _angle_deg(target.orientation),
                             target.layer),
                x=new_x, y=new_y, layer=_layer_name(board, target.layer))
            _STATE["pending"][_kiid(target)] = new_rec
            if _kiid(target) in _STATE["snapshot"]:
                _STATE["snapshot"][_kiid(target)] = new_rec
            return {"success": True, "dry_run": False, "reference": reference,
                    "old_mm": old_mm, "new_mm": [x_mm, y_mm], "nets": nets,
                    "sig": list(new_rec["sig"]), "wrote": True,
                    "committed": committed}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"live_move_footprint: {exc}"}

    @mcp.tool()
    def live_session_status() -> dict[str, Any]:
        """Health + identity of the live IPC session.

        Use this first, before any other ``live_*`` tool, to confirm the IPC
        bus is reachable and the editor is on the expected board; a failure
        here explains why the others would error.

        Pings get_version(), reports connection, last pull time, snapshot
        size, the configured autosave interval (watch-then-pull cadence) and
        whether the live layer is read-only. Detects a board change vs the
        last snapshot (which invalidates the diff baseline).

        Returns:
            ``{success, connected, kicad_version, board_id, board_changed,
            snapshot_size, last_pull_age_s, persist_cadence_s, persist_source,
            poll_hint_s, read_only}``. persist_source is "autosave_interval",
            "local_history_debounce" (KiCad 10) or "default".
        """
        try:
            k = _kicad()
            ver = str(k.get_version())
            board = _board(k)
            ident = _board_identity(k, board)
            changed = (_STATE["board_id"] is not None
                       and ident != _STATE["board_id"])
            cadence, cadence_src = _persist_cadence()
            poll = cadence if cadence else _DEFAULT_POLL_S
            age = (time.time() - _STATE["last_pull"]) if _STATE["last_pull"] else None
            return {"success": True, "connected": True, "kicad_version": ver,
                    "board_id": ident, "board_changed": changed,
                    "snapshot_size": len(_STATE["snapshot"]),
                    "last_pull_age_s": round(age, 1) if age is not None else None,
                    "persist_cadence_s": cadence, "persist_source": cadence_src,
                    "poll_hint_s": poll, "read_only": _read_only()}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "connected": False,
                    "error": f"live_session_status: {exc}"}
