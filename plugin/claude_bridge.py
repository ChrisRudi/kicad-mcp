# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge: drive the Claude Code CLI headlessly from the KiCad plugin.

Each chat message becomes one ``claude -p`` (print-mode) invocation that loads
the bundled kicad-mcp server, runs against the open board, and returns the
final text. The session id from the first reply is reused (``--resume``) so the
separate invocations form ONE conversation. No Anthropic API key — this uses
the user's Claude Code subscription.

Pure logic (subprocess + JSON parsing); no KiCad/wx imports, so it is unit
testable headless.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Optional


def find_claude() -> Optional[list[str]]:
    """Locate the Claude Code CLI. Returns the command prefix (list) or None.

    Tries a native ``claude`` on PATH first; falls back to ``wsl claude`` so a
    Windows KiCad can reach a Claude Code installed inside WSL.
    """
    for cand in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(cand)
        if found:
            return [found]
    wsl = shutil.which("wsl") or shutil.which("wsl.exe")
    if wsl:
        return [wsl, "claude"]
    return None


def _parse_json_reply(stdout: str) -> tuple[str, Optional[str]]:
    """Extract ``(text, session_id)`` from ``claude --output-format json``.

    Robust across schema variants: the well-known result schema
    (``{"result": "...", "session_id": "..."}``) and an assistant-content
    shape. Falls back to raw stdout as the text if it isn't JSON.
    """
    stdout = (stdout or "").strip()
    if not stdout:
        return "", None
    try:
        data: Any = json.loads(stdout)
    except Exception:
        return stdout, None  # plain text — show it as-is
    if not isinstance(data, dict):
        return stdout, None
    session_id = data.get("session_id") or data.get("sessionId")
    # 1) {"result": "<text>"}
    if isinstance(data.get("result"), str):
        return data["result"], session_id
    # 2) {"assistant_content": [{"type":"text","text":"..."}]}
    ac = data.get("assistant_content") or data.get("content")
    if isinstance(ac, list):
        text = "".join(
            b.get("text", "") for b in ac if isinstance(b, dict)
        )
        if text:
            return text, session_id
    # 3) {"text": "..."}
    if isinstance(data.get("text"), str):
        return data["text"], session_id
    return stdout, session_id


def build_command(
    claude: list[str],
    prompt: str,
    mcp_config_path: str,
    session_id: Optional[str],
) -> list[str]:
    """Build the ``claude`` argv for one chat turn."""
    cmd = list(claude) + [
        "-p", prompt,
        "--mcp-config", mcp_config_path,
        "--strict-mcp-config",            # ONLY the bundled kicad-mcp
        "--dangerously-skip-permissions",  # headless: no TTY to approve tools
        "--output-format", "json",
    ]
    if session_id:
        cmd += ["--resume", session_id]   # continue the same conversation
    return cmd


def ask(
    prompt: str,
    project_dir: str,
    mcp_config_path: str,
    session_id: Optional[str] = None,
    timeout: float = 300.0,
    claude_cmd: Optional[list[str]] = None,
    _runner=subprocess.run,
) -> dict[str, Any]:
    """Run one chat turn. Returns
    ``{ok, text, session_id, error}``. ``_runner`` is injectable for tests.

    ``claude_cmd`` (from a resolved RunPlan) overrides auto-detection so the
    caller controls native-vs-WSL; ``project_dir`` is the subprocess cwd and
    ``mcp_config_path`` the ``--mcp-config`` value (both in the style Claude
    expects for that plan).
    """
    claude = claude_cmd or find_claude()
    if claude is None:
        return {
            "ok": False,
            "text": "",
            "session_id": session_id,
            "error": (
                "Claude Code (claude) nicht gefunden. Installiere Claude Code "
                "und melde dich einmal an (claude login)."
            ),
        }
    cmd = build_command(claude, prompt, mcp_config_path, session_id)
    try:
        proc = _runner(
            cmd, cwd=project_dir, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": "", "session_id": session_id,
                "error": f"Zeitüberschreitung nach {int(timeout)}s."}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "text": "", "session_id": session_id,
                "error": f"Start fehlgeschlagen: {exc}"}

    text, new_session = _parse_json_reply(proc.stdout)
    if getattr(proc, "returncode", 0) != 0 and not text:
        err = (proc.stderr or proc.stdout or "claude beendete mit Fehler").strip()
        return {"ok": False, "text": "", "session_id": session_id,
                "error": err[:800]}
    return {
        "ok": True,
        "text": text,
        "session_id": new_session or session_id,
        "error": "",
    }
