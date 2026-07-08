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
    reference_pcb  basename einer mitgelieferten, fertig gerouteten
                 ``.kicad_pcb`` (mit gleichnamiger ``.kicad_pro``) unter
                 ``kicad_mcp/resources/data/demo_kits/``. Gesetzt für Bausätze,
                 deren Platine der Auto-Router (noch) nicht restlos schließt
                 (dichtes Fine-Pitch, 2-lagig): die *gelieferte saubere
                 Platine* IST diese Referenz (Hand-Route mit GND-Fläche), nicht
                 die frisch generierte. Das DRC-Gate prüft dann diese Datei
                 statt neu zu generieren. Leer = Platine kommt aus dem
                 Generator.
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
    reference_pcb: str = ""


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
            "sim_models": "Ich hinterlege zuerst SPICE-Modelle für OpAmp/"
                          "Endstufe — ohne die läuft keine Simulation.",
            "simulate": "Ich prüfe Frequenzgang/Bandbreite der Stufe, bevor "
                        "ich irgendetwas platziere.",
            "slew_rate": "Ich rechne, ob die Endstufe die geforderte "
                         "Signalflanke schafft — sonst Verzerrung.",
            "untangle": "Ich platziere den Signalweg Eingang→Endstufe "
                        "kreuzungsarm.",
            "thermal": "Ich kühle die Endstufe — den Verlustleistungs-Hotspot "
                       "— mit Kupferfläche und Thermal-Vias.",
            "ampacity": "Ich prüfe die Lautsprecher-Ausgangsbahnen gegen den "
                        "echten Laststrom.",
        },
        board_clean=True, verified=True,
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
            "bus_radar": "Ich sehe die I²C/SPI-Teilnehmer als eine "
                         "Bedeutungseinheit, nicht als lose Einzelnetze.",
            "semantic_erc": "Ich finde fehlende I²C-Pull-ups und Abblock-Cs — "
                            "die KiCads ERC nicht sieht.",
            "xtal_caps": "Ich rechne die Load-Caps des MCU-Quarzes aus dem CL "
                         "— sonst Startprobleme.",
            "pin_swap": "Ich lege die GPIOs so um, dass das Routing "
                        "kreuzungsfrei wird (Pinmux-Wissen).",
            "impedance": "Ich lege das USB-C-Paar D+/D− auf 90 Ω differentiell "
                         "aus.",
            "firmware_map": "Ich exportiere die Pinbelegung als Firmware-Header "
                            "— die Brücke zur Software.",
        },
        board_clean=True, verified=True,
        reference_pcb="usb_sensor_hub.reference.kicad_pcb",
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
            "protection_class": "Ich kläre Schutzklasse I/II und das "
                                "Isolationskonzept nach IEC 61140 — Norm-Wissen.",
            "safety_spacing": "Ich prüfe Kriech-/Luftstrecken Netz↔SELV gegen "
                              "IEC 60664 — sicherheitskritisch, kennt KiCad nicht.",
            "thermal": "Ich kühle Gleichrichter und Schalttransistor — die "
                       "Hotspots der Schaltung.",
            "operating_temp": "Ich prüfe Sperrschicht-Temperatur und "
                              "Derating-Reserve der Leistungshalbleiter.",
            "ampacity": "Ich verbreitere die Bahnen für Primär- und "
                        "Sekundärströme.",
        },
        board_clean=True, verified=True,
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
            "polar_board": "Ich lege das runde Board radial an (Radius+Winkel "
                           "statt X/Y).",
            "select_place": "Ich verteile die LEDs gleichmäßig auf dem Kreis.",
            "ampacity": "Ich prüfe den summierten 5-V-Strom des Rings — er wird "
                        "mit vielen LEDs schnell groß.",
            "mlcc_derating": "Ich prüfe die Abblock-Cs je LED — sie verlieren "
                             "unter DC-Bias Kapazität.",
            "cost_estimate": "Ich schätze die Kosten — bei vielen identischen "
                             "Teilen liegt der Hebel in der Stückzahl.",
        },
        board_clean=True, verified=True,
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
            "ampacity": "Ich prüfe die Bahnbreite für Phasen- und "
                        "Versorgungsströme — hier sicherheitsrelevant.",
            "thermal": "Ich kühle die MOSFETs — die Verlustleistungs-Hotspots.",
            "via_cost": "Ich optimiere Anzahl/Typ der Power-Vias — sie tragen "
                        "Strom und kosten.",
            "test_points": "Ich lege Bring-up-Prüfpunkte an Gate- und "
                           "Phasennetze.",
            "dfm_check": "Ich prüfe breite Bahnen und enge Gates gegen die "
                         "echten Fab-Regeln.",
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
            "datasheet_diff": "Ich gleiche die Beschaltung des Buck-ICs gegen "
                              "die Applikationsschaltung im Datenblatt ab.",
            "mlcc_derating": "Ich prüfe die Ausgangs-Cs unter Bias — 10 µF/"
                             "6,3 V an 5 V ist real nur ~4 µF.",
            "operating_temp": "Ich prüfe die Sperrschicht-Temperatur des "
                              "Reglers unter Last.",
            "simulate": "Ich simuliere Arbeitspunkt/Ripple, statt nur Kurven "
                        "auszuspucken.",
            "explain_board": "Ich rekonstruiere die Funktionsblöcke aus der "
                             "Netzliste — Doku auf Knopfdruck.",
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
            "nl_navigation": "Ich kläre in normaler Sprache, welcher Pin welche "
                             "PHY-/MII-Funktion treibt.",
            "semantic_erc": "Ich prüfe PHY-Entkopplung und "
                            "Magnetics-Beschaltung.",
            "impedance": "Ich lege die Ethernet-Paare auf 100 Ω differentiell "
                         "aus.",
            "bom_sourcing": "Ich prüfe die PHY-Verfügbarkeit live und finde "
                            "pin-kompatible Alternativen.",
            "silk_cleanup": "Ich rücke zum Abschluss die Referenzen lesbar "
                            "zurecht.",
        },
        board_clean=True, verified=False,
        reference_pcb="ethernet_device.reference.kicad_pcb",
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
            "untangle": "Ich schaffe erst einen sauberen, routbaren "
                        "Startpunkt.",
            "sketch_layer": "Ich zeichne die Routing-Absicht auf den "
                            "gemeinsamen Skizzen-Layer.",
            "sketch_conductor": "Ich gieße die Skizze in EINEM Zug als Kupfer "
                                "auf F.Cu — ein Undo-Schritt.",
            "watch_mode": "Ich reviewe die entstandenen Bahnen fachlich "
                          "(Clearance, DRC-Risiken).",
            "silk_cleanup": "Ich räume zum Schluss die Beschriftung auf.",
        },
        board_clean=False, verified=True,
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
            "bom_consolidate": "Ich standardisiere fast-gleiche R/C-Werte auf "
                               "E-Reihen — weniger Feeder.",
            "preferred_parts": "Ich mappe auf die No-Load-Fee-Vorzugsteile des "
                               "Fertigers (JLCPCB/Seeed).",
            "via_cost": "Ich wandle teure Blind/Buried-Vias in Through-Vias, wo "
                        "es gefahrlos ist.",
            "dfm_check": "Ich prüfe die Fertigbarkeit gegen die echten Regeln "
                         "eines konkreten Fertigers.",
            "cost_estimate": "Ich zeige die Kostentreiber sortiert — Fläche, "
                             "Lagen, Vias, BOM.",
        },
        board_clean=True, verified=True,
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
            "datasheet_circuit": "Ich generiere aus dem IC-Datenblatt die "
                                 "typische Applikationsschaltung.",
            "datasheet_diff": "Ich prüfe das Ergebnis gegen das Datenblatt "
                              "gegen.",
            "photo_reverse": "Ich rekonstruiere ein Referenz-Board aus einem "
                             "Foto (multimodal).",
            "explain_board": "Ich erkläre zum Schluss, was der Bausatz "
                             "eigentlich tut.",
        },
        board_clean=True, verified=True,
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


def reference_pcb_path(kit: DemoKit):
    """Absoluter Pfad der mitgelieferten Referenz-Platine eines Kits, oder
    ``None`` wenn keine gesetzt ist. Löst über denselben mcp-root-Resolver auf
    wie die Specs (``demo_runner._spec_dir``-Logik gespiegelt), damit Gate und
    installiertes Plugin dieselbe Datei sehen. Die gleichnamige ``.kicad_pro``
    liegt daneben und wird von ``kicad-cli`` automatisch mitgelesen."""
    if not kit.reference_pcb:
        return None
    from pathlib import Path
    from . import server_manager
    return (Path(server_manager.default_mcp_root())
            / "kicad_mcp" / "resources" / "data" / "demo_kits"
            / kit.reference_pcb)


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
