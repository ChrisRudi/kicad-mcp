# SPDX-License-Identifier: GPL-3.0-or-later
"""
Configuration settings for the KiCad MCP server.

This module provides platform-specific configuration for KiCad integration,
including file paths, extensions, component libraries, and operational constants.
All settings are determined at import time based on the operating system.

Module Variables:
    system (str): Operating system name from platform.system()
    KICAD_USER_DIR (str): User's KiCad documents directory
    KICAD_APP_PATH (str): KiCad application installation path
    ADDITIONAL_SEARCH_PATHS (List[str]): Additional project search locations
    DEFAULT_PROJECT_LOCATIONS (List[str]): Common project directory patterns
    KICAD_PYTHON_BASE (str): KiCad Python framework base path (macOS only)
    KICAD_EXTENSIONS (Dict[str, str]): KiCad file extension mappings
    DATA_EXTENSIONS (List[str]): Recognized data file extensions
    CIRCUIT_DEFAULTS (Dict[str, Union[float, List[float]]]): Default circuit parameters
    COMMON_LIBRARIES (Dict[str, Dict[str, Dict[str, str]]]): Component library mappings
    DEFAULT_FOOTPRINTS (Dict[str, List[str]]): Default footprint suggestions per component
    TIMEOUT_CONSTANTS (Dict[str, float]): Operation timeout values in seconds
    PROGRESS_CONSTANTS (Dict[str, int]): Progress reporting percentage values
    DISPLAY_CONSTANTS (Dict[str, int]): UI display configuration values

Platform Support:
    - macOS (Darwin): Full support with application bundle paths
    - Windows: Standard installation paths
    - Linux: System package paths
    - Unknown: Defaults to macOS paths for compatibility

Dependencies:
    - os: File system operations and environment variables
    - platform: Operating system detection
"""

import logging
import os
import platform
import re

logger = logging.getLogger(__name__)

# Determine operating system for platform-specific configuration
# Returns 'Darwin' (macOS), 'Windows', 'Linux', or other
system = platform.system()

# Platform-specific KiCad installation and user directory paths
# These paths are used for finding KiCad resources and user projects
if system == "Darwin":  # macOS
    _default_user_dir = os.path.expanduser("~/Documents/KiCad")
    KICAD_APP_PATH = "/Applications/KiCad/KiCad.app"
elif system == "Windows":
    _default_user_dir = os.path.expanduser("~/Documents/KiCad")
    KICAD_APP_PATH = r"C:\Program Files\KiCad"
elif system == "Linux":
    _default_user_dir = os.path.expanduser("~/KiCad")
    KICAD_APP_PATH = "/usr/share/kicad"
else:
    _default_user_dir = os.path.expanduser("~/Documents/KiCad")
    KICAD_APP_PATH = "/Applications/KiCad/KiCad.app"

# Allow KICAD_USER_DIR to be overridden via environment variable (e.g. for WSL)
KICAD_USER_DIR = os.environ.get("KICAD_USER_DIR", _default_user_dir)


# ---------------------------------------------------------------------------
# Auto-detect KiCad installation directory and ensure its bin/ is on PATH.
#
# Many tools (kicad-cli, cairo-2.dll, pcbnew, etc.) live in this directory.
# By resolving it once at config time and injecting it into PATH, every
# downstream module can rely on these binaries being discoverable.
# ---------------------------------------------------------------------------

def _win_to_wsl(win_path: str) -> str | None:
    """Convert a Windows path (C:\\...) to WSL mount path (/mnt/c/...)."""
    m = re.match(r"^([A-Za-z]):\\(.*)$", win_path)
    if not m:
        return None
    return f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}"


def _detect_kicad_install_dir() -> str:
    """Find the KiCad installation directory.

    Priority:
      1. KICAD_INSTALL_DIR environment variable
      2. Platform-specific well-known paths (newest version first)
    """
    env = os.environ.get("KICAD_INSTALL_DIR", "")
    if env:
        # On WSL the env var may hold a Windows path — normalise
        if system == "Linux" and not os.path.isdir(env):
            wsl = _win_to_wsl(env)
            if wsl and os.path.isdir(wsl):
                return wsl
        if os.path.isdir(env):
            return env

    candidates: list[str] = []
    if system == "Windows":
        candidates = [
            r"C:\Program Files\KiCad\10.0",
            r"C:\Program Files\KiCad\9.0",
            r"C:\Program Files\KiCad",
            r"C:\Program Files (x86)\KiCad\10.0",
        ]
    elif system == "Darwin":
        candidates = [
            "/Applications/KiCad/KiCad.app",
        ]
    else:  # Linux / WSL
        candidates = [
            "/mnt/c/Program Files/KiCad/10.0",
            "/mnt/c/Program Files/KiCad/9.0",
            "/usr/share/kicad",
            "/opt/kicad",
        ]

    for c in candidates:
        if os.path.isdir(c):
            return c

    return ""


KICAD_INSTALL_DIR = _detect_kicad_install_dir()

# Resolve the bin/ directory that contains kicad-cli, python, DLLs, etc.
if KICAD_INSTALL_DIR:
    _bin_candidate = os.path.join(KICAD_INSTALL_DIR, "bin")
    if system == "Darwin":
        _bin_candidate = os.path.join(KICAD_INSTALL_DIR, "Contents", "MacOS")
    KICAD_BIN_DIR: str = _bin_candidate if os.path.isdir(_bin_candidate) else ""
else:
    KICAD_BIN_DIR = ""

# Ensure KICAD_BIN_DIR is on PATH so all tools can find kicad-cli, DLLs, etc.
if KICAD_BIN_DIR and KICAD_BIN_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = KICAD_BIN_DIR + os.pathsep + os.environ.get("PATH", "")
    logger.info("Added KiCad bin to PATH: %s", KICAD_BIN_DIR)

# Additional search paths from environment variable KICAD_SEARCH_PATHS
# Users can specify custom project locations as comma-separated paths
ADDITIONAL_SEARCH_PATHS = []
env_search_paths = os.environ.get("KICAD_SEARCH_PATHS", "")
if env_search_paths:
    for path in env_search_paths.split(","):
        expanded_path = os.path.expanduser(path.strip())  # Expand ~ and variables
        if os.path.exists(expanded_path):  # Only add existing directories
            ADDITIONAL_SEARCH_PATHS.append(expanded_path)

# Auto-detect common project locations for convenient project discovery
# These are typical directory names users create for electronics projects
DEFAULT_PROJECT_LOCATIONS = [
    "~/Documents/PCB",  # Common Windows/macOS location
    "~/PCB",  # Simple home directory structure
    "~/Electronics",  # Generic electronics projects
    "~/Projects/Electronics",  # Organized project structure
    "~/Projects/PCB",  # PCB-specific project directory
    "~/Projects/KiCad",  # KiCad-specific project directory
]

# Add existing default locations to search paths
# Avoids duplicates and only includes directories that actually exist
for location in DEFAULT_PROJECT_LOCATIONS:
    expanded_path = os.path.expanduser(location)
    if os.path.exists(expanded_path) and expanded_path not in ADDITIONAL_SEARCH_PATHS:
        ADDITIONAL_SEARCH_PATHS.append(expanded_path)

# Base path to KiCad's Python framework for API access
# macOS bundles Python framework within the application
if system == "Darwin":  # macOS
    KICAD_PYTHON_BASE = os.path.join(
        KICAD_APP_PATH, "Contents/Frameworks/Python.framework/Versions"
    )
else:
    # Linux/Windows use system Python or require dynamic detection
    KICAD_PYTHON_BASE = ""  # Will be determined dynamically in python_path.py


# KiCad file extension mappings for project file identification
# Used by file discovery and validation functions
KICAD_EXTENSIONS = {
    "project": ".kicad_pro",
    "pcb": ".kicad_pcb",
    "schematic": ".kicad_sch",
    "design_rules": ".kicad_dru",
    "worksheet": ".kicad_wks",
    "footprint": ".kicad_mod",
    "netlist": "_netlist.net",
    "kibot_config": ".kibot.yaml",
}

# Additional data file extensions that may be part of KiCad projects
# Includes manufacturing files, component data, and export formats
DATA_EXTENSIONS = [
    ".csv",  # BOM or other data
    ".pos",  # Component position file
    ".net",  # Netlist files
    ".zip",  # Gerber files and other archives
    ".drl",  # Drill files
]

# Default parameters for circuit creation and component placement
# Values in mm unless otherwise specified, following KiCad conventions
CIRCUIT_DEFAULTS = {
    "grid_spacing": 1.0,  # Default grid spacing in mm for user coordinates
    "component_spacing": 10.16,  # Default component spacing in mm
    "wire_width": 6,  # Default wire width in KiCad units (0.006 inch)
    "text_size": [1.27, 1.27],  # Default text size in mm
    "pin_length": 2.54,  # Default pin length in mm
}

# Predefined component library mappings for quick circuit creation
# Maps common component types to their KiCad library and symbol names
# Organized by functional categories: basic, power, connectors
COMMON_LIBRARIES = {
    "basic": {
        "resistor": {"library": "Device", "symbol": "R"},
        "capacitor": {"library": "Device", "symbol": "C"},
        "inductor": {"library": "Device", "symbol": "L"},
        "led": {"library": "Device", "symbol": "LED"},
        "diode": {"library": "Device", "symbol": "D"},
    },
    "power": {
        "vcc": {"library": "power", "symbol": "VCC"},
        "gnd": {"library": "power", "symbol": "GND"},
        "+5v": {"library": "power", "symbol": "+5V"},
        "+3v3": {"library": "power", "symbol": "+3V3"},
        "+12v": {"library": "power", "symbol": "+12V"},
        "-12v": {"library": "power", "symbol": "-12V"},
    },
    "connectors": {
        "conn_2pin": {"library": "Connector", "symbol": "Conn_01x02_Male"},
        "conn_4pin": {"library": "Connector_Generic", "symbol": "Conn_01x04"},
        "conn_8pin": {"library": "Connector_Generic", "symbol": "Conn_01x08"},
    },
}

# Suggested footprints for common components, ordered by preference
# SMD variants listed first, followed by through-hole alternatives
DEFAULT_FOOTPRINTS = {
    "R": [
        "Resistor_SMD:R_0805_2012Metric",
        "Resistor_SMD:R_0603_1608Metric",
        "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
    ],
    "C": [
        "Capacitor_SMD:C_0805_2012Metric",
        "Capacitor_SMD:C_0603_1608Metric",
        "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm",
    ],
    "LED": ["LED_SMD:LED_0805_2012Metric", "LED_THT:LED_D5.0mm"],
    "D": ["Diode_SMD:D_SOD-123", "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal"],
}

# Operation timeout values in seconds for external process management
# Prevents hanging operations and provides user feedback
TIMEOUT_CONSTANTS = {
    "kicad_cli_version_check": 10.0,  # Timeout for KiCad CLI version checks
    "kicad_cli_export": 30.0,  # Timeout for KiCad CLI export operations
    "application_open": 10.0,  # Timeout for opening applications (e.g., KiCad)
    "subprocess_default": 30.0,  # Default timeout for subprocess operations
    # DRC is *not* like a quick export: kicad-cli pcb drc loads the board and
    # runs the full rule check + connectivity, which is minutes on large boards
    # (KiCad issue #17434), and a cold read on a cloud-synced disk alone costs
    # ~80 s. A fixed short timeout would kill legitimate work, so the DRC budget
    # is generous and *scales with board size* (see ``drc_timeout_seconds``).
    "drc_base": 300.0,        # floor: small board, worst-case cold read + check
    "drc_per_mb": 45.0,       # added per MB of .kicad_pcb (grows with complexity)
    "drc_max": 1800.0,        # hard ceiling — only to catch a true infinite hang
}

# Env override for the DRC timeout. An explicit positive number wins over the
# size-adaptive budget; ``0`` / ``none`` / ``off`` disables the timeout entirely
# (power users who accept the hang risk for an enormous board).
DRC_TIMEOUT_ENV = "KICAD_MCP_DRC_TIMEOUT_S"


def drc_timeout_seconds(pcb_path: str | None = None) -> float | None:
    """Resolve the DRC subprocess timeout in seconds.

    Precedence:
      1. ``KICAD_MCP_DRC_TIMEOUT_S`` — an explicit positive number is used
         verbatim; ``0`` / ``none`` / ``off`` returns ``None`` (no timeout).
      2. Otherwise a size-adaptive budget: ``drc_base + size_mb * drc_per_mb``,
         clamped to ``drc_max``. A missing/unstattable path falls back to
         ``drc_base`` so the caller still gets a sane finite budget.

    Returns the timeout in seconds, or ``None`` for "no timeout".
    """
    raw = os.environ.get(DRC_TIMEOUT_ENV, "").strip().lower()
    if raw:
        if raw in ("0", "none", "off"):
            return None
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass  # malformed override → fall through to the adaptive budget

    base = TIMEOUT_CONSTANTS["drc_base"]
    size_mb = 0.0
    if pcb_path:
        try:
            size_mb = os.path.getsize(pcb_path) / (1024 * 1024)
        except OSError:
            size_mb = 0.0
    budget = base + size_mb * TIMEOUT_CONSTANTS["drc_per_mb"]
    return min(budget, TIMEOUT_CONSTANTS["drc_max"])

# Progress percentage milestones for long-running operations
# Provides consistent progress reporting across different tools
PROGRESS_CONSTANTS = {
    "start": 10,  # Initial progress percentage
    "detection": 20,  # Progress after CLI detection
    "setup": 30,  # Progress after setup complete
    "processing": 50,  # Progress during processing
    "finishing": 70,  # Progress when finishing up
    "validation": 90,  # Progress during validation
    "complete": 100,  # Progress when complete
}

# User interface display configuration values
# Controls how much information is shown in previews and summaries
DISPLAY_CONSTANTS = {
    "bom_preview_limit": 20,  # Maximum number of BOM items to show in preview
}
