# SPDX-License-Identifier: GPL-3.0-or-later
"""
Universal KiCad library index — finds any symbol or footprint by name.

Instead of maintaining a manual mapping table, this module indexes all
.kicad_sym and .kicad_mod files from the KiCad installation and provides
fuzzy search by component name.

The index is built once and cached to disk (~200KB JSON file).
Subsequent lookups are instant.
"""

import json
import logging
import os
from pathlib import Path
import re
import time

logger = logging.getLogger(__name__)

# ── Library paths ───────────────────────────────────────────────────────────

def _env_path(var: str) -> Path | None:
    v = os.environ.get(var, "")
    return Path(v) if v else None

_SYM_DIRS = [
    _env_path("KICAD_SYMBOL_DIR"),
    Path("/mnt/c/Program Files/KiCad/10.0/share/kicad/symbols"),
    Path(r"C:\Program Files\KiCad\10.0\share\kicad\symbols"),
    Path("/usr/share/kicad/symbols"),
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
]

_FP_DIRS = [
    _env_path("KICAD_FOOTPRINT_DIR"),
    Path("/mnt/c/Program Files/KiCad/10.0/share/kicad/footprints"),
    Path(r"C:\Program Files\KiCad\10.0\share\kicad\footprints"),
    Path("/usr/share/kicad/footprints"),
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"),
]

_CACHE_DIR = Path(__file__).parent.parent / ".cache"
_SYM_INDEX_FILE = _CACHE_DIR / "symbol_index.json"
_FP_INDEX_FILE = _CACHE_DIR / "footprint_index.json"


def _find_dir(candidates: list[Path]) -> Path | None:
    for d in candidates:
        if d and str(d) and d.is_dir():
            return d
    return None


# ── Symbol Index ────────────────────────────────────────────────────────────

_sym_index: dict[str, str] | None = None  # name_upper → "Library:Symbol"


def _build_symbol_index(sym_dir: Path) -> dict[str, str]:
    """Scan all .kicad_sym files and build name → lib_id mapping."""
    index = {}
    for lib_file in sorted(sym_dir.glob("*.kicad_sym")):
        lib_name = lib_file.stem
        try:
            content = lib_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Find all top-level symbol definitions:
        # (symbol "SymbolName" ...) at indent level 1 (inside kicad_symbol_lib)
        for m in re.finditer(r'^\s{1,4}\(symbol "([^"]+)"', content, re.MULTILINE):
            sym_name = m.group(1)
            # Skip sub-units (e.g. "LM339_1_1", "LM339_0_1")
            # These have format "Name_unit_style" where unit and style are digits
            parts = sym_name.rsplit("_", 2)
            if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
                continue

            lib_id = f"{lib_name}:{sym_name}"
            # Index by exact name and uppercase
            index[sym_name.upper()] = lib_id

            # Also index common short forms:
            # "NE555P" → also index "NE555"
            # "LM358N" → also index "LM358"
            # Strip common package suffixes
            for suffix in ("P", "D", "N", "M", "T", "U", "xN", "xM", "xD", "xP"):
                if sym_name.upper().endswith(suffix.upper()) and len(sym_name) > len(suffix) + 2:
                    short = sym_name[:-len(suffix)].upper()
                    if short not in index:
                        index[short] = lib_id

    return index


def _load_symbol_index() -> dict[str, str]:
    global _sym_index
    if _sym_index is not None:
        return _sym_index

    # Try loading from cache
    if _SYM_INDEX_FILE.exists():
        try:
            cache_data = json.loads(_SYM_INDEX_FILE.read_text(encoding="utf-8"))
            cache_dir = cache_data.get("sym_dir", "")
            sym_dir = _find_dir(_SYM_DIRS)
            # Validate cache is for the same KiCad installation
            if sym_dir and str(sym_dir) == cache_dir:
                _sym_index = cache_data["index"]
                logger.info("Symbol index loaded from cache: %d entries", len(_sym_index))
                return _sym_index
        except (json.JSONDecodeError, KeyError) as exc:
            logger.debug("Symbol-Index-Cache unlesbar/veraltet, baue neu: %s", exc)

    # Build fresh index
    sym_dir = _find_dir(_SYM_DIRS)
    if not sym_dir:
        logger.warning("No KiCad symbol directory found")
        _sym_index = {}
        return _sym_index

    logger.info("Building symbol index from %s ...", sym_dir)
    t0 = time.time()
    _sym_index = _build_symbol_index(sym_dir)
    dt = time.time() - t0
    logger.info("Symbol index built: %d entries in %.1fs", len(_sym_index), dt)

    # Save cache
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _SYM_INDEX_FILE.write_text(json.dumps({
        "sym_dir": str(sym_dir),
        "count": len(_sym_index),
        "index": _sym_index,
    }, ensure_ascii=False), encoding="utf-8")

    return _sym_index


def find_symbol(name: str) -> str | None:
    """Find a KiCad symbol by component name.

    Tries: exact match, uppercase match, stripped suffixes, partial match.
    Returns "Library:Symbol" string or None.
    """
    index = _load_symbol_index()
    name_upper = name.upper().strip()

    # 1. Exact match
    if name_upper in index:
        return index[name_upper]

    # 2. Strip common prefixes/suffixes users might add
    for prefix in ("CD", "SN", "MC", "TI-"):
        stripped = name_upper.removeprefix(prefix)
        if stripped in index:
            return index[stripped]

    # 3. Add common suffixes to try package variants
    for suffix in ("P", "D", "N", "xN", "xP"):
        with_suffix = name_upper + suffix.upper()
        if with_suffix in index:
            return index[with_suffix]

    # 4. Partial match — name is contained in symbol name
    #    Only match if BOTH sides are substantial (>=4 chars) to avoid
    #    "C" matching "PIC16F84" or "L" matching "LM7805"
    if len(name_upper) >= 4:
        candidates = []
        for key, lib_id in index.items():
            if len(key) < 4:
                continue  # skip single-char symbols like "R", "C", "L", "D"
            if name_upper in key or key in name_upper:
                candidates.append((key, lib_id))
        if candidates:
            # Prefer exact length match, then shortest
            candidates.sort(key=lambda x: (abs(len(x[0]) - len(name_upper)), len(x[0])))
            return candidates[0][1]

    return None


# ── Footprint Index ─────────────────────────────────────────────────────────

_fp_index: dict[str, str] | None = None  # name_upper → "Library:Footprint"


def _build_footprint_index(fp_dir: Path) -> dict[str, str]:
    """Scan all .pretty directories and build name → fp_id mapping."""
    index = {}
    for lib_dir in sorted(fp_dir.glob("*.pretty")):
        lib_name = lib_dir.stem
        for fp_file in lib_dir.glob("*.kicad_mod"):
            fp_name = fp_file.stem
            fp_id = f"{lib_name}:{fp_name}"
            index[fp_name.upper()] = fp_id
    return index


def _load_footprint_index() -> dict[str, str]:
    global _fp_index
    if _fp_index is not None:
        return _fp_index

    if _FP_INDEX_FILE.exists():
        try:
            cache_data = json.loads(_FP_INDEX_FILE.read_text(encoding="utf-8"))
            fp_dir = _find_dir(_FP_DIRS)
            if fp_dir and str(fp_dir) == cache_data.get("fp_dir", ""):
                _fp_index = cache_data["index"]
                logger.info("Footprint index loaded from cache: %d entries", len(_fp_index))
                return _fp_index
        except (json.JSONDecodeError, KeyError) as exc:
            logger.debug("Footprint-Index-Cache unlesbar/veraltet, baue neu: %s", exc)

    fp_dir = _find_dir(_FP_DIRS)
    if not fp_dir:
        logger.warning("No KiCad footprint directory found")
        _fp_index = {}
        return _fp_index

    logger.info("Building footprint index from %s ...", fp_dir)
    t0 = time.time()
    _fp_index = _build_footprint_index(fp_dir)
    dt = time.time() - t0
    logger.info("Footprint index built: %d entries in %.1fs", len(_fp_index), dt)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _FP_INDEX_FILE.write_text(json.dumps({
        "fp_dir": str(fp_dir),
        "count": len(_fp_index),
        "index": _fp_index,
    }, ensure_ascii=False), encoding="utf-8")

    return _fp_index


def find_footprint(name: str) -> str | None:
    """Find a KiCad footprint by name.

    Returns "Library:Footprint" string or None.
    """
    index = _load_footprint_index()
    name_upper = name.upper().strip()

    if name_upper in index:
        return index[name_upper]

    # Try without library prefix
    if ":" in name:
        short = name.split(":", 1)[1].upper()
        if short in index:
            return index[short]

    # Partial match
    if len(name_upper) >= 5:
        for key, fp_id in index.items():
            if name_upper in key:
                return fp_id

    return None


def clear_index_cache():
    """Remove cached index files (for testing)."""
    global _sym_index, _fp_index
    _sym_index = None
    _fp_index = None
    if _SYM_INDEX_FILE.exists():
        _SYM_INDEX_FILE.unlink()
    if _FP_INDEX_FILE.exists():
        _FP_INDEX_FILE.unlink()
