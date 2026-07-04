# SPDX-License-Identifier: GPL-3.0-or-later
"""Agenten-Backends: welches MCP-fähige CLI treibt den Chat.

Das Produkt ist der KiCad-Assistent, nicht ein bestimmtes Modell. Claude Code
ist das erprobte Standard-Backend; jedes andere **MCP-sprechende Agenten-CLI**
lässt sich hier ergänzen. Ein Backend kapselt genau die Stellen, an denen sich
die Agenten unterscheiden:

  * ``find()``            — das Binary lokalisieren
  * ``build_command()``  — die argv für einen Zug bauen
  * ``write_mcp_config`` — den kicad-mcp-Server so registrieren, wie das CLI
                           ihn erwartet (Claude: ``--mcp-config <json>``;
                           Codex: ``[mcp_servers]`` in ``~/.codex/config.toml``)
  * die Stream-Zerlegung — je CLI eigenes Ereignisformat → ein gemeinsames,
                           normalisiertes Vokabular für :mod:`claude_bridge`

Der gemeinsame Spawn-/Antrieb-Loop in :mod:`claude_bridge` bleibt agent-neutral;
er ruft nur die Backend-Methoden. So ist der Claude-Pfad unverändert (und voll
getestet), und Codex ist isoliert dazusteckbar.

Pure logic — headless testbar.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Normalisiertes Ereignis: was der Antrieb-Loop je Stream-Zeile wissen will.
# Alle Felder optional; ein Backend füllt, was in der Zeile steckt.
#   mcp_status   : "connected" | "pending: …" | "failed: …" | "none" | None
#   has_kicad    : bool  — ruft diese Zeile ein kicad-mcp-Tool? (Ground truth)
#   desc         : str   — kurze Aktivitätszeile für die Statusleiste
#   tools        : list[(short_name, input_dict)]
#   text         : str   — Assistenten-Textstück (Fallback-Sammler)
#   is_result    : bool  — Abschluss-Ereignis des Zuges
#   result_text  : str   — finaler Antworttext (bei is_result)
#   subtype      : str   — "success" | "error_max_turns" | …
#   error        : str   — Fehlertext (bei is_result, subtype != success)
#   session_id   : str   — für --resume/Session-Fortsetzung
# ---------------------------------------------------------------------------


class Backend:
    """Basis-Vertrag eines Agenten-CLI-Backends."""

    key: str = ""
    display: str = ""
    experimental: bool = False

    def find(self) -> Optional[list]:
        raise NotImplementedError

    def build_command(self, binary: list, prompt: str, mcp_config_path: str,
                      session_id: Optional[str], extra_args: Optional[list],
                      system_prompt: str, language: str = "") -> list:
        raise NotImplementedError

    def write_mcp_config(self, path: str, mcp_root: str,
                         python_exe: Optional[str] = None,
                         deps_dir: Optional[str] = None) -> str:
        raise NotImplementedError

    def config_path(self, base: str) -> str:
        """Der tatsächliche Config-Pfad für dieses Backend, abgeleitet vom
        (Claude-geformten) ``base`` aus dem RunPlan. Default: unverändert."""
        return base

    def normalize(self, line: str) -> Optional[dict]:
        """Eine Stream-Zeile → normalisiertes Ereignis-Dict, oder None."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
#  Claude Code (Anthropic) — das erprobte Standard-Backend.                    #
#  Delegiert bewusst an die vorhandenen, getesteten claude_bridge/mcp_config   #
#  Funktionen: der Claude-Pfad bleibt Byte-für-Byte, was er war.              #
# --------------------------------------------------------------------------- #

class ClaudeCodeBackend(Backend):
    key = "claude_code"
    display = "Claude Code (Anthropic)"
    experimental = False

    def find(self) -> Optional[list]:
        from . import claude_bridge
        return claude_bridge.find_claude()

    def build_command(self, binary, prompt, mcp_config_path, session_id,
                      extra_args, system_prompt, language="") -> list:
        from . import claude_bridge
        return claude_bridge.build_command(
            binary, prompt, mcp_config_path, session_id, extra_args,
            language=language)

    def write_mcp_config(self, path, mcp_root, python_exe=None,
                         deps_dir=None) -> str:
        from . import mcp_config
        return mcp_config.write_mcp_config(path, mcp_root, python_exe, deps_dir)

    def normalize(self, line: str) -> Optional[dict]:
        from . import claude_bridge as cb
        ev = cb.parse_stream_event(line)
        if ev is None:
            return None
        out: dict[str, Any] = {}
        status = cb.mcp_status_from_init(ev)
        if status is not None:
            out["mcp_status"] = status
        out["has_kicad"] = cb.has_kicad_mcp_tool_use(ev)
        desc = cb.describe_event(ev)
        if desc:
            out["desc"] = desc
        tools = cb.tool_calls(ev)
        if tools:
            out["tools"] = tools
        if ev.get("type") == "assistant":
            t = cb.extract_text(ev)
            if t:
                out["text"] = t
        if ev.get("type") == "result":
            out["is_result"] = True
            res = ev.get("result")
            out["result_text"] = res if isinstance(res, str) else ""
            out["subtype"] = str(ev.get("subtype", "success"))
            out["error"] = ev.get("error") or ""
        sid = ev.get("session_id")
        if sid:
            out["session_id"] = sid
        return out or None


# --------------------------------------------------------------------------- #
#  Codex (OpenAI Codex CLI) — EXPERIMENTELL, im Feld ungetestet.               #
#  Codex spricht MCP, registriert Server aber über ~/.codex/config.toml       #
#  ([mcp_servers.<name>]) statt einer --mcp-config-Datei, und läuft            #
#  non-interaktiv über `codex exec`. Das JSONL-Ereignisformat (--json) wird   #
#  hier nach bestem dokumentierten Stand normalisiert; bitte im Feld prüfen.   #
# --------------------------------------------------------------------------- #

class CodexBackend(Backend):
    key = "codex"
    display = "Codex (OpenAI) — experimentell"
    experimental = True
    SERVER_NAME = "kicad-mcp"

    def find(self) -> Optional[list]:
        for cand in ("codex", "codex.cmd", "codex.exe"):
            found = shutil.which(cand)
            if found:
                return [found]
        wsl = shutil.which("wsl") or shutil.which("wsl.exe")
        if wsl:
            return [wsl, "codex"]
        return None

    def config_path(self, base: str) -> str:
        # Codex liest TOML, nicht Claudes JSON — eigene Datei daneben.
        return os.path.splitext(base)[0] + ".codex.toml"

    def build_command(self, binary, prompt, mcp_config_path, session_id,
                      extra_args, system_prompt, language="") -> list:
        """``codex exec`` non-interaktiv, JSONL-Ereignisse.

        Der System-/Sprach-Hinweis wird dem Prompt vorangestellt (Codex hat
        kein ``--append-system-prompt``). ``mcp_config_path`` ist hier die
        geschriebene config.toml, die Codex über ``--config`` einliest.
        Session-Fortsetzung: ``codex exec resume <id>`` (best effort).
        """
        sys_prefix = system_prompt
        if language:
            sys_prefix += f" Antworte IMMER in dieser Sprache: {language}."
        full_prompt = (sys_prefix + "\n\n" + prompt) if sys_prefix else prompt
        cmd = list(binary) + ["exec", "--json",
                              "--dangerously-bypass-approvals-and-sandbox"]
        if mcp_config_path:
            cmd += ["--config", mcp_config_path]
        if session_id:
            # experimentell: Codex-Session-Fortsetzung
            cmd += ["--session", session_id]
        if extra_args:
            cmd += list(extra_args)
        cmd += [full_prompt]
        return cmd

    def write_mcp_config(self, path, mcp_root, python_exe=None,
                         deps_dir=None) -> str:
        """kicad-mcp als ``[mcp_servers.kicad-mcp]`` in eine TOML schreiben.

        Codex kennt keine ``--mcp-config``-JSON; es liest ``[mcp_servers]`` aus
        einer config.toml. Wir schreiben eine eigenständige TOML (kein Merge in
        die globale ``~/.codex/config.toml``, um sie nicht zu verändern) und
        geben sie Codex per ``--config``.
        """
        from . import deps, mcp_config
        python_exe = python_exe or mcp_config.find_kicad_python()
        if not python_exe:
            raise RuntimeError("KiCad-Python (mit kipy) nicht gefunden.")
        if deps_dir is None:
            deps_dir = deps.active_deps_dir()
        boot = mcp_config.server_bootstrap_code(mcp_root, deps_dir)
        # TOML: command + args (das -c-Bootstrap), Transport per env gepinnt.
        toml = (
            f'[mcp_servers.{self.SERVER_NAME}]\n'
            f'command = {json.dumps(python_exe)}\n'
            f'args = ["-c", {json.dumps(boot)}, "--transport", "stdio"]\n'
            f'[mcp_servers.{self.SERVER_NAME}.env]\n'
            f'KICAD_MCP_TRANSPORT = "stdio"\n'
            f'PYTHONUNBUFFERED = "1"\n'
            f'KICAD_MCP_NO_AUTO_OPEN = "1"\n'
        )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(toml)
        return path

    def normalize(self, line: str) -> Optional[dict]:
        """Codex-JSONL → normalisiertes Ereignis (best effort, ungetestet).

        Erwartet Zeilen wie ``{"type":"item.completed","item":{...}}``. Wir
        mappen Agenten-Text, MCP-Tool-Aufrufe und das Abschluss-Ereignis; alles
        andere → leises Weiter. Ungewisse/abweichende Schemata dürfen den Zug
        NICHT killen (defensiv), damit ein Codex-Update nicht alles bricht.
        """
        line = (line or "").strip()
        if not line.startswith("{"):
            return None
        try:
            ev = json.loads(line)
        except Exception:
            return None
        if not isinstance(ev, dict):
            return None
        etype = str(ev.get("type", ""))
        item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
        itype = str(item.get("type", ""))
        out: dict[str, Any] = {}

        # Agenten-Textstücke
        if itype in ("agent_message", "assistant_message"):
            txt = item.get("text") or item.get("message") or ""
            if txt:
                out["text"] = txt
                out["desc"] = "formuliert die Antwort …"
        # MCP-Tool-Aufruf
        elif itype in ("mcp_tool_call", "tool_call", "function_call"):
            name = str(item.get("tool") or item.get("name") or "")
            # Claude-Stil ``mcp__srv__tool`` ODER Codex-Stil ``srv.tool``.
            short = name.replace("__", ".").split(".")[-1] if name else "?"
            inp = item.get("arguments") or item.get("input") or {}
            if isinstance(inp, str):
                try:
                    inp = json.loads(inp)
                except Exception:
                    inp = {}
            out["tools"] = [(short, inp if isinstance(inp, dict) else {})]
            out["has_kicad"] = name.startswith(("kicad", "mcp"))
            out["desc"] = f"{short} …"
        # Abschluss
        if etype in ("turn.completed", "session.completed", "task_complete"):
            out["is_result"] = True
            out["result_text"] = str(ev.get("text") or item.get("text") or "")
            out["subtype"] = "success"
            out["error"] = ""
        elif etype in ("error", "turn.failed"):
            out["is_result"] = True
            out["result_text"] = ""
            out["subtype"] = "error"
            out["error"] = str(ev.get("message") or ev.get("error") or "Fehler")
        # Session-Kennung (verschiedene mögliche Felder)
        sid = ev.get("session_id") or ev.get("thread_id") or item.get("id")
        if sid:
            out["session_id"] = str(sid)
        return out or None


_BACKENDS = {b.key: b for b in (ClaudeCodeBackend(), CodexBackend())}
DEFAULT_KEY = "claude_code"


def get(key: str) -> Backend:
    """Backend zum Schlüssel; unbekannt → Standard (Claude Code)."""
    return _BACKENDS.get(key or DEFAULT_KEY, _BACKENDS[DEFAULT_KEY])


def available() -> list:
    """Alle Backends in Anzeige-Reihenfolge (Standard zuerst)."""
    return [_BACKENDS["claude_code"], _BACKENDS["codex"]]
