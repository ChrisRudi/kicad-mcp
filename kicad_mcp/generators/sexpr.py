# SPDX-License-Identifier: GPL-3.0-or-later
"""
S-Expression Builder for KiCad file generation.

Provides an indent-aware builder that produces valid KiCad S-expression output.
"""

from uuid import UUID, uuid5

# Deterministic UUID namespace for reproducible output
KICAD_MCP_NS = UUID("d4e5f6a7-b8c9-0123-4567-89abcdef0123")

# KiCad file format versions (KiCad 10)
KICAD_SCH_VERSION = 20231120
KICAD_PCB_VERSION = 20240108

# Standard KiCad units (mm)
PIN_SPACING = 2.54
FONT_SIZE = 1.27
SYM_HALF_WIDTH = 5.08
PIN_LENGTH = 2.54


def uid(seed: str) -> str:
    """Generate a deterministic UUID from a seed string."""
    return str(uuid5(KICAD_MCP_NS, seed))


class SExpr:
    """Indent-aware S-Expression builder for KiCad files.

    Usage:
        s = SExpr()
        s.open("kicad_sch")
        s.prop("version", KICAD_SCH_VERSION)
        s.prop("generator", '"kicad-mcp"')
        s.close()
        print(s.render())
    """

    def __init__(self, indent_size: int = 2):
        self._lines: list[str] = []
        self._indent = 0
        self._indent_str = " " * indent_size

    def _prefix(self) -> str:
        return self._indent_str * self._indent

    def open(self, tag: str, *args: str | int | float) -> "SExpr":
        """Open a new S-expression node: (tag arg1 arg2 ..."""
        parts = [tag] + [self._format_arg(a) for a in args]
        self._lines.append(f"{self._prefix()}({' '.join(parts)}")
        self._indent += 1
        return self

    def close(self) -> "SExpr":
        """Close the current S-expression node with )."""
        self._indent = max(0, self._indent - 1)
        self._lines.append(f"{self._prefix()})")
        return self

    def prop(self, tag: str, value: str | int | float) -> "SExpr":
        """Add a single-line property: (tag value)."""
        self._lines.append(f"{self._prefix()}({tag} {self._format_arg(value)})")
        return self

    def prop_quoted(self, tag: str, value: str) -> "SExpr":
        """Add a quoted property: (tag "value")."""
        self._lines.append(f'{self._prefix()}({tag} "{value}")')
        return self

    def emit(self, text: str) -> "SExpr":
        """Emit a complete S-expression line."""
        self._lines.append(f"{self._prefix()}{text}")
        return self

    def blank(self) -> "SExpr":
        """Add a blank line."""
        self._lines.append("")
        return self

    # --- KiCad-specific helpers ---

    def kicad_property(
        self, name: str, value: str, x: float = 0, y: float = 0, angle: float = 0, hide: bool = False
    ) -> "SExpr":
        """Emit a KiCad property node."""
        hide_str = " hide" if hide else ""
        effects = f'(effects (font (size {FONT_SIZE} {FONT_SIZE})){hide_str})'
        self._lines.append(
            f'{self._prefix()}(property "{name}" "{value}" (at {x} {y} {int(angle)}) {effects})'
        )
        return self

    def pin(
        self, pin_type: str, name: str, number: str | int, x: float, y: float, angle: int = 0, length: float = PIN_LENGTH
    ) -> "SExpr":
        """Emit a KiCad pin."""
        self._lines.append(
            f'{self._prefix()}(pin {pin_type} line (at {x} {y} {angle}) (length {length})'
            f' (name "{name}" (effects (font (size {FONT_SIZE} {FONT_SIZE}))))'
            f' (number "{number}" (effects (font (size {FONT_SIZE} {FONT_SIZE})))))'
        )
        return self

    def pin_instance(self, number: str | int, uuid_str: str) -> "SExpr":
        """Emit a pin instance reference in a symbol."""
        self._lines.append(f'{self._prefix()}(pin "{number}" (uuid "{uuid_str}"))')
        return self

    def global_label(self, name: str, x: float, y: float, uuid_str: str,
                     angle: int = 0) -> "SExpr":
        """Emit a global label (for power nets)."""
        self._lines.append(
            f'{self._prefix()}(global_label "{name}" (at {x} {y} {angle})'
            f' (effects (font (size {FONT_SIZE} {FONT_SIZE}))) (uuid "{uuid_str}"))'
        )
        return self

    def net_label(self, name: str, x: float, y: float, uuid_str: str,
                  angle: int = 0) -> "SExpr":
        """Emit a local net label."""
        self._lines.append(
            f'{self._prefix()}(label "{name}" (at {x} {y} {angle})'
            f' (effects (font (size {FONT_SIZE} {FONT_SIZE}))) (uuid "{uuid_str}"))'
        )
        return self

    def wire(self, x1: float, y1: float, x2: float, y2: float, uuid_str: str) -> "SExpr":
        """Emit a schematic wire segment between two points."""
        self._lines.append(
            f'{self._prefix()}(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (uuid "{uuid_str}"))'
        )
        return self

    def junction(self, x: float, y: float, uuid_str: str) -> "SExpr":
        """Emit a junction dot — Pflicht an jedem T-Abzweig (Nutzer-Regel:
        „wenn aus einer geraden Leitung eine Leitung abzweigt, muss ein Punkt
        das kennzeichnen"; KiCad-Konvention ebenso)."""
        self._lines.append(
            f'{self._prefix()}(junction (at {x} {y}) (diameter 0)'
            f' (color 0 0 0 0) (uuid "{uuid_str}"))'
        )
        return self

    def no_connect(self, x: float, y: float, uuid_str: str) -> "SExpr":
        """Emit a no-connect flag (X marker) at a pin endpoint."""
        self._lines.append(
            f'{self._prefix()}(no_connect (at {x} {y}) (uuid "{uuid_str}"))'
        )
        return self

    def polyline(self, points: list[tuple[float, float]], uuid_str: str,
                 stroke_width: float = 0.2, stroke_type: str = "dash",
                 color: tuple[int, int, int, float] = (0, 0, 200, 0.3)) -> "SExpr":
        """Emit a graphical polyline (for functional block frames)."""
        pts = " ".join(f"(xy {x} {y})" for x, y in points)
        r, g, b, a = color
        self._lines.append(
            f'{self._prefix()}(polyline (pts {pts})'
            f' (stroke (width {stroke_width}) (type {stroke_type})'
            f' (color {r} {g} {b} {a})) (uuid "{uuid_str}"))'
        )
        return self

    def text_note(self, text: str, x: float, y: float, uuid_str: str,
                  font_size: float = 2.0) -> "SExpr":
        """Emit a text annotation (for block labels)."""
        self._lines.append(
            f'{self._prefix()}(text "{text}" (at {x} {y} 0)'
            f' (effects (font (size {font_size} {font_size}))) (uuid "{uuid_str}"))'
        )
        return self

    # --- 6.1: Multi-sheet helpers ---

    def hierarchical_label(self, name: str, x: float, y: float, uuid_str: str,
                           shape: str = "bidirectional", angle: int = 0) -> "SExpr":
        """Emit a hierarchical label (inter-sheet connection)."""
        self._lines.append(
            f'{self._prefix()}(hierarchical_label "{name}" (shape {shape})'
            f' (at {x} {y} {angle})'
            f' (effects (font (size {FONT_SIZE} {FONT_SIZE}))) (uuid "{uuid_str}"))'
        )
        return self

    def sheet(self, name: str, filename: str, x: float, y: float,
              w: float, h: float, uuid_str: str,
              pins: list[tuple[str, str, float, float, str]] | None = None) -> "SExpr":
        """Emit a sheet symbol (hierarchical sub-sheet reference).

        pins: list of (pin_name, shape, x_offset, y_offset, pin_uuid)
        """
        self.open("sheet", f'(at {x} {y})', f'(size {w} {h})')
        self.emit('(fields_autoplaced yes)')
        self.kicad_property("Sheetname", name, x + 0.5, y - 0.5)
        self.kicad_property("Sheetfile", filename, x + 0.5, y + h + 0.5)
        if pins:
            for pname, pshape, px, py, puuid in pins:
                self._lines.append(
                    f'{self._prefix()}(pin "{pname}" {pshape} (at {x + px} {y + py} 0)'
                    f' (effects (font (size {FONT_SIZE} {FONT_SIZE}))) (uuid "{puuid}"))'
                )
        self.emit(f'(uuid "{uuid_str}")')
        self.close()
        return self

    def gr_rect(self, x1: float, y1: float, x2: float, y2: float, layer: str, uuid_str: str) -> "SExpr":
        """Emit a graphical rectangle (typically board outline)."""
        self._lines.append(
            f'{self._prefix()}(gr_rect (start {x1} {y1}) (end {x2} {y2})'
            f' (layer "{layer}") (stroke (width 0.1) (type default)) (uuid "{uuid_str}"))'
        )
        return self

    def render(self) -> str:
        """Render all accumulated lines as a string."""
        return "\n".join(self._lines) + "\n"

    @staticmethod
    def _format_arg(arg: str | int | float) -> str:
        if isinstance(arg, str):
            if arg.startswith('"') or arg.startswith("("):
                return arg
            return arg
        if isinstance(arg, float):
            return f"{arg:g}"
        return str(arg)
