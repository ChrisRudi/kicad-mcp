# Design: Dynamische Dependency-Resilienz

**Ziel:** Versionsdrift zwischen `_deps` und seinen externen Ankern (KiCad, Claude-CLI/MCP,
KiCads gebündeltes Python) **dauerhaft** auflösen — ohne Pins, die pro Release nachgezogen
werden müssen. Prinzip: **Das System erkennt die Umgebung und leitet daraus ab**, statt
Versionen hart zu setzen.

> Status: **Design only** (kein Code). Grundlage für 0.4.3.

---

## 1. Die drei Anker (das Problem in einem Satz)

`_deps` wird mit „latest" installiert, muss aber zu drei **fixen, unabhängig wandernden**
Ankern passen:

| Anker | liegt auf | koppelt | Fehlerbild |
|---|---|---|---|
| **A — laufendes KiCad** | User-Maschine | `kipy`, `protobuf`-Wire | IPC tot → „nichts orange" |
| **B — Claude-CLI + MCP-Protokoll** | User-Maschine | `mcp`, `fastmcp` | Handshake scheitert → `failed: kicad-mcp` |
| **C — KiCads gebündeltes Python** (Version+Plattform+numpy-ABI) | KiCad-Install | `pandas`/`numpy`, `pynng`, `pywin32`, `protobuf`-nativ | Import-Crash beim Server-Start |

„latest" oder „fester Pin" sind **beide** falsch, weil keiner an die Anker gekoppelt ist.
Dynamisch = an die Anker koppeln **und** gegen die Realität verifizieren.

---

## 2. Architektur: Detect → Resolve → Install → Verify → Self-heal

Getrieben von einem **Environment-Fingerprint**. Alles andere hängt daran.

```
Fingerprint = hash(KiCad-Version, Python-Version/Plattform, Claude-CLI-Version)
   │
   ├─ unverändert?  → gecachtes, verifiziertes _deps benutzen (warm, 0 Arbeit)
   └─ geändert?     → Resolve → Install → Verify → (Self-heal) → Cache unter neuem Fingerprint
```

Der Fingerprint ist der Dreh- und Angelpunkt: **ändert sich ein Anker (KiCad-/CLI-/Python-
Upgrade), ändert sich der Fingerprint → das System re-resolved automatisch.** Es reagiert auf
Umgebungs-Änderung, nicht auf einen Kalender oder eine hartcodierte Versionszahl. Das ist
„für die Zukunft erledigt".

### 2.1 Detect — Anker messen (ohne kipy)
- **KiCad-Version:** `pcbnew.GetBuildVersion()` — läuft im GUI-Prozess, **kipy-unabhängig**
  (wichtig, weil kipy-basierte Erkennung zirkulär wäre: sie bräuchte ein funktionierendes
  kipy).
- **Python:** `sys.version_info`, `sys.implementation`, Plattform-Tag des laufenden
  KiCad-Python → Anker für native Wheels/ABI.
- **Claude-CLI + MCP-Protokoll:** `claude --version`; die akzeptierte `protocolVersion`
  ergibt sich aus dem `initialize`-Handshake (der CLI lehnt Inkompatibles ab).

### 2.2 Resolve — aus Ankern → Constraints (zwei Wissensquellen)
1. **Kompatibilitäts-Manifest (Daten, nicht Code):** eine kleine Tabelle/Regel-Datei im
   Plugin, **self-updatebar über denselben Updater**, der `version.py` zieht. Z. B.
   „KiCad 10.x → `kicad-python~=0.7.0`", plus ein **getestetes Constraints-Set** je
   KiCad-Python für die native Klasse (`pandas`/`numpy`/…). Neue KiCad-Release = **Daten-
   Edit oder Auto-Pull**, kein Plugin-Code-Release.
2. **Empirische Auflösung (Ground Truth, für unbekannte Zukunft):** hat das Manifest keinen
   Eintrag für ein neues KiCad, fällt das System auf „installiere das Neueste, das mit
   diesem Python kompatibel ist, dann **verifiziere per Handshake**; scheitert er, gehe
   Versionen zurück bis er besteht" (bounded search). **Der Handshake gegen das echte KiCad
   ist das Orakel** — funktioniert auch für KiCad-Versionen, die das Manifest nie gesehen
   hat.

→ Manifest = schneller, bekannter Pfad. Empirik = Sicherheitsnetz für die Zukunft. Zusammen
**ohne Pflege pro Release** tragfähig.

### 2.3 Install — constrained statt „latest", host-bewusst
- Resolvete Versionen als **pip-Constraints** verwenden — **nicht** `--upgrade …latest`.
- `--ignore-installed` **behalten** (0.4.2's gutes Stück: `_deps` self-contained), aber an
  resolvete Versionen gebunden → „force in `_deps`" heißt nicht mehr „force LATEST".
- Natives nur als Wheel (`--only-binary`) für die C-Klasse → fehlendes Wheel scheitert
  **sofort und klar** statt im aussichtslosen Source-Build (Windows = kein Compiler).

### 2.4 Verify — gegen die *echte* Laufzeit, zwei Oberflächen
- **Import-Verify (`-S`, existiert):** fängt fehlende/kaputte Natives in `_deps`-Isolation.
  **behalten.**
- **Handshake-Verifies (der neue, entscheidende Teil) — gegen die echten Anker B und A:**
  - **MCP** (existiert: `server_probe`): `initialize`+`tools/list` gegen den echten
    Claude-CLI → fängt `mcp`/`fastmcp`↔CLI-Skew.
  - **kipy↔KiCad (neu):** `from kipy import KiCad; KiCad().get_version()` gegen das laufende
    KiCad — **im GUI-Kontext** (gemischte Laufzeit), damit auch der `protobuf`-Doppelimport
    auffällt.
- Die Handshakes validieren gegen die **Realität**, bleiben also für unbekannte künftige
  Versionen korrekt. Kein Lockfile kann das, weil die Anker A/B auf der **User-Maschine**
  liegen.

### 2.5 Self-heal — die Schleife schließen
- Handshake scheitert + als Versions-Skew klassifiziert → **re-resolve + passende Version
  re-installieren** (bounded retry / Walk-back), dann re-verify.
- Konvergiert nicht → **lauter, handlungsweisender** Hinweis statt stillem „nichts orange":
  „kipy X passt nicht zu KiCad Y → brauche kipy Z" bzw. „MCP-Protokoll des Claude-CLI zu neu
  für fastmcp → fastmcp updaten".
- Ergebnis unter dem Fingerprint **cachen** → warm = 0 Arbeit, Upgrade = automatisch.

---

## 3. Single-Copy / Host bevorzugen (gegen ABI-/Doppelimport-Konflikte)
- `_inject_local_deps` ändern: `_deps` **nur voranstellen, wenn** das Modul nicht schon aus
  einer Host-/3rdparty-Quelle importierbar ist, **die den Handshake besteht**. KiCads
  eigenes kipy bevorzugen, wenn vorhanden und kompatibel. Nie blind überschreiben.
- Bündelt KiCad künftig den API-Client selbst, adoptiert „Host bevorzugen" ihn **ohne
  Code-Änderung**.

---

## 4. Was es konkret löst (Mapping auf die Fehlerklassen)

| Klasse / Fehlerbild | gelöst durch |
|---|---|
| **A** kipy↔KiCad-Skew (Links tot) | Detect(KiCad) → Resolve(kipy-Range) → kipy-Handshake-Verify + Self-heal |
| **B** mcp/fastmcp↔Claude-CLI (`failed: kicad-mcp`) | MCP-Handshake-Verify (server_probe) im Resolve-Loop + Self-heal |
| **C** pandas/numpy/native ↔ KiCad-Python (Import-Crash) | Constraints-Set statt latest + Wheel-only + `-S`-Import-Verify |
| protobuf-Doppelimport | Single-Copy / Host bevorzugen (GUI-Kontext-Verify deckt's auf) |
| transitive Major-Brüche (pydantic v1→v2 …) | Constraints-Set friert das getestete Bündel ein |

---

## 5. Ehrliche Grenzen (was es NICHT magisch löst)
- **API-Code-Drift:** Entfernt ein künftiges kipy eine Funktion, die `board_links` nutzt, ist
  das Plugin-vs-API-Code-Drift — **kein** Versions-Auswahl-Problem. Der laute Feature-/
  Handshake-Check macht's wenigstens **sichtbar** statt still. Diese Dimension braucht
  weiterhin Code-Pflege.
- **Echter Konflikt:** Hat KiCads Prozess bereits ein zu unserem kipy inkompatibles protobuf
  geladen, lässt sich das nicht still heilen — „Host bevorzugen" wählt das passende, sonst
  **muss** der laute Check den echten Konflikt melden.
- **Empirik braucht Netz** (PyPI) für den Walk-back; offline → Manifest/Cache.

---

## 6. Migrationsweg (risikoarm, behält 0.4.2-Gutes)
1. **Detect + Fingerprint + Cache** einziehen (reine Mess-/Cache-Schicht, ändert noch nichts
   am Install).
2. **Constraints-Set** statt `--upgrade …latest` (Klasse C sofort entschärft), `-S`-Verify
   bleibt.
3. **kipy↔KiCad-Handshake-Verify** + **Single-Copy/Host-bevorzugen** (Klasse A + protobuf).
4. **Self-heal-Loop** + lautes Mismatch-Reporting (schließt B und A selbstkorrigierend).
5. **Manifest self-updatebar** an den bestehenden Updater hängen (Zukunfts-Daten ohne
   Code-Release).

Jeder Schritt ist einzeln testbar und liefert für sich Wert; 0.4.2's `_deps`-Self-
Containedness bleibt erhalten, nur „latest" wird durch „resolved + verified" ersetzt.

---

## TL;DR
Nicht pinnen, nicht „latest" — **die Umgebung messen (KiCad/Python/CLI), Versionen daraus
ableiten, gegen die echten Handshakes verifizieren, bei Skew selbst heilen, das Ergebnis je
Environment-Fingerprint cachen, und Host-Kopien bevorzugen.** Damit reagiert das System auf
jedes künftige KiCad-/kipy-/CLI-Upgrade automatisch — das Problem ist strukturell erledigt,
nicht nur für heute.
