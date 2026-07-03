# SPDX-License-Identifier: GPL-3.0-or-later
"""
MCP server creation and configuration.
"""
import atexit
from collections.abc import Callable
import functools
import logging
import os
import signal

from fastmcp import FastMCP

# Import context management
from kicad_mcp.context import kicad_lifespan

# Central single-source-of-truth for tool / resource / prompt registrars.
# Adding a family there wires it into the server *and* the dynamic tests —
# no inline list to keep in sync (see kicad_mcp/tool_registry.py).
from kicad_mcp.tool_registry import (
    register_all_prompts,
    register_all_resources,
    register_all_tools,
)

# Track cleanup handlers
cleanup_handlers = []

# Flag to track whether we're already in shutdown process
_shutting_down = False

# Store server instance for clean shutdown
_server_instance = None

def add_cleanup_handler(handler: Callable) -> None:
    """Register a function to be called during cleanup.
    
    Args:
        handler: Function to call during cleanup
    """
    cleanup_handlers.append(handler)

def run_cleanup_handlers() -> None:
    """Run all registered cleanup handlers."""
    logging.info("Running cleanup handlers...")

    global _shutting_down

    # Prevent running cleanup handlers multiple times
    if _shutting_down:
        return

    _shutting_down = True
    logging.info("Running cleanup handlers...")

    for handler in cleanup_handlers:
        try:
            handler()
            logging.info(f"Cleanup handler {handler.__name__} completed successfully")
        except Exception as e:
            logging.error(f"Error in cleanup handler {handler.__name__}: {str(e)}", exc_info=True)

def shutdown_server():
    """Properly shutdown the server if it exists."""
    global _server_instance

    if _server_instance:
        try:
            logging.info("Shutting down KiCad MCP server")
            _server_instance = None
            logging.info("KiCad MCP server shutdown complete")
        except Exception as e:
            logging.error(f"Error shutting down server: {str(e)}", exc_info=True)


def register_signal_handlers(server: FastMCP) -> None:
    """Register handlers for system signals to ensure clean shutdown.
    
    Args:
        server: The FastMCP server instance
    """
    def handle_exit_signal(signum, frame):
        logging.info(f"Received signal {signum}, initiating shutdown...")

        # Run cleanup first
        run_cleanup_handlers()

        # Then shutdown server
        shutdown_server()

        # Exit without waiting for stdio processes which might be blocking
        os._exit(0)

    # Register for common termination signals
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_exit_signal)
            logging.info(f"Registered handler for signal {sig}")
        except (ValueError, AttributeError) as e:
            # Some signals may not be available on all platforms
            logging.error(f"Could not register handler for signal {sig}: {str(e)}")


def create_server() -> FastMCP:
    """Create and configure the KiCad MCP server."""
    logging.info("Initializing KiCad MCP server")

    # No in-process KiCad Python (SWIG pcbnew) path setup: external KiCad
    # operations go through kicad-cli, and the read tools lazy-import pcbnew on
    # first use. The flag is kept (read by e.g. bom_tools) and stays False.
    kicad_modules_available = False
    logging.info("Relying on kicad-cli for external KiCad operations.")

    # Build a lifespan callable with the kwarg baked in (FastMCP 2.x dropped lifespan_kwargs)
    lifespan_factory = functools.partial(kicad_lifespan, kicad_modules_available=kicad_modules_available)

    # Initialize FastMCP server
    mcp = FastMCP("KiCad", lifespan=lifespan_factory)
    logging.info("Created FastMCP server instance with lifespan management")

    # Register resources
    logging.info("Registering resources...")
    register_all_resources(mcp)

    # Register tools
    logging.info("Registering tools...")
    register_all_tools(mcp)

    # Register prompts
    logging.info("Registering prompts...")
    register_all_prompts(mcp)

    # Register signal handlers and cleanup
    register_signal_handlers(mcp)
    atexit.register(run_cleanup_handlers)

    # Add specific cleanup handlers
    add_cleanup_handler(lambda: logging.info("KiCad MCP server shutdown complete"))

    # Add temp directory cleanup
    def cleanup_temp_dirs():
        """Clean up any temporary directories created by the server."""
        import shutil

        from kicad_mcp.utils.temp_dir_manager import get_temp_dirs

        temp_dirs = get_temp_dirs()
        logging.info(f"Cleaning up {len(temp_dirs)} temporary directories")

        for temp_dir in temp_dirs:
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logging.info(f"Removed temporary directory: {temp_dir}")
            except Exception as e:
                logging.error(f"Error cleaning up temporary directory {temp_dir}: {str(e)}")

    add_cleanup_handler(cleanup_temp_dirs)

    logging.info("Server initialization complete")
    return mcp


def setup_logging() -> None:
    """Configure logging for the server."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def main() -> None:
    """Start the KiCad MCP server (blocking)."""
    setup_logging()
    logging.info("Starting KiCad MCP server...")

    server = create_server()

    try:
        server.run()  # FastMCP manages its own event loop
    except KeyboardInterrupt:
        logging.info("Server interrupted by user")
    except Exception as e:
        logging.error(f"Server error: {e}")
    finally:
        logging.info("Server shutdown complete")


if __name__ == "__main__":
    main()
