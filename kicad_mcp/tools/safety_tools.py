# SPDX-License-Identifier: GPL-3.0-or-later
"""Sicherheitsabstände: normbasierte Kriech-/Luftstrecken-Anforderungen.

KiCad hat kein Isolations-/Spannungsmodell und keine Sicherheitsnormen — welche
Abstände zwischen Netzspannung und Kleinspannung nötig sind, ist externes
Norm-Wissen. Dieses Tool liefert es aus einem kuratierten, datierten
IEC-60664-1-Snapshot (``utils/safety_spacing.py``); die semantische Arbeit
(welche Netze bilden die HV-Domäne, welche Schutzklasse hat das Gerät) bleibt
beim Agenten, das Messen der Ist-Abstände bei den Geometrie-Tools.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from kicad_mcp.utils import safety_spacing


def register_safety_tools(mcp: FastMCP) -> None:
    """Register safety-spacing tools with the MCP server."""

    @mcp.tool()
    def get_safety_spacing(
        working_voltage_v: float,
        nominal_mains_v: float = 0.0,
        pollution_degree: int = 2,
        material_group: str = "IIIa",
        overvoltage_category: str = "II",
        insulation: str = "basic",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Required creepage + clearance (mm) per IEC 60664-1 for a voltage boundary.

        Use this for "reicht der Abstand zwischen 230 V und Kleinspannung?",
        protection-class (Schutzklassen) reviews, or any isolation barrier
        sizing: it turns voltage + environment into the two required
        distances, with the derivation (rated impulse voltage) and the
        IEC-61140 protection-class semantics included. The values come from
        a dated, curated standards snapshot — engineering pre-check, NOT a
        certification. Rendert nicht; liest kein Board (die Ist-Abstände
        misst z. B. ``center_item_clearance``).

        Args:
            working_voltage_v: Arbeitsspannung über der Isolationsstrecke
                (RMS, V) — bestimmt die Kriechstrecke (Tabelle F.4).
            nominal_mains_v: Netz-Nennspannung Leiter-Neutral (V) für die
                Stoßspannungswahl (Tabelle F.1); 0 = Arbeitsspannung nehmen.
                Für 230-V-Netze 230 angeben (Reihe "≤ 300 V").
            pollution_degree: Verschmutzungsgrad 1-3 (PCB in Gehäuse
                typisch 2).
            material_group: CTI-Materialgruppe I (CTI≥600), II (400-599),
                III/IIIa/IIIb (100-399; FR-4 typisch IIIa).
            overvoltage_category: I-IV (netzgespeiste Geräte typisch II,
                Festinstallation III).
            insulation: functional | basic | reinforced | double —
                reinforced/double liefert die verstärkten Abstände
                (Schutzklasse II).

        Returns:
            ``{success, creepage_mm, clearance_mm, impulse_voltage_kv,
            working_voltage_v, nominal_mains_v, pollution_degree,
            material_group, overvoltage_category, insulation,
            protection_classes, source, snapshot_date, disclaimer}`` —
            bei ungültigen/außerhalb liegenden Parametern
            ``{success: False, error}``.
        """
        try:
            return safety_spacing.spacing_requirements(
                working_voltage_v=float(working_voltage_v),
                nominal_mains_v=float(nominal_mains_v or 0.0),
                pollution_degree=int(pollution_degree),
                material_group=str(material_group),
                overvoltage_category=str(overvoltage_category),
                insulation=str(insulation),
            )
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": f"invalid input: {exc}"}
