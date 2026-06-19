# Increment 2 — Plan & Live-Smoke-Test (Dependency-Resilienz)

Hand-off-Dokument: was nach 0.4.3 (Fundament) folgt, und **was du an deinem echten
KiCad** beisteuerst, damit der Live-Fix passgenau und ohne `_deps`-Brick-Risiko wird.

---

## Wo wir stehen
- **0.4.3 (PR #2):** das *Fundament* — `env_resolve.py` (KiCad→kipy-Kopplung,
  Environment-Fingerprint, Up/Downgrade-Entscheidung) + `deps.py`-Erweiterung, voll
  unit-getestet. **Noch kein Live-Effekt** (nicht in die GUI/den Install verdrahtet).
- **Increment 2:** der *Live-Fix* — Verdrahtung + Downgrade-Ausführung +
  Handshake-Selbstheilung. Berührt den zerstörerischen Install-Pfad und die wx-GUI,
  die **kein CI/Headless-Test** abdeckt → braucht einen Durchlauf auf echtem KiCad.

---

## Phase 0 — Umgebungs-Fakten sammeln (JETZT, vor dem Bau)

Diese Werte nageln die drei Anker + den Ist-Zustand fest. **Schick sie mir** — dann baue
ich Increment 2 gegen *deine* reale Konstellation statt gegen Annahmen.

### In der KiCad-Scripting-Console (`Tools → Scripting Console`)
```python
import pcbnew, sys
print("KiCad:", pcbnew.GetBuildVersion())
print("Python:", sys.version)
try:
    import kipy, importlib.metadata as m
    print("kipy geladen aus:", kipy.__file__)          # 3rdparty? oder _deps?
    print("kicad-python Version:", m.version("kicad-python"))
except Exception as e:
    print("kipy-Import:", type(e).__name__, e)
```
Entscheidend ist **`kipy geladen aus:`** — liegt der Pfad in `…\3rdparty\…` oder in
`…\<plugin>\_deps\…`? Das sagt, welche Kopie im GUI-Prozess gewinnt (= der Kern von
Fehlerbild A).

### In `_deps` (welche kicad-python-Version wurde dorthin gezwungen)
```python
import os, glob
DEPS = r"<PLUGIN>\_deps"   # der Ordner aus dem Setup-Dialog
print([os.path.basename(p) for p in glob.glob(os.path.join(DEPS, "kicad_python*"))])
```

### In cmd (der MCP-Anker)
```bat
claude --version
```

### Aus dem Chat-Panel
- Sind Bauteil-/Netz-Namen in Antworten **orange/klickbar**? (Link-Feature)
- Statuszeile: **MCP verbunden** oder `failed: kicad-mcp`?

---

## Increment 2 — was ich baue (defensiv, mit Fallback auf heute)

1. **GUI-Verdrahtung** (`setup_dialog`): `resolve_pip_specs(detect_kicad_version())` in
   `pip_install_argv` durchreichen — in `try/except` mit Fallback auf `PIP_SPECS`, damit
   ein Resolver-Fehler **nie** den Install bricht.
2. **Clean-Rebuild = Downgrade-Ausführung:** Install nach `_deps.new` → `verify_import`
   → **atomarer Swap** (`_deps.new` → `_deps`). Kein In-Place-Wipe → bei Fehlschlag bleibt
   das alte `_deps` intakt (kein Brick). Danach Fingerprint schreiben.
3. **Handshake-Selbstcheck nach Install:** kipy↔KiCad (`KiCad().get_version()`) **im
   GUI-Kontext** + MCP (`server_probe`). Bei Mismatch: **lauter, handlungsweisender
   Hinweis** statt stillem „nichts orange" + (bounded) Versions-Walk-back nach unten.

---

## Phase 1 — Live-Smoke-Test (NACH Increment 2)

Update einspielen, dann:

1. **Einrichtung öffnen → Install** laufen lassen → **„Erneut prüfen"**.
2. **`_deps`-Kopplung prüfen** (Scripting-Console, Snippet aus Phase 0): steht in `_deps`
   jetzt die **zur KiCad-Version gekoppelte** kicad-python (für KiCad 10: `0.7.1`)?
3. **Fingerprint da?** `os.path.isfile(r"<PLUGIN>\_deps\.env_fingerprint")` → `True`.
4. **Link-Feature:** Eine Frage stellen, die Bauteile/Netze nennt → werden sie **orange**?
5. **MCP:** Statuszeile **verbunden**?
6. **Downgrade-Test (der eigentliche Beweis):** in `_deps` kicad-python künstlich auf eine
   höhere Version setzen (oder Fingerprint-Datei löschen) → Install erneut → prüfen, ob
   das System auf die **gekoppelte (niedrigere)** Version **zurücknimmt**.

**Was du mir meldest:** je Schritt grün/rot + bei rot die Zeile (Traceback /
`server_probe`-Diagnose / `kipy geladen aus:`-Pfad).

---

## Akzeptanzkriterien (Definition of Done für Increment 2)
- [ ] `_deps` trägt die **KiCad-gekoppelte** kicad-python (nicht „latest").
- [ ] **Link-Feature orange**, **MCP verbunden**.
- [ ] **Downgrade greift** (zu neue kipy wird auf die gekoppelte zurückgenommen).
- [ ] Bei Mismatch ein **lauter Hinweis** statt stillem Fehlschlag.
- [ ] Ein fehlgeschlagener Install lässt das **alte `_deps` intakt** (kein Brick).

---

### TL;DR
0.4.3 = getestetes Fundament (jetzt). Increment 2 = der Live-Fix, der **nur** mit einem
Durchlauf auf deinem KiCad sicher gebaut werden kann. **Phase 0 jetzt ausfüllen und mir
schicken** → ich baue Increment 2 passgenau, du machst Phase 1, fertig.
