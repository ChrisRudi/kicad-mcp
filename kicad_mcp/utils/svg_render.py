# SPDX-License-Identifier: GPL-3.0-or-later
"""SVG->PNG rendering via cairosvg, with KiCad's native cairo DLLs made
discoverable and cairosvg auto-installed on first use.

Extracted from ``tools/cli_export_tools.py`` so both the export tools and
``generators/review`` share one implementation instead of importing a private
name across the tool layer. Windows-specific DLL bootstrap is a no-op elsewhere.
"""
import os
import subprocess


def ensure_cairo_dll_searchable() -> None:
    """Make KiCad's ``cairo-2.dll`` discoverable by cairocffi.

    cairocffi looks for libraries named ``libcairo-2.dll`` /
    ``libcairo.so.2`` / ``libcairo.2.dylib`` — it does NOT try the
    ``cairo-2.dll`` filename that KiCad ships. Workaround:

    1. Locate KiCad's ``cairo-2.dll`` (in ``bin/`` next to the CLI).
    2. Mirror it under ``~/.kicad-mcp/native_libs/libcairo-2.dll``.
    3. Register that directory via :func:`os.add_dll_directory` so the
       OS resolver finds it when cairocffi performs ``dlopen``.

    Idempotent — only copies if the mirror is missing or out of date.
    Safe no-op outside Windows.
    """
    if os.name != "nt":
        return
    add_dll = getattr(os, "add_dll_directory", None)
    if not callable(add_dll):
        return
    try:
        from kicad_mcp.utils.path_env import kicad_paths

        cli = kicad_paths().get("kicad_cli", "")
    except Exception:
        cli = ""
    bin_dir = os.path.dirname(cli) if cli else ""
    if not bin_dir or not os.path.isdir(bin_dir):
        return
    src = os.path.join(bin_dir, "cairo-2.dll")
    if not os.path.isfile(src):
        return
    mirror_dir = os.path.join(
        os.path.expanduser("~"), ".kicad-mcp", "native_libs"
    )
    os.makedirs(mirror_dir, exist_ok=True)
    dst = os.path.join(mirror_dir, "libcairo-2.dll")
    try:
        if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
            import shutil as _sh

            _sh.copy2(src, dst)
    except OSError:
        # Mirror-Copy fehlgeschlagen — ggf. tut es ein älterer Mirror noch
        pass
    try:
        add_dll(mirror_dir)
    except OSError:
        # Mirror-Dir nicht registrierbar — cairocffi sucht dann nur im PATH
        pass
    try:
        # Also register KiCad's bin directory itself for future native deps.
        add_dll(bin_dir)
    except OSError:
        # bin_dir nicht registrierbar — für cairo zählt der Mirror oben
        pass


def ensure_cairosvg():
    """Import cairosvg, auto-installing it on first failure and registering
    KiCad's ``cairo-2.dll`` directory as a DLL-search location so cairocffi
    can find the native cairo library. Idempotent.
    """
    ensure_cairo_dll_searchable()
    try:
        import cairosvg  # type: ignore
        return cairosvg
    except ImportError:
        # cairosvg fehlt noch — unten folgt die Auto-Installation
        pass
    import importlib
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "cairosvg"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "cairosvg auto-install failed. "
            f"pip stderr: {(proc.stderr or '').strip()[:300]}"
        )
    ensure_cairo_dll_searchable()
    return importlib.import_module("cairosvg")


def svg_to_png(svg_path: str, scale: float = 2.0) -> bytes:
    """Convert an SVG file to PNG bytes using cairosvg.

    Auto-installs cairosvg into the running interpreter on first call if
    the package is missing — no manual ``pip install`` step needed.
    """
    cairosvg = ensure_cairosvg()
    try:
        return cairosvg.svg2png(url=svg_path, scale=scale)
    except OSError as e:
        if "cairo" in str(e).lower():
            raise RuntimeError(
                f"Cairo library not found: {e}. "
                f"Set KICAD_INSTALL_DIR to your KiCad installation."
            ) from e
        raise

