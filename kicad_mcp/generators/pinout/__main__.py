# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI for the pinout pipeline — same pure functions the MCP tools call.

    python -m kicad_mcp.generators.pinout validate \
        --sym X.kicad_sym --symbol DRV8313 --pdf d.pdf [--pages 5,6] [--json]
    python -m kicad_mcp.generators.pinout search DRV8313 [--pins 28] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys

from .symbol_pins import extract_symbol_pins
from .datasheet_pins import extract_datasheet_pins
from .diff import diff_pinout
from .search import search_symbol_candidates


def _parse_pages(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    out: list[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit():
            out.append(int(tok))
    return out or None


def _cmd_validate(args: argparse.Namespace) -> int:
    sym = extract_symbol_pins(args.sym, args.symbol)
    if not sym.get("success"):
        _emit({"success": False, "error": sym.get("error")}, args.json)
        return 1
    ds = extract_datasheet_pins(
        args.pdf,
        _parse_pages(args.pages),
        expected_pin_count=sym.get("pin_count", 0),
    )
    if not ds.get("success"):
        _emit({"success": False, "error": ds.get("error")}, args.json)
        return 1
    result = diff_pinout(sym["pins"], ds["pins"], strict=not args.lenient)
    payload = {
        "success": True,
        "symbol": sym,
        "datasheet_source": ds.get("source"),
        "fallback_used": ds.get("fallback_used"),
        "diff": result,
    }
    _emit(payload, args.json)
    return 0 if result["match"] else 2


def _cmd_search(args: argparse.Namespace) -> int:
    cands = search_symbol_candidates(args.query, args.pins, args.limit)
    _emit({"success": True, "candidates": cands}, args.json)
    return 0


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    if not payload.get("success"):
        print(f"ERROR: {payload.get('error')}")
        return
    if "candidates" in payload:
        for c in payload["candidates"]:
            print(f"{c['score']:.3f}  {c['lib_id']}  pins={c['pin_count']}  ({c['source']})")
        return
    diff = payload["diff"]
    print(f"match={diff['match']}  source={payload['datasheet_source']}"
          f"  fallback={payload['fallback_used']}")
    print(f"summary: {diff['summary']}")
    for row in diff["rows"]:
        if row["status"] != "match":
            print(f"  pin {row['num']}: {row['status']}  sym={row['sym']}  ds={row['ds']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kicad_mcp.generators.pinout")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="diff a symbol pinout against a datasheet")
    pv.add_argument("--sym", required=True)
    pv.add_argument("--symbol", required=True)
    pv.add_argument("--pdf", required=True)
    pv.add_argument("--pages", default=None)
    pv.add_argument("--lenient", action="store_true", help="strict=False diff")
    pv.add_argument("--json", action="store_true")
    pv.set_defaults(func=_cmd_validate)

    ps = sub.add_parser("search", help="rank local symbol candidates by name")
    ps.add_argument("query")
    ps.add_argument("--pins", type=int, default=0)
    ps.add_argument("--limit", type=int, default=10)
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=_cmd_search)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
