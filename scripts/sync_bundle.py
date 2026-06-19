#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sync the bundled MCP server ``plugin/mcp/kicad_mcp/`` from the canonical
``kicad_mcp/`` package.

Why this exists
---------------
``plugin/mcp/kicad_mcp/`` is the server the INSTALLED plugin actually runs:
``claude_action._mcp_root()`` resolves *bundled-first*, so a user's live plugin
executes this copy, not the repo's canonical ``kicad_mcp/``. The copy was
hand-maintained and drifted silently — whole features (the v0.4.0 pinout
package) and connection-hardening fixes never reached users because only
canonical was edited. There was no sync step and ``make_pcm_zip.py`` only
*packages* whatever already sits in ``plugin/mcp/``.

This script makes the bundle an exact mirror of canonical (minus caches and
byte-code), and ``tests/test_bundle_sync.py`` fails the suite if they ever
diverge again — so the drift cannot recur unnoticed.

Usage
-----
    python scripts/sync_bundle.py            # write the bundle to match canonical
    python scripts/sync_bundle.py --check    # report drift only, exit 1 if any

Pure stdlib; runs anywhere (no KiCad needed).
"""

from __future__ import annotations

import filecmp
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "kicad_mcp")
DST = os.path.join(ROOT, "plugin", "mcp", "kicad_mcp")

# Never mirror caches, runtime-installed deps, or byte-code — matches
# make_pcm_zip.py's package-exclude policy (the bundle ships source only).
EXCLUDE_DIRS = {"__pycache__", ".cache", "_deps"}
EXCLUDE_SUFFIX = (".pyc", ".pyo")


def _rel_files(base: str) -> set:
    """Relative POSIX paths of every shippable file under ``base``."""
    out = set()
    if not os.path.isdir(base):
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(EXCLUDE_SUFFIX):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), base)
            out.add(rel.replace(os.sep, "/"))
    return out


def drift() -> tuple:
    """``(missing, extra, differing)`` rel-path sets comparing canonical→bundle.

    * ``missing``   — in canonical, absent from the bundle
    * ``extra``     — in the bundle, gone from canonical
    * ``differing`` — present in both but not byte-identical
    """
    src = _rel_files(SRC)
    dst = _rel_files(DST)
    missing = src - dst
    extra = dst - src
    differing = {
        r for r in (src & dst)
        if not filecmp.cmp(os.path.join(SRC, r), os.path.join(DST, r),
                           shallow=False)
    }
    return missing, extra, differing


def sync() -> tuple:
    """Make the bundle an exact mirror of canonical. Returns the drift that was
    repaired ``(missing, extra, differing)``."""
    missing, extra, differing = drift()
    for rel in sorted(missing | differing):
        dst = os.path.join(DST, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(SRC, rel), dst)
    for rel in sorted(extra):
        os.remove(os.path.join(DST, rel))
    # prune now-empty dirs (bottom-up)
    for dirpath, _dirnames, _filenames in os.walk(DST, topdown=False):
        if os.path.isdir(dirpath) and not os.listdir(dirpath):
            os.rmdir(dirpath)
    return missing, extra, differing


def main(argv) -> int:
    missing, extra, differing = drift()
    if "--check" in argv:
        total = len(missing) + len(extra) + len(differing)
        if not total:
            print("Bundle ist synchron mit kicad_mcp/.")
            return 0
        print(f"Bundle-Drift: {len(missing)} fehlend, {len(extra)} überzählig, "
              f"{len(differing)} abweichend")
        for rel in sorted(missing):
            print("  fehlt :", rel)
        for rel in sorted(extra):
            print("  extra :", rel)
        for rel in sorted(differing):
            print("  diff  :", rel)
        print("\nBeheben mit: python scripts/sync_bundle.py")
        return 1
    sync()
    print(f"Synchronisiert: {len(missing)} hinzugefügt, {len(differing)} "
          f"aktualisiert, {len(extra)} entfernt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
