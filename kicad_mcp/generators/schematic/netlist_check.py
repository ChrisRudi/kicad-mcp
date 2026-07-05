# SPDX-License-Identifier: GPL-3.0-or-later
"""Netzlisten-Roundtrip: stimmt der GEZEICHNETE Schaltplan elektrisch mit der
Soll-Netzliste überein?

Der Generator bekommt eine Netzliste (``nets``) und zeichnet Drähte, Stubs,
Labels und Power-Symbole. Ob das Gezeichnete dieselbe Konnektivität ergibt,
entscheidet allein KiCads eigene Verbindungs-Engine — deshalb wird die
Ist-Netzliste über ``kicad-cli sch export netlist`` aus der fertigen
``.kicad_sch`` extrahiert (das „aus dem Bild") und pin-genau gegen die
Soll-Netze verglichen. Netznamen dürfen abweichen (KiCad benennt nach Label);
verglichen werden die PIN-GRUPPEN.

Ergebnis-Vokabular:
    merged   mehrere Soll-Netze landen im selben Ist-Netz  → Kurzschluss
    split    ein Soll-Netz zerfällt in mehrere Ist-Netze   → offene Verbindung
    missing  Soll-Pin fehlt im Ist                          → nicht angeschlossen
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

# \b statt Leerzeichen im Lookahead: kicad-cli pretty-printet ``(net`` mit
# ZEILENUMBRUCH danach — ``(?=\(net )`` verpasste jede Folge-Net-Grenze und
# das erste Netz „verschluckte" alle weiteren (sah aus wie ein Total-Kurzschluss).
_NET_RE = re.compile(
    r'\(net\s+\(code "\d+"\)\s+\(name "([^"]*)"\)(.*?)(?=\(net\b|\Z)', re.DOTALL)
_NODE_RE = re.compile(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)')


def extract_netlist(sch_path: str) -> dict[str, set[tuple[str, str]]] | None:
    """Ist-Netzliste des gezeichneten Schaltplans über kicad-cli.

    Returns ``{netname: {(ref, pin), …}}`` oder ``None``, wenn kicad-cli
    nicht verfügbar ist (Aufrufer skippt dann)."""
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "out.net")
        try:
            r = subprocess.run(
                ["kicad-cli", "sch", "export", "netlist",
                 "--format", "kicadsexpr", "-o", out, sch_path],
                capture_output=True, text=True, timeout=120, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0 or not os.path.exists(out):
            return None
        text = open(out, encoding="utf-8").read()
    nets: dict[str, set[tuple[str, str]]] = {}
    for m in _NET_RE.finditer(text):
        nodes = {(ref, pin) for ref, pin in _NODE_RE.findall(m.group(2))}
        if nodes:
            nets[m.group(1)] = nodes
    return nets


def build_pin_aliases(parts: list[dict]) -> dict[tuple[str, str], tuple[str, str]]:
    """Alias-Map Soll-Pin → realer Symbol-Pin, mit derselben Namens-zuerst-
    Logik wie der Generator (``route._map_user_to_real_pins``).

    Kits adressieren Pins semantisch (Nummer laut Datenblatt-Paket, Name als
    Bedeutung); das reale Lib-Symbol kann anders nummerieren. Der Generator
    hängt Drähte an den NAMENS-Match — der Netzlisten-Vergleich muss dieselbe
    Übersetzung anwenden, sonst meldet er Phantom-Splits."""
    from ..symbol_lib import resolve_lib_id
    from ..symbol_cache import get_real_symbol
    from .route import _pins_from_real_symbol, _map_user_to_real_pins
    aliases: dict[tuple[str, str], tuple[str, str]] = {}
    for part in parts:
        ref = part.get("ref", "")
        raw = get_real_symbol(resolve_lib_id(part))
        if not raw:
            continue
        real_pins = _pins_from_real_symbol(raw)
        u2r = _map_user_to_real_pins(part, real_pins, raw)
        for pin in part.get("pins", []):
            unum = str(pin["num"])
            rnum = u2r.get(unum, unum)
            aliases[(ref, unum)] = (ref, rnum)
            if pin.get("name"):
                aliases[(ref, str(pin["name"]))] = (ref, rnum)
    return aliases


def _spec_pin_groups(nets: list[dict]) -> dict[str, set[tuple[str, str]]]:
    """Soll-Netze als ``{name: {(ref, pin), …}}`` (nur ref:pin-Form)."""
    groups: dict[str, set[tuple[str, str]]] = {}
    for net in nets:
        pins = set()
        for conn in net.get("connections", []):
            if ":" in conn:
                ref, pin = conn.split(":", 1)
                pins.add((ref, pin))
        if pins:
            groups[net["name"]] = pins
    return groups


def compare_netlists(
    spec_nets: list[dict],
    actual: dict[str, set[tuple[str, str]]],
    pin_aliases: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> dict:
    """Pin-genauer Vergleich Soll gegen Ist (Namen egal, Gruppen zählen).

    ``pin_aliases`` erlaubt Soll-Pin → Ist-Pin-Umbenennung (Kits adressieren
    Pins teils per Name, KiCad exportiert Pin-NUMMERN).

    Returns dict mit ``match: bool`` und Befund-Listen ``merged`` /
    ``split`` / ``missing`` (jeweils menschenlesbare Strings)."""
    spec = _spec_pin_groups(spec_nets)
    alias = pin_aliases or {}

    # Ist-Zuordnung: für jeden Soll-Pin das Ist-Netz finden
    pin_to_actual: dict[tuple[str, str], str] = {}
    for aname, apins in actual.items():
        for p in apins:
            pin_to_actual[p] = aname

    merged: list[str] = []
    split: list[str] = []
    missing: list[str] = []

    # split / missing: alle Pins EINES Soll-Netzes müssen im SELBEN Ist-Netz sein
    spec_net_actual: dict[str, str] = {}
    for sname, spins in spec.items():
        homes: dict[str, list[tuple[str, str]]] = {}
        for p in spins:
            q = alias.get(p, p)
            home = pin_to_actual.get(q)
            if home is None:
                missing.append(f"{sname}: Pin {p[0]}:{p[1]} nicht angeschlossen")
            else:
                homes.setdefault(home, []).append(p)
        if len(homes) > 1:
            parts = ["+".join(f"{r}:{n}" for r, n in v) for v in homes.values()]
            split.append(f"{sname} zerfällt in {len(homes)} Teile: " + " | ".join(parts))
        if homes:
            spec_net_actual[sname] = max(homes, key=lambda k: len(homes[k]))

    # merged: zwei verschiedene Soll-Netze im selben Ist-Netz = Kurzschluss
    by_actual: dict[str, list[str]] = {}
    for sname, aname in spec_net_actual.items():
        by_actual.setdefault(aname, []).append(sname)
    for aname, snames in by_actual.items():
        if len(snames) > 1:
            merged.append("Kurzschluss: " + " + ".join(sorted(snames))
                          + f" (Ist-Netz {aname})")

    return {
        "match": not merged and not split and not missing,
        "merged": merged,
        "split": split,
        "missing": missing,
        "n_spec_nets": len(spec),
        "n_actual_nets": len(actual),
    }
