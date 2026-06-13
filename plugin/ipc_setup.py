# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn KiCad's IPC API server ON for the user — the switch behind
*Einstellungen → Plugins → KiCad-API* — so the live-board tools just work.

The setting lives in ``kicad_common.json`` under ``api.enable_server``. KiCad
starts the IPC server only at launch, and it skips re-saving common settings
that weren't changed in-session (verified in ``json_settings.cpp``:
``if !modified && !aForce && file exists -> return false``). So writing the key
into the file is reliable: it survives the next clean exit, and the plugin
re-asserts it on every load to self-heal the rare clobber. The only unavoidable
manual step is **one KiCad restart** after first enabling it.

Pure logic (no KiCad/wx imports); the KiCad layer passes in the exact
``kicad_common.json`` path (from ``SETTINGS_MANAGER.GetUserSettingsPath()``).
Unit-testable headless.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Optional

COMMON_FILE = "kicad_common.json"


def _config_roots() -> list[str]:
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "kicad"))          # Windows
    roots.append(os.path.expanduser("~/.config/kicad"))        # Linux
    roots.append(os.path.expanduser(
        "~/Library/Preferences/kicad"))                        # macOS
    return roots


def find_kicad_common(explicit_dir: Optional[str] = None) -> Optional[str]:
    """Locate ``kicad_common.json``.

    Prefer ``explicit_dir`` (the KiCad layer passes
    ``SETTINGS_MANAGER.GetUserSettingsPath()`` — the exact per-version dir).
    Otherwise glob the standard per-OS roots and pick the highest version dir.
    Returns the path (it normally exists once KiCad has run) or ``None``.
    """
    if explicit_dir:
        cand = os.path.join(explicit_dir, COMMON_FILE)
        if os.path.isfile(cand):
            return cand
    hits = []
    for root in _config_roots():
        hits += glob.glob(os.path.join(root, "*", COMMON_FILE))
        direct = os.path.join(root, COMMON_FILE)
        if os.path.isfile(direct):
            hits.append(direct)
    existing = [p for p in hits if os.path.isfile(p)]
    if not existing:
        return None
    # Highest KiCad version wins (".../kicad/10.0/..." beats ".../9.0/...").
    # Version-aware: numeric tuple of the parent dir, so 10.0 > 9.0 (not string
    # sort, where "10.0" < "9.0").
    def _ver_key(path: str):
        name = os.path.basename(os.path.dirname(path))
        parts = []
        for tok in name.split("."):
            parts.append(int(tok) if tok.isdigit() else -1)
        return parts or [-1]
    return max(existing, key=_ver_key)


def read_ipc_enabled(common_path: Optional[str]) -> Optional[bool]:
    """Return ``api.enable_server`` from the file, or ``None`` if it can't be
    determined (no path / unreadable / key absent)."""
    if not common_path or not os.path.isfile(common_path):
        return None
    try:
        with open(common_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    api = data.get("api")
    if isinstance(api, dict) and "enable_server" in api:
        return bool(api["enable_server"])
    return None


def ensure_ipc_enabled(common_path: Optional[str]) -> dict:
    """Make sure ``api.enable_server`` is ``true`` (read-modify-write, all other
    keys preserved). Idempotent.

    Returns ``{found, path, was_enabled, changed, error}``. ``changed`` is True
    only when we actually flipped it (so the caller can prompt for the restart
    exactly once).
    """
    out = {"found": False, "path": common_path, "was_enabled": None,
           "changed": False, "error": ""}
    if not common_path or not os.path.isfile(common_path):
        out["error"] = "kicad_common.json nicht gefunden"
        return out
    out["found"] = True
    try:
        with open(common_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        out["error"] = f"lesen fehlgeschlagen: {exc}"
        return out
    api = data.get("api")
    if not isinstance(api, dict):
        api = {}
        data["api"] = api
    out["was_enabled"] = bool(api.get("enable_server", False))
    if out["was_enabled"]:
        return out                      # already on — nothing to do
    api["enable_server"] = True
    try:
        with open(common_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception as exc:
        out["error"] = f"schreiben fehlgeschlagen: {exc}"
        return out
    out["changed"] = True
    return out
