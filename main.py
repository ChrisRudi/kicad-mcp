#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
KiCad MCP Server - A Model Context Protocol server for KiCad.
This server allows Claude and other MCP clients to interact with KiCad projects.

Auto-relaunch: If started with a Python that cannot import pcbnew, the script
automatically locates KiCad's bundled Python and re-executes itself under it.
"""
import logging
import os
import sys
import tempfile

# --- Make pip --target-installed server deps importable (umlaut-safe) ---
# KiCad's bundled Python IGNORES PYTHONPATH, and `pip --user` is fragile under
# it, so install.ps1 installs the server + its deps into a sibling "_deps" dir
# via `pip install --target` (passed as argv -> CreateProcessW keeps a
# non-ASCII path like C:\Users\Schüler\… intact). Inject that dir here so the
# imports below resolve without any env-var/PYTHONPATH dance. No-op when the
# dir is absent (e.g. a classic site-packages install), so this is additive.
_deps_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_deps")
if os.path.isdir(_deps_dir) and _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

# --- Setup Logging ---
# Prefer ~/.kicad-mcp/logs/ (deterministic, easy to find). Fall back to tempdir
# if the home directory is not writable.
_log_dir = os.path.join(os.path.expanduser("~"), ".kicad-mcp", "logs")
try:
    os.makedirs(_log_dir, exist_ok=True)
    log_file = os.path.join(_log_dir, 'kicad-mcp.log')
except OSError:
    log_file = os.path.join(tempfile.gettempdir(), 'kicad-mcp.log')

log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(levelname)s - [PID:%(process)d] - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, mode='a'),
        logging.StreamHandler(sys.stderr),
    ]
)
print(f"[kicad-mcp] log file: {log_file}", file=sys.stderr)
# ---------------------


_MIN_KICAD_MAJOR = 10


def _ensure_kicad_version_at_least(major: int = _MIN_KICAD_MAJOR) -> None:
    """Refuse to start if the bundled KiCad install is older than ``major``.

    Pre-KiCad-10 installs lack the IPC API that the bridging tools depend
    on (and ship a markedly different SWIG/pcbnew surface). Rather than
    failing later with cryptic import-errors, we fail loudly here.

    Resolution: parse ``kicad-cli version`` — works without needing pcbnew
    imported, so this can run before the relaunch decision settles.
    """
    import re
    import shutil
    import subprocess

    cli = ""
    try:
        # path_env handles WSL <-> Windows path translation and walks the
        # 10+-only candidate list defined in _KICAD_CANDIDATES.
        from kicad_mcp.utils.path_env import kicad_cli as _resolve_kicad_cli
        cli = _resolve_kicad_cli() or ""
    except Exception:
        cli = ""
    if not cli:
        cli = shutil.which("kicad-cli") or shutil.which("kicad-cli.exe") or ""
    if not cli:
        # CLI not locatable; defer the check — _ensure_kicad_python will
        # surface a clearer error in the relaunch path.
        return

    try:
        out = subprocess.run(
            [cli, "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        logging.warning(f"kicad-cli version probe failed: {exc}")
        return

    text = (out.stdout or "") + "\n" + (out.stderr or "")
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b", text)
    if not match:
        logging.warning(f"Could not parse kicad-cli version from {text!r}")
        return

    found_major = int(match.group(1))
    if found_major < major:
        msg = (
            f"KiCad {match.group(0)} detected, but kicad-mcp requires "
            f"KiCad {major}+. The IPC API the server depends on does not "
            "exist in earlier releases. Install KiCad 10 or set "
            "KICAD_PYTHON_PATH / KICAD_CLI_PATH to a 10+ binary."
        )
        logging.error(msg)
        print(f"[kicad-mcp] {msg}", file=sys.stderr)
        sys.exit(1)
    logging.info(f"KiCad version OK: {match.group(0)}")


def _ensure_kicad_python():
    """Re-exec under KiCad's Python if pcbnew is not importable.

    Uses importlib.util.find_spec() instead of `import pcbnew` so that the
    ~25s pcbnew-initialization is deferred to first PCB-tool call. This
    keeps MCP client health-check timeouts (~10s) satisfied.
    """
    import importlib.util
    relaunched = bool(os.environ.get("_KICAD_MCP_RELAUNCHED"))

    if importlib.util.find_spec("pcbnew") is not None:
        logging.info(
            f"pcbnew module found in {sys.executable} "
            "(lazy-import deferred to first use)"
        )
        return

    # pcbnew not in path. If the launcher flag is set, the launcher script
    # ran the wrong Python — do NOT silently continue, the user must know.
    if relaunched:
        logging.error(
            "_KICAD_MCP_RELAUNCHED=1 but pcbnew module not found on import path. "
            "The launcher script ran a Python without KiCad bindings "
            f"(sys.executable={sys.executable}). "
            "Check start_mcp.bat / start_mcp_wsl.sh and KICAD_PYTHON_PATH."
        )
        return

    # pcbnew not available — find KiCad's Python
    from kicad_mcp.utils.find_kicad_python import find_kicad_python

    kicad_python = find_kicad_python()
    if not kicad_python:
        logging.warning(
            "KiCad Python not found — continuing without pcbnew. "
            "Set KICAD_PYTHON_PATH or KICAD_INSTALL_DIR to fix this."
        )
        return

    if os.path.abspath(kicad_python) == os.path.abspath(sys.executable):
        logging.warning("Already running KiCad Python but pcbnew still not importable")
        return

    # Re-exec: replace this process with KiCad's Python running the same script
    script = os.path.abspath(__file__)
    env = os.environ.copy()
    env["_KICAD_MCP_RELAUNCHED"] = "1"

    logging.info(f"Re-launching under KiCad Python: {kicad_python}")
    logging.info(f"  args: {kicad_python} {script}")

    if sys.platform == "win32":
        # On Windows, os.execv doesn't work well — use subprocess instead
        import subprocess
        result = subprocess.run(
            [kicad_python, script],
            env=env,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            check=False,
        )
        sys.exit(result.returncode)
    else:
        os.execve(kicad_python, [kicad_python, script], env)


# Load .env BEFORE importing config so that config reads the correct env vars
from kicad_mcp.utils.env import load_dotenv  # noqa: E402

dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
logging.info(f"Attempting to load .env file from: {dotenv_path}")
found_dotenv = load_dotenv(dotenv_path)
logging.info(f".env file found and loaded: {found_dotenv}")

# Now import config — it will see the env vars set by load_dotenv
from kicad_mcp import config  # noqa: E402
from kicad_mcp.server import main as server_main  # noqa: E402

logging.info("--- Server Starting ---")
logging.info(f"KICAD_USER_DIR: {config.KICAD_USER_DIR}")
logging.info(f"ADDITIONAL_SEARCH_PATHS: {config.ADDITIONAL_SEARCH_PATHS}")

if __name__ == "__main__":
    # Refuse to start on pre-KiCad-10 installs (no IPC API).
    _ensure_kicad_version_at_least()

    # Auto-detect and relaunch under KiCad Python if needed
    _ensure_kicad_python()

    try:
        logging.info("Starting KiCad MCP server process")

        if config.ADDITIONAL_SEARCH_PATHS:
            logging.info(f"Additional search paths: {', '.join(config.ADDITIONAL_SEARCH_PATHS)}")
        else:
            logging.info("No additional search paths configured")

        logging.info(f"Python: {sys.executable}")
        logging.info("Running server with stdio transport")
        server_main()
    except Exception:
        logging.exception("Unhandled exception in main")
        raise
