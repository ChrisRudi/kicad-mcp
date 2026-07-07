# SPDX-License-Identifier: GPL-3.0-or-later
"""Layer T — spec-driven circuit-block composition tools.

Five MCP tools that orchestrate the existing Layer-S patcher
(``add_schematic_symbols`` / ``connect_pins`` / ``add_power_symbols``)
to insert a datasheet-defined IC block (chip + outer beschaltung) into
an existing ``.kicad_sch`` from a JSON spec.

Workflow Phase A → F (see ``docs/circuit_block_workflow.md``):

  A) Spec gewinnen — three input paths:
      * ``apply_template_block`` — template + app_params -> draft spec
      * ``extract_circuit_from_pdf`` — datasheet PDF -> draft spec stub
        (pdfplumber tables + section text; LLM does the mapping in chat)
      * Hand-written spec
  B) User reviews + fills app-specific values
  C) Pre-flight ``validate_circuit_block`` — schema + lib + pin-types
  D) Apply ``apply_circuit_block`` — emits Layer-S patch sequence
  E) User wires cross-sheet (manual)
  F) Loop for next block
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from kicad_mcp.generators.circuit_block import schema_v1_1
from kicad_mcp.generators.circuit_block._block_to_patch import build_patch_payload
from kicad_mcp.utils.path_env import to_local_path


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _load_spec_from_arg(spec_arg: str) -> tuple[Optional[dict], Optional[str]]:
    """Accept either a path to a .json file or a JSON literal.

    Returns ``(spec_dict, error_message)``. Exactly one of the two is
    non-None. Path detection: if the string contains a path separator and
    points to an existing file we treat it as a path; otherwise we
    json.loads() it as inline JSON. A bare name without separator/brace
    (e.g. ``"mp1584_buck_5v"``) resolves against the shipped block library
    ``resources/data/circuit_blocks/`` — the single home of the blocks.
    """
    if not spec_arg:
        return None, "spec is empty"
    spec_arg = spec_arg.strip()

    # Bare block name → shipped library (one source for kits AND apply).
    if ("/" not in spec_arg and "\\" not in spec_arg
            and not spec_arg.startswith("{")):
        from kicad_mcp.generators.circuit_block.kit_compose import BLOCKS_DIR
        name = spec_arg[:-5] if spec_arg.endswith(".json") else spec_arg
        shipped = os.path.join(BLOCKS_DIR, f"{name}.json")
        if os.path.isfile(shipped):
            try:
                with open(shipped, encoding="utf-8") as fh:
                    return json.load(fh), None
            except Exception as exc:
                return None, f"Failed to read shipped block {shipped!r}: {exc}"

    # Path?
    looks_like_path = (
        ("/" in spec_arg or "\\" in spec_arg)
        or spec_arg.endswith(".json")
    )
    if looks_like_path:
        local = to_local_path(spec_arg)
        if os.path.isfile(local):
            try:
                with open(local, encoding="utf-8") as fh:
                    return json.load(fh), None
            except Exception as exc:
                return None, f"Failed to read spec file {local!r}: {exc}"
        # Path-like but missing — fall through to JSON parse so a paste
        # that happens to start with '{' still works.

    # Inline JSON
    try:
        return json.loads(spec_arg), None
    except Exception as exc:
        return None, f"spec is neither readable file nor valid JSON: {exc}"


def _jsonschema_validate(spec: dict) -> list[str]:
    """Validate ``spec`` against schema_v1_1.json. Returns list of errors.

    The ``jsonschema`` package is a soft dependency — if missing, fall
    back to a minimal-required-fields check so the tool stays usable.
    """
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return _fallback_validate(spec)

    schema = schema_v1_1()
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(spec), key=lambda e: list(e.path))
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    ]


def _fallback_validate(spec: dict) -> list[str]:
    """Minimal validation when jsonschema is not available."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    for req in ("schema_version", "chip", "kicad_symbol", "pins", "peripherals"):
        if req not in spec:
            errors.append(f"<root>: '{req}' is a required property")
    if spec.get("schema_version") not in (None, "1.1"):
        errors.append(f"schema_version: must be '1.1', got {spec['schema_version']!r}")
    pins = spec.get("pins", [])
    if not isinstance(pins, list) or len(pins) < 1:
        errors.append("pins: must be a non-empty list")
    peri = spec.get("peripherals", [])
    if not isinstance(peri, list):
        errors.append("peripherals: must be a list")
    return errors


def _check_kicad_symbols(spec: dict) -> list[str]:
    """Try to verify the chip's kicad_symbol exists in the available libs.

    Uses the same symbol-cache mechanism the patcher uses. Failures here
    are reported as *warnings* (not errors) — a missing symbol may still
    resolve once the user opens the project in KiCad and the lib-tables
    are walked.
    """
    warnings: list[str] = []
    lib_id = spec.get("kicad_symbol")
    if not lib_id:
        return warnings
    try:
        from kicad_mcp.generators.symbol_cache import get_real_symbol
        if not get_real_symbol(lib_id):
            warnings.append(
                f"chip kicad_symbol {lib_id!r}: not found in symbol cache "
                f"— may need 'index_kicad_footprints' or a stock-lib import"
            )
    except Exception as exc:  # pragma: no cover  cache may be unavailable
        warnings.append(f"symbol cache unavailable: {exc}")
    return warnings


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_circuit_block_tools(mcp: FastMCP) -> None:
    """Register Layer-T circuit-block tools with the MCP server.

    Tools registered:
      * ``validate_circuit_block``
      * ``apply_circuit_block``
      * ``apply_template_block``
      * ``extract_pdf_tables``
      * ``extract_circuit_from_pdf``
    """

    # ----- validate_circuit_block ------------------------------------------
    @mcp.tool()
    def validate_circuit_block(spec: str) -> dict[str, Any]:
        """Pre-flight a circuit-block JSON spec against schema v1.1.

        Validates the spec's structure (required fields, pin-type enum,
        between[] cardinality, instance refs, strap config) without
        touching any ``.kicad_sch``. Use this **before** every
        ``apply_circuit_block`` call so spec typos surface as a clean
        error list instead of a half-applied patch. Don't roll your own
        schema check — the tool reads the canonical
        ``schema_v1_1.json`` and reports JSON-Schema-style messages.

        Args:
            spec: Either a path to a ``.json`` file, an inline JSON
                string, or the bare name of a shipped block (e.g.
                ``"mp1584_buck_5v"`` — resolved against
                ``resources/data/circuit_blocks/``, the same blocks the
                demo kits are composed from).

        Returns:
            ``{success, errors: [str], warnings: [str], chip, schema_version,
            pin_count, peripheral_count, instance_count}``.
            ``success`` is True iff ``errors`` is empty.
        """
        spec_dict, err = _load_spec_from_arg(spec)
        if err:
            return {"success": False, "errors": [err], "warnings": []}
        errors = _jsonschema_validate(spec_dict)
        warnings = _check_kicad_symbols(spec_dict) if not errors else []
        return {
            "success": not errors,
            "errors": errors,
            "warnings": warnings,
            "chip": spec_dict.get("chip"),
            "schema_version": spec_dict.get("schema_version"),
            "pin_count": len(spec_dict.get("pins") or []),
            "peripheral_count": len(spec_dict.get("peripherals") or []),
            "instance_count": len(spec_dict.get("instances") or []) or 1,
        }

    # ----- apply_circuit_block ---------------------------------------------
    @mcp.tool()
    def apply_circuit_block(
        sch_path: str,
        spec: str,
        instance_id: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Insert a datasheet-defined IC block into an existing schematic.

        This is a Layer-T composition over Layer-S patcher tools — it
        does not implement any S-expression surgery itself. For a given
        ``spec`` it:

        1. Validates against schema v1.1 (same as
           ``validate_circuit_block``); aborts on errors.
        2. Picks the placement origin from ``spec.placement`` or the
           matching ``spec.instances[]`` entry when ``instance_id`` is
           given.
        3. Composes a ``parts`` payload (chip + every required
           peripheral) and emits ``add_schematic_symbols``.
        4. Composes a ``connections`` payload (peripheral pin →
           chip pin / external net) and emits ``connect_pins``
           with mode='wire' for chip-pin connections and mode='label'
           for external_nets[] when ``sheet_scope == 'hierarchical'``.
        5. Emits ``add_power_symbols`` for every power-typed pin and
           every peripheral whose ``between`` ends on a power net —
           **never** writes plain global labels for power rails (see
           CONVENTIONS.md "power-symbols").

        Don't try to call ``add_schematic_symbols`` + ``connect_pins``
        yourself from a datasheet — this tool centralises the
        peripheral-ring placement, the power-symbol convention and the
        multi-instance net-suffix logic.

        Args:
            sch_path: ``.kicad_sch`` to patch. WSL- and Windows-style
                paths both accepted.
            spec: Path to a v1.1 spec ``.json`` file, inline JSON, or
                the bare name of a shipped block (e.g.
                ``"mp1584_buck_5v"`` from
                ``resources/data/circuit_blocks/``).
            instance_id: Reference of one entry in
                ``spec.instances[]``. Empty = no instance loop, the
                root-level ``placement`` is used. To apply all
                instances, call this tool once per ``ref``.
            dry_run: If True, no schematic is written; the return dict
                contains ``would_apply`` with the composed payloads
                instead.

        Returns:
            ``{success, sch_path, refs_added, nets_added,
            erc_violations_after, warnings, applied_instance,
            spec_chip, would_apply?}``.
        """
        sch_path = to_local_path(sch_path)
        if not os.path.isfile(sch_path):
            return {"success": False, "error": f"File not found: {sch_path}"}
        spec_dict, err = _load_spec_from_arg(spec)
        if err:
            return {"success": False, "error": err}
        errors = _jsonschema_validate(spec_dict)
        if errors:
            return {"success": False, "errors": errors}

        # Pick instance ----------------------------------------------------
        instance: Optional[dict] = None
        if instance_id:
            instances = spec_dict.get("instances") or []
            for inst in instances:
                if inst.get("ref") == instance_id:
                    instance = inst
                    break
            if instance is None:
                return {
                    "success": False,
                    "error": f"instance_id {instance_id!r} not found in spec.instances[]",
                }

        # Compose payloads -------------------------------------------------
        try:
            payload = build_patch_payload(spec_dict, instance=instance)
        except Exception as exc:
            return {"success": False, "error": f"Build payload failed: {exc}"}

        warnings = list(payload.get("warnings", []))

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "sch_path": sch_path,
                "applied_instance": instance_id or None,
                "spec_chip": spec_dict.get("chip"),
                "would_apply": {
                    "parts": payload["parts"],
                    "connections": payload["connections"],
                    "power_anchors": payload["power_anchors"],
                    "external_nets": payload["external_nets"],
                },
                "warnings": warnings,
            }

        # Layer-S delegation — every effect on the schematic flows
        # through the same MCP-tool surface the LLM uses.
        result = _apply_via_layer_s(
            mcp_server=mcp,
            sch_path=sch_path,
            payload=payload,
            sheet_scope=spec_dict.get("sheet_scope", "root"),
        )
        if not result.get("success", False):
            warnings.extend(result.get("errors", []))
        return {
            "success": bool(result.get("success", False)),
            "sch_path": sch_path,
            "applied_instance": instance_id or None,
            "spec_chip": spec_dict.get("chip"),
            "refs_added": result.get("refs_added", []),
            "nets_added": result.get("nets_added", []),
            "lib_symbols_added": result.get("lib_symbols_added", []),
            "warnings": warnings,
            "errors": result.get("errors", []),
        }

    # ----- apply_template_block --------------------------------------------
    @mcp.tool()
    def apply_template_block(
        template_id: str,
        chip_meta: str = "{}",
        app_params: str = "{}",
        out_path: str = "",
    ) -> dict[str, Any]:
        """Materialise a template (e.g. ``smps_buck_converter``) into a
        concrete v1.1 spec by filling in chip metadata and
        application-specific parameters.

        This tool reads the template's ``block_definition`` section from
        ``training/templates/schematic/<template_id>.json``, merges in
        ``chip_meta`` (manufacturer-specific overrides like the actual
        kicad_symbol, footprint, pin-table) and ``app_params`` (Vin /
        Vout / Iout for buck, Vout for LDO, etc.), evaluates simple
        ``value_formula`` expressions where possible, and writes a
        draft spec — ready for user review and ``apply_circuit_block``.

        Don't hand-craft a spec from a known IC if a template exists —
        the template-derived spec already encodes the standard outer
        beschaltung (decoupling, bootstrap, FB-divider, …) so the
        result is consistent with the recognised pattern that
        ``identify_circuit_patterns`` would later detect.

        Args:
            template_id: Name of a template under
                ``kicad_mcp/training/templates/schematic/`` (without
                ``.json``).  Examples: ``smps_buck_converter``,
                ``linear_voltage_regulator``, ``h_bridge``.
            chip_meta: JSON object overriding ``chip``,
                ``kicad_symbol``, ``kicad_footprint``, ``pins`` etc.
            app_params: JSON object with values like
                ``{Vin:12, Vout:3.3, Iout:0.5}``.
            out_path: Optional file path; when given the resulting spec
                is written there. Empty = return inline.

        Returns:
            ``{success, template_id, draft_spec, draft_path?,
            review_status, needs_review[]}``.
        """
        from kicad_mcp.tools.circuit_block_tools_helpers import (
            load_template_block_definition,
        )

        try:
            chip_meta_d = json.loads(chip_meta) if chip_meta.strip() else {}
            app_params_d = json.loads(app_params) if app_params.strip() else {}
        except Exception as exc:
            return {"success": False, "error": f"Bad JSON in chip_meta/app_params: {exc}"}

        block_def, terr = load_template_block_definition(template_id)
        if terr:
            return {"success": False, "error": terr}
        if block_def is None:
            return {
                "success": False,
                "error": (
                    f"Template {template_id!r} has no block_definition yet "
                    f"(stub-only). Pick a template from the fully-defined "
                    f"set: smps_buck_converter, linear_voltage_regulator, "
                    f"h_bridge."
                ),
            }

        # Merge chip_meta over block_def's chip/kicad_symbol/pins
        spec = dict(block_def)
        for k in ("chip", "manufacturer", "kicad_symbol", "kicad_footprint",
                  "package", "datasheet_url", "datasheet_revision"):
            if k in chip_meta_d:
                spec[k] = chip_meta_d[k]
        if "pins" in chip_meta_d:
            spec["pins"] = chip_meta_d["pins"]

        # Inject app_params under operating_envelope so users see them.
        env = dict(spec.get("operating_envelope") or {})
        env.update(app_params_d)
        spec["operating_envelope"] = env

        # Mark review status — values from formulas are best-effort.
        # Even if the template was 'verified_against_datasheet', the merged
        # spec carries app-specific values that the user must inspect.
        spec.setdefault("schema_version", "1.1")
        spec["review_status"] = "needs_review"
        nr = list(spec.get("needs_review") or [])
        nr.append(f"verify component values against actual app_params: {sorted(app_params_d.keys())}")
        spec["needs_review"] = nr

        # Optional persist
        out: dict[str, Any] = {
            "success": True,
            "template_id": template_id,
            "draft_spec": spec,
            "review_status": spec["review_status"],
            "needs_review": spec["needs_review"],
        }
        if out_path:
            out_local = to_local_path(out_path)
            try:
                os.makedirs(os.path.dirname(out_local) or ".", exist_ok=True)
                with open(out_local, "w", encoding="utf-8") as fh:
                    json.dump(spec, fh, indent=2)
                out["draft_path"] = out_local
            except Exception as exc:
                out["success"] = False
                out["error"] = f"Failed to write {out_local}: {exc}"
        return out

    # ----- extract_pdf_tables ----------------------------------------------
    @mcp.tool()
    def extract_pdf_tables(
        pdf_path: str, pages: str = ""
    ) -> dict[str, Any]:
        """Extract every table found on the requested datasheet pages.

        Layout-aware extraction via pdfplumber. Use this when you need
        the raw cell content of a Pin-Functions table or a Recommended-
        Component-Values table. The result is a deterministic
        list-of-list-of-strings — semantic mapping ("which column is
        Pin Number?") is left to the calling LLM.

        Don't try to pdf-text-stream the raw datasheet — column
        boundaries and merged cells get lost. This tool keeps the row /
        column structure that PyPDF2-style streams discard.

        Args:
            pdf_path: Datasheet PDF.
            pages: Comma-separated 1-based page numbers, e.g. ``"2,3,7"``.
                Empty = every page.

        Returns:
            ``{success, page_count, tables: [{page, index, rows: [[cell,...]]}]}``.
            On missing dependency: ``{success:False, error:"pip install kicad-mcp[pdf]"}``.
        """
        from kicad_mcp.generators.circuit_block._pdf_extract import extract_tables

        pdf_path = to_local_path(pdf_path)
        if not os.path.isfile(pdf_path):
            return {"success": False, "error": f"File not found: {pdf_path}"}

        page_list: Optional[list[int]] = None
        if pages.strip():
            try:
                page_list = [int(p.strip()) for p in pages.split(",") if p.strip()]
            except ValueError:
                return {"success": False, "error": f"Bad pages list: {pages!r}"}
        try:
            return extract_tables(pdf_path, pages=page_list)
        except ImportError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"Extraction failed: {exc}"}

    # ----- extract_circuit_from_pdf ----------------------------------------
    @mcp.tool()
    def extract_circuit_from_pdf(
        pdf_path: str, target_chip: str, pages: str = ""
    ) -> dict[str, Any]:
        """Bundle datasheet tables + per-page text into an LLM-ready blob.

        Use this when you have a chip's datasheet PDF and need its tables and
        page text pulled into a structured payload to hand-author a v1.1
        ``draft_block.json`` from.

        This is the **first half** of Phase A1: the tool extracts every
        table and the section-text for the requested pages and returns
        a structured payload that the calling LLM (you, in the chat) can
        map to a v1.1 ``draft_block.json``. The tool deliberately does
        not call out to an LLM API — keeping it stateless and key-free.

        Don't expect a finished spec from this tool. The output's
        ``draft_block_skeleton`` is a v1.1 stub with
        ``review_status="needs_review"`` and ``needs_review[]`` entries
        listing every empty field. Use the returned ``tables`` and
        ``pages`` to fill it in.

        Args:
            pdf_path: Datasheet PDF.
            target_chip: Chip part-number (used in the skeleton's
                ``chip`` field; otherwise free-text).
            pages: Comma-separated 1-based page numbers; empty = all.

        Returns:
            ``{success, target_chip, page_count, tables:[...],
            pages:[{page,text}], draft_block_skeleton:{...},
            llm_mapping_hint: str}``.
        """
        from kicad_mcp.generators.circuit_block._pdf_extract import (
            extract_tables, extract_text_blocks,
        )

        pdf_path = to_local_path(pdf_path)
        if not os.path.isfile(pdf_path):
            return {"success": False, "error": f"File not found: {pdf_path}"}

        page_list: Optional[list[int]] = None
        if pages.strip():
            try:
                page_list = [int(p.strip()) for p in pages.split(",") if p.strip()]
            except ValueError:
                return {"success": False, "error": f"Bad pages list: {pages!r}"}

        try:
            tbl = extract_tables(pdf_path, pages=page_list)
            txt = extract_text_blocks(pdf_path, pages=page_list)
        except ImportError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"Extraction failed: {exc}"}

        skeleton = {
            "schema_version": "1.1",
            "chip": target_chip,
            "manufacturer": "",
            "datasheet_url": "",
            "datasheet_revision": "",
            "package": "",
            "kicad_symbol": "",
            "kicad_footprint": "",
            "pins": [],
            "peripherals": [],
            "external_nets": [],
            "operating_envelope": {},
            "review_status": "needs_review",
            "needs_review": [
                "extract pins[] from the Pin-Functions table",
                "fill manufacturer + datasheet_url + datasheet_revision",
                "pick kicad_symbol and kicad_footprint from KiCad library",
                "extract peripherals[] from Application-Schematic figure",
                "verify recommended component values from datasheet table",
            ],
        }

        return {
            "success": True,
            "target_chip": target_chip,
            "page_count": tbl.get("page_count", 0),
            "tables": tbl.get("tables", []),
            "pages": txt.get("pages", []),
            "draft_block_skeleton": skeleton,
            "llm_mapping_hint": (
                "Map the 'tables' entries to draft_block_skeleton.pins (look "
                "for a 'PIN'/'PIN NO.'/'NAME'/'TYPE' header row). Use the "
                "'pages' text to find the 'Typical Application' figure "
                "section and infer peripherals[] (input cap, bootstrap cap, "
                "inductor, output cap, FB divider, EN pulldown). Set "
                "review_status='verified_against_datasheet' only after "
                "every needs_review[] item is resolved."
            ),
        }


# ---------------------------------------------------------------------------
# Layer-S adapter
# ---------------------------------------------------------------------------


def _apply_via_layer_s(
    mcp_server: FastMCP,
    sch_path: str,
    payload: dict,
    sheet_scope: str = "root",
) -> dict[str, Any]:
    """Translate a circuit-block payload into Layer-S patcher calls.

    Implementation note: instead of going through ``mcp_server.call_tool``
    (which would round-trip async dispatch and JSON-encode every arg),
    we invoke the underlying patcher implementations directly via the
    helper module ``circuit_block_tools_helpers``. This keeps the LLM-
    facing API and the in-process API identical and avoids importing
    asyncio just to call our own code.
    """
    from kicad_mcp.tools.circuit_block_tools_helpers import (
        invoke_add_schematic_symbols,
        invoke_add_power_symbols,
        invoke_connect_pins,
    )

    refs_added: list[str] = []
    nets_added: list[str] = []
    lib_symbols_added: list[str] = []
    errors: list[str] = []

    # 1) Symbols (chip + peripherals + strap resistors)
    if payload["parts"]:
        r = invoke_add_schematic_symbols(sch_path, payload["parts"])
        if not r.get("success", False):
            errors.extend(r.get("errors", []))
        refs_added.extend(r.get("inserted", []))
        lib_symbols_added.extend(r.get("lib_symbols_added", []))

    # 2) Power symbols
    if payload["power_anchors"]:
        r = invoke_add_power_symbols(sch_path, payload["power_anchors"])
        if not r.get("success", False):
            errors.extend(r.get("errors", []))
        refs_added.extend(r.get("inserted", []))
        for a in payload["power_anchors"]:
            net = a.get("net")
            if net and net not in nets_added:
                nets_added.append(net)

    # 3) Connections
    if payload["connections"]:
        r = invoke_connect_pins(sch_path, payload["connections"], mode="wire")
        if not r.get("success", False):
            errors.extend(
                [str(item.get("error", "")) for item in r.get("results", []) if not item.get("ok", True)]
            )

    # 4) External-net labels are deferred to a later pass (hierarchical
    #    mode). For now, sheet_scope='hierarchical' is recognised but acts the
    #    same as 'root': external nets sit on global labels emitted by
    #    the user via connect_pins(mode='label') from the chat.
    _ = sheet_scope

    success = not errors
    return {
        "success": success,
        "refs_added": refs_added,
        "nets_added": nets_added,
        "lib_symbols_added": lib_symbols_added,
        "errors": errors,
    }


# Set up module-level logger (used by helper).
logging.getLogger(__name__).setLevel(logging.INFO)
