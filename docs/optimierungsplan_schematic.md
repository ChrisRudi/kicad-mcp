# Optimierungsplan Schaltplan-Generator — kürzer & schneller

Stand 0.20.1. Basis-Messung (usb_sensor_hub, warm): Emission **68 ms**,
Messung 13 ms → 1 Optimizer-Eval ≈ 81 ms. Profil davor: `resolve_lib_id`
44 % (behoben: Memo-Cache), jetzt dominiert `_astar_route` (~33 Aufrufe/Emit,
je ~8 ms). Umfang: `generators/schematic/` + `generators/common/` ≈ 6 970
Zeilen (nach Streichung von 143 Zeilen totem Code).

**Eiserne Regel für JEDEN Schritt:** Netzlisten-Roundtrip 10/10 und
Byte-Determinismus (gleiche Eingabe → identisches `.kicad_sch`) müssen nach
jedem Commit halten — beides ist per `tests/test_netlist_roundtrip.py`
abgesichert; für Determinismus vor/nach Hash-Vergleich über die 10 Kits.

## Bereits erledigt (0.20.1)
- [x] Toter Code raus: `_carve_pin_corridors`, `_cells_owned_by`,
      `_should_wire_power_net`, `_rasterize_path_cells` (−143 Zeilen).
- [x] `resolve_lib_id` memoisiert (528 Aufrufe/Emit, 108 Fuzzy-Suchen → 1×):
      Emission 106 → 68 ms, byte-identische Ausgabe.

## Phase A — Tempo (risikoarm, messbar)
1. **A*-Aufrufe halbieren:** `_astar_route` wird pro MST-Kante gerufen; bei
   Fehlschlag nochmal L-Bend. Vorab-Sichtprüfung (L-Bend-Test ZUERST, A* nur
   wenn L-Bend belegt) spart bei einfachen Kanten den teuren A* komplett.
   Erwartung: −30…50 % Emissionszeit. Risiko: gering (gleiche Fallbacks).
2. **`_seg_conflicts` indizieren:** aktuell O(Segmente) je Kandidat mit
   Python-Schleife. Ein Grid-Bucket-Index (Zelle → Segmente) macht es O(1)-ish.
   Lohnt erst bei großen Blättern; Messpunkt ethernet.
3. **Messung `measure_text` cachen im Optimizer:** Kandidaten unterscheiden
   sich nur in 1–2 Bauteilen; heute wird das GANZE Blatt neu geparst.
   Inkrementelle Messung ist der größte Eval-Hebel (13 ms → ~2 ms), aber
   invasiv → nur mit Golden-Vergleich (badness identisch auf 1 000 Kandidaten).
4. **`_detect_units`/`parse_sexpr`-Restkosten:** lru_cache auf
   `parse_sexpr(raw)` (Key = raw-String) — 108 Parses/Emit verschwinden.

## Phase B — Kürzer (Strukturabbau ohne Verhaltensänderung)
5. **route.py (1 413 Z.) aufteilen:** `power_emit.py` (Power-Symbole/Labels),
   `conflict_registry.py` (Registry + Junctions), `astar.py` (Router).
   Reine Verschiebung, Re-Exports für Tests. −0 Zeilen, +Lesbarkeit.
6. **defrag_place.py (800 Z.) Phasen-Tabelle:** Die 9 „Phase n"-Blöcke haben
   identisches Skelett (Filter → Kandidaten → Kosten → _do_place). Eine
   Phasen-Liste `[(filter, candidates, cost), …]` + generischer Läufer spart
   geschätzt 200–300 Zeilen und macht neue Phasen zu Daten.
7. **builder.py Ref/Value-Platzierung + Placeholder** in `annotate.py`
   extrahieren (~150 Z.), `_declutter_labels` in `declutter.py` (~180 Z.).
8. **Doppelte Geometrie-Helfer einsammeln:** `_seg_through_rect`/`on_seg`/
   Kollinear-Checks existieren 3× (layout_measure, route-Closure, declutter).
   Eine Quelle in `common/segops.py` (−80 Z., ein Verhalten).

## Phase C — Suche schneller statt Code schneller
9. **Optimizer-Frühabbruch:** Kandidat verwerfen, sobald Teil-badness die
   aktuelle Bestmarke übersteigt (Messung abbrechbar machen).
10. **Operator-Bandit:** Operatoren, die bei diesem Kit nie Verbesserung
    liefern, seltener ziehen (UCB1) — weniger Evals bis 0.

## Nicht anfassen (bewusst)
- Registry-Konfliktprüfung vereinfachen („ist doch nur O(n²)") — sie ist der
  Kurzschluss-Schutz; Korrektheit vor Mikro-Optimierung.
- `sorted()`-Determinismus-Stellen „wegoptimieren" — sie sind Absicht
  (PYTHONHASHSEED), siehe Kommentare an Ort und Stelle.

## Messrezept je Schritt
```bash
python - <<'EOF'   # Vorher/Nachher: Zeit + Byte-Hash über alle 10 Kits
...   (siehe scripts/… bzw. Session-Notizen; Hash-Gleichheit = Pflicht
      bei reinen Perf-Schritten, Roundtrip-Suite bei allen)
EOF
pytest tests/test_netlist_roundtrip.py -q
```
