# SPDX-License-Identifier: GPL-3.0-or-later
"""
``via_promote`` — universal blind/buried → through-via optimiser.

Blind/buried vias need an advanced (and expensive) board process; plain
through vias are JLC-standard. This tool finds every blind/buried via
that could become a through F.Cu↔B.Cu via *without* creating a clearance
violation, and (optionally) rewrites it.

Two stages, cleanly split:
  * **Analysis** runs against a warm ``pcbnew`` daemon (``via_promote_
    worker.py``, board cached by path+mtime) that fills zones first, then
    tests each candidate's pad circle against other-net copper on the
    layers a through via would newly occupy. Read-only, so the cached board
    is reused across calls (the first load+fill is paid once).
  * **Apply** is a surgical *text-patch*: only the ``(layers …)`` line of
    each promotable via is rewritten to ``"F.Cu" "B.Cu"`` (no pcbnew
    ``SaveBoard`` that would reformat the whole file).

Report mode (``dry_run=True``, default) answers "where can I free up a
through via?"; the remaining blind/buried count is the manufacturing-tier
indicator.
"""
import importlib.util
import os
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools._warm_daemon import WarmDaemon
from kicad_mcp.tools.via_promote_worker import (
    MARK as _MARK,
    run as _run_in_process,  # cold in-process path, re-exported for unit tests
)
from kicad_mcp.cache import get_text, put_text
from kicad_mcp.utils.path_env import to_local_path

_HAS_PCBNEW = importlib.util.find_spec("pcbnew") is not None
_WORKER = os.path.join(os.path.dirname(__file__), "via_promote_worker.py")
# First cold load + fill on a dense, fully-poured board can be slow; warm
# cache hits return fast. Keep a generous ceiling for the cold case.
_WORKER_TIMEOUT_S = 240
_DAEMON = WarmDaemon(_WORKER, _MARK, max_loads=25)

__all__ = ["via_promote_impl", "via_retype_impl", "via_resize_impl",
           "register_via_promote_tools", "_promote_layers_text",
           "_retype_via_text", "_resize_via_text", "_run_in_process"]


# ---------------------------------------------------------------------------
# Apply: surgical text-patch of via (layers …) lines by UUID
# ---------------------------------------------------------------------------


def _block_end(text: str, start: int) -> int:
    """Index just past the ``)`` that closes the s-expr opened at
    ``start`` (which must point at the ``(``)."""
    depth, i = 0, start
    n = len(text)
    while i < n:
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


_LAYERS_PAIR_RE = re.compile(r'\(layers\s+"[^"]+"\s+"[^"]+"\)')
_UUID_RE = re.compile(r'\(uuid\s+"([^"]+)"\)')


def _promote_layers_text(pcb_text: str, uuids) -> tuple[str, int]:
    """Rewrite the ``(layers …)`` of every ``(via …)`` whose uuid is in
    ``uuids`` to ``"F.Cu" "B.Cu"``. Returns ``(new_text, n_changed)``.

    Pure text transform — walks each top-level ``(via`` block, matches by
    uuid, swaps only its two-layer pair. Vias not in the set are left
    untouched; a uuid whose via is already F/B (or absent) is a no-op.
    """
    want = set(uuids)
    if not want:
        return pcb_text, 0
    out = []
    pos = 0
    changed = 0
    while True:
        idx = pcb_text.find("(via", pos)
        if idx < 0:
            out.append(pcb_text[pos:])
            break
        end = _block_end(pcb_text, idx)
        block = pcb_text[idx:end]
        m = _UUID_RE.search(block)
        out.append(pcb_text[pos:idx])
        if m and m.group(1) in want:
            new_block, n = _LAYERS_PAIR_RE.subn(
                '(layers "F.Cu" "B.Cu")', block, count=1)
            if n:
                # Also strip the blind/buried/micro TYPE TOKEN. KiCad treats the
                # token as authoritative over (layers) (CLAUDE.md §6), so without
                # this the via stays blind/buried at fab despite the F/B rewrite —
                # i.e. the promotion silently does nothing. _VIA_TYPE_RE turns
                # "(via blind " → "(via " (no-op on an already-through via).
                new_block = _VIA_TYPE_RE.sub(r"(via\2", new_block, count=1)
                changed += 1
            out.append(new_block)
        else:
            out.append(block)
        pos = end
    return "".join(out), changed


_VIA_TYPE_RE = re.compile(r'\(via((?:\s+(?:blind|buried|micro))?)(\s)')
_VALID_VIA_TYPES = ("through", "blind", "buried", "micro")


def _retype_via_text(pcb_text: str, uuids, new_type: str) -> tuple[str, int]:
    """Rewrite the via-*type* token of every ``(via …)`` whose uuid is in
    ``uuids`` to ``new_type``. Returns ``(new_text, n_changed)``.

    ``"through"`` drops the token; ``"blind"``/``"buried"``/``"micro"`` set
    it. Same surgical, uuid-targeted walk as :func:`_promote_layers_text`:
    only the type word right after ``(via`` is touched — layers / size /
    drill / net and every other via stay byte-for-byte intact. Use this to
    correct a mis-tagged span class (e.g. a 0.2 mm via tagged ``micro`` that
    is really a mechanically-drilled ``blind``, dropping the HDI laser tier).
    """
    want = set(uuids)
    if not want or new_type not in _VALID_VIA_TYPES:
        return pcb_text, 0
    token = "" if new_type == "through" else f" {new_type}"
    out = []
    pos = 0
    changed = 0
    while True:
        idx = pcb_text.find("(via", pos)
        if idx < 0:
            out.append(pcb_text[pos:])
            break
        end = _block_end(pcb_text, idx)
        block = pcb_text[idx:end]
        m = _UUID_RE.search(block)
        out.append(pcb_text[pos:idx])
        if m and m.group(1) in want:
            new_block, n = _VIA_TYPE_RE.subn(
                lambda mm: f"(via{token}{mm.group(2)}", block, count=1)
            if n and new_block != block:
                changed += 1
            out.append(new_block)
        else:
            out.append(block)
        pos = end
    return "".join(out), changed


def via_retype_impl(pcb_path: str, uuids, new_type: str = "blind",
                    dry_run: bool = True) -> dict[str, Any]:
    """Apply (or preview) a uuid-targeted via-type retype. Pure text — needs
    no pcbnew."""
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if new_type not in _VALID_VIA_TYPES:
        return {"success": False,
                "error": f"new_type must be one of {_VALID_VIA_TYPES}"}
    text = get_text(pcb_path)
    new_text, n = _retype_via_text(text, uuids, new_type)
    result = {"success": True, "requested": len(set(uuids)),
              "new_type": new_type, "changed": n, "dry_run": dry_run,
              "wrote": False}
    if not dry_run and n:
        with open(pcb_path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        put_text(pcb_path, new_text)
        result["wrote"] = True
    return result


_SIZE_RE = re.compile(r'\(size\s+[\d.]+\)')
_DRILL_RE = re.compile(r'\(drill\s+[\d.]+\)')


def _resize_via_text(pcb_text: str, size, drill=None, uuids=None) -> tuple[str, int]:
    """Set the ``(size …)`` (and optionally ``(drill …)``) of vias.

    ``uuids=None`` ⇒ every via (board-wide standardisation); otherwise only
    the listed uuids. Returns ``(new_text, n_changed)``. Same surgical via-
    block walk as the other patchers — only size/drill tokens are touched,
    layers/type/net/position stay intact. A via already at the target is a
    no-op (not counted).
    """
    want = set(uuids) if uuids else None
    out = []
    pos = 0
    changed = 0
    while True:
        idx = pcb_text.find("(via", pos)
        if idx < 0:
            out.append(pcb_text[pos:])
            break
        end = _block_end(pcb_text, idx)
        block = pcb_text[idx:end]
        m = _UUID_RE.search(block)
        out.append(pcb_text[pos:idx])
        if (want is None) or (m and m.group(1) in want):
            nb = _SIZE_RE.sub(f'(size {size})', block, count=1)
            if drill is not None:
                nb = _DRILL_RE.sub(f'(drill {drill})', nb, count=1)
            if nb != block:
                changed += 1
            out.append(nb)
        else:
            out.append(block)
        pos = end
    return "".join(out), changed


def via_resize_impl(pcb_path: str, size: float, drill=None, uuids=None,
                    dry_run: bool = True) -> dict[str, Any]:
    """Apply (or preview) a via size/drill standardisation. Pure text."""
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    text = get_text(pcb_path)
    new_text, n = _resize_via_text(text, size, drill, uuids)
    result = {"success": True, "size": size, "drill": drill,
              "scope": "all" if not uuids else len(set(uuids)),
              "changed": n, "dry_run": dry_run, "wrote": False}
    if not dry_run and n:
        with open(pcb_path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        put_text(pcb_path, new_text)
        result["wrote"] = True
    return result


# ---------------------------------------------------------------------------
# Analysis spawn
# ---------------------------------------------------------------------------


def _analyse(pcb_path: str, clearance_mm: float) -> dict[str, Any]:
    """Run the read-only via analysis against the warm daemon (loads + fills
    once, then reuses the in-memory board on the unchanged file)."""
    resp = _DAEMON.request(
        {"op": "analyse", "pcb_path": pcb_path, "clearance_mm": clearance_mm},
        timeout=_WORKER_TIMEOUT_S,
    )
    if not resp.get("ok"):
        out = {"success": False, "error": resp.get("error", "via_promote worker failed")}
        if "traceback" in resp:
            out["traceback"] = resp["traceback"]
        return out
    resp.pop("ok", None)
    resp.pop("id", None)
    return {"success": True, **resp}


def via_promote_impl(pcb_path: str, clearance_mm: float = 0.2,
                     dry_run: bool = True, pofv_ok: bool = True) -> dict[str, Any]:
    """Analyse (and optionally apply) blind/buried→through promotion."""
    if not _HAS_PCBNEW:
        return {"success": False,
                "error": "pcbnew not importable — run under KiCad's bundled Python."}
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}

    report = _analyse(pcb_path, clearance_mm)
    if not report.get("success"):
        return report

    report["dry_run"] = dry_run
    report["pofv_ok"] = pofv_ok
    report["applied"] = 0
    report["wrote"] = False
    if not dry_run:
        uuids = [v["uuid"] for v in report.get("promotable", [])]
        if pofv_ok:
            uuids += [v["uuid"] for v in report.get("needs_pofv", [])]
        if uuids:
            text = get_text(pcb_path)
            new_text, n = _promote_layers_text(text, uuids)
            if n:
                with open(pcb_path, "w", encoding="utf-8") as fh:
                    fh.write(new_text)
                put_text(pcb_path, new_text)
                report["wrote"] = True
            report["applied"] = n
    return report


def register_via_promote_tools(mcp: FastMCP) -> None:
    """Register the ``via_promote`` tool with the MCP server."""

    @mcp.tool()
    def via_promote(pcb_path: str, clearance_mm: float = 0.2,
                    dry_run: bool = True, pofv_ok: bool = True) -> dict[str, Any]:
        """Promote blind/buried vias to plain through vias where it's safe.

        Universal board-wide pass (works on ANY via, regardless of how it
        was created). For every blind/buried via it asks: would making this
        a through ``F.Cu↔B.Cu`` via collide with copper it would newly
        touch? Zones are filled first so the check sees real pour copper.
        Through vias are JLC-standard and cheaper than blind/buried, so
        promoting as many as possible lowers the board's manufacturing
        tier; the vias that must stay blind/buried are reported with what
        blocks them.

        Three outcomes per candidate (not just go / no-go):
          * **promotable** — becomes a clean through via, no conflict.
          * **needs_pofv** — would become through but lands inside an
            *own-net SMD pad* (a bare through via there wicks solder); it
            is still promotable **only as a filled+capped via-in-pad
            (POFV)** — free at JLC on 6–20 layers. The pad(s) it sits in
            are listed in ``in_pads``.
          * **blocked** — a through via here shorts other-net copper
            (``blocked_on`` = tracks/vias/zones, ``pad_shorts`` = other-net
            pads on F/B) — must stay blind/buried.

        Pad overlap is tested on **both** outer layers (F.Cu and B.Cu)
        regardless of the via's current span, so a pad on a layer the blind
        via already occupied is not missed.

        Use this as the final pass after polar routing (which lays buried
        In1↔In2 vias to keep the outer layers clear), or any time you want
        to know how many advanced vias a layout truly needs.

        Args:
            pcb_path: Path to the ``.kicad_pcb`` (WSL or Windows form).
            clearance_mm: Copper-to-copper clearance for the safety check
                (default 0.2 mm, JLC 4-layer standard).
            dry_run: If True (default), only report — nothing is written.
                If False, rewrite each promoted via's ``(layers …)`` to
                ``F.Cu B.Cu`` (a surgical text-patch; one write).
            pofv_ok: If True (default), the apply step also promotes the
                ``needs_pofv`` vias (you accept they become filled
                via-in-pad — free at JLC). If False, they are left
                blind/buried and only the clean ``promotable`` set is
                applied.

        Returns:
            ``{success, total_vias, already_through, promotable_count,
            needs_pofv_count, blocked_count,
            promotable:[{uuid,x_mm,y_mm,net,adds_layers}],
            needs_pofv:[{…,in_pads:[{layer,pad}]}],
            blocked:[{uuid,…,blocked_on:[…],pad_shorts:[…]}],
            tier_before/tier_after_promotable/tier_after_with_pofv:
            {spans:{"F.Cu/In2.Cu":n,…}, blind_buried_types, blind_buried_vias},
            zones_filled, dry_run, pofv_ok, applied, wrote}``. On failure
            ``{success: False, error}``. Analysis is read-only; only the
            apply path (``dry_run=False``) writes, and only via layer lines.

        Idempotency: a second run with the same args after an apply is a
        no-op (already-through vias are skipped); the report is stable for
        an unchanged board.
        """
        pcb_path = to_local_path(pcb_path)
        return via_promote_impl(pcb_path, clearance_mm, dry_run, pofv_ok)

    @mcp.tool()
    def via_retype(pcb_path: str, uuids: list[str], new_type: str = "blind",
                   dry_run: bool = True) -> dict[str, Any]:
        """Change the via-*type* token of specific vias by UUID.

        Companion to ``via_promote`` (which changes a via's *span/layers*).
        ``via_retype`` corrects the span-*class* word right after ``(via`` —
        ``through`` / ``blind`` / ``buried`` / ``micro`` — without touching
        layers, size, drill, net or any other via. Surgical, uuid-targeted
        text-patch (same mechanism as the promote apply); the rest of the
        file is byte-for-byte unchanged.

        Primary use: drop a needless manufacturing tier. A via mechanically
        drillable (drill ≳0.15 mm) but tagged ``micro`` forces an HDI/laser
        process; retyping it to ``blind`` keeps the same span but removes the
        laser tier — no routing change.

        Args:
            pcb_path: ``.kicad_pcb`` (WSL or Windows form).
            uuids: via UUIDs to retype (get them from the board / a parse).
            new_type: ``"blind"`` (default), ``"buried"``, ``"micro"`` or
                ``"through"`` (drops the token).
            dry_run: if True (default), report how many would change without
                writing.

        Returns:
            ``{success, requested, new_type, changed, dry_run, wrote}`` or
            ``{success: False, error}``. Idempotent: re-running after an apply
            changes 0 (the token already matches).
        """
        pcb_path = to_local_path(pcb_path)
        return via_retype_impl(pcb_path, uuids, new_type, dry_run)

    @mcp.tool()
    def via_resize(pcb_path: str, size: float = 0.4, drill: float = 0.2,
                   uuids: list[str] | None = None,
                   dry_run: bool = True) -> dict[str, Any]:
        """Standardise via *size* (and drill) — board-wide or by UUID.

        Surgical text-patch of each via's ``(size …)`` and ``(drill …)``
        tokens; layers / type / net / position untouched. With ``uuids=None``
        (default) it sets **every** via on the board to one size — the way to
        collapse a mix of 0.45 / 0.6 mm vias to a single 0.4 mm standard
        (one drill tool, more copper clearance). Pass ``uuids`` to resize only
        specific vias.

        Note the trade-off: smaller pad = less annular ring (0.4/0.2 ⇒ 0.10 mm).
        Run a DRC after to confirm no annular/manufacturing rule is tripped.

        Args:
            pcb_path: ``.kicad_pcb`` (WSL or Windows form).
            size: via pad diameter mm (default 0.4).
            drill: via drill diameter mm (default 0.2); pass the same for all.
            uuids: specific vias, or None for all vias.
            dry_run: if True (default), report how many would change.

        Returns:
            ``{success, size, drill, scope, changed, dry_run, wrote}``.
            Idempotent — vias already at the target size/drill aren't counted.
        """
        pcb_path = to_local_path(pcb_path)
        return via_resize_impl(pcb_path, size, drill, uuids, dry_run)
