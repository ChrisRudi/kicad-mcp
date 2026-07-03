# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure kipy/IPC board helpers shared across the IPC tool modules.

Extracted from ``tools/ipc_tools.py`` so ``ipc_interact_tools`` /
``ipc_markup_tools`` can share them without importing private names from a
sibling tool module. All kipy imports stay lazy (module imports headless); the
functions operate only on a passed kipy ``board`` object + layer strings.
"""


def layer_to_enum(layer_str: str):
    """Map a KiCad layer name like ``"F.Cu"`` to the kipy ``BoardLayer`` enum int.

    Returns the enum int value, or ``None`` if the layer cannot be resolved.
    Accepts both human form (``F.Cu``, ``In1.Cu``) and the proto form
    (``BL_F_Cu``).
    """
    try:
        from kipy.proto.board.board_types_pb2 import BoardLayer  # type: ignore
    except Exception:
        return None
    if not layer_str:
        return None
    name = layer_str if layer_str.startswith("BL_") else "BL_" + layer_str.replace(".", "_")
    try:
        return BoardLayer.Value(name)
    except Exception:
        return None


def find_net(board, name: str):
    """Return the kipy ``Net`` wrapper whose ``name`` matches, else ``None``."""
    if not name:
        return None
    try:
        nets = board.get_nets()
    except Exception:
        return None
    for n in nets:
        if getattr(n, "name", None) == name:
            return n
    return None


def board_default_via_nm(board) -> tuple[int, int]:
    """Return the board's default ``(via_diameter, via_drill)`` in nm.

    A freshly-constructed kipy ``Via()`` has diameter/drill **0** and KiCad
    keeps it at 0 on create — a degenerate via. Callers that create vias must
    fall back to the Default net class's via size. Falls back to 0.4/0.2 mm if
    the net classes can't be read.
    """
    try:
        for nc in board.get_project().get_net_classes():
            if getattr(nc, "name", None) in ("Default", "default"):
                d = int(getattr(nc, "via_diameter", 0) or 0)
                k = int(getattr(nc, "via_drill", 0) or 0)
                if d > 0 and k > 0:
                    return d, k
    except Exception:
        pass
    return 400_000, 200_000

