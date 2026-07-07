#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Demo-Kit-JSONs aus Circuit-Blocks + Rezepten regenerieren.

Eine Quelle (Nutzer-Auftrag „die zwei Orte verschmelzen"): die datenblatt-
geprüfte Schaltung lebt als Block unter
``kicad_mcp/resources/data/circuit_blocks/``; das Kit-Rezept unter
``…/demo_kits/recipes/``. Dieses Script schreibt die eingecheckten
``demo_kits/<key>.json`` neu; ``tests/test_kit_compose.py`` ist der
Drift-Wächter (Muster wie scripts/sync_bundle.py).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kicad_mcp.generators.circuit_block.kit_compose import compose_all  # noqa: E402

if __name__ == "__main__":
    written = compose_all()
    print(f"Komponiert: {', '.join(written)}")
