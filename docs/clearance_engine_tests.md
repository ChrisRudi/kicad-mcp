# Clearance-Engine — Test- & Lokal-Lauf-Runbook

Handoff für den **lokalen** Claude (auf der Festplatte, mit installiertem
KiCad 10.0). Dieser Branch wurde auf dem Server gebaut, wo **kein `pcbnew`**
verfügbar ist — die eigentliche Kollisions-Geometrie konnte dort nicht
ausgeführt werden. Lokal hast du KiCads gebündeltes Python **mit** `pcbnew`,
also laufen dort die scharfen Tests. Dieses Dokument sagt dir, wie.

> TL;DR: (1) diesen Branch ziehen, (2) Tests unter **KiCads** Python laufen
> lassen, (3) prüfen dass die `@_needs_pcbnew`-Tests **passen statt skippen**,
> (4) bei Weiterarbeit `sync_bundle` + pylint nicht vergessen.

---

## 1. Diesen Stand vom Server holen

Branch: **`claude/affectionate-lamport-p2n3a1`**

```bash
git fetch origin claude/affectionate-lamport-p2n3a1
git switch claude/affectionate-lamport-p2n3a1     # oder: git checkout …
git pull origin claude/affectionate-lamport-p2n3a1
```

Sanity-Check, dass der Stand da ist:

```bash
git log --oneline -1
#   → 1009570 Clearance-Engine: gemeinsamer Kurzschluss-Check …
ls kicad_mcp/tools/clearance_worker.py kicad_mcp/tools/clearance_tools.py
ls tests/test_clearance_tools.py
```

---

## 2. Was in diesem Stand steckt (Kurzfassung)

Neue, geteilte **Clearance-Engine** + Verdrahtung als `clearance`-Effekt-Echo
in die Kupfer-mutierenden Disk-Tools. Details im `CHANGELOG.md` (`[Unreleased]`).

| Datei | Rolle |
|---|---|
| `kicad_mcp/tools/clearance_worker.py` | Warm-`pcbnew`-Daemon. `SHAPE.Collide` gegen Fremdnetz-Kupfer. Zwei Modi: **targeted** (nur neue Items) / **board-wide** (grid-gebinnt). Reiner Read, lazy `pcbnew`-Import. |
| `kicad_mcp/tools/clearance_tools.py` | Tool `check_clearance` + `attach_clearance()`/`check_clearance_impl()` (Verdrahtung). `attach_clearance` ist total: wirft nie, kippt nie `success`. |
| `tests/test_clearance_tools.py` | Diese Tests. |

Verdrahtet (je ein optionaler Parameter `check_clearance=True`, sonst keine
Signatur-Brüche): `add_track_to_pcb`, `add_arc_to_pcb`, `add_via_to_pcb`,
`add_vias_to_pcb`, `add_zone_pour_to_pcb`, `via_retype`, `via_resize`,
`pcb_batch`. IPC-Live-Tools bewusst **noch nicht** (Disk-first).

---

## 3. Tests laufen lassen

Es gibt **zwei** Python-Umgebungen, und sie testen **unterschiedliche** Pfade.

### 3a. Schneller Lauf (generisches Python / `.venv`) — `pcbnew`-Tests SKIPPEN

Das ist, was auf dem Server lief. Validiert Spec-Builder, Degradations-Pfad,
Tool-Surface und die Verdrahtung — **nicht** die Kollisions-Geometrie.

```bash
python -m pytest tests/test_clearance_tools.py -v
#   → 14 passed, 8 skipped   (die 8 @_needs_pcbnew skippen mangels pcbnew)
```

### 3b. Voller Lauf unter **KiCads** Python — `pcbnew`-Tests LAUFEN ⭐

Das ist der **wichtige** lokale Schritt. KiCads gebündeltes Python hat `pcbnew`
eingebaut, also führen die `@_needs_pcbnew`-Tests die echte `SHAPE.Collide`-
Geometrie aus.

**Windows (cmd / PowerShell):**

```bat
REM einmalig: pytest in KiCads Python verfügbar machen, falls noch nicht
"C:\Program Files\KiCad\10.0\bin\python.exe" -m pip install pytest

REM die Clearance-Tests, ausführlich
"C:\Program Files\KiCad\10.0\bin\python.exe" -m pytest tests\test_clearance_tools.py -v
```

**WSL (KiCads Windows-Python aus WSL aufrufen):**

```bash
KPY="/mnt/c/Program Files/KiCad/10.0/bin/python.exe"
"$KPY" -m pytest tests/test_clearance_tools.py -v
```

> Pfad-Hinweis: Der Pfad zu `python.exe` kann je nach Installation abweichen
> (`/10.0/`, anderes Laufwerk). `start_mcp_wsl.sh` zeigt die Kandidaten-Liste
> und respektiert die Env-Variable `KICAD_PYTHON_PATH` — dieselbe hier nutzbar.

**Volle Suite** unter KiCads Python (wie CI, nur eben mit `pcbnew`):

```bat
"C:\Program Files\KiCad\10.0\bin\python.exe" -m pytest tests\ -q --no-cov
```

---

## 4. Erwartete Ergebnisse

Welche Tests laufen vs. skippen, hängt **bewusst** von der Umgebung ab — die
Skip-Logik ist in beide Richtungen verdrahtet (siehe die `skipif`-Marker oben
in der Test-Datei). Das ist **kein** Fehler:

| Test(klasse) | ohne `pcbnew` (Server/.venv/CI) | mit `pcbnew` (lokal KiCad) |
|---|---|---|
| `TestSpecBuilders` (4) | ✅ läuft | ✅ läuft |
| `TestAttachClearance` (3) | ✅ läuft (`no_pcbnew`-Fall aktiv) | ✅ 2 laufen, `no_pcbnew`-Fall **skippt** |
| `TestImplValidation` (2) | ✅ läuft | ✅ 1 läuft, `no_pcbnew`-Fall **skippt** |
| `TestCheckClearanceTool` (2) | ✅ läuft | ✅ läuft |
| `TestGeometryWiring` (3) | ✅ läuft (Echo = `{checked: False}`) | ✅ läuft (Echo = echtes Ergebnis) |
| `TestWorkerTargeted` (4) | ⏭️ **skippt** | ✅ **läuft** ⭐ |
| `TestWorkerBoardWide` (2) | ⏭️ **skippt** | ✅ **läuft** ⭐ |
| `TestWorkerViaUuid` (2) | ⏭️ **skippt** | ✅ **läuft** ⭐ |

- **ohne `pcbnew`:** `14 passed, 8 skipped`
- **mit `pcbnew`:** `20 passed, 2 skipped` (die 2 expliziten „no-pcbnew"-Fälle
  skippen, weil `pcbnew` ja da ist)

➡️ **Das Erfolgskriterium lokal:** die mit ⭐ markierten 8 Tests müssen
**passen** (nicht skippen). Skippen sie trotzdem, wird `pcbnew` nicht gefunden →
du läufst nicht unter KiCads Python (zurück zu 3b).

---

## 5. Was die Tests abdecken

`tests/test_clearance_tools.py`:

- **`TestSpecBuilders`** — reine Spec-Bauer (`via_spec` / `seg_spec` /
  `arc_specs`); kein `pcbnew`. Form/Defaults der Item-Specs.
- **`TestAttachClearance`** — der Total-Kontrakt: `enabled=False` →
  `{checked: False, reason: "disabled"}`; fehlende Datei kippt `success`
  **nicht**; ohne `pcbnew` rein advisory.
- **`TestImplValidation`** — `check_clearance_impl`: fehlende Datei →
  strukturierter Fehler; ohne `pcbnew` → `{checked: False}`.
- **`TestCheckClearanceTool`** — MCP-Tool-Surface: fehlender Pfad + kaputtes
  `items`-JSON → strukturierte Fehler (kein Crash).
- **`TestGeometryWiring`** — End-to-End durch `add_via_to_pcb`: `clearance`-Key
  vorhanden, **Edit passiert trotzdem**; `dry_run` und `check_clearance=False`
  überspringen den Check sauber.
- **`TestWorkerTargeted`** ⭐ — Via-Kreis auf Fremdnetz-Pad → Verletzung;
  Via in Freiraum → sauber; gleiches Netz → keine Verletzung; **Track-Segment**
  über Fremdnetz-Pad → Verletzung (übt den `SHAPE_SEGMENT`-Pfad).
- **`TestWorkerBoardWide`** ⭐ — sauberes Board → 0; Board mit echtem
  Different-Net-Short (Track kreuzt Fremd-Pad) → ≥1 Verletzung.
- **`TestWorkerViaUuid`** ⭐ — `via_uuid`-Auflösung (der Pfad hinter
  `via_retype`/`via_resize`): findet die Via per uuid, liest **ihr** Netz,
  flaggt das Fremd-Pad; unbekannte uuid → sauber, kein Fehler.

---

## 6. `pcbnew`-Spot-Check (remote NICHT ausgeführt)

Die Worker-Logik ist eng an den **bewährten** `via_promote_worker` angelehnt
(gleiche Idiome: `GetClass()=="PCB_VIA"`, `GetEffectiveShape`,
`shape.Collide(subject, clr)`, `m_Uuid.AsString()`, `GetWidth(TopLayer())`,
`CuStack()`, `GetPadName()` — letzteres durch `test_via_promote` auf KiCad 10.0
bewiesen). **Neu** in der Clearance-Engine und daher remote nie gelaufen sind
nur diese Aufrufe — falls ein Test mit `AttributeError`/falscher Signatur
fällt, liegt hier die KiCad-10-API-Differenz:

| `pcbnew`-Aufruf | wo | abgedeckt durch |
|---|---|---|
| `board.FindNet(name).GetNetCode()` | Netz-Name → Code, Same-Net-Skip | `TestWorkerTargeted` |
| `pcbnew.VECTOR2I(int, int)` (aus mm) | Subjekt-Koords | targeted (alle) |
| `pcbnew.SHAPE_CIRCLE(VECTOR2I, r)` | Via-Subjekt | `test_via_over_foreign_pad…` |
| `pcbnew.SHAPE_SEGMENT(VECTOR2I, VECTOR2I, w)` | Track-Subjekt | `test_track_over_foreign_pad…` |
| `other.Collide(<SEGMENT>, clr)` | Segment als Argument | `test_track_over_foreign_pad…` |
| `shape.BBox()` → `GetLeft/Right/Top/Bottom` | board-wide Grid | `test_detects_different_net_short` |
| `via.m_Uuid.AsString()` (Match) | `via_uuid`-Auflösung | `TestWorkerViaUuid` |

Defensive Auslegung: Jeder dieser Aufrufe ist in `clearance_worker.py` von
`try/except` umgeben — ein API-Mismatch degradiert (Item übersprungen / Label
fällt zurück), statt zu crashen. Falls ein ⭐-Test deshalb **falsch sauber**
meldet (skip-frei, aber `ok=True` wo eine Verletzung erwartet wird), ist das
das Signal, den betroffenen Aufruf an die lokale KiCad-10-API anzupassen.

Optional als Live-Gegenprobe (gegen ein echtes Board mit bekanntem Short):

```bat
"C:\Program Files\KiCad\10.0\bin\python.exe" -c "from kicad_mcp.tools.clearance_worker import run; import json; print(json.dumps(run(r'C:\pfad\zu\board.kicad_pcb', None, 0.2), indent=2))"
```

---

## 7. Beim Weiterentwickeln lokal

Vor dem Commit **immer**:

```bash
# 1) gebündelten Server spiegeln (plugin/mcp/ muss == kicad_mcp/ sein)
python scripts/sync_bundle.py
python scripts/sync_bundle.py --check     # muss "synchron" melden

# 2) Lint wie CI (0 Errors/Warnings)
pylint --rcfile=pyproject.toml --score=n --disable=C,R kicad_mcp tests

# 3) Volle Suite (unter KiCads Python, damit auch die pcbnew-Tests zählen)
"C:\Program Files\KiCad\10.0\bin\python.exe" -m pytest tests\ -q --no-cov
```

- Neues Tool? → `EXPECTED_TOOL_COUNT` in `tests/test_tool_audit.py` mitbumpen
  (steht aktuell auf **174**) + `CHANGELOG.md`-Eintrag. Der Drift-Wächter
  `test_tool_count_locked` und `test_bundle_sync` fangen Vergesslichkeit ab.
- `clearance_worker.py`/`clearance_tools.py` dürfen `pcbnew` **nicht** beim
  Modul-Import ziehen (lazy) — `test_no_heavy_imports_on_tool_module_load`
  bewacht das.

---

## 8. Bewusst offen (nächster Schritt)

Die **IPC-Live-Tools** (`ipc_*`: Via/Track/Zone in den laufenden Editor) sind
**nicht** verdrahtet — „Disk-first, IPC danach", wie abgestimmt. Sie brauchen
einen anderen Pfad (lebendes In-Memory-Board statt Disk-Reload + Daemon), z. B.
einen `check_clearance`-Modus, der das offene kipy-Board prüft statt die Datei.
Das ist die natürliche Fortsetzung, wenn die Disk-Seite lokal verifiziert ist.
