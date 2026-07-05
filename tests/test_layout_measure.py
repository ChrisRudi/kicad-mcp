# SPDX-License-Identifier: GPL-3.0-or-later
"""Die objektive Schaltplan-Metrik (`generators/schematic/layout_measure`) misst
das FERTIGE .kicad_sch (inkl. Labels/Drähte). Kern-Anker: die professionellen
KiCad-Referenz-Schaltbilder müssen **badness 0** erreichen — sonst ist die
Metrik nicht am Goldstandard geeicht und als Fitness untauglich."""

from __future__ import annotations

import os

import pytest

from kicad_mcp.generators.schematic import layout_measure as lm

_REF_DIR = os.path.join(os.path.dirname(__file__), "data", "reference_schematics")
_REFS = ("sallen_key", "rectifier")


@pytest.mark.parametrize("ref", _REFS)
def test_professional_reference_scores_zero(ref):
    """Der Goldstandard: ein Profi-Schaltbild hat keine Überlappungen, keine
    Label-auf-Bauteil, keine echten Kreuzungen, keine Diagonalen → badness 0."""
    m = lm.measure_file(os.path.join(_REF_DIR, f"{ref}.kicad_sch"))
    d = m.as_dict()
    assert d["comp_overlaps"] == 0, m.details
    assert d["label_overlaps"] == 0, m.details
    assert d["label_wrong_dir"] == 0, m.details
    assert d["wire_crossings"] == 0
    assert d["diag_wires"] == 0
    assert m.badness() == 0.0


def test_reference_actually_parsed():
    # Sanity: die Metrik hat die Referenz WIRKLICH gelesen (nicht 0 durch leeres
    # Parsen) — Symbole, Labels UND Drähte sind da.
    m = lm.measure_file(os.path.join(_REF_DIR, "sallen_key.kicad_sch"))
    assert m.n_symbols >= 10 and m.n_wires >= 10


def test_overlap_is_detected():
    # zwei Bauteile exakt übereinander → mindestens eine Überlappung.
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "Device:R") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n'
           '(symbol (lib_id "Device:R") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n)')
    assert lm.measure_text(sch).comp_overlaps >= 1


def test_diagonal_wire_is_flagged():
    sch = '(kicad_sch (wire (pts (xy 10 10) (xy 20 25))))'
    assert lm.measure_text(sch).diag_wires == 1


def test_ic_bbox_is_real_not_fallback():
    # Regression: eine Closure-``+=``-Falle ließ _bbox_for_lib bei JEDEM Symbol
    # mit Rechteck-Körper auf die 2.54×2.54-Fallback-Bbox zurückfallen → die
    # Überlappungs-Metrik war blind. Ein echter IC ist DEUTLICH größer als der
    # Fallback; ein 2-Pin-R schmaler-aber-höher als der Fallback.
    w, h = lm._bbox_for_lib("74xx:74HC595")
    assert w > 8.0 and h > 15.0, f"IC-Bbox sieht nach Fallback aus: {(w, h)}"
    rw, rh = lm._bbox_for_lib("Device:R")
    assert rh > 4.0, f"R-Bbox sieht nach Fallback aus: {(rw, rh)}"


def test_two_large_ics_side_by_side_overlap_is_detected():
    # Zwei ICs 3 mm auseinander: mit Fallback-Bbox (Halb-Breite 1.27) würde das
    # NICHT als Überlappung zählen; mit echter Bbox (Halb-Breite ~7.6) schon.
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "74xx:74HC595") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n'
           '(symbol (lib_id "74xx:74HC595") (at 103 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n)')
    assert lm.measure_text(sch).comp_overlaps >= 1
