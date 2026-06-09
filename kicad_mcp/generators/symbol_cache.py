# SPDX-License-Identifier: GPL-3.0-or-later
"""
KiCad symbol cache — extracts real symbol definitions from .kicad_sym library files.

Used by schematic_builder to embed authentic KiCad symbols in generated schematics
instead of generic rectangle placeholders.

Resolution order: stock libraries (bundled with the KiCad install) first; on miss,
the user's global ``sym-lib-table`` is consulted so custom / third-party libraries
registered in KiCad Preferences are picked up automatically.
"""

from functools import lru_cache
import glob
import logging
import os
import re

from kicad_mcp.utils.path_env import detect_environment, to_local_path

logger = logging.getLogger(__name__)

# Standard KiCad symbol library search paths
_KICAD_SYM_DIRS: list[str] = []

_SYM_LIB_TABLE_CACHE: dict[str, str] | None = None

_NAME_KEY_RE = re.compile(r'\(name\s+"([^"]+)"\)')
_URI_KEY_RE = re.compile(r'\(uri\s+"([^"]+)"\)')


def _iter_sym_lib_blocks(content: str):
    """Yield balanced-paren ``(lib ...)`` blocks from a sym-lib-table file."""
    pos = 0
    while True:
        start = content.find("(lib", pos)
        if start == -1:
            return
        # Boundary check: ``(lib`` followed by whitespace, not ``(libname...``.
        nxt = start + 4
        if nxt >= len(content) or content[nxt] not in (" ", "\t", "\n", "\r"):
            pos = start + 1
            continue
        depth = 0
        for i in range(start, len(content)):
            if content[i] == "(":
                depth += 1
            elif content[i] == ")":
                depth -= 1
                if depth == 0:
                    yield content[start:i + 1]
                    pos = i + 1
                    break
        else:
            return


def _find_kicad_sym_dir() -> str | None:
    """Find the KiCad stock symbol library directory."""
    if _KICAD_SYM_DIRS:
        return _KICAD_SYM_DIRS[0]

    candidates = []

    # From environment
    env_path = os.environ.get("KICAD_SYMBOL_DIR")
    if env_path:
        candidates.append(env_path)

    # Windows standard paths
    for ver in ("10.0", "9.0", "8.0"):
        candidates.append(rf"C:\Program Files\KiCad\{ver}\share\kicad\symbols")
        # WSL mount
        candidates.append(f"/mnt/c/Program Files/KiCad/{ver}/share/kicad/symbols")

    # macOS
    candidates.append("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")

    # Linux
    candidates.append("/usr/share/kicad/symbols")
    candidates.append("/usr/local/share/kicad/symbols")

    for d in candidates:
        if os.path.isdir(d):
            _KICAD_SYM_DIRS.append(d)
            logger.info(f"Found KiCad symbol library at: {d}")
            return d

    logger.warning("KiCad symbol library directory not found")
    return None


def _find_user_sym_lib_tables() -> list[str]:
    """Return paths to the user's KiCad ``sym-lib-table`` config files.

    KiCad stores per-user library registrations in its config dir (one per
    KiCad major version). We look at the typical locations for the current
    runtime environment. Cross-environment access is supported: a WSL
    interpreter can read the Windows AppData copy via ``/mnt/c/...``.

    The ``KICAD_CONFIG_DIR`` env var, if set, is consulted first and takes
    precedence over all auto-detected locations.

    Returns:
        Existing ``sym-lib-table`` file paths in priority order. Empty list
        if no user config can be located (fresh KiCad install, no overrides).
    """
    paths: list[str] = []

    override = os.environ.get("KICAD_CONFIG_DIR", "").strip()
    if override:
        # Explicit override: trust the user, do not fall back to auto-detected
        # AppData/.config locations even if the override has no sym-lib-table.
        candidate = os.path.join(override, "sym-lib-table")
        return [candidate] if os.path.isfile(candidate) else []

    env = detect_environment()
    versions = ("10.0", "9.0", "8.0")

    if env == "windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            for ver in versions:
                paths.append(os.path.join(appdata, "kicad", ver, "sym-lib-table"))
    elif env == "wsl":
        # AppData lives on the Windows side; the WSL-side username does not
        # match the Windows username in general, so glob the Windows /Users
        # mount across all profiles.
        for ver in versions:
            paths.extend(sorted(glob.glob(
                f"/mnt/c/Users/*/AppData/Roaming/kicad/{ver}/sym-lib-table"
            )))
    elif env == "linux":
        home = os.path.expanduser("~")
        for ver in versions:
            paths.append(os.path.join(home, ".config", "kicad", ver, "sym-lib-table"))
    elif env == "darwin":
        home = os.path.expanduser("~")
        for ver in versions:
            paths.append(os.path.join(
                home, "Library", "Preferences", "kicad", ver, "sym-lib-table"
            ))

    return [p for p in paths if os.path.isfile(p)]


def _expand_lib_uri(uri: str) -> str | None:
    """Expand ``${VAR}`` placeholders in a sym-lib-table URI.

    Returns None when the URI references variables we cannot resolve cheaply
    (notably ``${KIPRJMOD}``, which is project-local and only meaningful
    inside a KiCad project context).
    """
    if "${KIPRJMOD}" in uri:
        return None
    expanded = os.path.expandvars(uri)
    # Unsubstituted ${...} markers mean a referenced env var was not set.
    if "${" in expanded:
        return None
    return expanded


def _load_user_sym_libs() -> dict[str, str]:
    """Parse the user's sym-lib-table(s) and return ``{lib_name: lib_path}``.

    Cached for the process lifetime. Library entries whose URI cannot be
    resolved to an existing file are skipped with a debug log message.
    """
    global _SYM_LIB_TABLE_CACHE
    if _SYM_LIB_TABLE_CACHE is not None:
        return _SYM_LIB_TABLE_CACHE

    out: dict[str, str] = {}
    for table_path in _find_user_sym_lib_tables():
        try:
            with open(table_path, encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            logger.warning(f"Could not read sym-lib-table at {table_path}: {exc}")
            continue

        for block in _iter_sym_lib_blocks(content):
            name_m = _NAME_KEY_RE.search(block)
            uri_m = _URI_KEY_RE.search(block)
            if not name_m or not uri_m:
                continue
            lib_name, raw_uri = name_m.group(1), uri_m.group(1)
            if lib_name in out:
                continue
            expanded = _expand_lib_uri(raw_uri)
            if expanded is None:
                logger.debug(
                    f"sym-lib-table: skipping '{lib_name}' — unresolvable URI {raw_uri}"
                )
                continue
            local_uri = to_local_path(expanded)
            if not os.path.isfile(local_uri):
                logger.debug(
                    f"sym-lib-table: '{lib_name}' URI does not exist on disk: {local_uri}"
                )
                continue
            out[lib_name] = local_uri
            logger.info(f"sym-lib-table: registered '{lib_name}' → {local_uri}")

    _SYM_LIB_TABLE_CACHE = out
    return out


def _reset_user_sym_libs_cache() -> None:
    """Test hook: drop the cached user-sym-lib-table parse."""
    global _SYM_LIB_TABLE_CACHE
    _SYM_LIB_TABLE_CACHE = None


def _extract_top_level_symbol(content: str, sym_name: str) -> str | None:
    """Extract a top-level symbol definition from .kicad_sym file content.

    Matches by balanced parentheses to extract the complete symbol block.
    Only matches top-level symbols (depth <= 1), not sub-symbols.
    """
    target = f'(symbol "{sym_name}"'
    pos = 0

    while True:
        start = content.find(target, pos)
        if start == -1:
            return None

        # Verify the target string ends at a boundary (not partial match)
        end_of_target = start + len(target)
        if end_of_target < len(content) and content[end_of_target] not in ('"', '\n', '\r', ' ', '\t'):
            pos = start + 1
            continue

        # Check this is a top-level symbol (depth <= 1 means inside kicad_symbol_lib)
        depth_before = 0
        for c in content[:start]:
            if c == '(':
                depth_before += 1
            elif c == ')':
                depth_before -= 1
        if depth_before > 1:
            pos = start + 1
            continue

        # Extract by balanced parens
        depth = 0
        for i in range(start, len(content)):
            if content[i] == '(':
                depth += 1
            elif content[i] == ')':
                depth -= 1
                if depth == 0:
                    return content[start:i + 1]

        return None


@lru_cache(maxsize=64)
def _read_lib_file(lib_path: str) -> str:
    """Read and cache a library file's contents."""
    with open(lib_path, encoding="utf-8") as f:
        return f.read()


def _resolve_symbol_from_lib(lib_path: str, sym_name: str, lib_id: str) -> str | None:
    """Extract ``sym_name`` from ``lib_path`` and produce a lib_symbols-ready
    snippet keyed under ``lib_id``.

    Handles the ``(extends "BaseSymbol")`` mechanism by inlining the parent's
    geometry under the derived name.
    """
    content = _read_lib_file(lib_path)
    sym_text = _extract_top_level_symbol(content, sym_name)

    if sym_text is None:
        logger.debug(f"Symbol '{sym_name}' not found in {lib_path}")
        return None

    # Handle (extends "BaseSymbol") — symbol inherits from another.
    # In .kicad_sch lib_symbols, extends does NOT work.  The simplest
    # reliable approach: take the complete base symbol, rename all
    # occurrences to the derived name, and update the Value property.
    extends_match = re.search(r'\(extends\s+"([^"]+)"\)', sym_text)
    if extends_match:
        base_name = extends_match.group(1)
        logger.info(f"Symbol '{sym_name}' extends '{base_name}' — using base with rename")

        base_text = _extract_top_level_symbol(content, base_name)
        if base_text:
            sym_text = base_text.replace(f'"{base_name}"', f'"{sym_name}"')
            sym_text = sym_text.replace(f'"{base_name}_', f'"{sym_name}_')
        else:
            logger.warning(f"Base symbol '{base_name}' not found — using as-is")

    # Rewrite the top-level symbol name to "Library:Symbol" format
    sym_text = sym_text.replace(
        f'(symbol "{sym_name}"',
        f'(symbol "{lib_id}"',
        1,
    )

    return sym_text


def get_real_symbol(lib_id: str) -> str | None:
    """Get the real KiCad symbol definition for a lib_id.

    Resolves the symbol by trying, in order:

      1. The stock KiCad library directory (bundled symbols, found via
         :func:`_find_kicad_sym_dir`).
      2. The user's global ``sym-lib-table`` (custom / third-party libraries
         registered in KiCad Preferences → Manage Symbol Libraries).

    Handles the KiCad ``(extends "BaseSymbol")`` mechanism: if the symbol
    inherits from another, the base symbol's geometry is inlined under the
    derived name so KiCad can render the full graphics inside ``lib_symbols``.

    Args:
        lib_id: KiCad library ID, e.g. ``"Device:R"``, ``"Amplifier_Operational:NE5532"``,
            or a custom-lib reference such as ``"MyCustomLib:MCU_X"``.

    Returns:
        The complete symbol definition with the top-level name rewritten to
        the ``Library:Symbol`` form, or ``None`` if the lib_id cannot be
        resolved in either source.
    """
    if ":" not in lib_id:
        return None

    lib_name, sym_name = lib_id.split(":", 1)

    # 1. Stock library lookup (cheap, hits for the vast majority of lib_ids).
    sym_dir = _find_kicad_sym_dir()
    if sym_dir:
        stock_path = os.path.join(sym_dir, f"{lib_name}.kicad_sym")
        if os.path.isfile(stock_path):
            resolved = _resolve_symbol_from_lib(stock_path, sym_name, lib_id)
            if resolved is not None:
                return resolved

    # 2. User-configured libraries (custom / third-party).
    user_libs = _load_user_sym_libs()
    custom_path = user_libs.get(lib_name)
    if custom_path:
        resolved = _resolve_symbol_from_lib(custom_path, sym_name, lib_id)
        if resolved is not None:
            return resolved

    logger.debug(f"lib_id '{lib_id}' not resolved in stock or user libraries")
    return None
