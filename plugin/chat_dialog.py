# SPDX-License-Identifier: GPL-3.0-or-later
"""The KiCad-side chat panel: a small wx dialog that is the input/output for
talking to Claude about the open board.

Each "Send" runs one Claude Code turn in a worker thread (so the GUI stays
responsive) via :mod:`claude_bridge`, and appends the reply. The Claude session
id is kept on the dialog so the whole exchange is one conversation.
"""

from __future__ import annotations

import threading

import wx  # KiCad ships wxPython; only importable inside KiCad

from . import claude_bridge
from .version import __version__


class ClaudeChatDialog(wx.Dialog):
    def __init__(self, parent, plan, on_open_setup=None):
        super().__init__(
            parent, title=f"Claude — KiCad (v{__version__})", size=(640, 560),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        # The RunPlan carries the path-consistent cwd / --mcp-config / claude
        # argv for this machine (native Windows, native Linux, or WSL-bridge).
        self._plan = plan
        self._on_open_setup = on_open_setup  # reopen Einrichtung/Update panel
        self._session_id = None
        self._busy = False

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        self._out = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2
        )
        root.Add(self._out, 1, wx.EXPAND | wx.ALL, 6)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self._in = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self._in.SetHint("Frag Claude etwas über dieses Board …")
        self._in.Bind(wx.EVT_TEXT_ENTER, self._on_send)
        row.Add(self._in, 1, wx.EXPAND | wx.RIGHT, 6)
        self._send = wx.Button(panel, label="Senden")
        self._send.Bind(wx.EVT_BUTTON, self._on_send)
        row.Add(self._send, 0)
        root.Add(row, 0, wx.EXPAND | wx.ALL, 6)

        foot = wx.BoxSizer(wx.HORIZONTAL)
        self._status = wx.StaticText(panel, label="Bereit.")
        foot.Add(self._status, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        if self._on_open_setup:
            setup_btn = wx.Button(panel, label="Einrichtung / Update")
            setup_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_open_setup())
            foot.Add(setup_btn, 0, wx.RIGHT, 6)
        root.Add(foot, 0, wx.EXPAND | wx.BOTTOM, 8)

        panel.SetSizer(root)
        self._append(
            "Claude",
            "Hallo! Ich bin über kicad-mcp mit deinem offenen Board verbunden. "
            "Frag mich z.B. 'wie viele GND-Vias gibt es?' oder 'markier die 3 "
            "kleinsten'.",
        )
        self._in.SetFocus()

    # -- ui helpers ---------------------------------------------------------

    def _append(self, who: str, text: str) -> None:
        self._out.AppendText(f"{who}: {text}\n\n")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._send.Enable(not busy)
        self._in.Enable(not busy)
        self._status.SetLabel("Claude denkt nach …" if busy else "Bereit.")

    # -- send flow ----------------------------------------------------------

    def _on_send(self, _evt) -> None:
        if self._busy:
            return
        prompt = self._in.GetValue().strip()
        if not prompt:
            return
        self._in.SetValue("")
        self._append("Du", prompt)
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
        )
        wx.CallAfter(self._on_reply, result)

    def _on_reply(self, result: dict) -> None:
        if result.get("ok"):
            self._session_id = result.get("session_id") or self._session_id
            self._append("Claude", result.get("text") or "(keine Antwort)")
        else:
            self._append("Fehler", result.get("error") or "unbekannt")
        self._set_busy(False)
        self._in.SetFocus()
