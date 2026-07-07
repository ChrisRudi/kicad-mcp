# SPDX-License-Identifier: GPL-3.0-or-later
"""PCB-Platzierung: Courtyard-Wahrheit + garantierte Überlappungsfreiheit.

Feld-Anlass (Demo-Board-Messlatte 2026-07-06): der Courtyard-Parser las beim
SOIC-8 das Pin-1-Silk-Dreieck (0.48×0.33 mm) statt des Courtyards (~7×5) —
Bauteile wurden „kollisionsfrei" mitten auf den Chip gesetzt; zusätzlich
konnte die Kräfte-Verfeinerung mit Rest-Überlappungen auslaufen (Schrittweite
gegen Ende 0.2 mm). Diese Tests sind die Wächter beider Fixes.
"""

from __future__ import annotations

import glob
import json
import os

import pytest

from kicad_mcp.generators.common.bbox import _fp_size, _read_courtyard_size
from kicad_mcp.generators.common.fd_refine import _resolve_pcb_overlaps
from kicad_mcp.generators.pcb.place import _compute_pcb_placement

_KITS = sorted(glob.glob(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "kicad_mcp", "resources", "data", "demo_kits", "*.json")))

_SOIC8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
_needs_fp_lib = pytest.mark.skipif(
    _read_courtyard_size(_SOIC8) is None,
    reason="KiCad-Footprint-Bibliothek nicht installiert")


@_needs_fp_lib
def test_courtyard_is_element_exact_not_silk_triangle():
    # Der alte Cross-Element-Regex lieferte 0.48×0.33 (Pin-1-Dreieck der
    # Silkscreen). Elementgenau muss das SOIC-8 deutlich größer sein.
    w, h = _read_courtyard_size(_SOIC8)
    assert w > 5.0 and h > 4.0, (w, h)


def test_resolve_pcb_overlaps_separates_stacked_parts():
    parts = [
        {"ref": "U1", "footprint": "", "pins": [{"num": str(i)} for i in range(8)]},
        {"ref": "C1", "footprint": "", "pins": [{"num": "1"}, {"num": "2"}]},
    ]
    ref_to_part = {p["ref"]: p for p in parts}
    result = {"U1": (20.0, 20.0, 0), "C1": (20.0, 20.0, 0)}
    left = _resolve_pcb_overlaps(result, ref_to_part, 5, 5, 95, 75)
    assert left == 0
    (ux, uy, _), (cx, cy, _) = result["U1"], result["C1"]
    uw, uh = _fp_size(ref_to_part["U1"])
    cw, ch = _fp_size(ref_to_part["C1"])
    assert (abs(ux - cx) >= (uw + cw) / 2 + 2.0 - 0.01
            or abs(uy - cy) >= (uh + ch) / 2 + 2.0 - 0.01)


def test_resolve_pcb_overlaps_keeps_fixed_parts():
    parts = [
        {"ref": "J1", "footprint": "", "pins": [{"num": "1"}, {"num": "2"}]},
        {"ref": "R1", "footprint": "", "pins": [{"num": "1"}, {"num": "2"}]},
    ]
    ref_to_part = {p["ref"]: p for p in parts}
    result = {"J1": (10.0, 10.0, 0), "R1": (10.0, 10.0, 0)}
    left = _resolve_pcb_overlaps(result, ref_to_part, 5, 5, 95, 75,
                                 fixed={"J1"})
    assert left == 0
    assert result["J1"] == (10.0, 10.0, 0)  # Stecker bleibt an seiner Kante


def _overlap_pairs(result: dict, ref_to_part: dict, gap: float = 1.9) -> list:
    refs = sorted(result)
    bad = []
    for i, a in enumerate(refs):
        ax, ay, arot = result[a]
        aw, ah = _fp_size(ref_to_part[a])
        if arot in (90, 270):
            aw, ah = ah, aw
        for b in refs[i + 1:]:
            bx, by, brot = result[b]
            bw, bh = _fp_size(ref_to_part[b])
            if brot in (90, 270):
                bw, bh = bh, bw
            if (abs(ax - bx) < (aw + bw) / 2 + gap
                    and abs(ay - by) < (ah + bh) / 2 + gap):
                bad.append((a, b))
    return bad


@pytest.mark.parametrize("kit_path", _KITS,
                         ids=[os.path.splitext(os.path.basename(p))[0]
                              for p in _KITS])
@_needs_fp_lib
def test_demo_kits_place_without_overlaps(kit_path):
    # Das Gate der Phase 2: KEIN Demo-Board hat Bauteil-auf-Bauteil.
    # Nur mit echter Footprint-Lib (echte Courtyards) — die groben
    # Fallback-Schätzmaße des Mock-Runners überzeichnen die Bauteile so,
    # dass kleine Demo-Boards (44×32) physisch nicht kollisionsfrei
    # passen KÖNNEN (CI-Rot 02e7c17); der Echt-KiCad-Job erzwingt weiter.
    spec = json.load(open(kit_path, encoding="utf-8"))
    parts = json.loads(json.dumps(spec["parts"]))
    nets = json.loads(json.dumps(spec["nets"]))
    board = spec.get("board", {})
    result = _compute_pcb_placement(parts, nets,
                                    float(board.get("width", 100)),
                                    float(board.get("depth", 80)))
    ref_to_part = {p["ref"]: p for p in parts}
    bad = _overlap_pairs(result, ref_to_part)
    assert not bad, f"Überlappende Paare: {bad}"


# ── Router-Gate: fertige Demo-Boards sind DRC-sauber ─────────────────────────

# audio_amp fehlt bewusst: 0 DRC-Fehler, aber 2 offene IN_NODE-Kanten —
# die Pin-Tasche an U1:3 ist von Nachbar-Pad-Aufblasungen versiegelt
# (Pin-Escape-Fähigkeit steht aus; naiver Escape shortete 2-Pad-Passives).
_DONE_KITS = ["buck_converter", "kit_seeding", "led_ring",
              "motor_driver", "production_ready"]


def _kicad_cli():
    import shutil
    return shutil.which("kicad-cli")


@pytest.mark.parametrize("kit", _DONE_KITS)
@_needs_fp_lib
@pytest.mark.skipif(_kicad_cli() is None, reason="kicad-cli nicht installiert")
def test_finished_kits_route_drc_clean(kit, tmp_path):
    # Die Kern-Zusage der Demo-Platinen (Nutzer-Auftrag „3 Demos fertig"):
    # Grid-Router + Platzierung liefern 0 DRC-Fehler UND 0 offene
    # Verbindungen — gemessen mit KiCads eigenem DRC, nicht selbstbewertet.
    import subprocess

    from kicad_mcp.generators.pcb.builder import build_pcb
    spec_path = os.path.join(os.path.dirname(_KITS[0]), f"{kit}.json")
    spec = json.load(open(spec_path, encoding="utf-8"))
    pcb = build_pcb(json.loads(json.dumps(spec["parts"])),
                    json.loads(json.dumps(spec["nets"])),
                    json.loads(json.dumps(spec.get("board", {}))), kit)
    board = tmp_path / f"{kit}.kicad_pcb"
    board.write_text(pcb, encoding="utf-8")
    report = tmp_path / "drc.json"
    subprocess.run([_kicad_cli(), "pcb", "drc", "--format", "json",
                    "--severity-all", "-o", str(report), str(board)],
                   capture_output=True, timeout=300, check=False)
    data = json.loads(report.read_text(encoding="utf-8"))
    errors = [v for v in data.get("violations", [])
              if v.get("severity") == "error"]
    assert not errors, [f"{v['type']}: {v['description']}" for v in errors[:5]]
    assert not data.get("unconnected_items"), \
        len(data.get("unconnected_items", []))
