# SPDX-License-Identifier: GPL-3.0-or-later
"""Single source of truth for the version — plugin *and* packaged wheel.

Bumped on every released change. ``pyproject.toml`` reads ``__version__`` from
here (``[tool.hatch.version] path = "plugin/version.py"``), so one bump moves the
GUI plugin and the ``kicad-mcp`` distribution together. Shown in the panel/chat
titles so "did my update apply?" is answerable at a glance, and it feeds the PCM
metadata.
"""

from __future__ import annotations

__version__ = "0.4.2"
