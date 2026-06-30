# SPDX-License-Identifier: GPL-3.0-or-later
"""Layer R — Review tools.

Three MCP tools that prepare structured material for a downstream LLM
that performs the actual schematic / datasheet review. The tools do
*not* judge — they assemble data and images so the reviewing model can
work pin-by-pin.

* ``review_ic_against_datasheet`` (Phase 1) — per-IC review payload:
  symbol info + per-pin connectivity + cropped schematic region PNG +
  rasterised datasheet page PNG + filtered local BOM.
* ``review_system_interconnect`` (Phase 2) — system-wide audit data:
  power tree, decoupling-cap distribution, pull-up/down audit, bus peers.
* ``list_missing_datasheets`` (Phase 0 helper) — read-only inventory of
  unique IC ``Value``s in the project that have no ``<project>/docs/<value>.pdf``
  on disk yet; the orchestrating LLM uses this to ask the user up-front
  which datasheets to fetch.

Outputs are written to ``<project_dir>/review/<REF>/`` (per-IC) and
``<project_dir>/review/system/`` (system). Re-running with the same inputs
overwrites the same files (idempotent).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any

from fastmcp import Context, FastMCP

from kicad_mcp.generators.review._brief import (
    render_brief_md,
    render_system_brief_md,
)
from kicad_mcp.generators.review._pdf_raster import rasterize_pdf_page
from kicad_mcp.generators.review._pin_check import compare_symbol_pins_to_footprint
from kicad_mcp.generators.review._svg_crop import render_region_to_png
from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.netlist_parser import extract_netlist
from kicad_mcp.utils.path_env import (
    _wsl_to_windows,  # pylint: disable=protected-access
    from_local_to_other,
    to_local_path,
)

logger = logging.getLogger(__name__)

# Reference-prefix classifier — used by the system-level audit
_POWER_PREFIXES = (
    "VCC", "VDD", "AVDD", "DVDD", "VBAT", "VBUS", "VIN",
    "+3V3", "+5V", "+12V", "+1V8", "+2V5",
)
_GROUND_PREFIXES = ("GND", "AGND", "DGND", "PGND", "VSS", "VEE")
_BUS_PATTERNS = {
    "I2C": re.compile(r"(?:^|[_\W])(SDA|SCL|I2C)(?:[_\W\d]|$)", re.IGNORECASE),
    "SPI": re.compile(r"(?:^|[_\W])(MOSI|MISO|SCK|SCLK|CSn?|SS|nSS)(?:[_\W\d]|$)", re.IGNORECASE),
    "UART": re.compile(r"(?:^|[_\W])(TX|RX|UART|RTS|CTS)(?:[_\W\d]|$)", re.IGNORECASE),
    "USB": re.compile(
        r"(?:^|[_\W])(USB(?:_)?(?:DP|DM|D\+|D-)?|D\+|D-)(?:[_\W\d]|$)",
        re.IGNORECASE,
    ),
    "RESET": re.compile(r"(?:^|[_\W])(RST|RESET|nRST|MR)(?:[_\W\d]|$)", re.IGNORECASE),
    "BOOT": re.compile(r"(?:^|[_\W])(BOOT|BOOT0)(?:[_\W\d]|$)", re.IGNORECASE),
}


# ---------------------------------------------------------------------------
# Helpers (no MCP-tool decoration)
# ---------------------------------------------------------------------------


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_local_pdf(maybe_path: str) -> bool:
    return bool(
        maybe_path
        and not maybe_path.lower().startswith(("http://", "https://"))
        and os.path.isfile(to_local_path(maybe_path))
    )


def _load_schematic_components(schematic_path: str) -> list[dict[str, Any]]:
    """Return the list ``list_schematic_components`` would return — without
    going through the MCP layer (avoids async / FastMCP coupling)."""
    from kicad_mcp.tools.schematic_tools import _extract_components, _parse_schematic

    tree = _parse_schematic(schematic_path)
    return _extract_components(tree)


def _index_components(components: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a ``ref -> component dict`` index."""
    return {c["reference"]: c for c in components if c.get("reference")}


def _resolve_datasheet_path(
    datasheet_pdf: str,
    project_dir: str,
    ic_value: str,
    properties: dict[str, str],
) -> tuple[str, str]:
    """Return ``(path, source)`` where source ∈
    ``{"parameter", "convention", "property", "missing"}``.
    """
    if datasheet_pdf:
        local = to_local_path(datasheet_pdf)
        if os.path.isfile(local):
            return local, "parameter"

    if ic_value:
        candidate = os.path.join(project_dir, "docs", f"{ic_value}.pdf")
        if os.path.isfile(candidate):
            return candidate, "convention"

    ds = (properties or {}).get("Datasheet", "") or ""
    if _is_local_pdf(ds):
        return to_local_path(ds), "property"

    return "", "missing"


def _filter_pins_by_range(pins: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    if start <= 0 and end <= 0:
        return pins
    lo = start if start > 0 else 1
    hi = end if end > 0 else 10**9
    out = []
    for p in pins:
        try:
            n = int(str(p.get("pin", "")).strip())
        except (TypeError, ValueError):
            continue
        if lo <= n <= hi:
            out.append(p)
    return out


def _build_pin_rows(
    component_info: dict[str, Any],
    netlist_data: dict[str, Any],
    component_index: dict[str, dict[str, Any]],
    ic_ref: str,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Return ``(pins, connected_refs)`` for the IC.

    Each pin row carries: ``pin``, ``name``, ``type``, ``net``,
    ``connected: [{ref, value, footprint, datasheet}]``.
    """
    nets = netlist_data.get("nets", {}) or {}
    # Build pin -> (net, peers, pinfunction, pintype) maps.
    # extract_schematic_netlist emits nodes as
    # ``{"ref": REF, "pin": N, "pinfunction": "FB_4", "pintype": "input"}``
    # — historical alternative key is ``component`` (older parsers).
    # Read both for forward/back compatibility.
    def _node_ref(p: dict[str, Any]) -> str:
        return p.get("ref") or p.get("component") or ""

    pin_to_net: dict[str, str] = {}
    pin_to_peers: dict[str, list[dict[str, Any]]] = {}
    pin_to_function: dict[str, str] = {}
    pin_to_type: dict[str, str] = {}
    for net_name, pin_list in nets.items():
        ic_pins_here = [p for p in pin_list if _node_ref(p) == ic_ref]
        if not ic_pins_here:
            continue
        peers = [
            {"ref": _node_ref(p), "pin": p.get("pin")}
            for p in pin_list
            if _node_ref(p) and _node_ref(p) != ic_ref
        ]
        for ip in ic_pins_here:
            pin_num = str(ip.get("pin", ""))
            pin_to_net[pin_num] = net_name
            pin_to_peers[pin_num] = peers
            if ip.get("pinfunction"):
                pin_to_function[pin_num] = str(ip["pinfunction"])
            if ip.get("pintype"):
                pin_to_type[pin_num] = str(ip["pintype"])

    # Symbol pins (number/name/type) — authoritative for the pin order.
    # Fallback for ``(extends "PARENT")``-based symbols where the local
    # lib block only carries the pin-number as ``type`` (KiCad emits
    # the pin as ``(pin "<number>" ...)`` without resolving the parent's
    # name/type). When ``number`` is empty but ``type`` looks numeric,
    # treat ``type`` as the pin number — matches what
    # ``get_symbol_details`` reports for stock extends symbols
    # (e.g. ``TPS54202DDC extends TPS54302``,
    # ``CSD18540Q5B extends Q_NMOS_SSSGD_AvalancheRated``).
    sym_pins: list[dict[str, Any]] = component_info.get("pins", []) or []
    rows: list[dict[str, Any]] = []
    connected_refs: set[str] = set()
    for sp in sym_pins:
        num = str(sp.get("number") or sp.get("num") or "")
        sp_type = str(sp.get("type") or "")
        if not num and sp_type.isdigit():
            num = sp_type
            sp_type = ""  # was the pin number masquerading as type
        net = pin_to_net.get(num, "")
        peers_raw = pin_to_peers.get(num, [])
        connected: list[dict[str, Any]] = []
        for peer in peers_raw:
            ref = peer.get("ref") or ""
            comp = component_index.get(ref, {})
            connected.append(
                {
                    "ref": ref,
                    "pin": peer.get("pin"),
                    "value": comp.get("value", ""),
                    "footprint": comp.get("footprint", ""),
                    "datasheet": (comp.get("properties") or {}).get("Datasheet", ""),
                }
            )
            if ref:
                connected_refs.add(ref)
        # Fill missing name/type from netlist pinfunction/pintype when
        # the lib_symbol (extends-only block) carries no own pin metadata.
        # pinfunction is "<NAME>_<NUMBER>" — strip the trailing _N to
        # recover the bare pin name.
        sp_name = str(sp.get("name") or "")
        if not sp_name and num in pin_to_function:
            fn = pin_to_function[num]
            if fn.endswith(f"_{num}"):
                sp_name = fn[: -(len(num) + 1)]
            else:
                sp_name = fn
        if not sp_type and num in pin_to_type:
            sp_type = pin_to_type[num]
        rows.append(
            {
                "pin": num,
                "name": sp_name,
                "type": sp_type,
                "net": net,
                "connected": connected,
            }
        )
    return rows, connected_refs


def _bom_local_from_refs(
    connected_refs: set[str], component_index: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ref in sorted(connected_refs):
        c = component_index.get(ref)
        if not c:
            continue
        out.append(
            {
                "ref": ref,
                "value": c.get("value", ""),
                "footprint": c.get("footprint", ""),
                "datasheet": (c.get("properties") or {}).get("Datasheet", "") or None,
            }
        )
    return out


def _export_sheet_svg(sch_path: str, dest_dir: str) -> str:
    """Run ``kicad-cli sch export svg`` and return the produced SVG path.

    Returns ``""`` on failure (caller falls back to no-crop rendering).
    """
    import subprocess  # noqa: WPS433 - local import keeps top of file clean

    try:
        from kicad_mcp.utils.kicad_cli import find_kicad_cli
    except ImportError:
        return ""
    try:
        cli = find_kicad_cli()
    except Exception:
        return ""
    if not cli:
        return ""

    os.makedirs(dest_dir, exist_ok=True)
    # kicad-cli.exe (Windows binary) requires Windows-form paths even
    # when called from WSL via interop. On native Windows the local
    # form already is Windows form so no conversion is needed.
    # ``from_local_to_other`` flips local→other unconditionally which
    # produces broken WSL paths on Windows — feed kicad-cli.exe its
    # native path form instead.
    is_win_cli = cli.lower().endswith(".exe")
    def _cli_arg(p: str) -> str:
        if is_win_cli and p.startswith("/mnt/"):
            return _wsl_to_windows(p)
        return p
    try:
        proc = subprocess.run(
            [
                cli, "sch", "export", "svg",
                "--output", _cli_arg(dest_dir),
                _cli_arg(sch_path),
            ],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("kicad-cli sch export svg failed: %s", exc)
        return ""
    if proc.returncode != 0:
        logger.warning("kicad-cli stderr: %s", (proc.stderr or "").strip()[:200])
        return ""
    stem = os.path.splitext(os.path.basename(sch_path))[0]
    candidate = os.path.join(dest_dir, f"{stem}.svg")
    return candidate if os.path.isfile(candidate) else ""


def _compute_region_bbox(
    components: list[dict[str, Any]],
    ic_ref: str,
    connected_refs: set[str],
    padding_mm: float,
) -> tuple[float, float, float, float] | None:
    """Axis-aligned bbox over the IC + connected refs' anchor positions."""
    targets = {ic_ref, *connected_refs}
    xs: list[float] = []
    ys: list[float] = []
    for c in components:
        if c.get("reference") not in targets:
            continue
        pos = c.get("position") or [0.0, 0.0]
        if len(pos) >= 2:
            xs.append(float(pos[0]))
            ys.append(float(pos[1]))
    if not xs:
        return None
    return (min(xs) - padding_mm, min(ys) - padding_mm,
            max(xs) + padding_mm, max(ys) + padding_mm)


def _write_outputs(
    out_dir: str,
    payload: dict[str, Any],
    brief_text: str,
) -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    payload_path = os.path.join(out_dir, "review_payload.json")
    brief_path = os.path.join(out_dir, "review_brief.md")
    with open(payload_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    with open(brief_path, "w", encoding="utf-8") as fh:
        fh.write(brief_text)
    return payload_path, brief_path


def _classify_net(net_name: str) -> str:
    upper = (net_name or "").upper().lstrip("/")
    if any(upper.startswith(p) for p in _GROUND_PREFIXES):
        return "ground"
    if any(upper.startswith(p) for p in _POWER_PREFIXES):
        return "power"
    return "signal"


def _bus_classify(net_name: str) -> list[str]:
    hits: list[str] = []
    for label, pattern in _BUS_PATTERNS.items():
        if pattern.search(net_name or ""):
            hits.append(label)
    return hits


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_review_tools(mcp: FastMCP) -> None:
    """Register Layer-R review tools."""

    @mcp.tool()
    def review_ic_against_datasheet(
        ic_reference: str,
        project_path: str,
        datasheet_pdf: str = "",
        datasheet_page: int = 1,
        pin_range_start: int = 0,
        pin_range_end: int = 0,
        padding_mm: float = 10.0,
        output_dir: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Assemble per-IC review material: symbol info, per-pin connectivity, cropped schematic PNG, datasheet PNG, BOM-local + review prompt.

        Use this for "review U3 against its datasheet", "check the buck
        converter wiring", "compare implementation to TPS54202 reference
        design". The tool prepares data only — the calling LLM does the
        actual side-by-side review using the produced images and tables.

        Output lands in ``<project_dir>/review/<REF>/`` by default with
        ``review_payload.json`` (structured), ``review_brief.md`` (human-
        readable, images embedded, prompt appended), ``schematic_region.png``
        and ``datasheet_p<NN>.png``. Re-running overwrites the same files
        (idempotent).

        Sibling tools: ``review_system_interconnect`` for cross-IC busses /
        power audit; ``list_missing_datasheets`` to inventory which PDFs are
        missing before kicking off the per-IC review loop.

        Args:
            ic_reference: Reference designator of the IC, e.g. ``"U3"``.
            project_path: Path to ``.kicad_pro``.
            datasheet_pdf: Path to the datasheet PDF. If empty, the tool
                looks for ``<project_dir>/docs/<value>.pdf`` and then the
                symbol's ``Datasheet`` property (if it points to a local file).
            datasheet_page: 1-based page index inside the PDF (default 1).
            pin_range_start: Lowest pin number to include (default 0 = no limit).
            pin_range_end: Highest pin number to include (default 0 = no limit).
            padding_mm: Margin in mm added around the IC + periphery bbox
                for the schematic crop. Default 10 mm.
            output_dir: Override the default output location.

        Returns:
            ``{success, output_dir, payload_path, brief_path, ic, pin_count,
            connected_refs_count, images, datasheet_resolved_via,
            partial?, output_dir_other_env}``.
        """
        project_path = to_local_path(project_path)
        datasheet_pdf = to_local_path(datasheet_pdf)
        output_dir = to_local_path(output_dir) if output_dir else ""

        if not ic_reference:
            return {"success": False, "error": "ic_reference is required"}
        if not project_path or not os.path.isfile(project_path):
            return {"success": False, "error": f"Project not found: {project_path}"}

        try:
            files = get_project_files(project_path)
        except Exception as exc:
            return {"success": False, "error": f"Cannot resolve project files: {exc}"}
        sch_path = files.get("schematic", "")
        pcb_path = files.get("pcb", "")
        if not sch_path or not os.path.isfile(sch_path):
            return {"success": False, "error": "Schematic not found in project."}
        project_dir = os.path.dirname(project_path)

        try:
            components = _load_schematic_components(sch_path)
        except Exception as exc:
            return {"success": False, "error": f"Cannot parse schematic: {exc}"}
        index = _index_components(components)
        if ic_reference not in index:
            return {
                "success": False,
                "error": f"Component '{ic_reference}' not found in schematic.",
                "available_refs_sample": sorted(index.keys())[:30],
            }
        ic_info = index[ic_reference]
        ic_value = ic_info.get("value", "")

        ds_path, ds_source = _resolve_datasheet_path(
            datasheet_pdf, project_dir, ic_value, ic_info.get("properties", {}) or {}
        )

        # Netlist + per-pin connectivity (kicad-cli first, falls back to label-only parser)
        try:
            netlist_data = extract_netlist(sch_path)
        except Exception as exc:
            return {"success": False, "error": f"Netlist extraction failed: {exc}"}
        partial = bool(netlist_data.get("partial"))

        pins, connected_refs = _build_pin_rows(ic_info, netlist_data, index, ic_reference)
        full_pin_count = len(pins)
        pins = _filter_pins_by_range(pins, pin_range_start, pin_range_end)

        # Pin-consistency cross-check (best-effort)
        pin_check = compare_symbol_pins_to_footprint(
            ic_info.get("pins", []) or [], pcb_path, ic_reference,
        )

        # Output directory
        if not output_dir:
            output_dir = os.path.join(project_dir, "review", ic_reference)
        if os.path.isdir(output_dir):
            # Idempotent: wipe per-IC dir so stale renders don't linger.
            for entry in os.listdir(output_dir):
                full = os.path.join(output_dir, entry)
                try:
                    if os.path.isfile(full):
                        os.unlink(full)
                    elif os.path.isdir(full):
                        shutil.rmtree(full)
                except OSError:
                    pass
        os.makedirs(output_dir, exist_ok=True)

        # Schematic region PNG — propagate root-cause when any sub-step fails
        # so the calling LLM knows whether bbox-detection, kicad-cli export,
        # or the PNG-rasterizer broke (the previous silent ``success:false``
        # was useless for debugging).
        sch_png_path = os.path.join(output_dir, "schematic_region.png")
        sch_render: dict[str, Any] = {"success": False}
        bbox = _compute_region_bbox(
            components, ic_reference, connected_refs, padding_mm=0.0
        )
        tmp_svg_dir = os.path.join(output_dir, "_svg")
        tmp_svg_path = _export_sheet_svg(sch_path, tmp_svg_dir)
        if bbox is None:
            sch_render = {
                "success": False,
                "error": (
                    f"could not compute schematic-region bbox for "
                    f"{ic_reference} (component not found in schematic "
                    "or position missing)"
                ),
            }
        elif not tmp_svg_path:
            sch_render = {
                "success": False,
                "error": (
                    "kicad-cli sch export svg failed — see stderr; common "
                    "causes: kicad-cli not on PATH, schematic has unsaved "
                    "changes, or KiCad has the file open with a lock"
                ),
            }
        else:
            sch_render = render_region_to_png(
                tmp_svg_path, bbox, sch_png_path,
                padding_mm=padding_mm, scale=2.0,
            )
        if not sch_render.get("success"):
            sch_png_path = ""

        # Datasheet page PNG
        ds_png_path = ""
        ds_render: dict[str, Any] = {"success": False, "error": "no datasheet"}
        if ds_path:
            ds_png_path = os.path.join(output_dir, f"datasheet_p{datasheet_page:02d}.png")
            ds_render = rasterize_pdf_page(ds_path, datasheet_page, ds_png_path, dpi=300)
            if not ds_render.get("success"):
                ds_png_path = ""

        # Cleanup tmp SVG dir
        if os.path.isdir(tmp_svg_dir):
            shutil.rmtree(tmp_svg_dir, ignore_errors=True)

        # Build payload
        pin_range_serial: list[int] | None = None
        if pin_range_start or pin_range_end:
            pin_range_serial = [int(pin_range_start or 0), int(pin_range_end or 0)]

        bom_local = _bom_local_from_refs(connected_refs, index)
        payload: dict[str, Any] = {
            "ic": {
                "ref": ic_reference,
                "value": ic_value,
                "footprint": ic_info.get("footprint", ""),
                "library_id": ic_info.get("library_id", ""),
                "sheet": os.path.basename(sch_path),
                "position_mm": ic_info.get("position", [0.0, 0.0]),
            },
            "pins": pins,
            "bom_local": bom_local,
            "images": {
                "schematic_region": sch_png_path,
                "datasheet_reference": ds_png_path,
            },
            "meta": {
                "project": project_path,
                "schematic": sch_path,
                "generated_at": _now_utc_iso(),
                "pin_range": pin_range_serial,
                "full_pin_count": full_pin_count,
                "shown_pin_count": len(pins),
                "datasheet_resolved_via": ds_source,
                "datasheet_path": ds_path,
                "datasheet_render": ds_render,
                "schematic_region_render": {
                    k: v for k, v in sch_render.items() if k != "tree"
                },
                "pin_consistency_warnings": pin_check.get("warnings", []),
                "pin_consistency_checked": pin_check.get("checked", False),
                "partial_netlist": partial,
            },
            "review_prompt_hint": (
                "Open review_brief.md — the hard-wired review prompt is at the "
                "end. Fill in pin-by-pin against the embedded images."
            ),
        }
        if full_pin_count > 64 and not pin_range_serial:
            payload["meta"]["pin_count_hint"] = (
                f"IC has {full_pin_count} pins — consider calling again with "
                "pin_range_start/end to split the review."
            )

        brief_text = render_brief_md(payload, output_dir)
        payload_path, brief_path = _write_outputs(output_dir, payload, brief_text)

        if ctx:
            ctx.info(
                f"Review for {ic_reference} written to {output_dir} "
                f"(datasheet via {ds_source})"
            )

        result = {
            "success": True,
            "output_dir": output_dir,
            "output_dir_other_env": from_local_to_other(output_dir),
            "payload_path": payload_path,
            "brief_path": brief_path,
            "ic": payload["ic"],
            "pin_count": len(pins),
            "full_pin_count": full_pin_count,
            "connected_refs_count": len(connected_refs),
            "images": payload["images"],
            "datasheet_resolved_via": ds_source,
        }
        if partial:
            result["partial_netlist"] = True
        return result

    # -----------------------------------------------------------------------

    @mcp.tool()
    def review_system_interconnect(
        project_path: str,
        output_dir: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Assemble system-level review data: power tree, decoupling-cap audit, pull-up/down audit, bus peers.

        Use this AFTER the per-IC review loop is complete. It looks at the
        whole schematic at once and surfaces interconnect-level concerns
        the per-IC pass cannot see: duplicate pull-ups on a shared bus,
        missing decoupling on a VCC pin, two SPI masters on the same net,
        polarity / protection holes.

        Output lands in ``<project_dir>/review/system/`` with
        ``system_payload.json`` and ``system_brief.md`` (idempotent).

        Sibling tools: ``review_ic_against_datasheet`` for the per-IC pass.

        Args:
            project_path: Path to ``.kicad_pro``.
            output_dir: Override the default output location.

        Returns:
            ``{success, output_dir, payload_path, brief_path, ic_count,
            power_net_count, bus_net_count, pullup_findings,
            output_dir_other_env}``.
        """
        project_path = to_local_path(project_path)
        output_dir = to_local_path(output_dir) if output_dir else ""

        if not project_path or not os.path.isfile(project_path):
            return {"success": False, "error": f"Project not found: {project_path}"}

        try:
            files = get_project_files(project_path)
        except Exception as exc:
            return {"success": False, "error": f"Cannot resolve project files: {exc}"}
        sch_path = files.get("schematic", "")
        if not sch_path or not os.path.isfile(sch_path):
            return {"success": False, "error": "Schematic not found in project."}
        project_dir = os.path.dirname(project_path)

        try:
            components = _load_schematic_components(sch_path)
        except Exception as exc:
            return {"success": False, "error": f"Cannot parse schematic: {exc}"}
        index = _index_components(components)

        try:
            netlist_data = extract_netlist(sch_path)
        except Exception as exc:
            return {"success": False, "error": f"Netlist extraction failed: {exc}"}
        nets = netlist_data.get("nets", {}) or {}

        # Classify nets
        power_nets: dict[str, dict[str, Any]] = {}
        bus_nets: dict[str, list[str]] = {}
        ground_nets: list[str] = []
        for net_name, pin_list in nets.items():
            kind = _classify_net(net_name)
            if kind == "ground":
                ground_nets.append(net_name)
                power_nets[net_name] = {
                    "consumer_count": len(pin_list),
                    "source_hint": "GND",
                }
            elif kind == "power":
                power_nets[net_name] = {
                    "consumer_count": len(pin_list),
                    "source_hint": net_name,
                }
            for bus in _bus_classify(net_name):
                bus_nets.setdefault(bus, []).append(net_name)

        # Pull-up/down audit: scan resistor components, see if one pin is on
        # a signal net and the other on a power/ground net.
        pullup_rows: list[dict[str, Any]] = []
        net_to_pullups: dict[str, list[str]] = {}
        net_to_pulldowns: dict[str, list[str]] = {}
        for comp in components:
            ref = comp.get("reference", "")
            if not ref or not ref.upper().startswith("R"):
                continue
            # Collect this R's two pin nets
            r_pins: dict[str, str] = {}
            for net_name, pin_list in nets.items():
                for p in pin_list:
                    if p.get("component") == ref:
                        r_pins[str(p.get("pin", ""))] = net_name
                if len(r_pins) >= 2:
                    break
            if len(r_pins) != 2:
                continue
            r_nets = list(r_pins.values())
            net_a = r_nets[0]
            net_b = r_nets[1]
            k_a, k_b = _classify_net(net_a), _classify_net(net_b)
            if k_a == "signal" and k_b == "power":
                net_to_pullups.setdefault(net_a, []).append(f"{ref}({comp.get('value','')})")
            elif k_b == "signal" and k_a == "power":
                net_to_pullups.setdefault(net_b, []).append(f"{ref}({comp.get('value','')})")
            elif k_a == "signal" and k_b == "ground":
                net_to_pulldowns.setdefault(net_a, []).append(f"{ref}({comp.get('value','')})")
            elif k_b == "signal" and k_a == "ground":
                net_to_pulldowns.setdefault(net_b, []).append(f"{ref}({comp.get('value','')})")

        for net in sorted(set(list(net_to_pullups) + list(net_to_pulldowns))):
            pu = net_to_pullups.get(net, [])
            pd = net_to_pulldowns.get(net, [])
            note = ""
            if len(pu) > 1:
                note = "Mehrfach-Pullup auf demselben Netz — pruefen"
            elif len(pu) and len(pd):
                note = "Sowohl Pullup als auch Pulldown — pruefen"
            pullup_rows.append(
                {"net": net, "pullups": pu, "pulldowns": pd, "note": note}
            )

        # Decoupling-cap audit: for each IC (ref starts with U), list caps
        # sharing the same VCC-class net. Geographic proximity is left to
        # the reviewing LLM (positions are present in component_index).
        decoupling_rows: list[dict[str, Any]] = []
        ic_refs = sorted(r for r in index if r.upper().startswith("U"))
        for ic_ref in ic_refs:
            ic_pins = (index[ic_ref].get("pins") or [])
            # Map this IC's pins → net
            ic_pin_net: dict[str, str] = {}
            for net_name, pin_list in nets.items():
                for p in pin_list:
                    if p.get("component") == ic_ref:
                        ic_pin_net[str(p.get("pin", ""))] = net_name
            for pin in ic_pins:
                name = (pin.get("name") or "").upper()
                ptype = (pin.get("type") or "").lower()
                num = str(pin.get("number") or pin.get("num") or "")
                if not (ptype.startswith("power") or name.startswith(
                    _POWER_PREFIXES
                )):
                    continue
                net = ic_pin_net.get(num, "")
                if not net or _classify_net(net) != "power":
                    continue
                # Find caps connected to the same net
                caps = []
                for p in nets.get(net, []) or []:
                    other = p.get("component", "")
                    if other and other != ic_ref and other.upper().startswith("C"):
                        caps.append(f"{other}({index.get(other,{}).get('value','')})")
                verdict = "ok" if caps else "kein Decoupling-Cap auf dem Netz gefunden"
                decoupling_rows.append(
                    {
                        "ic": ic_ref,
                        "pin": num,
                        "pin_name": pin.get("name", ""),
                        "net": net,
                        "nearby_caps": sorted(set(caps)),
                        "verdict": verdict,
                    }
                )

        # Output
        if not output_dir:
            output_dir = os.path.join(project_dir, "review", "system")
        if os.path.isdir(output_dir):
            for entry in os.listdir(output_dir):
                full = os.path.join(output_dir, entry)
                try:
                    if os.path.isfile(full):
                        os.unlink(full)
                except OSError:
                    pass
        os.makedirs(output_dir, exist_ok=True)

        payload = {
            "project_name": os.path.splitext(os.path.basename(project_path))[0],
            "ics": ic_refs,
            "power_tree": power_nets,
            "ground_nets": sorted(ground_nets),
            "bus_peers": {k: sorted(set(v)) for k, v in bus_nets.items()},
            "pullup_audit": pullup_rows,
            "decoupling_audit": decoupling_rows,
            "meta": {
                "project": project_path,
                "schematic": sch_path,
                "generated_at": _now_utc_iso(),
                "partial_netlist": bool(netlist_data.get("partial")),
            },
        }
        brief = render_system_brief_md(payload, output_dir)
        payload_path, brief_path = _write_outputs(output_dir, payload, brief)
        # The system brief filename is review_brief.md by default; rename for
        # clarity since system != per-IC.
        sys_payload = os.path.join(output_dir, "system_payload.json")
        sys_brief = os.path.join(output_dir, "system_brief.md")
        os.replace(payload_path, sys_payload)
        os.replace(brief_path, sys_brief)

        if ctx:
            ctx.info(f"System review written to {output_dir}")

        return {
            "success": True,
            "output_dir": output_dir,
            "output_dir_other_env": from_local_to_other(output_dir),
            "payload_path": sys_payload,
            "brief_path": sys_brief,
            "ic_count": len(ic_refs),
            "power_net_count": len(power_nets),
            "bus_net_count": sum(len(v) for v in bus_nets.values()),
            "pullup_findings": len(pullup_rows),
            "decoupling_findings": len(decoupling_rows),
        }

    # -----------------------------------------------------------------------

    @mcp.tool()
    def list_missing_datasheets(
        project_path: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List every unique IC ``Value`` whose datasheet PDF is not on disk yet.

        Use this BEFORE the per-IC review loop to ask the user which
        datasheets to fetch. Datasheets are expected at
        ``<project_dir>/docs/<value>.pdf`` (one PDF per unique chip value;
        reuses across projects with the same chip).

        Returns refs that map to the same value grouped together so the
        user sees how many ICs benefit from each datasheet. The tool's
        ``Datasheet`` property of each symbol is reported when set so the
        user can copy the URL into a browser.

        Args:
            project_path: Path to ``.kicad_pro``.

        Returns:
            ``{success, project_path, docs_dir, missing: [{value,
            refs, datasheet_url}], present: [...], total_unique_values}``.
        """
        project_path = to_local_path(project_path)
        if not project_path or not os.path.isfile(project_path):
            return {"success": False, "error": f"Project not found: {project_path}"}

        try:
            files = get_project_files(project_path)
        except Exception as exc:
            return {"success": False, "error": f"Cannot resolve project files: {exc}"}
        sch_path = files.get("schematic", "")
        if not sch_path or not os.path.isfile(sch_path):
            return {"success": False, "error": "Schematic not found in project."}
        project_dir = os.path.dirname(project_path)
        docs_dir = os.path.join(project_dir, "docs")

        try:
            components = _load_schematic_components(sch_path)
        except Exception as exc:
            return {"success": False, "error": f"Cannot parse schematic: {exc}"}

        # Only ICs (U-prefix); skip resistors, caps, connectors, mech.
        groups: dict[str, dict[str, Any]] = {}
        for c in components:
            ref = c.get("reference", "")
            if not ref.upper().startswith("U"):
                continue
            value = (c.get("value") or "").strip()
            if not value:
                continue
            slot = groups.setdefault(
                value,
                {"value": value, "refs": [], "datasheet_url": ""},
            )
            slot["refs"].append(ref)
            url = (c.get("properties") or {}).get("Datasheet", "") or ""
            if url and not slot["datasheet_url"]:
                slot["datasheet_url"] = url

        missing = []
        present = []
        for value, info in sorted(groups.items()):
            info["refs"] = sorted(info["refs"])
            pdf_path = os.path.join(docs_dir, f"{value}.pdf")
            if os.path.isfile(pdf_path):
                info["pdf_path"] = pdf_path
                present.append(info)
            else:
                info["expected_pdf_path"] = pdf_path
                missing.append(info)

        if ctx:
            ctx.info(
                f"{len(missing)} datasheet(s) missing, {len(present)} present "
                f"in {docs_dir}"
            )

        return {
            "success": True,
            "project_path": project_path,
            "docs_dir": docs_dir,
            "total_unique_values": len(groups),
            "missing": missing,
            "present": present,
        }
