# SPDX-License-Identifier: GPL-3.0-or-later
"""The KiCad-side chat UI for talking to Claude about the open board.

:class:`ClaudeChatPanel` is the actual UI (a wx.Panel) so it can live either
docked inside the PCB editor as an AUI pane (see :mod:`dock`) or hosted in
the floating :class:`ClaudeChatDialog` fallback. Each "Send" runs one Claude
Code turn in a worker thread (so the GUI stays responsive) via
:mod:`claude_bridge`, and appends the reply. The Claude session id is kept on
the panel so the whole exchange is one conversation. The look (dark terminal,
monospace, Claude-orange bullets, pulsing spinner) comes from
:mod:`chat_theme` so it matches the Claude Code CLI.
"""

from __future__ import annotations

import threading

import wx  # KiCad ships wxPython; only importable inside KiCad

from . import chat_theme as theme
from . import claude_bridge
from .version import __version__


def _pick_mono_font() -> "wx.Font":
    """The first installed monospace face from the theme's candidate list."""
    font = wx.Font(theme.FONT_SIZE_PT, wx.FONTFAMILY_TELETYPE,
                   wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
    for face in theme.FONT_FACES:
        if font.SetFaceName(face):
            break
    return font


class ClaudeChatPanel(wx.Panel):
    """The chat surface itself — dockable (AUI pane) or dialog-hosted."""

    def __init__(self, parent, plan, on_open_setup=None):
        super().__init__(parent)
        # The RunPlan carries the path-consistent cwd / --mcp-config / claude
        # argv for this machine (native Windows, native Linux, or WSL-bridge).
        self._plan = plan
        self._on_open_setup = on_open_setup  # reopen Einrichtung/Update panel
        self._session_id = None
        self._busy = False
        self._mono = _pick_mono_font()
        # Board elements named in replies become clickable: char-range → target.
        self._refs: set = set()
        self._nets: set = set()
        self._links: list = []  # (start, end, kind, value)

        self.SetBackgroundColour(wx.Colour(theme.BACKGROUND))
        root = wx.BoxSizer(wx.VERTICAL)

        self._out = wx.TextCtrl(
            self,
            style=(wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2
                   | wx.BORDER_NONE),
        )
        self._out.SetBackgroundColour(wx.Colour(theme.BACKGROUND))
        self._out.SetForegroundColour(wx.Colour(theme.FOREGROUND))
        self._out.SetFont(self._mono)
        self._out.Bind(wx.EVT_LEFT_UP, self._on_output_click)
        root.Add(self._out, 1, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        chevron = wx.StaticText(self, label="❯")
        chevron.SetForegroundColour(wx.Colour(theme.CLAUDE_ORANGE))
        chevron.SetFont(self._mono.Bold())
        row.Add(chevron, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self._in = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self._in.SetBackgroundColour(wx.Colour(theme.SURFACE))
        self._in.SetForegroundColour(wx.Colour(theme.FOREGROUND))
        self._in.SetFont(self._mono)
        self._in.SetHint("Frag Claude etwas über dieses Board …")
        self._in.Bind(wx.EVT_TEXT_ENTER, self._on_send)
        row.Add(self._in, 1, wx.EXPAND | wx.RIGHT, 6)
        self._send = wx.Button(self, label="Senden")
        self._send.SetBackgroundColour(wx.Colour(theme.SURFACE))
        self._send.SetForegroundColour(wx.Colour(theme.FOREGROUND))
        row.Add(self._send, 0)
        self._send.Bind(wx.EVT_BUTTON, self._on_send)
        root.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        foot = wx.BoxSizer(wx.HORIZONTAL)
        self._status = wx.StaticText(self, label=theme.STATUS_READY)
        self._status.SetForegroundColour(wx.Colour(theme.DIM))
        self._status.SetFont(self._mono)
        foot.Add(self._status, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        if self._on_open_setup:
            setup_btn = wx.Button(self, label="Einrichtung / Update")
            setup_btn.SetBackgroundColour(wx.Colour(theme.SURFACE))
            setup_btn.SetForegroundColour(wx.Colour(theme.DIM))
            setup_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_open_setup())
            foot.Add(setup_btn, 0, wx.RIGHT, 6)
        root.Add(foot, 0, wx.EXPAND | wx.BOTTOM, 8)

        self.SetSizer(root)

        # Pulsing CLI-style spinner ("✻ Claude denkt nach … (12s)") plus the
        # live activity from the stream ("Tool list_pcb_footprints …").
        self._spinner = wx.Timer(self)
        self._tick = 0
        self._activity = ""
        self.Bind(wx.EVT_TIMER, self._on_spin, self._spinner)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)

        self._append(
            "banner",
            "Hallo! Ich bin über kicad-mcp mit deinem offenen Board verbunden. "
            "Frag mich z.B. 'wie viele GND-Vias gibt es?' oder 'markier die 3 "
            "kleinsten'.",
        )
        self._in.SetFocus()

    # -- public -------------------------------------------------------------

    def set_plan(self, plan) -> None:
        """Refresh the run plan (a re-shown docked pane keeps the old panel;
        the project/board may have changed since)."""
        self._plan = plan

    # -- ui helpers ---------------------------------------------------------

    def _write(self, text: str, color: str, bold: bool = False,
               underline: bool = False) -> None:
        attr = wx.TextAttr(wx.Colour(color))
        attr.SetFontWeight(wx.FONTWEIGHT_BOLD if bold else wx.FONTWEIGHT_NORMAL)
        attr.SetFontUnderlined(underline)
        self._out.SetDefaultStyle(attr)
        self._out.AppendText(text)

    def _append(self, role: str, text: str) -> None:
        style = theme.style_for(role)
        self._write(style["prefix"], style["prefix_color"], bold=True)
        self._write(text + "\n\n", style["text_color"])

    def _append_claude(self, text: str) -> None:
        """Claude reply with board references/nets rendered as clickable links
        (orange + underlined); clicking selects + zooms them in the editor."""
        from . import board_links
        style = theme.style_for("claude")
        self._write(style["prefix"], style["prefix_color"], bold=True)
        for chunk, target in board_links.tokenize(text + "\n\n", self._refs,
                                                   self._nets):
            if target is None:
                self._write(chunk, style["text_color"])
                continue
            start = self._out.GetLastPosition()
            self._write(chunk, theme.CLAUDE_ORANGE, underline=True)
            kind, value = target
            self._links.append((start, self._out.GetLastPosition(), kind, value))

    def _set_status(self, label: str, color: str) -> None:
        self._status.SetLabel(label)
        self._status.SetForegroundColour(wx.Colour(color))
        self._status.Refresh()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._send.Enable(not busy)
        self._in.Enable(not busy)
        if busy:
            self._tick = 0
            self._activity = ""
            self._set_status(theme.spinner_label(0), theme.CLAUDE_ORANGE)
            self._spinner.Start(theme.SPINNER_INTERVAL_MS)
        else:
            self._spinner.Stop()
            self._set_status(theme.STATUS_READY, theme.DIM)

    def _on_activity(self, text: str) -> None:
        """Live progress from the stream (called via wx.CallAfter)."""
        self._activity = text or ""

    def _on_spin(self, _evt) -> None:
        self._tick += 1
        label = theme.spinner_label(self._tick)
        if self._activity:
            label += "  ·  " + self._activity
        self._set_status(label, theme.CLAUDE_ORANGE)

    def _on_destroy(self, evt) -> None:
        self._spinner.Stop()  # never let the timer outlive the window
        evt.Skip()

    # -- send flow ----------------------------------------------------------

    def _on_send(self, _evt) -> None:
        if self._busy:
            return
        prompt = self._in.GetValue().strip()
        if not prompt:
            return
        self._in.SetValue("")
        self._append("user", prompt)
        self._set_busy(True)
        threading.Thread(
            target=self._worker, args=(prompt,), daemon=True
        ).start()

    def _worker(self, prompt: str) -> None:
        result = claude_bridge.ask(
            prompt,
            project_dir=self._plan.run_cwd,
            mcp_config_path=self._plan.config_arg_path,
            session_id=self._session_id,
            claude_cmd=self._plan.claude_cmd,
            on_status=lambda s: wx.CallAfter(self._on_activity, s),
        )
        # Refresh the board's refs/nets so this reply can be linkified (best
        # effort; an unreachable editor just means no links this turn).
        try:
            from . import board_links
            _client, board = board_links.connect()
            result["_refs"], result["_nets"] = board_links.board_targets(board)
        except Exception:
            pass
        wx.CallAfter(self._on_reply, result)

    def _on_reply(self, result: dict) -> None:
        if not self:  # panel destroyed while Claude was thinking
            return
        if result.get("_refs") is not None:
            self._refs = result["_refs"]
            self._nets = result.get("_nets") or set()
        mcp_status = result.get("mcp_status") or ""
        if mcp_status.startswith("failed"):
            self._append(
                "error",
                "MCP nicht verbunden (" + mcp_status + ") — Antwort kam OHNE "
                "Board-Tools. Einrichtung öffnen und 'Erneut prüfen'.",
            )
        if result.get("ok"):
            self._session_id = result.get("session_id") or self._session_id
            self._append_claude(result.get("text") or "(keine Antwort)")
        else:
            self._append("error", result.get("error") or "unbekannt")
        self._set_busy(False)
        self._in.SetFocus()

    # -- board cross-probe (clickable elements) -----------------------------

    def _on_output_click(self, evt) -> None:
        evt.Skip()  # let the control handle caret/selection as usual
        if not self._links:
            return
        hit = self._out.HitTestPos(evt.GetPosition())
        # wx returns (HitTestResult, pos); pos is the char index.
        pos = hit[1] if isinstance(hit, (tuple, list)) else hit
        target = next((( k, v) for s, e, k, v in self._links
                       if s <= pos < e), None)
        if target is None:
            return
        kind, value = target
        threading.Thread(target=self._select_worker, args=(kind, value),
                         daemon=True).start()

    def _select_worker(self, kind: str, value: str) -> None:
        from . import board_links
        try:
            client, board = board_links.connect()
            count = board_links.select(client, board, kind, value)
            msg = (f"{value}: {count} Element(e) markiert"
                   if count else f"{value}: nichts gefunden")
        except Exception as exc:
            msg = f"Auswahl fehlgeschlagen: {exc}"
        wx.CallAfter(self._flash_status, msg)

    def _flash_status(self, msg: str) -> None:
        if self and not self._busy:
            self._set_status(msg, theme.CLAUDE_ORANGE)


class ClaudeChatDialog(wx.Dialog):
    """Floating fallback when docking into the PCB editor isn't possible."""

    def __init__(self, parent, plan, on_open_setup=None):
        super().__init__(
            parent, title=f"Claude — KiCad (v{__version__})", size=(680, 580),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetBackgroundColour(wx.Colour(theme.BACKGROUND))
        self.panel = ClaudeChatPanel(self, plan, on_open_setup=on_open_setup)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.panel, 1, wx.EXPAND)
        self.SetSizer(sizer)
