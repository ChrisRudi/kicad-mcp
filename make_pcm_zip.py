#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a KiCad PCM ("Plugin and Content Manager") archive of the plugin.

GitHub's auto-generated repo ZIP is NOT a valid KiCad add-on: it wraps the
whole repo in a ``<repo>-<branch>/`` folder. KiCad's PCM "Install from File"
needs a specific layout instead:

    metadata.json            (at the archive ROOT, PCM v1 schema)
    plugins/                 (the action-plugin package: every plugin/*.py +
                              the bundled mcp/ server)
    resources/icon.png       (the toolbar icon)

This script assembles exactly that from ``plugin/`` and writes
``dist/claude_kicad-<version>-pcm.zip``. Run it, then in KiCad:
Plugin and Content Manager → Install from File… → pick the zip.

Pure stdlib; runs anywhere (no KiCad needed).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(ROOT, "plugin")
DIST_DIR = os.path.join(ROOT, "dist")

IDENTIFIER = "com.github.chrisrudi.claude-kicad"
NAME = "Claude für KiCad"
HOMEPAGE = "https://github.com/ChrisRudi/kicad-mcp"

# Never ship caches, the runtime-installed deps, or tests inside the package.
_EXCLUDE_DIRS = {"__pycache__", "_deps", ".cache"}
_EXCLUDE_SUFFIX = (".pyc", ".pyo")


def _version() -> str:
    text = open(os.path.join(PLUGIN_DIR, "version.py"), encoding="utf-8").read()
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise SystemExit("could not read plugin/version.py __version__")
    return m.group(1)


def _iter_plugin_files():
    """Yield ``(abs_path, arcname_under_plugins)`` for every shipped file."""
    for dirpath, dirnames, filenames in os.walk(PLUGIN_DIR):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(_EXCLUDE_SUFFIX):
                continue
            ap = os.path.join(dirpath, fn)
            rel = os.path.relpath(ap, PLUGIN_DIR).replace(os.sep, "/")
            yield ap, rel


def _metadata(version: str, install_size: int) -> dict:
    return {
        "$schema": "https://go.kicad.org/pcm/schemas/v1",
        "name": NAME,
        "description": "Chat with Claude about your open KiCad board (kicad-mcp).",
        "description_full": (
            "A toolbar button in the PCB editor that opens a chat panel wired "
            "to the bundled kicad-mcp server and the open board. Each message "
            "runs one headless Claude Code turn against the board — the user's "
            "Claude subscription, no API key. Clickable references/nets/pins/"
            "layers/coordinates cross-probe back into the editor."
        ),
        "identifier": IDENTIFIER,
        "type": "plugin",
        "author": {"name": "ChrisRudi",
                   "contact": {"web": HOMEPAGE}},
        "license": "GPL-3.0-or-later",
        "resources": {"homepage": HOMEPAGE},
        "versions": [
            {
                "version": version,
                # pre-1.0 + active development → "testing", not "stable"
                "status": "testing",
                # minimum KiCad; the plugin's own preflight enforces 10.0
                "kicad_version": "9.0",
                "install_size": install_size,
            }
        ],
    }


def build() -> str:
    version = _version()
    files = list(_iter_plugin_files())
    install_size = sum(os.path.getsize(ap) for ap, _ in files)
    icon = os.path.join(PLUGIN_DIR, "icon.png")

    os.makedirs(DIST_DIR, exist_ok=True)
    out = os.path.join(DIST_DIR, f"claude_kicad-{version}-pcm.zip")
    meta = _metadata(version, install_size)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("metadata.json", json.dumps(meta, indent=2,
                                               ensure_ascii=False))
        for ap, rel in files:
            z.write(ap, "plugins/" + rel)        # plugin package under plugins/
        if os.path.isfile(icon):
            z.write(icon, "resources/icon.png")  # PCM toolbar icon

    sha = hashlib.sha256(open(out, "rb").read()).hexdigest()
    size = os.path.getsize(out)
    print(f"built {out}")
    print(f"  files: {len(files)}  install_size: {install_size} bytes")
    print(f"  zip:   {size} bytes  sha256: {sha}")
    print("Install in KiCad: Plugin and Content Manager → Install from File…")
    return out


if __name__ == "__main__":
    build()
