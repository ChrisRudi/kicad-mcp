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
    assert d["crowding"] == 0, m.details
    assert d["label_overlaps"] == 0, m.details
    assert d["label_wrong_dir"] == 0, m.details
    assert d["label_label_overlaps"] == 0, m.details
    assert d["label_wire_overlaps"] == 0, m.details
    assert d["annot_overlaps"] == 0, m.details
    assert d["wire_through_body"] == 0, m.details
    assert d["wire_overlaps"] == 0, m.details
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


def test_annotation_text_overlap_is_detected():
    # Referenz/Wert-Texte ZWEIER Bauteile an derselben Stelle → 1 Annotations-
    # Überlappung (der visuelle Dreck bei eng gepackten Passives). Körper weit
    # auseinander, damit NUR die Beschriftung kollidiert.
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "Device:R") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes)\n'
           '  (property "Reference" "R1" (at 120 99 0))\n'
           '  (property "Value" "10k" (at 120 101 0)))\n'
           '(symbol (lib_id "Device:R") (at 160 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes)\n'
           '  (property "Reference" "R2" (at 120 99 0))\n'
           '  (property "Value" "10k" (at 120 101 0)))\n)')
    assert lm.measure_text(sch).annot_overlaps >= 1


def test_hidden_annotation_does_not_count():
    # Verborgene Felder (Footprint, Power-Ref) zeichnen nicht → keine Kollision.
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "Device:R") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes)\n'
           '  (property "Reference" "R1" (at 120 99 0) (hide yes)))\n'
           '(symbol (lib_id "Device:R") (at 160 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes)\n'
           '  (property "Reference" "R2" (at 120 99 0) (hide yes)))\n)')
    assert lm.measure_text(sch).annot_overlaps == 0


def test_label_over_component_body_is_detected():
    # Label-TEXT-Box liegt über einem Kondensator-Körper (der Anker daneben) →
    # muss als label_overlaps zählen (der motor_driver-Fall).
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "Device:C") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n'
           '(label "NET1" (at 99 100 0))\n)')   # Text läuft nach rechts ÜBER das C
    assert lm.measure_text(sch).label_overlaps >= 1


def test_two_labels_overlapping_are_detected():
    sch = ('(kicad_sch\n'
           '(label "SIGNAL" (at 100 100 0))\n'
           '(label "SIGNAL" (at 101 100 0))\n)')
    assert lm.measure_text(sch).label_label_overlaps >= 1


def test_label_over_wire_is_detected():
    # Label-Box liegt über einem FREMDEN Draht (nicht dem eigenen Stub).
    sch = ('(kicad_sch\n'
           '(label "NET" (at 100 100 0))\n'
           '(wire (pts (xy 101 95) (xy 101 105)))\n)')
    assert lm.measure_text(sch).label_wire_overlaps >= 1


def test_label_own_stub_is_not_counted_as_wire_overlap():
    # der eigene Stub (endet am Label-Anker) zählt NICHT als Label-über-Draht.
    sch = ('(kicad_sch\n'
           '(label "NET" (at 100 100 0))\n'
           '(wire (pts (xy 100 100) (xy 105 100)))\n)')
    assert lm.measure_text(sch).label_wire_overlaps == 0


def test_labels_get_five_mm_lead():
    # „auch Labels benötigen 5 mm Leitung" — der Label-Stub ist 2 Grid lang.
    from kicad_mcp.generators.common.constants import LABEL_STUB_LEN
    assert LABEL_STUB_LEN >= 5.0


def test_collinear_overlapping_wires_are_detected():
    # zwei waagrechte Leitungen auf gleichem y, deren x-Bereiche sich überlappen
    # → 1 „Leitung übereinander" (nicht Kreuzung, nicht Endpunkt).
    sch = ('(kicad_sch\n'
           '(wire (pts (xy 10 50) (xy 30 50)))\n'
           '(wire (pts (xy 20 50) (xy 40 50)))\n)')
    m = lm.measure_text(sch)
    assert m.wire_overlaps >= 1
    assert m.wire_crossings == 0


def test_contiguous_collinear_wires_are_not_overlap():
    # zwei Segmente einer geraden Leitung, die nur EINEN Endpunkt teilen → kein
    # Übereinanderliegen (das ist eine fortlaufende Leitung).
    sch = ('(kicad_sch\n'
           '(wire (pts (xy 10 50) (xy 20 50)))\n'
           '(wire (pts (xy 20 50) (xy 30 50)))\n)')
    assert lm.measure_text(sch).wire_overlaps == 0


def test_crossing_is_not_counted_as_overlap():
    sch = ('(kicad_sch\n'
           '(wire (pts (xy 20 10) (xy 20 40)))\n'
           '(wire (pts (xy 10 25) (xy 30 25)))\n)')
    m = lm.measure_text(sch)
    assert m.wire_overlaps == 0
    assert m.wire_crossings == 1


def test_wire_through_body_is_detected():
    # Waagrechter Draht quer durch einen IC, Endpunkte weit außerhalb → 1 Querung
    # (Regel: Drähte gehen nie durch Bauteile).
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "74xx:74HC595") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n'
           '(wire (pts (xy 60 100) (xy 140 100)))\n)')
    assert lm.measure_text(sch).wire_through_body >= 1


def test_pin_stub_wire_is_not_through_body():
    # Kurzer Stub, der AM Pin (nahe Rand) endet, quert den Körper nicht.
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "Device:R") (at 100 100 90) (unit 1)'
           ' (in_bom yes) (on_board yes))\n'
           '(wire (pts (xy 100 95) (xy 100 90)))\n)')
    assert lm.measure_text(sch).wire_through_body == 0


def test_bus_across_component_counts_even_with_a_pin_endpoint():
    # Regression: die alte Pin-Ring-Ausnahme („ein Endpunkt < 2.84 mm vom Rand →
    # ganzes Segment ignorieren") versteckte reale Busse quer über große ICs.
    # Ein waagrechter Draht, der AN einem IC-Pin (linke Kante) beginnt und quer
    # durch den ganzen Körper zur anderen Seite läuft, MUSS zählen.
    w, _h = lm._bbox_for_lib("74xx:74HC595")  # ~ breit×hoch, Körper real
    x_left = 100 - w / 2            # linke Körperkante (~ Pin-Bereich)
    x_far = 100 + w / 2 + 10        # weit rechts, jenseits des Körpers
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "74xx:74HC595") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n'
           f'(wire (pts (xy {x_left:.2f} 100) (xy {x_far:.2f} 100)))\n)')
    assert lm.measure_text(sch).wire_through_body >= 1


def test_single_pin_center_connection_is_not_through_body():
    # Ein-Pin-Bauteil (TestPoint): Anschluss = Symbol-Ursprung = Körper-Mitte.
    # Der Anschluss-Stub startet zwangsläufig in der (winzigen) Körper-Mitte —
    # das ist eine legitime Ein-Pin-Verbindung, KEIN Bus quer durchs Bauteil.
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "Connector:TestPoint") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n'
           '(wire (pts (xy 100 100) (xy 102.54 100)))\n)')
    assert lm.measure_text(sch).wire_through_body == 0


def test_custom_power_symbol_is_not_a_body():
    # Profi-Referenzen nutzen eine EIGENE Symbol-Lib (z. B. "myschlib:GND") mit
    # (in_bom yes). Erkannt wird das Power-Symbol an der Referenz "#PWR…" —
    # sonst zählt ein Draht in seinen Stub als „quer durchs Bauteil" (der
    # Falsch-Positiv, der die 0-Eichung der Referenzen zerstörte).
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "myschlib:GND") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes)\n'
           '  (property "Reference" "#PWR01" (at 100 106 0) (effects (hide yes))))\n'
           '(wire (pts (xy 100 96) (xy 100 100)))\n)')
    m = lm.measure_text(sch)
    assert m.wire_through_body == 0
    assert m.n_symbols == 1


def test_two_large_ics_side_by_side_overlap_is_detected():
    # Zwei ICs 3 mm auseinander: mit Fallback-Bbox (Halb-Breite 1.27) würde das
    # NICHT als Überlappung zählen; mit echter Bbox (Halb-Breite ~7.6) schon.
    sch = ('(kicad_sch\n'
           '(symbol (lib_id "74xx:74HC595") (at 100 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n'
           '(symbol (lib_id "74xx:74HC595") (at 103 100 0) (unit 1)'
           ' (in_bom yes) (on_board yes))\n)')
    assert lm.measure_text(sch).comp_overlaps >= 1


def test_crowding_is_detected():
    # „mehr Luft lassen": zwei Widerstände fast Körper an Körper (Spalt < 2.54)
    # → Gedränge; mit ordentlichem Abstand → 0.
    tight = ('(kicad_sch\n'
             '(symbol (lib_id "Device:R") (at 100 100 0) (unit 1)'
             ' (in_bom yes) (on_board yes))\n'
             '(symbol (lib_id "Device:R") (at 103.5 100 0) (unit 1)'
             ' (in_bom yes) (on_board yes))\n)')
    roomy = tight.replace("103.5", "112")
    assert lm.measure_text(tight).crowding >= 1
    assert lm.measure_text(roomy).crowding == 0
