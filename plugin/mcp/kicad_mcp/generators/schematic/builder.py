# SPDX-License-Identifier: GPL-3.0-or-later
"""
KiCad Schematic (.kicad_sch) builder — symbol emission, lib symbols, instances.

Strangler-fig extraction from schematic_builder.py — all functions
copied verbatim (no behaviour changes).

Callers:
  - schematic_builder.py   (re-exports build_schematic for backward compat)
  - generation_tools.py    (build_schematic via schematic_builder)
  - esphome_tools.py       (build_schematic via schematic_builder)
  - tests                  (build_schematic via schematic_builder)

Routing rules (IEC 61082 / EDA best practices):
- Wires never pass through component bounding boxes
- No 4-way wire junctions (T-junctions only)
- Labels replace wires longer than WIRE_MAX_LENGTH
- Feedback paths use labels instead of wires across the sheet
- Text is always horizontal
- Functional block frames around component groups
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
import logging
import re as _re

# Eine emittierte Draht-Zeile: (wire (pts (xy x1 y1) (xy x2 y2)) (uuid "…"))
_WIRE_LINE_RE = _re.compile(
    r'^(\s*)\(wire \(pts \(xy (-?[\d.]+) (-?[\d.]+)\) '
    r'\(xy (-?[\d.]+) (-?[\d.]+)\)\) \(uuid "([^"]+)"\)\)\s*$')

# Eine emittierte Label-Zeile (label/global_label/hierarchical_label, einzeilig).
_LABEL_LINE_RE = _re.compile(
    r'^(\s*)\((label|global_label|hierarchical_label) "([^"]*)" '
    r'\(at (-?[\d.]+) (-?[\d.]+) (\d+)\)(.*)\)\s*$')

# Auswärts-Richtung → (Winkel, Einheitsvektor). KiCad: 0=Text rechts, 180=links,
# 90=oben (−y), 270=unten (+y).
_DECL_DIRS = {"right": (0, (1.0, 0.0)), "left": (180, (-1.0, 0.0)),
              "up": (90, (0.0, -1.0)), "down": (270, (0.0, 1.0))}
_DECL_CW, _DECL_LH = 0.6, 1.4   # wie in layout_measure (Referenzen bleiben 0)


def _decl_label_box(lx, ly, angle, text):
    w = max(len(text), 1) * _DECL_CW
    h = _DECL_LH
    if angle == 180:
        return (lx - w, ly - h / 2, lx, ly + h / 2)
    if angle == 90:
        return (lx - h / 2, ly - w, lx + h / 2, ly)
    if angle == 270:
        return (lx - h / 2, ly, lx + h / 2, ly + w)
    return (lx, ly - h / 2, lx + w, ly + h / 2)


def _declutter_labels(lines: list[str], parts: list[dict]) -> list[str]:
    """Dreht/spiegelt Netz-Labels, deren Text-Box einen FREMDEN Draht, einen
    Bauteilkörper oder ein anderes Label trifft, auf eine freie Auswärts-
    Richtung (Nutzer: „drehen oder spiegeln … auch gegen Drähte"). Der
    Label-Anker wird um seinen PIN (fernes Stub-Ende) neu gesetzt, der Stub
    entsprechend umgelegt. Findet sich keine freie Richtung, bleibt es."""
    from ..common.constants import LABEL_STUB_LEN

    # Bauteil-Rahmen aus den Parts (rotations-bewusst)
    bodies = []
    for p in parts:
        if "_place_x" not in p:
            continue
        w, h = _get_symbol_bbox(p)
        if int(p.get("_rotation", 0)) in (90, 270):
            w, h = h, w
        bodies.append((round(p["_place_x"], 2), round(p["_place_y"], 2),
                       w / 2.0, h / 2.0))

    # Pin-Welt-Positionen: ein Label, dessen Anker AUF einem Pin sitzt (das
    # Heilungs-Label „direkt am Pin"), darf NIE bewegt werden — der „Stub" an
    # seinem Anker ist der PIN-STUB; ihn umzulegen trennt den Pin von seiner
    # Route (die R1:2-„zerfällt in 2 Teile"-Insel im Netzlisten-Roundtrip).
    pin_pts: set[tuple[float, float]] = set()
    for p in parts:
        if "_place_x" not in p:
            continue
        try:
            pos = _extract_pin_positions(resolve_lib_id(p), p)
        except Exception:
            continue
        for _num, (plx, ply) in pos.items():
            pin_pts.add((round(p["_place_x"] + plx, 2),
                         round(p["_place_y"] + ply, 2)))

    # Draht-Segmente + Label-Zeilen einsammeln
    wires = []   # (idx, x1, y1, x2, y2, indent, uuid)
    labels = []  # (idx, kind, name, x, y, angle, rest, indent)
    for i, ln in enumerate(lines):
        mw = _WIRE_LINE_RE.match(ln)
        if mw:
            wires.append((i, float(mw.group(2)), float(mw.group(3)),
                          float(mw.group(4)), float(mw.group(5)),
                          mw.group(1), mw.group(6)))
            continue
        ml = _LABEL_LINE_RE.match(ln)
        if ml:
            labels.append((i, ml.group(2), ml.group(3), float(ml.group(4)),
                           float(ml.group(5)), int(ml.group(6)), ml.group(7),
                           ml.group(1)))

    def _box_hits_body(box):
        cx, cy = (box[0]+box[2])/2, (box[1]+box[3])/2
        hw, hh = (box[2]-box[0])/2 - 0.2, (box[3]-box[1])/2 - 0.2
        for bx, by, bhw, bhh in bodies:
            # Zone wie die Metrik: Körper + Pin-Zone (2.84) — sonst hält der
            # Declutter Positionen für frei, die die Metrik als „Label in der
            # Pin-Nummern-Spalte" zählt, und repariert sie nie.
            if abs(cx-bx) < bhw+2.84+hw and abs(cy-by) < bhh+2.84+hh:
                return True
        return False

    def _box_hits_wire(box, skip_idx):
        cx, cy = (box[0]+box[2])/2, (box[1]+box[3])/2
        hw, hh = (box[2]-box[0])/2 - 0.1, (box[3]-box[1])/2 - 0.1
        from . import layout_measure as _lm
        for wi in wires:
            if wi[0] == skip_idx:
                continue
            if _lm._seg_through_rect(wi[1], wi[2], wi[3], wi[4], cx, cy, hw, hh):
                return True
        return False

    def _box_hits_label(box, self_i):
        for lb in labels:
            if lb[0] == self_i:
                continue
            b2 = _decl_label_box(lb[3], lb[4], lb[5], lb[2])
            if (box[0] < b2[2]-0.2 and b2[0] < box[2]-0.2
                    and box[1] < b2[3]-0.2 and b2[1] < box[3]-0.2):
                return True
        return False

    # Endpunkt-Grad: an wie vielen Draht-Enden hängt ein Punkt? Ein Label darf
    # NUR bewegt werden, wenn sein Anker eine reine Stichleitung abschließt
    # (Grad 1). Bei Grad ≥2 führt am Anker eine Route weiter — den Stub
    # umzulegen risse sie ab (die „zerfällt in 2 Teile"-Splits des
    # Netzlisten-Roundtrips kamen genau daher).
    _deg: dict[tuple[float, float], int] = {}
    for wi in wires:
        for p in ((round(wi[1], 2), round(wi[2], 2)),
                  (round(wi[3], 2), round(wi[4], 2))):
            _deg[p] = _deg.get(p, 0) + 1

    def _seg_touches_wires(x1, y1, x2, y2, skip_idx) -> bool:
        """Berührt das neue Stub-Segment ein anderes Draht-Segment ELEKTRISCH
        (Endpunkt-auf-Segment / gemeinsamer Endpunkt am neuen Anker /
        kollineare Überlappung)? Der Pin-Endpunkt darf natürlich anliegen."""
        def on(px, py, a1, b1, a2, b2):
            if abs(b1 - b2) < 0.01:
                return abs(py - b1) < 0.01 and min(a1, a2) - 0.01 <= px <= max(a1, a2) + 0.01
            if abs(a1 - a2) < 0.01:
                return abs(px - a1) < 0.01 and min(b1, b2) - 0.01 <= py <= max(b1, b2) + 0.01
            return False
        for wi in wires:
            if wi[0] == skip_idx:
                continue
            wx1, wy1, wx2, wy2 = wi[1], wi[2], wi[3], wi[4]
            # neuer Anker (x2,y2) auf/an fremdem Draht?
            if on(x2, y2, wx1, wy1, wx2, wy2):
                return True
            # fremdes Draht-Ende mitten auf dem neuen Stub?
            for ex, ey in ((wx1, wy1), (wx2, wy2)):
                if (abs(ex - x1) < 0.01 and abs(ey - y1) < 0.01):
                    continue  # Pin-Ende — dort dürfen andere anliegen
                if on(ex, ey, x1, y1, x2, y2):
                    return True
        return False

    out = list(lines)
    for (idx, kind, name, lx, ly, angle, rest, lind) in labels:
        box = _decl_label_box(lx, ly, angle, name)
        # Stub = Draht, der am Label-Anker endet; fernes Ende = Pin
        stub = next((wi for wi in wires
                     if (abs(wi[1]-lx) < 0.05 and abs(wi[2]-ly) < 0.05)
                     or (abs(wi[3]-lx) < 0.05 and abs(wi[4]-ly) < 0.05)), None)
        conflict = (_box_hits_body(box) or _box_hits_wire(box, stub[0] if stub else -1)
                    or _box_hits_label(box, idx))
        if not conflict or not stub:
            continue
        if _deg.get((round(lx, 2), round(ly, 2)), 0) != 1:
            continue  # am Anker führt eine Route weiter → nicht bewegen
        if (round(lx, 2), round(ly, 2)) in pin_pts:
            continue  # Label sitzt AM PIN — sein „Stub" ist der Pin-Stub
        pin = (stub[3], stub[4]) if (abs(stub[1]-lx) < 0.05 and abs(stub[2]-ly) < 0.05) \
            else (stub[1], stub[2])
        # freie Richtung suchen (die aktuelle zuerst NICHT — die ist ja kollidiert)
        for _d, (nang, vec) in _DECL_DIRS.items():
            nlx = round(pin[0] + vec[0]*LABEL_STUB_LEN, 2)
            nly = round(pin[1] + vec[1]*LABEL_STUB_LEN, 2)
            nbox = _decl_label_box(nlx, nly, nang, name)
            if (_box_hits_body(nbox) or _box_hits_wire(nbox, stub[0])
                    or _box_hits_label(nbox, idx)):
                continue
            if _seg_touches_wires(pin[0], pin[1], nlx, nly, stub[0]):
                continue  # elektrischer Kontakt mit fremdem Draht → nächste
            # Label-Zeile + Stub umschreiben
            out[idx] = f'{lind}({kind} "{name}" (at {_fmt(nlx)} {_fmt(nly)} {nang}){rest})'
            out[stub[0]] = (f'{stub[5]}(wire (pts (xy {_fmt(pin[0])} {_fmt(pin[1])}) '
                            f'(xy {_fmt(nlx)} {_fmt(nly)})) (uuid "{stub[6]}"))')
            break
    return out


def _merge_overlapping_wires(lines: list[str]) -> list[str]:
    """Führe kollinear ÜBEREINANDER liegende Draht-Segmente zusammen.

    „keine Leitungen übereinander": der Router legt gelegentlich zwei Segmente
    desselben Netzes auf dieselbe Spur (teil-überlappend). Da sich überlappende
    kollineare Segmente in KiCad elektrisch mit ihrer Vereinigung decken (und ein
    Über­lappen zweier Netze wäre ohnehin schon ein Kurzschluss, den das Vereinen
    nicht ändert), ersetzen wir überlappende Intervalle durch ihre Vereinigung.
    NUR echt überlappende (nicht bloß sich berührende) Segmente — eine
    fortlaufende Leitung mit geteiltem Endpunkt bleibt unangetastet."""
    slots, horiz, vert, indent = [], {}, {}, ""
    for i, ln in enumerate(lines):
        m = _WIRE_LINE_RE.match(ln)
        if not m:
            continue
        indent = m.group(1)
        x1, y1, x2, y2 = (float(m.group(2)), float(m.group(3)),
                          float(m.group(4)), float(m.group(5)))
        uid_ = m.group(6)
        slots.append(i)
        if abs(y1 - y2) < 0.01:            # waagrecht
            horiz.setdefault(round(y1, 2), []).append(
                (min(x1, x2), max(x1, x2), uid_))
        elif abs(x1 - x2) < 0.01:          # senkrecht
            vert.setdefault(round(x1, 2), []).append(
                (min(y1, y2), max(y1, y2), uid_))
        else:                              # diagonal → unverändert lassen
            horiz.setdefault(("diag", i), []).append((x1, y1, x2, y2, uid_))
    if not slots:
        return lines

    def _merge(intervals):
        # NUR Segmente vereinigen, die sich überlappen UND einen Endpunkt
        # teilen: ein geteilter Endpunkt heißt in KiCad „gleicher Knoten" =
        # garantiert gleiches Netz. Überlappung OHNE geteilten Endpunkt kann
        # zwei VERSCHIEDENE Netze betreffen (zwei Nachbar-Stubs übereinander)
        # — die zu vereinigen wäre ein handfester Kurzschluss.
        out = []
        for lo, hi, uid_ in sorted(intervals):
            if out and lo < out[-1][1] - 0.05 \
                    and (abs(lo - out[-1][0]) < 0.05
                         or abs(hi - out[-1][1]) < 0.05
                         or abs(lo - out[-1][1]) < 0.05
                         or abs(hi - out[-1][0]) < 0.05):
                out[-1] = (out[-1][0], max(out[-1][1], hi), out[-1][2])
            else:
                out.append((lo, hi, uid_))
        return out

    merged: list[tuple] = []
    for key, lst in horiz.items():
        if isinstance(key, tuple):          # Diagonale unverändert
            for x1, y1, x2, y2, uid_ in lst:
                merged.append((x1, y1, x2, y2, uid_))
        else:
            for lo, hi, uid_ in _merge(lst):
                merged.append((lo, key, hi, key, uid_))
    for x, lst in vert.items():
        for lo, hi, uid_ in _merge(lst):
            merged.append((x, lo, x, hi, uid_))

    out_lines = list(lines)
    for k, idx in enumerate(slots):
        if k < len(merged):
            x1, y1, x2, y2, uid_ = merged[k]
            out_lines[idx] = (f'{indent}(wire (pts (xy {_fmt(x1)} {_fmt(y1)}) '
                              f'(xy {_fmt(x2)} {_fmt(y2)})) (uuid "{uid_}"))')
        else:
            out_lines[idx] = None
    return [ln for ln in out_lines if ln is not None]

from ..sexpr import (
    SExpr, uid, KICAD_SCH_VERSION, FONT_SIZE, PIN_SPACING,
    SYM_HALF_WIDTH, PIN_LENGTH,
)
from ..spice_models import get_spice_properties
from .place import place_schematic
from ..common.bbox import _get_symbol_bbox
from ..symbol_lib import resolve_lib_id
from ..symbol_cache import get_real_symbol
from ...utils.sexpr_parser import parse_sexpr, find_node

logger = logging.getLogger(__name__)

# Routing functions needed by builder functions
from .route import (  # noqa: E402
    _emit_wires_and_labels,
    _extract_pin_positions,
    _pins_from_real_symbol, _map_user_to_real_pins,
)


def _resolve_pin_collisions(parts: list[dict], min_dist: float = 1.0) -> None:
    """Verschiebe Bauteile, deren PINS mit Pins ANDERER Bauteile zusammenfallen.

    Pin-auf-Pin (< ``min_dist`` mm) ist in KiCad eine harte elektrische
    Verbindung — zwei fremde Netze wären kurzgeschlossen, bevor ein Draht
    existiert. Das jeweils spätere Bauteil wandert in 2.54er-Schritten
    (rechts, unten, links, oben, dann weiter außen), bis seine Pins frei sind.
    Deterministisch; bricht nach 24 Versuchen ab (dann bleibt der Befund dem
    Netzlisten-Roundtrip überlassen)."""
    from .route import _extract_pin_positions

    def _world_pins(part: dict) -> list[tuple[float, float]]:
        pos = _extract_pin_positions(resolve_lib_id(part), part)
        return [(round(part["_place_x"] + lx, 2), round(part["_place_y"] + ly, 2))
                for lx, ly in pos.values()]

    placed: list[dict] = [p for p in parts if "_place_x" in p]
    taken: set[tuple[float, float]] = set()

    def _near_taken(pts: list[tuple[float, float]]) -> bool:
        for x, y in pts:
            for tx, ty in taken:
                if abs(x - tx) < min_dist and abs(y - ty) < min_dist:
                    return True
        return False

    offsets = [(2.54, 0), (0, 2.54), (-2.54, 0), (0, -2.54),
               (5.08, 0), (0, 5.08), (-5.08, 0), (0, -5.08),
               (5.08, 2.54), (2.54, 5.08), (-5.08, -2.54), (-2.54, -5.08),
               (7.62, 0), (0, 7.62), (-7.62, 0), (0, -7.62),
               (7.62, 2.54), (2.54, 7.62), (10.16, 0), (0, 10.16),
               (-10.16, 0), (0, -10.16), (10.16, 5.08), (5.08, 10.16)]
    for part in placed:
        pts = _world_pins(part)
        if _near_taken(pts):
            ox0, oy0 = part["_place_x"], part["_place_y"]
            for dx, dy in offsets:
                part["_place_x"] = round(ox0 + dx, 2)
                part["_place_y"] = round(oy0 + dy, 2)
                pts = _world_pins(part)
                if not _near_taken(pts):
                    break
            else:
                part["_place_x"], part["_place_y"] = ox0, oy0
                pts = _world_pins(part)
        taken.update(pts)


def build_schematic(
    parts: list[dict],
    nets: list[dict],
    project_name: str = "project",
    simulation: bool = False,
    intersheet_nets: list[dict] | None = None,
    place: bool = True,
    keep_placement: bool = False,
    optimize: bool = False,
    optimize_evals: int = 1500,
    optimize_seconds: float = 30.0,
) -> str:
    """Build a .kicad_sch file from parts and nets.

    ``intersheet_nets`` (optional) — list of net dicts that cross sheet
    boundaries on the parent root-sheet (as produced by
    :func:`kicad_mcp.generators.schematic.multisheet.find_intersheet_nets`).
    Signal nets with a name in this set get a hierarchical_label on the
    sub-sheet so the matching pin on the root's sheet-symbol resolves.
    Pass ``None`` (default) on single-sheet projects.

    ``place`` (default True) runs the auto-placement pipeline. Pass ``False``
    to emit from a placement the caller already put on the parts (used by the
    layout optimizer to re-emit candidate placements without the pipeline
    overwriting its moves). ``keep_placement`` (default False) leaves the
    ``_place_*`` metadata on the parts after emission so the caller can emit
    again — the optimizer needs this; normal one-shot builds clean up.

    ``optimize`` (default False) runs the layout optimizer
    (:func:`layout_optimizer.optimize`) after placement: a real hill-climb that
    re-emits + measures candidate placements and keeps only those that lower
    the objective ``badness`` (overlaps, label direction, crossings). It never
    makes the layout worse than the pipeline's. ``optimize_evals`` caps the
    search budget. Only meaningful together with ``place=True``.
    """
    # Auto-place components
    if place:
        place_schematic(parts, nets)
        if optimize:
            from .layout_optimizer import optimize as _optimize_layout

            def _emit_candidate() -> str:
                return build_schematic(
                    parts, nets, project_name, simulation,
                    intersheet_nets, place=False, keep_placement=True,
                )
            _optimize_layout(parts, nets, _emit_candidate,
                             max_evals=optimize_evals,
                             max_seconds=optimize_seconds)

    # ``_extra_units`` wird bei jedem Emit aus der Platzierung neu berechnet
    # (``_emit_symbol_instances``). Beim wiederholten Emit derselben Parts
    # (Optimizer, keep_placement) würde es sich sonst aufsummieren → doppelte
    # Zusatz-Units. Vor jedem Emit zurücksetzen.
    for part in parts:
        part.pop("_extra_units", None)

    # Pin-Kollisions-Auflösung: liegen PINS zweier verschiedener Bauteile auf
    # demselben Punkt (Placement-Pech im 2.54er-Raster: R2:1 exakt auf U1:7 des
    # MP1584-Platzhalters), sind die Netze in KiCad hart verbunden — kein
    # Routing kann das reparieren. Das später platzierte Bauteil wird
    # deterministisch in 2.54er-Schritten verschoben, bis alle Pins frei sind.
    _resolve_pin_collisions(parts)

    s = SExpr()

    # Header
    s.open("kicad_sch")
    s.prop("version", KICAD_SCH_VERSION)
    s.prop_quoted("generator", "kicad-mcp")
    s.prop_quoted("generator_version", "1.0")
    s.emit(f'(uuid "{uid(f"{project_name}_sch")}")')
    s.prop_quoted("paper", "A4")
    s.blank()

    # Lib symbols section
    _emit_lib_symbols(s, parts, nets, project_name)
    s.blank()

    # Symbol instances (placed by auto_place)
    _emit_symbol_instances(s, parts, project_name, simulation)
    s.blank()

    # PWR_FLAG symbols on power nets
    _emit_pwr_flags(s, parts, nets, project_name)
    s.blank()

    # Functional block frames (dashed rectangles around groups)
    _emit_block_frames(s, parts, project_name)
    s.blank()

    # Wires + labels — respecting routing rules. Sub-sheet calls pass
    # `intersheet_nets` so cross-sheet signals get hierarchical labels
    # that match the pins on the root sheet symbol.
    _hier_names: set[str] | None = (
        {n["name"] for n in intersheet_nets} if intersheet_nets else None
    )
    labeled_positions = _emit_wires_and_labels(
        s, parts, nets, project_name, intersheet_nets=_hier_names,
    )
    s.blank()

    # No-connect flags on unused pins
    _emit_no_connects(s, parts, nets, project_name, labeled_positions)
    s.blank()

    # Sheet + Symbol instances
    _emit_instances(s, parts, project_name)

    s.close()  # kicad_sch

    # Kollidierende Netz-Labels (Text-Box über Draht/Körper/Label) auf eine freie
    # Auswärts-Richtung drehen/spiegeln — dann die kollinear überlappenden
    # Draht-Segmente (auch die umgelegten Stubs) zu ihrer Vereinigung mergen.
    s._lines = _declutter_labels(s._lines, parts)
    s._lines = _merge_overlapping_wires(s._lines)

    # Clean up placement metadata — unless the caller wants to re-emit (optimizer)
    if not keep_placement:
        for part in parts:
            part.pop("_place_x", None)
            part.pop("_place_y", None)
            part.pop("_group", None)
            part.pop("_rotation", None)
            part.pop("_extra_units", None)

    return s.render()


# ── Lib symbols ──────────────────────────────────────────────────────────────

def _symbol_name(lib_id: str) -> str:
    if ":" in lib_id:
        return lib_id.split(":", 1)[1]
    return lib_id


def _fmt(v: float) -> str:
    return f"{round(v, 4):g}"


def _emit_lib_symbols(s: SExpr, parts: list[dict], nets: list[dict], project_name: str) -> None:
    s.open("lib_symbols")
    seen = set()
    for part in parts:
        lib_id = resolve_lib_id(part)
        if lib_id in seen:
            continue
        seen.add(lib_id)
        real_sym = get_real_symbol(lib_id)
        if real_sym:
            indented = _indent_block(real_sym, s._indent)
            s._lines.append(indented)
            logger.info(f"Embedded real KiCad symbol: {lib_id}")
        else:
            logger.warning(f"Symbol '{lib_id}' not found in KiCad libraries, using placeholder")
            _emit_placeholder_symbol(s, part, lib_id)

    pwr_flag = get_real_symbol("power:PWR_FLAG")
    if pwr_flag:
        indented = _indent_block(pwr_flag, s._indent)
        s._lines.append(indented)

    # Embed lib_symbols for real KiCad power symbols used by power nets
    from .route import get_power_symbol_info
    power_lib_ids_seen: set[str] = set()
    for net in nets:
        if net.get("type") != "power":
            continue
        pwr_info = get_power_symbol_info(net["name"])
        if pwr_info:
            lib_id = pwr_info[0]
            if lib_id not in power_lib_ids_seen:
                power_lib_ids_seen.add(lib_id)
                pwr_sym = get_real_symbol(lib_id)
                if pwr_sym:
                    indented = _indent_block(pwr_sym, s._indent)
                    s._lines.append(indented)
                    logger.info(f"Embedded power symbol: {lib_id}")

    s.close()


def _indent_block(text: str, indent_level: int) -> str:
    prefix = "  " * indent_level
    lines = text.split("\n")
    return "\n".join(prefix + line if line.strip() else "" for line in lines)


def _emit_placeholder_symbol(s: SExpr, part: dict, lib_id: str) -> None:
    pins = part.get("pins", [])
    n_pins = len(pins)
    half_h = max(n_pins * FONT_SIZE, FONT_SIZE * 2)
    ref_prefix = part["ref"][0] if part["ref"] else "U"
    sym_name = _symbol_name(lib_id)

    s.open("symbol", f'"{lib_id}"')
    s.kicad_property("Reference", ref_prefix, 0, round(half_h + PIN_SPACING, 4))
    s.kicad_property("Value", part.get("value", part["name"]), 0, round(-(half_h + PIN_SPACING), 4))
    s.kicad_property("Footprint", part.get("footprint", ""), 0, round(-(half_h + PIN_SPACING * 2), 4), hide=True)

    s.open("symbol", f'"{sym_name}_0_1"')
    s.emit(
        f"(rectangle (start -{_fmt(SYM_HALF_WIDTH)} {_fmt(half_h)}) (end {_fmt(SYM_HALF_WIDTH)} {_fmt(-half_h)})"
        f" (stroke (width 0.254) (type default)) (fill (type background)))"
    )
    for i, pin in enumerate(pins):
        y = round((n_pins - 1) * FONT_SIZE - i * PIN_SPACING, 4)
        pin_type = pin.get("type", "passive")
        s.pin(pin_type, pin["name"], pin["num"],
              round(-(SYM_HALF_WIDTH + PIN_LENGTH), 4), y, angle=0)
    s.close()
    s.close()


# ── Multi-unit detection ─────────────────────────────────────────────────────

_UNITS_CACHE: dict[str, dict[int, list[dict]]] = {}


def _detect_units(lib_id: str) -> dict[int, list[dict]]:
    """Detect units in a multi-unit KiCad symbol.

    Returns: {unit_number: [{'num': pin_num, 'x': x, 'y': y, 'type': type}, ...]}
    Empty dict if single-unit or not found.

    Memoisiert je lib_id — reines Parsen des (stabilen) Symbol-Texts, aber
    Emit ruft es pro Bauteil mehrfach (Instanz, No-Connect, Sheet-Instanz).
    """
    if lib_id in _UNITS_CACHE:
        return _UNITS_CACHE[lib_id]
    raw = get_real_symbol(lib_id)
    if not raw:
        _UNITS_CACHE[lib_id] = {}
        return {}

    tree = parse_sexpr(raw)
    units: dict[int, list[dict]] = {}

    def _walk(node: list) -> None:
        if not isinstance(node, list) or not node:
            return
        if node[0] == "symbol" and len(node) > 1 and isinstance(node[1], str):
            m = _re.match(r'.*_(\d+)_(\d+)$', node[1])
            if m:
                unit_num = int(m.group(1))
                pins = []
                def _find_pins(n):
                    if not isinstance(n, list) or not n:
                        return
                    if n[0] == "pin":
                        at = find_node(n, "at")
                        num_node = find_node(n, "number")
                        if at and num_node and len(at) >= 3 and len(num_node) >= 2:
                            pin_type = n[1] if len(n) > 1 and isinstance(n[1], str) and n[1] != "(" else "passive"
                            pins.append({
                                "num": str(num_node[1]),
                                "x": float(at[1]),
                                "y": -float(at[2]),  # Negate Y: lib uses math Y, schematic uses screen Y
                                "type": pin_type,
                            })
                    for c in n:
                        if isinstance(c, list):
                            _find_pins(c)
                _find_pins(node)
                if pins:
                    units[unit_num] = pins
        for child in node:
            if isinstance(child, list):
                _walk(child)

    _walk(tree)
    return units


def _find_user_unit(part: dict, units: dict[int, list[dict]]) -> int:
    """Determine which unit the user's pins belong to."""
    user_pin_nums = {str(p["num"]) for p in part.get("pins", [])}
    user_pin_names = {p.get("name", "") for p in part.get("pins", [])}

    best_unit = 1
    best_overlap = 0
    for unit_num, unit_pins in units.items():
        unit_pin_nums = {p["num"] for p in unit_pins}
        overlap = len(user_pin_nums & unit_pin_nums) + len(user_pin_names & unit_pin_nums)
        if overlap > best_overlap:
            best_overlap = overlap
            best_unit = unit_num
    return best_unit


# ── Symbol instances ─────────────────────────────────────────────────────────

def _emit_symbol_instances(s: SExpr, parts: list[dict], project_name: str, simulation: bool = False) -> None:
    for part in parts:
        ref = part["ref"]
        lib_id = resolve_lib_id(part)
        x = round(part.get("_place_x", 50.8), 2)
        y = round(part.get("_place_y", 38.1), 2)
        sym_uid = uid(f"{project_name}_sym_{ref}")

        # Detect multi-unit symbols
        units = _detect_units(lib_id)
        user_unit = _find_user_unit(part, units) if units else 1

        rot = int(part.get("_rotation", 0))
        s.open("symbol", f'(lib_id "{lib_id}") (at {_fmt(x)} {_fmt(y)} {rot}) (unit {user_unit})')
        s.emit('(in_bom yes) (on_board yes)')
        s.emit(f'(uuid "{sym_uid}")')

        # Referenz/Wert-Platzierung: bei mehrpinnigen ICs (Pins links/rechts)
        # kommen sie ÜBER bzw. UNTER den Körper — sonst liegen sie auf den
        # seitlichen Pinreihen und Pin-Namen (der Text-Stau am IC). 2-Pin-
        # Passives behalten die Seiten-Platzierung (bewährt, an der Referenz auf
        # 0 Annotations-Überlappung geeicht). „IC" = > 4 Pins.
        sym_w, sym_h = _get_symbol_bbox(part)
        n_pins = len(part.get("pins", []))
        is_ic = n_pins > 4
        if is_ic and rot in (0, 180):
            # Pins liegen links/rechts → Referenz oben, Wert unten (auf x zentriert).
            # Hat der IC aber Pins an der UNTER-/OBERkante (LAN8720: GND-Reihe
            # unten), stehen dort dessen Power-Symbole — Wert/Referenz weichen
            # dann 2 Raster weiter aus, sonst liegt der Wertetext exakt auf dem
            # GND-Symbol (ethernet: „LAN8720"⧉GND, annot_overlap).
            top_air = bot_air = 0.0
            try:
                from .route import _extract_pin_positions
                for _, (_, py) in _extract_pin_positions(lib_id, part).items():
                    if py >= sym_h / 2 - 0.1:
                        bot_air = 5.08
                    elif py <= -sym_h / 2 + 0.1:
                        top_air = 5.08
            except Exception:
                pass  # ohne Pin-Geometrie: bisheriges Verhalten
            ref_x = round(x, 2)
            ref_y = round(y - sym_h / 2 - FONT_SIZE - top_air, 2)
            val_x = round(x, 2)
            val_y = round(y + sym_h / 2 + FONT_SIZE + bot_air, 2)
            hidden_x, hidden_y = ref_x, ref_y
        else:
            if rot == 90 or rot == 270:
                label_x = round(x + sym_h / 2 + 2.0, 2)
            else:
                label_x = round(x + sym_w / 2 + 2.0, 2)
            ref_x = val_x = label_x
            ref_y = round(y - FONT_SIZE, 2)
            val_y = round(y + FONT_SIZE, 2)
            hidden_x, hidden_y = label_x, round(y + FONT_SIZE * 3, 2)
        # KiCad rendert Property-Text RELATIV zur Symbol-Rotation: bei einem um
        # 90/270° gedrehten Bauteil würde angle=0 vertikal gezeichnet — Referenz
        # und Wert (gleiches x!) lägen als Buchstabensalat übereinander
        # („10uC1", „22uG2", der Text-Stau an JEDEM liegenden C/R/D). Die
        # Gegenrotation macht die effektive Darstellung wieder horizontal:
        # Referenz oben, Wert darunter — wie KiCads eigene Feld-Autoplatzierung.
        prop_angle = {90: 270, 270: 90}.get(rot, 0)
        s.kicad_property("Reference", ref, ref_x, ref_y, angle=prop_angle)
        s.kicad_property("Value", part.get("value", part["name"]), val_x, val_y, angle=prop_angle)
        s.kicad_property("Footprint", part.get("footprint", ""), hidden_x, hidden_y, hide=True)

        if simulation:
            sim_props = part.get("sim_properties") or get_spice_properties(part)
            for key, val in sim_props.items():
                s.kicad_property(key, val, hidden_x, round(hidden_y + FONT_SIZE * 2, 2), hide=True)

        real_sym = get_real_symbol(lib_id)
        if real_sym:
            real_pins = _pins_from_real_symbol(real_sym)
            emitted_pins = set()
            user_to_real = _map_user_to_real_pins(part, real_pins, real_sym)
            for pin in part.get("pins", []):
                real_num = user_to_real.get(str(pin["num"]), str(pin["num"]))
                pin_uid = uid(f"{project_name}_{ref}_pin{real_num}")
                s.pin_instance(real_num, pin_uid)
                emitted_pins.add(real_num)
            # Emit remaining pins from the same unit AND unit 0 (common/shared pins)
            unit_pins = set()
            if units:
                unit_pins = {p["num"] for p in units.get(user_unit, [])}
                # Unit 0 is the "common" unit — its pins appear on every unit
                unit_pins |= {p["num"] for p in units.get(0, [])}
            else:
                unit_pins = set(real_pins.keys())
            # Voll-deterministischer Schlüssel: nicht-numerische Pin-Namen
            # ("A3", "B9", "SH") kollabierten auf 0 → der Stable-Sort ließ die
            # PYTHONHASHSEED-Set-Ordnung durch (Emission variierte je Prozess).
            for pnum in sorted(unit_pins,
                               key=lambda x: (0, int(x), "") if x.isdigit()
                               else (1, 0, x)):
                if pnum not in emitted_pins and pnum in real_pins:
                    pin_uid = uid(f"{project_name}_{ref}_pin{pnum}")
                    s.pin_instance(pnum, pin_uid)
        else:
            for pin in part.get("pins", []):
                pin_uid = uid(f"{project_name}_{ref}_pin{pin['num']}")
                s.pin_instance(pin["num"], pin_uid)

        s.close()

        # Multi-unit: place additional units (e.g., power unit for dual op-amps)
        if len(units) > 1:
            _emit_additional_units(s, part, lib_id, units, user_unit,
                                   x, y, project_name, parts)


def _emit_additional_units(
    s: SExpr, part: dict, lib_id: str,
    units: dict[int, list[dict]], main_unit: int,
    main_x: float, main_y: float,
    project_name: str, all_parts: list[dict],
) -> None:
    """Place additional units of multi-unit symbols (e.g., power unit of dual op-amp).

    General rule: Every unit with pins that are connected to nets gets placed.
    Power units are placed near the main unit. Unused signal units are skipped.
    """
    ref = part["ref"]

    # Build set of connected pin numbers (from user spec)
    user_pin_names = {p.get("name", "") for p in part.get("pins", [])}
    user_pin_nums = {str(p["num"]) for p in part.get("pins", [])}

    for unit_num, unit_pins in sorted(units.items()):
        if unit_num == main_unit:
            continue

        # Unit 0 is the "common" unit in KiCad — its pins appear on ALL unit
        # placements automatically. Do NOT create a separate placement for it.
        if unit_num == 0:
            continue

        # Check if this unit has power pins → always place power units
        is_power_unit = all(p.get("type") == "power_in" for p in unit_pins)

        # Check if any pin in this unit is connected via user spec
        unit_pin_nums = {p["num"] for p in unit_pins}
        has_connected_pins = bool(unit_pin_nums & user_pin_nums) or bool(unit_pin_nums & user_pin_names)

        if not is_power_unit and not has_connected_pins:
            continue

        # Place this unit near the main unit
        # Power units: offset below; signal units: offset to the right
        # Offsets MUST be multiples of 1.27mm (KiCad grid) to keep pins on-grid
        if is_power_unit:
            ux = round(main_x, 2)
            uy = round(main_y + 15.24, 2)  # 12 * 1.27 = 15.24mm below main
        else:
            ux = round(main_x + 20.32, 2)  # 16 * 1.27 = 20.32mm to the right
            uy = round(main_y, 2)

        unit_uid = uid(f"{project_name}_sym_{ref}_unit{unit_num}")

        s.open("symbol", f'(lib_id "{lib_id}") (at {_fmt(ux)} {_fmt(uy)} 0) (unit {unit_num})')
        s.emit('(in_bom no) (on_board no)')  # Only main unit in BOM
        s.emit(f'(uuid "{unit_uid}")')

        eu_label_x = round(ux + 5.0, 2)
        s.kicad_property("Reference", ref, eu_label_x, round(uy - FONT_SIZE, 2), angle=0)
        s.kicad_property("Value", part.get("value", part["name"]), eu_label_x, round(uy + FONT_SIZE, 2), angle=0, hide=True)
        s.kicad_property("Footprint", part.get("footprint", ""), round(ux, 2), round(uy - PIN_SPACING * 3, 2), hide=True)

        # Pin instances for this unit
        for pin_info in unit_pins:
            pnum = pin_info["num"]
            pin_uid = uid(f"{project_name}_{ref}_unit{unit_num}_pin{pnum}")
            s.pin_instance(pnum, pin_uid)

        s.close()

        # Store extra unit position for wire routing
        part.setdefault("_extra_units", []).append({
            "unit": unit_num,
            "x": ux,
            "y": uy,
            "pins": unit_pins,
        })

        logger.info(f"Placed {ref} unit {unit_num} ({'power' if is_power_unit else 'signal'}) at ({ux}, {uy})")


# ── PWR_FLAG ─────────────────────────────────────────────────────────────────

def _emit_pwr_flags(s: SExpr, parts: list[dict], nets: list[dict], project_name: str) -> None:
    _pin_pos_cache: dict[str, dict[str, tuple[float, float]]] = {}
    pin_to_net: dict[str, str] = {}
    for net in nets:
        for conn in net.get("connections", []):
            pin_to_net[conn] = net["name"]

    flag_count = 0
    power_net_positions: dict[str, tuple[float, float]] = {}

    for part in parts:
        ref = part["ref"]
        lib_id = resolve_lib_id(part)
        sx = round(part.get("_place_x", 50.8), 2)
        sy = round(part.get("_place_y", 38.1), 2)

        if lib_id not in _pin_pos_cache:
            _pin_pos_cache[lib_id] = _extract_pin_positions(lib_id, part)
        pin_pos = _pin_pos_cache[lib_id]

        # Build extra-unit pin lookup
        extra_units = part.get("_extra_units", [])
        extra_pin_lookup: dict[str, tuple[float, float, float, float]] = {}
        for eu in extra_units:
            for ep in eu.get("pins", []):
                extra_pin_lookup[ep["num"]] = (eu["x"], eu["y"], ep["x"], ep["y"])

        real_sym = get_real_symbol(lib_id)
        real_pins_map = _pins_from_real_symbol(real_sym) if real_sym else {}
        u2r = _map_user_to_real_pins(part, real_pins_map, real_sym) if real_pins_map else {}

        for pin in part.get("pins", []):
            conn_key = f"{ref}:{pin['name']}"
            net_name = pin_to_net.get(conn_key)
            if not net_name:
                continue
            net_obj = next((n for n in nets if n["name"] == net_name), None)
            if not net_obj or net_obj.get("type") != "power":
                continue
            if net_name in power_net_positions:
                continue
            pnum = str(pin["num"])
            real_num = u2r.get(pnum, pnum)

            # Check extra unit first
            if real_num in extra_pin_lookup:
                ux, uy, lx, ly = extra_pin_lookup[real_num]
                power_net_positions[net_name] = (round(ux + lx, 2), round(uy + ly, 2))
            else:
                lp = pin_pos.get(pnum)
                if lp:
                    power_net_positions[net_name] = (round(sx + lp[0], 2), round(sy + lp[1], 2))

    for net_name, (px, py) in power_net_positions.items():
        flag_uid = uid(f"{project_name}_pwrflag_{net_name}_{flag_count}")
        flag_count += 1
        fx = round(px - 5.08, 2)
        fy = py
        s.open("symbol", f'(lib_id "power:PWR_FLAG") (at {_fmt(fx)} {_fmt(fy)} 0) (unit 1)')
        s.emit("(in_bom no) (on_board no)")
        s.emit(f'(uuid "{flag_uid}")')
        s.kicad_property("Reference", f"#FLG0{flag_count}", fx, round(fy - 2.54, 2), hide=True)
        s.kicad_property("Value", "PWR_FLAG", fx, round(fy + 2.54, 2), hide=True)
        pin_uid = uid(f"{project_name}_pwrflag_pin_{net_name}_{flag_count}")
        s.pin_instance("1", pin_uid)
        s.close()
        wire_uid = uid(f"{project_name}_pwrflag_wire_{net_name}_{flag_count}")
        s.wire(fx, fy, px, py, wire_uid)

    if flag_count:
        logger.info("Placed %d PWR_FLAG symbols on power nets", flag_count)


# ── Functional block frames ──────────────────────────────────────────────────

def _emit_block_frames(s: SExpr, parts: list[dict], project_name: str) -> None:
    """Draw dashed rectangles around functional groups.

    Rule: Each group of components gets a labeled frame to improve
    visual separation and readability.
    """
    GROUP_LABELS = {
        "connector_in": "Input",
        "connector_out": "Output",
        "connector_pwr": "Power Supply",
        "power_reg": "Voltage Regulator",
        "main_ic": "Main IC",
        "transistor": "Active Stage",
        "passive": "Passives",
        "power_passive": "Power Network",
        "bypass_cap": None,  # skip — visually part of their IC
        "diode": None,       # skip — usually part of another group
        "pullup": None,
        "indicator": None,
        "other": None,
    }
    FRAME_PAD = 8.0  # mm padding around group

    groups: dict[str, list[dict]] = {}
    for part in parts:
        g = part.get("_group", "other")
        if g not in groups:
            groups[g] = []
        groups[g].append(part)

    frame_count = 0
    for group_name, group_parts in groups.items():
        label = GROUP_LABELS.get(group_name)
        if not label or len(group_parts) < 3:
            continue

        placed = [p for p in group_parts if "_place_x" in p]
        if len(placed) < 3:
            continue

        # Compute bounding box of group
        min_x = min(p["_place_x"] - _get_symbol_bbox(p)[0] / 2 for p in placed) - FRAME_PAD
        max_x = max(p["_place_x"] + _get_symbol_bbox(p)[0] / 2 for p in placed) + FRAME_PAD
        min_y = min(p["_place_y"] - _get_symbol_bbox(p)[1] / 2 for p in placed) - FRAME_PAD
        max_y = max(p["_place_y"] + _get_symbol_bbox(p)[1] / 2 for p in placed) + FRAME_PAD

        # Draw dashed rectangle
        corners = [
            (round(min_x, 2), round(min_y, 2)),
            (round(max_x, 2), round(min_y, 2)),
            (round(max_x, 2), round(max_y, 2)),
            (round(min_x, 2), round(max_y, 2)),
            (round(min_x, 2), round(min_y, 2)),  # close rectangle
        ]
        frame_uid = uid(f"{project_name}_frame_{group_name}_{frame_count}")
        s.polyline(corners, frame_uid)

        # Label at top-left
        text_uid = uid(f"{project_name}_frametext_{group_name}_{frame_count}")
        s.text_note(label, round(min_x + 2, 2), round(min_y + 2, 2), text_uid)

        frame_count += 1

    if frame_count:
        logger.info("Drew %d functional block frames", frame_count)


# ── No-connect flags ─────────────────────────────────────────────────────────

def _emit_no_connects(
    s: SExpr, parts: list[dict], nets: list[dict], project_name: str,
    labeled_positions: set[tuple[float, float]] | None = None,
) -> None:
    """Place no-connect flags on ALL unused pins of ALL components.

    General rule: Every pin that has no net must have a no-connect flag.
    This applies to all components (not just MCUs), preventing ERC errors.
    Only pins belonging to the placed unit are considered.
    """
    # Build set of connected pin names per ref: "REF:pin_name"
    connected_pin_names: set[str] = set()
    for net in nets:
        for conn in net.get("connections", []):
            connected_pin_names.add(conn)

    # Build set of user-defined pin names per ref
    ref_pin_names: dict[str, set[str]] = {}
    for part in parts:
        ref = part["ref"]
        ref_pin_names[ref] = {p.get("name", "") for p in part.get("pins", [])}

    _labeled = labeled_positions or set()
    nc_count = 0

    for part in parts:
        ref = part["ref"]
        lib_id = resolve_lib_id(part)
        sym_x = round(part.get("_place_x", 50.8), 2)
        sym_y = round(part.get("_place_y", 38.1), 2)

        # Get pin positions for the placed unit
        pin_positions = _extract_pin_positions(lib_id, part)

        # Determine which pins are connected
        connected_user_nums = set()
        for pin in part.get("pins", []):
            conn_key = f"{ref}:{pin['name']}"
            if conn_key in connected_pin_names:
                connected_user_nums.add(str(pin["num"]))

        # Map user pin nums to real pin nums
        real_sym = get_real_symbol(lib_id)
        real_pins = _pins_from_real_symbol(real_sym) if real_sym else {}
        user_to_real = _map_user_to_real_pins(part, real_pins, real_sym)

        # Get set of real pin nums that are connected
        connected_real_nums = set()
        for u_num in connected_user_nums:
            connected_real_nums.add(user_to_real.get(u_num, u_num))

        # Detect multi-unit — only mark pins from the placed unit
        units = _detect_units(lib_id) if real_sym else {}
        if units:
            main_unit = _find_user_unit(part, units)
            placed_unit_pins = {p["num"] for p in units.get(main_unit, [])}
        else:
            placed_unit_pins = set(pin_positions.keys())

        nc_positions: set[tuple[float, float]] = set()

        for pin_num_key, (local_x, local_y) in pin_positions.items():
            # Only consider pins from the placed unit
            real_num = user_to_real.get(pin_num_key, pin_num_key)
            if placed_unit_pins and real_num not in placed_unit_pins:
                continue

            if real_num in connected_real_nums:
                continue

            abs_x = round(sym_x + local_x, 2)
            abs_y = round(sym_y + local_y, 2)
            pos = (abs_x, abs_y)

            if pos in _labeled:
                continue
            if pos in nc_positions:
                continue

            nc_positions.add(pos)
            nc_uid = uid(f"{project_name}_nc_{ref}_{real_num}_{nc_count}")
            nc_count += 1
            s.no_connect(abs_x, abs_y, nc_uid)

        # Also place no-connects on extra unit pins (e.g. LM358 Unit B)
        for eu in part.get("_extra_units", []):
            eu_x = eu["x"]
            eu_y = eu["y"]
            for ep in eu.get("pins", []):
                real_num = str(ep["num"])
                if real_num in connected_real_nums:
                    continue
                abs_x = round(eu_x + ep["x"], 2)
                abs_y = round(eu_y + ep["y"], 2)
                pos = (abs_x, abs_y)
                if pos in _labeled or pos in nc_positions:
                    continue
                nc_positions.add(pos)
                nc_uid = uid(f"{project_name}_nc_{ref}_eu_{real_num}_{nc_count}")
                nc_count += 1
                s.no_connect(abs_x, abs_y, nc_uid)

    if nc_count > 0:
        logger.info("Placed %d no-connect flags on unused pins", nc_count)


# ── Sheet/symbol instances ───────────────────────────────────────────────────

def _emit_instances(s: SExpr, parts: list[dict], project_name: str) -> None:
    uid(f"{project_name}_sch")
    s.open("sheet_instances")
    s.emit('(path "/" (page "1"))')
    s.close()
    s.blank()
    s.open("symbol_instances")
    for part in parts:
        ref = part["ref"]
        lib_id = resolve_lib_id(part)
        value = part.get("value", part["name"])
        footprint = part.get("footprint", "")

        # Detect multi-unit to emit correct unit number
        units = _detect_units(lib_id)
        main_unit = _find_user_unit(part, units) if units else 1

        sym_uuid = uid(f"{project_name}_sym_{ref}")
        s.emit(
            f'(path "/{sym_uuid}" (reference "{ref}") (unit {main_unit})'
            f' (value "{value}") (footprint "{footprint}"))'
        )

        # Emit additional unit instances
        for extra in part.get("_extra_units", []):
            unit_num = extra["unit"]
            unit_uid = uid(f"{project_name}_sym_{ref}_unit{unit_num}")
            s.emit(
                f'(path "/{unit_uid}" (reference "{ref}") (unit {unit_num})'
                f' (value "{value}") (footprint "{footprint}"))'
            )
    s.close()
