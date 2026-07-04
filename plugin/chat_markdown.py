# SPDX-License-Identifier: GPL-3.0-or-later
"""Markdown → Stil-Segmente für das Chat-Transkript — reine Logik, kein wx.

Claude antwortet in Markdown; das Panel zeigte bisher die Roh-Marker
(``**fett**``, ``# Überschrift``, Backticks). Dieses Modul zerlegt eine
Antwort in ``(text, stil)``-Segmente, die ``chat_dialog`` als gestylte
Spans rendert. Bewusst ein SUBSET: fett, Überschriften, Aufzählungen,
Inline-Code, Codeblöcke, Trennlinien — kein kursiv (``_`` und ``*`` sind
in Netz-/Referenznamen wie ``GND_3V3`` zu häufig, kursiv-Parsing würde
Board-Vokabular zerreißen).

WICHTIG (Board-Links): Das Modul verändert NUR Marker, nie Wortlaut —
``**R12**`` wird zum Segment ``R12``, das der Aufrufer weiterhin durch
``board_links.tokenize`` schickt. Die Linkifizierung läuft also pro
Segment unverändert; Marker-Entfernung macht Referenzen sogar erst
matchbar (der Tokenizer kennt kein ``**R12**``).

Getrennt von ``chat_dialog`` (wx, nur in KiCad importierbar), damit alles
hier headless unit-testbar ist.
"""

from __future__ import annotations

import re

# Stil-Schlüssel — chat_dialog mappt sie auf Farbe/Fett/Hintergrund.
TEXT = "text"            # normaler Antworttext (linkifiziert)
BOLD = "bold"            # **fett** (linkifiziert)
HEADING = "heading"      # #/##/### Zeile (linkifiziert)
CODE = "code"            # `inline code` (linkifiziert — oft Netz-/Ref-Namen)
CODEBLOCK = "codeblock"  # ```…```-Inhalt (NICHT linkifiziert, copy-treu)
RULE = "rule"            # --- Trennlinie (als ─-Linie gerendert)

_FENCE_RE = re.compile(r"^\s*```")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
_RULE_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
# Inline: `code` zuerst (Backtick-Inhalt darf ** enthalten), dann **fett**.
_INLINE_RE = re.compile(r"(`[^`\n]+`)|(\*\*[^*\n]+?\*\*)")

RULE_LINE = "─" * 40


def _inline(line: str, base_style: str) -> list:
    """Eine Zeile in (text, stil)-Segmente zerlegen (Code/Bold-Spans)."""
    out = []
    pos = 0
    for m in _INLINE_RE.finditer(line):
        if m.start() > pos:
            out.append((line[pos:m.start()], base_style))
        if m.group(1):  # `code`
            out.append((m.group(1)[1:-1], CODE))
        else:           # **fett**
            out.append((m.group(2)[2:-2],
                        BOLD if base_style == TEXT else base_style))
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], base_style))
    return out


def parse(text: str) -> list:
    """Markdown-Text → Liste von ``(segment, stil)``-Tupeln, renderfertig.

    Zeilenbasiert; Zeilenumbrüche bleiben in den Segmenten erhalten, sodass
    ``"".join(seg for seg, _ in parse(t))`` den Wortlaut (ohne Marker)
    ergibt. Nie leere Segmente. Use this before writing a Claude reply so
    the transcript shows styled text instead of raw markers.
    """
    out: list = []
    in_fence = False
    for line in (text or "").splitlines(keepends=True):
        bare = line.rstrip("\n")
        if _FENCE_RE.match(bare):
            in_fence = not in_fence  # Fence-Zeile selbst verschwindet
            continue
        if in_fence:
            out.append((line, CODEBLOCK))
            continue
        eol = line[len(bare):]  # "\n" oder "" (letzte Zeile)
        m = _HEADING_RE.match(bare)
        if m:
            out.extend(_inline(m.group(2), HEADING))
            if eol:
                out.append((eol, HEADING))
            continue
        if _RULE_RE.match(bare) and bare.strip():
            out.append((RULE_LINE + eol, RULE))
            continue
        m = _BULLET_RE.match(bare)
        if m:
            out.append((m.group(1) + "• ", TEXT))
            out.extend(_inline(m.group(2), TEXT))
            if eol:
                out.append((eol, TEXT))
            continue
        out.extend(_inline(bare, TEXT))
        if eol:
            out.append((eol, TEXT))
    return [s for s in out if s[0]]
