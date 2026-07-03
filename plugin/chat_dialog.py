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

import os
import shlex
import threading
import webbrowser

import wx  # KiCad ships wxPython; only importable inside KiCad

from . import banner
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
        # Path of the .kicad_pcb open in this pcbnew instance — used as the disk
        # fallback for linkification when live IPC can't resolve the board.
        self._pcb_path = self._discover_board_path()
        self._session_id = None
        self._busy = False
        self._proc = None       # live claude process of the running turn
        self._stopped = False   # set when the user pressed Stopp
        self._mono = _pick_mono_font()
        # Board elements named in replies become clickable: char-range → target.
        self._refs: set = set()
        self._nets: set = set()
        self._layers: set = set()
        self._pins: dict = {}  # ref -> {padnumber}, so pin links are verified
        self._links: list = []  # (start, end, kind, value)
        self._turn_changes: list = []  # board targets the agent changed this turn

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
        self._out.Bind(wx.EVT_RIGHT_UP, self._on_output_right_click)
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
        # P1 (Dok 1): include the live editor selection as context for the turn,
        # so "was ist das?" works without typing a reference.
        self._include_selection = wx.CheckBox(self, label="🔗 Auswahl einbeziehen")
        self._include_selection.SetForegroundColour(wx.Colour(theme.DIM))
        self._include_selection.SetFont(self._mono)
        opt.Add(self._include_selection, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        root.Add(opt, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Super-Feature roadmap bar — one button per entry in
        # plugin/superfeatures.py. SHIPPED buttons dispatch their canonical
        # prompt as a chat turn; "coming soon" buttons print their pitch on
        # click; hover shows the tooltip. Best-effort so a layout hiccup can
        # never break the panel.
        try:
            self._build_superfeature_bar(root)
        except Exception:
            pass

        foot = wx.BoxSizer(wx.HORIZONTAL)
        self._status = wx.StaticText(self, label=theme.STATUS_READY)
        self._status.SetForegroundColour(wx.Colour(theme.DIM))
        self._status.SetFont(self._mono)
        foot.Add(self._status, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        # Always-available safety net: undo the last board change (KiCad Ctrl+Z).
        # Right after an agent turn that is its last commit, so "take back what
        # Claude just did" is one click away — no need to find the PCB window.
        undo_btn = wx.Button(self, label="↶ Rückgängig")
        undo_btn.SetBackgroundColour(wx.Colour(theme.SURFACE))
        undo_btn.SetForegroundColour(wx.Colour(theme.DIM))
        undo_btn.SetToolTip("Letzte Board-Änderung rückgängig (KiCad Ctrl+Z)")
        undo_btn.Bind(wx.EVT_BUTTON, lambda e: threading.Thread(
            target=self._undo_worker, daemon=True).start())
        foot.Add(undo_btn, 0, wx.RIGHT, 6)
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

        self._render_startup()
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

    def _append_claude(self, text: str) -> int:
        """Claude reply with board references / nets / layers rendered as
        clickable links (orange + underlined); clicking selects+zooms an
        element or sets the active layer in the editor.

        Returns the number of clickable spans actually rendered in THIS reply,
        so the caller can tell "board data present but nothing in the text
        matched" (0 returned despite a non-empty board) apart from a genuine
        success — otherwise that case is silent and undiagnosable."""
        from . import board_links
        style = theme.style_for("claude")
        self._write(style["prefix"], style["prefix_color"], bold=True)
        rendered = 0
        marks: list = []
        for chunk, target in board_links.tokenize(text + "\n\n", self._refs,
                                                   self._nets, self._layers,
                                                   known_pins=self._pins):
            if target is None:
                self._write(chunk, style["text_color"])
                continue
            kind, value = target
            self._write_link(chunk, kind, value)
            rendered += 1
            if kind in ("ref", "net", "pin", "coord") and target not in marks:
                marks.append(target)
        # P4 (Dok 1): when a reply names several board elements, offer a single
        # click that selects+zooms ALL of them at once ("zeig's mir auf dem
        # Board") instead of clicking each link in turn.
        if len(marks) >= 2:
            self._write("  ", style["text_color"])
            self._write_link("📍 alle markieren", "markall", marks)
            self._write("\n\n", style["text_color"])
        return rendered

    def _write_link(self, chunk: str, kind: str, value) -> None:
        """Write one clickable span (orange + underlined) and record its
        char-range → target so a click resolves it. ``kind`` is a board target
        (``ref``/``net``/``layer``/``pin``/``coord``) or ``"url"`` (Dok 2:
        the recommend-mailto link, opened via the OS handler)."""
        start = self._out.GetLastPosition()
        self._write(chunk, theme.CLAUDE_ORANGE, underline=True)
        self._links.append((start, self._out.GetLastPosition(), kind, value))

    # -- startup banner (Dok 2) ---------------------------------------------

    def _render_startup(self) -> None:
        """Render the instant (no-Claude-turn) panel banner: version + board
        file, a clickable recommend-mailto, the interaction guide, then kick off
        the async board summary. Replaces the old one-line static banner."""
        board_name = os.path.basename(self._pcb_path) if self._pcb_path else None
        head = f"kicad-mcp  v{__version__}"
        head += f"  ·  verbunden mit {board_name}" if board_name else \
            "  ·  kein Board erkannt"
        self._write(head + "\n", theme.DIM)
        self._write("Gefällt dir das Plugin? → ", theme.FOREGROUND)
        self._write_link("Empfiehl es einem Freund ✉", "url",
                         banner.recommend_mailto())
        self._write("\n\n", theme.FOREGROUND)
        self._write(banner.interaction_guide() + "\n\n", theme.FOREGROUND)
        threading.Thread(target=self._summary_worker, daemon=True).start()

    def _summary_worker(self) -> None:
        """Load the board vocabulary off the GUI thread and build the summary.
        Sets refs/nets/layers so even the FIRST reply is linkable (previously
        only from the second). Degrades silently to no summary block."""
        from . import board_links
        refs: set = set()
        nets: set = set()
        layers: set = set()
        pins: dict = {}
        try:
            _client, board = board_links.connect()
            refs, nets, layers, pins = board_links.board_targets(board)
        except Exception:
            pass
        if not (refs or nets or layers) and self._pcb_path:
            refs, nets, layers, pins = board_links.board_targets_from_file(
                self._pcb_path)
        if not (refs or nets or layers):
            return
        summary = board_links.board_summary(refs, nets, layers)
        extent = (board_links.board_extent_mm_from_file(self._pcb_path)
                  if self._pcb_path else None)
        wx.CallAfter(self._on_summary, refs, nets, layers, pins, summary, extent)

    def _on_summary(self, refs, nets, layers, pins, summary, extent) -> None:
        if not self:
            return
        self._refs, self._nets, self._layers, self._pins = refs, nets, layers, pins
        for line in banner.summary_lines(summary, extent):
            self._write(line + "\n", theme.FOREGROUND)
        self._write("\n", theme.FOREGROUND)

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
        self._in.SetValue("")
        self._dispatch_prompt(prompt,
                              include_sel=self._include_selection.GetValue())

    def _dispatch_prompt(self, prompt: str, include_sel: bool = True) -> None:
        """Start one chat turn with ``prompt`` — the shared path for typed
        messages (``_on_send``) and Super-Feature buttons
        (``_on_superfeature``). ``include_sel`` prepends the current KiCad
        selection as context (the selection-aware contract of every
        super-feature)."""
        if self._busy:
            return
        try:
            extra_args = shlex.split(self._opts.GetValue().strip())
        except ValueError:  # unbalanced quotes in the options field
            self._append("error", "Optionen unlesbar (Anführungszeichen?).")
            return
        self._append("user", prompt)
        self._proc = None
        self._stopped = False
        self._turn_changes = []  # fresh receipt per turn
        self._set_busy(True)
        threading.Thread(
            target=self._worker, args=(prompt, extra_args, include_sel),
            daemon=True
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

    def _worker(self, prompt: str, extra_args: list,
                include_sel: bool = False) -> None:
        from . import board_links
        # P1 (Dok 1): prepend what the user has selected in the editor so
        # "this"/"the selected" resolves without typing a reference. Best-effort:
        # an empty/unreadable selection just adds nothing.
        if include_sel:
            try:
                _c, board = board_links.connect()
                ctx = board_links.selection_context(
                    board_links.get_selection(board))
                if ctx:
                    prompt = ctx + "\n\n" + prompt
            except Exception:
                pass
        result = claude_bridge.ask(
            prompt,
            project_dir=self._plan.run_cwd,
            mcp_config_path=self._plan.config_arg_path,
            session_id=self._session_id,
            claude_cmd=self._plan.claude_cmd,
            extra_args=extra_args,
            on_status=lambda s: wx.CallAfter(self._on_activity, s),
            on_tool=lambda n, i: wx.CallAfter(self._on_tool, n, i),
            on_proc=lambda p: wx.CallAfter(self._on_proc, p),
        )
        # Refresh the board's refs/nets/layers so this reply can be linkified.
        # Capture (don't swallow) any failure so the real reason is VISIBLE —
        # the links silently breaking was undiagnosable before.
        try:
            _client, board = board_links.connect()
            refs, nets, layers, pins = board_links.board_targets(board)
            result["_refs"], result["_nets"], result["_layers"] = (
                refs, nets, layers)
            result["_pins"] = pins
            result["_link_counts"] = (len(refs), len(nets), len(layers))
            result["_link_source"] = "live"
        except Exception as exc:
            # BoardUnavailable already carries a user-facing, actionable message
            # (multiple KiCad instances / no board) — show it verbatim; prefix
            # the type only for unexpected failures so they stay debuggable.
            result["_link_error"] = (
                str(exc) if type(exc).__name__ == "BoardUnavailable"
                else f"{type(exc).__name__}: {exc}")
        # Disk fallback: live IPC failed OR returned nothing, but the .kicad_pcb
        # the chat is about is on disk (the same file the MCP server reads). Parse
        # refs/nets/layers from it so links still RENDER even when the live API
        # can't resolve the board (the classic multi-instance case). Clicks still
        # need live IPC, but the answer stops looking link-dead.
        if not result.get("_refs") and self._pcb_path:
            refs, nets, layers, pins = board_links.board_targets_from_file(self._pcb_path)
            if refs or nets or layers:
                result["_refs"], result["_nets"], result["_layers"] = (
                    refs, nets, layers)
                result["_pins"] = pins
                result["_link_counts"] = (len(refs), len(nets), len(layers))
                result["_link_source"] = "disk"
                # Preserve WHY live IPC failed (kipy missing? API off?
                # multi-instance?) instead of discarding it — otherwise the
                # status line hides the real reason clicks are inactive behind a
                # bland "aus Datei".
                if result.get("_link_error"):
                    result["_link_live_error"] = result.pop("_link_error")
        wx.CallAfter(self._on_reply, result)

    def _discover_board_path(self) -> str:
        """Path of the .kicad_pcb open in this pcbnew instance, for the disk
        fallback. Lazy + guarded so this module stays importable headless (the
        pure-logic tests import it without pcbnew/wx). Falls back to the first
        ``.kicad_pcb`` in the run cwd when ``GetFileName()`` is empty (e.g. a
        never-saved board), so the link fallback still has a file to parse."""
        try:
            import pcbnew  # only importable inside KiCad
            board = pcbnew.GetBoard()
            path = board.GetFileName() if board else ""
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass
        try:
            import glob
            cwd = getattr(self._plan, "run_cwd", "") or ""
            hits = sorted(glob.glob(os.path.join(cwd, "*.kicad_pcb")))
            return hits[0] if hits else ""
        except Exception:
            return ""

    def _on_proc(self, proc) -> None:
        """The bridge handed us the live process — store it for the Stopp button."""
        self._proc = proc

    def _on_tool(self, name: str, tool_input: dict = None) -> None:
        """Append one streamed tool call to the transcript in board language
        (``⚙ 6× Via gesetzt`` instead of the raw ``add_vias_to_pcb`` slug) and
        collect what it changed for the end-of-turn "[zeigen]" receipt."""
        if not self:
            return
        from . import claude_bridge
        self._write(f"  ⚙ {claude_bridge.describe_tool(name, tool_input)}\n",
                    theme.DIM)
        for tgt in claude_bridge.changed_targets(name, tool_input):
            if tgt not in self._turn_changes:
                self._turn_changes.append(tgt)

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
            self._pins = result.get("_pins") or {}
        mcp_status = result.get("mcp_status") or ""
        if mcp_status.startswith("failed"):
            self._append(
                "error",
                "MCP nicht verbunden (" + mcp_status + ") — auch nach "
                "automatischem Neuversuch kein Board-Tool-Server. Meist hilft "
                "ein KiCad-Neustart (wärmt den Server), sonst Einrichtung → "
                "'Erneut prüfen'.",
            )
        if result.get("ok"):
            self._session_id = result.get("session_id") or self._session_id
            rendered = self._append_claude(result.get("text") or "(keine Antwort)")
            self._write_link_status(result, rendered)
            self._write_change_receipt()
        else:
            self._append("error", result.get("error") or "unbekannt")
        self._set_busy(False)
        self._in.SetFocus()

    def _write_change_receipt(self) -> None:
        """Glass-box receipt: after a turn that changed the board, list what was
        touched and offer a clickable "📍 zeigen" that selects it all in the
        editor (reuses the P4 ``markall`` link path). Silent when nothing
        changed (read-only turn)."""
        changes = self._turn_changes
        if not changes:
            return
        labels = []
        for kind, value in changes:
            if kind == "coord":
                labels.append(f"({value[0]}, {value[1]})")
            elif kind == "pin":
                labels.append(f"{value[0]}.{value[1]}")
            else:
                labels.append(str(value))
        # de-dupe labels while keeping order, cap the visible list
        seen, shown = set(), []
        for lb in labels:
            if lb not in seen:
                seen.add(lb)
                shown.append(lb)
        head = ", ".join(shown[:8]) + (" …" if len(shown) > 8 else "")
        self._write(f"  ✎ geändert: {head}  ", theme.DIM)
        self._write_link("📍 zeigen", "markall", list(changes))
        self._write("  ", theme.DIM)
        self._write_link("↶ zurück", "undo", None)
        self._write("\n", theme.DIM)

    def _write_link_status(self, result: dict, rendered: int) -> None:
        """One always-on dim line that makes the cross-probe link state a FACT,
        not a guess: did the board hand us refs/nets/layers, and did THIS reply
        actually linkify any? Every path prints exactly one line so "nichts ist
        orange" is never undiagnosable — the user can read the cause straight off
        (connection error vs. empty board vs. data present but 0 tokens matched
        vs. all good)."""
        if result.get("_link_error"):
            self._write("  ⓘ Links aus: " + result["_link_error"] + "\n",
                        theme.DIM)
            return
        counts = result.get("_link_counts")
        if counts is None:
            self._write("  ⓘ Links: Board-Status unbekannt (kein Board-Refresh "
                        "gelaufen).\n", theme.DIM)
            return
        r, n, ly = counts
        if counts == (0, 0, 0):
            self._write("  ⓘ Links: 0 Refs/Netze/Layer vom Board gelesen "
                        "(Board leer oder kein Zugriff).\n", theme.DIM)
            return
        # Whether the data came from the live editor (clicks work) or the disk
        # fallback (links render, clicks need live IPC) — say so, since a disk
        # source means the live API couldn't resolve the board.
        src = result.get("_link_source")
        if src == "disk":
            live = result.get("_link_live_error") or "nicht verfügbar"
            origin = f"aus Datei — Klick inaktiv (Live-IPC: {live})"
        else:
            origin = "vom Board"
        if rendered == 0:
            self._write(
                f"  ⓘ Links: {r} Refs / {n} Netze / {ly} Layer {origin} "
                "gelesen, aber 0 im Antworttext erkannt (Token-Format?).\n",
                theme.DIM)
            return
        self._write(
            f"  ⓘ Links: {rendered} im Reply · {r} Refs / {n} Netze / "
            f"{ly} Layer {origin}.\n", theme.DIM)

    # -- board cross-probe (clickable elements) -----------------------------

    def _hit_target(self, evt):
        """The (kind, value) link under the mouse, or None. Shared by left- and
        right-click."""
        if not self._links:
            return None
        hit = self._out.HitTestPos(evt.GetPosition())
        # wx returns (HitTestResult, pos); pos is the char index.
        pos = hit[1] if isinstance(hit, (tuple, list)) else hit
        return next(((k, v) for s, e, k, v in self._links if s <= pos < e), None)

    def _on_output_click(self, evt) -> None:
        evt.Skip()  # let the control handle caret/selection as usual
        target = self._hit_target(evt)
        if target is None:
            return
        kind, value = target
        if kind == "url":  # Dok 2: recommend-mailto → OS handler
            threading.Thread(target=self._open_url, args=(value,),
                             daemon=True).start()
            return
        if kind == "markall":  # P4: select every named element at once
            threading.Thread(target=self._mark_all_worker, args=(value,),
                             daemon=True).start()
            return
        if kind == "undo":  # change receipt: take back the agent's last commit
            threading.Thread(target=self._undo_worker, daemon=True).start()
            return
        # P5 (Dok 1): Ctrl/⌘-click accumulates selection instead of replacing it.
        add = bool(evt.CmdDown() or evt.ControlDown())
        self._dispatch(kind, value, "select", add=add)

    def _on_output_right_click(self, evt) -> None:
        """P2 (Dok 1): per-link actions — markieren / hinzoomen / Eigenschaften —
        instead of one fixed click action."""
        target = self._hit_target(evt)
        if target is None or target[0] == "url":
            evt.Skip()
            return
        kind, value = target
        menu = wx.Menu()
        entries = [(menu.Append(wx.ID_ANY, "Nur markieren"), "highlight"),
                   (menu.Append(wx.ID_ANY, "Hinzoomen"), "zoom")]
        if kind in ("ref", "pin"):
            entries.append((menu.Append(wx.ID_ANY, "Eigenschaften"), "inspect"))
        for item, action in entries:
            self.Bind(wx.EVT_MENU,
                      lambda _e, k=kind, v=value, a=action: self._dispatch(k, v, a),
                      item)
        self._out.PopupMenu(menu)
        menu.Destroy()

    def _dispatch(self, kind, value, action: str, add: bool = False) -> None:
        threading.Thread(target=self._select_worker, args=(kind, value),
                         kwargs={"action": action, "add": add},
                         daemon=True).start()

    def _open_url(self, href: str) -> None:
        try:
            webbrowser.open(href)
            msg = "Mail-Vorlage geöffnet"
        except Exception as exc:
            msg = f"Link konnte nicht geöffnet werden: {exc}"
        wx.CallAfter(self._flash_status, msg)

    def _select_worker(self, kind: str, value, action: str = "select",
                       add: bool = False) -> None:
        from . import board_links
        zoom = action in ("select", "zoom")
        try:
            client, board = board_links.connect()
            if action == "inspect":
                msg = self._inspect_msg(board, kind, value)
            elif kind == "coord":
                x, y = value
                dist = board_links.select_coord(client, board, x, y, zoom=zoom)
                msg = (f"({x}, {y}): nächstes Element {dist:.1f} mm entfernt "
                       "markiert" if dist is not None
                       else f"({x}, {y}): kein Element in der Nähe")
            elif kind == "layer":
                gui = board_links.set_active_layer(board, value)
                msg = (f"Aktiver Layer → {gui}" if gui
                       else f"{value}: Layer nicht auflösbar")
            elif kind == "pin":
                ref, pin = value
                n = board_links.select_pin(client, board, ref, pin, zoom=zoom,
                                           add=add)
                msg = (f"{ref}.{pin}: Pad markiert" if n
                       else f"{ref}.{pin}: Pad nicht gefunden")
                if n and action == "select":  # P3: enrich with connectivity
                    msg = self._augment_inspect(board, ref, msg)
            else:
                count = board_links.select(client, board, kind, value,
                                           zoom=zoom, add=add)
                msg = (f"{value}: {count} Element(e) markiert"
                       if count else f"{value}: nichts gefunden")
                if count and action == "select" and kind == "ref":
                    msg = self._augment_inspect(board, value, msg)
        except Exception as exc:
            msg = f"Auswahl fehlgeschlagen: {exc}"
        wx.CallAfter(self._flash_status, msg)

    def _undo_worker(self) -> None:
        """Trigger KiCad's native undo in the running editor (the change-receipt
        "↶ zurück" and the footer button). One undo pops the agent's last commit
        right after a turn. Off the GUI thread; status-flashes the outcome."""
        from . import board_links
        try:
            client, _board = board_links.connect()
            ok = board_links.undo(client)
            msg = ("↶ Rückgängig gemacht" if ok
                   else "Rückgängig nicht möglich (Editor?)")
        except Exception as exc:
            msg = f"Rückgängig fehlgeschlagen: {exc}"
        wx.CallAfter(self._flash_status, msg)

    def _build_superfeature_bar(self, root) -> None:
        """Render the Super-Feature roadmap as a wrapping button row, driven by
        ``plugin/superfeatures.py``. Every entry gets a button; ``SOON`` ones are
        dimmed and print their pitch on click, ``SHIPPED`` ones will dispatch to
        a live handler (wired in ``_on_superfeature``)."""
        from . import superfeatures
        bar = wx.WrapSizer(wx.HORIZONTAL)
        tag = wx.StaticText(self, label="✨ Super-Features")
        tag.SetForegroundColour(wx.Colour(theme.CLAUDE_ORANGE))
        tag.SetFont(self._mono)
        bar.Add(tag, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 6)
        for feat in superfeatures.all_features():
            btn = wx.Button(self, label=feat.label, style=wx.BU_EXACTFIT)
            btn.SetBackgroundColour(wx.Colour(theme.SURFACE))
            soon = feat.status == superfeatures.SOON
            btn.SetForegroundColour(
                wx.Colour(theme.DIM if soon else theme.FOREGROUND))
            btn.SetToolTip(("🔜 Kommt bald — " if soon else "") + feat.tooltip)
            btn.Bind(wx.EVT_BUTTON, lambda _e, f=feat: self._on_superfeature(f))
            bar.Add(btn, 0, wx.ALL, 2)
        root.Add(bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

    def _on_superfeature(self, feat) -> None:
        """Click on a Super-Feature button. SHIPPED features dispatch their
        canonical ``feat.prompt`` as a real chat turn (selection prepended, so
        the feature scopes to what's marked in the editor). SOON features
        print their "coming soon" pitch into the transcript."""
        from . import superfeatures
        if feat.status == superfeatures.SHIPPED and getattr(feat, "prompt", ""):
            if self._busy:  # _flash_status is a no-op while busy → _set_status
                self._set_status("⏳ Es läuft noch ein Zug — danach nochmal "
                                 "klicken.", theme.CLAUDE_ORANGE)
                return
            self._write(f"\n✨ {feat.name}\n", theme.CLAUDE_ORANGE, bold=True)
            self._dispatch_prompt(feat.prompt, include_sel=True)
            return
        self._write(f"\n✨ {feat.name}", theme.CLAUDE_ORANGE, bold=True)
        self._write("   🔜 kommt bald\n", theme.DIM)
        self._write(f"  {feat.tooltip}\n", theme.DIM)
        self._write(f"  Warum KiCad das nicht kann: {feat.moat}\n", theme.DIM)
        if getattr(feat, "selection_aware", False):
            self._write("  Wirkt aufs ganze Board oder auf deine aktuelle "
                        "Auswahl.\n", theme.DIM)

    def _mark_all_worker(self, marks: list) -> None:
        """P4 (Dok 1): select every named board element together (first replaces
        the selection, the rest accumulate), then zoom to fit the group."""
        from . import board_links
        total = 0
        try:
            client, board = board_links.connect()
            for i, (kind, value) in enumerate(marks):
                add = i > 0  # first clears, rest accumulate
                if kind == "pin":
                    ref, pin = value
                    total += board_links.select_pin(client, board, ref, pin,
                                                     zoom=False, add=add)
                elif kind == "coord":
                    x, y = value
                    if board_links.select_coord(client, board, x, y,
                                                 zoom=False, add=add) is not None:
                        total += 1
                else:
                    total += board_links.select(client, board, kind, value,
                                                 zoom=False, add=add)
            board_links._zoom_to_selection(client)  # one fit-zoom at the end
            msg = f"{total} Element(e) markiert"
        except Exception as exc:
            msg = f"Markieren fehlgeschlagen: {exc}"
        wx.CallAfter(self._flash_status, msg)

    def _augment_inspect(self, board, ref: str, base_msg: str) -> str:
        """P3 (Dok 1): append a compact "what is this wired to" summary to the
        status line after a normal select. Best-effort — never overrides the
        primary message on failure."""
        from . import board_links
        try:
            pad_nets = board_links.inspect_ref(board, ref)
            if pad_nets is not None:
                return base_msg + " · " + board_links.inspect_summary(
                    ref, pad_nets)
        except Exception:
            pass
        return base_msg

    def _inspect_msg(self, board, kind: str, value) -> str:
        """The status text for the right-click "Eigenschaften" action."""
        from . import board_links
        if kind not in ("ref", "pin"):
            return f"{value}: keine Eigenschaften verfügbar"
        ref = value[0] if kind == "pin" else value
        pad_nets = board_links.inspect_ref(board, ref)
        if pad_nets is None:
            return f"{ref}: nicht gefunden"
        return board_links.inspect_summary(ref, pad_nets)

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
