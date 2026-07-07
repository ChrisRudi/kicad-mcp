# SPDX-License-Identifier: GPL-3.0-or-later
"""Single source of truth for the version — plugin *and* packaged wheel.

Bumped on every released change. ``pyproject.toml`` reads ``__version__`` from
here (``[tool.hatch.version] path = "plugin/version.py"``), so one bump moves the
GUI plugin and the ``kicad-mcp`` distribution together. Shown in the panel/chat
titles so "did my update apply?" is answerable at a glance, and it feeds the PCM
metadata.
"""

from __future__ import annotations

__version__ = "0.34.1"

# Number of MCP tools shipped by this version. Coupled to the registry via
# ``tests/test_version_release.py``: adding/removing a tool bumps
# ``tests/test_tool_audit.EXPECTED_TOOL_COUNT``, which then mismatches this
# number until it is updated here too — and while you are in this file you MUST
# bump ``__version__`` as well, or the plugin self-updater (updater.py compares
# ``__version__`` remote-vs-local) will never offer the new tools to users. This
# is the guard that stops "shipped new tools but forgot to bump the version".
__tool_count__ = 189
