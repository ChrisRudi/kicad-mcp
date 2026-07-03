# SPDX-License-Identifier: GPL-3.0-or-later
"""Ratchet: no NEW cross-module import of a private (``_``-prefixed) name from
another ``kicad_mcp.tools.*`` module.

Such imports are the smell behind the "god module" / "secret shared library"
findings: a private helper in one tool module reached into by another module
(or, worse, by a ``generators/*`` module — a layer inversion). The fix is to
move the shared helper into ``utils/``. This test freezes the *known* remaining
cases in ``ALLOWED`` (each with a reason + where it will be resolved) and fails
on any new one, so the surface can only shrink.

Relative imports (``from .ipc_tools import _x``) are resolved to their absolute
module so they cannot slip past the check.
"""

from __future__ import annotations

import ast
import os

_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "kicad_mcp")

# (importer path relative to kicad_mcp/, source tools submodule, private name).
# Each entry is deliberately-deferred debt, not an endorsement — do not add to
# this set to make a new cross-module private import pass; move the helper to
# utils/ instead.
ALLOWED: set[tuple[str, str, str]] = {
    # god-module cascade: _parse_pcb_pads_per_ref pulls _find_block_end &
    # friends from the 5488-line pcb_patch_tools core → belongs with the
    # god-module split (S1), not this pass.
    ("generators/review/_pin_check.py", "pcb_patch_tools", "_parse_pcb_pads_per_ref"),
    # S2 live-IPC path: _connect_kicad / _require_editor are 29/184-line,
    # editor-auto-launch + presence-beacon entangled, and untestable headless
    # (no KiCad GUI). Deferred to avoid unverifiable live-path surgery.
    ("tools/ipc_interact_tools.py", "ipc_tools", "_connect_kicad"),
    ("tools/ipc_interact_tools.py", "ipc_tools", "_require_editor"),
    ("tools/ipc_markup_tools.py", "ipc_tools", "_connect_kicad"),
    # pre-existing: polar_grid reuses pcb_geometry's footprint indexer; a clean
    # utils/ move, but outside this refactor's scope.
    ("tools/polar_grid_tools.py", "pcb_geometry_tools", "_index_footprints"),
}


def _iter_py_files():
    for dirpath, _dirs, files in os.walk(_ROOT):
        norm = dirpath.replace(os.sep, "/")
        if "/plugin/" in norm or norm.endswith("/plugin"):
            continue
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def _resolve(module: str | None, level: int, importer_rel: str) -> str | None:
    """Resolve a (possibly relative) ImportFrom target to an absolute dotted
    module under ``kicad_mcp`` (or None if not resolvable / not our package)."""
    if level == 0:
        return module
    # importer_rel is like "tools/ipc_interact_tools.py" -> package parts
    parts = ("kicad_mcp/" + importer_rel).replace(".py", "").split("/")
    pkg = parts[:-1]  # drop the module filename → its package
    base = pkg[: len(pkg) - (level - 1)] if level > 1 else pkg
    return ".".join(base + ([module] if module else []))


def _find_violations() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in _iter_py_files():
        rel = os.path.relpath(path, _ROOT).replace(os.sep, "/")
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolve(node.module, node.level, rel)
            if not target or not target.startswith("kicad_mcp.tools."):
                continue
            src = target[len("kicad_mcp.tools."):]
            if rel == f"tools/{src}.py":
                continue  # a module importing from itself is fine
            for alias in node.names:
                if alias.name.startswith("_"):
                    found.add((rel, src, alias.name))
    return found


def test_no_new_cross_tool_private_imports():
    found = _find_violations()
    new = found - ALLOWED
    assert not new, (
        "New cross-module private import(s) of a tools/ helper detected — move "
        "the shared helper into kicad_mcp/utils/ instead of importing a _name "
        "across modules:\n  " + "\n  ".join(sorted(map(str, new)))
    )


def test_allowlist_has_no_stale_entries():
    """Keep ALLOWED honest: once a deferred case is fixed, its entry must be
    removed so the ratchet keeps tightening."""
    stale = ALLOWED - _find_violations()
    assert not stale, (
        "ALLOWED lists cross-tool private imports that no longer exist — remove "
        "them:\n  " + "\n  ".join(sorted(map(str, stale)))
    )
