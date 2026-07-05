# SPDX-License-Identifier: GPL-3.0-or-later
"""Net-tag emission helpers for ``.kicad_pcb`` text editing.

KiCad accepts two ways of tagging a routing element (segment / arc / via /
zone) with a net:

* **Indexed form** — ``(net N)`` inside the element, plus a top-level
  ``(net N "name")`` table. This is what the SWIG ``pcbnew`` writer emits
  on classic V8 / V9 PCBs and what the public test fixtures in this
  repo use.
* **String form** — ``(net "name")`` inside the element, **no** top-level
  table. KiCad 10's editor accepts this and round-trips it; some
  hand-curated / tool-generated PCBs (e.g. the reference mainboards) use it
  exclusively because it survives net-rename refactors without index
  churn.

The element emitters in ``pcb_geometry_tools`` / ``pcb_patch_tools`` were
originally hard-wired to the indexed form. On a string-form PCB they
silently wrote ``(net 0)`` (= no-connect) and polluted the file with a
synthetic ``(net 0 "name")`` line at the top. This module centralises the
format detection + tag emission so the two emitter modules stay in sync.
"""

from __future__ import annotations

import re
import uuid

_NET_TABLE_RE = re.compile(
    r'^\s*\(net\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\s*\)', re.MULTILINE,
)
_NET_STR_REF_RE = re.compile(r'\(net\s+"((?:[^"\\]|\\.)*)"\s*\)')


def pcb_net_format(pcb_text: str) -> str:
    """Return ``"string"`` or ``"index"`` for the net-tag convention in
    ``pcb_text``.

    Detection logic:

    * Has at least one top-level ``(net N "name")`` table entry → ``"index"``.
      The index form is authoritative for the whole file in that case;
      the SWIG writer never mixes the two.
    * No table, but at least one ``(net "name")`` short-form ref anywhere →
      ``"string"``.
    * Neither (fresh / empty PCB) → ``"index"`` (the SWIG-writer default,
      what KiCad emits for a brand-new board).
    """
    if _NET_TABLE_RE.search(pcb_text):
        return "index"
    if _NET_STR_REF_RE.search(pcb_text):
        return "string"
    return "index"


def ensure_net_tag(
    pcb_text: str, net_name: str,
) -> tuple[str, str, str, int | None]:
    """Resolve ``net_name`` into the ready-to-emit ``(net …)`` S-expression
    fragment for the surrounding PCB's net-tag convention.

    Returns ``(updated_text, net_tag, net_format, net_id)``:

    * ``net_tag`` — literal S-expression to embed inside the element body,
      e.g. ``'(net 17)'`` or ``'(net "GND")'`` or ``'(net 0)'``.
    * ``net_format`` — ``"string"`` or ``"index"``.
    * ``net_id`` — the integer index for index-form PCBs (caller-facing
      metadata); ``None`` for string-form PCBs.

    Empty ``net_name`` always emits ``"(net 0)"`` (no-connect), regardless
    of format — that is the universal "unassigned" sentinel KiCad uses on
    both sides of the schism.

    For **pad** tags use :func:`ensure_pad_net_tag` instead — KiCad's pad
    form on indexed-form PCBs is the longer ``(net N "name")`` (both
    index and name), not the short ``(net N)`` used by tracks / vias /
    arcs / zones.
    """
    if not net_name:
        return pcb_text, "(net 0)", pcb_net_format(pcb_text), 0

    fmt = pcb_net_format(pcb_text)
    if fmt == "string":
        return pcb_text, f'(net "{net_name}")', "string", None

    new_text, idx = _ensure_index_net(pcb_text, net_name)
    return new_text, f"(net {idx})", "index", idx


def ensure_pad_net_tag(
    pcb_text: str, net_name: str,
) -> tuple[str, str, str, int | None]:
    """Pad-specific variant of :func:`ensure_net_tag`. Pads on indexed-form
    PCBs carry the **full** ``(net N "name")`` tag (both the numeric index
    and the textual name), not the short ``(net N)`` form used by
    tracks / arcs / vias / zones. String-form PCBs use the same
    ``(net "name")`` short form for both pads and routing elements.

    Returns the same ``(updated_text, net_tag, net_format, net_id)``
    tuple as :func:`ensure_net_tag`.
    """
    if not net_name:
        return pcb_text, '(net 0 "")', pcb_net_format(pcb_text), 0

    fmt = pcb_net_format(pcb_text)
    if fmt == "string":
        return pcb_text, f'(net "{net_name}")', "string", None

    new_text, idx = _ensure_index_net(pcb_text, net_name)
    return new_text, f'(net {idx} "{net_name}")', "index", idx


def _ensure_index_net(pcb_text: str, net_name: str) -> tuple[str, int]:
    """Index-form ``_ensure_net``: return ``(text, idx)`` and insert the
    ``(net N "name")`` table line if missing. Internal to this module —
    callers should go through :func:`ensure_net_tag`."""
    table = {m.group(2): int(m.group(1))
             for m in _NET_TABLE_RE.finditer(pcb_text)}
    if net_name in table:
        return pcb_text, table[net_name]
    # KiCad reserves index 0 for "no net" — a real net must start at 1. On a
    # bootstrap board with no net table at all, also emit the (net 0 "")
    # sentinel, or the first real net would land on 0 and read as unconnected.
    bootstrap = not table
    next_id = (max(table.values()) + 1) if table else 1
    new_line = f'\n\t(net {next_id} "{net_name}")'
    if bootstrap:
        new_line = '\n\t(net 0 "")' + new_line
    last_def = list(_NET_TABLE_RE.finditer(pcb_text))
    if last_def:
        insert_at = last_def[-1].end()
        return pcb_text[:insert_at] + new_line + pcb_text[insert_at:], next_id
    layers_m = re.search(r'(\(layers\s*\n[\s\S]*?\n\t\))', pcb_text)
    if layers_m:
        insert_at = layers_m.end()
    else:
        insert_at = pcb_text.find("\n") + 1
    return (
        pcb_text[:insert_at] + new_line + "\n" + pcb_text[insert_at:],
        next_id,
    )


def segment_block(
    p1: tuple[float, float], p2: tuple[float, float],
    width_mm: float, layer: str, net_tag: str,
) -> str:
    """Emit one ``(segment …)`` block (KiCad-10-Format, Tab-Einrückung).

    Die EINE Quelle für Track-Segmente — ``pcb_geometry_tools`` und
    ``pcb_patch_tools`` emittieren beide hierüber. ``net_tag`` kommt aus
    :func:`ensure_net_tag` (Index- oder String-Form, je nach Board).
    """
    return (
        "\t(segment\n"
        f"\t\t(start {p1[0]:.6f} {p1[1]:.6f})\n"
        f"\t\t(end {p2[0]:.6f} {p2[1]:.6f})\n"
        f"\t\t(width {width_mm:.6f})\n"
        f'\t\t(layer "{layer}")\n'
        f"\t\t{net_tag}\n"
        f'\t\t(uuid "{uuid.uuid4()}")\n'
        "\t)\n"
    )
