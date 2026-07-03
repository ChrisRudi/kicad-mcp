# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-Ratchet: das Plugin-Paket darf sich selbst NIE absolut importieren.

Im Repo (und damit in CI) heißt das Paket zufällig ``plugin`` — installiert
wird es aber als ``scripting/plugins/claude_kicad`` (install_plugin.sh:
PKGNAME). Ein ``import plugin.x`` / ``__import__("plugin.x")`` läuft deshalb
in jedem Test grün und stirbt erst im Feld mit ``ModuleNotFoundError``.
Genau so war der 🧪-E2E-Button tot: der Klick starb VOR dem
Bestätigungsdialog, und wx schluckte den Traceback (Feld-Report 0.8.3).

Erlaubt sind nur relative Imports (``from . import x`` / ``from .x import``).
"""

from __future__ import annotations

import os
import re

PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "plugin")

# Absolute Selbst-Referenzen in jeder Form: import-Statements und dynamische
# __import__/importlib-Strings.
_FORBIDDEN = re.compile(
    r"(^\s*(from|import)\s+plugin\b)"
    r"|(__import__\(\s*[\"']plugin[.\"'])"
    r"|(import_module\(\s*[\"']plugin[.\"'])",
    re.MULTILINE)


def _plugin_sources():
    for name in sorted(os.listdir(PLUGIN_DIR)):
        if name.endswith(".py"):
            yield name, os.path.join(PLUGIN_DIR, name)


def test_no_absolute_self_imports_in_plugin_package():
    offenders = []
    for name, path in _plugin_sources():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for m in _FORBIDDEN.finditer(src):
            line = src.count("\n", 0, m.start()) + 1
            offenders.append(f"plugin/{name}:{line}: {m.group(0).strip()}")
    assert not offenders, (
        "Absoluter Selbst-Import im Plugin-Paket — installiert heißt das "
        "Paket claude_kicad, nicht 'plugin'; nur relative Imports nutzen:\n"
        + "\n".join(offenders))
