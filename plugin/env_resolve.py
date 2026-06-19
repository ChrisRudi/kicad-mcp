# SPDX-License-Identifier: GPL-3.0-or-later
"""Couple the bundled kicad-mcp install to the *running* KiCad version.

The bug this fixes ("nichts orange" / ``failed: kicad-mcp``): ``deps.PIP_SPECS``
installs ``kicad-python`` UNPINNED, so pip pulls the *latest* kipy into
``_deps``. But kipy speaks KiCad's IPC protocol, and KiCad bumps that protocol
per major release — a kipy newer than the running KiCad hands the GUI a protobuf
schema it doesn't understand, so ``KiCad().get_version()`` fails inside the GUI
process and every board-aware feature (chat links, live selection) silently goes
dark. The cure is to pin kipy to the version COUPLED to the detected KiCad
(KiCad 10 → kicad-python 0.7.1) and to *downgrade* an already-too-new ``_deps``
back to it.

This module is the decision layer — all pure logic + injectable I/O so it is
unit-testable headless (no KiCad, no pip, no GUI):

  * version detection / parsing / comparison
  * the KiCad→kipy coupling table  → ``resolve_pip_specs``
  * what kipy is actually in ``_deps`` → ``installed_kipy_version``
  * up/downgrade decision           → ``downgrade_decision``
  * an environment fingerprint       → ``build_fingerprint`` / ``*_fingerprint``
  * an atomic ``_deps`` swap (no-brick rebuild) → ``atomic_swap_dir``

It does NOT touch the GUI or run pip; ``setup_dialog`` wires those to it.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
from typing import Optional

# --- the coupling table ----------------------------------------------------
# KiCad MAJOR version  ->  the kicad-python (kipy) version that speaks its IPC
# protocol. KiCad re-versions the IPC API per major release and kicad-python is
# published in lockstep; installing "latest" against an older KiCad is the root
# of "nichts orange". Keyed by major because the IPC protocol is stable within a
# major line. Only verified entries belong here.
#
#   KiCad 10.0  ->  0.7.1   (confirmed live — see CLAUDE.md / kipy 0.7.1)
KICAD_KIPY_COUPLING = {
    10: "0.7.1",
}

# The package that provides the ``kipy`` import (the entry resolve_pip_specs
# rewrites). Matches deps.PIP_SPECS by name (normalized: ``-`` ~ ``_``).
KIPY_PACKAGE = "kicad-python"

# Lives inside ``_deps``; records what env that tree was built for, so a later
# run can tell "already coupled to this KiCad" from "needs a (down/up)grade".
FINGERPRINT_FILENAME = ".env_fingerprint"

_SENTINEL = object()


# --- version parsing / comparison ------------------------------------------

def parse_version(raw: Optional[str]) -> Optional[tuple]:
    """Best-effort ``"10.0.1-rc2"`` / ``"(10.0.1)"`` / ``"0.7.1"`` → ``(10,0,1)``.

    Grabs the first ``N.N`` (with optional ``.N``) run anywhere in the string,
    so KiCad's decorated ``GetBuildVersion()`` and a bare PyPI version both
    parse. Returns ``None`` when no numeric version is present (never raises).
    """
    if not raw:
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(raw))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def version_le(a: Optional[tuple], b: Optional[tuple]) -> bool:
    """``a <= b`` on version tuples (None sorts lowest)."""
    return (a or ()) <= (b or ())


def major_of(version) -> Optional[int]:
    """The KiCad major number from a ``(maj,min,patch)`` tuple or a bare int."""
    if version is None:
        return None
    if isinstance(version, int):
        return version
    if isinstance(version, (tuple, list)) and version:
        return int(version[0])
    return None


# --- KiCad version detection -----------------------------------------------

def parse_kicad_version_from_path(kicad_py_path: Optional[str]) -> Optional[tuple]:
    """``…/KiCad/10.0/bin/python.exe`` → ``(10, 0, 0)``.

    A robust fallback for headless contexts where ``pcbnew`` isn't importable:
    KiCad installs under a version-named dir. Returns ``None`` if no such
    segment is present.
    """
    if not kicad_py_path:
        return None
    m = re.search(r"[Kk]i[Cc]ad[\\/]+(\d+)\.(\d+)", str(kicad_py_path))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), 0)


def detect_kicad_version(_pcbnew=_SENTINEL,
                         kicad_py_path: Optional[str] = None) -> Optional[tuple]:
    """The running KiCad's version as ``(maj,min,patch)`` — or ``None``.

    Order: (1) ``pcbnew.GetBuildVersion()`` — authoritative, and available
    because the plugin runs INSIDE KiCad's Python; (2) parse the KiCad install
    dir out of ``kicad_py_path`` (headless fallback). Never raises — a failure
    to detect simply yields ``None``, and the caller falls back to today's
    unpinned behaviour rather than breaking the install.

    ``_pcbnew`` is injectable for tests (pass a stub or ``None`` to skip the
    import path).
    """
    pcb = _pcbnew
    if pcb is _SENTINEL:
        try:
            import pcbnew as pcb  # type: ignore
        except Exception:
            pcb = None
    if pcb is not None:
        try:
            v = parse_version(pcb.GetBuildVersion())
        except Exception:
            v = None
        if v:
            return v
    return parse_kicad_version_from_path(kicad_py_path)


# --- the coupling decision -------------------------------------------------

def coupled_kipy_version(kicad_version) -> Optional[str]:
    """The pinned kicad-python version for a KiCad version/major — or ``None``
    when the KiCad major isn't in the table (unknown/future → don't guess)."""
    return KICAD_KIPY_COUPLING.get(major_of(kicad_version))


def _is_kipy_spec(spec: str) -> bool:
    """Does this pip spec refer to the kicad-python package? (name only, before
    any ``==``/``>=``/``[extra]``; ``-``/``_`` insensitive)."""
    name = re.split(r"[<>=!~\[ ]", spec, 1)[0].strip().lower()
    return name.replace("_", "-") == KIPY_PACKAGE


def plan_kipy_pin(kicad_version, search_paths=None, _glob=glob.glob) -> dict:
    """Decide which kipy version to pin, with diagnostics. Three sources, in
    order of trust:

      * ``"table"``    — the coupling table has this KiCad major (authoritative).
      * ``"bundled"``  — table miss, but KiCad ships a kipy in its own
        site-packages → pin to THAT (best-effort for an unknown/future KiCad,
        strictly better than blind "latest"). Carries a verify-me warning.
      * ``"unpinned"`` — nothing known → bare package (last resort). Warns.

    Also a pollution guard: when the table HAS this major but KiCad's 3rdparty
    holds a *different* kipy (e.g. someone pip-installed "latest" into the
    mutable ``…/3rdparty/…`` site), the table still wins but a loud warning is
    emitted. ``search_paths`` is normally ``sys.path``; the KiCad-owned copies
    are read from there (the plugin's own ``_deps`` is ignored). Never raises.

    Returns ``{spec, version, source, bundled, warning}``.
    """
    table = coupled_kipy_version(kicad_version)
    bundled = kicad_bundled_kipy_version(search_paths, _glob=_glob)
    if table:
        out = {"spec": f"{KIPY_PACKAGE}=={table}", "version": table,
               "source": "table", "bundled": bundled, "warning": ""}
        if bundled and parse_version(bundled) != parse_version(table):
            out["warning"] = (
                f"kipy in KiCads site-packages ist {bundled}, gekoppelt "
                f"erwartet {table} — moegliche Verschmutzung; gepinnt auf "
                f"{table}.")
        return out
    if bundled:
        return {"spec": f"{KIPY_PACKAGE}=={bundled}", "version": bundled,
                "source": "bundled", "bundled": bundled,
                "warning": (f"KiCad-Major unbekannt — kipy aus KiCads "
                            f"site-packages abgeleitet ({bundled}); bitte die "
                            "Coupling-Tabelle in env_resolve.py ergaenzen.")}
    return {"spec": KIPY_PACKAGE, "version": None, "source": "unpinned",
            "bundled": None,
            "warning": ("KiCad-Major unbekannt und keine kipy in KiCads "
                        "site-packages gefunden — ungepinnt (latest) "
                        "installiert; bitte verifizieren.")}


def kipy_spec(kicad_version) -> str:
    """The pip spec for kipy from the coupling table alone (no path scan):
    pinned (``kicad-python==0.7.1``) when the KiCad major is known, else bare."""
    return plan_kipy_pin(kicad_version)["spec"]


def resolve_pip_specs(kicad_version, base_specs, search_paths=None,
                      _glob=glob.glob) -> list:
    """``base_specs`` (e.g. ``deps.PIP_SPECS``) with the kicad-python entry
    rewritten to the resolved pin (see ``plan_kipy_pin``).

    Non-kipy specs pass through untouched and order is preserved. Pass
    ``search_paths`` (``sys.path``) to enable the KiCad-bundled fallback for an
    unknown major; without it only the table/unpinned path is used. Never
    raises, so a resolver miss degrades gracefully instead of bricking install.
    """
    pinned = plan_kipy_pin(kicad_version, search_paths, _glob=_glob)["spec"]
    out = []
    seen_kipy = False
    for spec in base_specs:
        if _is_kipy_spec(spec):
            out.append(pinned)
            seen_kipy = True
        else:
            out.append(spec)
    if not seen_kipy:  # base set had no kipy entry — add the resolved one
        out.append(pinned)
    return out


def pinned_kipy_in(specs) -> Optional[str]:
    """The version a resolved spec list pins kipy to (``kicad-python==0.7.1`` →
    ``"0.7.1"``), or ``None`` if it's unpinned/absent."""
    for spec in specs or []:
        if _is_kipy_spec(spec):
            m = re.search(r"==\s*([0-9][^\s,]*)", spec)
            return m.group(1) if m else None
    return None


# --- what is actually installed in _deps -----------------------------------

def _distinfo_version(path: str) -> Optional[str]:
    """``…/kicad_python-0.7.1.dist-info`` → ``"0.7.1"`` (raw), else ``None``."""
    m = re.search(r"kicad[_-]python-([0-9][^/\\]*?)\.dist-info$",
                  os.path.basename(path.rstrip("/\\")))
    return m.group(1) if m else None


def _highest_distinfo(dirs, _glob=glob.glob) -> Optional[str]:
    """The highest kicad-python version found across ``dirs`` (each scanned for
    a ``kicad_python-*.dist-info``), or ``None``."""
    best_tuple = None
    best_raw = None
    for d in dirs:
        if not d:
            continue
        hits = (_glob(os.path.join(d, "kicad_python-*.dist-info"))
                + _glob(os.path.join(d, "kicad-python-*.dist-info")))
        for h in hits:
            raw = _distinfo_version(h)
            v = parse_version(raw) if raw else None
            if v and (best_tuple is None or v > best_tuple):
                best_tuple, best_raw = v, raw
    return best_raw


def installed_kipy_version(deps_dir: Optional[str],
                           _glob=glob.glob) -> Optional[str]:
    """The kicad-python version currently sitting in ``_deps`` — read from its
    ``kicad_python-<ver>.dist-info`` directory name (no import needed, so it
    works headless and even when the tree is incomplete). ``None`` if absent."""
    if not deps_dir:
        return None
    return _highest_distinfo([deps_dir], _glob=_glob)


def classify_kipy_location(path: str) -> str:
    """Where does a kicad-python copy live? Mirrors ``scripts/check_kipy.py``:
    ``"deps"`` (the plugin's own ``_deps`` — ours), ``"install"`` (a pristine
    KiCad install dir), ``"3rdparty"`` (KiCad's user-mutable 3rdparty site —
    authoritative-by-origin but pollutable), or ``"other"``."""
    low = path.lower().replace(os.sep, "/")
    if "_deps" in low:
        return "deps"
    if "3rdparty" in low:
        return "3rdparty"
    if "program files" in low or "/kicad/" in low or "/applications/" in low:
        return "install"
    return "other"


def kicad_bundled_kipy_version(search_paths, _glob=glob.glob) -> Optional[str]:
    """The kipy version KiCad itself provides — read from the
    ``kicad_python-*.dist-info`` under KiCad-owned entries of ``search_paths``
    (normally ``sys.path``). The plugin's own ``_deps`` is ignored (it's not a
    KiCad source). A pristine ``install`` copy wins over the mutable
    ``3rdparty`` one. ``None`` if KiCad ships no kipy on the given paths.

    This is the read-it-out fallback: for a KiCad major absent from the
    coupling table, what KiCad bundled for itself is the best available guess.
    """
    if not search_paths:
        return None
    by_class = {}
    for d in search_paths:
        if not d or not os.path.isdir(d):
            continue
        hits = (_glob(os.path.join(d, "kicad_python-*.dist-info"))
                + _glob(os.path.join(d, "kicad-python-*.dist-info")))
        for h in hits:
            cls = classify_kipy_location(h)
            if cls not in ("install", "3rdparty"):
                continue  # ignore our own _deps and anything unrelated
            best = _highest_distinfo([d], _glob=_glob)
            cur = by_class.get(cls)
            if best and (cur is None or parse_version(best) > parse_version(cur)):
                by_class[cls] = best
    for cls in ("install", "3rdparty"):  # pristine wins over mutable
        if cls in by_class:
            return by_class[cls]
    return None


def downgrade_decision(kicad_version, deps_dir: Optional[str],
                       _glob=glob.glob) -> dict:
    """Compare the kipy in ``_deps`` against the coupled target.

    Returns ``{action, installed, target, mismatch}`` where ``action`` is:
      * ``"none"``    — already coupled, or KiCad version unknown (leave as-is)
      * ``"install"`` — nothing in ``_deps`` yet (fresh install)
      * ``"downgrade"`` — installed kipy is NEWER than coupled (the bug case)
      * ``"upgrade"`` — installed kipy is OLDER than coupled
    ``mismatch`` is True for any action that requires a (re)install.
    """
    target = coupled_kipy_version(kicad_version)
    installed = installed_kipy_version(deps_dir, _glob=_glob)
    out = {"action": "none", "installed": installed, "target": target,
           "mismatch": False}
    if target is None:  # unknown KiCad — don't force anything
        return out
    if installed is None:
        out["action"], out["mismatch"] = "install", True
        return out
    iv, tv = parse_version(installed), parse_version(target)
    if iv == tv:
        return out
    out["mismatch"] = True
    out["action"] = "downgrade" if version_le(tv, iv) else "upgrade"
    return out


# --- environment fingerprint -----------------------------------------------

def build_fingerprint(kicad_version, specs, plugin_version: str = "") -> dict:
    """A small, comparable record of what ``_deps`` was built for. ``kipy`` is
    the version actually pinned in ``specs`` (table OR bundled fallback), so the
    fingerprint reflects what landed — not just table hits."""
    return {
        "kicad_version": ".".join(str(x) for x in kicad_version)
        if kicad_version else "",
        "kicad_major": major_of(kicad_version),
        "kipy": pinned_kipy_in(specs) or coupled_kipy_version(kicad_version)
        or "",
        "specs": sorted(specs),
        "plugin_version": plugin_version,
    }


def fingerprint_path(deps_dir: str) -> str:
    return os.path.join(deps_dir, FINGERPRINT_FILENAME)


def read_fingerprint(deps_dir: Optional[str]) -> Optional[dict]:
    """The fingerprint stored in ``_deps`` — or ``None`` (missing/unreadable)."""
    if not deps_dir:
        return None
    try:
        with open(fingerprint_path(deps_dir), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def write_fingerprint(deps_dir: str, fingerprint: dict) -> bool:
    """Persist the fingerprint into ``_deps``. Best-effort: returns False on
    failure (a missing fingerprint just forces a future rebuild, never bricks)."""
    try:
        with open(fingerprint_path(deps_dir), "w", encoding="utf-8") as fh:
            json.dump(fingerprint, fh, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def fingerprint_stale(deps_dir: Optional[str], current: dict) -> bool:
    """Does ``_deps`` need a rebuild for ``current``? True if the stored
    fingerprint is missing or disagrees on the KiCad major or coupled kipy
    (the two fields that actually drive the coupling)."""
    stored = read_fingerprint(deps_dir)
    if not stored:
        return True
    return (stored.get("kicad_major") != current.get("kicad_major")
            or stored.get("kipy") != current.get("kipy"))


# --- atomic _deps swap (no-brick rebuild) ----------------------------------

def atomic_swap_dir(new_dir: str, dest_dir: str, _os=os,
                    _shutil=shutil) -> dict:
    """Replace ``dest_dir`` with ``new_dir`` as atomically as the OS allows.

    The no-brick guarantee: the live ``_deps`` is only touched AFTER a fresh
    tree was installed AND verified into ``new_dir`` (``_deps.new``). Sequence:
    move the old ``dest`` aside to ``dest.old`` → move ``new`` into ``dest`` →
    delete ``dest.old``. If the second move fails the old tree is rolled back
    into place, so a failed swap leaves the previous working ``_deps`` intact.

    Returns ``{ok, error}``. Never raises.
    """
    backup = dest_dir + ".old"
    try:
        if not _os.path.isdir(new_dir):
            return {"ok": False, "error": f"Quelle fehlt: {new_dir}"}
        # clear any stale backup from an earlier interrupted swap
        if _os.path.exists(backup):
            _shutil.rmtree(backup, ignore_errors=True)
        had_old = _os.path.isdir(dest_dir)
        if had_old:
            _os.replace(dest_dir, backup)  # move live tree aside (reversible)
        try:
            _os.replace(new_dir, dest_dir)  # promote the verified new tree
        except Exception as exc:
            if had_old and not _os.path.exists(dest_dir):
                _os.replace(backup, dest_dir)  # roll back — old _deps survives
            return {"ok": False, "error": f"Swap fehlgeschlagen: {exc}"}
        if had_old:
            _shutil.rmtree(backup, ignore_errors=True)  # best-effort cleanup
        return {"ok": True, "error": ""}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
