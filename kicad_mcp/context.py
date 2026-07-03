# SPDX-License-Identifier: GPL-3.0-or-later
"""
Lifespan context management for KiCad MCP Server.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging

from mcp.server.fastmcp import FastMCP


@dataclass
class KiCadAppContext:
    """Type-safe context for KiCad MCP server.

    Only ``kicad_modules_available`` is carried here (read e.g. by
    ``bom_tools``). The real caching lives in dedicated layers —
    ``cache/file_cache.py`` (board/schematic text) and the warm-board daemons
    (``tools/_warm_daemon.py``); there is deliberately no generic context cache,
    so state has one obvious home instead of two.
    """
    kicad_modules_available: bool


@asynccontextmanager
async def kicad_lifespan(server: FastMCP, kicad_modules_available: bool = False) -> AsyncIterator[KiCadAppContext]:
    """Manage KiCad MCP server lifecycle with type-safe context.

    Args:
        server: The FastMCP server instance
        kicad_modules_available: Flag indicating if Python modules were found
            (passed from create_server)

    Yields:
        KiCadAppContext: A typed context object shared across all handlers
    """
    logging.info("Starting KiCad MCP server initialization")
    logging.info("KiCad Python module availability: %s", kicad_modules_available)

    try:
        logging.info("KiCad MCP server initialization complete")
        yield KiCadAppContext(kicad_modules_available=kicad_modules_available)
    finally:
        logging.info("KiCad MCP server shutdown complete")
