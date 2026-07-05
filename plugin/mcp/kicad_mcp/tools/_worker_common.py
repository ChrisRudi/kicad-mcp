# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemeinsame Server-Seite der Warm-Worker — pcbnew + stdlib ONLY.

Die drei Worker (``connectivity_worker``, ``via_promote_worker``,
``pcb_session_worker``) teilen sich hier das Protokoll-Boilerplate:
pcbnew-Import mit stummgeschaltetem stdout (Import-Geplapper würde den
JSON-Stream vergiften) und die stdin/stdout-Request-Schleife mit
Mark-Framing. Die Client-Seite (Spawn/Recycle) wohnt in ``_warm_daemon``.

WICHTIG: Dieses Modul darf NICHTS aus ``kicad_mcp`` importieren — die
Worker starten per Dateipfad (nie ``-m``), damit der Paket-``__init__``
(→ Server → alle Tools, ~3 s) beim Subprozess-Start nicht bezahlt wird.
Worker importieren es deshalb zweigleisig::

    try:
        import _worker_common as wc          # Subprozess (Skript-Dir im Pfad)
    except ImportError:
        from kicad_mcp.tools import _worker_common as wc   # Tests/Paket
"""
import json
import sys
import traceback


def import_pcbnew():
    """Import pcbnew with stdout muted and return the module.

    pcbnew prints ("Adding duplicate image handler …") to stdout on import;
    route that to stderr so the framed protocol stream stays clean.
    """
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        import pcbnew  # noqa: E402  # pylint: disable=import-outside-toplevel,import-error
    finally:
        sys.stdout = real_stdout
    return pcbnew


def serve(handle, mark: str, name: str) -> None:
    """Newline-JSON-Requestschleife: ein Request → eine Mark-geframte Antwort.

    ``handle(req) -> dict`` macht die eigentliche Arbeit; Fehler werden als
    ``{ok: False, error, traceback}`` gemeldet statt den Prozess zu reißen.
    Recycling gehört dem CLIENT (``_warm_daemon``): nach ``mutated``/
    ``SwigPyObject``/Loads-Cap killt er diesen Prozess und respawnt beim
    nächsten Request — Selbst-Beenden hier würde mit dessen nächstem Write
    racen (Write in sterbenden Daemon → Hänger).
    """
    sys.stderr.write(f"{name} ready\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        rid = req.get("id")
        try:
            resp = handle(req)
        except Exception:  # pylint: disable=broad-exception-caught
            resp = {"ok": False, "error": "daemon error",
                    "traceback": traceback.format_exc()[-2000:]}
        resp["id"] = rid
        sys.stdout.write(mark + json.dumps(resp) + "\n")
        sys.stdout.flush()
