# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared warm-worker daemon client.

Manages one long-lived pcbnew worker subprocess over a stdin/stdout pipe:
spawn on first use, forward newline-delimited JSON requests, read the
matching response (framed by a ``mark`` prefix so stray pcbnew chatter
can't be mistaken for a reply), and recycle the process when its pcbnew
state is spent.

Used by both the warm-board ``pcb_eval`` session (``pcb_session_tools``)
and the warm connectivity check (``connectivity_tools``); each instantiates
its own ``WarmDaemon`` pointing at its own worker file. Extracting the
client here keeps the two in lock-step (recycle policy, broken-pipe retry,
stale-response skipping) instead of drifting as two copies.

Recycle policy — the process is killed (next request respawns a fresh one)
when a response is flagged ``mutated`` (a what-if poisoned the in-memory
board / SWIG state), carries a ``SwigPyObject`` (KiCad's SWIG degradation
after repeated LoadBoard in one interpreter), or once ``loads`` reaches the
cap. The board cache lives in the worker, so a recycle only costs a reload
on the next call.
"""
import json
import queue
import subprocess
import sys
import threading


class WarmDaemon:
    """Manages one warm worker subprocess (spawn / pipe / request / respawn).

    Args:
        worker_path: filesystem path to the standalone worker .py (run by
            file path, never ``-m``, so the package ``__init__`` is not paid).
        mark: line prefix the worker stamps on every response so chatter on
            the shared stdout stream is ignored.
        max_loads: recycle the process once a response reports this many
            fresh board loads (SWIG-degradation safety).
    """

    def __init__(self, worker_path: str, mark: str, max_loads: int = 25) -> None:
        self.worker_path = worker_path
        self.mark = mark
        self.max_loads = max_loads
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue | None = None
        self.lock = threading.Lock()
        self._next_id = 0

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _reader(self, proc: subprocess.Popen, q: queue.Queue) -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                if line.startswith(self.mark):
                    q.put(line[len(self.mark):].strip())
        except Exception:
            pass

    def _spawn(self) -> None:
        self._kill()
        self.proc = subprocess.Popen(
            [sys.executable, self.worker_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self.q = queue.Queue()
        threading.Thread(target=self._reader, args=(self.proc, self.q), daemon=True).start()

    def _kill(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None
        self.q = None

    def request(self, req: dict, timeout: float) -> dict:
        with self.lock:
            if not self._alive():
                try:
                    self._spawn()
                except Exception as e:
                    return {"ok": False, "error": f"could not start worker daemon: {e}"}
            self._next_id += 1
            rid = self._next_id
            req = {**req, "id": rid}
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")  # type: ignore[union-attr]
                self.proc.stdin.flush()                        # type: ignore[union-attr]
            except Exception:
                # broken pipe → respawn once and retry
                try:
                    self._spawn()
                    self.proc.stdin.write(json.dumps(req) + "\n")  # type: ignore[union-attr]
                    self.proc.stdin.flush()                        # type: ignore[union-attr]
                except Exception as e:
                    return {"ok": False, "error": f"worker daemon write failed: {e}"}
            deadline_q = self.q
            while True:
                try:
                    line = deadline_q.get(timeout=timeout)  # type: ignore[union-attr]
                except queue.Empty:
                    self._kill()  # hung worker → recycle; next call respawns
                    return {"ok": False, "error": f"request timed out after {timeout}s (daemon recycled)"}
                try:
                    resp = json.loads(line)
                except Exception:
                    continue
                if resp.get("id") == rid:
                    if (resp.get("mutated")
                            or "SwigPyObject" in json.dumps(resp)
                            or (isinstance(resp.get("loads"), int) and resp["loads"] >= self.max_loads)):
                        self._kill()
                    return resp
                # else: stale response from a prior (timed-out) request — skip
