# HOWTO: Footprint-Resync-Tools in deinem kicad-mcp nachbauen

**Ziel:** drei neue MCP-Tools, die headless leisten, was bisher nur GUI-F8 kann — ohne die Flip-Bugs (#10/#11/#16).
Verifiziert gegen **KiCad 10.0.1**. Voraussetzung: dein kicad-mcp-Repo (Struktur `kicad_mcp/tools/`, `tests/`,
Worker-Subprozess-Muster wie `connectivity_worker`). Begleitdoc mit API-Belegen: `KICADMCP_footprint_resync_impl_note.md`.

| Tool | Behebt | Technik | Risiko |
|---|---|---|---|
| `normalize_footprint_libid` | bare lib_id-Header | **Text-Patch** | null (1 Token) |
| `refresh_pinfunctions` | stale Pad-`pinfunction` | **Text-Patch** | null (keine Geometrie/Netze) |
| `replace_footprint_canonical` | Footprint-Ersetzung flip-korrekt | **pcbnew-Worker** | mittel (SaveBoard=Voll-Rewrite) |

---

## 0. Grundregeln (vorab)
- **Tool 1+2 = reiner Text-Patch** (kein pcbnew, kein SaveBoard) → chirurgisch, kein Voll-Rewrite. Das ist die
  bevorzugte Lösung für die kosmetischen Defekte.
- **Tool 3 = pcbnew im Subprozess** (SWIG degradiert im Dauer-Prozess → frischer Prozess pro Aufruf, wie
  `connectivity_tools._run_in_process`). `SaveBoard` rewritet die ganze Datei → immer mit Backup + dry_run.
- **`put_text`/`get_text` sind KEIN Disk-Write** (Cache) — eigener `_persist(path,text)` (open+write, dann
  `put_text`-Sync), wie in `polar_grid_tools`.

---

## 1. Gemeinsamer Schaltplan-Parser (`kicad_mcp/utils/sch_inspect.py`)
Beide Text-Tools brauchen aus der `.kicad_sch`: `ref → Footprint-Property` und `ref → {pin_nr: pin_name}`.

```python
import re

def _block(s, start):
    d = 0
    for i in range(start, len(s)):
        c = s[i]
        if c == '(': d += 1
        elif c == ')':
            d -= 1
            if d == 0: return i + 1
    return len(s)

def schematic_footprint_map(sch_text):
    """ref -> 'Lib:Name' (Footprint-Property der Symbol-Instanzen)."""
    out = {}
    for m in re.finditer(r'\(symbol\b', sch_text):
        fb = sch_text[m.start():_block(sch_text, m.start())]
        rm = re.search(r'\(property "Reference" "([^"]+)"', fb)
        fpp = re.search(r'\(property "Footprint" "([^"]*)"', fb)
        if rm and fpp and fpp.group(1):
            out[rm.group(1)] = fpp.group(1)
    return out

def schematic_pin_names(sch_text):
    """ref -> {pin_number: pin_name}, aufgelöst über den lib_symbols-Cache."""
    # 1) lib_id -> {nr: name} aus (lib_symbols ...)
    lib = {}
    lm = re.search(r'\(lib_symbols\b', sch_text)
    if lm:
        libblk = sch_text[lm.start():_block(sch_text, lm.start())]
        for sm in re.finditer(r'\(symbol "([^"]+)"', libblk):
            sb = libblk[sm.start():_block(libblk, sm.start())]
            pins = {}
            for pm in re.finditer(r'\(pin\b', sb):
                pb = sb[pm.start():_block(sb, pm.start())]
                nm = re.search(r'\(name "([^"]*)"', pb)
                nr = re.search(r'\(number "([^"]*)"', pb)
                if nm and nr: pins[nr.group(1)] = nm.group(1)
            if pins: lib[sm.group(1)] = pins         # key z.B. "iFloat_Custom:DRV8313" oder "DRV8313_1_1"
    # 2) ref -> lib_id, dann mappen
    out = {}
    for m in re.finditer(r'\(symbol\b', sch_text):
        fb = sch_text[m.start():_block(sch_text, m.start())]
        rm = re.search(r'\(property "Reference" "([^"]+)"', fb)
        lid = re.search(r'\(lib_id "([^"]+)"', fb)
        if rm and lid and lid.group(1) in lib:
            out[rm.group(1)] = lib[lid.group(1)]
    return out
```
> Hinweis Multi-Unit: bei mehreren Units pro lib_id ggf. über alle `…_N_M`-Sub-Symbole mergen.

---

## 2. Tool 1 — `normalize_footprint_libid` (`kicad_mcp/tools/footprint_libid_tools.py`)
```python
import re
from kicad_mcp.utils.sch_inspect import schematic_footprint_map, _block
from kicad_mcp.cache.file_cache import get_text          # nur Lesen
# _persist wie in polar_grid_tools (open+write, dann put_text-Sync)

def _persist(path, text):
    with open(path, 'w', encoding='utf-8') as f: f.write(text)
    try:
        from kicad_mcp.cache.file_cache import put_text; put_text(path, text)
    except Exception: pass

def normalize_footprint_libid_impl(pcb_path, sch_path, refs=None, dry_run=True):
    pcb = get_text(pcb_path); sch = get_text(sch_path)
    want = schematic_footprint_map(sch)                  # ref -> 'Lib:Name'
    refset = set(refs) if refs else None
    edits = []; out = []; pos = 0
    for m in re.finditer(r'\(footprint ', pcb):
        st = m.start(); en = _block(pcb, st); fb = pcb[st:en]
        rm = re.search(r'\(property "Reference" "([^"]+)"', fb)
        ref = rm.group(1) if rm else None
        fid_m = re.search(r'\(footprint "([^"]+)"', fb)
        cur = fid_m.group(1)
        new = want.get(ref)
        do = (ref and (refset is None or ref in refset)
              and ':' not in cur and new and ':' in new and new.split(':')[-1] == cur)
        if do:
            edits.append((ref, cur, new))
            fb = fb.replace('(footprint "%s"' % cur, '(footprint "%s"' % new, 1)
        out.append(pcb[pos:st]); out.append(fb); pos = en
    out.append(pcb[pos:]); newtext = ''.join(out)
    if not dry_run and edits: _persist(pcb_path, newtext)
    return {'dry_run': dry_run, 'normalized': [{'ref': r, 'from': a, 'to': b} for r, a, b in edits],
            'count': len(edits)}
```
**Guard:** nur ersetzen, wenn `new.split(':')[-1] == cur` (Name identisch, nur Namespace fehlt) → kann nie den
falschen Footprint zuweisen. Idempotent (`':' in cur` → skip).

---

## 3. Tool 2 — `refresh_pinfunctions` (gleiche Datei oder `footprint_pinfunc_tools.py`)
```python
def refresh_pinfunctions_impl(pcb_path, sch_path, refs=None, dry_run=True):
    pcb = get_text(pcb_path); sch = get_text(sch_path)
    pinmap = schematic_pin_names(sch)                    # ref -> {nr: name}
    refset = set(refs) if refs else None
    changed = []; out = []; pos = 0
    for m in re.finditer(r'\(footprint ', pcb):
        st = m.start(); en = _block(pcb, st); fb = pcb[st:en]
        rm = re.search(r'\(property "Reference" "([^"]+)"', fb)
        ref = rm.group(1) if rm else None
        pins = pinmap.get(ref)
        if not (ref and pins and (refset is None or ref in refset)):
            out.append(pcb[pos:st]); out.append(fb); pos = en; continue
        nb = []; p2 = 0
        for pm in re.finditer(r'\(pad "', fb):
            ps = pm.start(); pe = _block(fb, ps); pad = fb[ps:pe]
            pn = re.search(r'\(pad "([^"]*)"', pad).group(1)
            want = pins.get(pn)
            if want and re.search(r'\.Cu"', pad):
                if re.search(r'\(pinfunction "[^"]*"', pad):
                    newpad = re.sub(r'\(pinfunction "[^"]*"', '(pinfunction "%s"' % want, pad, 1)
                else:  # einfügen direkt nach (net ... ) — KiCad-Reihenfolge
                    newpad = re.sub(r'(\(net \d* ?"[^"]*"\)|\(net "[^"]*"\))',
                                    r'\1\n\t\t\t(pinfunction "%s")' % want, pad, 1)
                if newpad != pad: changed.append('%s.%s->%s' % (ref, pn, want)); pad = newpad
            nb.append(fb[p2:ps]); nb.append(pad); p2 = pe
        nb.append(fb[p2:]); fb = ''.join(nb)
        out.append(pcb[pos:st]); out.append(fb); pos = en
    out.append(pcb[pos:]); newtext = ''.join(out)
    if not dry_run and changed: _persist(pcb_path, newtext)
    return {'dry_run': dry_run, 'changed': changed, 'count': len(changed)}
```
> Achtung Net-Token-Form: dieses Board nutzt **string-form** `(net "name")`; ältere `(net N "name")`. Das Regex
> oben deckt beide ab. Wenn ein Pad keinen `(net …)` hat (paste-only), wird es übersprungen.

---

## 4. Tool 3 — `replace_footprint_canonical` (pcbnew-Worker)
### 4a. Worker `kicad_mcp/tools/footprint_resync_worker.py`
```python
import sys, json
MARK_A, MARK_B = '<<<FPR_JSON>>>', '<<<FPR_END>>>'

def _run(payload):
    import pcbnew
    pcb_path = payload['pcb_path']; jobs = payload['jobs']   # [{ref, lib_nick, fp_name, pretty_dir}]
    b = pcbnew.LoadBoard(pcb_path)
    done = []; errs = []
    for j in jobs:
        try:
            old = b.FindFootprintByReference(j['ref'])
            if old is None: errs.append({'ref': j['ref'], 'err': 'not found'}); continue
            new = pcbnew.FootprintLoad(j['pretty_dir'], j['fp_name'])
            new.SetParent(b)
            new.SetReference(old.GetReference()); new.SetValue(old.GetValue())
            new.SetPath(old.GetPath()); new.SetLocked(old.IsLocked())
            new.SetPosition(old.GetPosition())
            if old.IsFlipped():
                new.Flip(old.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
            new.SetOrientation(old.GetOrientation())        # ABSOLUT, NACH Flip!
            new.SetFPID(pcbnew.LIB_ID(j['lib_nick'], j['fp_name']))
            new.FixUpPadsForBoard(b)
            oldpads = {p.GetNumber(): p for p in old.Pads()}
            for np in new.Pads():
                op = oldpads.get(np.GetNumber())
                if op:
                    np.SetNet(op.GetNet())
                    np.SetPinFunction(op.GetPinFunction()); np.SetPinType(op.GetPinType())
            # Verifikation VOR commit: jede gemeinsame Pad-Nr <1µm?
            drift = []
            for np in new.Pads():
                op = oldpads.get(np.GetNumber())
                if op:
                    dx = abs(np.GetPosition().x - op.GetPosition().x)
                    dy = abs(np.GetPosition().y - op.GetPosition().y)
                    if max(dx, dy) > 1000:                  # 1µm in nm
                        drift.append({'pad': np.GetNumber(), 'dx_nm': dx, 'dy_nm': dy})
            if drift:
                errs.append({'ref': j['ref'], 'err': 'pad drift', 'drift': drift}); continue
            b.Remove(old); b.Add(new); done.append(j['ref'])
        except Exception as e:
            errs.append({'ref': j['ref'], 'err': str(e)})
    if done and not payload.get('dry_run', True):
        pcbnew.SaveBoard(pcb_path, b)
    return {'done': done, 'errors': errs, 'saved': bool(done) and not payload.get('dry_run', True)}

if __name__ == '__main__':
    res = _run(json.loads(sys.argv[1]))
    print(MARK_A + json.dumps(res) + MARK_B)
```
**Kritisch:** die Pad-Drift-Prüfung VOR `b.Add` ist die eingebaute Korrektheits-Absicherung — driftet ein Pad
>1µm gegenüber dem Original, wird der ref **nicht** getauscht (Flip/Orient-Reihenfolge stimmte nicht). So kann
das Tool nie still die Geometrie verbiegen.

### 4b. Server-Wrapper `kicad_mcp/tools/footprint_resync_tools.py`
```python
import sys, json, subprocess, re, os
from kicad_mcp.utils.sch_inspect import schematic_footprint_map
from kicad_mcp.utils.kicad_paths import kicad_python_exe, kicad_lib_root   # repo-Helper

def _pretty_dir(lib_nick):
    # fp-lib-table-Auflösung; Minimal: Bündel-Lib unter <root>/footprints/<nick>.pretty
    return os.path.join(kicad_lib_root(), 'footprints', lib_nick + '.pretty')

def replace_footprint_canonical_impl(pcb_path, sch_path, refs, dry_run=True):
    want = schematic_footprint_map(__import__('kicad_mcp.cache.file_cache',
            fromlist=['get_text']).get_text(sch_path))
    jobs = []
    for ref in refs:
        fid = want.get(ref)
        if not fid or ':' not in fid: continue
        nick, name = fid.split(':', 1)
        jobs.append({'ref': ref, 'lib_nick': nick, 'fp_name': name, 'pretty_dir': _pretty_dir(nick)})
    payload = {'pcb_path': pcb_path, 'jobs': jobs, 'dry_run': dry_run}
    proc = subprocess.run([kicad_python_exe(), '-m',
                           'kicad_mcp.tools.footprint_resync_worker', json.dumps(payload)],
                          capture_output=True, text=True,
                          cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                          env={**os.environ, 'PYTHONPATH': os.getcwd()})
    m = re.search(r'<<<FPR_JSON>>>(.*?)<<<FPR_END>>>', proc.stdout, re.S)
    if not m:
        return {'success': False, 'stderr': proc.stderr[-2000:], 'stdout': proc.stdout[-500:]}
    return {'success': True, **json.loads(m.group(1))}
```
> `kicad_python_exe()` = euer Helper, der das **standalone** `python.exe` von KiCad findet (WSL:
> `/mnt/c/Program Files/KiCad/10.0/bin/python.exe`). NICHT der Server-Interpreter.

---

## 5. Registrierung
In `tool_registry.py` / `register_*` (analog `register_connectivity_tools`):
```python
@server.tool()
def normalize_footprint_libid(pcb_path: str, schematic_path: str, refs: list[str] = [], dry_run: bool = True):
    "Präfixt bare Footprint-lib_id (\"NAME\"->\"Lib:NAME\") aus dem Schaltplan. Text-Patch, keine Geometrie."
    return normalize_footprint_libid_impl(pcb_path, schematic_path, refs or None, dry_run)

@server.tool()
def refresh_pinfunctions(pcb_path: str, schematic_path: str, refs: list[str] = [], dry_run: bool = True):
    "Schreibt Pad-(pinfunction) aus den Symbol-Pinnamen. Text-Patch, keine Geometrie/Netze."
    return refresh_pinfunctions_impl(pcb_path, schematic_path, refs or None, dry_run)

@server.tool()
def replace_footprint_canonical(pcb_path: str, schematic_path: str, refs: list[str], dry_run: bool = True):
    "Ersetzt Footprints durch die Lib-Version mit korrektem Flip/Placement (echte pcbnew-Engine). SaveBoard=Voll-Rewrite."
    return replace_footprint_canonical_impl(pcb_path, schematic_path, refs, dry_run)
```
Tool-Count im Doc/Tests hochziehen. **MCP-Server neu starten**, damit die Tools in der Liste erscheinen.

---

## 6. Tests (`tests/test_footprint_resync.py`)
```python
def test_normalize_libid_idempotent(scratch_pcb, scratch_sch):
    r1 = normalize_footprint_libid_impl(scratch_pcb, scratch_sch, dry_run=False)
    assert r1['count'] > 0
    r2 = normalize_footprint_libid_impl(scratch_pcb, scratch_sch, dry_run=False)
    assert r2['count'] == 0                              # idempotent

def test_normalize_guard_rejects_namemismatch(...):
    # Footprint, dessen Schaltplan-Name != PCB-Name -> NICHT angefasst
    ...

def test_pinfunction_text_patch(...):
    out = refresh_pinfunctions_impl(...); assert 'U_DRV4.1->CPL' in out['changed']

def test_flip_involution_via_worker(scratch_pcb):
    # Worker mit 1 geflipptem ref im dry_run -> done==[ref], errors==[] (kein pad drift)
    res = replace_footprint_canonical_impl(scratch_pcb, sch, ['U_USBESD1'], dry_run=True)
    assert res['success'] and not res['errors']

def test_worker_rejects_on_drift(monkeypatch):
    # künstliche Lib mit verschobenem Pad -> errors enthält 'pad drift', kein Save
    ...
```
**Scratch-Kopie** der Test-Boards verwenden (nie Original). Subprozess-Test braucht das KiCad-python.exe →
ggf. `@pytest.mark.skipif(not have_kicad_python())`.

---

## 7. Manuelle Abnahme
1. `cp board.kicad_pcb board.bak` (Backup).
2. `python3 _sync_check.py base` (Baseline — Skript liegt in `iFloat_V16_07/mainboard/`).
3. Tool im `dry_run=True` → Report prüfen (welche refs, welche Werte).
4. Tool `dry_run=False`, **U1B in `refs` weglassen** (oder gar nicht `replace_footprint_canonical` auf U1B).
5. `python3 _sync_check.py check` → erwartet: `[ZIEL]` refs normalisiert, `[SCHUTZ]` DRV-Netze/Routing/U1B PASS,
   `[KONTROLLE]` nur intendierte refs, `==> GESAMT: PASS`.
6. KiCad öffnen → Board lädt fehlerfrei; bei Tool 3 zusätzlich DRC.

---

## 8. Git-Workflow
```
git checkout -b feat/footprint-resync
# neue Dateien: utils/sch_inspect.py, tools/footprint_libid_tools.py,
#               tools/footprint_resync_tools.py, tools/footprint_resync_worker.py,
#               tests/test_footprint_resync.py ; tool_registry.py erweitern
pytest tests/test_footprint_resync.py -q
git add -A && git commit   # Server-Restart nicht vergessen
```

---

## 9. Fallstrick-Checkliste (aus den Bug-Klassen #8–#16)
- [ ] **Subprozess** für jeden pcbnew-Aufruf (SWIG-Degradation).
- [ ] **`put_text` ≠ Disk-Write** → eigener `_persist`.
- [ ] **Reihenfolge:** erst `Flip`, dann `SetOrientation` (Flip negiert orient).
- [ ] **Pad-Drift-Gate <1µm** vor `b.Add` (fängt falsche Flip/Orient-Reihenfolge).
- [ ] **`via.GetWidth()` NIE ohne Layer-Arg** (blockierender wx-Assert) — hier nicht nötig, generell.
- [ ] **`SaveBoard` = Voll-Rewrite** → Backup + dry_run + `_sync_check.py`.
- [ ] **string-form Netze** `(net "name")` im Regex berücksichtigen.
- [ ] **Tool 1/2 NICHT** über pcbnew lösen (würde unnötig SaveBoard erzwingen) — Text-Patch.
- [ ] **Idempotenz + Namens-Guard** in Tool 1 (nie falschen Footprint zuweisen).
```
```
