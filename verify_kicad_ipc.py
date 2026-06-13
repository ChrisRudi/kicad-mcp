# SPDX-License-Identifier: GPL-3.0-or-later
# verify_kicad_ipc.py
# Zweck:   Prueft gegen eine LAUFENDE KiCad-10.0.x-Instanz, ob die Annahmen der
#          geplanten IPC-Live-Schicht real erfuellt sind, BEVOR darauf gebaut wird.
#          Entdeckt die tatsaechlich vorhandenen Board-Getter zur Laufzeit
#          (keine hartkodierten Methodennamen).
# Inputs:  Laufendes KiCad mit offenem PCB-Projekt; API-Server aktiv
#          (Preferences > Plugins > Enable IPC API). Env KICAD_API_SOCKET/_TOKEN
#          werden von kipy automatisch gelesen.
# Outputs: Exit 0 wenn alle PFLICHT-Checks bestehen, sonst 1. Klartext-Report
#          nach stdout via logging. Keine Schreibzugriffe ausser dem opt-in
#          Move-Test (--move), der danach zurueckgesetzt wird.
# Deps:    kipy (pip install kicad-python), Python 3.9-3.12.
# Aufruf:  python verify_kicad_ipc.py            (nur Lese-Checks)
#          python verify_kicad_ipc.py --move     (zusaetzl. Sichtbarkeits-Test)

import argparse
import logging
import sys
import time

log = logging.getLogger("verify")

# Minimal-Schwellen. KiCad 10.0.0 brachte Group-Support (kipy 0.7.0); die
# Live-Schicht braucht get_footprints + KIID + position. Tracks/Vias optional
# je nach Diff-Umfang, daher als WARN statt FAIL behandelt.
MIN_KIPY = (0, 7, 0)


def _section(title):
    log.info("")
    log.info("=== %s ===", title)


def _import_kipy():
    try:
        import kipy
        return kipy
    except ImportError as exc:
        log.error("kipy nicht installiert: %s", exc)
        log.error("  -> pip install kicad-python  (Python 3.9-3.12, NICHT 3.13)")
        return None


def _kipy_version(kipy):
    ver = getattr(kipy, "__version__", None)
    if ver is None:
        log.warning("kipy.__version__ nicht lesbar; Versionspruefung uebersprungen")
        return None
    parts = []
    for chunk in ver.split(".")[:3]:
        num = "".join(c for c in chunk if c.isdigit())
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _connect(kipy):
    # kipy.KiCad() liest socket_path/token aus den Env-Variablen, die KiCad setzt.
    k = kipy.KiCad()
    ver = k.get_version()
    log.info("Verbunden. KiCad-Version: %s", ver)
    return k, str(ver)


def _discover_getters(board):
    # Live-Erkennung statt Annahme: welche get_*-Methoden liefert dieses Board?
    found = {}
    for name in dir(board):
        if not name.startswith("get_"):
            continue
        attr = getattr(board, name, None)
        if callable(attr):
            found[name] = attr
    return found


def _safe_call(fn, label):
    try:
        result = fn()
        n = len(result) if hasattr(result, "__len__") else "?"
        log.info("  %-22s -> ok (%s Items)", label, n)
        return result
    except Exception as exc:  # bewusst breit: Diagnose-Tool, jeder Fehler ist ein Befund
        log.warning("  %-22s -> FEHLER: %s", label, exc)
        return None


def _item_signature(item):
    # Baut den Diff-Fingerprint, den die Live-Schicht spaeter nutzt.
    # Greift defensiv zu, weil Track/Via/Footprint unterschiedliche Felder haben.
    sig = {}
    kiid = getattr(item, "id", None)
    sig["kiid"] = str(kiid) if kiid is not None else None
    pos = getattr(item, "position", None)
    if pos is not None:
        sig["x"] = getattr(pos, "x", None)
        sig["y"] = getattr(pos, "y", None)
    for fld in ("orientation", "layer"):
        if hasattr(item, fld):
            sig[fld] = getattr(item, fld)
    return sig


def check_connection(kipy):
    _section("PFLICHT: Verbindung zur laufenden Instanz")
    try:
        k, ver = _connect(kipy)
    except Exception as exc:
        log.error("Verbindung fehlgeschlagen: %s", exc)
        log.error("  Pruefe: KiCad laeuft? PCB-Editor offen? API-Server aktiviert?")
        return None, None, False
    is_103 = ver.replace("v", "").startswith(("10.0.3", "10.0.4", "10.0.5", "10.1", "10.99"))
    if "10.0.3" in ver:
        log.info("Zielversion 10.0.3 bestaetigt.")
    elif is_103:
        log.info("Version >= 10.0.3-kompatibel.")
    else:
        log.warning("Version ist NICHT 10.0.3 (%s). Test laeuft weiter, aber "
                    "Recovery-/Autosave-Verhalten kann abweichen.", ver)
    return k, ver, True


def check_board_and_kiid(k):
    _section("PFLICHT: Board + Footprints + KIID-Stabilitaet")
    try:
        board = k.get_board()
    except Exception as exc:
        log.error("get_board() fehlgeschlagen: %s (kein PCB offen?)", exc)
        return None, False

    getters = _discover_getters(board)
    log.info("Entdeckte Board-Getter: %s", ", ".join(sorted(getters)) or "(keine)")

    if "get_footprints" not in getters:
        log.error("get_footprints fehlt -> Live-Schicht nicht baubar.")
        return board, False
    fps = _safe_call(getters["get_footprints"], "get_footprints")
    if not fps:
        log.warning("Keine Footprints im Board. KIID-Test braucht >=1 Bauteil.")
        return board, True

    # KIID-Stabilitaet: zwei Reads, gleiche IDs? (Grundannahme des Diffs.)
    first = {str(getattr(f, "id", None)) for f in fps}
    time.sleep(0.2)
    fps2 = getters["get_footprints"]()
    second = {str(getattr(f, "id", None)) for f in fps2}
    if None in first or "None" in first:
        log.error("Footprint ohne .id gefunden -> KIID-Keying unmoeglich.")
        return board, False
    if first == second:
        log.info("KIID stabil ueber zwei Reads (%d Footprints). Diff-Keying ok.", len(first))
    else:
        log.error("KIID instabil zwischen zwei Reads -> Diff wuerde Phantom-"
                  "Aenderungen melden. added=%s removed=%s",
                  second - first, first - second)
        return board, False
    return board, True


def check_track_via(k, board):
    _section("OPTIONAL: Tracks/Vias (Diff-Umfang Footprints+Tracks+Vias)")
    getters = _discover_getters(board)
    candidates = [n for n in getters if any(t in n for t in ("track", "via", "item"))]
    if not candidates:
        log.warning("Keine track/via/item-Getter gefunden. Diff-Umfang muss auf "
                    "Footprints reduziert oder per get_items() geloest werden.")
        return
    for name in sorted(candidates):
        items = _safe_call(getters[name], name)
        if items:
            sig = _item_signature(items[0])
            log.info("    Beispiel-Signatur (%s): %s", name, sig)


def check_timeout_retry(k):
    _section("PFLICHT-WISSEN: Timeout-Verhalten (Single-Thread/Retry)")
    # Kein erzwungener Timeout moeglich ohne Nutzerinteraktion; wir dokumentieren
    # nur, dass das Client-Timeout konfigurierbar ist (Basis der Retry-Logik).
    to = getattr(k, "_timeout_ms", None)
    log.info("Client-Timeout (ms): %s", to if to is not None else "default ~2000")
    log.info("Merke fuer die Live-Schicht: Schreib-Calls koennen scheitern, "
             "waehrend der Nutzer interagiert -> Retry-with-backoff Pflicht.")


def check_commit_attribution(k, board):
    _section("OPTIONAL: Commit-Attribution lesbar? (entscheidet 4a bevorzugt/Fallback)")
    # Prueft, ob ueber kipy Commit-Metadaten/History abfragbar sind. Wenn nein,
    # nutzt die Live-Schicht die Maskierungs-Fallback-Attribution.
    hits = []
    for obj, label in ((k, "KiCad"), (board, "Board")):
        for name in dir(obj):
            low = name.lower()
            if any(t in low for t in ("commit", "history", "author")):
                hits.append("%s.%s" % (label, name))
    if hits:
        log.info("Moegliche Commit/History-Zugriffe gefunden: %s", ", ".join(hits))
        log.info("  -> 4a commit-basierte Attribution PRUEFEN (kann nutzbar sein).")
    else:
        log.warning("Keine Commit/History-Lese-API ueber kipy sichtbar.")
        log.warning("  -> 4a Fallback (erwartete-Aenderungen-Maskierung) verwenden.")


def check_move_visible(k, board):
    _section("OPTIONAL (--move): Schreibzugriff sichtbar + Self-Write-Maskierung")
    getters = _discover_getters(board)
    fps = getters["get_footprints"]()
    if not fps:
        log.warning("Kein Footprint zum Verschieben.")
        return
    fp = fps[0]
    ref = getattr(getattr(fp, "reference_field", None), "text", None)
    ref_txt = getattr(ref, "value", "?") if ref is not None else "?"
    orig = getattr(fp, "position", None)
    if orig is None:
        log.error("Footprint ohne position -> Move-Test nicht moeglich.")
        return
    ox, oy = orig.x, orig.y
    log.info("Verschiebe '%s' um +1mm und zurueck...", ref_txt)
    try:
        from kipy.geometry import Vector2
        fp.position = Vector2.from_xy(ox + 1_000_000, oy)  # 1mm in nm
        update = getters.get("update_items") or getattr(board, "update_items", None)
        # update_items liegt am Board, nicht unter get_*; defensiv beziehen.
        board.update_items([fp])
        log.info("  Verschoben. PRUEFE VISUELL: Bauteil im Editor bewegt? "
                 "(Bestaetigt: Aktion sofort sichtbar.)")
        time.sleep(1.0)
        fp.position = Vector2.from_xy(ox, oy)
        board.update_items([fp])
        log.info("  Zurueckgesetzt. (Self-Write: dein Diff muss DIESE Aenderung "
                 "maskieren, sonst Fehlalarm 'Nutzer-Eingriff'.)")
    except Exception as exc:
        log.error("Move-Test fehlgeschlagen: %s", exc)


def main():
    parser = argparse.ArgumentParser(description="KiCad IPC Capability-Verifier")
    parser.add_argument("--move", action="store_true",
                        help="Fuehrt einen sichtbaren Move+Reset aus (Schreibzugriff)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    kipy = _import_kipy()
    if kipy is None:
        return 1

    ver_tuple = _kipy_version(kipy)
    if ver_tuple is not None:
        ok = ver_tuple >= MIN_KIPY
        log.info("kipy-Version: %s (min %s) -> %s",
                 ".".join(map(str, ver_tuple)), ".".join(map(str, MIN_KIPY)),
                 "ok" if ok else "ZU ALT")
        if not ok:
            log.error("kipy zu alt fuer KiCad-10-Features. Upgrade noetig.")
            return 1

    k, ver, ok = check_connection(kipy)
    if not ok:
        return 1

    check_timeout_retry(k)

    board, ok = check_board_and_kiid(k)
    if not ok:
        log.error("")
        log.error("ERGEBNIS: PFLICHT-Check fehlgeschlagen. Live-Schicht NICHT bauen, "
                  "bis das behoben ist.")
        return 1

    check_track_via(k, board)
    check_commit_attribution(k, board)

    if args.move:
        check_move_visible(k, board)

    _section("ERGEBNIS")
    log.info("Alle PFLICHT-Checks bestanden. Live-Schicht kann auf diesen "
             "Annahmen aufbauen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
