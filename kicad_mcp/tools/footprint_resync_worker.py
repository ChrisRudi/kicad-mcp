# SPDX-License-Identifier: GPL-3.0-or-later
"""Warm-less pcbnew worker for ``replace_footprint_canonical``.

Replaces footprints with their library version using the REAL pcbnew engine
(so flip/orientation/pad geometry are correct — the GUI-F8 equivalent), in a
FRESH subprocess per call because SWIG ``pcbnew`` degrades in a long-lived
interpreter. Imports NOTHING from ``kicad_mcp`` / ``mcp`` (that would drag in
the whole server). Run by FILE PATH, not ``-m``.

Protocol: a JSON payload on stdin; a single ``MARK<json>MARK`` line on stdout.
``SaveBoard`` only when ``dry_run`` is false AND at least one ref swapped.

Built-in correctness gate: after building the replacement footprint, every
pad shared with the original must sit within 1 µm of the original pad — else
the ref is skipped (a wrong flip/orientation order would drift pads). The
tool therefore can never silently warp the geometry.
"""

from __future__ import annotations

import json
import sys

MARK = "<<<FPR_JSON>>>"
MARK_END = "<<<FPR_END>>>"

_DRIFT_LIMIT_NM = 1000  # 1 µm


def _replace_one(board, pcbnew, job: dict) -> dict:
    """Replace one footprint; returns ``{ref, status, ...}`` (never raises)."""
    ref = job["ref"]
    try:
        old = board.FindFootprintByReference(ref)
        if old is None:
            return {"ref": ref, "status": "error", "error": "not found"}
        new = pcbnew.FootprintLoad(job["pretty_dir"], job["fp_name"])
        if new is None:
            return {"ref": ref, "status": "error",
                    "error": f"FootprintLoad failed: {job['pretty_dir']} / "
                             f"{job['fp_name']}"}
        new.SetParent(board)
        new.SetReference(old.GetReference())
        new.SetValue(old.GetValue())
        new.SetPath(old.GetPath())
        new.SetLocked(old.IsLocked())
        new.SetPosition(old.GetPosition())
        if old.IsFlipped():
            new.Flip(old.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
        new.SetOrientation(old.GetOrientation())   # ABSOLUTE, AFTER the flip
        new.SetFPID(pcbnew.LIB_ID(job["lib_nick"], job["fp_name"]))
        new.FixUpPadsForBoard(board)

        oldpads = {p.GetNumber(): p for p in old.Pads()}
        for npad in new.Pads():
            opad = oldpads.get(npad.GetNumber())
            if opad is not None:
                npad.SetNet(opad.GetNet())
                npad.SetPinFunction(opad.GetPinFunction())
                npad.SetPinType(opad.GetPinType())

        # Correctness gate BEFORE committing: shared pads must not drift.
        drift = []
        for npad in new.Pads():
            opad = oldpads.get(npad.GetNumber())
            if opad is None:
                continue
            dx = abs(npad.GetPosition().x - opad.GetPosition().x)
            dy = abs(npad.GetPosition().y - opad.GetPosition().y)
            if max(dx, dy) > _DRIFT_LIMIT_NM:
                drift.append({"pad": npad.GetNumber(),
                              "dx_nm": dx, "dy_nm": dy})
        if drift:
            return {"ref": ref, "status": "error", "error": "pad drift",
                    "drift": drift}

        board.Remove(old)
        board.Add(new)
        return {"ref": ref, "status": "ok"}
    except Exception as exc:  # noqa: BLE001 - reported per-ref, never raises
        return {"ref": ref, "status": "error", "error": str(exc)}


def run(payload: dict) -> dict:
    """Process all jobs; SaveBoard only on a real (non-dry) run with swaps."""
    import pcbnew  # pylint: disable=import-error  # KiCad-bundled only

    pcb_path = payload["pcb_path"]
    dry_run = bool(payload.get("dry_run", True))
    board = pcbnew.LoadBoard(pcb_path)

    done, errors = [], []
    for job in payload.get("jobs", []):
        res = _replace_one(board, pcbnew, job)
        if res["status"] == "ok":
            done.append(res["ref"])
        else:
            errors.append(res)

    saved = bool(done) and not dry_run
    if saved:
        pcbnew.SaveBoard(pcb_path, board)
    return {"done": done, "errors": errors, "saved": saved}


def main() -> None:
    payload = json.loads(sys.stdin.read())
    # pcbnew (LoadBoard/SaveBoard) may print to stdout — keep the JSON-RPC-like
    # MARK channel clean by routing that chatter to stderr during the run.
    real_out = sys.stdout
    sys.stdout = sys.stderr
    try:
        result = run(payload)
    finally:
        sys.stdout = real_out
    sys.stdout.write(MARK + json.dumps(result) + MARK_END + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
