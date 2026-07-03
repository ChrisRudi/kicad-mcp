# Warm-Server — Implementierungsplan (persistenter lokaler HTTP-MCP-Server)

Ziel: den kicad-mcp-Server **einmal pro KiCad-Sitzung** starten und warm halten,
statt ihn bei **jeder** Chat-Nachricht per stdio neu zu spawnen. Damit
verschwindet der „✗ MCP nicht verbunden (failed: kicad-mcp)"-Kaltstart-Wackler
(Kanal A: claude ↔ Server). Die Echtzeit-IPC zu KiCad (Kanal B: Server ↔ kipy)
bleibt **unangetastet** — sie verbindet lazy im Tool-Body, unabhängig vom
Transport.

## Nicht-Ziele
- kipy/IPC-Verhalten ändern (Kanal B bleibt wie er ist).
- Multi-KiCad-Instanzen (ein Server ↔ eine KiCad reicht für v1; dokumentieren).
- Remote/Netz-Zugriff (strikt `127.0.0.1`).

## Architektur

**Vorher (stdio, pro Nachricht):**
```
claude -p  --mcp-config(stdio)  →  spawnt python -c bootstrap  →  kicad_mcp.server.main() [stdio]
        (Kaltstart + Handshake JEDES Mal)
```
**Nachher (HTTP, einmal warm):**
```
Plugin startet EINMAL:  python -m kicad_mcp.server --transport streamable-http --host 127.0.0.1 --port P
claude -p  --mcp-config(http url=http://127.0.0.1:P/mcp)  →  verbindet an den LAUFENDEN Server
        (kein Spawn, kein Kaltstart; nur HTTP-Connect)
```

## KRITISCHE Vorab-Checks (zuerst, sonst scheitert alles)

1. **uvicorn/starlette in `_deps`?** FastMCP's `streamable-http` läuft über einen
   ASGI-Server (uvicorn). Der stdio-Pfad zog die evtl. **nicht** mit. Prüfen:
   `python -c "import uvicorn, starlette"` unter KiCads Python mit dem
   `_deps`-sys.path. Fehlt es → in `plugin/deps.py` zur Dep-Liste hinzufügen und
   Re-Install auslösen. **Das ist der wahrscheinlichste Blocker.**
2. **FastMCP-Version & API.** `mcp.run(transport="streamable-http", host=, port=)`
   existiert ab FastMCP 2.x. Version prüfen (`import fastmcp; fastmcp.__version__`),
   und den **Endpoint-Pfad** verifizieren (meist `/mcp/`). Claude-Code-Config-Typ
   ist `"http"` mit `"url"`.
3. **Claude Code akzeptiert HTTP-Server** in `--mcp-config`
   (`{"type":"http","url":...}`). Mit `claude.exe 2.1.185` einmal manuell testen.

## Feature-Flag & Fallback (Pflicht — wir können nicht auf allen Windows testen)

- Neues Env: `KICAD_MCP_TRANSPORT = stdio | http` (Default zunächst **`stdio`**;
  nach Validierung auf `http` flippen). Beide Pfade bleiben dauerhaft erhalten —
  ein einziger Schalter, überall respektiert (`mcp_config`, `claude_bridge`,
  `server_probe`).

## Phasen (jede mit eigenem Testpunkt, einzeln mergebar)

### Phase 0 — Flag-Plumbing (kein Verhaltenswechsel)
- `plugin/runtime_env.py` (oder neu): `transport_mode()` liest das Env, Default
  `stdio`.
- Überall wo Transport relevant ist, das Flag durchreichen, aber Default = altes
  Verhalten. **Test:** alles grün, nichts ändert sich.

### Phase 1 — Server HTTP-Modus
- `kicad_mcp/server.py::main()`: Arg-/Env-Parsing für
  `--transport {stdio,streamable-http}`, `--host`, `--port`. Bei http:
  `mcp.run(transport="streamable-http", host=host, port=port)`; sonst wie bisher.
  Lifespan/kipy bleibt unangetastet (lazy).
- **Test (headless, Linux ok):** Server auf `127.0.0.1:0`/festem Port starten,
  per HTTP einen MCP-`initialize`+`tools/list` fahren → 183 Tools. Beweist, dass
  der HTTP-Transport die volle Registry bedient. (Bundle-Sync nicht vergessen:
  `kicad_mcp/` → `plugin/mcp/kicad_mcp/`.)

### Phase 2 — Lifecycle-Manager `plugin/server_manager.py` (neu, pure wo möglich)
- `pick_free_port()` — bind `127.0.0.1:0`, Port lesen, schließen (Rest-Race
  akzeptieren) **oder** Server mit Port 0 starten und den echten Port aus dessen
  Logzeile lesen (robuster).
- `ensure_running() -> {url, port, pid, token}` — läuft ein gesunder Server?
  → wiederverwenden; sonst starten. Health-Check: TCP-Connect auf den Port
  **plus** optional ein HTTP-`initialize`-Ping.
- `shutdown()` — Prozessbaum killen (Windows: `taskkill /T`; reuse
  `claude_bridge._kill_tree`/`_register`).
- **Runtime-Statei** (Pidfile) unter `%LOCALAPPDATA%\...\kicad_mcp_server.json`
  `{pid, port, token, started}` — damit Plugin-Reloads innerhalb einer
  KiCad-Sitzung denselben Server finden und **Waisen** beim nächsten Start
  aufräumen (alter pid tot → wegräumen).
- Optionales **Bearer-Token** (zufällig pro Start) im Header, damit nicht jeder
  lokale Prozess den Server anspricht. localhost-Bind + Token = ausreichend.
- **Test (pure):** `pick_free_port` liefert nutzbaren Port; `ensure_running`
  startet genau einmal (zweiter Aufruf reused); Restart-Entscheidung bei totem
  pid; URL-Bau; Pidfile-Read/Write. Prozess-Spawn injizierbar (`_popen`) wie in
  `claude_bridge`-Tests.

### Phase 3 — Verdrahten (`mcp_config` + `claude_bridge`)
- `plugin/mcp_config.py::build_mcp_config`: bei `http`-Modus liefern
  `{"mcpServers":{"kicad-mcp":{"type":"http","url":"http://127.0.0.1:P/mcp",
  "headers":{"Authorization":"Bearer <token>"}}}}`. Bei `stdio` wie bisher.
- `plugin/claude_bridge.py::ask`: bei `http`-Modus **vor** dem `claude -p`-Start
  `server_manager.ensure_running()` aufrufen, dann die mcp-config auf die URL
  schreiben. Der bestehende Connect-Retry bleibt, wird aber selten gebraucht
  (Server ist schon warm). **Wichtig:** im http-Modus wird der Server **nicht**
  von claude, sondern vom Plugin verwaltet — claude verbindet nur.
- **Test:** `build_mcp_config` erzeugt beide Formen korrekt; `ask` ruft im
  http-Modus `ensure_running` genau einmal vor dem Spawn (mit gemocktem Manager).

### Phase 4 — Probe & Diagnose
- `plugin/server_probe.py`: im http-Modus statt stdio-Handshake einen HTTP-Ping
  gegen den laufenden Server (oder gegen einen frisch gestarteten, wie bisher).
- `plugin/diagnose.py`: Server-Status zeigen — **läuft? PID, Port, Uptime,
  Transport**. Genau die Info, die diese ganze Debug-Odyssee gespart hätte.

### Phase 5 — Teardown & Waisen
- Server bei **KiCad-Close** beenden (Plugin trackt seine Kindprozesse bereits;
  `server_manager.shutdown()` in den bestehenden Teardown-Hook hängen).
- Beim nächsten Start: Pidfile lesen, toten/fremden Prozess wegräumen, sauber neu
  starten. Kein doppelter Server.

### Phase 6 — Default flippen
- Nach erfolgreicher Windows-Validierung `KICAD_MCP_TRANSPORT`-Default auf `http`.
  `stdio` bleibt als Fallback (ein Env-Wort zurück).

## Neue / geänderte Dateien

| Datei | Änderung |
|---|---|
| `kicad_mcp/server.py` | HTTP-Transport-Modus (`--transport/--host/--port`) |
| `plugin/server_manager.py` | **neu** — Start/Health/Restart/Shutdown/Pidfile |
| `plugin/mcp_config.py` | http-Config-Variante (type=http, url, token) |
| `plugin/claude_bridge.py` | `ensure_running()` vor Spawn im http-Modus |
| `plugin/server_probe.py` | HTTP-Probe-Variante |
| `plugin/diagnose.py` | Server-Status (PID/Port/Uptime/Transport) |
| `plugin/runtime_env.py` | `transport_mode()` (+ Port/Token-Helfer) |
| `plugin/deps.py` | uvicorn/starlette sicherstellen (falls Check 1 fehlt) |
| `plugin/version.py` + `VERSIONS.md` | Version-Bump, Release-Wächter |

## Risiken & Mitigation

| Risiko | Mitigation |
|---|---|
| uvicorn fehlt in `_deps` | Vorab-Check 1; sonst deps ergänzen + Re-Install |
| Port belegt / Race | Port 0 → echten Port aus Server-Log lesen; Retry |
| Windows-Firewall-Prompt | strikt `127.0.0.1` binden (kein `0.0.0.0`) |
| Waisen-Server nach KiCad-Crash | Pidfile + Aufräumen beim nächsten Start |
| Server verhakt über Stunden | Health-Check vor jedem Turn → Auto-Restart |
| Fremder lokaler Prozess ruft Server | Bearer-Token + localhost-only |
| HTTP-Pfad bricht auf einem Setup | Feature-Flag → `KICAD_MCP_TRANSPORT=stdio` zurück |
| Zwei KiCad-Fenster | v1: nicht unterstützt, dokumentieren |

## Tests (headless, wie die bestehende Suite)
- Phase 1: HTTP-`initialize`+`tools/list` gegen echten Server → 183 Tools.
- Phase 2: `server_manager`-Pure-Logik (Port, ensure-once, Restart-Entscheid,
  Pidfile, URL/Token) mit injiziertem `_popen`.
- Phase 3: `build_mcp_config` beide Formen; `ask` ruft `ensure_running` genau
  einmal (gemockt).
- pylint 0/0, Bundle-Sync-Test grün.

## Rollback
Ein Env-Wort: `KICAD_MCP_TRANSPORT=stdio` (oder Default zurückdrehen) → exakt das
heutige Verhalten, ohne Code-Revert.

## Release
- `plugin/version.py` bumpen (0.7.0 — neue Architektur, aber additiv/geflaggt),
  `VERSIONS.md`-Pointer + Eintrag, `test_version_release.py` grün
  (`__tool_count__` bleibt 183).
- **Bundle-Sync** nach jeder `kicad_mcp/`-Änderung (`scripts/sync_bundle.py`);
  `test_bundle_sync.py` muss grün sein.
- Commit + Push auf `claude/analysis-improvements-fhl41u` **und** `main` (wie im
  bisherigen Workflow).

## Reihenfolge für Fable
1. Vorab-Checks 1–3 (uvicorn, FastMCP-API, claude-http). **Ergebnis zuerst
   berichten** — davon hängt der Rest ab.
2. Phase 1 (+Test) → Phase 2 (+Test) → Phase 3 (+Test) → Phase 4/5.
3. Alles hinter `KICAD_MCP_TRANSPORT`-Flag, Default `stdio`, bis auf echtem
   Windows validiert. Erst dann Phase 6.
