# Circuit-Blocks — umgezogen (eine Quelle)

Die Block-Specs, die früher hier lagen, leben jetzt **ausgeliefert** unter
`kicad_mcp/resources/data/circuit_blocks/` — die EINE Heimat für beide
Aufgaben (verschmolzen):

1. **Block in bestehenden Schaltplan einfügen** (`apply_circuit_block`):
   nackter Block-Name genügt, kein Pfad nötig —

   ```text
   validate_circuit_block(spec="tps54202_buck_3v3")
   apply_circuit_block(sch_path="my.kicad_sch", spec="tps54202_buck_3v3")
   ```

2. **Demo-Kit komponieren** (▶-Demo-Knopf im Plugin): ein Kit-Rezept unter
   `kicad_mcp/resources/data/demo_kits/recipes/` referenziert denselben
   Block; `scripts/compose_demo_kits.py` schreibt daraus die Kit-JSONs
   (`demo_kits/<key>.json` sind Build-Artefakte, Drift-Wächter:
   `tests/test_kit_compose.py`).

## Verfügbare Blocks

| Block | IC | Klasse | Highlights |
|---|---|---|---|
| `tps54202_buck_3v3` | TPS54202 (TI) | Buck converter | Multi-GND-Pins, Bootstrap-C, FB-Teiler mit `value_formula`, `operating_envelope` |
| `ams1117_ldo_3v3` | AMS1117-3.3 | Linearregler | Polarisierte Bulk-Caps (ESR-Fenster!), 3-Pin-Chip, `external_nets` mit Richtung |
| `lm358_opamp` | LM358 | Dual-OpAmp | 2 Sektionen pro Gehäuse, Misch-Netze Signal/Power |
| `mp1584_buck_5v` | MP1584 (MPS) | Buck converter | Basis des Buck-Demo-Kits; COMP-RC nach MPS-Verfahren |
| `drv8871_hbridge` | DRV8871 (TI) | H-Brücke | Basis des Motor-Demo-Kits; ILIM-Bemessung, VM-Bypass-Pflicht |
| `lm386_amp20` | LM386 (TI) | Audio-Amp | Basis des Audio-Demo-Kits; gain=20, Zobel-Glied |

Alle Blocks sind reine Datenblatt-Transkriptionen (Quelle in jeder Spec),
Schema: `kicad_mcp/generators/circuit_block/schema_v1_1.json`.

Für Multi-Instanz: `instances[]`-Array in der Spec, dann
`apply_circuit_block` einmal je `instance_id`.
