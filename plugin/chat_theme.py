# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude-Code-Terminal-Look für das Chat-Panel — reine Daten/Logik, kein wx.

Farben, Rollen-Styles und das Spinner-Label, die das wx-Panel wie die
Claude-Code-CLI aussehen lassen: schwarzer Terminal-Hintergrund, Monospace,
Claude-Orange für Claude-Zeilen, gedimmtes Grau für die eigene Eingabe, Rot
für Fehler. Getrennt von ``chat_dialog`` (wx, nur in KiCad importierbar),
damit alles hier headless unit-testbar ist.
"""

from __future__ import annotations

# -- Palette: dunkles Terminal + Claude-Markenorange --------------------------
BACKGROUND = "#1F1E1D"     # Fensterhintergrund (warmes Terminal-Schwarz)
SURFACE = "#2A2826"        # Eingabefeld / abgesetzte Flächen
FOREGROUND = "#E8E6E3"     # Claude-Antworttext (helles Off-White)
CLAUDE_ORANGE = "#D97757"  # Markenfarbe: Spinner, Bullets, Prompt-Chevron
DIM = "#8A8782"            # gedimmt: eigene Eingabe, Banner, Status "Bereit."
ERROR_RED = "#E5484D"

# Monospace-Kandidaten, beste zuerst; der Dialog nimmt den ersten installierten.
FONT_FACES = ("Cascadia Code", "Cascadia Mono", "Consolas", "JetBrains Mono",
              "Menlo", "DejaVu Sans Mono")
FONT_SIZE_PT = 10

# -- Chat-Rollen: Glyph-Prefix + Farben, wie die CLI-Bullets ------------------
ROLE_STYLES = {
    # eigene Eingabe: Chevron wie die CLI-Eingabezeile, Text gedimmt
    "user": {"prefix": "❯ ", "prefix_color": CLAUDE_ORANGE, "text_color": DIM},
    # Claude-Antwort: Bullet in Markenorange, Text hell
    "claude": {"prefix": "● ", "prefix_color": CLAUDE_ORANGE,
               "text_color": FOREGROUND},
    "error": {"prefix": "✗ ", "prefix_color": ERROR_RED,
              "text_color": ERROR_RED},
    # Begrüßung/Meta: pulsierender Stern, gedimmt
    "banner": {"prefix": "✻ ", "prefix_color": CLAUDE_ORANGE,
               "text_color": DIM},
}


def style_for(role: str) -> dict:
    """Style-Dict (``prefix``/``prefix_color``/``text_color``) für eine
    Chat-Rolle; unbekannte Rollen fallen auf den Claude-Style zurück."""
    return ROLE_STYLES.get(role, ROLE_STYLES["claude"])


# -- Statuszeile ---------------------------------------------------------------
STATUS_READY = "Bereit."
STATUS_BUSY = "Claude denkt nach …"
SPINNER_FRAMES = ("·", "✢", "✳", "✶", "✻", "✶", "✳", "✢")
SPINNER_INTERVAL_MS = 150


def spinner_label(tick: int, interval_ms: int = SPINNER_INTERVAL_MS) -> str:
    """Die Statuszeile während Claude arbeitet — pulsierender Stern plus
    verstrichene Sekunden, wie in der CLI ("✻ Claude denkt nach … (12s)").
    ``tick`` ist der wievielte Timer-Schlag (Abstand ``interval_ms``)."""
    frame = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
    seconds = tick * interval_ms // 1000
    return f"{frame} {STATUS_BUSY} ({seconds}s)"
