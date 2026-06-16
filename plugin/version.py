# SPDX-License-Identifier: GPL-3.0-or-later
"""Single source of truth for the plugin version.

Bumped on every released change to the plugin (and, once the kicad-mcp is
bundled, the bundle as a whole). Shown in the panel/chat titles so "did my
update apply?" is answerable at a glance, and it feeds the PCM metadata.
"""

from __future__ import annotations

__version__ = "0.2.36"
