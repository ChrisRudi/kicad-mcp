# SPDX-License-Identifier: GPL-3.0-or-later
"""
Incremental editor for ``.kicad_sch`` files.

Mirrors the philosophy of the PCB text-patcher (``tools/pcb_patch_tools.py``):
mutations operate on the raw S-expression text using depth-balanced block
extraction, so original formatting is preserved verbatim wherever the patch
does not touch it. The parser (``utils.sexpr_parser``) is used for read paths
only — pin lookup, group enumeration, bbox queries.

Public surface:
  * ``SchematicDoc`` — open/save/mutate handle around a single ``.kicad_sch``
  * Helpers around symbol-instance attributes, lib-symbol pins, group tagging
  * Deterministic UUID generation (shared namespace with ``generators/sexpr``)

The module deliberately avoids re-serialising the parsed tree — every mutation
returns a new ``text`` blob and re-parses lazily on next read, so we never
introduce whitespace/quoting drift.
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
from __future__ import annotations

import re
from typing import Any, Optional
from uuid import uuid5

from ..utils.sexpr_parser import find_node, find_nodes, parse_sexpr
from ..utils.sch_geometry import pin_world_xy
from .sexpr import KICAD_MCP_NS
from .symbol_cache import get_project_symbol, get_real_symbol


GROUP_PROP_NAME = "kicad-mcp.group"


# ---------------------------------------------------------------------------
# Block-extraction helpers (text-level, depth-balanced)
# ---------------------------------------------------------------------------


def find_block_end(text: str, start: int) -> int:
    """Return the index *just past* the matching ``)`` for the ``(`` at
    ``start``. Falls back to ``len(text)`` if unbalanced.
    """
    depth = 0
    in_str = False
    esc = False
    i = start
    while i < len(text):
        ch = text[i]
        if esc:
            esc = False
        elif ch == "\\" and in_str:
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return len(text)


def iter_top_blocks(text: str, head: str) -> list[tuple[int, int]]:
    """All ``(head …)`` block offsets at any nesting level. Restrict to
    top-level by checking depth before the match — see
    ``iter_top_level_blocks`` for that variant.
    """
    needle = "(" + head
    out: list[tuple[int, int]] = []
    i = 0
    while True:
        idx = text.find(needle, i)
        if idx < 0:
            return out
        # ensure boundary: needle must be followed by space, newline or '('
        nxt = idx + len(needle)
        if nxt < len(text) and text[nxt] not in (" ", "\t", "\n", "\r", "(", ")"):
            i = idx + 1
            continue
        end = find_block_end(text, idx)
        out.append((idx, end))
        i = end


def iter_top_level_blocks(text: str, head: str) -> list[tuple[int, int]]:
    """``(head …)`` blocks whose opening ``(`` is at depth 0 — i.e. direct
    children of the top-level ``(kicad_sch …)`` block.
    """
    if not text.lstrip().startswith("(kicad_sch"):
        return iter_top_blocks(text, head)
    # First skip into the kicad_sch body.
    open_idx = text.find("(kicad_sch")
    body_start = open_idx + len("(kicad_sch")
    out: list[tuple[int, int]] = []
    i = body_start
    depth = 0
    in_str = False
    esc = False
    needle = "(" + head
    while i < len(text):
        ch = text[i]
        if esc:
            esc = False
            i += 1
            continue
        if ch == "\\" and in_str:
            esc = True
            i += 1
            continue
        if ch == '"':
            in_str = not in_str
            i += 1
            continue
        if in_str:
            i += 1
            continue
        if ch == "(":
            if depth == 0 and text[i : i + len(needle)] == needle:
                # boundary check
                nxt = i + len(needle)
                if nxt < len(text) and text[nxt] in (" ", "\t", "\n", "\r", "(", ")"):
                    end = find_block_end(text, i)
                    out.append((i, end))
                    i = end
                    continue
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                break
        i += 1
    return out


# ---------------------------------------------------------------------------
# UUID + small helpers
# ---------------------------------------------------------------------------


def stable_uuid(seed: str) -> str:
    """Deterministic UUID-5 in the project namespace."""
    return str(uuid5(KICAD_MCP_NS, seed))


# ---------------------------------------------------------------------------
# Schematic document handle
# ---------------------------------------------------------------------------


class SchematicDoc:
    """Read/write handle around a ``.kicad_sch`` file."""

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text
        self._tree: Optional[list] = None
        self._dirty = False

    # ------------------------------------------------------------------ I/O

    @classmethod
    def load(cls, path: str) -> "SchematicDoc":
        with open(path, encoding="utf-8") as fh:
            return cls(path, fh.read())

    def save(self, path: Optional[str] = None) -> str:
        target = path or self.path
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(self.text)
        self._dirty = False
        return target

    # ---------------------------------------------------------------- Parse

    @property
    def tree(self) -> list:
        if self._tree is None or self._dirty:
            self._tree = parse_sexpr(self.text)
            self._dirty = False
        return self._tree

    def _invalidate(self) -> None:
        self._tree = None
        self._dirty = True

    # ----------------------------------------------------- Top-level enums

    def root(self) -> list:
        t = self.tree
        return t if t else []

    def project_uuid(self) -> str:
        u = find_node(self.root(), "uuid")
        if u and len(u) > 1 and isinstance(u[1], str):
            return u[1]
        return ""

    def project_name(self) -> str:
        """Return the project stem (filename without extension) — used
        to populate the ``(instances (project "<name>" …))`` block on
        every placed symbol so KiCad-10 connection-detection works.
        Falls back to the schematic stem if no ``.kicad_pro`` is next
        to the file.
        """
        import os as _os
        if not self.path:
            return ""
        d = _os.path.dirname(self.path)
        if d and _os.path.isdir(d):
            for name in _os.listdir(d):
                if name.endswith(".kicad_pro"):
                    return _os.path.splitext(name)[0]
        return _os.path.splitext(_os.path.basename(self.path))[0]

    def root_sheet_path(self) -> str:
        """Return the root-sheet path UUID used in
        ``(instances (path "/<uuid>" …))``. KiCad-10 uses the project
        UUID itself as the root path; this is the same UUID the
        schematic root carries."""
        return self.project_uuid()

    def iter_symbol_offsets(self) -> list[tuple[int, int]]:
        """Top-level ``(symbol (lib_id …) …)`` instance blocks."""
        out: list[tuple[int, int]] = []
        for start, end in iter_top_level_blocks(self.text, "symbol"):
            head = self.text[start : start + 200]
            if "(lib_id" in head:
                out.append((start, end))
        return out

    def iter_lib_symbol_blocks(self) -> list[tuple[int, int]]:
        """Sub-blocks ``(symbol "Lib:Name" …)`` inside the top-level
        ``(lib_symbols …)`` container.
        """
        ls_blocks = iter_top_level_blocks(self.text, "lib_symbols")
        if not ls_blocks:
            return []
        ls_start, ls_end = ls_blocks[0]
        out: list[tuple[int, int]] = []
        i = ls_start + len("(lib_symbols")
        # walk symbol blocks at depth 1 inside lib_symbols
        depth = 0
        in_str = False
        esc = False
        needle = "(symbol"
        while i < ls_end:
            ch = self.text[i]
            if esc:
                esc = False
                i += 1
                continue
            if ch == "\\" and in_str:
                esc = True
                i += 1
                continue
            if ch == '"':
                in_str = not in_str
                i += 1
                continue
            if in_str:
                i += 1
                continue
            if ch == "(":
                if depth == 0 and self.text[i : i + len(needle)] == needle:
                    nxt = i + len(needle)
                    if nxt < len(self.text) and self.text[nxt] in (
                        " ",
                        "\t",
                        "\n",
                        "\r",
                    ):
                        end = find_block_end(self.text, i)
                        out.append((i, min(end, ls_end)))
                        i = end
                        continue
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        return out

    # ------------------------------------------------- Lookup convenience

    def find_symbol_by_ref(self, ref: str) -> Optional[tuple[int, int, list]]:
        for start, end in self.iter_symbol_offsets():
            block = self.text[start:end]
            try:
                node = parse_sexpr(block)
            except Exception:
                continue
            if get_symbol_ref(node) == ref:
                return (start, end, node)
        return None

    def find_lib_symbol(self, lib_id: str) -> Optional[tuple[int, int, list]]:
        for start, end in self.iter_lib_symbol_blocks():
            block = self.text[start:end]
            try:
                node = parse_sexpr(block)
            except Exception:
                continue
            if isinstance(node, list) and len(node) > 1 and node[0] == "symbol":
                if str(node[1]) == lib_id:
                    return (start, end, node)
        return None

    def iter_symbol_world_bboxes(
        self,
    ) -> list[tuple[str, float, float, float, float]]:
        """Return ``(ref, xmin, ymin, xmax, ymax)`` for every placed symbol,
        derived from world pin positions of its lib_symbol definition.

        Used for collision detection when placing labels: a label landing
        inside a (foreign) symbol's pin BBox is hard to read in the GUI.
        """
        # Local import to avoid circular dep with utils.sch_geometry.
        from kicad_mcp.utils.sch_geometry import pin_world_xy as _pwxy
        from kicad_mcp.utils.sexpr_parser import parse_sexpr as _parse

        out: list[tuple[str, float, float, float, float]] = []
        for start, end in self.iter_symbol_offsets():
            try:
                node = _parse(self.text[start:end])
            except Exception:
                continue
            ref = get_symbol_ref(node)
            if not ref:
                continue
            attrs = get_symbol_attrs(node)
            lib_id = attrs.get("lib_id")
            sx = float(attrs.get("x", 0.0))
            sy = float(attrs.get("y", 0.0))
            if not lib_id:
                out.append((ref, sx - 1.27, sy - 1.27, sx + 1.27, sy + 1.27))
                continue
            lib = self.find_lib_symbol(lib_id)
            pins = get_lib_symbol_pins(lib[2]) if lib else []
            if not pins:
                out.append((ref, sx - 1.27, sy - 1.27, sx + 1.27, sy + 1.27))
                continue
            xs: list[float] = []
            ys: list[float] = []
            for p in pins:
                wx, wy = _pwxy(
                    sx, sy,
                    int(attrs.get("rot", 0)),
                    attrs.get("mirror"),
                    p["x"], p["y"],
                )
                xs.append(wx); ys.append(wy)
            out.append((ref, min(xs), min(ys), max(xs), max(ys)))
        return out

    def has_label_at(
        self,
        name: str,
        x: float,
        y: float,
        kind: str = "global",
        tol_mm: float = 0.05,
    ) -> bool:
        """True if a label of ``kind`` named ``name`` already sits at (x, y).

        Used by ``connect_pins(mode='label')`` and ``add_schematic_label`` to
        avoid stacking duplicate labels on the same point — KiCad would render
        them as a single label visually but they'd accumulate on every re-run.
        """
        tag = {
            "local": "label",
            "global": "global_label",
            "hierarchical": "hierarchical_label",
        }.get(kind, "label")
        for start, end in iter_top_level_blocks(self.text, tag):
            block = self.text[start:end]
            # Quick name check: "{name}" must appear in the first line.
            head = block.split("\n", 1)[0]
            if f'"{name}"' not in head:
                continue
            m = re.search(r"\(at\s+([\d.\-]+)\s+([\d.\-]+)", block)
            if not m:
                continue
            try:
                bx = float(m.group(1)); by = float(m.group(2))
            except ValueError:
                continue
            if abs(bx - x) <= tol_mm and abs(by - y) <= tol_mm:
                return True
        return False

    def any_label_at(
        self,
        x: float,
        y: float,
        tol_mm: float = 0.05,
    ) -> Optional[tuple[str, str]]:
        """Return ``(kind, name)`` of *any* label sitting at (x, y), else None.

        Considers ``label``, ``global_label``, ``hierarchical_label`` blocks —
        i.e. labels of any name and any kind. Used to keep newly emitted
        labels from landing on existing ones (cross-name collision, beyond
        the same-name dedupe in ``has_label_at``).
        """
        for tag in ("label", "global_label", "hierarchical_label"):
            for start, end in iter_top_level_blocks(self.text, tag):
                block = self.text[start:end]
                m_name = re.match(rf'\({tag}\s+"([^"]+)"', block)
                if not m_name:
                    continue
                m_at = re.search(r"\(at\s+([\d.\-]+)\s+([\d.\-]+)", block)
                if not m_at:
                    continue
                try:
                    bx = float(m_at.group(1)); by = float(m_at.group(2))
                except ValueError:
                    continue
                if abs(bx - x) <= tol_mm and abs(by - y) <= tol_mm:
                    return (tag, m_name.group(1))
        return None

    def all_refs(self) -> list[str]:
        out: list[str] = []
        for start, end in self.iter_symbol_offsets():
            try:
                node = parse_sexpr(self.text[start:end])
            except Exception:
                continue
            r = get_symbol_ref(node)
            if r:
                out.append(r)
        return out

    # ----------------------------------------------------- Pin world coords

    def pin_world_xy(self, ref: str, pin_number: str) -> Optional[tuple[float, float]]:
        sym = self.find_symbol_by_ref(ref)
        if not sym:
            return None
        _, _, node = sym
        attrs = get_symbol_attrs(node)
        lib_id = attrs.get("lib_id")
        if not lib_id:
            return None
        lib = self.find_lib_symbol(lib_id)
        if not lib:
            return None
        pins = get_lib_symbol_pins(lib[2])
        for p in pins:
            if str(p["number"]) == str(pin_number):
                return pin_world_xy(
                    attrs["x"],
                    attrs["y"],
                    int(attrs.get("rot", 0)),
                    attrs.get("mirror"),
                    p["x"],
                    p["y"],
                )
        return None

    # --------------------------------------------------------- Mutations

    def insert_before_close(self, snippet: str) -> None:
        """Insert ``snippet`` immediately before the final ``)`` that closes
        ``(kicad_sch …)``. Handles trailing newline cleanly.
        """
        # Find the matching close of the top-level (kicad_sch
        if not self.text.lstrip().startswith("(kicad_sch"):
            self.text = self.text.rstrip() + "\n" + snippet + "\n"
            self._invalidate()
            return
        open_idx = self.text.find("(kicad_sch")
        end = find_block_end(self.text, open_idx)
        # end is just past ')'. Insert right before that closing ')'.
        close_pos = end - 1
        # Walk back over whitespace so we land on the ')' itself.
        while close_pos > 0 and self.text[close_pos] != ")":
            close_pos -= 1
        head = self.text[:close_pos]
        tail = self.text[close_pos:]
        if not head.endswith("\n"):
            head = head + "\n"
        # Ensure snippet ends with newline before the closing ')'.
        snippet = snippet.rstrip() + "\n"
        self.text = head + snippet + tail
        self._invalidate()

    def replace_block(self, start: int, end: int, new_text: str) -> None:
        self.text = self.text[:start] + new_text + self.text[end:]
        self._invalidate()

    def delete_block(self, start: int, end: int) -> None:
        # Also strip a trailing newline so deletions don't leave blanks.
        cut_end = end
        if cut_end < len(self.text) and self.text[cut_end] == "\n":
            cut_end += 1
        self.text = self.text[:start] + self.text[cut_end:]
        self._invalidate()

    def insert_into_lib_symbols(self, snippet: str) -> bool:
        """Insert ``snippet`` just before the closing ``)`` of the
        ``(lib_symbols …)`` block. Returns ``False`` if no such block exists.
        """
        ls_blocks = iter_top_level_blocks(self.text, "lib_symbols")
        if not ls_blocks:
            return False
        ls_start, ls_end = ls_blocks[0]
        # Walk back from ls_end-1 (the ')') to find last newline before it.
        close_pos = ls_end - 1
        while close_pos > ls_start and self.text[close_pos] != ")":
            close_pos -= 1
        head = self.text[:close_pos]
        tail = self.text[close_pos:]
        if not head.endswith("\n"):
            head = head + "\n"
        snippet = snippet.rstrip() + "\n"
        self.text = head + snippet + tail
        self._invalidate()
        return True

    def drop_lib_symbol(self, lib_id: str) -> bool:
        """Remove the ``(symbol "lib_id" …)`` block from the ``lib_symbols``
        container. Returns ``True`` if a block was removed, ``False`` if the
        lib_id was not cached.

        Use this before re-embedding a fresh definition (e.g. in
        ``bulk_swap_symbol``): renaming the cached block in place keeps the
        *old* geometry/pins under the new name, which is wrong when the two
        symbols differ.
        """
        found = self.find_lib_symbol(lib_id)
        if not found:
            return False
        start, end, _ = found
        self.delete_block(start, end)
        return True

    def ensure_lib_symbol(self, lib_id: str, project_dir: Optional[str] = None) -> bool:
        """Make sure ``lib_id`` is present in the ``lib_symbols`` container.
        Loads the symbol from the bundled KiCad library on demand. Returns
        ``True`` if the lib_symbol now exists, ``False`` if it could not be
        located.

        Resolution order: stock + global ``sym-lib-table`` (via
        :func:`get_real_symbol`); on miss, if ``project_dir`` is given, the
        project-local ``sym-lib-table`` (``${KIPRJMOD}`` libs) is consulted
        via :func:`get_project_symbol`.
        """
        if self.find_lib_symbol(lib_id):
            return True
        sym_text = get_real_symbol(lib_id)
        if not sym_text and project_dir:
            sym_text = get_project_symbol(lib_id, project_dir)
        if not sym_text:
            return False
        # The cache returns the symbol with leading "(symbol \"Lib:Name\" …)";
        # indent it one level (4 spaces) for nesting under lib_symbols.
        snippet = _indent_block(sym_text.strip(), 1)
        if not iter_top_level_blocks(self.text, "lib_symbols"):
            # No lib_symbols container yet — create one before the first
            # symbol-instance line, or just before the closing paren of
            # kicad_sch.
            container = "  (lib_symbols\n" + snippet + "\n  )"
            self.insert_before_close(container)
            return True
        return self.insert_into_lib_symbols(snippet)


# ---------------------------------------------------------------------------
# Symbol-instance attribute readers (work on parsed list nodes)
# ---------------------------------------------------------------------------


def get_symbol_lib_id(node: list) -> Optional[str]:
    n = find_node(node, "lib_id")
    if n and len(n) > 1:
        return str(n[1])
    return None


def get_symbol_at(node: list) -> tuple[float, float, int]:
    """Return ``(x, y, rotation_deg)`` from a symbol's ``(at …)`` attribute."""
    n = find_node(node, "at")
    if not n or len(n) < 3:
        return (0.0, 0.0, 0)
    try:
        x = float(n[1])
        y = float(n[2])
        rot = int(float(n[3])) if len(n) > 3 else 0
        return (x, y, rot)
    except (TypeError, ValueError):
        return (0.0, 0.0, 0)


def get_symbol_mirror(node: list) -> Optional[str]:
    n = find_node(node, "mirror")
    if n and len(n) > 1:
        return str(n[1])
    return None


def get_symbol_uuid(node: list) -> Optional[str]:
    n = find_node(node, "uuid")
    if n and len(n) > 1:
        return str(n[1])
    return None


def get_symbol_property(node: list, prop_name: str) -> Optional[str]:
    """Return the value of a ``(property "prop_name" "value" …)`` child."""
    for p in find_nodes(node, "property"):
        if len(p) >= 3 and str(p[1]) == prop_name:
            return str(p[2])
    return None


def get_symbol_ref(node: list) -> Optional[str]:
    return get_symbol_property(node, "Reference")


def get_symbol_value(node: list) -> Optional[str]:
    return get_symbol_property(node, "Value")


def get_symbol_group(node: list) -> Optional[str]:
    return get_symbol_property(node, GROUP_PROP_NAME)


def get_symbol_attrs(node: list) -> dict[str, Any]:
    x, y, rot = get_symbol_at(node)
    return {
        "lib_id": get_symbol_lib_id(node),
        "x": x,
        "y": y,
        "rot": rot,
        "mirror": get_symbol_mirror(node),
        "uuid": get_symbol_uuid(node),
        "ref": get_symbol_ref(node),
        "value": get_symbol_value(node),
        "group": get_symbol_group(node),
    }


# ---------------------------------------------------------------------------
# Lib-symbol pin enumeration
# ---------------------------------------------------------------------------


def get_lib_symbol_pins(
    lib_node: list, unit: Optional[int] = None
) -> list[dict[str, Any]]:
    """Return list of pins for a lib_symbol entry. Walks every nested
    ``(symbol "<Sub>" …)`` (KiCad splits each symbol into ``_<unit>_<style>``
    sub-pieces, e.g. ``_0_1`` common / ``_1_1`` unit 1 / ``_2_1`` unit 2).

    ``unit`` filters to a single unit's pins (plus the shared unit-0 pins) —
    REQUIRED for multi-unit parts (op-amps, the 74xx gates): without it the
    union of *all* units' pins is returned, so placing unit 2 would emit pin
    UUIDs for unit 1's pins and corrupt connectivity. ``unit=None`` (default)
    keeps the legacy "all pins" behaviour for single-unit parts / callers that
    only need a bbox.
    """
    pins: list[dict[str, Any]] = []

    def _sub_unit(name: Any) -> Optional[int]:
        m = re.search(r"_(\d+)_\d+$", str(name))
        return int(m.group(1)) if m else None

    def _walk(node: Any, in_unit: Optional[int] = None) -> None:
        if not isinstance(node, list):
            return
        if node and node[0] == "symbol" and len(node) > 1:
            su = _sub_unit(node[1])
            if su is not None:
                in_unit = su
        if node and node[0] == "pin" and len(node) >= 2:
            if (
                unit is not None
                and in_unit is not None
                and in_unit not in (0, unit)
            ):
                return  # pin belongs to a different unit
            pin_type = str(node[1]) if isinstance(node[1], str) else "unspecified"
            x, y, angle = 0.0, 0.0, 0
            name, number = "", ""
            for child in node[2:]:
                if not isinstance(child, list) or not child:
                    continue
                if child[0] == "at" and len(child) >= 3:
                    try:
                        x = float(child[1])
                        y = float(child[2])
                        angle = int(float(child[3])) if len(child) > 3 else 0
                    except (TypeError, ValueError):
                        # defektes (at ...)-Feld — Default-Koordinaten behalten
                        pass
                elif child[0] == "name" and len(child) > 1:
                    name = str(child[1])
                elif child[0] == "number" and len(child) > 1:
                    number = str(child[1])
            pins.append(
                {
                    "type": pin_type,
                    "name": name,
                    "number": number,
                    "x": x,
                    "y": y,
                    "angle": angle,
                }
            )
            return
        for child in node:
            if isinstance(child, list):
                _walk(child, in_unit)

    _walk(lib_node)
    return pins


# ---------------------------------------------------------------------------
# Snippet builders (render single S-expression elements as text)
# ---------------------------------------------------------------------------


def render_property(
    name: str,
    value: str,
    x: float = 0.0,
    y: float = 0.0,
    angle: int = 0,
    hide: bool = False,
    indent: int = 2,
) -> str:
    pad = " " * indent
    hide_clause = "\n" + pad + "  (hide yes)" if hide else ""
    return (
        f'{pad}(property "{name}" "{value}"\n'
        f"{pad}  (at {_fmt(x)} {_fmt(y)} {int(angle)})\n"
        f"{pad}  (effects (font (size 1.27 1.27))){hide_clause}\n"
        f"{pad})"
    )


def render_symbol_instance(
    *,
    ref: str,
    lib_id: str,
    value: str,
    footprint: str,
    x: float,
    y: float,
    rot: int = 0,
    mirror: Optional[str] = None,
    uuid: Optional[str] = None,
    group_id: Optional[str] = None,
    project_uuid: str = "",
    extra_props: Optional[list[tuple[str, str]]] = None,
    indent: int = 2,
    pin_numbers: Optional[list[str]] = None,
    project_name: Optional[str] = None,
    sheet_path_uuid: str = "",
    hide_reference: bool = False,
    snap: bool = True,
    unit: int = 1,
) -> str:
    """Render a top-level ``(symbol (lib_id …) …)`` block as text. The output
    is a single newline-terminated chunk ready for insertion via
    ``SchematicDoc.insert_before_close``.

    KiCad-10 connection-detection requires per-pin UUIDs and an
    ``(instances …)`` block on every placed symbol — without them, the
    ERC engine reports every pin as ``pin_not_connected`` even when the
    wire endpoints land on the pin hot-spots. Pass ``pin_numbers`` (a
    list extracted via :func:`get_lib_symbol_pins`) and ``project_name``
    (the schematic's project stem, e.g. ``"my_board"``) so this block
    is emitted automatically.

    Args:
        pin_numbers: Pin numbers from the lib_symbol (``["1", "2",
            "GND", …]``); each gets its own deterministic UUID derived
            from ``project_uuid`` + ``ref`` + pin number.
        project_name: The KiCad project name for the ``(instances)``
            sub-block. If omitted no instances block is emitted (older
            behaviour, kept for backwards-compat tests).
        sheet_path_uuid: UUID of the root sheet in KiCad-10 hierarchy
            format. Defaults to the standard root path. Pass the value
            from :func:`SchematicDoc.root_sheet_path` for multi-sheet
            schematics.
    """
    if uuid is None:
        uuid = stable_uuid(f"{project_uuid}|{ref}|sym")
    from kicad_mcp.utils.sch_geometry import snap_to_grid as _snap
    if snap:
        x, y = _snap(float(x), float(y))
    else:
        # Keep an exact off-grid endpoint (e.g. a fine-pitch IC pin) instead
        # of pulling it to the 1.27 mm grid; clamp only to sch resolution.
        x, y = round(float(x), 4), round(float(y), 4)
    pad = " " * indent
    mirror_line = f"\n{pad}  (mirror {mirror})" if mirror else ""
    rot_norm = ((int(round(rot)) % 360) + 360) % 360
    extra_names = {n for n, _ in (extra_props or [])}
    lines = [
        f"{pad}(symbol (lib_id \"{lib_id}\") (at {_fmt(x)} {_fmt(y)} {rot_norm}) (unit {int(unit)})"
        f"{mirror_line}",
        f'{pad}  (in_bom yes) (on_board yes) (dnp no)',
        f'{pad}  (uuid "{uuid}")',
        render_property(
            "Reference", ref, x, y - 5.08, 0,
            hide=hide_reference, indent=indent + 2,
        ),
        render_property("Value", value, x, y + 5.08, 0, indent=indent + 2),
        render_property(
            "Footprint", footprint, x, y + 7.62, 0, hide=True, indent=indent + 2
        ),
    ]
    # Always emit Datasheet/Description as hidden instance properties so that
    # KiCad's GUI-render path does not fall back to the lib_symbol defaults
    # (which carry the visible "Power symbol creates a global label …" boiler-
    # plate for power: symbols and surface as cluttering text on the sheet).
    # The empty value lets KiCad's "Update Symbols from Library" repopulate
    # them on demand without overwriting user edits.
    if "Datasheet" not in extra_names:
        lines.append(render_property(
            "Datasheet", "", x, y + 10.16, 0, hide=True, indent=indent + 2,
        ))
    if "Description" not in extra_names:
        lines.append(render_property(
            "Description", "", x, y + 12.7, 0, hide=True, indent=indent + 2,
        ))
    if extra_props:
        for n, v in extra_props:
            lines.append(render_property(n, v, x, y + 15.24, 0, hide=True, indent=indent + 2))
    if group_id:
        lines.append(
            render_property(
                GROUP_PROP_NAME, group_id, x, y, 0, hide=True, indent=indent + 2
            )
        )
    if pin_numbers:
        for n in pin_numbers:
            pin_uuid = stable_uuid(f"{project_uuid}|{ref}|pin|{n}")
            lines.append(f'{pad}  (pin "{n}" (uuid "{pin_uuid}"))')
    if project_name:
        sheet_path = sheet_path_uuid or "00000000-0000-0000-0000-000000000000"
        lines.append(f'{pad}  (instances')
        lines.append(f'{pad}    (project "{project_name}"')
        lines.append(f'{pad}      (path "/{sheet_path}"')
        lines.append(f'{pad}        (reference "{ref}")')
        lines.append(f'{pad}        (unit {int(unit)})')
        lines.append(f'{pad}      )')
        lines.append(f'{pad}    )')
        lines.append(f'{pad}  )')
    lines.append(f"{pad})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Power-net helpers
# ---------------------------------------------------------------------------


# Map of net-name → (lib_id, default_value_text). When a caller wants to wire
# up a power/ground net, the canonical KiCad way is to drop a ``power:`` lib
# symbol — these symbols carry the power-input pin convention that ERC's
# ``power_pin_not_driven`` check relies on. Plain ``global_label`` with the
# same net name routes electrically the same but breaks the ERC contract,
# the visual unique-shape convention, and the PCB net-class assignment.
#
# Project-suffixed rails (``+5V_SYS``, ``VBUS_SYS``, ``+3V3_MCU`` etc.)
# are recognised but **canonicalised to the bare KiCad rail name** as
# the ``value`` field. Reason: KiCad joins power nets by the displayed
# value of the power-symbol pin — every ``power:+5V`` instance with
# value ``"+5V"`` shares the same net implicitly. Keeping the suffix
# would create a separate ``+5V_SYS`` island that does not connect to
# any other ``+5V`` pin elsewhere in the schematic. Drop the suffix
# during conversion so all rail consumers end up on one net.
_POWER_LIB_IDS = {
    "GND": ("power:GND", "GND"),
    "GNDA": ("power:GNDA", "GNDA"),
    "GNDD": ("power:GNDD", "GNDD"),
    "GNDPWR": ("power:GNDPWR", "GNDPWR"),
    "+3V3": ("power:+3V3", "+3V3"),
    "+3.3V": ("power:+3V3", "+3V3"),
    "+3V3_SYS": ("power:+3V3", "+3V3"),
    "+5V": ("power:+5V", "+5V"),
    "+5V_SYS": ("power:+5V", "+5V"),
    "+12V": ("power:+12V", "+12V"),
    "+12V_SYS": ("power:+12V", "+12V"),
    "-12V": ("power:-12V", "-12V"),
    "+15V": ("power:+15V", "+15V"),
    "-15V": ("power:-15V", "-15V"),
    "VBUS": ("power:VBUS", "VBUS"),
    "VBUS_SYS": ("power:VBUS", "VBUS"),
    "VCC": ("power:VCC", "VCC"),
    "VDD": ("power:VDD", "VDD"),
    "VEE": ("power:VEE", "VEE"),
    "VSS": ("power:VSS", "VSS"),
    "+BATT": ("power:+BATT", "+BATT"),
    "-BATT": ("power:-BATT", "-BATT"),
    "EARTH": ("power:Earth", "Earth"),
}


def power_lib_id_for(net_name: str) -> Optional[tuple[str, str]]:
    """Return ``(lib_id, value)`` for a power/ground net, or ``None`` if
    the name is a plain signal net.

    Schematic-patch tools should call this whenever they would otherwise
    emit a ``global_label`` for a power net, and prefer dropping a
    ``power:``-symbol instance via :func:`render_symbol_instance` instead
    — that is the only KiCad convention that satisfies ERC's
    ``power_pin_not_driven`` rule and renders with the standard power-net
    shape.

    Recognised names cover the common rails (``GND``, ``+3V3``, ``+5V``,
    ``+12V``, ``VBUS``, ``VCC``, ``VDD``, …). Project-specific suffixed
    rails (``+5V_SYS``, ``VBUS_SYS``) map to the closest canonical
    symbol but keep their original text as the displayed value.
    """
    if not net_name:
        return None
    key = net_name.strip()
    if key in _POWER_LIB_IDS:
        return _POWER_LIB_IDS[key]
    # Strip leading "/" from hierarchical net paths
    if key.startswith("/"):
        return _POWER_LIB_IDS.get(key.lstrip("/"))
    return None


# KiCad's stock ``power:`` lib-symbols already bake the conventional
# glyph orientation into the symbol drawing: ``power:GND`` has its bar
# below the pin, ``power:+5V`` / ``+3V3`` / ``VBUS`` / ``VCC`` have
# their arrow above. Rotation 0 is therefore the canonical orientation
# for **every** power family — both the GND drop (pin up, bar down)
# and the positive rail (pin down, arrow up) read correctly without
# user intervention. Rotating to 180 flips the arrow upside-down.
_GND_FAMILY_VALUES = {
    "GND", "GNDA", "GNDD", "GNDPWR",
    "Earth",
    "-12V", "-15V", "-BATT",
    "VEE", "VSS",
}


def default_power_rotation(value: str) -> int:  # pylint: disable=unused-argument
    """Conventional rotation for a power-symbol instance.

    Always returns 0 — KiCad's stock ``power:`` library bakes the glyph
    orientation (bar / arrow) into the lib-symbol itself, so the
    conventional placement for every power-net family at instance time
    is rotation 0. The ``value`` argument is accepted for forward
    compatibility (callers may want family-specific behaviour for
    custom power libs) but is currently ignored.
    """
    return 0


# Match the head of a ``(global_label "TEXT" (shape …) (at X Y A) …)``
# block. Captures the label text, x, y, angle. Used by
# ``iter_global_label_blocks`` and the ``convert_global_labels_to_power``
# tool. Tolerant of inserted whitespace; ``angle`` defaults to 0 if the
# at-form has no third argument (rare in practice — KiCad always emits
# the angle).
_GLOBAL_LABEL_HEAD_RE = re.compile(
    r'\(global_label\s+"([^"]+)".*?'
    r"\(at\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)(?:\s+(-?\d+(?:\.\d+)?))?",
    re.DOTALL,
)


def iter_global_label_blocks(
    text: str,
) -> list[tuple[int, int, str, float, float, int]]:
    """Enumerate top-level ``(global_label …)`` blocks with their parsed
    head fields.

    Returns a list of ``(start, end, name, x_mm, y_mm, angle_deg)`` tuples.
    ``start``/``end`` are the byte offsets of the block in the source
    text (suitable for :py:meth:`SchematicDoc.delete_block`); ``name`` is
    the label text; ``angle_deg`` is the integer angle from the ``(at …)``
    triple (defaults to 0 if missing).

    Skips local and hierarchical labels — call ``iter_top_level_blocks``
    directly for those.
    """
    out: list[tuple[int, int, str, float, float, int]] = []
    for start, end in iter_top_level_blocks(text, "global_label"):
        block = text[start:end]
        m = _GLOBAL_LABEL_HEAD_RE.search(block)
        if not m:
            continue
        try:
            x = float(m.group(2))
            y = float(m.group(3))
            angle = int(round(float(m.group(4)))) if m.group(4) else 0
        except (TypeError, ValueError):
            continue
        out.append((start, end, m.group(1), x, y, angle))
    return out


def render_wire(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    uuid: Optional[str] = None,
    group_id: Optional[str] = None,
    project_uuid: str = "",
    indent: int = 2,
    snap: bool = True,
) -> str:
    """Render a ``(wire …)`` block. ``group_id`` is accepted for API
    symmetry but currently has no effect — KiCad's S-expression format does
    not officially support comments, so trailing ``; group=…`` annotations
    would survive only by accident. Group membership is therefore tracked
    only on symbol instances (via the ``kicad-mcp.group`` hidden property).

    Endpoint coordinates are snapped to the 1.27 mm placement grid by default.
    Pass ``snap=False`` to keep an exact off-grid endpoint — a wire to a
    fine-pitch IC pin (pads off the 1.27 grid) must land *exactly* on the pin
    or the net breaks (same footgun as ``add_power_symbols``).
    """
    _ = group_id  # noqa: F841 — reserved for future use
    # Local import to avoid pulling sch_geometry into module init order
    from kicad_mcp.utils.sch_geometry import snap_to_grid as _snap
    if snap:
        x1, y1 = _snap(float(x1), float(y1))
        x2, y2 = _snap(float(x2), float(y2))
    else:
        x1, y1 = round(float(x1), 4), round(float(y1), 4)
        x2, y2 = round(float(x2), 4), round(float(y2), 4)
    if uuid is None:
        uuid = stable_uuid(f"{project_uuid}|wire|{x1},{y1}-{x2},{y2}")
    pad = " " * indent
    return (
        f"{pad}(wire (pts (xy {_fmt(x1)} {_fmt(y1)}) (xy {_fmt(x2)} {_fmt(y2)}))"
        f' (uuid "{uuid}"))'
    )


def render_no_connect(
    x: float,
    y: float,
    uuid: Optional[str] = None,
    project_uuid: str = "",
    indent: int = 2,
) -> str:
    """Render a ``(no_connect …)`` block — marks a pin as intentionally
    unconnected so ERC stops raising ``pin_not_connected`` for it.

    The flag is electrically active only when its ``(at x y)`` coincides
    with the pin's endpoint, so the coordinate is defensively snapped to
    the 1.27 mm placement grid (pins live on that grid).
    """
    from kicad_mcp.utils.sch_geometry import snap_to_grid as _snap
    x, y = _snap(float(x), float(y))
    if uuid is None:
        uuid = stable_uuid(f"{project_uuid}|no_connect|{x},{y}")
    pad = " " * indent
    return f'{pad}(no_connect (at {_fmt(x)} {_fmt(y)}) (uuid "{uuid}"))'


def render_label(
    name: str,
    x: float,
    y: float,
    *,
    kind: str = "local",
    angle: int = 0,
    justify: Optional[str] = None,
    uuid: Optional[str] = None,
    group_id: Optional[str] = None,
    project_uuid: str = "",
    indent: int = 2,
) -> str:
    """Render a label. ``group_id`` is reserved (see ``render_wire``).

    ``justify``: ``"left"`` or ``"right"`` — emits ``(justify <v>)`` inside
    ``(effects ...)``. Use ``"left"`` for labels with angle 0/90 (text reads
    rightward / upward from anchor) and ``"right"`` for angle 180/270.
    Helper ``justify_for_angle()`` picks the convention automatically.
    """
    _ = group_id  # noqa: F841 — reserved
    if kind not in ("local", "global", "hierarchical"):
        raise ValueError(f"Unknown label kind: {kind!r}")
    from kicad_mcp.utils.sch_geometry import snap_to_grid as _snap
    x, y = _snap(float(x), float(y))
    if uuid is None:
        uuid = stable_uuid(f"{project_uuid}|{kind}_label|{name}|{x},{y}")
    pad = " " * indent
    tag = {
        "local": "label",
        "global": "global_label",
        "hierarchical": "hierarchical_label",
    }[kind]
    shape = ' (shape bidirectional)' if kind in ("hierarchical", "global") else ""
    just_part = ""
    if justify in ("left", "right"):
        just_part = f" (justify {justify})"
    return (
        f'{pad}({tag} "{name}"{shape} (at {_fmt(x)} {_fmt(y)} {int(angle)})'
        f" (effects (font (size 1.27 1.27)){just_part})"
        f' (uuid "{uuid}"))'
    )


def justify_for_angle(angle: int) -> str:
    """Default justify convention for label angles 0/90/180/270 — picks
    ``"left"`` for 0/90 and ``"right"`` for 180/270 so that text reads
    consistently outward from a chip body. See ``render_label``.
    """
    a = ((int(angle) % 360) + 360) % 360
    return "left" if a in (0, 90) else "right"


# Outward-direction → (dx_mm, dy_mm, label_angle_deg)
# In schematic coords (Y grows downward):
#   "right" (+X)  → angle   0 → offset (+3.81,  0)
#   "up"   (-Y)   → angle  90 → offset ( 0,   -3.81)
#   "left" (-X)   → angle 180 → offset (-3.81,  0)
#   "down" (+Y)   → angle 270 → offset ( 0,   +3.81)
LABEL_STUB_LEN_MM = 3.81

LABEL_OUTWARD_TABLE = {
    0:   (LABEL_STUB_LEN_MM,  0.0, 0),
    90:  (0.0,  -LABEL_STUB_LEN_MM, 90),
    180: (-LABEL_STUB_LEN_MM, 0.0, 180),
    270: (0.0,   LABEL_STUB_LEN_MM, 270),
}


def pin_outward_angle(
    pin_xy: tuple[float, float], sym_at_xy: tuple[float, float]
) -> int:
    """Determine the outward angle (away from the symbol body) for a pin
    at ``pin_xy`` belonging to a symbol whose ``at`` anchor is ``sym_at_xy``.

    Returns 0/90/180/270 for right/up/left/down. Falls back to 0 when the
    delta is exactly zero (degenerate case, treated as right).
    """
    dx = pin_xy[0] - sym_at_xy[0]
    dy = pin_xy[1] - sym_at_xy[1]
    if abs(dx) >= abs(dy):
        return 0 if dx >= 0 else 180
    return 270 if dy > 0 else 90


def _fmt(v: float) -> str:
    """Format a float the way KiCad does: drop trailing zeros.

    Schematic IU is 100 nm — the file format truncates to 4 decimal mm
    on every save. Emitting more is pointless drift the next KiCad save
    would silently normalise away (and which diff tools then flag as a
    spurious change). See ``CLAUDE.md`` §Coord-Systems #10 / K10.
    """
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _indent_block(text: str, levels: int = 1) -> str:
    pad = "  " * levels
    return "\n".join((pad + line) if line else line for line in text.splitlines())
