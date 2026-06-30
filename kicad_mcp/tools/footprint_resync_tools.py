# SPDX-License-Identifier: GPL-3.0-or-later
"""Footprint-resync MCP tools — the headless equivalent of KiCad's GUI F8
(update PCB from schematic), without the SWIG flip/rotation bugs.

Three tools, lowest-risk first:

* ``normalize_footprint_libid`` — prefix a bare footprint lib_id
  (``"NAME"`` → ``"Lib:NAME"``) from the schematic. Pure text-patch.
* ``refresh_pinfunctions`` — rewrite each copper pad's ``(pinfunction …)`` from
  the symbol's pin names. Pure text-patch (no geometry, no nets).
* ``replace_footprint_canonical`` — replace a footprint with its library
  version, flip/placement-correct, via the real pcbnew engine in a fresh
  subprocess. ``SaveBoard`` rewrites the whole file → backup + dry_run guarded.

The two text-patch tools never touch geometry or nets and are idempotent.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text, write_text
from kicad_mcp.tools.footprint_resync_worker import MARK, MARK_END
from kicad_mcp.utils.path_env import kicad_lib_root, to_local_path
from kicad_mcp.utils.sch_inspect import (
    _block,
    schematic_footprint_map,
    schematic_pin_names,
)

_WORKER = os.path.join(os.path.dirname(__file__), "footprint_resync_worker.py")

# Net token in either form: (net 7 "GND") or string-form (net "GND").
_NET_TOKEN_RE = re.compile(r'(\(net \d+ "[^"]*"\)|\(net "[^"]*"\))')


# --------------------------------------------------------------------------- #
# Tool 1 — normalize_footprint_libid (text-patch)
# --------------------------------------------------------------------------- #

def normalize_footprint_libid_impl(
    pcb_path: str, sch_path: str, refs: Optional[list[str]] = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    pcb_path = to_local_path(pcb_path)
    sch_path = to_local_path(sch_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if not os.path.isfile(sch_path):
        return {"success": False, "error": f"Schematic not found: {sch_path}"}

    pcb = get_text(pcb_path)
    want = schematic_footprint_map(get_text(sch_path))  # ref -> "Lib:Name"
    refset = set(refs) if refs else None

    edits: list[dict[str, str]] = []
    out: list[str] = []
    pos = 0
    for m in re.finditer(r'\(footprint ', pcb):
        st = m.start()
        en = _block(pcb, st)
        fb = pcb[st:en]
        rm = re.search(r'\(property "Reference" "([^"]+)"', fb)
        ref = rm.group(1) if rm else None
        fid_m = re.search(r'\(footprint "([^"]+)"', fb)
        cur = fid_m.group(1) if fid_m else ""
        new = want.get(ref) if ref else None
        # only prefix a BARE name whose schematic name is identical → can never
        # assign a different footprint; idempotent (already-qualified → skip)
        if (ref and (refset is None or ref in refset) and ":" not in cur
                and new and ":" in new and new.split(":")[-1] == cur):
            edits.append({"ref": ref, "from": cur, "to": new})
            fb = fb.replace('(footprint "%s"' % cur,
                            '(footprint "%s"' % new, 1)
        out.append(pcb[pos:st])
        out.append(fb)
        pos = en
    out.append(pcb[pos:])
    new_text = "".join(out)

    if not dry_run and edits:
        write_text(pcb_path, new_text)
    return {"success": True, "dry_run": dry_run, "normalized": edits,
            "count": len(edits)}


# --------------------------------------------------------------------------- #
# Tool 2 — refresh_pinfunctions (text-patch)
# --------------------------------------------------------------------------- #

def refresh_pinfunctions_impl(
    pcb_path: str, sch_path: str, refs: Optional[list[str]] = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    pcb_path = to_local_path(pcb_path)
    sch_path = to_local_path(sch_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if not os.path.isfile(sch_path):
        return {"success": False, "error": f"Schematic not found: {sch_path}"}

    pcb = get_text(pcb_path)
    pinmap = schematic_pin_names(get_text(sch_path))  # ref -> {nr: name}
    refset = set(refs) if refs else None

    changed: list[str] = []
    out: list[str] = []
    pos = 0
    for m in re.finditer(r'\(footprint ', pcb):
        st = m.start()
        en = _block(pcb, st)
        fb = pcb[st:en]
        rm = re.search(r'\(property "Reference" "([^"]+)"', fb)
        ref = rm.group(1) if rm else None
        pins = pinmap.get(ref) if ref else None
        if not (ref and pins and (refset is None or ref in refset)):
            out.append(pcb[pos:st])
            out.append(fb)
            pos = en
            continue
        fb = _patch_pads(fb, ref, pins, changed)
        out.append(pcb[pos:st])
        out.append(fb)
        pos = en
    out.append(pcb[pos:])
    new_text = "".join(out)

    if not dry_run and changed:
        write_text(pcb_path, new_text)
    return {"success": True, "dry_run": dry_run, "changed": changed,
            "count": len(changed)}


def _patch_pads(fb: str, ref: str, pins: dict[str, str],
                changed: list[str]) -> str:
    """Rewrite/insert ``(pinfunction …)`` on each copper pad of one footprint."""
    nb: list[str] = []
    p2 = 0
    for pm in re.finditer(r'\(pad "', fb):
        ps = pm.start()
        pe = _block(fb, ps)
        pad = fb[ps:pe]
        num_m = re.search(r'\(pad "([^"]*)"', pad)
        pn = num_m.group(1) if num_m else ""
        want = pins.get(pn)
        if want and re.search(r'\.Cu"', pad):
            if re.search(r'\(pinfunction "[^"]*"', pad):
                newpad = re.sub(r'\(pinfunction "[^"]*"',
                                '(pinfunction "%s"' % want, pad, 1)
            else:  # insert right after the (net …) token (KiCad ordering)
                newpad = _NET_TOKEN_RE.sub(
                    lambda mo: mo.group(0) + '\n\t\t\t(pinfunction "%s")' % want,
                    pad, 1)
            if newpad != pad:
                changed.append("%s.%s->%s" % (ref, pn, want))
                pad = newpad
        nb.append(fb[p2:ps])
        nb.append(pad)
        p2 = pe
    nb.append(fb[p2:])
    return "".join(nb)


# --------------------------------------------------------------------------- #
# Tool 3 — replace_footprint_canonical (pcbnew worker)
# --------------------------------------------------------------------------- #

def _expand_uri(uri: str, pcb_dir: str) -> str:
    """Expand KiCad ``${…}`` vars in an fp-lib-table URI to a local path."""
    def _sub(mo: "re.Match") -> str:
        var = mo.group(1)
        if var == "KIPRJMOD":
            return pcb_dir
        if "FOOTPRINT_DIR" in var:
            root = kicad_lib_root()
            return root or os.environ.get(var, mo.group(0))
        return os.environ.get(var, mo.group(0))
    return re.sub(r'\$\{([^}]+)\}', _sub, uri)


def _resolve_pretty_dir(nick: str, pcb_dir: str) -> Optional[str]:
    """Map a footprint-lib nick to its ``.pretty`` directory.

    Reads the project-local ``fp-lib-table`` first, then falls back to
    ``<kicad_lib_root>/footprints/<nick>.pretty``. Returns None if neither
    resolves to an existing directory (the worker then reports a clear error).
    """
    table = os.path.join(pcb_dir, "fp-lib-table")
    if os.path.isfile(table):
        try:
            txt = get_text(table)
        except Exception:
            txt = ""
        for lm in re.finditer(r'\(lib\b', txt):
            blk = txt[lm.start():_block(txt, lm.start())]
            name = re.search(r'\(name "([^"]+)"', blk)
            uri = re.search(r'\(uri "([^"]+)"', blk)
            if name and uri and name.group(1) == nick:
                path = _expand_uri(uri.group(1), pcb_dir)
                if os.path.isdir(path):
                    return path
    root = kicad_lib_root()
    if root:
        cand = os.path.join(root, nick + ".pretty")
        if os.path.isdir(cand):
            return cand
    return None


def replace_footprint_canonical_impl(
    pcb_path: str, sch_path: str, refs: list[str], dry_run: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    pcb_path = to_local_path(pcb_path)
    sch_path = to_local_path(sch_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if not os.path.isfile(sch_path):
        return {"success": False, "error": f"Schematic not found: {sch_path}"}
    if not refs:
        return {"success": False, "error": "refs must be a non-empty list"}

    # An open board would collide with the worker's SaveBoard (full rewrite).
    from kicad_mcp.utils.board_open_guard import BoardOpenError, guard_pcb_disk_write
    if not dry_run:
        try:
            guard_pcb_disk_write(pcb_path)
        except BoardOpenError as exc:
            return {"success": False, "error": str(exc)}

    pcb_dir = os.path.dirname(pcb_path)
    want = schematic_footprint_map(get_text(sch_path))
    jobs: list[dict[str, str]] = []
    unresolved: list[str] = []
    for ref in refs:
        fid = want.get(ref)
        if not fid or ":" not in fid:
            unresolved.append(ref)
            continue
        nick, name = fid.split(":", 1)
        pretty = _resolve_pretty_dir(nick, pcb_dir)
        if pretty is None:
            unresolved.append(ref)
            continue
        jobs.append({"ref": ref, "lib_nick": nick, "fp_name": name,
                     "pretty_dir": pretty})

    if not jobs:
        return {"success": True, "done": [], "errors": [],
                "unresolved": unresolved, "saved": False, "dry_run": dry_run,
                "note": "Keine auflösbaren refs."}

    payload = {"pcb_path": pcb_path, "jobs": jobs, "dry_run": dry_run,
               "force": force}
    try:
        proc = subprocess.run(
            [sys.executable, _WORKER], input=json.dumps(payload),
            capture_output=True, text=True, timeout=300, check=False,
        )
    except Exception as exc:
        return {"success": False, "error": f"worker launch failed: {exc}"}
    m = re.search(re.escape(MARK) + r'(.*?)' + re.escape(MARK_END),
                  proc.stdout, re.S)
    if not m:
        return {"success": False, "error": "worker produced no result",
                "stderr": (proc.stderr or "")[-2000:],
                "stdout": (proc.stdout or "")[-500:]}
    result = json.loads(m.group(1))
    result.update({"success": True, "dry_run": dry_run,
                   "unresolved": unresolved})
    return result


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def register_footprint_resync_tools(mcp: FastMCP) -> None:
    """Register the footprint-resync tools on the MCP server."""

    @mcp.tool()
    def normalize_footprint_libid(
        pcb_path: str, schematic_path: str, refs: list[str] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Prefix a bare footprint lib_id in the PCB from the schematic.

        KiCad sometimes leaves a PCB footprint header as a bare name
        (``(footprint "DRV8313" …)``) instead of the qualified lib_id
        (``"iFloat_Custom:DRV8313"``). This restores the namespace by reading
        each symbol's Footprint property from the schematic. Pure surgical
        TEXT-patch — no geometry, no nets, no full rewrite.

        Safe by construction: a footprint is only touched when its schematic
        footprint NAME equals the bare PCB name (so it can never point at a
        different footprint), and an already-qualified lib_id is skipped
        (idempotent). Use this when KiCad reports "footprint … not found" due
        to a missing library namespace, before any geometry work.

        Args:
            pcb_path: Path to the ``.kicad_pcb``.
            schematic_path: Path to the matching ``.kicad_sch``.
            refs: Optional list of references to limit to; default all.
            dry_run: If True (default), only report what WOULD change.

        Returns:
            ``{success, dry_run, normalized: [{ref, from, to}], count}``; on
            error ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        schematic_path = to_local_path(schematic_path)
        return normalize_footprint_libid_impl(
            pcb_path, schematic_path, refs or None, dry_run)

    @mcp.tool()
    def refresh_pinfunctions(
        pcb_path: str, schematic_path: str, refs: list[str] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Refresh each copper pad's pinfunction from the schematic pin names.

        After a symbol's pin names change, the PCB pads keep a stale
        ``(pinfunction …)`` (the label shown in the ratsnest / DRC). This
        rewrites or inserts the correct pinfunction per pad from the symbol's
        real pin names. Pure surgical TEXT-patch — it never touches pad
        geometry or net assignment, so it cannot affect routing.

        Net-token form agnostic (handles both ``(net N "x")`` and string-form
        ``(net "x")``); pads without a net (paste-only) are skipped. Use this
        when pin labels in KiCad look outdated after a symbol edit.

        Args:
            pcb_path: Path to the ``.kicad_pcb``.
            schematic_path: Path to the matching ``.kicad_sch``.
            refs: Optional list of references to limit to; default all.
            dry_run: If True (default), only report what WOULD change.

        Returns:
            ``{success, dry_run, changed: ["REF.PAD->NAME"], count}``; on error
            ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        schematic_path = to_local_path(schematic_path)
        return refresh_pinfunctions_impl(
            pcb_path, schematic_path, refs or None, dry_run)

    @mcp.tool()
    def replace_footprint_canonical(
        pcb_path: str, schematic_path: str, refs: list[str],
        dry_run: bool = True, force: bool = False,
    ) -> dict[str, Any]:
        """Replace footprints with their library version, flip/placement-correct.

        The headless equivalent of GUI-F8 footprint update for chosen
        references: loads the canonical footprint from its library and swaps it
        in using the REAL pcbnew engine (correct flip + absolute orientation +
        pad geometry), carrying over position/lock/net/pinfunction. Runs in a
        FRESH subprocess (SWIG pcbnew degrades in a long-lived process).

        Built-in correctness gate: every pad shared with the original must stay
        within 1 µm — otherwise that ref is skipped and reported as "pad drift"
        (a wrong flip/orientation order would move pads), so the tool can never
        silently warp geometry. ``SaveBoard`` rewrites the WHOLE file, so make
        a backup; default ``dry_run`` only reports. Refuses to run on a board
        currently open in KiCad. Use only when a footprint genuinely needs the
        library geometry restored — for a bare lib_id use
        ``normalize_footprint_libid`` instead.

        Args:
            pcb_path: Path to the ``.kicad_pcb``.
            schematic_path: Path to the matching ``.kicad_sch`` (lib_id source).
            refs: References to replace (required — no board-wide default).
            dry_run: If True (default), build + verify but do not SaveBoard.
            force: If True, BYPASS the pad-drift gate. Required for an
                intentional footprint SWAP (e.g. SOIC-16 -> TSSOP-16, or
                tantalum -> MLCC) where the pads are MEANT to move. The
                pcbnew placement (Flip + absolute orientation) stays correct;
                forcing only skips the same-geometry safety check. Leave
                False to restore canonical geometry of the SAME footprint.

        Returns:
            ``{success, dry_run, done: [refs], errors: [{ref, error, …}],
            unresolved: [refs], saved}``; on error ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        schematic_path = to_local_path(schematic_path)
        return replace_footprint_canonical_impl(
            pcb_path, schematic_path, refs, dry_run, force)
