#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""KiCad-Fähigkeits-Probe — was kann die INSTALLIERTE KiCad-Version?

Frühwarnsystem für KiCad 11 (docs/kicad11_vorbereitung.md §2): der Nightly-CI-
Job ruft dieses Skript und druckt den Report als Job-Summary. Der Wert ist der
**Diff von Woche zu Woche** — taucht ein IPC-/Serve-Modus in ``kicad-cli`` auf,
wächst die kipy-Proto-Fläche (Schematic-API?), wird headless-IPC möglich? Das
sind die Trigger für die Abschnitte 3–5 des Vorbereitungsdokuments.

Bewusst STANDALONE und DEFENSIV: kein Import aus ``kicad_mcp`` (läuft auch, wenn
das Paket nicht installiert ist), fängt jeden Fehler ab, endet immer mit 0 (rein
diagnostisch). Ausgabe ist Markdown → direkt in ``$GITHUB_STEP_SUMMARY``
umleitbar. Lokal: ``python scripts/kicad_capability_probe.py``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def _run(cmd: list[str], timeout: float = 20.0) -> tuple[int, str]:
    """Kommando ausführen, (returncode, stdout+stderr). Nie werfen."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # pragma: no cover - Umgebungs-abhängig
        return 127, f"{type(exc).__name__}: {exc}"


def probe_kicad_cli() -> list[str]:
    """kicad-cli: Version + Subkommandos; markiert einen etwaigen API/IPC-Modus
    (das strategische Signal für headless-IPC)."""
    out: list[str] = ["## kicad-cli", ""]
    exe = shutil.which("kicad-cli")
    if not exe:
        out += ["- ❌ `kicad-cli` nicht im PATH", ""]
        return out
    _, ver = _run([exe, "version"])
    out.append(f"- Version: `{ver.strip().splitlines()[0] if ver.strip() else '?'}`")
    _, helptext = _run([exe, "--help"])
    # Subkommandos aus der help-Sektion sammeln (grob: eingerückte erste Wörter)
    subs: list[str] = []
    in_cmds = False
    for line in helptext.splitlines():
        low = line.lower()
        if "commands:" in low or "positional arguments" in low:
            in_cmds = True
            continue
        if in_cmds:
            stripped = line.strip()
            if not stripped:
                continue
            tok = stripped.split()[0]
            if tok.isalpha() and tok.islower():
                subs.append(tok)
    subs = sorted(set(subs))
    out.append(f"- Subkommandos: {', '.join(f'`{s}`' for s in subs) or '(keine erkannt)'}")
    # Das gesuchte Signal: ein Server-/API-/IPC-Modus in der CLI
    hot = [s for s in subs if s in ("api", "ipc", "serve", "server", "daemon")]
    if hot:
        out.append(f"- 🔥 **Möglicher API/IPC-Modus:** {', '.join(hot)} "
                   "→ headless-IPC prüfen (kicad11_vorbereitung §3)")
    else:
        out.append("- kein API/IPC/Serve-Subkommando (Stand KiCad 10 erwartet)")
    out.append("")
    return out


def probe_kipy() -> list[str]:
    """kipy: installiert? Welche Proto-Message-/API-Flächen — insbesondere ob
    eine Schematic-API auftaucht (in KiCad 10 nur PCB)."""
    out: list[str] = ["## kipy (kicad-python)", ""]
    try:
        import kipy  # noqa: F401
    except Exception as exc:
        out += [f"- ❌ `kipy` nicht importierbar: {exc}", ""]
        return out
    out.append(f"- kipy-Version: `{getattr(kipy, '__version__', '?')}`")

    # Board- vs Schematic-API: welche Domänen-Objekte kennt kipy?
    domains = []
    for modname, label in (("kipy.board", "Board/PCB"),
                           ("kipy.schematic", "Schematic"),
                           ("kipy.project", "Project")):
        try:
            __import__(modname)
            domains.append(f"`{label}` ✓")
        except Exception:
            domains.append(f"`{label}` –")
    out.append(f"- API-Domänen: {', '.join(domains)}")
    if "Schematic` ✓" in " ".join(domains):
        out.append("- 🔥 **Schematic-API vorhanden** → Live-ERC/Eeschema-IPC "
                   "prüfen (kicad11_vorbereitung §5)")

    # Proto-Commands zählen (grobes Wachstumsmaß der API-Fläche)
    try:
        from kipy.proto.common import commands  # type: ignore
        names = [n for n in dir(commands) if n and n[0].isupper()]
        out.append(f"- Proto-Command-Messages: **{len(names)}** "
                   f"(z. B. {', '.join(names[:5])}…)")
    except Exception as exc:
        out.append(f"- Proto-Commands nicht introspizierbar: {exc}")
    out.append("")
    return out


def probe_running_instance() -> list[str]:
    """Läuft ein KiCad, das IPC beantwortet? (Im Nightly-CI meist nein — dann
    ehrlich „keine Instanz", kein Fehler.)"""
    out: list[str] = ["## Laufende IPC-Instanz", ""]
    try:
        from kipy import KiCad  # type: ignore
    except Exception:
        out += ["- kipy fehlt → übersprungen", ""]
        return out
    try:
        kc = KiCad()
        ver = kc.get_version()
        out.append(f"- ✅ IPC-Handshake ok — laufende Version `{ver}`")
    except Exception as exc:
        out.append(f"- keine antwortende Instanz ({type(exc).__name__}) — "
                   "erwartet, wenn kein KiCad läuft")
    out.append("")
    return out


def main() -> int:
    lines: list[str] = ["# KiCad-Fähigkeits-Report", ""]
    lines += probe_kicad_cli()
    lines += probe_kipy()
    lines += probe_running_instance()
    lines += ["---",
              "Diff-Signal: Änderungen ggü. der Vorwoche sind der Trigger für "
              "die Migrationsschritte in `docs/kicad11_vorbereitung.md`."]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
