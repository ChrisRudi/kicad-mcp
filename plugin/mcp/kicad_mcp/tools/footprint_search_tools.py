# SPDX-License-Identifier: GPL-3.0-or-later
"""
Intelligent footprint search across the bundled KiCad libraries.

Generators that emit ``.kicad_pcb`` from scratch frequently re-create custom
footprint geometries (placeholders or hand-rolled S-expressions) even though
KiCad already ships a more accurate built-in counterpart. This module turns
the bundled ``share/kicad/footprints/*.pretty/*.kicad_mod`` tree into a
queryable index so callers (LLMs, generators, or developers) can ask:

  * "Is there a built-in footprint that matches this name pattern?"
  * "Give me everything with N pads and roughly this body size."
  * "I have a custom ``.kicad_mod`` here — which built-in is closest?"

The index is computed once on demand, cached as JSON in ``~/.kicad_mcp/``
and refreshed when the source mtime changes. All tools fall back gracefully
when the library cannot be located — they return a clear ``error`` and a
hint to set ``KICAD_LIB_ROOT`` or pass ``library_root`` explicitly.

Tools registered:
  * ``index_kicad_footprints`` — Build/refresh the index. Idempotent.
  * ``search_footprints`` — Fuzzy + substring search by lib/footprint name.
  * ``find_footprint_by_specs`` — Filter by pad count, package family, or
    body bounding box.
  * ``suggest_builtin_for_custom`` — Given a custom ``.kicad_mod`` (or its
    text contents), return ranked built-in candidates that match its
    pad-count and body-size, plus a confidence score.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.path_env import kicad_lib_root, to_local_path


# ---------------------------------------------------------------------------
# Default paths + cache location
# ---------------------------------------------------------------------------

CACHE_DIR = os.path.expanduser("~/.kicad_mcp")
INDEX_FILE = os.path.join(CACHE_DIR, "footprint_index.json")


def _default_kicad_lib_root() -> str:
    """Delegates to ``utils.path_env.kicad_lib_root`` so detection +
    ``KICAD_LIB_ROOT`` overrides stay centralised across the codebase."""
    root = kicad_lib_root()
    if root:
        return root
    candidates = [
        os.environ.get("KICAD_LIB_ROOT", ""),
        r"C:\Program Files\KiCad\10.0\share\kicad\footprints",
        r"C:\Program Files\KiCad\9.0\share\kicad\footprints",
        "/usr/share/kicad/footprints",
        "/usr/local/share/kicad/footprints",
        "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints",
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return ""


# ---------------------------------------------------------------------------
# Index records + helpers
# ---------------------------------------------------------------------------


@dataclass
class FootprintRecord:
    lib: str                  # e.g. "Capacitor_SMD"
    name: str                 # e.g. "C_0402_1005Metric"
    full_id: str              # "lib:name"
    path: str                 # absolute .kicad_mod path
    pad_count: int            # number of (pad …) blocks
    smd_pads: int             # smd pads (for hand-soldering hint)
    tht_pads: int             # thru-hole / through pads
    body_w_mm: float          # F.Fab/F.CrtYd bounding box width
    body_h_mm: float          # F.Fab/F.CrtYd bounding box height
    package_family: str       # heuristic: "0402", "SOT-23", "QFN-28", …
    description: str = ""     # free text from (descr "…") if present
    tags: list[str] = field(default_factory=list)


PAD_RE = re.compile(r'\(pad\s+"[^"]+"\s+(smd|thru_hole|np_thru_hole|connect)\s')
DESCR_RE = re.compile(r'\(descr\s+"([^"]*)"\)')
TAGS_RE = re.compile(r'\(tags\s+"([^"]*)"\)')

# Coordinates inside ``(fp_poly …)`` / ``(fp_line …)`` / ``(fp_rect …)`` /
# ``(fp_arc …)`` blocks. KiCad uses ``(xy …)`` inside polygons but
# ``(start …) (end …)`` inside lines / rectangles, so we accept all three.
COORD_RE = re.compile(
    r'\((?:xy|start|end)\s+([-\d.]+)\s+([-\d.]+)\)',
)
LINE_LAYER_RE = re.compile(
    r'\(fp_(?:line|poly|rect|arc)[\s\S]*?\(layer\s+"([^"]+)"\)', re.MULTILINE,
)


def _detect_package_family(name: str) -> str:
    """Heuristic: extract a recognizable package family from the FP name."""
    upper = name.upper()
    # Order matters: longer / more specific patterns first.
    patterns = [
        r"QFN-?\d+", r"DFN-?\d+", r"BGA-?\d+", r"LGA-?\d+",
        r"HTSSOP-?\d+", r"TSSOP-?\d+", r"SSOP-?\d+", r"MSOP-?\d+",
        r"SOIC-?\d+", r"SOP-?\d+",
        r"SOT-23-?\d+", r"SOT-23\b", r"SOT-89", r"SOT-223",
        r"DIP-?\d+", r"SIP-?\d+",
        r"DPAK", r"D2PAK", r"TO-220", r"TO-247", r"TO-92", r"TO-252",
        # USB / connector families need to win over the chip-resistor regex
        # below, so list them first.
        r"USB[_-]?C", r"USB[_-]?A", r"USB[_-]?B",
        r"RJ[_-]?45", r"RJ[_-]?11",
        # Imperial chip-resistor/cap codes: 0201, 0402, 0603, 0805, 1206,
        # 1210, 2010, 2512 etc. KiCad names the FP "C_0402_1005Metric" so the
        # imperial code sits between two underscores; ``\b`` cannot detect
        # that boundary (``_`` is a word char), so use look-arounds against
        # alphanumerics instead.
        r"(?<![A-Z\d])(?:0201|0402|0603|0805|1206|1210|1812|2010|2512|3216|3225)(?![A-Z\d])",
    ]
    for p in patterns:
        m = re.search(p, upper)
        if m:
            return m.group(0).replace("-", "").replace("_", "")
    # Fall back: leading alphabetic chunk before first digit.
    m = re.match(r"([A-Z]+)", upper)
    return m.group(1) if m else ""


def _parse_one_kicad_mod(path: str) -> FootprintRecord | None:
    """Parse a single ``.kicad_mod`` file into a FootprintRecord."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None

    # Prefer the footprint name embedded in the S-expression header (more
    # authoritative than the filename, which may be renamed by the user).
    name_m = re.match(r'\(footprint\s+"([^"]+)"', text)
    if name_m:
        name = name_m.group(1)
    else:
        name = os.path.splitext(os.path.basename(path))[0]
    lib = os.path.basename(os.path.dirname(path)).removesuffix(".pretty")

    pad_count = 0
    smd = 0
    tht = 0
    for m in PAD_RE.finditer(text):
        pad_count += 1
        kind = m.group(1)
        if kind == "smd":
            smd += 1
        elif kind in ("thru_hole", "np_thru_hole"):
            tht += 1

    # Body size from F.Fab geometry if available, else F.CrtYd, else fallback
    # to the union of all (xy …) coordinates in the file.
    body_xs: list[float] = []
    body_ys: list[float] = []
    for layer_target in ("F.Fab", "F.CrtYd"):
        for shape_m in re.finditer(
            r"\(fp_(?:line|poly|rect|arc)[\s\S]*?\(layer\s+\"" + re.escape(layer_target) + r"\"\)",
            text,
        ):
            for cm in COORD_RE.finditer(shape_m.group(0)):
                body_xs.append(float(cm.group(1)))
                body_ys.append(float(cm.group(2)))
        if body_xs:
            break
    if not body_xs:
        # Fall back to bounding box of all coords in the file (rough but always
        # produces something usable for ranking).
        for cm in COORD_RE.finditer(text):
            body_xs.append(float(cm.group(1)))
            body_ys.append(float(cm.group(2)))

    body_w = (max(body_xs) - min(body_xs)) if body_xs else 0.0
    body_h = (max(body_ys) - min(body_ys)) if body_ys else 0.0

    descr_m = DESCR_RE.search(text)
    descr = descr_m.group(1) if descr_m else ""
    tags_m = TAGS_RE.search(text)
    tags = tags_m.group(1).split() if tags_m else []

    return FootprintRecord(
        lib=lib,
        name=name,
        full_id=f"{lib}:{name}",
        path=path,
        pad_count=pad_count,
        smd_pads=smd,
        tht_pads=tht,
        body_w_mm=round(body_w, 3),
        body_h_mm=round(body_h, 3),
        package_family=_detect_package_family(name),
        description=descr,
        tags=tags,
    )


def _scan_library(library_root: str) -> list[FootprintRecord]:
    """Walk a KiCad-style library root and parse every ``.kicad_mod``."""
    records: list[FootprintRecord] = []
    if not os.path.isdir(library_root):
        return records
    for entry in os.listdir(library_root):
        if not entry.endswith(".pretty"):
            continue
        sub = os.path.join(library_root, entry)
        if not os.path.isdir(sub):
            continue
        for mod in os.listdir(sub):
            if not mod.endswith(".kicad_mod"):
                continue
            rec = _parse_one_kicad_mod(os.path.join(sub, mod))
            if rec is not None:
                records.append(rec)
    return records


def _index_signature(library_root: str) -> str:
    """Cheap signature for cache invalidation: count + max mtime."""
    if not os.path.isdir(library_root):
        return "missing"
    files = 0
    latest = 0.0
    for entry in os.listdir(library_root):
        if not entry.endswith(".pretty"):
            continue
        sub = os.path.join(library_root, entry)
        if not os.path.isdir(sub):
            continue
        try:
            files += len(os.listdir(sub))
        except OSError:
            # unlesbares .pretty-Verzeichnis fließt nicht in die Signatur ein
            pass
        try:
            mt = os.path.getmtime(sub)
            if mt > latest:
                latest = mt
        except OSError:
            # mtime nicht lesbar — Verzeichnis für die Signatur ignorieren
            pass
    return f"{library_root}|{files}|{int(latest)}"


def _load_or_build_index(
    library_root: str, force_rebuild: bool = False,
) -> tuple[list[FootprintRecord], str, bool]:
    """Return ``(records, index_path, rebuilt)``.

    Reuses the cached JSON when its embedded signature matches the live tree.
    """
    sig = _index_signature(library_root)

    if not force_rebuild and os.path.isfile(INDEX_FILE):
        try:
            with open(INDEX_FILE, encoding="utf-8") as fh:
                payload = json.load(fh)
            if payload.get("signature") == sig:
                records = [FootprintRecord(**r) for r in payload.get("records", [])]
                return records, INDEX_FILE, False
        except Exception:
            # Index-Cache defekt/unlesbar — unten frisch scannen
            pass

    records = _scan_library(library_root)
    os.makedirs(CACHE_DIR, exist_ok=True)
    payload = {
        "signature": sig,
        "library_root": library_root,
        "built_at": time.time(),
        "record_count": len(records),
        "records": [asdict(r) for r in records],
    }
    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        # best effort: Cache-Write optional, Index bleibt im Speicher
        pass
    return records, INDEX_FILE, True


# ---------------------------------------------------------------------------
# Search algorithms
# ---------------------------------------------------------------------------


def _score_name_match(query: str, record: FootprintRecord) -> float:
    """Score a record against a name query in [0, 1]. Combines exact match,
    substring containment and ``difflib`` similarity for a robust ranking
    that tolerates minor typos / casing.
    """
    q_lower = query.lower()
    full = record.full_id.lower()
    name = record.name.lower()
    if q_lower == full or q_lower == name:
        return 1.0
    score = 0.0
    if q_lower in full:
        score = max(score, 0.85)
    if q_lower in name:
        score = max(score, 0.9)
    # SequenceMatcher on the FP name (not lib:name) — "C_0402" finds a lot.
    sm = difflib.SequenceMatcher(None, q_lower, name).ratio()
    score = max(score, sm)
    # Bonus when the package family matches.
    pf = record.package_family.lower()
    if pf and pf in q_lower:
        score = min(1.0, score + 0.1)
    # Penalize hand-solder variants slightly so the canonical FP wins on ties.
    if "hand" in name or "handsolder" in name:
        score *= 0.97
    return score


def _spec_match(
    record: FootprintRecord,
    pad_count: int | None,
    package: str | None,
    body_w_mm: float | None,
    body_h_mm: float | None,
    body_tolerance_mm: float,
) -> float | None:
    """Return a [0,1] score if every supplied filter accepts the record,
    else ``None``."""
    if pad_count is not None and record.pad_count != pad_count:
        return None
    if package and package.lower() not in record.package_family.lower() \
            and package.lower() not in record.name.lower():
        return None
    if body_w_mm is not None:
        if abs(record.body_w_mm - body_w_mm) > body_tolerance_mm:
            return None
    if body_h_mm is not None:
        if abs(record.body_h_mm - body_h_mm) > body_tolerance_mm:
            return None
    # Score: how close the body size is to the ask.
    score = 1.0
    if body_w_mm is not None and record.body_w_mm:
        delta = abs(record.body_w_mm - body_w_mm) / max(record.body_w_mm, 0.001)
        score *= max(0.0, 1.0 - delta)
    if body_h_mm is not None and record.body_h_mm:
        delta = abs(record.body_h_mm - body_h_mm) / max(record.body_h_mm, 0.001)
        score *= max(0.0, 1.0 - delta)
    return score


def _score_custom_match(
    candidate: FootprintRecord,
    custom_pad_count: int,
    custom_body_w: float,
    custom_body_h: float,
) -> float:
    """Confidence that a candidate built-in matches a given custom footprint.
    Combines pad-count parity (hard requirement) with body-size proximity and
    a small penalty for hand-solder variants.
    """
    if candidate.pad_count != custom_pad_count:
        return 0.0
    score = 0.5  # base score for correct pin count
    # Body-size similarity (within 50% on each side scales score linearly).
    def proximity(a: float, b: float) -> float:
        if a <= 0 and b <= 0:
            return 1.0
        bigger = max(a, b)
        if bigger == 0:
            return 1.0
        return max(0.0, 1.0 - abs(a - b) / bigger)
    score += 0.25 * proximity(candidate.body_w_mm, custom_body_w)
    score += 0.25 * proximity(candidate.body_h_mm, custom_body_h)
    name_lower = candidate.name.lower()
    if "hand" in name_lower or "handsolder" in name_lower:
        score *= 0.95
    return score


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register_footprint_search_tools(mcp: FastMCP) -> None:
    """Register footprint search/discovery tools with the MCP server."""

    @mcp.tool()
    def index_kicad_footprints(
        library_root: str = "",
        force_rebuild: bool = False,
    ) -> dict[str, Any]:
        """Build (or refresh) the local footprint index.

        The index covers every ``.kicad_mod`` file under
        ``<library_root>/*.pretty/`` and is cached as JSON in
        ``~/.kicad_mcp/footprint_index.json``. Subsequent calls reuse the
        cache when it is still in sync with the on-disk library mtime;
        ``force_rebuild=True`` skips the cache.

        Args:
            library_root: KiCad ``footprints/`` directory. If empty falls
                back to ``KICAD_LIB_ROOT`` env var, then well-known install
                paths.
            force_rebuild: Ignore any cached index and re-scan from scratch.
        """
        library_root = to_local_path(library_root) if library_root else ""
        root = library_root or _default_kicad_lib_root()
        if not root:
            return {
                "success": False,
                "error": "KiCad footprint root not found. Pass library_root or "
                         "set KICAD_LIB_ROOT.",
            }
        if not os.path.isdir(root):
            return {"success": False, "error": f"Not a directory: {root}"}
        records, index_path, rebuilt = _load_or_build_index(root, force_rebuild)
        libs = sorted({r.lib for r in records})
        return {
            "success": True,
            "library_root": root,
            "rebuilt": rebuilt,
            "index_path": index_path,
            "record_count": len(records),
            "library_count": len(libs),
            "libraries": libs[:50] + (["…"] if len(libs) > 50 else []),
        }

    @mcp.tool()
    def search_footprints(
        query: str,
        max_results: int = 10,
        library_root: str = "",
    ) -> dict[str, Any]:
        """Fuzzy / substring search across all indexed footprint names.

        Use this when you know roughly what a footprint is called (a name,
        package code, or connector type like ``"C_0402"`` or ``"USB-C"``) and
        want the closest built-in matches ranked by name similarity.

        Tries (in order) exact match, ``lib:name`` substring, ``name``
        substring, and finally a SequenceMatcher similarity. Results are
        ranked by score in ``[0,1]`` and capped at ``max_results``.

        Args:
            query: Free-text needle, e.g. ``"C_0402"`` or ``"USB-C"``.
            max_results: Max number of hits to return.
            library_root: Optional library override, see ``index_kicad_footprints``.
        """
        library_root = to_local_path(library_root) if library_root else ""
        root = library_root or _default_kicad_lib_root()
        if not root:
            return {
                "success": False,
                "error": "KiCad footprint root not found.",
            }
        records, _, _ = _load_or_build_index(root)
        scored = []
        for r in records:
            s = _score_name_match(query, r)
            if s > 0.3:  # cut-off below which the match is meaningless
                scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_results]
        return {
            "success": True,
            "query": query,
            "result_count": len(top),
            "total_records": len(records),
            "results": [
                {
                    "score": round(s, 3),
                    "full_id": r.full_id,
                    "lib": r.lib,
                    "name": r.name,
                    "pad_count": r.pad_count,
                    "body_w_mm": r.body_w_mm,
                    "body_h_mm": r.body_h_mm,
                    "package_family": r.package_family,
                    "description": r.description,
                    "path": r.path,
                }
                for s, r in top
            ],
        }

    @mcp.tool()
    def find_footprint_by_specs(
        pad_count: int | None = None,
        package: str = "",
        body_w_mm: float | None = None,
        body_h_mm: float | None = None,
        body_tolerance_mm: float = 0.5,
        max_results: int = 10,
        library_root: str = "",
    ) -> dict[str, Any]:
        """Filter the footprint index by hard specs (pad count, package
        family, body bounding box) and rank what is left by body-size
        proximity to the requested dimensions.

        Use this when you do not know the footprint name but can describe its
        physical specs (pad count, package family, and/or body dimensions) and
        want candidates that fit those constraints.

        At least one of ``pad_count``, ``package`` or the body-size pair
        must be supplied — empty queries return an error.

        Args:
            pad_count: Required pad count (exact match).
            package: Substring matched against the auto-detected package
                family or the footprint name (e.g. ``"QFN"``, ``"SOT-23"``,
                ``"0402"``).
            body_w_mm: Target body width.
            body_h_mm: Target body height.
            body_tolerance_mm: Reject candidates whose dimension differs by
                more than this (per axis).
            max_results: Max number of hits.
            library_root: Optional library override.
        """
        if pad_count is None and not package and body_w_mm is None and body_h_mm is None:
            return {
                "success": False,
                "error": "Specify at least one of pad_count, package, body_w_mm or body_h_mm.",
            }
        library_root = to_local_path(library_root) if library_root else ""
        root = library_root or _default_kicad_lib_root()
        if not root:
            return {"success": False, "error": "KiCad footprint root not found."}
        records, _, _ = _load_or_build_index(root)
        scored = []
        for r in records:
            s = _spec_match(
                r, pad_count, package or None, body_w_mm, body_h_mm,
                body_tolerance_mm,
            )
            if s is None:
                continue
            scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_results]
        return {
            "success": True,
            "filters": {
                "pad_count": pad_count, "package": package or None,
                "body_w_mm": body_w_mm, "body_h_mm": body_h_mm,
                "body_tolerance_mm": body_tolerance_mm,
            },
            "result_count": len(top),
            "results": [
                {
                    "score": round(s, 3),
                    "full_id": r.full_id,
                    "lib": r.lib,
                    "name": r.name,
                    "pad_count": r.pad_count,
                    "body_w_mm": r.body_w_mm,
                    "body_h_mm": r.body_h_mm,
                    "package_family": r.package_family,
                    "description": r.description,
                    "path": r.path,
                }
                for s, r in top
            ],
        }

    @mcp.tool()
    def suggest_builtin_for_custom(
        custom_path: str = "",
        custom_text: str = "",
        max_results: int = 5,
        library_root: str = "",
    ) -> dict[str, Any]:
        """Given a custom ``.kicad_mod`` (path or raw text), suggest the best
        matching built-in footprint(s).

        Useful when a generator emitted a one-off custom footprint by
        accident and KiCad already ships a more accurate one. Pass either
        ``custom_path`` (preferred) or ``custom_text``. Returned candidates
        carry a ``confidence`` in ``[0,1]`` plus a ``recommended_tag`` that
        can go directly into a placeholder Value string for
        ``resolve_pcb_footprints``.

        Args:
            custom_path: Path to a ``.kicad_mod`` to analyse.
            custom_text: Inline S-expression text (alternative to ``custom_path``).
            max_results: Max candidates returned.
            library_root: Optional library override.
        """
        if not custom_path and not custom_text:
            return {
                "success": False,
                "error": "Provide either custom_path or custom_text.",
            }
        custom_path = to_local_path(custom_path) if custom_path else ""
        library_root = to_local_path(library_root) if library_root else ""
        # Build a temporary record from the custom input
        if custom_path:
            if not os.path.isfile(custom_path):
                return {"success": False, "error": f"Not found: {custom_path}"}
            tmp = _parse_one_kicad_mod(custom_path)
            if tmp is None:
                return {"success": False, "error": "Could not parse custom file."}
        else:
            tmp_path = os.path.join(CACHE_DIR, "_inline_custom.kicad_mod")
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(custom_text)
            tmp = _parse_one_kicad_mod(tmp_path)
            try:
                os.unlink(tmp_path)
            except OSError:
                # best effort: Temp-Datei ggf. schon entfernt
                pass
            if tmp is None:
                return {"success": False, "error": "Could not parse custom text."}

        root = library_root or _default_kicad_lib_root()
        if not root:
            return {"success": False, "error": "KiCad footprint root not found."}
        records, _, _ = _load_or_build_index(root)

        # Two complementary signals: hard pad-count match + body-size proximity.
        scored: list[tuple[float, FootprintRecord]] = []
        for r in records:
            score = _score_custom_match(
                r, tmp.pad_count, tmp.body_w_mm, tmp.body_h_mm,
            )
            if score <= 0.0:
                continue
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_results]
        return {
            "success": True,
            "custom": {
                "name_hint": tmp.name,
                "pad_count": tmp.pad_count,
                "body_w_mm": tmp.body_w_mm,
                "body_h_mm": tmp.body_h_mm,
                "package_family": tmp.package_family,
            },
            "candidates": [
                {
                    "confidence": round(s, 3),
                    "full_id": r.full_id,
                    "lib": r.lib,
                    "name": r.name,
                    "pad_count": r.pad_count,
                    "body_w_mm": r.body_w_mm,
                    "body_h_mm": r.body_h_mm,
                    "recommended_tag": f"[{r.lib}:{r.name}]",
                    "path": r.path,
                }
                for s, r in top
            ],
        }
