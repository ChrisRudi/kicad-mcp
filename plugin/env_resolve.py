# SPDX-License-Identifier: GPL-3.0-or-later
"""Dynamic dependency resolution — couple ``_deps`` to the *detected* environment.

The runtime deps in ``_deps`` must stay compatible with three independently-
versioned anchors: the running KiCad (kipy/protobuf), the Claude CLI + MCP
protocol (mcp/fastmcp) and KiCad's bundled Python (native ABI). A fixed pin
breaks on a KiCad upgrade; "always latest" breaks when a dep moves ahead of the
user's (older) KiCad/CLI. The sustainable answer is to DERIVE versions from the
detected environment and key the installed set by an *environment fingerprint*,
so any anchor change triggers a re-resolve — including a **downgrade** when the
anchor is older than the latest release.

This module is the pure resolution core. The detection wrappers are thin and
guarded (never raise, injectable runner), so the logic is unit-testable headless.
See ``docs/DESIGN_dependency_resilienz.md`` for the full design.

Scope of this increment: detection + fingerprint + KiCad→kipy coupling (the
Class-A / link-feature anchor) + the reinstall decision. The fingerprint-keyed
clean rebuild (downgrade *execution*) and the handshake self-heal are wired in a
later increment, gated on live-KiCad testing.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from typing import Optional

# --- KiCad version → kipy spec (the Class-A coupling: link feature / live IPC)--
# kicad-python (kipy) MUST match the running KiCad's IPC API version. A single
# constant pin would break on a KiCad upgrade, so this is a DATA map keyed by the
# detected KiCad ``major.minor`` — extend it as new KiCad/kipy pairs ship
# (ideally fed by the self-updater, so a new pair is data, not a code release).
# When the detected KiCad is not in the map we fall back to the unpinned spec
# (today's behaviour) — a later increment adds an empirical handshake walk-back.
KIPY_FOR_KICAD = {
    "10.0": "kicad-python==0.7.1",
}
_KIPY_DEFAULT = "kicad-python"


def kicad_major_minor(version: Optional[str]) -> str:
    """``"10.0.1"`` -> ``"10.0"`` (the granularity the kipy map is keyed on).

    Returns ``""`` for a falsy/garbage version so callers fall back cleanly.
    """
    if not version:
        return ""
    parts = [p for p in str(version).strip().split(".") if p[:1].isdigit()]
    if len(parts) >= 2:
        return parts[0] + "." + parts[1]
    return parts[0] if parts else ""


def kipy_spec(kicad_version: Optional[str]) -> str:
    """The pip spec for kicad-python that matches ``kicad_version``.

    Pinned when the KiCad ``major.minor`` is known-good, else the unpinned spec
    (unchanged behaviour) — it never guesses a wrong pin.
    """
    return KIPY_FOR_KICAD.get(kicad_major_minor(kicad_version), _KIPY_DEFAULT)


def _spec_name(spec: str) -> str:
    """The bare distribution name out of a pip spec (``kicad-python==0.7.1`` ->
    ``kicad-python``). Lower-cased for comparison."""
    head = spec.replace("==", " ").replace(">=", " ").replace("~=", " ").strip()
    return head.split()[0].lower() if head.split() else ""


def resolve_pip_specs(base_specs: list, kicad_version: Optional[str]) -> list:
    """``base_specs`` with the ``kicad-python`` entry replaced by the
    KiCad-matched :func:`kipy_spec`. All other specs pass through unchanged
    (their constraint sets land in a later increment). Order is preserved.
    """
    want = kipy_spec(kicad_version)
    return [want if _spec_name(s) == "kicad-python" else s for s in base_specs]


# --- environment fingerprint --------------------------------------------------

def python_tag() -> str:
    """A stable tag for the running interpreter's ABI surface
    (``cpython311-windows-amd64``-style): the anchor for native wheels."""
    impl = sys.implementation.name
    ver = f"{sys.version_info.major}{sys.version_info.minor}"
    return f"{impl}{ver}-{platform.system().lower()}-{platform.machine().lower()}"


def environment_fingerprint(kicad_version: Optional[str],
                            py_tag: Optional[str],
                            claude_version: Optional[str]) -> str:
    """A short, stable hash of the three anchors. Any anchor change -> a new
    fingerprint -> the installer rebuilds ``_deps`` for the new environment
    (including a downgrade when the new anchor is older than the cached one).
    """
    norm = "|".join([
        kicad_major_minor(kicad_version) or "?",
        (py_tag or python_tag()),
        (claude_version or "?").strip(),
    ])
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def needs_reinstall(recorded_fp: Optional[str], current_fp: str) -> bool:
    """Rebuild ``_deps`` when no fingerprint is recorded yet, or the recorded one
    differs from the current environment (the trigger for both up- and
    downgrade)."""
    return (not recorded_fp) or (recorded_fp != current_fp)


# --- thin, guarded detection (injectable; never raises) -----------------------

def detect_kicad_version() -> Optional[str]:
    """The running KiCad's version via ``pcbnew`` — deliberately kipy-INDEPENDENT
    (so it works even when kipy is mismatched/absent, which is exactly the case
    we resolve for). ``None`` when not running inside KiCad."""
    try:
        import pcbnew  # only available inside KiCad's Python
        return pcbnew.GetBuildVersion()
    except Exception:
        return None


def detect_claude_version(_run=subprocess.run, claude_exe: str = "claude") -> Optional[str]:
    """``claude --version`` (the MCP-client anchor), or ``None`` when the CLI is
    not reachable. The runner is injectable for tests."""
    try:
        proc = _run([claude_exe, "--version"], capture_output=True, text=True,
                    timeout=10, check=False)
    except Exception:
        return None
    out = (getattr(proc, "stdout", "") or "").strip()
    return out or None


def current_fingerprint(_run=subprocess.run) -> str:
    """Detect all three anchors and fingerprint them (convenience for callers)."""
    return environment_fingerprint(detect_kicad_version(), python_tag(),
                                   detect_claude_version(_run=_run))
