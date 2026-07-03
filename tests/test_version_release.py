# SPDX-License-Identifier: GPL-3.0-or-later
"""Release-Wächter — the shipped version must move when the tool set changes.

The KiCad plugin self-updater (``plugin/updater.py``) only offers an update when
the remote ``plugin/version.py::__version__`` is strictly newer than the
installed one. So if new tools ship without bumping ``__version__``, users never
get them — exactly the failure this test guards against.

The coupling: adding a tool forces bumping ``EXPECTED_TOOL_COUNT`` (enforced by
``test_tool_audit.test_tool_count_locked``). This test ties that same count to
``version.py::__tool_count__`` — so the count change also breaks *here* until
``version.py`` is edited, and the failure message tells you to bump
``__version__`` while you're there. It also keeps ``VERSIONS.md`` honest.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_tool_audit import EXPECTED_TOOL_COUNT

import plugin.version as pv

_VERSIONS_MD = Path(__file__).resolve().parents[1] / "plugin" / "VERSIONS.md"


def test_version_is_clean_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", pv.__version__), (
        f"__version__ {pv.__version__!r} must be N.N.N so updater.version_tuple "
        "orders it correctly.")


def test_tool_count_coupled_to_version():
    assert pv.__tool_count__ == EXPECTED_TOOL_COUNT, (
        f"plugin/version.py __tool_count__ is {pv.__tool_count__} but the "
        f"registry has {EXPECTED_TOOL_COUNT} tools. You changed the tool set: "
        "update __tool_count__ AND bump __version__ in plugin/version.py so the "
        "plugin self-updater actually delivers the change to users.")


def test_versions_md_pointer_matches():
    text = _VERSIONS_MD.read_text(encoding="utf-8")
    m = re.search(r"Aktuelle Version:\s*\*\*([0-9.]+)\*\*", text)
    assert m, "VERSIONS.md must carry an 'Aktuelle Version: **X.Y.Z**' line."
    assert m.group(1) == pv.__version__, (
        f"VERSIONS.md pointer {m.group(1)} != version.py {pv.__version__}. "
        "Bump the pointer and log the release in VERSIONS.md.")
