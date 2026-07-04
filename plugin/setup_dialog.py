# SPDX-License-Identifier: GPL-3.0-or-later
"""The onboarding / preflight panel: a green-red checklist with a one-click fix
per failing row, then a "Chat starten" button. Opened on the first click (or
whenever something isn't ready); once everything is green the action goes
straight to the chat.
"""

from __future__ import annotations

import os

import wx  # KiCad ships wxPython

from . import deps, env_resolve, installer, ipc_setup, mcp_config, preflight, \
    runtime_env, server_probe, terminal, updater
from . import settings as plugin_settings
from .i18n import tr
from .version import __version__

_ICON = {preflight.OK: ("✓", wx.Colour(0, 140, 0)),
         preflight.WARN: ("⚠", wx.Colour(200, 130, 0)),
         preflight.FAIL: ("✗", wx.Colour(200, 0, 0))}
_FIX_LABEL = {"install_claude": "Installieren", "login": "Anmelden",
              "env_help": "Hilfe", "enable_ipc": "Aktivieren",
              "install_deps": "Installieren"}


class SetupDialog(wx.Dialog):
    def __init__(self, parent, project_dir, mcp_root, mcp_config_path,
                 board_open, board_name, on_start_chat,
                 common_path=None, ipc_restart_hint=False):
        super().__init__(parent,
                         title=f"Claude für KiCad — Einrichtung (v{__version__})",
                         size=(560, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._project_dir = project_dir
        self._mcp_root = mcp_root
        self._mcp_config_path = mcp_config_path
        self._board_open = board_open
        self._board_name = board_name
        self._common_path = common_path
        self._ipc_restart_hint = ipc_restart_hint
        self._on_start_chat = on_start_chat

        self._panel = wx.Panel(self)
        self._root = wx.BoxSizer(wx.VERTICAL)
        self._panel.SetSizer(self._root)

        self._rows = wx.BoxSizer(wx.VERTICAL)
        self._root.Add(self._rows, 1, wx.EXPAND | wx.ALL, 10)

        # WrapSizer statt BoxSizer: die 7 Knöpfe passen bei den breiteren
        # GTK-Button-Metriken NICHT in die fixe Fensterbreite (Linux-Befund
        # 1. Smoke) — auf schmalen Fenstern bricht die Leiste jetzt sauber um,
        # statt die letzten Knöpfe (Systemtest/Chat starten) abzuschneiden.
        bar = wx.WrapSizer(wx.HORIZONTAL)
        recheck = wx.Button(self._panel, label=tr("Erneut prüfen"))
        recheck.Bind(wx.EVT_BUTTON, lambda e: self._guarded(self._render))
        bar.Add(recheck, 0, wx.RIGHT, 8)
        update = wx.Button(self._panel, label=tr("Update prüfen"))
        update.Bind(wx.EVT_BUTTON, lambda e: self._guarded(self._check_update))
        bar.Add(update, 0, wx.RIGHT, 8)
        diag = wx.Button(self._panel, label=tr("Diagnose"))
        diag.Bind(wx.EVT_BUTTON, lambda e: self._guarded(self._show_diagnose))
        bar.Add(diag, 0, wx.RIGHT, 8)
        settings_btn = wx.Button(self._panel, label=tr("Einstellungen"))
        settings_btn.Bind(wx.EVT_BUTTON,
                          lambda e: self._guarded(self._show_settings))
        bar.Add(settings_btn, 0, wx.RIGHT, 8)
        e2e_btn = wx.Button(self._panel, label=tr("🧪 E2E-Test"))
        e2e_btn.SetToolTip(tr(
            "Alle Super-Features automatisch gegen das offene Board testen "
            "(ohne Board-Änderung) und einen Report schreiben — dauert je "
            "nach Board 15-45 Minuten."))
        e2e_btn.Bind(wx.EVT_BUTTON, lambda e: self._guarded(self._run_e2e))
        bar.Add(e2e_btn, 0, wx.RIGHT, 8)
        st_btn = wx.Button(self._panel, label=tr("🔬 Systemtest"))
        st_btn.SetToolTip(tr(
            "Prüft die Maschinerie OHNE Claude (kein Kontingent): erzeugt "
            "ein Demo-Board aus der eingebauten Vorlage und testet Server, "
            "Generatoren und Werkzeuge lokal — dauert ~1 Minute."))
        st_btn.Bind(wx.EVT_BUTTON, lambda e: self._guarded(self._run_selftest))
        bar.Add(st_btn, 0, wx.RIGHT | wx.BOTTOM, 8)
        self._start = wx.Button(self._panel, label=tr("Chat starten"))
        self._start.Bind(wx.EVT_BUTTON, self._on_start)
        bar.Add(self._start, 0, wx.BOTTOM, 8)
        self._root.Add(bar, 0, wx.EXPAND | wx.ALL, 10)

        self._render()
        # Fenster auf seinen Inhalt wachsen lassen: die fixe (560,440)-Vorgabe
        # war für Windows-Buttonbreiten gedacht; unter GTK/anderen Themes ist
        # der Mindestbedarf größer. Fit + Mindestgröße sichern alle Plattformen.
        self._panel.Layout()
        self.SetMinSize(wx.Size(560, 420))
        self.Fit()

    def _guarded(self, fn) -> None:
        """Button-Handler-Schutz: wx schluckt Handler-Exceptions (Traceback
        geht nach stderr — in KiCad unsichtbar), der Klick wirkt dann 'tot'.
        Genau so blieb der E2E-Button-Absturz (Feld-Report) unsichtbar.
        Stattdessen den Fehler als kopierbaren Dialog zeigen."""
        try:
            fn()
        except Exception:
            import traceback
            wx.MessageBox(traceback.format_exc(),
                          "Plugin-Fehler (bitte melden)",
                          wx.OK | wx.ICON_ERROR)

    # -- rendering ----------------------------------------------------------

    def _render(self) -> None:
        self._rows.Clear(delete_windows=True)
        checks = preflight.run_preflight(
            self._mcp_root, self._project_dir, self._board_open,
            self._board_name, common_path=self._common_path,
            ipc_restart_hint=self._ipc_restart_hint)
        for c in checks:
            self._rows.Add(self._make_row(c), 0, wx.EXPAND | wx.BOTTOM, 8)
        self._start.Enable(preflight.hard_ok(checks))
        self._panel.Layout()

    def _make_row(self, check) -> wx.Sizer:
        row = wx.BoxSizer(wx.HORIZONTAL)
        glyph, colour = _ICON.get(check.status, ("?", wx.BLACK))
        icon = wx.StaticText(self._panel, label=glyph)
        icon.SetForegroundColour(colour)
        f = icon.GetFont(); f.SetPointSize(f.GetPointSize() + 3); icon.SetFont(f)
        row.Add(icon, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        texts = wx.BoxSizer(wx.VERTICAL)
        lbl = wx.StaticText(self._panel, label=check.label)
        lf = lbl.GetFont(); lf.SetWeight(wx.FONTWEIGHT_BOLD); lbl.SetFont(lf)
        texts.Add(lbl, 0)
        if check.detail:
            det = wx.StaticText(self._panel, label=check.detail)
            det.SetForegroundColour(wx.Colour(90, 90, 90))
            texts.Add(det, 0)
        row.Add(texts, 1, wx.ALIGN_CENTER_VERTICAL)

        if check.fix:
            btn = wx.Button(self._panel, label=_FIX_LABEL.get(check.fix, "Beheben"))
            btn.Bind(wx.EVT_BUTTON, lambda e, fx=check.fix: self._run_fix(fx))
            row.Add(btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        return row

    # -- fix actions --------------------------------------------------------

    def _run_fix(self, fix: str) -> None:
        if fix == "install_claude":
            self._install_claude()
        elif fix == "env_help":
            wx.MessageBox(
                "Setze diese Umgebungsvariablen (z. B. in start_mcp.bat / der "
                ".mcp.json):\n\n"
                "KICAD_PYTHON_PATH = <KiCad>\\bin\\python.exe (mit kipy)\n"
                "KICAD_MCP_ROOT    = Ordner des kicad-mcp (enthält kicad_mcp\\)",
                "Einrichtung", wx.OK | wx.ICON_INFORMATION)
        elif fix == "login":
            self._open_login_terminal()
        elif fix == "enable_ipc":
            self._enable_ipc()
        elif fix == "install_deps":
            self._install_deps()

    def _run_e2e(self) -> None:
        """Der Loop durchs Produkt: jedes Super-Feature einmal als echter
        Chat-Zug (Testmodus: keine Mutation), Live-Fortschritt im Fenster,
        Report nach <Projekt>/.kicad-mcp/e2e_report.md — den Report dem
        Entwicklungs-Agenten zurückgeben, der verbessert daraus die Prompts."""
        import threading
        from datetime import date

        # NUR relative Imports: das installierte Paket heißt claude_kicad,
        # nicht "plugin" — ein absoluter Import stirbt im Feld VOR dem
        # Bestätigungsdialog (toter Button, Feld-Report 0.8.3).
        from . import e2e_runner, i18n, superfeatures

        n = sum(1 for f in superfeatures.all_features()
                if f.status == superfeatures.SHIPPED)
        if wx.MessageBox(
                tr("Alle {n} Super-Features werden nacheinander als echte "
                   "Claude-Züge gegen das offene Board getestet — OHNE "
                   "Board-Änderung (Testmodus stoppt vor jedem Go). Das "
                   "dauert typischerweise 15-45 Minuten und verbraucht "
                   "entsprechend Claude-Kontingent. Starten?").replace(
                       "{n}", str(n)),
                tr("🧪 E2E-Test"), wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return
        plan = runtime_env.resolve(self._project_dir, self._mcp_root,
                                   self._mcp_config_path)
        if plan is None:
            wx.MessageBox("Claude/KiCad-Python nicht auflösbar — erst die "
                          "Checkliste grün machen.", "🧪", wx.OK)
            return

        dlg = wx.Dialog(self, title=tr("🧪 E2E-Test läuft …"),
                        size=(640, 420),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        panel = wx.Panel(dlg)
        out = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        close = wx.Button(panel, label=tr("Schließen"))
        close.Enable(False)
        close.Bind(wx.EVT_BUTTON, lambda e: dlg.EndModal(wx.ID_OK))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(out, 1, wx.EXPAND | wx.ALL, 8)
        sizer.Add(close, 0, wx.ALL, 8)
        panel.SetSizer(sizer)

        def emit(line: str) -> None:
            wx.CallAfter(out.AppendText, line + "\n")

        def worker() -> None:
            try:
                results = e2e_runner.run_all(plan, on_line=emit)
                meta = {"date": date.today().isoformat(),
                        "board": self._board_name or "",
                        "transport": os.environ.get(
                            "KICAD_MCP_TRANSPORT", "stdio"),
                        "language": i18n.get_lang()}
                md, js = e2e_runner.write_report(self._project_dir,
                                                 results, meta)
                emit("")
                emit(tr("Report geschrieben:"))
                emit("  " + md)
                emit("  " + js)
                emit(tr("→ Diese Datei dem Entwicklungs-Agenten geben — er "
                        "liest sie zurück und verbessert die Prompts."))
            except Exception as exc:
                emit(f"FEHLER: {type(exc).__name__}: {exc}")
            wx.CallAfter(close.Enable, True)

        threading.Thread(target=worker, daemon=True).start()
        dlg.ShowModal()
        dlg.Destroy()

    def _run_selftest(self) -> None:
        """Standalone-Systemtest (kicad_mcp.selftest) unter KiCads Python:
        Demo-Board aus der JSON-Vorlage, echte Tools, kein Claude, kein
        Kontingent. Live-Ausgabe im Fenster; grün endet in einer Zeile."""
        import subprocess
        import threading

        from . import deps as plugin_deps, server_manager
        from .claude_bridge import hidden_console_kwargs

        py = mcp_config.find_kicad_python()
        if not py:
            wx.MessageBox("KiCad-Python nicht gefunden — erst die "
                          "Checkliste grün machen.", "🔬", wx.OK)
            return
        out_dir = os.path.join(server_manager.state_dir(), "selftest")
        cmd = [py, "-c",
               mcp_config.selftest_bootstrap_code(
                   self._mcp_root, plugin_deps.active_deps_dir()),
               "--out", out_dir, "--verbose"]

        dlg = wx.Dialog(self, title=tr("🔬 Systemtest läuft …"),
                        size=(640, 420),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        panel = wx.Panel(dlg)
        out = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        close = wx.Button(panel, label=tr("Schließen"))
        close.Enable(False)
        close.Bind(wx.EVT_BUTTON, lambda e: dlg.EndModal(wx.ID_OK))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(out, 1, wx.EXPAND | wx.ALL, 8)
        sizer.Add(close, 0, wx.ALL, 8)
        panel.SetSizer(sizer)

        def emit(line: str) -> None:
            wx.CallAfter(out.AppendText, line + "\n")

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, **hidden_console_kwargs())
                for line in proc.stdout:
                    emit(line.rstrip())
                rc = proc.wait()
                emit("")
                emit(tr("✅ Alles grün.") if rc == 0
                     else tr("❌ Es gibt rote Schritte — Report ansehen:"))
                emit("  " + os.path.join(out_dir, "selftest_report.md"))
            except Exception as exc:
                emit(f"FEHLER: {type(exc).__name__}: {exc}")
            wx.CallAfter(close.Enable, True)

        threading.Thread(target=worker, daemon=True).start()
        dlg.ShowModal()
        dlg.Destroy()

    def _show_settings(self) -> None:
        """Einstellungen (Sprache, Transport, ngspice, Max-Schritte) —
        GUI-gepflegt statt Env-Handarbeit; gespeichert in settings.json und
        beim nächsten Chat-Zug wirksam (settings.apply_env beim Panel-Start).
        Hand-gesetzte Env-Variablen behalten Vorrang."""
        values = plugin_settings.load()
        dlg = wx.Dialog(self, title=tr("Einstellungen"), size=(460, 300),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        panel = wx.Panel(dlg)
        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=8)
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(panel, label=tr("Sprache:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        lang = wx.Choice(panel, choices=[tr("Automatisch"), "Deutsch",
                                         "English"])
        lang.SetSelection({"auto": 0, "de": 1, "en": 2}.get(
            values.get("language", "auto"), 0))
        grid.Add(lang, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label=tr("Transport:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        transport = wx.Choice(panel, choices=[
            tr("stdio (Server pro Nachricht)"),
            tr("Warm-Server (http, empfohlen nach Validierung)")])
        transport.SetSelection(1 if values.get("transport") == "http" else 0)
        grid.Add(transport, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label=tr("ngspice-Pfad:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        ngspice = wx.TextCtrl(panel, value=values.get("ngspice_path", ""))
        ngspice.SetHint(tr("(leer = automatisch suchen)"))
        grid.Add(ngspice, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel,
                               label=tr("Max. Schritte pro Nachricht:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        turns = wx.SpinCtrl(panel, min=0, max=500,
                            initial=int(values.get("max_turns") or 0))
        grid.Add(turns, 1, wx.EXPAND)

        note = wx.StaticText(panel, label="")
        save = wx.Button(panel, label=tr("Einstellungen speichern"))

        def _save(_evt):
            plugin_settings.save({
                "language": {0: "auto", 1: "de", 2: "en"}[lang.GetSelection()],
                "transport": "http" if transport.GetSelection() == 1 else "",
                "ngspice_path": ngspice.GetValue().strip(),
                "max_turns": int(turns.GetValue()),
            })
            note.SetLabel(tr("Gespeichert — gilt ab dem nächsten Chat-Zug."))
            panel.Layout()

        save.Bind(wx.EVT_BUTTON, _save)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(grid, 1, wx.EXPAND | wx.ALL, 12)
        outer.Add(save, 0, wx.LEFT | wx.RIGHT, 12)
        outer.Add(note, 0, wx.ALL, 12)
        panel.SetSizer(outer)
        dlg.ShowModal()
        dlg.Destroy()

    def _show_diagnose(self) -> None:
        """Collect the full diagnosis report (runs the server probe — can take
        a minute on a cold start) and show it copyable + save it to a file."""
        import tempfile

        from . import diagnose
        busy = wx.BusyCursor()
        try:
            report = diagnose.collect(self._mcp_root, self._project_dir)
        finally:
            del busy
        path = os.path.join(tempfile.gettempdir(), "kicad_claude_diagnose.txt")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(report)
            note = f"Auch gespeichert unter: {path}"
        except Exception as exc:
            note = f"(Datei nicht gespeichert: {exc})"

        dlg = wx.Dialog(self, title="Diagnose — alles kopieren & senden",
                        size=(720, 540),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        txt = wx.TextCtrl(panel, value=report,
                          style=wx.TE_MULTILINE | wx.TE_READONLY)
        txt.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL,
                            wx.FONTWEIGHT_NORMAL))
        sizer.Add(txt, 1, wx.EXPAND | wx.ALL, 8)
        foot = wx.BoxSizer(wx.HORIZONTAL)
        copy_btn = wx.Button(panel, label="Alles kopieren")

        def _copy(_evt):
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(report))
                wx.TheClipboard.Close()

        copy_btn.Bind(wx.EVT_BUTTON, _copy)
        foot.Add(copy_btn, 0, wx.RIGHT, 8)
        foot.Add(wx.StaticText(panel, label=note), 1,
                 wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(foot, 0, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(sizer)
        dlg.ShowModal()
        dlg.Destroy()

    def _install_deps(self) -> None:
        py = mcp_config.find_kicad_python()
        if not py:
            wx.MessageBox("KiCad-Python (mit kipy) nicht gefunden.",
                          "Abhängigkeiten", wx.OK | wx.ICON_WARNING)
            return
        final = deps.default_target_dir()
        staging = deps.staging_target_dir(final)
        # Start from an EMPTY staging dir: pip --target won't downgrade a
        # newer kipy already sitting there (from an interrupted run), so a
        # leftover _deps.new would defeat the very downgrade we're forcing.
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        # Resolve kicad-python (kipy) to the version COUPLED to the running
        # KiCad (KiCad 10 → 0.7.1). A "latest" kipy speaks a newer IPC protocol
        # than the GUI → silent handshake death ("nichts orange"). Defensive:
        # any resolver hiccup falls back to today's unpinned specs so the
        # coupling layer can NEVER break the install itself.
        import sys
        try:
            kv = env_resolve.detect_kicad_version(kicad_py_path=py)
            # search_paths=sys.path enables the KiCad-bundled (3rdparty) fallback
            # for an unknown KiCad major + the pollution guard for a known one.
            plan = env_resolve.plan_kipy_pin(kv, sys.path)
            specs = env_resolve.resolve_pip_specs(kv, deps.PIP_SPECS,
                                                  search_paths=sys.path)
        except Exception:
            kv, plan, specs = None, None, deps.PIP_SPECS
        # Stage into _deps.new (NOT the live _deps): install + verify happen in
        # the staging dir, then an atomic swap promotes it. A failed install
        # therefore leaves the working _deps intact — no brick (see _finalize).
        # Run pip DIRECTLY (argv list, no cmd/batch): a cmd.exe/batch round-trip
        # mangles a non-ASCII --target path (e.g. C:\Users\üser) to "?" →
        # WinError 123. CreateProcessW passes the unicode argv intact. Output
        # streams live into a dialog so the long install stays visible.
        steps = [("Installiere MCP-Abhängigkeiten (gekoppelt an KiCad) …",
                  deps.pip_install_argv(py, staging, specs=specs)),
                 ("Prüfe Importe …", deps.verify_import_argv(py, staging))]
        self._run_streamed_install(
            "MCP-Abhängigkeiten installieren", staging, steps,
            finalize=lambda emit: self._finalize_deps_install(
                emit, py, staging, final, kv, specs, plan))

    def _finalize_deps_install(self, emit, py, staging, final, kv, specs,
                               plan=None) -> bool:
        """Runs in the worker thread AFTER install+verify succeeded in the
        staging dir. Atomic-swaps it over the live _deps, writes the env
        fingerprint, confirms the coupling, and runs the MCP handshake
        self-check. Returns True only when everything is green; a mismatch
        emits a LOUD, actionable line rather than failing silently."""
        emit("$ Aktiviere neue Abhängigkeiten (atomarer Swap) …\n")
        swap = env_resolve.atomic_swap_dir(staging, final)
        if not swap["ok"]:
            emit(f"[Fehler] {swap['error']}\n"
                 "Das bisherige _deps bleibt unverändert (kein Brick).\n")
            return False
        fp = env_resolve.build_fingerprint(kv, specs, plugin_version=__version__)
        env_resolve.write_fingerprint(final, fp)
        src = f" [{plan['source']}]" if plan else ""
        emit(f"Gekoppelt an KiCad {fp['kicad_version'] or '?'} → kipy "
             f"{fp['kipy'] or '(unpinned/latest)'}{src}\n")
        # Loud notice when the pin came from the mutable 3rdparty fallback or
        # the KiCad major is unknown, or a pollution mismatch was detected.
        if plan and plan.get("warning"):
            emit(f"\n  ⚠  {plan['warning']}\n")
        # Coupling proof: is the kipy now in _deps the coupled one?
        dec = env_resolve.downgrade_decision(kv, final)
        coupled_ok = not dec["mismatch"]
        if dec["mismatch"]:
            emit("\n  ⚠  KIPY-KOPPLUNG NICHT ERREICHT — installiert: "
                 f"{dec['installed'] or '?'}, erwartet: {dec['target'] or '?'} "
                 f"(Aktion: {dec['action']}).\n"
                 "     Bitte diese Meldung im Plugin per 'Diagnose' melden.\n")
        # Handshake self-check: does the MCP server actually start for Claude?
        emit("\n$ Handshake-Selbstcheck (startet der MCP-Server für Claude?) …\n")
        probe = server_probe.probe_server(py, self._mcp_root)
        if probe.get("ok"):
            emit(f"OK — MCP-Server antwortet ({probe.get('seconds')}s).\n")
        else:
            emit("\n  ⚠  HANDSHAKE FEHLGESCHLAGEN — der Server startet noch "
                 "nicht für Claude:\n"
                 f"     {probe.get('error') or 'unbekannter Fehler'}\n"
                 "     'Diagnose' im Plugin liefert den vollen Traceback.\n")
        return coupled_ok and bool(probe.get("ok"))

    def _run_streamed_install(self, title, target, steps, finalize=None) -> None:
        """Run a sequence of (label, argv) steps via direct subprocess in a
        worker thread, streaming combined stdout/stderr into a live dialog.
        No shell — argv goes straight to CreateProcessW so Umlaut paths survive.
        """
        import subprocess
        import threading

        from .claude_bridge import hidden_console_kwargs

        dlg = wx.Dialog(self, title=title, size=(760, 480),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        out = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY
                          | wx.TE_DONTWRAP)
        out.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL,
                            wx.FONTWEIGHT_NORMAL))
        sizer.Add(out, 1, wx.EXPAND | wx.ALL, 8)
        foot = wx.BoxSizer(wx.HORIZONTAL)
        close_btn = wx.Button(panel, wx.ID_CLOSE, label="Schließen")
        close_btn.Enable(False)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: dlg.EndModal(wx.ID_CLOSE))
        foot.AddStretchSpacer()
        foot.Add(close_btn, 0)
        sizer.Add(foot, 0, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(sizer)

        def emit(line: str) -> None:
            wx.CallAfter(out.AppendText, line)

        def worker():
            emit(f"Ziel-Ordner (_deps): {target}\n\n")
            ok = True
            for label, argv in steps:
                emit(f"$ {label}\n")
                try:
                    os.makedirs(target, exist_ok=True)
                    proc = subprocess.Popen(  # noqa: S603
                        argv, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True,
                        encoding="utf-8", errors="replace",
                        **hidden_console_kwargs())
                    for line in proc.stdout:
                        emit(line)
                    rc = proc.wait()
                except Exception as exc:
                    emit(f"\n[Fehler] {exc}\n")
                    ok = False
                    break
                if rc != 0:
                    emit(f"\n[Abbruch] Schritt endete mit Code {rc}.\n")
                    ok = False
                    break
                emit("\n")
            if ok and finalize is not None:
                # post-install: swap into place, fingerprint, handshake check
                try:
                    ok = finalize(emit)
                except Exception as exc:  # never let the worker die silently
                    emit(f"\n[Fehler] Abschluss fehlgeschlagen: {exc}\n")
                    ok = False
            if ok:
                emit("\n============================================\n"
                     "Fertig. Dieses Fenster schließen und im Plugin auf "
                     "'Erneut prüfen'.\n"
                     "============================================\n")
            else:
                emit("\n============================================\n"
                     "Installation NICHT abgeschlossen — Meldung oben prüfen.\n"
                     "============================================\n")
            wx.CallAfter(close_btn.Enable, True)

        threading.Thread(target=worker, daemon=True).start()
        dlg.ShowModal()
        dlg.Destroy()
        self._render()

    def _enable_ipc(self) -> None:
        res = ipc_setup.ensure_ipc_enabled(self._common_path)
        if res.get("changed") or res.get("was_enabled"):
            self._ipc_restart_hint = res.get("changed", False)
            wx.MessageBox(
                "KiCad-API (IPC) aktiviert. Bitte starte KiCad einmal neu — "
                "danach arbeitet Claude live am offenen Board.",
                "KiCad-API", wx.OK | wx.ICON_INFORMATION)
            self._render()
        else:
            wx.MessageBox(
                "Konnte die KiCad-API nicht automatisch aktivieren"
                f"{(': ' + res['error']) if res.get('error') else ''}.\n\n"
                "Aktiviere sie manuell: Einstellungen → Plugins → KiCad-API.",
                "KiCad-API", wx.OK | wx.ICON_WARNING)

    def _install_claude(self) -> None:
        cmd_text = installer.install_command_text()
        msg = (
            "Claude Code wird mit dem offiziellen Installer installiert:\n\n"
            f"    {cmd_text}\n\n"
            "Quelle: claude.ai (offiziell). Es öffnet sich ein Terminal-Fenster, "
            "das den Fortschritt zeigt. Danach hier auf 'Erneut prüfen' — ein "
            "KiCad-Neustart ist dafür nicht nötig.\n\n"
            "Jetzt installieren?  (Nein = nur die Doku öffnen)"
        )
        dlg = wx.MessageDialog(self, msg, "Claude Code installieren",
                               wx.YES_NO | wx.ICON_QUESTION)
        choice = dlg.ShowModal()
        dlg.Destroy()
        if choice != wx.ID_YES:
            wx.LaunchDefaultBrowser(installer.INSTALL_DOCS_URL)
            return
        try:
            terminal.open_terminal(installer.install_terminal_commands(),
                                   "Claude Code installieren")
            wx.MessageBox(
                "Installer gestartet. Wenn das Terminal 'Fertig' zeigt, hier auf "
                "'Erneut prüfen' klicken.",
                "Claude Code", wx.OK | wx.ICON_INFORMATION)
        except Exception as exc:
            wx.MessageBox(
                f"Konnte den Installer nicht starten:\n{exc}\n\n"
                "Bitte manuell installieren — ich öffne die Doku.",
                "Claude Code", wx.OK | wx.ICON_ERROR)
            wx.LaunchDefaultBrowser(installer.INSTALL_DOCS_URL)

    def _open_login_terminal(self) -> None:
        claude = runtime_env.find_claude() or ["claude"]
        try:
            terminal.open_terminal(preflight.login_commands(claude),
                                   "Claude Login", cwd=self._project_dir)
            wx.MessageBox(
                "Ein Terminal wurde geöffnet. Melde dich dort an (Browser) und "
                "bestätige den Projekt-Zugriff. Danach hier auf 'Erneut prüfen'.",
                "Anmelden", wx.OK | wx.ICON_INFORMATION)
        except Exception as exc:
            wx.MessageBox(f"Konnte kein Terminal öffnen:\n{exc}\n\n"
                          "Führe manuell aus:  claude login",
                          "Anmelden", wx.OK | wx.ICON_ERROR)

    # -- update (provisional: direct from GitHub) ---------------------------

    def _check_update(self) -> None:
        wx.BeginBusyCursor()
        try:
            res = updater.check_for_update(__version__)
        finally:
            wx.EndBusyCursor()
        if not res["ok"]:
            wx.MessageBox(
                f"Update-Prüfung fehlgeschlagen:\n{res['error']}\n\n"
                f"Quelle: {updater.GITHUB_REPO} ({updater.GITHUB_BRANCH})",
                "Update", wx.OK | wx.ICON_WARNING)
            return
        if not res["available"]:
            wx.MessageBox(f"Du hast die neueste Version (v{res['local']}).",
                          "Update", wx.OK | wx.ICON_INFORMATION)
            return
        ask = wx.MessageBox(
            f"Update verfügbar: v{res['local']} → v{res['remote']}.\n\n"
            f"Direkt aus {updater.GITHUB_REPO} laden und installieren? "
            "Danach KiCad neu starten.",
            "Update", wx.YES_NO | wx.ICON_QUESTION)
        if ask == wx.YES:
            self._do_update(res["remote"])

    def _do_update(self, remote: str) -> None:
        install_dir = os.path.dirname(__file__)
        wx.BeginBusyCursor()
        try:
            data = updater.download_zip()
            out = updater.apply_update(install_dir, data)
        except Exception as exc:
            wx.EndBusyCursor()
            wx.MessageBox(f"Update fehlgeschlagen:\n{exc}", "Update",
                          wx.OK | wx.ICON_ERROR)
            return
        wx.EndBusyCursor()
        if out["error"]:
            wx.MessageBox(f"Update fehlgeschlagen:\n{out['error']}", "Update",
                          wx.OK | wx.ICON_ERROR)
            return
        wx.MessageBox(
            f"{len(out['updated'])} Dateien auf v{remote} aktualisiert.\n\n"
            "Bitte KiCad neu starten, damit die neue Version geladen wird.",
            "Update", wx.OK | wx.ICON_INFORMATION)

    # -- start --------------------------------------------------------------

    def _on_start(self, _evt) -> None:
        self.Close()
        self._on_start_chat()
