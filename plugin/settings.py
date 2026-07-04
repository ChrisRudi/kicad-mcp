# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistente Plugin-Einstellungen (GUI-gepflegt, statt Env-Handarbeit).

Ein kleines JSON neben dem Warm-Server-Pidfile (``%LOCALAPPDATA%\\kicad-claude``
bzw. ``~/.local/state/kicad-claude``). Die Einstellungs-Sektion im
Einrichtungs-Fenster schreibt es; ``apply_env()`` übersetzt es beim Panel-Start
in die Env-Variablen, die Server/Bridge ohnehin schon respektieren — Env
gewinnt also weiterhin, wenn der Nutzer sie von Hand setzt (Power-User-Pfad
bleibt intakt).

Pure/stdlib — headless testbar.
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import server_manager

SETTINGS_FILENAME = "settings.json"

# key -> (env var, default). Nur Schlüssel mit gesetztem Wert landen im Env.
ENV_KEYS = {
    "transport": "KICAD_MCP_TRANSPORT",
    "ngspice_path": "KICAD_MCP_NGSPICE",
    "max_turns": "KICAD_MCP_MAX_TURNS",
}

DEFAULTS: dict = {
    "language": "auto",     # auto | de | en (konsumiert i18n, nicht Env)
    "backend": "claude_code",  # Agenten-CLI (siehe plugin/backends.py)
    "transport": "",        # "" = Default des Codes (stdio) nicht anfassen
    "ngspice_path": "",
    "max_turns": 0,          # 0 = Code-Default (80)
}


def settings_path() -> str:
    return os.path.join(server_manager.state_dir(), SETTINGS_FILENAME)


def load() -> dict:
    """Gespeicherte Einstellungen + Defaults (fehlende Keys aufgefüllt)."""
    data: dict = {}
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            data = raw
    except Exception:
        pass
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def save(values: dict) -> str:
    """Nur bekannte Keys persistieren; Rückgabe: Dateipfad."""
    current = load()
    current.update({k: v for k, v in (values or {}).items() if k in DEFAULTS})
    path = settings_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(current, fh, indent=2)
    return path


def apply_env(env: Any = None) -> dict:
    """Einstellungen → Env (nur gesetzte Werte; vorhandene Env gewinnt).

    ``env`` injectable für Tests (Default ``os.environ``). Rückgabe: was
    angewendet wurde.
    """
    if env is None:
        env = os.environ
    values = load()
    applied: dict = {}
    for key, var in ENV_KEYS.items():
        val = values.get(key)
        if val in ("", 0, None):
            continue  # nicht konfiguriert → Code-Default
        if env.get(var):
            continue  # Hand-gesetzte Env hat Vorrang
        env[var] = str(val)
        applied[var] = str(val)
    return applied
