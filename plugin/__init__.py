# SPDX-License-Identifier: GPL-3.0-or-later
"""KiCad Action Plugin: Claude chat for the open board (via kicad-mcp).

KiCad imports this package from its scripting/plugins directory; the
registration below runs the toolbar button. The try/except keeps the package
importable OUTSIDE KiCad (no ``pcbnew``) so the pure-logic modules
(``claude_bridge``, ``mcp_config``) can be unit tested headless.
"""

from .version import __version__  # noqa: F401  (package version, used by GUI)


def _inject_local_deps() -> None:
    """Put the plugin-local ``_deps`` dir on ``sys.path`` so the GUI plugin
    (chat panel, ``board_links`` → ``kipy``) imports the SAME bundled deps the
    MCP server uses. KiCad's bundled Python lacks ``kipy`` (it's the separate
    ``kicad-python`` PyPI package) and ignores ``PYTHONPATH`` (isolated build),
    so without this in-process insertion the live IPC cross-probe/selection and
    the chat-link board refresh fail with ``ModuleNotFoundError: kipy`` even
    after the deps were installed into ``_deps``. Idempotent + dir-guarded → a
    no-op when ``_deps`` is absent (rely on site dirs / a KiCad that ships kipy).
    """
    import os
    import sys
    base = os.path.dirname(os.path.abspath(__file__))
    deps = os.path.join(base, "_deps")
    if not os.path.isdir(deps):
        return
    # _deps first, then pywin32's .pth dirs (no-op off Windows / without pywin32).
    for entry in (os.path.join(deps, "win32", "lib"),
                  os.path.join(deps, "win32"), deps):
        if os.path.isdir(entry) and entry not in sys.path:
            sys.path.insert(0, entry)


_inject_local_deps()


def _register_if_in_kicad_gui() -> bool:
    """Register the toolbar button — but ONLY inside the running pcbnew GUI.

    The KiCad-bundled ``python.exe`` can ``import pcbnew`` *standalone* (our MCP
    server and the test suite both run under it). In that no-GUI context the
    program singleton ``Pgm()`` is null, and calling ``register()`` trips a C++
    ``wxASSERT(PgmOrNull())`` in ``action_plugin.cpp`` — which a Python
    ``try/except`` CANNOT catch (it is not a Python exception; on Windows it pops
    an assert dialog). So we gate on a live ``wx.App``: the GUI plugin loader has
    one before it imports us; a bare interpreter / console import does not.
    """
    try:  # pragma: no cover - exercised only inside KiCad
        import wx
        if wx.GetApp() is None:        # no running GUI → would assert; skip
            return False
        from .claude_action import ClaudeActionPlugin
        ClaudeActionPlugin().register()
        return True
    except Exception:
        # Not running inside KiCad (pcbnew/wx unavailable) — fine; the
        # subprocess/config/preflight helpers are still importable for tests.
        return False


_register_if_in_kicad_gui()
