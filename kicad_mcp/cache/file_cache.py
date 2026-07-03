# SPDX-License-Identifier: GPL-3.0-or-later
"""file_cache.py — in-memory text cache for .kicad_pcb / .kicad_sch files.

Purpose
    The MCP server is one long-lived process; many tools re-read the same
    board/schematic file from disk on every call. On a OneDrive-synced
    disk a 1.7 MB read costs ~16 ms *every* time (the sync filter +
    UTF-8 decode are a fixed cost that the OS page cache does not hide).
    An ``os.stat`` is ~160x cheaper. This module caches the file text and
    revalidates it with a cheap stat fingerprint (mtime_ns + size), so a
    repeated read of an unchanged file costs a stat + dict lookup instead
    of a full read.

Staleness
    The fingerprint *is* the correctness guard: if the user saves the
    file in the KiCad GUI, mtime_ns changes -> fingerprint mismatch ->
    the next get_text() reads fresh. No separate conflict machinery.

Inputs   file paths (any form; normalized to realpath as the cache key).
Outputs  file text (str).
Deps     stdlib only (os, threading).
"""

import os
import threading

# Cache key -> {"text": str, "fp": (mtime_ns, size)}.
_CACHE: dict[str, dict] = {}
# Cache keys in LRU order (oldest first).
_ORDER: list[str] = []
# Memo: input-path-string -> canonical key. realpath resolution touches
# the filesystem per path component; the mapping is stable for the life
# of the process, so memoizing it keeps a cache hit near pure dict speed.
_KEY_MEMO: dict[str, str] = {}
_LOCK = threading.RLock()
_MAX_ENTRIES = 5
# Bound the path→key memo so a long-lived server that touches many distinct
# absolute paths cannot grow it without limit. On overflow we drop the whole
# memo (it rebuilds lazily and cheaply) rather than track per-entry LRU.
_KEY_MEMO_MAX = 512


def _key(path: str) -> str:
    """Canonical cache key: absolute realpath (symlinks resolved), so a
    relative path, an absolute path and a symlink to the same file all
    share one cache entry. Memoized — realpath is the dominant cost of a
    cache hit. The realpath resolution (which touches the filesystem) runs
    outside the lock; only the small memo dict op is guarded."""
    with _LOCK:
        k = _KEY_MEMO.get(path)
        if k is not None:
            return k
    k = os.path.realpath(os.path.abspath(path))
    # Only memoize absolute inputs — a relative path resolves against the
    # current working directory, which can change between calls.
    if os.path.isabs(path):
        with _LOCK:
            if len(_KEY_MEMO) >= _KEY_MEMO_MAX:
                _KEY_MEMO.clear()
            _KEY_MEMO[path] = k
    return k


def _fingerprint(key: str) -> tuple[int, int]:
    """Cheap on-disk fingerprint: (mtime_ns, size). Raises OSError if the
    file is missing."""
    st = os.stat(key)
    return (st.st_mtime_ns, st.st_size)


def _touch(key: str) -> None:
    """Mark ``key`` as most-recently-used; evict the oldest beyond the
    LRU limit. Caller holds the lock."""
    if key in _ORDER:
        _ORDER.remove(key)
    _ORDER.append(key)
    while len(_ORDER) > _MAX_ENTRIES:
        evicted = _ORDER.pop(0)
        _CACHE.pop(evicted, None)


def get_text(path: str, encoding: str = "utf-8") -> str:
    """Return the file's text, served from cache when the on-disk
    fingerprint is unchanged, otherwise read fresh and cache it.

    Raises OSError if the file does not exist (same as ``open``).
    """
    key = _key(path)
    # Stat outside the lock — it touches no shared state, and a cache hit only
    # needs the dict op under the lock.
    fp = _fingerprint(key)
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is not None and entry["fp"] == fp:
            _touch(key)
            return entry["text"]
    # Miss: read OUTSIDE the lock. On a cold cloud-synced disk this read can be
    # tens of seconds; holding the lock across it would serialize every other
    # cache user against one slow read. Two threads racing the same miss both
    # read and the last store wins (identical bytes for one fingerprint).
    with open(key, encoding=encoding) as fh:
        text = fh.read()
    # Re-stat: the read itself does not change mtime, but a parallel writer
    # might have; capture the fingerprint of what we just read.
    read_fp = _fingerprint(key)
    with _LOCK:
        _CACHE[key] = {"text": text, "fp": read_fp}
        _touch(key)
    return text


def put_text(path: str, text: str) -> None:
    """Register ``text`` that was just written to ``path`` on disk, so the
    next ``get_text`` is a cache hit without a re-read. Call this right
    after a tool writes the file."""
    key = _key(path)
    with _LOCK:
        _CACHE[key] = {"text": text, "fp": _fingerprint(key)}
        _touch(key)


def write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` on disk and register it in the cache.

    The single disk-write chokepoint for the PCB text-patcher: it first runs
    the board-open guard, which BLOCKS the write (``BoardOpenError``) if the
    target .kicad_pcb is open in a running KiCad GUI — preventing the
    file-vs-editor save conflict (use the IPC live tools for an open board).
    Non-PCB files and headless runs are unaffected. Replaces the old
    ``open(...,"w") + put_text(...)`` pair so every tool is guarded centrally.
    """
    from kicad_mcp.utils.board_open_guard import guard_pcb_disk_write
    guard_pcb_disk_write(path)  # raises BoardOpenError if open in the GUI
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)
    put_text(path, text)


def invalidate(path: str | None = None) -> None:
    """Drop a single cached path, or the whole cache when ``path`` is
    None. Idempotent — dropping an absent path is a no-op."""
    with _LOCK:
        if path is None:
            _CACHE.clear()
            _ORDER.clear()
            _KEY_MEMO.clear()
            return
        key = _key(path)
        _CACHE.pop(key, None)
        if key in _ORDER:
            _ORDER.remove(key)


def cache_status() -> list[dict]:
    """Diagnostic snapshot: one dict per cached entry (MRU last) with the
    path, cached character count, and whether it still matches disk."""
    with _LOCK:
        out = []
        for key in _ORDER:
            entry = _CACHE[key]
            try:
                in_sync = _fingerprint(key) == entry["fp"]
            except OSError:
                in_sync = False
            out.append({
                "path": key,
                "chars": len(entry["text"]),
                "in_sync_with_disk": in_sync,
            })
        return out
