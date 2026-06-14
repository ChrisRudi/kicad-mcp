# Claude für KiCad — Action-Plugin (Stufe 1)

Ein KiCad-PCB-Editor-Plugin mit Toolbar-Button **„Claude"**, das ein Chat-Panel
öffnet. Du tippst eine Frage/Anweisung zum offenen Board, das Plugin fährt einen
**Claude-Code**-Turn (dein Abo, *keine* API-Kosten) über den gebündelten
**kicad-mcp**-Server, und zeigt die Antwort. Claude arbeitet dabei live am Board.

```
[Chat-Panel in KiCad]  →  claude -p (Abo)  →  kicad-mcp  →  IPC  →  dieselbe pcbnew
        ▲                                                              │
        └────────────────────  Antwort + Board-Aktionen  ◀────────────┘
```

## Läuft auf Windows UND Linux — kein WSL nötig
Das Plugin läuft in KiCads eigenem Python, also in **KiCads Betriebssystem**.
Claude Code und der kicad-mcp-Server laufen im **selben** System — dadurch sind
alle Pfade in einem Stil und es gibt keine Cross-OS-Brüche:

| KiCad | Claude Code | Modus |
|---|---|---|
| Windows | Windows-`claude` | nativ (alle `C:\…`-Pfade) |
| Linux/mac | lokaler `claude` | nativ (alle `/…`-Pfade) |

> **WSL ist nicht erforderlich.** Ein Entwickler, der Claude bereits in WSL hat,
> kann die WSL-Brücke per `KICAD_CLAUDE_ALLOW_WSL=1` einschalten — standardmäßig
> ist sie aus, und niemand wird je aufgefordert, WSL zu installieren.

## Einmal-Setup (das, was kein Plugin abnehmen kann)
1. **Claude Code installieren** (im selben System wie KiCad — auf Windows der
   Windows-Installer/`npm`, *kein* WSL) und **einloggen**: `claude` muss
   aufrufbar sein, einmal `claude login` (dein Anthropic-Abo).
2. **Projekt-Vertrauen**: einmal `claude` *interaktiv* im Projektordner starten,
   damit der Trust-Dialog durch ist (Headless-`-p` fragt nicht nach).
3. **kicad-mcp** muss erreichbar sein. Falls nicht am Default-Pfad, setze:
   - `KICAD_MCP_ROOT` = Ordner des `kicad-mcp`-Repos (mit `kicad_mcp/`)
   - `KICAD_PYTHON_PATH` = KiCad-Python mit `kipy`
     (z. B. `C:\Program Files\KiCad\10.0\bin\python.exe`)

Schritt 1+2 macht der **Einrichtungs-Check** beim ersten Klick mit Ein-Klick-
Knöpfen (Installieren / Anmelden) so weit wie möglich selbst.

## Plugin installieren

**Ein-Klick (empfohlen):** Skript aus dem Repo-Root ausführen — es holt das Plugin
(git oder ZIP-Fallback) und kopiert es an die richtige Stelle:

- **Windows:** `install_plugin.bat` herunterladen, Doppelklick (optional KiCad-Version als
  Argument, Default `10.0`).
- **Linux/macOS:**
  ```bash
  curl -fsSL https://raw.githubusercontent.com/ChrisRudi/kicad-mcp/main/install_plugin.sh | bash
  ```

**Manuell:** Kopiere den `plugin/`-Ordner in KiCads Plugin-Verzeichnis und benenne ihn
`claude_kicad`:
- Windows: `%APPDATA%\kicad\10.0\scripting\plugins\claude_kicad\`
- Linux: `~/.local/share/kicad/10.0/scripting/plugins/claude_kicad/`
- macOS: `~/Library/Application Support/kicad/10.0/scripting/plugins/claude_kicad/`

Dann in pcbnew: **Werkzeuge → Externe Plugins → Aktualisieren** (oder KiCad neu
starten). Der **Claude**-Button erscheint in der Toolbar.

> **Wichtig — nicht aus der Konsole importieren.** Lade das Plugin ausschließlich
> über den Plugin-Ordner + **Aktualisieren**. Ein `import` im Scripting-Terminal
> (oder unter dem nackten KiCad-`python.exe`) löst den C++-Assert
> `PgmOrNull() … register_action()` aus, weil dort kein laufendes GUI existiert.
> Das Plugin registriert sich deshalb nur, wenn eine echte `wx.App` läuft.

## Erster Klick: Einrichtungs-Check
Beim ersten Mal (oder sobald etwas fehlt) öffnet der Button ein
**Einrichtungs-Panel** — eine grün/rote Checkliste mit je einem Ein-Klick-Fix:

| Prüfung | grün = | Fix-Knopf |
|---|---|---|
| Claude Code gefunden | `claude` aufrufbar | **Installieren** (offizieller Installer) |
| KiCad-Python (kipy) | `python.exe` mit kipy gefunden | **Hilfe** (Env-Vars) |
| kicad-mcp bereit | `KICAD_MCP_ROOT` enthält `kicad_mcp/` | **Hilfe** |
| Angemeldet & Ordner vertraut | `claude login` + Projekt getrustet | **Anmelden** (Terminal) |
| MCP-Abhängigkeiten | fastmcp/mcp/pandas/… in KiCad-Python | **Installieren** (pip --user) |
| KiCad-API aktiv | IPC-Server an (für Live-Arbeit am Board) | **Aktivieren** |
| Board offen | ein `.kicad_pcb` ist offen | — |

**Claude Code installiert der Knopf wirklich** (nicht nur Doku): er zeigt erst
den exakten **offiziellen** Befehl (Windows `irm https://claude.ai/install.ps1 |
iex`, Linux/mac `curl -fsSL https://claude.ai/install.sh | bash`), und öffnet
nach Bestätigung ein **sichtbares** Terminal, das ihn ausführt — kein stilles
`curl|bash`. Der Installer legt `claude` in `~/.local/bin` ab; das Plugin findet
ihn dort sofort (kein KiCad-Neustart nötig). Der eine interaktive Rest bleibt:
einmal `claude login` (OAuth) — dafür ist der **Anmelden**-Knopf da.

**KiCad-API (IPC) wird automatisch aktiviert.** Beim ersten Klick setzt das
Plugin `api.enable_server` in `kicad_common.json` (der Schalter hinter
*Einstellungen → Plugins → KiCad-API*) und sagt dir, dass du KiCad **einmal neu
starten** musst — der IPC-Server startet nur beim Programmstart. Es schreibt nur
diesen einen Schlüssel, alle anderen Einstellungen bleiben unangetastet, und es
ist idempotent (re-asserted bei jedem Laden, falls KiCad ihn je zurücksetzt).

**Chat starten** wird aktiv, sobald kein harter Fehler (rot) mehr offen ist
(Warnungen sind nur Hinweise). Ist beim Klick schon alles grün, springt der
Button direkt in den Chat — das Panel erscheint dann gar nicht.

## Benutzen
1. Board in pcbnew öffnen.
2. **Claude**-Button klicken → Einrichtungs-Check (einmalig) → Chat-Panel.
3. Tippen, z. B.:
   - „wie viele GND-Vias hat das Board?"
   - „markier die 3 kleinsten Vias"
   - „setz ein GND-Via bei 140,110"

Das Panel ist **nicht-modal** — du siehst das Board live aktualisieren, während
Claude arbeitet. Der Gesprächsfaden bleibt über mehrere Nachrichten erhalten
(`--resume` mit der Session-ID aus der ersten Antwort).

## Was wo läuft (Stufe 1)
| Datei | Aufgabe |
|---|---|
| `__init__.py` | registriert den Button — **nur bei laufender `wx.App`** (sonst Assert) |
| `claude_action.py` | Klick → Preflight → Einrichtungs-Panel **oder** direkt Chat |
| `setup_dialog.py` | wx-Einrichtungs-Panel (grün/rote Checkliste + Fix-Knöpfe) |
| `chat_dialog.py` | wx-Chat-Panel (Ein-/Ausgabe, Worker-Thread) |
| `runtime_env.py` | löst pro Maschine einen pfad-konsistenten RunPlan (nativ Win/Linux, opt-in WSL-Brücke) (testbar) |
| `ipc_setup.py` | aktiviert KiCads IPC-API (`api.enable_server` in `kicad_common.json`) (testbar) |
| `installer.py` | offizielle Claude-Code-Install-Befehle + sichtbares Install-Terminal (testbar) |
| `deps.py` | prüft/installiert die MCP-Server-Laufzeit-Deps (fastmcp/pandas/…) in KiCad-Python (testbar) |
| `mcp/kicad_mcp/` | **gebündelte** Kopie des kicad-mcp-Servers (self-contained; ohne `.cache`/`__pycache__`) |
| `preflight.py` | reine Detektoren (Claude/Python/MCP/Login/Board) (testbar) |
| `claude_bridge.py` | `claude -p … --mcp-config … --resume …`, JSON-Antwort (testbar) |
| `mcp_config.py` | erzeugt die `--mcp-config`-JSON für kicad-mcp (testbar) |

`runtime_env.py`, `ipc_setup.py`, `installer.py`, `deps.py`, `updater.py`,
`claude_bridge.py`, `mcp_config.py` und `preflight.py` sind reine Logik (keine
KiCad-Imports), headless getestet in `tests/test_plugin_*.py` (98 Tests).

## Self-contained (gebündeltes MCP)
Das Plugin liefert eine Kopie des kicad-mcp-Servers in `mcp/kicad_mcp/` mit. Die
MCP-Wurzel wird so aufgelöst: `KICAD_MCP_ROOT` (falls gesetzt & gültig) →
**gebündeltes `mcp/`** → Dev-Checkout-Fallback. Damit läuft das Plugin auf
fremden Rechnern ohne Repo-Pfad. Die **Laufzeit-Deps** (fastmcp/mcp/pandas/
pyyaml/defusedxml/jsonschema) sind nicht in KiCads Python — der Onboarding-Check
*MCP-Abhängigkeiten* installiert sie per `pip --user` (kein Admin).

## Icon
Der Toolbar-Button trägt das **offizielle Model-Context-Protocol-Logo**
(`icon.png` hell / `icon_dark.png` dunkel, aus der MCP-SVG gerendert). Bewusst
das MCP- statt eines Claude-Logos: MCP ist der **offene, anbieterneutrale**
Standard — das Icon signalisiert „spricht MCP", nicht einen bestimmten Anbieter.
Lizenz des Logos: MIT bzw. unterhalb der Schöpfungshöhe (faktisch gemeinfrei,
Quelle Wikimedia Commons / `modelcontextprotocol`); in manchen Ländern als Marke
schützbar — Verwendung nur als Kompatibilitäts-Hinweis, ohne Anthropic-Billigung
zu suggerieren. Zum Tauschen einfach `icon.png` / `icon_dark.png` ersetzen
(quadratisch, transparent; ~48 px).

## Sicherheit (ehrlich)
Stufe 1 nutzt `--dangerously-skip-permissions`, damit Claude im Headless-Modus
ohne TTY-Rückfrage die kicad-mcp-Tools ausführen darf. Das heißt: **alle**
kicad-mcp-Tools laufen ungefragt. Da der MCP-Server lokal + von dir kontrolliert
ist, ist das vertretbar — aber jede Board-Änderung ist in pcbnew **undo-bar**
(Bearbeiten → Rückgängig, „kicad-mcp …"). Spätere Stufe: `--permission-mode`
/ `--allowedTools` für feinere Kontrolle.

## Updates (zwei Wege)
Das Plugin trägt eine **Version** (`version.py`, im Panel-/Chat-Titel sichtbar).

1. **Direkt aus GitHub (vorläufig, zum Testen):** Im Einrichtungs-Panel
   *Update prüfen* — liest `plugin/version.py` aus `ChrisRudi/kicad-mcp`, und
   lädt bei neuerer Version das Branch-Zip, überschreibt die `plugin/`-Dateien
   in place. Danach **KiCad neu starten**. (Erreichbar auch aus dem Chat über
   *Einrichtung / Update*.) Nur dieses eine Repo, über HTTPS — Update aus dem
   Netz ist Code-Ausführung, also nie auf eine fremde Quelle zeigen.
2. **Offizielles KiCad-PCM (Ziel):** sobald alles läuft, Einreichung ans
   offizielle Metadaten-Repo (`gitlab.com/kicad/addons/metadata`) → erscheint im
   *Plugin and Content Manager* bei **allen** KiCad-Nutzern (Update/Deinstall in
   der GUI). Voraussetzung: das Paket self-contained machen (kicad-mcp bündeln).

Branch überschreibbar per `KICAD_MCP_PLUGIN_BRANCH` (Default `main`).

## Roadmap
- **Stufe 1 ✓:** Button → Einrichtungs-Check → Chat über Claude Code + kicad-mcp.
- **Stufe 2:** Backend-Auswahl (Claude Code / Codex / …) im Panel.
- **Stufe 3:** kicad-mcp in den Plugin-Ordner bündeln (Default-`KICAD_MCP_ROOT`
  wird dann der mitgelieferte Ordner statt eines Repo-Pfads) → Paket
  self-contained → MR ans offizielle PCM-Metadaten-Repo (alle KiCad-Nutzer).
