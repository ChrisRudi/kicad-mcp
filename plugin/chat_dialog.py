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

import shlex
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
        self._proc = None       # live claude process of the running turn
        self._stopped = False   # set when the user pressed Stopp
        self._mono = _pick_mono_font()
        # Board elements named in replies become clickable: char-range → target.
        self._refs: set = set()
        self._nets: set = set()
        self._layers: set = set()
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
        # Stopp button — usable WHILE Claude thinks (the input is disabled then),
        # so a too-long turn can be cancelled. Hidden until a turn is running.
        self._stop = wx.Button(self, label="Stopp")
        self._stop.SetBackgroundColour(wx.Colour(theme.SURFACE))
        self._stop.SetForegroundColour(wx.Colour(theme.ERROR_RED))
        self._stop.Bind(wx.EVT_BUTTON, self._on_stop)
        self._stop.Hide()
        row.Add(self._stop, 0, wx.LEFT, 6)
        root.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Raw Claude Code CLI switches (e.g. "--model sonnet"), shlex-split and
        # appended to every turn's command. Empty = plain defaults.
        opt = wx.BoxSizer(wx.HORIZONTAL)
        opt_lbl = wx.StaticText(self, label="⚑")
        opt_lbl.SetForegroundColour(wx.Colour(theme.DIM))
        opt_lbl.SetFont(self._mono)
        opt.Add(opt_lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 6)
        self._opts = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self._opts.SetBackgroundColour(wx.Colour(theme.SURFACE))
        self._opts.SetForegroundColour(wx.Colour(theme.DIM))
        self._opts.SetFont(self._mono)
        self._opts.SetHint("Claude-Optionen, z. B. --model sonnet  (optional)")
        opt.Add(self._opts, 1, wx.EXPAND | wx.RIGHT, 6)
        root.Add(opt, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

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
        """Claude reply with board references / nets / layers rendered as
        clickable links (orange + underlined); clicking selects+zooms an
        element or sets the active layer in the editor."""
        from . import board_links
        style = theme.style_for("claude")
        self._write(style["prefix"], style["prefix_color"], bold=True)
        for chunk, target in board_links.tokenize(text + "\n\n", self._refs,
                                                   self._nets, self._layers):
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
        # While thinking, swap Senden→Stopp so the turn is cancellable.
        self._send.Show(not busy)
        self._stop.Show(busy)
        self._stop.Enable(busy)
        self.Layout()
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
        # Kill any in-flight claude turn + its MCP child so nothing survives a
        # closed chat / closed KiCad (atexit is the additional safety net).
        try:
            claude_bridge.terminate_all()
        except Exception:
            pass
        evt.Skip()

    # -- send flow ----------------------------------------------------------

    def _on_send(self, _evt) -> None:
        if self._busy:
            return
        prompt = self._in.GetValue().strip()
        if not prompt:
            return
        try:
            extra_args = shlex.split(self._opts.GetValue().strip())
        except ValueError:  # unbalanced quotes in the options field
            self._append("error", "Optionen unlesbar (Anführungszeichen?).")
            return
        self._in.SetValue("")
        self._append("user", prompt)
        self._proc = None
        self._stopped = False
        self._set_busy(True)
        threading.Thread(
            target=self._worker, args=(prompt, extra_args), daemon=True
        ).start()

    def _on_stop(self, _evt) -> None:
        """User pressed Stopp — kill the running turn now."""
        if not self._busy:
            return
        self._stopped = True
        self._stop.Enable(False)
        self._set_status("⏹ Wird gestoppt …", theme.ERROR_RED)
        proc = self._proc
        threading.Thread(target=lambda: claude_bridge.stop(proc),
                         daemon=True).start()

    def _worker(self, prompt: str, extra_args: list) -> None:
        result = claude_bridge.ask(
            prompt,
            project_dir=self._plan.run_cwd,
            mcp_config_path=self._plan.config_arg_path,
            session_id=self._session_id,
            claude_cmd=self._plan.claude_cmd,
            extra_args=extra_args,
            on_status=lambda s: wx.CallAfter(self._on_activity, s),
            on_tool=lambda n: wx.CallAfter(self._on_tool, n),
            on_proc=lambda p: wx.CallAfter(self._on_proc, p),
        )
        # Refresh the board's refs/nets/layers so this reply can be linkified.
        # Capture (don't swallow) any failure so the real reason is VISIBLE —
        # the links silently breaking was undiagnosable before.
        try:
            from . import board_links
            _client, board = board_links.connect()
            refs, nets, layers = board_links.board_targets(board)
            result["_refs"], result["_nets"], result["_layers"] = (
                refs, nets, layers)
            result["_link_counts"] = (len(refs), len(nets), len(layers))
        except Exception as exc:
            # BoardUnavailable already carries a user-facing, actionable message
            # (multiple KiCad instances / no board) — show it verbatim; prefix
            # the type only for unexpected failures so they stay debuggable.
            result["_link_error"] = (
                str(exc) if type(exc).__name__ == "BoardUnavailable"
                else f"{type(exc).__name__}: {exc}")
        wx.CallAfter(self._on_reply, result)

    def _on_proc(self, proc) -> None:
        """The bridge handed us the live process — store it for the Stopp button."""
        self._proc = proc

    def _on_tool(self, name: str) -> None:
        """Append one streamed tool call to the transcript (dim ⚙ line)."""
        if self:
            self._write(f"  ⚙ {name}\n", theme.DIM)

    def _on_reply(self, result: dict) -> None:
        if not self:  # panel destroyed while Claude was thinking
            return
        self._proc = None
        if self._stopped:  # user pressed Stopp — show that, ignore the rest
            self._append("error", "⏹ Abgebrochen.")
            self._set_busy(False)
            self._in.SetFocus()
            return
        if result.get("_refs") is not None:
            self._refs = result["_refs"]
            self._nets = result.get("_nets") or set()
            self._layers = result.get("_layers") or set()
        # Make the link-data state VISIBLE so "no links" is diagnosable.
        if result.get("_link_error"):
            self._write("  ⓘ Links aus: " + result["_link_error"] + "\n",
                        theme.DIM)
        elif result.get("_link_counts") == (0, 0, 0):
            self._write("  ⓘ Links: 0 Refs/Netze/Layer vom Board gelesen "
                        "(Board leer oder kein Zugriff).\n", theme.DIM)
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

    def _select_worker(self, kind: str, value) -> None:
        from . import board_links
        try:
            client, board = board_links.connect()
            if kind == "coord":
                x, y = value
                dist = board_links.select_coord(client, board, x, y)
                msg = (f"({x}, {y}): nächstes Element {dist:.1f} mm entfernt "
                       "markiert" if dist is not None
                       else f"({x}, {y}): kein Element in der Nähe")
            elif kind == "layer":
                gui = board_links.set_active_layer(board, value)
                msg = (f"Aktiver Layer → {gui}" if gui
                       else f"{value}: Layer nicht auflösbar")
            elif kind == "pin":
                ref, pin = value
                n = board_links.select_pin(client, board, ref, pin)
                msg = (f"{ref}.{pin}: Pad markiert" if n
                       else f"{ref}.{pin}: Pad nicht gefunden")
            else:
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
