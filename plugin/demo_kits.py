# SPDX-License-Identifier: GPL-3.0-or-later
"""Demo-Bausatz-Registry — die 10 Schaustück-Schaltungen und ihre Skill-Folge.

Zweck (zwei in einem):

1. **Demo-Menü.** Der Chat-Panel-Demo-Knopf bekommt ein Auswahlmenü aus diesen
   Einträgen. Wählt der Nutzer „Audioverstärker", legt der Runner den
   hinterlegten Schaltplan an und ruft danach die ``pipeline`` — eine geordnete
   Liste von Super-Skill-Keys — der Reihe nach auf, transparent wie
   Button-Klicks. So *sieht* man das Board entstehen und jeder Skill hilft
   sichtbar mit.
2. **Bausatzsystem.** Dieselben Schaltpläne sind die Startpunkte für ein neues
   Projekt („Neues Projekt aus Bausatz").

Diese Datei ist die Single Source of Truth der Zuordnung *Projekt → Skills*;
``plugin/demo_runner.py`` löst einen Bausatz zu konkreten Schritten auf (jeder
Skill-Schritt zieht seinen kanonischen Prompt aus ``plugin/superfeatures.py``).
Die 34 Super-Skills sind so auf 10 Projekte verteilt, dass **jeder** Skill in
mindestens einem Projekt real gebraucht wird (``test_demo_kits`` ist der
Vollständigkeits-Wächter) — kein Skill ist bloß Deko.

Bewusst getrennt von den ``.kicad_sch``-Specs: die JSON-Vorlagen (unter
``kicad_mcp/resources/data/demo_kits/<key>.json``) werden separat gebaut; hier
steht nur die Bedeutungs-Ebene (welche Skills, in welcher Reihenfolge, warum sie
genau hier helfen). Pure/stdlib, damit es headless importiert und ohne
wx/KiCad unit-getestet wird — wie ``superfeatures.py``.

Reife je Bausatz steht als zwei Flags am Kit (``board_clean``/``verified``)
und wird zu einer Menü-Stufe ⭐/✅/🔬 verdichtet (``stage``/``stage_badge``);
das ist die EINE Quelle — der Nutzer sieht ehrlich, was Referenz-Qualität hat,
und ``board_clean_keys()`` speist das DRC-Test-Gate. Der Fahrplan, alle 10 auf
mindestens ✅ zu heben, steht in ``docs/roadmap.md`` (Phasen 2–4).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import superfeatures as sf


@dataclass(frozen=True)
class DemoKit:
    """Ein Demo-Bausatz.

    key       stabile id (Menü-Dispatch, Spec-Dateiname)
    title     Menü-Text: Emoji + Kurzname
    summary   ein Satz, was die Schaltung ist
    section   Menü-Abschnitt (Key aus ``SECTIONS``) — gliedert das Demo-Menü
    spec_file basename der (separat zu bauenden) ``.kicad_sch``-Spec-JSON unter
              ``kicad_mcp/resources/data/demo_kits/``
    pipeline  GEORDNETE Super-Skill-Keys — die Reihenfolge ist ein echter
              Design-Ablauf (verstehen → platzieren → elektrisch prüfen →
              fertigungsfertig → abschließen), nicht willkürlich.
    rationale je Skill-Key eine Zeile: *warum hilft dieser Skill genau hier* —
              wird im Demo-Ablauf als Begründung des Schritts angezeigt. Muss
              exakt die Keys aus ``pipeline`` abdecken.

    Reife (zwei unabhängige Achsen, siehe ``docs/roadmap.md`` — Kit-Lebens-
    zyklus). Default beider ist ``False``, damit ein neuer oder vom Nutzer
    geänderter Bausatz automatisch als „🔬 Draft" gilt (nie fälschlich als
    fertig verkauft):
    board_clean  Platine ist 0 DRC-Fehler / 0 offene Netze (KiCads eigenes
                 DRC). Treibt das Test-Gate ``_DONE_KITS`` — Label IST der
                 Gate-Eintrag, keine zweite Meinung.
    verified     Schaltplan Pin-für-Pin gegen Herstellerdatenblatt geprüft
                 (Quelle in der Spec/im Circuit-Block).
    """
    key: str
    title: str
    summary: str
    section: str
    spec_file: str
    pipeline: tuple[str, ...]
    rationale: dict[str, str]
    board_clean: bool = False
    verified: bool = False


# Menü-Abschnitte (Reihenfolge = Menü-Reihenfolge). Der Demo-Knopf klappt zu
# diesen Gruppen auf — „den Demo-Button in Abschnitte unterteilen".
SECTIONS: tuple[tuple[str, str], ...] = (
    ("analog", "🎛️ Analog & Simulation"),
    ("digital", "🔌 Digital & Schnittstellen"),
    ("power", "⚡ Leistung & Norm"),
    ("layout", "⊙ Spezial-Layout"),
    ("fertigung", "🏭 Fertigung & Methode"),
)


# Reihenfolge = Menü-Reihenfolge. Der Audioverstärker führt (Nutzer-Beispiel).
KITS: tuple[DemoKit, ...] = (
    DemoKit(
        key="audio_amp",
        title="🔊 Audioverstärker",
        summary="Chip-Endstufe mit OpAmp-Eingangsstufe — der Analog-Klassiker.",
        section="analog",
        spec_file="audio_amp.json",
        pipeline=("sim_models", "simulate", "slew_rate", "untangle",
                  "thermal", "ampacity"),
        rationale={
            "sim_models": "Ohne SPICE-Modelle für OpAmp/Endstufe läuft keine "
                          "Simulation — der lästige Schritt zuerst.",
            "simulate": "Frequenzgang/Bandbreite der Stufe prüfen, bevor "
                        "irgendwas platziert wird.",
            "slew_rate": "Schafft die Endstufe die geforderte Signalflanke? "
                         "Sonst Verzerrung — reine Datenblatt-Rechnung.",
            "untangle": "Signalweg Eingang→Endstufe kreuzungsarm platzieren.",
            "thermal": "Die Endstufe ist der Verlustleistungs-Hotspot — "
                       "Kühlkupfer/Thermal-Vias.",
            "ampacity": "Die Lautsprecher-Ausgangsbahnen tragen echten Strom — "
                        "Breite gegen Laststrom prüfen.",
        },
        board_clean=False, verified=True,
    ),
    DemoKit(
        key="usb_sensor_hub",
        title="🔌 USB-C Sensor-Hub",
        summary="MCU mit I²C/SPI-Sensoren und USB-C — das Digital-Arbeitstier.",
        section="digital",
        spec_file="usb_sensor_hub.json",
        pipeline=("bus_radar", "semantic_erc", "xtal_caps", "pin_swap",
                  "impedance", "firmware_map"),
        rationale={
            "bus_radar": "I²C/SPI-Teilnehmer als eine Bedeutungseinheit sehen, "
                         "nicht als lose Einzelnetze.",
            "semantic_erc": "Fehlende I²C-Pull-ups und Abblock-Cs findet KiCads "
                            "ERC nicht — der semantische schon.",
            "xtal_caps": "Load-Caps des MCU-Quarzes aus CL rechnen, sonst "
                         "Startprobleme.",
            "pin_swap": "GPIOs so umlegen, dass das Routing kreuzungsfrei wird "
                        "(Pinmux-Wissen).",
            "impedance": "USB-C D+/D− auf 90 Ω differentiell auslegen.",
            "firmware_map": "Pinbelegung als Firmware-Header exportieren — "
                            "Brücke zur Software.",
        },
        board_clean=False, verified=False,
    ),
    DemoKit(
        key="ac_dc_supply",
        title="⚡ AC-DC-Netzteil",
        summary="Offline-Flyback 230 V → 5 V — das Netzspannungs-Projekt.",
        section="power",
        spec_file="ac_dc_supply.json",
        pipeline=("protection_class", "safety_spacing", "thermal",
                  "operating_temp", "ampacity"),
        rationale={
            "protection_class": "Schutzklasse I/II und Isolationskonzept nach "
                                "IEC 61140 klären — reines Norm-Wissen.",
            "safety_spacing": "Kriech-/Luftstrecken Netz↔SELV gegen IEC-60664 — "
                              "Sicherheitskritisch, kennt KiCad nicht.",
            "thermal": "Gleichrichter und Schalttransistor sind Hotspots.",
            "operating_temp": "Sperrschicht-Temperatur und Derating-Reserve der "
                              "Leistungshalbleiter.",
            "ampacity": "Primär- und Sekundärströme brauchen breite Bahnen.",
        },
        board_clean=False, verified=False,
    ),
    DemoKit(
        key="led_ring",
        title="⊙ LED-Ring",
        summary="Adressierbare WS2812-LEDs auf einem runden Board.",
        section="layout",
        spec_file="led_ring.json",
        pipeline=("polar_board", "select_place", "ampacity", "mlcc_derating",
                  "cost_estimate"),
        rationale={
            "polar_board": "Rundes Board zwingt zu Radial-Layout (Radius+Winkel "
                           "statt X/Y).",
            "select_place": "Die LEDs gleichmäßig auf dem Kreis verteilen.",
            "ampacity": "Der 5-V-Ring speist viele LEDs — summierter Strom wird "
                        "schnell groß.",
            "mlcc_derating": "Die Abblock-Cs je LED verlieren unter DC-Bias "
                             "Kapazität.",
            "cost_estimate": "Viele identische Teile — der Kostenhebel liegt in "
                             "der Stückzahl.",
        },
        board_clean=True, verified=False,
    ),
    DemoKit(
        key="motor_driver",
        title="⚙️ Motor-Treiber",
        summary="Gate-Driver mit MOSFET-Brücke und MCU — Leistungselektronik.",
        section="power",
        spec_file="motor_driver.json",
        pipeline=("ampacity", "thermal", "via_cost", "test_points",
                  "dfm_check"),
        rationale={
            "ampacity": "Phasen- und Versorgungsströme sind hoch — Bahnbreite "
                        "ist hier sicherheitsrelevant.",
            "thermal": "Die MOSFETs sind die Verlustleistungs-Hotspots.",
            "via_cost": "Power-Vias tragen Strom und kosten — Anzahl/Typ "
                        "optimieren.",
            "test_points": "Gate- und Phasennetze brauchen Bring-up-Prüfpunkte.",
            "dfm_check": "Breite Bahnen + enge Gates gegen die echten "
                         "Fab-Regeln prüfen.",
        },
        board_clean=True, verified=True,
    ),
    DemoKit(
        key="buck_converter",
        title="📉 Buck-Wandler-Modul",
        summary="DC-DC-Abwärtswandler mit IC, Spule und Filter-Cs.",
        section="power",
        spec_file="buck_converter.json",
        pipeline=("datasheet_diff", "mlcc_derating", "operating_temp",
                  "simulate", "explain_board"),
        rationale={
            "datasheet_diff": "Die Beschaltung des Buck-ICs gegen die typische "
                              "Applikationsschaltung im Datenblatt abgleichen.",
            "mlcc_derating": "Der Klassiker: 10 µF/6,3 V an 5 V ist real ~4 µF — "
                             "Ausgangs-Cs unter Bias prüfen.",
            "operating_temp": "Sperrschicht-Temperatur des Reglers unter Last.",
            "simulate": "Arbeitspunkt/Ripple verstehen statt nur Kurven "
                        "auszuspucken.",
            "explain_board": "Die Funktionsblöcke aus der Netzliste "
                             "rekonstruieren — Doku auf Knopfdruck.",
        },
        board_clean=True, verified=True,
    ),
    DemoKit(
        key="ethernet_device",
        title="🌐 Ethernet-Gerät",
        summary="MCU mit Ethernet-PHY und RJ45 — Signalintegrität + Beschaffung.",
        section="digital",
        spec_file="ethernet_device.json",
        pipeline=("nl_navigation", "semantic_erc", "impedance", "bom_sourcing",
                  "silk_cleanup"),
        rationale={
            "nl_navigation": "In normaler Sprache klären, welcher Pin welche "
                             "PHY-/MII-Funktion treibt.",
            "semantic_erc": "PHY-Entkopplung und Magnetics-Beschaltung prüfen.",
            "impedance": "Die Ethernet-Paare auf 100 Ω differentiell auslegen.",
            "bom_sourcing": "PHY-Verfügbarkeit live prüfen und pin-kompatible "
                            "Alternativen finden.",
            "silk_cleanup": "Zum Abschluss die Referenzen lesbar rücken.",
        },
        board_clean=False, verified=False,
    ),
    DemoKit(
        key="sketch_to_copper",
        title="✏️ Skizze → Kupfer",
        summary="Kleiner Leistungspfad, den man interaktiv mit Hilfe routet.",
        section="layout",
        spec_file="sketch_to_copper.json",
        pipeline=("untangle", "sketch_layer", "sketch_conductor", "watch_mode",
                  "silk_cleanup"),
        rationale={
            "untangle": "Erst einen sauberen, routbaren Startpunkt schaffen.",
            "sketch_layer": "Die Routing-Absicht auf den gemeinsamen "
                            "Skizzen-Layer zeichnen.",
            "sketch_conductor": "Die Skizze in EINEM Zug als Kupfer auf F.Cu "
                                "gießen — ein Undo-Schritt.",
            "watch_mode": "Die entstandenen Bahnen fachlich reviewen "
                          "(Clearance, DRC-Risiken).",
            "silk_cleanup": "Zum Schluss die Beschriftung aufräumen.",
        },
        board_clean=False, verified=False,
    ),
    DemoKit(
        key="production_ready",
        title="🏭 Serienreife & Kosten",
        summary="Dichtes Breakout mit vielen R/C — Fokus Fertigung & Kosten.",
        section="fertigung",
        spec_file="production_ready.json",
        pipeline=("bom_consolidate", "preferred_parts", "via_cost",
                  "dfm_check", "cost_estimate"),
        rationale={
            "bom_consolidate": "Fast-gleiche R/C-Werte auf E-Reihen "
                               "standardisieren — weniger Feeder.",
            "preferred_parts": "Auf die No-Load-Fee-Vorzugsteile des Fertigers "
                               "mappen (JLCPCB/Seeed).",
            "via_cost": "Teure Blind/Buried-Vias in Through-Vias wandeln, wo "
                        "gefahrlos.",
            "dfm_check": "Fertigbarkeit gegen die echten Regeln eines konkreten "
                         "Fertigers.",
            "cost_estimate": "Die Kostentreiber sortiert zeigen — Fläche, "
                             "Lagen, Vias, BOM.",
        },
        board_clean=True, verified=False,
    ),
    DemoKit(
        key="kit_seeding",
        title="🪄 Datenblatt & Foto → Schaltung",
        summary="Meta-Demo: wie ein Bausatz überhaupt entsteht.",
        section="fertigung",
        spec_file="kit_seeding.json",
        pipeline=("datasheet_circuit", "datasheet_diff", "photo_reverse",
                  "explain_board"),
        rationale={
            "datasheet_circuit": "Aus dem IC-Datenblatt die typische "
                                 "Applikationsschaltung generieren.",
            "datasheet_diff": "Das Ergebnis gegen das Datenblatt gegenprüfen.",
            "photo_reverse": "Ein Referenz-Board aus einem Foto rekonstruieren "
                             "(Multimodal).",
            "explain_board": "Zum Schluss erklären, was der Bausatz eigentlich "
                             "tut.",
        },
        board_clean=True, verified=False,
    ),
)


def all_kits() -> tuple[DemoKit, ...]:
    """Alle Demo-Bausätze, in Menü-Reihenfolge."""
    return KITS


# Reife-Stufen (Anzeige). Zwei Achsen → ein Menü-Symbol; siehe DemoKit-Doc.
STAGE_PRIME = "prime"      # ⭐ board_clean UND verified — Referenz-Qualität
STAGE_VERIFIED = "verified"  # ✅ eine der beiden Achsen grün — belastbar
STAGE_DRAFT = "draft"      # 🔬 keine — in Arbeit

_STAGE_BADGE = {STAGE_PRIME: "⭐", STAGE_VERIFIED: "✅", STAGE_DRAFT: "🔬"}


def stage(kit: DemoKit) -> str:
    """Reife-Stufe eines Bausatzes aus den zwei Achsen ableiten (eine Quelle:
    die Flags am Kit). ``prime`` nur wenn Platine sauber UND Schaltplan
    datenblatt-geprüft; ``verified`` wenn genau eine Achse grün; sonst
    ``draft``. Robust gegen JSON-Änderungen: die Flags sind Metadaten hier,
    kein Rücklesen der Spec."""
    if kit.board_clean and kit.verified:
        return STAGE_PRIME
    if kit.board_clean or kit.verified:
        return STAGE_VERIFIED
    return STAGE_DRAFT


def stage_badge(kit: DemoKit) -> str:
    """Das Menü-Symbol der Reife-Stufe (⭐ / ✅ / 🔬)."""
    return _STAGE_BADGE[stage(kit)]


def board_clean_keys() -> list[str]:
    """Keys aller Kits mit sauberer Platine (0 DRC / 0 offen) — die EINE Quelle
    für das DRC-Test-Gate ``tests/test_pcb_placement._DONE_KITS``. Wer ein Kit
    auf ``board_clean=True`` hebt, muss den DRC-Test bestehen; fällt ein Board
    zurück, macht der Test rot (Gate statt Meinung)."""
    return [k.key for k in KITS if k.board_clean]


def get(key: str) -> DemoKit | None:
    """Der Bausatz mit ``key``, oder ``None``."""
    return next((k for k in KITS if k.key == key), None)


def by_section(section: str) -> list[DemoKit]:
    """Bausätze eines Menü-Abschnitts, in Registry-Reihenfolge."""
    return [k for k in KITS if k.section == section]


def pipeline_items(kit: DemoKit) -> list[tuple[str, str]]:
    """Die Skill-Folge eines Bausatzes als ``(Skill-Label, Begründung)`` — für
    die Menü-/Hover-Anzeige (welche Super-Skills, was passiert). Zieht das
    Anzeige-Label (Emoji + Name) aus ``superfeatures``."""
    items = []
    for fk in kit.pipeline:
        feat = sf.get(fk)
        label = feat.label if feat else fk
        items.append((label, kit.rationale[fk]))
    return items


def hover_preview(kit: DemoKit) -> str:
    """Kompakte Hover-Vorschau: ein Satz Zweck + die Skill-Kette (Pfeil-getrennt).
    Zeigt vor dem Klick, welche Super-Skills beteiligt sind und was passiert."""
    chain = " → ".join(sf.get(fk).label if sf.get(fk) else fk
                       for fk in kit.pipeline)
    return f"{kit.summary}  ·  {len(kit.pipeline)} Skills: {chain}"


def covered_skills() -> frozenset[str]:
    """Alle Super-Skill-Keys, die irgendein Bausatz aufruft."""
    return frozenset(fk for kit in KITS for fk in kit.pipeline)


def uncovered_skills() -> frozenset[str]:
    """Super-Skills, die in KEINEM Bausatz vorkommen (Ziel: leer)."""
    return frozenset(f.key for f in sf.all_features()) - covered_skills()


def validate() -> None:
    """Integritäts-Check der Registry — wirft ``ValueError`` bei Verstoß.

    Prüft: eindeutige Keys, jede ``pipeline`` verweist nur auf existierende
    Super-Skills, ``rationale`` deckt exakt die Pipeline-Keys ab, und alle 34
    Skills sind abgedeckt. Von ``test_demo_kits`` UND beim Import durch den
    Runner genutzt, damit kein halbgares Menü ausgeliefert wird."""
    keys = [k.key for k in KITS]
    if len(keys) != len(set(keys)):
        raise ValueError("Doppelter Bausatz-Key in demo_kits.KITS")
    valid_skills = {f.key for f in sf.all_features()}
    valid_sections = {s for s, _ in SECTIONS}
    for kit in KITS:
        if kit.section not in valid_sections:
            raise ValueError(
                f"Bausatz '{kit.key}': unbekannter Abschnitt '{kit.section}'")
        if not kit.pipeline:
            raise ValueError(f"Bausatz '{kit.key}' hat keine Pipeline")
        unknown = [fk for fk in kit.pipeline if fk not in valid_skills]
        if unknown:
            raise ValueError(
                f"Bausatz '{kit.key}': unbekannte Skill-Keys {unknown}")
        if set(kit.rationale) != set(kit.pipeline):
            raise ValueError(
                f"Bausatz '{kit.key}': rationale-Keys decken die Pipeline nicht "
                f"exakt ab (fehlt/überzählig: "
                f"{set(kit.rationale) ^ set(kit.pipeline)})")
    missing = uncovered_skills()
    if missing:
        raise ValueError(
            f"Demo-Bausätze decken nicht alle Super-Skills ab — fehlen: "
            f"{sorted(missing)}")
