# Circuit-Block Examples

Reference specs against the v1.1 schema (`kicad_mcp/generators/circuit_block/schema_v1_1.json`).
Use them as templates for hand-written specs and as fixtures for the
`validate_circuit_block` tool.

| File | IC | Block class | Highlights |
|---|---|---|---|
| `tps54202_buck_3v3.json` | TPS54202 (TI) | Buck converter | Multi-GND pin handling, bootstrap capacitor, FB divider with `value_formula`, EN pull-down strap, `operating_envelope` |
| `ams1117_ldo_3v3.json` | AMS1117-3.3 | Linear regulator | Polarised bulk caps (Tantal/Alu), simple 3-pin chip, `external_nets` with direction |
| `lm358_opamp.json` | LM358 | Dual op-amp | 8-pin DIL/SO package, two functional sections per package, mixed signal/power nets |

All three use generic, KiCad-stock-library symbols and footprints. None of
them encode topology specific to any downstream project — they are pure
datasheet transcriptions.

## Recommended use

```text
validate_circuit_block(spec="examples/circuit_block/tps54202_buck_3v3.json")
apply_circuit_block(sch_path="my.kicad_sch",
                    spec="examples/circuit_block/tps54202_buck_3v3.json")
```

For multi-instance: add an `instances[]` array to the spec, then call
`apply_circuit_block` once per `instance_id`.

## Abgrenzung: Wo liegen die Demo-Schaltungen?

Die **kompletten Demo-Schaltungen** (Buck-Converter, Motor-Treiber,
Audio-Amp, …) liegen NICHT hier, sondern als parts+nets+board-Specs unter
`kicad_mcp/resources/data/demo_kits/*.json` — das ist, was der ▶-Demo-Knopf
im Plugin baut (Topologien datenblatt-geprüft, Quelle in jeder Spec).

Dieser Ordner enthält dagegen **wiederverwendbare IC-Applikations-Blöcke**
(Schema v1.1) für `apply_circuit_block`: „füge diese Regler-/OpAmp-
Beschaltung in einen BESTEHENDEN Schaltplan ein". Geplant (Demo-Plan
Schritt 3): die Demo-Kits aus solchen Blöcken komponieren, damit beide
Welten dieselbe Quelle teilen.
