# SPDX-License-Identifier: GPL-3.0-or-later
"""KiCad documentation / shortcut lookup tools.

Pure data-driven: a curated JSON index of the most-asked KiCad actions
per major version (menu paths in EN + DE, default keyboard shortcuts,
description, search keywords). The lookup tool does fuzzy matching
across action IDs, menu-path words, descriptions, and keywords so the
caller can ask either "fill zones" or "alle zonen füllen" or even
"b key" and get the right hit.

Version handling:
* The local KiCad install is detected via ``kicad-cli --version``.
* The data file ``data/actions/kicad{major}.json`` for the matching
  major version is loaded.
* If the exact version's file is missing, the tool falls back to the
  closest available major (preferring the same-or-newer first, then
  older).
* Callers may pass ``kicad_version=<major>`` (int) to override the
  auto-detect.

Update the JSON file when KiCad rebinds keys upstream — the schema is
documented in the file's ``_meta`` block.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.path_env import detect_environment, to_local_path

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "actions",
)
_DEFAULT_MAJOR = 10  # fall-through if version detection AND file-search both fail


def _detect_kicad_major_version() -> tuple[int, str]:
    """Return ``(major, raw_version)`` of the installed kicad-cli.

    Falls back to ``(_DEFAULT_MAJOR, "")`` if kicad-cli is not found
    or the version string can't be parsed.
    """
    try:
        from kicad_mcp.utils.path_env import kicad_cli  # local import
    except Exception:
        return _DEFAULT_MAJOR, ""
    cli = kicad_cli()
    if not cli:
        return _DEFAULT_MAJOR, ""
    try:
        out = subprocess.run(
            [cli, "--version"],
            capture_output=True, timeout=5, check=False,
        )
        text = (out.stdout + out.stderr).decode("utf-8", "replace").strip()
        # Accept "10.0.1", "KiCad CLI utility 10.0.1", "11.0.0-rc1" etc.
        m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
        if m:
            return int(m.group(1)), text
        return _DEFAULT_MAJOR, text
    except Exception:
        return _DEFAULT_MAJOR, ""


def _available_majors() -> list[int]:
    """Return the sorted list of major versions for which we ship a
    data file (``data/actions/kicad{N}.json``)."""
    out: list[int] = []
    if not os.path.isdir(_DATA_DIR):
        return out
    for name in os.listdir(_DATA_DIR):
        m = re.match(r"kicad(\d+)\.json$", name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def _resolve_data_path(major: int) -> tuple[str, int]:
    """Find the actions file for ``major`` (or the closest available).

    Strategy:
        1. Exact ``kicad{major}.json``.
        2. Older same-family: largest ``v ≤ major``.
        3. Newer same-family: smallest ``v > major``.
        4. ``FileNotFoundError`` if no files at all.

    Returns ``(path, resolved_major)``.
    """
    candidates = _available_majors()
    if not candidates:
        raise FileNotFoundError(
            f"No KiCad actions data files in {_DATA_DIR}. "
            "Add kicad{N}.json to enable lookup_kicad_action."
        )
    # Exact
    if major in candidates:
        return os.path.join(_DATA_DIR, f"kicad{major}.json"), major
    # Largest ≤ major
    older = [v for v in candidates if v < major]
    if older:
        v = max(older)
        return os.path.join(_DATA_DIR, f"kicad{v}.json"), v
    # Smallest > major
    newer = [v for v in candidates if v > major]
    if newer:
        v = min(newer)
        return os.path.join(_DATA_DIR, f"kicad{v}.json"), v
    # Should be unreachable
    raise FileNotFoundError(
        f"No data file found for major={major}; have {candidates}"
    )


_CACHE: dict[int, dict[str, Any]] = {}


def _load_actions(major: int | None = None) -> tuple[dict[str, Any], int, int, str]:
    """Load the actions index for the requested major. Returns
    ``(data, requested_major, resolved_major, raw_version_string)``.

    When ``major`` is ``None`` the local install is auto-detected. The
    parsed data is cached per resolved major.
    """
    if major is None:
        detected_major, raw = _detect_kicad_major_version()
    else:
        detected_major, raw = int(major), ""
    path, resolved = _resolve_data_path(detected_major)
    if resolved not in _CACHE:
        with open(path, encoding="utf-8") as fh:
            _CACHE[resolved] = json.load(fh)
    return _CACHE[resolved], detected_major, resolved, raw


def _search_corpus(action: dict[str, Any]) -> str:
    parts: list[str] = [action["id"]]
    parts.extend(action.get("menu_path_en", []))
    parts.extend(action.get("menu_path_de", []))
    parts.append(action.get("description_en", ""))
    parts.append(action.get("description_de", ""))
    parts.extend(action.get("keywords", []))
    parts.append(action.get("shortcut_default", ""))
    return " ".join(p.lower() for p in parts if p)


def _score(query: str, action: dict[str, Any]) -> float:
    q = query.lower().strip()
    if not q:
        return 0.0
    corpus = _search_corpus(action)
    score = 0.0
    keywords = [k.lower() for k in action.get("keywords", [])]
    if q in keywords:
        score += 5.0
    if any(q in k for k in keywords):
        score += 2.0
    if q == action["id"]:
        score += 10.0
    if q in action["id"]:
        score += 1.5
    menu_words = " ".join(
        action.get("menu_path_en", []) + action.get("menu_path_de", [])
    ).lower()
    for word in q.split():
        if word in menu_words:
            score += 1.0
        if word in corpus:
            score += 0.5
    sc = action.get("shortcut_default", "").lower()
    if sc and (q == sc or q == sc.replace("ctrl+", "")):
        score += 4.0
    score += difflib.SequenceMatcher(None, q, corpus).ratio() * 0.5
    return score


# ---------------------------------------------------------------------------
# user.hotkeys discovery + parsing
# ---------------------------------------------------------------------------


def _wsl_user_appdata_dir() -> str:
    """Best-effort Windows %APPDATA% path from inside WSL.

    KiCad on Windows stores ``user.hotkeys`` under
    ``%APPDATA%/kicad/<version>/`` which from WSL is reachable as
    ``/mnt/c/Users/<user>/AppData/Roaming/``. Windows env vars are usually
    NOT propagated into WSL shells, so we reconstruct the path from the
    Linux ``$USER`` / ``$LOGNAME``. Returns ``""`` if we cannot make a
    reasonable guess.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return to_local_path(appdata)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return to_local_path(os.path.join(userprofile, "AppData", "Roaming"))
    for var in ("USER", "LOGNAME"):
        name = os.environ.get(var)
        if name:
            cand = f"/mnt/c/Users/{name}/AppData/Roaming"
            if os.path.isdir(cand):
                return cand
    return ""


def _candidate_user_hotkeys_paths(major: int) -> list[str]:
    """Build the ordered list of paths to probe for ``user.hotkeys``.

    Order: explicit env override → per-platform default for the active
    runtime (Windows / WSL / Linux / macOS). The caller picks the first
    one that exists.
    """
    version_dir = f"{major}.0"
    out: list[str] = []

    # 1. Explicit override env var (custom user-config dir).
    override = os.environ.get("KICAD_USER_CONFIG_PATH", "").strip()
    if override:
        p = to_local_path(override)
        # Allow caller to point at the dir OR the file itself.
        if p.endswith("user.hotkeys"):
            out.append(p)
        else:
            out.append(os.path.join(p, "user.hotkeys"))
            out.append(os.path.join(p, version_dir, "user.hotkeys"))

    env = detect_environment()
    if env == "windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            out.append(os.path.join(appdata, "kicad", version_dir, "user.hotkeys"))
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            out.append(os.path.join(
                userprofile, "AppData", "Roaming",
                "kicad", version_dir, "user.hotkeys",
            ))
    elif env == "wsl":
        # KiCad on Windows is the common case; check %APPDATA% first.
        roaming = _wsl_user_appdata_dir()
        if roaming:
            out.append(os.path.join(roaming, "kicad", version_dir, "user.hotkeys"))
        # Also probe the Linux-side XDG dir in case the user runs KiCad
        # natively inside WSL via a Linux package.
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        home = os.environ.get("HOME", "")
        if xdg:
            out.append(os.path.join(xdg, "kicad", version_dir, "user.hotkeys"))
        if home:
            out.append(os.path.join(home, ".config", "kicad", version_dir, "user.hotkeys"))
    elif env == "darwin":
        home = os.environ.get("HOME", "")
        if home:
            out.append(os.path.join(
                home, "Library", "Preferences",
                "kicad", version_dir, "user.hotkeys",
            ))
    else:  # linux
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        home = os.environ.get("HOME", "")
        if xdg:
            out.append(os.path.join(xdg, "kicad", version_dir, "user.hotkeys"))
        if home:
            out.append(os.path.join(home, ".config", "kicad", version_dir, "user.hotkeys"))

    # De-dup while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


# Cache: {(abs_path, mtime_ns): parsed_actions_list}
_HOTKEYS_CACHE: dict[tuple[str, int], list[dict[str, str]]] = {}


def _parse_user_hotkeys(path: str) -> list[dict[str, str]]:
    """Parse ``user.hotkeys`` into ``[{id, shortcut, secondary, namespace}, …]``.

    Format is tab-separated: ``<action_id>\\t<primary>\\t<secondary>``.
    Section-header lines (action_id with empty trailing columns, e.g.
    ``3DViewer.Control``) are kept iff they look like a real action id
    (containing a dot) — that matches the upstream KiCad behaviour where
    every menu-callable action has a dotted id. Truly blank / malformed
    lines are dropped silently.

    Results are cached on ``(path, mtime_ns)`` so repeat calls don't
    re-scan 880 lines.
    """
    try:
        st = os.stat(path)
    except OSError as exc:
        raise FileNotFoundError(f"user.hotkeys not readable: {path}: {exc}") from exc
    key = (path, st.st_mtime_ns)
    cached = _HOTKEYS_CACHE.get(key)
    if cached is not None:
        return cached
    out: list[dict[str, str]] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n").rstrip("\r")
            if not line.strip():
                continue
            parts = line.split("\t")
            # Pad to exactly three columns.
            while len(parts) < 3:
                parts.append("")
            action_id = parts[0].strip()
            if not action_id or "." not in action_id:
                continue
            shortcut = parts[1].strip()
            secondary = parts[2].strip()
            namespace = action_id.split(".", 1)[0]
            out.append({
                "id": action_id,
                "shortcut": shortcut,
                "secondary": secondary,
                "namespace": namespace,
            })
    _HOTKEYS_CACHE[key] = out
    return out


def register_docs_tools(mcp: FastMCP) -> None:
    """Register documentation / shortcut lookup tools."""

    @mcp.tool()
    def lookup_kicad_action(
        query: str,
        editor: str = "all",
        lang: str = "de",
        max_results: int = 5,
        kicad_version: int = 0,
    ) -> dict[str, Any]:
        """Look up KiCad menu path + keyboard shortcut from a free-text
        query, version-aware against the locally installed KiCad.

        The data set is loaded per **major** KiCad version
        (``data/actions/kicad{N}.json``). On first call the tool runs
        ``kicad-cli --version`` to detect the local install and loads
        the matching file — or the closest available if that major
        version isn't shipped yet.

        Query forms:
            * English / German menu word ("fill zones", "alle zonen
              füllen", "rastereinstellungen"),
            * keyboard shortcut ("Ctrl+B", "F8", "B"),
            * action id ("pcb.edit.fill_all_zones"),
            * any keyword from the action's tag list.

        Args:
            query: Free-text query.
            editor: ``"all"`` (default), ``"schematic"``, ``"pcb"``.
                ``schematic``/``pcb`` entries are filtered; ``editor:"all"``
                actions are always included.
            lang: ``"de"`` (default) / ``"en"`` — primary description.
            max_results: Max hits to return (default 5).
            kicad_version: Force a specific KiCad major version
                (``10``, ``11``, …). ``0`` (default) = auto-detect from
                ``kicad-cli --version``.

        Returns:
            ``{success, query, hits: [...], total_actions_indexed,
            kicad_version_detected, kicad_version_used,
            kicad_version_raw}``.
        """
        if editor not in ("all", "schematic", "pcb"):
            return {
                "success": False,
                "error": (
                    f"editor must be all / schematic / pcb, got {editor!r}"
                ),
            }
        if lang not in ("de", "en"):
            return {
                "success": False,
                "error": f"lang must be de or en, got {lang!r}",
            }
        try:
            data, requested, resolved, raw_ver = _load_actions(
                kicad_version if kicad_version > 0 else None,
            )
        except FileNotFoundError as exc:
            return {"success": False, "error": str(exc)}

        actions = data.get("actions", [])
        if editor == "all":
            pool = actions
        else:
            pool = [
                a for a in actions if a.get("editor") in (editor, "all")
            ]
        if not query.strip():
            return {
                "success": True,
                "query": query,
                "hits": [],
                "total_actions_indexed": len(actions),
                "kicad_version_detected": requested,
                "kicad_version_used": resolved,
                "kicad_version_raw": raw_ver,
                "note": "Empty query — no fuzzy match attempted.",
            }
        scored = sorted(
            ((_score(query, a), a) for a in pool),
            key=lambda t: t[0],
            reverse=True,
        )
        hits = []
        for score, a in scored[: max(1, max_results)]:
            if score < 0.3:
                continue
            desc = a.get(f"description_{lang}", "") or a.get(
                "description_en", "",
            )
            hits.append({
                "id": a["id"],
                "editor": a.get("editor", "all"),
                "shortcut": a.get("shortcut_default", ""),
                "menu_path_de": " → ".join(a.get("menu_path_de", [])),
                "menu_path_en": " → ".join(a.get("menu_path_en", [])),
                "description": desc,
                "score": round(score, 3),
                "keywords": a.get("keywords", []),
            })
        return {
            "success": True,
            "query": query,
            "editor_filter": editor,
            "hits": hits,
            "total_actions_indexed": len(actions),
            "kicad_version_detected": requested,
            "kicad_version_used": resolved,
            "kicad_version_raw": raw_ver,
            "fallback_used": requested != resolved,
        }

    @mcp.tool()
    def list_kicad_actions(
        editor: str = "all",
        lang: str = "de",
        kicad_version: int = 0,
    ) -> dict[str, Any]:
        """List every indexed KiCad action for the active version
        (alphabetically by id). Version-aware just like
        :func:`lookup_kicad_action`.

        Use this to browse the action catalogue when you do not yet
        know the action id — e.g. before composing a ``RunAction`` IPC
        call or before suggesting a GUI menu path to the user. Prefer
        :func:`lookup_kicad_action` instead when you already know the
        id and only want the menu path / shortcut for a single action;
        this tool is intentionally returning the full catalogue (~600
        entries) and is heavier to scan.

        Args:
            editor: ``"all"`` (default), ``"schematic"``, ``"pcb"``.
            lang: ``"de"`` or ``"en"``.
            kicad_version: Force a specific major version (``0`` =
                auto-detect).

        Returns:
            ``{success, count, actions: [...], kicad_version_used}``.
        """
        if editor not in ("all", "schematic", "pcb"):
            return {
                "success": False,
                "error": (
                    f"editor must be all / schematic / pcb, got {editor!r}"
                ),
            }
        try:
            data, requested, resolved, raw_ver = _load_actions(
                kicad_version if kicad_version > 0 else None,
            )
        except FileNotFoundError as exc:
            return {"success": False, "error": str(exc)}
        actions = data.get("actions", [])
        if editor != "all":
            actions = [
                a for a in actions if a.get("editor") in (editor, "all")
            ]
        actions_sorted = sorted(actions, key=lambda a: a["id"])
        out = []
        for a in actions_sorted:
            primary_path = a.get(f"menu_path_{lang}", []) or a.get(
                "menu_path_en", [],
            )
            out.append({
                "id": a["id"],
                "editor": a.get("editor", "all"),
                "shortcut": a.get("shortcut_default", ""),
                "menu_path": " → ".join(primary_path),
            })
        return {
            "success": True,
            "editor_filter": editor,
            "lang": lang,
            "count": len(out),
            "kicad_version_detected": requested,
            "kicad_version_used": resolved,
            "kicad_version_raw": raw_ver,
            "actions": out,
        }

    @mcp.tool()
    def list_user_hotkeys(
        kicad_version: int = 0,
        namespace: str = "",
        only_bound: bool = False,
        summary: bool = False,
        config_path: str = "",
    ) -> dict[str, Any]:
        """List every KiCad action and its currently bound keyboard
        shortcut from the local ``user.hotkeys`` file (~880 actions total
        in KiCad 10).

        Use this when ``lookup_kicad_action`` / ``list_kicad_actions``
        come up empty: those two are backed by a hand-curated 50-action
        index covering the most-asked menu items, while this tool reads
        the *complete* per-user shortcut map KiCad writes to disk —
        every menu action, every Python-action, every plugin that
        registered a hotkey. Includes actions that have no shortcut
        bound (set ``only_bound=True`` to hide them).

        Path resolution priority:
          1. Explicit ``config_path`` argument (override).
          2. ``$KICAD_USER_CONFIG_PATH`` env var.
          3. Per-platform default
             (``%APPDATA%/kicad/<v>/user.hotkeys`` on Windows /
             WSL — WSL falls back to ``$USER`` / ``$LOGNAME`` to
             reconstruct ``/mnt/c/Users/<u>/AppData/Roaming/`` —
             ``${XDG_CONFIG_HOME:-$HOME/.config}/kicad/<v>/`` on
             Linux, ``$HOME/Library/Preferences/kicad/<v>/`` on macOS).

        The parsed result is cached per ``(path, mtime)`` so repeat
        calls don't re-scan the file.

        Args:
            kicad_version: Force a specific KiCad major version (e.g.
                ``10`` selects ``.../kicad/10.0/user.hotkeys``).
                ``0`` (default) auto-detects via ``kicad-cli --version``.
            namespace: Filter to a single action namespace such as
                ``"pcbnew"``, ``"eeschema"``, ``"common"``,
                ``"3DViewer"``, ``"gerbview"``, ``"kicad"`` or
                ``"plEditor"``. Empty string (default) returns all.
            only_bound: If ``True``, drop actions whose primary shortcut
                is empty. Default ``False`` (return everything).
            summary: If ``True``, return per-namespace action/bound COUNTS
                instead of the full action list (the full dump is ~20k
                tokens). Default ``False``.
            config_path: Explicit path to a ``user.hotkeys`` file,
                bypassing all env / default detection. WSL and Windows
                paths are both accepted.

        Returns:
            On success::

                {
                  "success": True,
                  "config_path_used": "/mnt/c/.../user.hotkeys",
                  "kicad_version_detected": 10,
                  "namespace_filter": "pcbnew",
                  "only_bound_filter": False,
                  "total_actions": 257,
                  "actions": [
                    {"id": "pcbnew.EditorControl.boardSetup",
                     "shortcut": "Ctrl+,", "secondary": "",
                     "namespace": "pcbnew"},
                    …
                  ],
                  "namespaces_available": ["3DViewer", "common", …]
                }

            On failure::

                {"success": False, "error": "...",
                 "tried_paths": ["/mnt/c/.../user.hotkeys", …]}
        """
        # Detect major version (or use override) for path construction.
        if kicad_version > 0:
            major = int(kicad_version)
            raw_ver = ""
        else:
            major, raw_ver = _detect_kicad_major_version()

        # Resolve which file to open.
        if config_path:
            resolved = to_local_path(config_path)
            tried = [resolved]
            if not os.path.isfile(resolved):
                return {
                    "success": False,
                    "error": f"user.hotkeys not found at explicit path: {resolved}",
                    "tried_paths": tried,
                }
        else:
            tried = _candidate_user_hotkeys_paths(major)
            resolved = ""
            for cand in tried:
                if os.path.isfile(cand):
                    resolved = cand
                    break
            if not resolved:
                return {
                    "success": False,
                    "error": (
                        "user.hotkeys not found. Pass config_path=... or set "
                        "$KICAD_USER_CONFIG_PATH. See tried_paths for the "
                        "locations probed."
                    ),
                    "tried_paths": tried,
                    "kicad_version_detected": major,
                }

        # Parse (cached on (path, mtime)).
        try:
            actions = _parse_user_hotkeys(resolved)
        except (FileNotFoundError, OSError) as exc:
            return {
                "success": False,
                "error": str(exc),
                "tried_paths": [resolved],
            }

        # Build namespaces list before filtering so callers see the full set.
        namespaces_available = sorted({a["namespace"] for a in actions})

        # Apply filters.
        filtered = actions
        if namespace:
            filtered = [a for a in filtered if a["namespace"] == namespace]
        if only_bound:
            filtered = [a for a in filtered if a["shortcut"]]

        if summary:
            # Counts-only overview (the full ~880-action dump is ~20k tokens).
            by_ns: dict[str, dict[str, int]] = {}
            for a in filtered:
                d = by_ns.setdefault(a["namespace"], {"actions": 0, "bound": 0})
                d["actions"] += 1
                if a["shortcut"]:
                    d["bound"] += 1
            return {
                "success": True,
                "config_path_used": resolved,
                "kicad_version_detected": major,
                "namespace_filter": namespace,
                "only_bound_filter": only_bound,
                "summary": True,
                "total_actions": len(filtered),
                "bound_actions": sum(1 for a in filtered if a["shortcut"]),
                "by_namespace": by_ns,
                "namespaces_available": namespaces_available,
            }

        return {
            "success": True,
            "config_path_used": resolved,
            "kicad_version_detected": major,
            "kicad_version_raw": raw_ver,
            "namespace_filter": namespace,
            "only_bound_filter": only_bound,
            "total_actions": len(filtered),
            "actions": filtered,
            "namespaces_available": namespaces_available,
        }
