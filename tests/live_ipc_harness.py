# SPDX-License-Identifier: GPL-3.0-or-later
"""Ein ECHTER laufender pcbnew-Editor headless (Xvfb) — für Live-IPC-Tests.

Der Selftest (``kicad_mcp.selftest``) deckt die datei-/tool-Maschinerie ab.
Was er NICHT kann: die „Mitarbeiter"-Schicht — Live-IPC gegen einen wirklich
laufenden KiCad-Editor (Selektion, Cross-Probe, ``ipc_*``-Tools). Genau das
lief bisher nur auf dem Rechner des Nutzers. Dieses Harness startet einen
echten pcbnew-Prozess unter einem virtuellen Display (Xvfb), räumt den
„Welcome to KiCad"-Erststart-Dialog per xdotool weg (er erscheint bei jedem
pcbnew-Standalone-Start neu) und wartet, bis die kipy-IPC antwortet.

Nur Linux + Container/CI. Opt-in: die Tests, die es nutzen, skippen sich,
wenn ``xvfb-run``/``pcbnew``/``xdotool`` fehlen oder ``KICAD_MCP_LIVE_IPC``
nicht gesetzt ist (lokale Windows-Dev-Maschine bleibt unberührt).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Optional


def tools_present() -> bool:
    """Sind Xvfb + pcbnew + xdotool da? (sonst: Test skippen)."""
    return all(shutil.which(t) for t in ("Xvfb", "pcbnew", "xdotool"))


def _wait_display(display: str, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if subprocess.run(["xdpyinfo"], env={**os.environ, "DISPLAY": display},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False).returncode == 0:
            return True
        time.sleep(0.5)
    return False


def _dismiss_setup_dialog(display: str) -> None:
    """Den „Welcome to KiCad / KiCad Setup"-Erststart-Dialog wegklicken.

    Escape öffnet einen „Are you sure?"-Bestätiger (Yes links) — beide
    Klicks blind an ihren festen Koordinaten; harmlos, wenn kein Dialog da
    ist (xdotool findet dann kein Fenster)."""
    env = {**os.environ, "DISPLAY": display}
    hit = subprocess.run(["xdotool", "search", "--name", "KiCad Setup"],
                         env=env, capture_output=True, text=True, check=False)
    if not hit.stdout.strip():
        return
    subprocess.run(["xdotool", "search", "--name", "KiCad Setup",
                    "windowactivate", "--sync"], env=env,
                   stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["xdotool", "key", "--clearmodifiers", "Escape"],
                   env=env, check=False)
    time.sleep(1.0)
    # „Yes" des Bestätigers (linke Hälfte der Button-Zeile)
    subprocess.run(["xdotool", "mousemove", "487", "391", "click", "1"],
                   env=env, check=False)
    time.sleep(1.0)


class LiveEditor:
    """Kontext: startet pcbnew mit ``board_path``, liefert einen kipy-Client.

    ``with LiveEditor(board) as (kicad, display): …`` — der Editor lebt für
    die Dauer des Blocks und wird danach sicher beendet.
    """

    def __init__(self, board_path: str, display: str = ":77",
                 ready_timeout: float = 90.0):
        self.board_path = board_path
        self.display = display
        self.ready_timeout = ready_timeout
        self._xvfb: Optional[subprocess.Popen] = None
        self._pcbnew: Optional[subprocess.Popen] = None
        self.kicad = None
        self._stderr_path = ""
        self._stderr_file = None

    def __enter__(self):
        # Xvfb nur starten, wenn das Display noch nicht lebt (CI kann eins
        # mitbringen).
        if not _wait_display(self.display, timeout=1.0):
            self._xvfb = subprocess.Popen(
                ["Xvfb", self.display, "-screen", "0", "1600x1000x24"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not _wait_display(self.display, timeout=15.0):
                raise RuntimeError(f"Xvfb {self.display} kam nicht hoch")
        env = {**os.environ, "DISPLAY": self.display}
        try:
            os.remove("/tmp/kicad/api.sock")
        except OSError:
            pass
        # stderr in Datei statt DEVNULL: beim Timeout ist pcbnews eigene
        # Meldung die einzige Diagnose („zuletzt: None" hilft niemandem).
        self._stderr_path = os.path.join(
            os.path.dirname(self.board_path) or ".", "pcbnew_stderr.log")
        self._stderr_file = open(  # pylint: disable=consider-using-with
            self._stderr_path, "w", encoding="utf-8")
        self._pcbnew = subprocess.Popen(
            ["pcbnew", self.board_path], env=env,
            stdout=self._stderr_file, stderr=self._stderr_file)
        self.kicad = self._await_ready()
        return self.kicad, self.display

    def _await_ready(self):
        from kipy import KiCad  # nur wenn kipy da ist (Test-Guard davor)
        from kipy.errors import ApiError
        end = time.time() + self.ready_timeout
        last = None
        while time.time() < end:
            if os.path.exists("/tmp/kicad/api.sock"):
                try:
                    k = KiCad()
                    k.ping()
                    return k
                except (ApiError, OSError) as exc:
                    last = exc
                    # „not ready to reply" = Modaldialog blockiert → wegklicken
                    _dismiss_setup_dialog(self.display)
            time.sleep(2.0)
        tail = ""
        try:
            self._stderr_file.flush()
            with open(self._stderr_path, encoding="utf-8",
                      errors="replace") as fh:
                tail = fh.read()[-1500:]
        except OSError:
            pass
        raise RuntimeError(
            f"Live-Editor nicht bereit in {int(self.ready_timeout)}s "
            f"(zuletzt: {last}; api.sock existiert: "
            f"{os.path.exists('/tmp/kicad/api.sock')}; pcbnew-stderr: "
            f"{tail or '<leer>'})")

    def __exit__(self, *exc):
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()
            except OSError:
                pass
        for proc in (self._pcbnew, self._xvfb):
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        return False
