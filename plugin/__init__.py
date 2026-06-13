# SPDX-License-Identifier: GPL-3.0-or-later
"""KiCad Action Plugin: Claude chat for the open board (via kicad-mcp).

KiCad imports this package from its scripting/plugins directory; the
registration below runs the toolbar button. The try/except keeps the package
importable OUTSIDE KiCad (no ``pcbnew``) so the pure-logic modules
(``claude_bridge``, ``mcp_config``) can be unit tested headless.
"""

from .version import __version__  # noqa: F401  (package version, used by GUI)


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
