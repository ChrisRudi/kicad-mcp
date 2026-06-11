# SPDX-License-Identifier: GPL-3.0-or-later
"""Provisional self-update straight from the GitHub repo (ChrisRudi/kicad-mcp).

This is the *second* update path — for iterating/testing before the official
KiCad PCM route is ready. It reads the repo's ``plugin/version.py`` to see if a
newer version exists, then downloads the branch zip and overwrites the installed
``plugin/`` files in place. The official PCM channel comes once everything works.

Security: only this one repo over HTTPS, and only the ``plugin/`` subtree is
extracted. Updating code from the network is still code execution — fine for the
maintainer's own repo, but never point this at an untrusted source.

Network calls are injectable (``_get``) so the logic is unit-testable headless.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import zipfile
from typing import Callable, Optional

GITHUB_REPO = "ChrisRudi/kicad-mcp"
GITHUB_BRANCH = os.environ.get("KICAD_MCP_PLUGIN_BRANCH", "main")

RAW_VERSION_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"
    "/plugin/version.py"
)
# The API contents endpoint is near-realtime (raw's CDN lags a push by minutes,
# even with a cache-buster); used as the primary version source, raw as backup.
API_VERSION_URL = (
    f"https://api.github.com/repos/{GITHUB_REPO}/contents/plugin/version.py"
    f"?ref={GITHUB_BRANCH}"
)
ZIPBALL_URL = (
    f"https://codeload.github.com/{GITHUB_REPO}/zip/refs/heads/{GITHUB_BRANCH}"
)

_VERSION_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


def parse_version(text: str) -> Optional[str]:
    """Pull ``__version__`` out of a ``version.py`` source string."""
    m = _VERSION_RE.search(text or "")
    return m.group(1) if m else None


def version_tuple(v: str) -> tuple:
    """``"0.1.0"`` -> ``(0, 1, 0)`` for ordering; non-numeric parts -> -1."""
    out = []
    for tok in (v or "").split("."):
        tok = tok.strip()
        out.append(int(tok) if tok.isdigit() else -1)
    return tuple(out) or (-1,)


def is_newer(remote: str, local: str) -> bool:
    return version_tuple(remote) > version_tuple(local)


def _bust(url: str) -> str:
    """Append a changing cache-buster so the raw-CDN (~5 min cache) doesn't
    serve a stale version right after a push."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}cb={int(time.time())}"


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    import urllib.request
    req = urllib.request.Request(
        url, headers={"User-Agent": "kicad-claude-plugin"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _remote_version_via_api(_get: Callable[[str], bytes]) -> Optional[str]:
    data = json.loads(_get(API_VERSION_URL).decode("utf-8", "replace"))
    content = data.get("content")
    if not content:
        return None
    text = base64.b64decode(content).decode("utf-8", "replace")
    return parse_version(text)


def _remote_version_via_raw(_get: Callable[[str], bytes]) -> Optional[str]:
    return parse_version(_get(_bust(RAW_VERSION_URL)).decode("utf-8", "replace"))


def check_for_update(local_version: str,
                     _get: Callable[[str], bytes] = _http_get) -> dict:
    """Return ``{ok, available, local, remote, error}`` by reading the repo's
    ``version.py`` (API first for freshness, raw as fallback). Never raises —
    network errors come back as ``ok=False``."""
    out = {"ok": False, "available": False, "local": local_version,
           "remote": None, "error": ""}
    remote, errs = None, []
    for source in (_remote_version_via_api, _remote_version_via_raw):
        try:
            remote = source(_get)
        except Exception as exc:
            errs.append(str(exc))
            remote = None
        if remote:
            break
    if not remote:
        out["error"] = "; ".join(errs) or "Konnte Remote-Version nicht lesen."
        return out
    out["ok"] = True
    out["remote"] = remote
    out["available"] = is_newer(remote, local_version)
    return out


def download_zip(_get: Callable[[str], bytes] = _http_get) -> bytes:
    return _get(ZIPBALL_URL)


def apply_update(install_dir: str, zip_bytes: bytes) -> dict:
    """Extract the ``plugin/`` subtree of the repo zip over ``install_dir``.

    Returns ``{updated: [relpaths], error}``. Overwrites in place (Python ``.py``
    files aren't locked while imported on Windows); the new code takes effect on
    the next KiCad restart. Stale/removed files are NOT pruned yet.
    """
    out = {"updated": [], "error": ""}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                parts = name.split("/")
                if "plugin" not in parts:
                    continue
                rel = parts[parts.index("plugin") + 1:]
                if not rel or "__pycache__" in rel:
                    continue
                dest = os.path.join(install_dir, *rel)
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                with zf.open(name) as src, open(dest, "wb") as fh:
                    fh.write(src.read())
                out["updated"].append("/".join(rel))
    except Exception as exc:
        out["error"] = str(exc)
    return out
