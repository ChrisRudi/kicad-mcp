# Diagnose: „✗ MCP nicht verbunden (failed: kicad-mcp)"

**Symptom:** Der Chat antwortet, aber **ohne Board-Tools**; Statuszeile: `failed: kicad-mcp`.

**Was das technisch heißt:** Der Claude-CLI hat den MCP-Server-**Prozess** gestartet, aber
er meldete sich nicht als `connected` — d.h. der Server-Prozess ist **beim Start gecrasht**
(oder hat den `initialize`+`tools/list`-Handshake nicht rechtzeitig beendet). Quelle des
Status: `plugin/claude_bridge.mcp_status_from_init`.

## Was schon geklärt ist (nicht nochmal testen)

| Geprüft | Ergebnis |
|---|---|
| Server-Code importiert + registriert Tools | ✅ 173 Tools, sauber |
| **Voller Handshake** (`initialize`+`tools/list`) gegen `plugin/mcp/kicad_mcp` | ✅ **9,2 s, exit 0**, `serverInfo` + `tools` kamen zurück |
| Startup-Timeout | ✅ steht auf **5 min** (`MCP_STARTUP_TIMEOUT_MS = 300000`) → Timeout unwahrscheinlich |
| Deps-Liste im Plugin | ✅ vollständig: `fastmcp, mcp, pandas, yaml, defusedxml, jsonschema, kipy` |

→ **Der Server-Code ist nicht die Ursache.** Das Problem ist **umgebungsspezifisch auf der
Live-Maschine**: am wahrscheinlichsten eine **fehlende Runtime-Dependency im `_deps`-Ordner**
oder ein **unvollständiger `mcp/`-Ordner** (z. B. nach einem Update).

---

## Test 0 — Built-in-Diagnose zuerst (1 Klick, das haben wir eingebaut)

Das Plugin hat den autoritativen Check schon an Bord (`server_probe.probe_server` +
`deps.check_deps`).

1. Chat-Panel → **Einrichtung** öffnen → **„Erneut prüfen"**.
2. Lies die **rote Zeile**. Sie ist die echte Diagnose:

| Rote Zeile enthält … | Ursache | Fix |
|---|---|---|
| `missing_dep` / `ModuleNotFoundError: No module named 'X'` | Dependency `X` fehlt in `_deps` | Im Setup-Dialog **Deps installieren** klicken |
| `kicad_mcp-Paket fehlt: …` (`missing_root`) | `mcp/`-Ordner unvollständig (Update kaputt) | Setup → **„Update prüfen"** (lädt `mcp/` neu) |
| `Server antwortet nicht (> …s) — Kaltstart zu langsam` | echter Timeout | siehe Test 3 / Defender, sonst Timeout erhöhen |
| `initialize ok, aber tools/list kam nicht` | Tool-Enumeration zu langsam/crasht | stderr aus Test 3 holen |

> **Wenn Test 0 die rote Zeile zeigt: kopier sie mir — fertig.** Der Rest unten ist nur,
> falls du es manuell nachstellen willst.

---

## Test 1 — Pfade ermitteln (KiCad-Scripting-Console)

`Tools → Scripting Console` (läuft in **KiCads** Python). Dann:

```python
import sys; print("KiCad-Python:", sys.executable)
```

Den **Plugin-Ordner** findest du unter
`…\Documents\KiCad\<version>\3rdparty\plugins\<plugin>\` — es ist der Ordner, der
**`mcp\`** und **`_deps\`** nebeneinander enthält. (PCM zeigt den Pfad auch an, und der
Setup-Dialog druckt ihn.)

Im Folgenden:
- `<MCP>`  = `…\<plugin>\mcp`
- `<DEPS>` = `…\<plugin>\_deps`
- `<PY>`   = das KiCad-Python aus Test 1

---

## Test 2 — Sind die Deps wirklich im `_deps`? (wahrscheinlichste Ursache)

In der **Scripting Console** (Pfade anpassen):

```python
import importlib.util, sys, os
DEPS = r"<DEPS>"
sys.path[:0] = [DEPS, os.path.join(DEPS, "win32"), os.path.join(DEPS, "win32", "lib"),
                os.path.join(DEPS, "Pythonwin")]
req = ["fastmcp", "mcp", "pandas", "yaml", "defusedxml", "jsonschema", "kipy"]
miss = [m for m in req if importlib.util.find_spec(m) is None]
print("FEHLT:", miss or "nichts — alle Deps da")
```

- **`FEHLT: ['kipy']`** (oder `['pandas']` …) → genau dieses Modul ist nicht/kaputt
  installiert. **Das ist die Wurzel.** → Setup-Dialog „Deps installieren", oder manuell
  (Test 2b).
- **`FEHLT: nichts`** → Deps sind da; weiter mit Test 3 (der Crash liegt woanders, z. B.
  ein nativer Import, der `find_spec` besteht aber beim echten `import` knallt).

### Test 2b — Echten Import erzwingen (deckt kaputte native Wheels auf)

`find_spec` sagt nur „Datei da", nicht „importierbar". Nativer Code (`pandas`, `kipy`/`pynng`,
`pywin32`) kann trotzdem beim echten Import scheitern (fehlende DLL/VC-Runtime):

```python
import sys, os
DEPS = r"<DEPS>"
sys.path[:0] = [DEPS, os.path.join(DEPS, "win32"), os.path.join(DEPS, "win32", "lib"),
                os.path.join(DEPS, "Pythonwin")]
for m in ["fastmcp", "mcp", "pandas", "yaml", "defusedxml", "jsonschema", "kipy"]:
    try:
        __import__(m); print("OK   ", m)
    except Exception as e:
        print("CRASH", m, "->", type(e).__name__, e)
```

Die erste `CRASH …`-Zeile ist der echte Grund für „failed: kicad-mcp".

---

## Test 3 — Manueller Voll-Start (autoritativ, = was Claude macht)

In einer **Eingabeaufforderung (cmd)** — Pfade einsetzen, **mit Anführungszeichen**:

```bat
"<PY>" -c "import sys; sys.path[:0]=[r'<MCP>', r'<DEPS>']; from kicad_mcp.server import main; main()"
```

- **Sofortiger Traceback + Prozess endet** → das ist der echte Crash (z. B.
  `ModuleNotFoundError: No module named 'fastmcp'`). **Diese Zeilen mir schicken.**
- **Cursor bleibt stehen / nichts passiert** → Server läuft gesund und wartet auf stdin
  (= alles gut, dann ist die Ursache woanders, z. B. der Claude-CLI-Aufruf). Mit `Strg+C`
  beenden.

> Das Plugin kann diesen Start auch selbst in einem sichtbaren Terminal öffnen
> (Setup-Dialog → Terminal/Install-Knopf) — gleicher Effekt.

---

## Test 4 — Ist der `mcp/`-Ordner vollständig? (`missing_root`, nach Update)

```python
import os
MCP = r"<MCP>"
print("server.py da?", os.path.isfile(os.path.join(MCP, "kicad_mcp", "server.py")))
print("Dateien:", len(os.listdir(os.path.join(MCP, "kicad_mcp"))))
```

`server.py da? False` → die Update-/Installation hat `mcp/kicad_mcp` nicht (vollständig)
geschrieben → Setup „Update prüfen" neu laufen lassen.

---

## Test 5 — Claude-CLI (zur Sicherheit)

`failed: kicad-mcp` heißt eigentlich schon: **claude lief** (nur der MCP-Server fiel aus).
Zur Kontrolle in cmd:

```bat
claude --version
```

Kein Output / „nicht gefunden" → claude ist nicht auf dem PATH (anderes Symptom, nicht dieses).

---

## Was ich von dir brauche, um die Wurzel zu fixen

**Genau eine** dieser Ausgaben:
1. die **rote Zeile** aus Test 0 (Setup → „Erneut prüfen"), **oder**
2. die `CRASH …`-Zeile aus Test 2b, **oder**
3. der **Traceback** aus Test 3.

Damit ist es kein Raten mehr — ich ziehe entweder die fehlende Dependency nach, repariere den
Deps-Installer (häufig: nativer Build wie `pynng`/`pywin32` scheitert unter Windows), oder
fixe den unvollständigen `mcp/`-Ordner.
