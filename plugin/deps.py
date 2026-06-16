# SPDX-License-Identifier: GPL-3.0-or-later
"""Check / install the bundled kicad-mcp server's RUNTIME dependencies.

The server imports ``fastmcp`` / ``mcp`` / ``pandas`` / ``yaml`` / ``defusedxml``
/ ``jsonschema`` — none of which ship in KiCad's bundled Python. We probe them
fast with ``importlib.util.find_spec`` in a subprocess (KiCad's Python), and
install into a **plugin-local target dir** (``<plugin>/_deps``) that goes onto
``PYTHONPATH`` explicitly. ``pip --user`` turned out fragile under KiCad's
bundled Python ("Installation klappt, Server startet trotzdem nicht"): the
user-site dir is shared with other CPython installs (version clashes) and is
not guaranteed on sys.path in every embed configuration. A dir the plugin OWNS
and wires up itself removes that whole failure class — and needs no admin.

Pure logic (command builders + an injectable runner); unit-testable headless.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from .claude_bridge import hidden_console_kwargs

# Import names to probe (note: pyyaml imports as ``yaml``).
IMPORT_NAMES = ["fastmcp", "mcp", "pandas", "yaml", "defusedxml", "jsonschema"]
# pip specs to install (no brackets -> no cross-shell quoting headaches;
# fastmcp pulls mcp transitively, mcp listed too to be safe).
PIP_SPECS = ["fastmcp", "mcp", "pandas", "pyyaml", "defusedxml", "jsonschema"]

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

def _check_code(deps_dir: Optional[str] = None) -> str:
    """The ``-c`` probe: inject ``deps_dir`` into ``sys.path`` IN-PROCESS, then
    ``find_spec`` each module.

    KiCad's bundled Python IGNORES the ``PYTHONPATH`` env var (isolated
    ``._pth`` build) — so a probe that only set PYTHONPATH found nothing even
    when ``_deps`` was fully populated, and the panel reported the deps as
    "fehlt" right after a successful install (then re-installed in a loop).
    In-process ``sys.path`` insertion is the same mechanism the server actually
    starts with (``mcp_config.server_bootstrap_code``), so the check now agrees
    with reality.
    """
    inject = (f"sys.path[:0]={[deps_dir] + pywin32_path_entries(deps_dir)!r};"
              if deps_dir else "")
    return (
        "import importlib.util,sys;"
        f"{inject}"
        f"req={IMPORT_NAMES!r};"
        "miss=[m for m in req if importlib.util.find_spec(m) is None];"
        "print(','.join(miss));"
        "sys.exit(1 if miss else 0)"
    )


# Name of the env var that carries the (possibly non-ASCII) target dir into the
# install terminal. On Windows the .bat references it as %KICAD_MCP_DEPS% instead
# of inlining the path, so a username like "üser" can't be folded to "?" by
# cmd.exe's codepage (-> WinError 123). See plugin.terminal module docstring.
DEPS_ENV_VAR = "KICAD_MCP_DEPS"


def default_target_dir() -> str:
    """Where the plugin installs the server deps (``<plugin>/_deps``)."""
    return os.path.join(PLUGIN_DIR, "_deps")


def active_deps_dir() -> Optional[str]:
    """The plugin-local deps dir if it exists, else None (rely on site dirs —
    keeps earlier ``pip --user`` installs working)."""
    d = default_target_dir()
    return d if os.path.isdir(d) else None


def pywin32_path_entries(deps_dir: Optional[str]) -> list:
    """sys.path dirs that pywin32's ``.pth`` would add (``win32``,
    ``win32/lib``). A ``pip install --target`` never executes that ``.pth``, so
    without these dirs (and the DLL dir from ``pywin32_dll_setup_code``) mcp's
    eager ``import pywintypes`` fails with ``ModuleNotFoundError`` even though
    pywin32 IS installed in ``_deps``. No-op for a falsy dir."""
    if not deps_dir:
        return []
    return [os.path.join(deps_dir, "win32"),
            os.path.join(deps_dir, "win32", "lib")]


def pywin32_dll_setup_code(deps_dir: Optional[str]) -> str:
    """A Python statement (for the ``-c`` bootstraps) registering pywin32's
    ``pywin32_system32`` DLL dir via ``os.add_dll_directory`` — what
    ``pywin32_bootstrap`` (invoked from the ``.pth``) normally does so
    ``pywintypes311.dll`` loads. No-op when the dir/API is absent (non-Windows,
    or a deps dir without pywin32). The path is embedded via ``repr`` so a
    non-ASCII ``_deps`` (``C:\\Users\\üser\\…``) stays a valid literal."""
    if not deps_dir:
        return ""
    w = os.path.join(deps_dir, "pywin32_system32")
    return (f"import os as _o; _w={w!r}; "
            "_o.path.isdir(_w) and hasattr(_o, 'add_dll_directory') "
            "and _o.add_dll_directory(_w); ")


def build_check_cmd(kicad_py: str, deps_dir: Optional[str] = None) -> list:
    return [kicad_py, "-c", _check_code(deps_dir)]


def check_deps(kicad_py: Optional[str], _run=subprocess.run,
               deps_dir: Optional[str] = None) -> dict:
    """Return ``{ok, missing, error}`` — which runtime deps KiCad's Python lacks.

    Probes with the plugin-local deps dir injected into ``sys.path`` IN-PROCESS
    (exactly how the MCP server starts) — NOT via PYTHONPATH, which KiCad's
    bundled Python ignores. Fast (find_spec, no full import). Never raises.
    """
    out = {"ok": False, "missing": [], "error": ""}
    if not kicad_py:
        out["error"] = "KiCad-Python nicht gefunden"
        return out
    env = dict(os.environ)
    deps_dir = active_deps_dir() if deps_dir is None else deps_dir
    if deps_dir:
        # belt-and-suspenders for pythons that DO honor it; the real path
        # injection happens in-process via build_check_cmd(deps_dir).
        env["PYTHONPATH"] = deps_dir
    try:
        proc = _run(build_check_cmd(kicad_py, deps_dir), capture_output=True,
                    text=True, timeout=30, check=False, env=env,
                    **hidden_console_kwargs())
    except Exception as exc:
        out["error"] = str(exc)
        return out
    miss = [m for m in (proc.stdout or "").strip().split(",") if m]
    out["missing"] = miss
    out["ok"] = getattr(proc, "returncode", 1) == 0 and not miss
    return out


def pip_install_env(target: Optional[str] = None) -> dict:
    """The env vars to hand ``terminal.open_terminal(env=...)`` so the install
    terminal can resolve the target dir without inlining it into the .bat.

    Pairs with ``pip_install_commands``: on Windows those commands reference
    ``%KICAD_MCP_DEPS%`` (this var), which Windows passes UTF-16 to the child —
    immune to the codepage folding that turns ``üser`` into ``Sch?ler``.
    """
    return {DEPS_ENV_VAR: target or default_target_dir()}


def pip_install_commands(kicad_py: str, target: Optional[str] = None) -> list:
    """The command lines for a visible terminal (see plugin.terminal):
    install into the plugin-local ``--target`` dir (no admin, no user-site).

    Self-diagnosing: shows which Python runs, bootstraps pip via ensurepip
    when the bundle ships without it, and VERIFIES after the install that
    every module actually imports from the target dir — so "Installation
    klappt scheinbar" and "Server startet" can't diverge silently anymore.

    NOTE: prefer ``pip_install_argv`` + ``verify_import_argv`` (run directly via
    subprocess, no shell at all) — that is the path the setup dialog uses now and
    sidesteps the codepage problem entirely. This terminal-based variant is kept
    as a legacy fallback; on Windows it references the target through
    ``%KICAD_MCP_DEPS%`` (set via ``pip_install_env``) rather than inlining it, so
    a non-ASCII path survives cmd.exe's codepage; POSIX shells handle UTF-8 paths
    directly, so there the literal is used. The caller MUST pass
    ``env=pip_install_env()``.
    """
    target = target or default_target_dir()
    # Windows: reference the env var (uncorruptible). POSIX: literal is safe.
    ref = f"%{DEPS_ENV_VAR}%" if os.name == "nt" else target
    pkgs = " ".join(PIP_SPECS)
    q = f'"{kicad_py}"'
    verify = (
        "import sys,os;"
        f"sys.path[:0]=[r'{ref}',os.path.join(r'{ref}','win32'),"
        f"os.path.join(r'{ref}','win32','lib')];"
        f"_w=os.path.join(r'{ref}','pywin32_system32');"
        "os.path.isdir(_w) and hasattr(os,'add_dll_directory') "
        "and os.add_dll_directory(_w);"
        f"import {','.join(IMPORT_NAMES)};"
        "print('OK - alle MCP-Module importierbar')"
    )
    return [
        f"echo Python: {kicad_py}",
        f"echo Ziel-Ordner (_deps): {ref}",
        f"{q} --version",
        # Some bundles ship without pip -> bootstrap it (no admin needed).
        f"{q} -m pip --version || {q} -m ensurepip --user",
        f'{q} -m pip install --upgrade --target "{ref}" {pkgs}',
        f'{q} -c "{verify}"',
    ]


def pip_install_argv(kicad_py: str, target: Optional[str] = None) -> list:
    """The pip-install command as an argv LIST (NOT a shell string).

    Run directly via ``subprocess`` so a non-ASCII ``--target`` path (e.g.
    ``C:\\Users\\üser\\…\\_deps``) is passed to pip as proper unicode via
    Windows' CreateProcessW — a cmd.exe/batch round-trip mangles the ``ü`` to
    ``?`` (an invalid path char → ``WinError 123``).
    """
    target = target or default_target_dir()
    return [kicad_py, "-m", "pip", "install", "--upgrade",
            "--target", target, *PIP_SPECS]


def verify_import_argv(kicad_py: str, target: Optional[str] = None) -> list:
    """argv that imports every MCP dep from ``target`` and prints an OK line —
    catches "install said success but imports still fail". Unicode-safe: the
    path is embedded via ``repr`` (a valid Python string literal)."""
    target = target or default_target_dir()
    paths = [target] + pywin32_path_entries(target)
    code = ("import sys; sys.path[:0] = " + repr(paths) + "; "
            + pywin32_dll_setup_code(target)
            + "import " + ", ".join(IMPORT_NAMES) + "; "
            "print('OK - alle MCP-Module importierbar')")
    return [kicad_py, "-c", code]
