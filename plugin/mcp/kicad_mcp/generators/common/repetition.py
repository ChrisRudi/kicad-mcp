# SPDX-License-Identifier: GPL-3.0-or-later
"""Wiederholte Teilschaltungen erkennen — für uniformes, symmetrisches Layout.

Nutzer-Regel: „Wiederholung im Schaltplan sollte zu gleichartigen
Schaltungsteilen führen" — die zwei Hälften eines Multivibrators, die Glieder
einer LED-Kette. Dieses Modul findet solche Instanzen strukturell; die
Platzierung (``schematic/place._uniform_repeated_units``) stampt dann das
Layout der ersten Instanz auf alle weiteren und stellt sie in Leseordnung.

Konservativ geschaltet, damit es nur bei ECHTER Wiederholung feuert:
  * Anker = Bauteil-Signatur (Präfix, Wert, Pin-Zahl) mit ≥ 3 Pins, die
    genau k ≥ 2-mal vorkommt (Q:BC547:3 ×2, WS2812B:4 ×6) — 2-Pin-Massenware
    (vier gleiche Pull-ups) ist KEINE Teilschaltungs-Wiederholung.
  * Jede weitere Signatur mit Vorkommen == k wird per Signal-Netz-Nähe
    (BFS) eindeutig einem Anker zugeschlagen; Reste bleiben draußen.
  * Alle Einheiten müssen dieselbe Signatur-Multimenge tragen, sonst
    verwerfen wir die Erkennung komplett (lieber kein Muster als ein
    falsches).

Pure/stdlib, deterministisch (nur sortierte Iteration).
"""

from __future__ import annotations

import re


def _sig(part: dict) -> tuple[str, str, int]:
    ref = part.get("ref", "")
    prefix = "".join(c for c in ref if c.isalpha())
    return (prefix, str(part.get("value", "")), len(part.get("pins", [])))


def _natural_ref_key(ref: str) -> tuple:
    m = re.match(r"^([A-Za-z]+)(\d+)$", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


def _signal_adjacency(parts: list[dict], nets: list[dict]) -> dict[str, set[str]]:
    """ref → direkt über SIGNAL-Netze verbundene refs (Power verbindet alles
    mit allem und trägt keine Instanz-Information)."""
    adj: dict[str, set[str]] = {p.get("ref", ""): set() for p in parts}
    for net in nets:
        if net.get("type") == "power":
            continue
        refs = sorted({c.split(":")[0] for c in net.get("connections", [])})
        for r in refs:
            if r in adj:
                adj[r].update(x for x in refs if x != r)
    return adj


def find_repeated_units(parts: list[dict], nets: list[dict]) -> list[list[str]]:
    """Instanzen einer wiederholten Teilschaltung finden.

    Returns:
        Liste von Einheiten in Leseordnung (Anker-Ref natürlich sortiert),
        jede Einheit = Liste von refs (Anker zuerst). Leer, wenn keine
        verlässliche Wiederholung erkannt wurde.
    """
    by_sig: dict[tuple, list[str]] = {}
    for p in sorted(parts, key=lambda q: _natural_ref_key(q.get("ref", ""))):
        by_sig.setdefault(_sig(p), []).append(p.get("ref", ""))

    # Anker: ≥3-Pin-Signatur mit den meisten Instanzen (dann meiste Pins)
    anchors: list[str] = []
    k = 0
    for sig, refs in sorted(by_sig.items()):
        if sig[2] >= 3 and len(refs) >= 2:
            if len(refs) > k or (len(refs) == k and anchors
                                 and sig[2] > len(anchors)):
                anchors, k = refs, len(refs)
    if k < 2:
        return []

    units: dict[str, list[str]] = {a: [a] for a in anchors}

    # Mitglieder: jede andere Signatur, die GENAU k-mal vorkommt, wird per
    # BFS-Distanz über Signal-Netze dem nächsten Anker zugeordnet.
    adj = _signal_adjacency(parts, nets)
    dist: dict[str, dict[str, int]] = {}
    for a in anchors:
        d = {a: 0}
        frontier = [a]
        while frontier:
            nxt = []
            for r in frontier:
                for n in sorted(adj.get(r, ())):
                    if n not in d:
                        d[n] = d[r] + 1
                        nxt.append(n)
            frontier = nxt
        dist[a] = d

    anchor_set = set(anchors)
    for sig, refs in sorted(by_sig.items()):
        if len(refs) != k or refs[0] in anchor_set:
            continue
        # Zuordnung: ref → (Distanz, Anker) minimal; muss BIJEKTIV aufgehen
        taken: set[str] = set()
        assign: dict[str, str] = {}
        for ref in refs:
            cands = sorted(
                (dist[a].get(ref, 10**6), a) for a in anchors if a not in taken)
            if not cands or cands[0][0] >= 10**6:
                assign = {}
                break
            assign[ref] = cands[0][1]
            taken.add(cands[0][1])
        if len(assign) == k:
            for ref, a in assign.items():
                units[a].append(ref)

    # Validierung: alle Einheiten strukturgleich (gleiche Signatur-Multimenge)
    def unit_shape(refs: list[str]) -> tuple:
        ref_to_part = {p.get("ref", ""): p for p in parts}
        return tuple(sorted(_sig(ref_to_part[r]) for r in refs))

    shapes = {unit_shape(u) for u in units.values()}
    if len(shapes) != 1:
        return []

    return [units[a] for a in sorted(anchors, key=_natural_ref_key)]
