# SPDX-License-Identifier: GPL-3.0-or-later
"""In-memory file-text cache for kicad-mcp.

Eliminates redundant disk reads of the same ``.kicad_pcb`` / ``.kicad_sch``
file across MCP tool calls. See :mod:`kicad_mcp.cache.file_cache`.
"""

from kicad_mcp.cache.file_cache import (
    cache_status,
    get_text,
    invalidate,
    put_text,
)

__all__ = ["get_text", "put_text", "invalidate", "cache_status"]
