# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the human-readable Markdown brief for a per-IC review payload.

Purpose
-------
``review_ic_against_datasheet`` writes a structured ``review_payload.json``
plus a sibling ``review_brief.md`` that the reviewing LLM (or a human)
can open directly. The brief embeds the schematic-region PNG and the
datasheet-reference PNG by relative path, lists the pin-by-pin connection
table, and appends the hard-wired review prompt with placeholders filled.

Inputs
------
* ``payload`` — the dict that ``review_ic_against_datasheet`` returns
  before serialisation. Image paths in ``payload["images"]`` are
  rewritten to *relative* paths for the Markdown (so the brief stays
  portable if the review folder is moved).

Outputs
-------
* ``render_brief_md(payload) -> str`` — Markdown text.
* ``render_system_brief_md(payload) -> str`` — Markdown text for
  ``review_system_interconnect``.

Dependencies
------------
Stdlib only.
"""
from __future__ import annotations

import os
from typing import Any


_REVIEW_PROMPT_TEMPLATE = """\
Rolle: Schaltungsreviewer. Vergleiche Referenzdesign aus
Datenblatt mit Implementierung.
Ziel: Abweichungen finden, die Funktion, Sicherheit oder
Performance beeinflussen.

IC: {ic_ref} ({ic_value})
Pin-Range: {pin_range_text}

Methode:
- Pin fuer Pin durchgehen, Pinnummer als Anker
- Pro Pin: Referenz vs. Ist, Status OK / Abweichung / Unklar
- Zusaetzlich pruefen: Entkopplung, Power-Pfade, Pull-Ups,
  Schutzbeschaltung, Polaritaet

Output:
Tabelle | Pin | Referenz | Ist | Status | Bewertung |
Danach: Kritisch / Wichtig / Hinweis / Unklar

Regeln:
- Keine Spekulation. Unleserlich -> "Unklar".
- Bauteilwerte exakt zitieren.
- Keine Bewertung ohne Datenblatt-Begruendung.
"""

_SYSTEM_PROMPT_TEMPLATE = """\
Rolle: System-Reviewer. Pruefe Bus-Konsistenz, Power-Distribution
und Schutzbeschaltung *zwischen* den ICs.

Projekt: {project_name}
ICs im Scope: {ic_list}

Methode:
- Power-Tree: jede Schiene listen, Verbraucher zaehlen, Decoupling-
  Caps pro IC-VCC-Pin pruefen (mind. einer in <=3 mm Naehe erwartet).
- Pull-Up/Down-Audit: pro Bus-Netz (I2C / SPI-CS / Reset / Boot)
  Pull-Resistor-Anzahl + Wert; Mehrfach-Pullups auf demselben Netz
  flaggen.
- Bus-Peers: I2C-/SPI-/UART-Peer-Liste je Netz auf Plausibilitaet
  checken (z.B. zwei SPI-Master auf dem gleichen Net = Fehler).
- Polaritaet / Schutz: Reverse-Polarity-Diode, TVS, Serien-R?

Output: Sektion je Kategorie. Pro Fund: Schweregrad
(Kritisch / Wichtig / Hinweis / Unklar) + Vorschlag.

Regeln:
- Keine Spekulation. Unsicher -> "Unklar".
- Auf konkrete Refs zeigen (R5, C12, U3-Pin-7 …).
"""


def _relpath(path: str, base: str) -> str:
    if not path:
        return ""
    try:
        return os.path.relpath(path, base).replace("\\", "/")
    except ValueError:
        return path


def _format_pin_range(pin_range: Any) -> str:
    if not pin_range:
        return "alle"
    if isinstance(pin_range, (list, tuple)) and len(pin_range) == 2:
        return f"{pin_range[0]} … {pin_range[1]}"
    return str(pin_range)


def _format_connected(connected: list[dict[str, Any]]) -> str:
    if not connected:
        return "—"
    parts = []
    for c in connected:
        ref = c.get("ref", "?")
        val = c.get("value") or ""
        if val:
            parts.append(f"{ref} ({val})")
        else:
            parts.append(ref)
    return ", ".join(parts)


def render_brief_md(payload: dict[str, Any], base_dir: str) -> str:
    """Produce the per-IC review brief Markdown."""
    ic = payload.get("ic", {}) or {}
    pins = payload.get("pins", []) or []
    bom = payload.get("bom_local", []) or []
    images = payload.get("images", {}) or {}
    meta = payload.get("meta", {}) or {}
    warnings = meta.get("pin_consistency_warnings", []) or []

    img_sch = _relpath(images.get("schematic_region", ""), base_dir)
    img_ds = _relpath(images.get("datasheet_reference", ""), base_dir)
    pin_range_text = _format_pin_range(meta.get("pin_range"))

    lines: list[str] = []
    lines.append(f"# Review — {ic.get('ref', '?')} ({ic.get('value', '')})")
    lines.append("")
    lines.append(f"- **Footprint:** `{ic.get('footprint', '')}`")
    lines.append(f"- **Sheet:** `{ic.get('sheet', '')}`")
    lines.append(f"- **Pin-Range:** {pin_range_text}")
    lines.append(f"- **Generated:** {meta.get('generated_at', '')}")
    lines.append("")
    if warnings:
        lines.append("## Konsistenz-Warnungen")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## Schaltplan-Ausschnitt")
    if img_sch:
        lines.append(f"![schematic region]({img_sch})")
    else:
        lines.append("_(kein Schaltplan-Bild verfuegbar)_")
    lines.append("")

    lines.append("## Datenblatt-Referenz")
    if img_ds:
        lines.append(f"![datasheet page]({img_ds})")
    else:
        lines.append("_(kein Datenblatt-Bild verfuegbar)_")
    lines.append("")

    lines.append("## Pin-Connectivity (Ist-Zustand)")
    lines.append("")
    lines.append("| Pin | Name | Typ | Net | Verbunden mit |")
    lines.append("|----:|------|-----|-----|----------------|")
    for p in pins:
        pin = p.get("pin", "")
        name = p.get("name", "")
        typ = p.get("type", "")
        net = p.get("net", "")
        conn = _format_connected(p.get("connected", []) or [])
        lines.append(f"| {pin} | {name} | {typ} | `{net}` | {conn} |")
    lines.append("")

    if bom:
        lines.append("## BOM-Auszug (verbundene Bauteile)")
        lines.append("")
        lines.append("| Ref | Value | Footprint | Datasheet |")
        lines.append("|-----|-------|-----------|-----------|")
        for entry in bom:
            ds = entry.get("datasheet") or ""
            ref = entry.get("ref", "")
            val = entry.get("value", "")
            fp = entry.get("footprint", "")
            lines.append(f"| {ref} | {val} | `{fp}` | {ds} |")
        lines.append("")

    lines.append("## Review-Auftrag")
    lines.append("")
    lines.append("```")
    lines.append(
        _REVIEW_PROMPT_TEMPLATE.format(
            ic_ref=ic.get("ref", "?"),
            ic_value=ic.get("value", ""),
            pin_range_text=pin_range_text,
        ).rstrip()
    )
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def render_system_brief_md(payload: dict[str, Any], base_dir: str) -> str:
    """Produce the system-level review brief Markdown."""
    project_name = payload.get("project_name", "")
    ics = payload.get("ics", []) or []
    power = payload.get("power_tree", {}) or {}
    pullups = payload.get("pullup_audit", []) or []
    buses = payload.get("bus_peers", {}) or {}
    decoupling = payload.get("decoupling_audit", []) or []
    meta = payload.get("meta", {}) or {}

    lines: list[str] = []
    lines.append(f"# System-Review — {project_name}")
    lines.append("")
    lines.append(f"- **ICs im Scope:** {', '.join(ics) if ics else '(keine)'}")
    lines.append(f"- **Generated:** {meta.get('generated_at', '')}")
    lines.append("")

    if power:
        lines.append("## Power-Tree")
        lines.append("")
        lines.append("| Netz | Verbraucher | Quelle? |")
        lines.append("|------|-------------|---------|")
        for net, info in sorted(power.items()):
            count = info.get("consumer_count", 0)
            src = info.get("source_hint", "")
            lines.append(f"| `{net}` | {count} | {src} |")
        lines.append("")

    if decoupling:
        lines.append("## Decoupling-Cap-Audit")
        lines.append("")
        lines.append("| IC | VCC-Pin | Nahe Caps (<= 5 mm) | Befund |")
        lines.append("|----|---------|---------------------|--------|")
        for row in decoupling:
            ic_ref = row.get("ic", "")
            pin = row.get("pin", "")
            caps = ", ".join(row.get("nearby_caps", []) or []) or "—"
            verdict = row.get("verdict", "")
            lines.append(f"| {ic_ref} | {pin} | {caps} | {verdict} |")
        lines.append("")

    if pullups:
        lines.append("## Pull-Up / Pull-Down-Audit")
        lines.append("")
        lines.append("| Netz | Pullups | Pulldowns | Hinweis |")
        lines.append("|------|---------|-----------|---------|")
        for row in pullups:
            net = row.get("net", "")
            pu = ", ".join(row.get("pullups", []) or []) or "—"
            pd = ", ".join(row.get("pulldowns", []) or []) or "—"
            note = row.get("note", "")
            lines.append(f"| `{net}` | {pu} | {pd} | {note} |")
        lines.append("")

    if buses:
        lines.append("## Bus-Peers")
        lines.append("")
        for iface, nets in sorted(buses.items()):
            lines.append(f"### {iface}")
            for net in nets:
                lines.append(f"- `{net}`")
            lines.append("")

    lines.append("## Review-Auftrag")
    lines.append("")
    lines.append("```")
    lines.append(
        _SYSTEM_PROMPT_TEMPLATE.format(
            project_name=project_name,
            ic_list=", ".join(ics) if ics else "(keine)",
        ).rstrip()
    )
    lines.append("```")
    lines.append("")
    return "\n".join(lines)
