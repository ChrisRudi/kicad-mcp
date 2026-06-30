# SPDX-License-Identifier: GPL-3.0-or-later
"""KiCad PCB-editor Action Plugin: a "Claude" toolbar button that opens the
chat panel wired to the bundled kicad-mcp server + the open board.

Stufe 1: fixed on the Claude Code backend. The kicad-mcp package is referenced
from ``KICAD_MCP_ROOT`` (env) or the default repo path; a later stage bundles a
copy inside the plugin.
"""

from __future__ import annotations

import os

import pcbnew  # only importable inside KiCad

from . import ipc_setup, mcp_config, preflight, runtime_env
from .version import __version__

# Dev fallback only — used when neither an env override nor the bundled copy is
# present (e.g. running from a repo checkout before bundling). Empty by default
# so NO machine-specific path is ever hardcoded or shipped; set
# ``KICAD_MCP_DEV_ROOT`` to your local kicad-mcp checkout if you rely on this.
_DEV_MCP_ROOT = os.environ.get("KICAD_MCP_DEV_ROOT", "").strip()


def _mcp_root() -> str:
    """Where the kicad-mcp package lives, self-contained-first.

    1) ``KICAD_MCP_ROOT`` env (if it actually contains ``kicad_mcp/``),
    2) the bundled copy shipped inside the plugin (``<plugin>/mcp``),
    3) the dev checkout fallback (only if it actually exists),
    4) else the bundled path anyway — it is the EXPECTED location, so every
       preflight/probe error message then points at the right directory
       instead of a foreign dev path.
    """
    env = os.environ.get("KICAD_MCP_ROOT", "").strip()
    if env and os.path.isdir(os.path.join(env, "kicad_mcp")):
        return env
    bundled = os.path.join(os.path.dirname(__file__), "mcp")
    if os.path.isdir(os.path.join(bundled, "kicad_mcp")):
        return bundled
    if _DEV_MCP_ROOT and os.path.isdir(os.path.join(_DEV_MCP_ROOT, "kicad_mcp")):
        return _DEV_MCP_ROOT
    return bundled

# Keep references so non-modal dialogs aren't garbage-collected.
_OPEN_DIALOGS: list = []


def _kicad_common_path():
    """The running KiCad's exact ``kicad_common.json`` (per-version dir from the
    settings manager), with a glob fallback if that isn't available."""
    settings_dir = None
    try:
        settings_dir = pcbnew.GetSettingsManager().GetUserSettingsPath()
    except Exception:
        pass
    return ipc_setup.find_kicad_common(settings_dir)


class ClaudeActionPlugin(pcbnew.ActionPlugin):
    def defaults(self) -> None:
        self.name = "Claude (kicad-mcp)"
        self.category = "AI"
        self.description = "Mit Claude über das offene Board reden (kicad-mcp)"
        self.show_toolbar_button = True
        here = os.path.dirname(__file__)
        icon = os.path.join(here, "icon.png")           # MCP logo (light theme)
        icon_dark = os.path.join(here, "icon_dark.png")  # MCP logo (dark theme)
        if os.path.isfile(icon):
            self.icon_file_name = icon
            self.dark_icon_file_name = (
                icon_dark if os.path.isfile(icon_dark) else icon)

    def Run(self) -> None:
        import wx  # local: only available at click time inside KiCad

        board = pcbnew.GetBoard()
        pcb_path = board.GetFileName() if board else ""
        project_dir = os.path.dirname(pcb_path) if pcb_path else os.getcwd()
        cfg_path = os.path.join(project_dir, ".kicad-mcp", "claude_mcp.json")
        mcp_root = _mcp_root()  # bundled-first; env override; dev fallback

        def open_chat() -> None:
            from . import dock
            from .chat_dialog import ClaudeChatDialog, ClaudeChatPanel
            # Resolve a path-consistent plan (native Win/Linux, or WSL-bridge),
            # then write the MCP config in that plan's path styles. The preflight
            # guarantees python+root+claude are present before this runs.
            plan = runtime_env.resolve(project_dir, mcp_root, cfg_path)
            if plan is None:
                wx.MessageBox(
                    "Konnte Claude Code oder KiCad-Python nicht auflösen.\n"
                    "Setze KICAD_MCP_ROOT und KICAD_PYTHON_PATH und installiere "
                    "Claude Code (im selben System wie KiCad oder in WSL).",
                    "Claude", wx.OK | wx.ICON_ERROR,
                )
                return
            try:
                # config_pythonpath is the real local mcp_root (Windows-style
                # even in bridge mode), so the isdir() check inside is valid.
                mcp_config.write_mcp_config(
                    plan.config_write_path, plan.config_pythonpath,
                    python_exe=plan.config_command)
            except Exception as exc:  # pragma: no cover - GUI path
                wx.MessageBox(
                    f"Konnte die MCP-Konfiguration nicht schreiben:\n{exc}\n\n"
                    "Setze KICAD_MCP_ROOT (kicad-mcp-Ordner) und "
                    "KICAD_PYTHON_PATH (KiCad-Python mit kipy).",
                    "Claude", wx.OK | wx.ICON_ERROR,
                )
                return
            # Preferred: snap the chat into the PCB editor as a native AUI
            # pane (dockable/tear-off like Appearance/Search). A re-shown pane
            # keeps its panel, so refresh the plan. Fallback: floating dialog.
            panel = dock.attach(
                lambda frame: ClaudeChatPanel(
                    frame, plan, on_open_setup=open_setup),
                caption=f"Claude — KiCad (v{__version__})",
            )
            if panel is not None:
                set_plan = getattr(panel, "set_plan", None)
                if set_plan:
                    set_plan(plan)
                return
            dlg = ClaudeChatDialog(None, plan, on_open_setup=open_setup)
            _OPEN_DIALOGS.append(dlg)
            dlg.Bind(wx.EVT_CLOSE,
                     lambda e, d=dlg: (_OPEN_DIALOGS.remove(d), d.Destroy()))
            dlg.Show()  # non-modal: keep working in KiCad + watch the board update

        # Turn KiCad's IPC API server on for the user (the switch behind
        # Preferences → Plugins → KiCad-API). It only takes effect at the next
        # launch, so flag the one restart if we just flipped it.
        common_path = _kicad_common_path()
        ensure = ipc_setup.ensure_ipc_enabled(common_path)
        if ensure.get("changed"):
            wx.MessageBox(
                "Die KiCad-API (IPC) wurde für dich aktiviert.\n\n"
                "Bitte starte KiCad einmal neu — danach kann Claude live am "
                "offenen Board arbeiten.",
                "Claude — KiCad-API aktiviert", wx.OK | wx.ICON_INFORMATION,
            )

        # First click (or anything not ready) → onboarding checklist; else chat.
        checks = preflight.run_preflight(
            mcp_root, project_dir,
            board_open=bool(pcb_path), board_name=os.path.basename(pcb_path),
            common_path=common_path, ipc_restart_hint=ensure.get("changed", False),
        )
        def open_setup() -> None:
            from .setup_dialog import SetupDialog
            setup = SetupDialog(
                None, project_dir, mcp_root, cfg_path,
                board_open=bool(pcb_path), board_name=os.path.basename(pcb_path),
                common_path=common_path,
                ipc_restart_hint=ensure.get("changed", False),
                on_start_chat=open_chat,
            )
            _OPEN_DIALOGS.append(setup)
            setup.Bind(wx.EVT_CLOSE,
                       lambda e, d=setup: (_OPEN_DIALOGS.remove(d), d.Destroy()))
            setup.Show()  # non-modal

        # All green → straight to chat (Einrichtung/Update bleibt von dort
        # erreichbar); sonst das Einrichtungs-Panel.
        if preflight.hard_ok(checks):
            open_chat()
        else:
            open_setup()
