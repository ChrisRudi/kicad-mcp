# SPDX-License-Identifier: GPL-3.0-or-later
"""IEC-60664-1-Lookups: Kriech-/Luftstrecken je Spannung — der Datenkern des
Schutzklassen-Super-Features.

Die Normwerte leben als kuratierter, DATIERTER Snapshot in
``resources/data/safety_spacing_iec60664.json`` (gleiche Mechanik wie die
Fab-Standardteile) — nicht im Modellgedächtnis, damit die Zahlen prüfbar und
versioniert sind. Dieses Modul macht die reinen Lookups: Nennspannung + OVC →
Stoßspannung (F.1) → Luftstrecke (F.2, Fall A) mit PD-Minima; Arbeitsspannung +
PD + Materialgruppe → Kriechstrecke (F.4); verstärkte Isolierung = Kriechweg ×2
bzw. Stoßspannung eine Vorzugsstufe höher.

Konservativ: es wird immer die NÄCHSTE Tabellenzeile ≥ Anfrage genommen (keine
Interpolation). Pure/stdlib, headless testbar. Richtwerte — keine
Zertifizierung; die Quelle + Disclaimer wandern in jedes Ergebnis.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "resources",
                          "data", "safety_spacing_iec60664.json")

_cache: Optional[dict] = None

OVC_VALUES = ("I", "II", "III", "IV")
MATERIAL_GROUPS = ("I", "II", "III", "IIIa", "IIIb")
INSULATIONS = ("functional", "basic", "reinforced", "double")


def load_data() -> dict:
    """Der Norm-Snapshot (einmal geladen, read-only geteilt)."""
    global _cache
    if _cache is None:
        with open(_DATA_PATH, encoding="utf-8") as fh:
            _cache = json.load(fh)
    return _cache


def _norm_material_group(mg: str) -> str:
    mg = (mg or "").strip().upper()
    if mg in ("IIIA", "IIIB"):
        return "III"
    return mg


def impulse_voltage_kv(nominal_v: float, ovc: str) -> Optional[float]:
    """F.1: Bemessungs-Stoßspannung für Netz-Nennspannung (L-N) + OVC."""
    ovc = (ovc or "").strip().upper()
    if ovc not in OVC_VALUES:
        return None
    for row in load_data()["impulse_voltage_kv"]["rows"]:
        if nominal_v <= row["nominal_v_max"]:
            return row[ovc]
    return None


def _impulse_step_up(kv: float) -> float:
    """Verstärkte Isolierung: eine Stufe höher in der Vorzugsreihe."""
    series = [r["impulse_kv"] for r in load_data()["clearance_mm"]["rows"]]
    for step in series:
        if step > kv + 1e-9:
            return step
    return series[-1]


def clearance_mm(impulse_kv: float, pollution_degree: int = 2,
                 reinforced: bool = False) -> Optional[float]:
    """F.2 (Fall A): Luftstrecke für die Stoßspannung, inkl. PD-Minimum."""
    if impulse_kv is None:
        return None
    if reinforced:
        impulse_kv = _impulse_step_up(impulse_kv)
    data = load_data()["clearance_mm"]
    value = None
    for row in data["rows"]:
        if impulse_kv <= row["impulse_kv"] + 1e-9:
            value = row["mm"]
            break
    if value is None:
        return None  # jenseits der Tabelle (> 12 kV)
    minimum = data["min_mm_by_pollution_degree"].get(
        str(int(pollution_degree)), 0.0)
    return max(value, minimum)


def creepage_mm(working_v: float, pollution_degree: int = 2,
                material_group: str = "III",
                reinforced: bool = False) -> Optional[float]:
    """F.4: Kriechstrecke für Arbeitsspannung + PD + Materialgruppe."""
    mg = _norm_material_group(material_group)
    if mg not in ("I", "II", "III"):
        return None
    pd = int(pollution_degree)
    if pd not in (1, 2, 3):
        return None
    for row in load_data()["creepage_mm"]["rows"]:
        if working_v <= row["v_max"]:
            base = row["pd1"] if pd == 1 else row[f"pd{pd}"][mg]
            factor = (load_data()["insulation_rules"]
                      ["reinforced_creepage_factor"] if reinforced else 1.0)
            return round(base * factor, 3)
    return None  # jenseits der Tabelle (> 1000 V)


def spacing_requirements(
    working_voltage_v: float,
    nominal_mains_v: float = 0.0,
    pollution_degree: int = 2,
    material_group: str = "IIIa",
    overvoltage_category: str = "II",
    insulation: str = "basic",
) -> dict[str, Any]:
    """Das Gesamtergebnis fürs Tool: Kriech- + Luftstrecke + Herleitung.

    ``nominal_mains_v`` (L-N, für die Stoßspannungswahl) fällt auf die
    Arbeitsspannung zurück, wenn 0. ``insulation``: functional/basic →
    Basiswerte; reinforced/double → verstärkte Werte (Kriechweg ×2,
    Stoßspannung eine Stufe höher).
    """
    ins = (insulation or "basic").strip().lower()
    if ins not in INSULATIONS:
        return {"success": False,
                "error": f"insulation must be one of {INSULATIONS}"}
    reinforced = ins in ("reinforced", "double")
    nominal = nominal_mains_v or working_voltage_v
    kv = impulse_voltage_kv(nominal, overvoltage_category)
    if kv is None:
        return {"success": False, "error": (
            f"Keine Stoßspannung für Nennspannung {nominal} V / "
            f"OVC {overvoltage_category!r} (Tabelle endet bei 1000 V; "
            "OVC muss I-IV sein).")}
    clearance = clearance_mm(kv, pollution_degree, reinforced=reinforced)
    creepage = creepage_mm(working_voltage_v, pollution_degree,
                           material_group, reinforced=reinforced)
    if creepage is None or clearance is None:
        return {"success": False, "error": (
            "Wert außerhalb des Tabellenbereichs (Arbeitsspannung ≤ 1000 V, "
            "PD 1-3, Materialgruppe I/II/III[a/b]).")}
    # F.7-Regel: die Kriechstrecke darf nie kleiner sein als die zugehörige
    # Luftstrecke — sonst wäre der kürzeste Weg durch die Luft maßgeblich.
    creepage = max(creepage, clearance)
    data = load_data()
    return {
        "success": True,
        "working_voltage_v": working_voltage_v,
        "nominal_mains_v": nominal,
        "pollution_degree": int(pollution_degree),
        "material_group": material_group,
        "overvoltage_category": overvoltage_category.upper(),
        "insulation": ins,
        "impulse_voltage_kv": kv,
        "clearance_mm": clearance,
        "creepage_mm": creepage,
        "protection_classes": data["protection_classes_iec61140"],
        "source": data["source"],
        "snapshot_date": data["snapshot_date"],
        "disclaimer": data["disclaimer"],
    }
