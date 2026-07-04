# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude-Code-Terminal-Look für das Chat-Panel — reine Daten/Logik, kein wx.

Farben, Rollen-Styles und das Spinner-Label, die das wx-Panel wie die
Claude-Code-CLI aussehen lassen: schwarzer Terminal-Hintergrund, Monospace,
Claude-Orange für Claude-Zeilen, gedimmtes Grau für die eigene Eingabe, Rot
für Fehler. Getrennt von ``chat_dialog`` (wx, nur in KiCad importierbar),
damit alles hier headless unit-testbar ist.
"""

from __future__ import annotations

# -- Palette: Design A „Werkbank" — helles, natives KiCad-Panel ---------------
# Das Panel soll aussehen wie ein eingebautes KiCad-Werkzeug (pcbnew-Chrome):
# helle Fläche, dunkle Systemschrift, KiCad-Blau für Klickbares. Die
# Markenfarbe (warmes Orange) bleibt als sparsamer Akzent (Chevron, Spinner,
# Feature-Tag, Überschriften). Token-Namen bleiben stabil (66 Aufrufstellen).
BACKGROUND = "#FBFCFD"     # Panel-Hintergrund (nahezu weiß, leicht kühl)
SURFACE = "#E3E8ED"        # Eingabefeld / Knöpfe / Flächen — deutlich abgesetzt
FOREGROUND = "#15181D"     # Antworttext (kräftige dunkle Systemschrift)
CLAUDE_ORANGE = "#B24A22"  # warmer Marken-Akzent: Spinner, Chevron, Bullets
LINK = "#1F5FA8"           # KiCad-Blau: klickbare Board-Links (kontraststark)
DIM = "#49505A"            # gedimmt, aber lesbar: Eingabe, Banner, Status
ERROR_RED = "#B21F1F"
OK_GREEN = "#237A3B"       # Ampel „läuft"
CODE_FG = "#134E78"        # Inline-Code & Codeblöcke (dunkles Blau auf SURFACE)

# Gruppenfarben der Super-Feature-Leiste — ein Akzent je Kategorie, damit die
# Leiste scanbar wird. Keys = superfeatures.CATEGORIES-Keys (Guard-Test).
# Kräftig abgedunkelt für lesbaren Kontrast auf hellem Button-Grund.
CATEGORY_COLORS = {
    "verstehen": "#255FA0",   # blau — lesen/prüfen
    "layout": "#6E3FB0",      # violett — Geometrie/Skizze
    "elektrik": "#8A6108",    # gold/braun — Strom/Norm
    "fertigung": "#357A3B",   # grün — Fertigung/Kosten
    "simulation": "#A63A6E",  # magenta — Simulation
    "kreativ": "#127567",     # türkis — Brücken/Kreativ
}

# Markdown-Segment → (Farbe, fett, Hintergrund | None). "text" nimmt die
# Rollenfarbe des Aufrufers (None = einsetzen); Überschriften im Marken-Akzent.
MARKDOWN_STYLES = {
    "text": (None, False, None),
    "bold": (None, True, None),
    "heading": (CLAUDE_ORANGE, True, None),
    "code": (CODE_FG, False, SURFACE),
    "codeblock": (CODE_FG, False, SURFACE),
    "rule": (DIM, False, None),
}

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
