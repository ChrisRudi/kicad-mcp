# Versionsübersicht — Claude für KiCad (Plugin)

Was jede Version gebracht hat, in einfacher Sprache. Neueste zuerst.
Aktuelle Version: **0.12.5**

---

## 🧩 Neu in 0.12.5 — die Layout-Regeln steuern den Generator (aus einer Liste)

- **Die Schaltplan-Regeln sind jetzt fest im Generator verdrahtet — aber
  gesteuert aus der einen wartbaren Liste.** Der Generator arbeitet die
  Regeln (keine Überlappung, ≥5 mm Leitung, Leitung folgt Pin, Raster) ab,
  indem er über das Regel-Set läuft, statt die Schritte fest im Code zu haben.
- **Vorteil:** eine Regel hinzufügen, entfernen oder umsortieren = die Liste
  ändern; die Reihenfolge und Auswahl der Durchsetzung folgt automatisch.
- Verhalten unverändert geprüft: alle 10 Demo-Schaltpläne weiterhin **null**
  Überlappungen und **null** zu-kurze Leitungen.

---

## 📏 Neu in 0.12.4 — Leitungen folgen dem Pin + alle Layout-Regeln an einem Ort

- **Die Mindest-Leitung folgt der Pin-Richtung:** die 5-mm-Leitung verläuft
  jetzt entlang der Richtung, in der der Anschluss aus dem Bauteilkörper
  austritt — geradlinig aus dem Pin statt schräg wegzuknicken.
- **Alle Schaltplan-Layout-Regeln als wartbares Set:** die Konventionen
  (keine Labels, GND unten/VCC oben, Stecker außen, keine Überlappung,
  ≥5 mm Leitung, Leitung folgt Pin …) stehen jetzt zentral und dokumentiert an
  einer Stelle statt verstreut im Code — leichter zu pflegen und zu erweitern.

---

## 🔌 Neu in 0.12.3 — jede Verbindung hat eine sichtbare Leitung (≥ 5 mm)

- **Nie mehr Pin direkt an Pin.** Verbundene Bauteile werden so platziert, dass
  zwischen ihren Pins immer mindestens ~5 mm Leitung sichtbar ist — kein
  Aneinanderkleben ohne erkennbaren Draht.
- Gilt für direkt verdrahtete Signal-Pins verschiedener Bauteile; Power-Pins
  (die über GND-/VCC-Symbole gehen) und zwei Pins desselben ICs sind
  ausgenommen (die kann man nicht auseinanderziehen).
- Läuft Hand in Hand mit der Überlappungs-Garantie: geprüft, dass alle 10
  Demo-Schaltpläne zugleich **null** Überlappungen und **null** zu-kurze
  Leitungen haben.

---

## 📐 Neu in 0.12.2 — Bauteile liegen nie mehr übereinander

- **Garantie: kein Bauteil überlappt ein anderes im Schaltplan.** Der bisherige
  „sanfte" Auseinanderschieber konnte bei sehr großen Symbolen (ein volles
  Mikrocontroller-Symbol ist über 8 cm hoch) endlos hin- und herschieben, ohne
  sauber zu trennen. Jetzt gibt es einen harten Nachlauf: große Symbole sind
  Anker, jedes weitere Bauteil rückt ringförmig nach außen, bis es frei steht.
- Nebenbei war der Auseinanderschieber vorher **nicht rotations-bewusst** und
  übersah gedrehte Bauteile — auch das ist behoben.
- Geprüft: alle 10 Demo-Schaltpläne jetzt mit **null** Überlappungen.

---

## 🧭 Neu in 0.12.1 — Lesbarere Schaltpläne: eng platziert, GND unten, VCC oben

- **Bauteile clustern jetzt eng ums IC, gedreht, mit kurzen echten Leitungen —
  keine Netz-Label-Wüste.** Der bessere Platzierer (er stellt Bauteile wie von
  Hand nacheinander an die Stelle mit den kürzesten Drähten) war bisher von
  einem schwächeren Ersatz verdeckt; jetzt läuft er.
- **GND-Symbole zeigen immer nach unten, Versorgungs-Symbole (VCC/+5V/…) immer
  nach oben** — die vertraute Konvention. Vorher drehte der Generator sie mit
  dem Pin mit, sodass GND auch mal seitwärts zeigte.
- **Ein-/Ausgangs-Stecker sitzen außen, die Leitung läuft nach innen zur
  Schaltung** — Signalfluss links→rechts, wie man es erwartet.
- Wirkt auf alle erzeugten Schaltpläne, nicht nur die Demos.

---

## 🧰 Neu in 0.12.0 — Demo-Menü mit 10 echten Schaustück-Schaltungen

- **Der Demo-Knopf ist jetzt ein Auswahlmenü** mit 10 Beispielschaltungen,
  nach Themen gegliedert (Analog & Simulation, Digital & Schnittstellen,
  Leistung & Norm, Spezial-Layout, Fertigung & Methode). Beim Aufklappen eines
  Bausatzes siehst du seine Skill-Folge mit Begründung — was passiert und
  welche Super-Skills mithelfen.
- **Jede Demo baut wirklich ein Board.** Die 10 Schaltungen sind an frei
  verfügbaren, publizierten Referenz-Designs orientiert und bewusst minimal
  gehalten: Audioverstärker (LM386), USB-C Sensor-Hub (STM32), AC-DC-Netzteil
  (Flyback), LED-Ring (WS2812, rund), Motor-Treiber (DRV8871), Buck-Wandler
  (MP1584), Ethernet-Gerät (LAN8720), Skizze→Kupfer, Serienreife & Kosten,
  Datenblatt & Foto → Schaltung (NE555). Klick startet sie — Schaltplan +
  Platine werden sichtbar angelegt, dann zeigt der Chat die Skill-Folge.
- **Bausatzsystem:** dieselben 10 Schaltungen sind zugleich saubere Startpunkte
  für ein neues Projekt.
- Zusammen decken die 10 Demos alle ~34 Super-Skills mindestens einmal ab —
  jeder Skill hat ein Projekt, in dem er wirklich gebraucht wird.

---

## 🎨 Neu in 0.11.0 — Schaltung als Vorlage: du zeichnest, der MCP merkt sich und baut

- **Deine Lieblings-Schaltung einmal zeichnen, immer wieder bauen.** KiCad 10
  kann Schaltpläne nicht selbst zeichnen (die Live-API ist leer) — also
  zeichnest **du** einen schönen Block (ein LDO-Frontend, einen MCU-Reset,
  was auch immer), und der MCP merkt ihn sich unter einem Namen.
- **Drei neue Werkzeuge:**
  - „Vorlage speichern" liest deinen gezeichneten `.kicad_sch` (Bauteile +
    Netze über KiCads eigene Netzliste) und legt ihn persistent ab.
  - „Vorlagen auflisten" zeigt, was du bisher gemerkt hast (Name,
    Beschreibung, Bauteil-/Netz-Zahl).
  - „Vorlage bauen" macht daraus auf Knopfdruck ein komplettes Projekt —
    Schaltplan **und** Platine, Pins/Footprints automatisch aufgelöst.
- **Ende-zu-Ende gegen echtes KiCad geprüft:** gezeichnet → als Vorlage
  „Mein LDO" gemerkt (7 Bauteile / 4 Netze) → Board gebaut (11 Footprints /
  27 Symbole). Der Kreis schließt sich: einmal schön, beliebig oft.

---

## 🩹 Neu in 0.10.3 — Demo-Board: sauber platziert (wirkte „leer")

- **Der Demo-Schaltplan und die -Platine waren nie leer** — sie hatten alle
  Bauteile, aber das Auto-Layout stapelte sie zu einem winzigen, überlappenden
  Klumpen (drei Footprints auf demselben Punkt). Beim Öffnen sah man ein
  fast leeres Blatt mit einem Fleck.
- **Jetzt sauber vorplatziert:** Regler in der Mitte, links der Eingang
  (Stecker, Eingangs-C), rechts der LED-Zweig (Vorwiderstand, LED), Testpunkt
  oben — ein echter Links-nach-rechts-Stromfluss in Schaltplan UND Platine,
  mit gerouteten Leiterbahnen. Per Screenshot geprüft.
- Technisch: Der PCB-Platzierer respektiert jetzt explizite Positions-Hints
  (wie der Schaltplan schon), sodass eine bewusst gestaltete Vorlage nicht
  vom Auto-Layout zerdrückt wird.

---

## 🩹 Neu in 0.10.2 — Feld-Feedback: Einzelbuttons, mehr Kontrast, Demo repariert

- **Ein Button pro Super-Feature** (statt Kategorie-Dropdown): alle Features
  liegen als einzelne Knöpfe da, nach Kategorie gruppiert (farbiger
  Gruppen-Titel); Hover zeigt die Beschreibung unten — kein Klick nötig.
- **Kräftigerer Kontrast:** Text und Gruppenfarben deutlich dunkler, Knöpfe
  klarer von der Fläche abgesetzt — das helle Design war zu blass.
- **ngspice-Ampel entfernt:** unten stehen nur noch MCP und IPC (die im
  Chat-Alltag zählen); der SPICE-Status lebt in der Diagnose weiter.
- **Demo-Knopf repariert:** lief mit „ModuleNotFoundError: kicad_mcp" ins
  Leere, weil das GUI-Python das Server-Paket nicht auf dem Pfad hat. Der
  Demo läuft jetzt — wie der Systemtest — als Subprozess mit korrektem
  Pfad-Bootstrap.

---

## 🎨 Neu in 0.10.1 — Design A „Werkbank": das Panel sieht jetzt aus wie KiCad

- **Heller, nativer Look** statt dunklem Terminal: helle Fläche, dunkle
  Systemschrift, dünne Ränder — das Panel wirkt wie ein eingebautes
  KiCad-Werkzeug, nicht wie eine fremde App.
- **Klickbare Board-Links in KiCad-Blau** (statt Orange) — signalisiert
  „das kannst du anklicken" so, wie man es aus KiCad kennt. Die Marken-Farbe
  (warmes Orange) bleibt als sparsamer Akzent: Überschriften, Eingabe-Pfeil,
  Feature-Tag.
- **Gruppenfarben** der Super-Features für hellen Grund abgestimmt, Ampeln
  und Knöpfe im nativen Stil.

---

## 🔀 Neu in 0.10.0 — das KI-Backend ist wählbar (Claude ist nicht das Produkt)

- **Das Produkt ist der KiCad-Assistent, nicht ein bestimmtes Modell.** In den
  Einstellungen gibt es jetzt oben „KI-Backend": **Claude Code** (erprobter
  Standard) oder **Codex (OpenAI)** — jedes MCP-fähige Agenten-CLI lässt sich
  ergänzen. Die Wahl gilt ab dem nächsten Chat-Zug.
- **Sauber getrennt:** Jedes Backend kapselt nur, was sich unterscheidet —
  wie es startet, wie es den kicad-mcp-Server registriert (Claude:
  `--mcp-config`, Codex: `[mcp_servers]` in einer TOML) und wie es seinen
  Ereignis-Stream ausgibt. Der gemeinsame Ablauf bleibt gleich; alle
  Board-Werkzeuge, Links und Super-Features funktionieren unabhängig vom
  Backend.
- **Ehrlich:** Claude Code ist voll erprobt und getestet. **Codex ist
  experimentell** und im Feld noch ungetestet (das Ereignisformat kann sich
  ändern) — bitte als solches behandeln und Rückmeldung geben. Der
  Claude-Pfad ist unverändert.

---

## ▶ Neu in 0.9.2 — Demo-Knopf: ein Klick baut die Testschaltung

- **Neuer „▶ Demo"-Knopf** in der Feature-Leiste: Er lässt genau die
  Schaltung, die auch der Systemtest nutzt, automatisch vor deinen Augen
  entstehen — in vier Schritten: **Idee → Schaltplan → Berechnung →
  Platine**. Jeder Schritt erscheint live im Verlauf.
- **Ohne Modell-Kontingent, immer gleich:** Der Ablauf ruft die echten
  Generierungs-Werkzeuge direkt (kein Claude-Zug nötig) — ideal fürs
  Onboarding („zeig mir, was das kann") und als 30-Sekunden-Vorführung,
  die bewiesen lauffähig ist.
- Die **Berechnung** ist echt: der LED-Vorwiderstand wird aus den
  Bauteilwerten nachgerechnet ((3,3 V − 2,0 V) / 1 kΩ = 1,3 mA) und
  bewertet — die semantische Schicht an einem Mini-Beispiel.
- Ehrlich: KiCad 10 kann per Schnittstelle **kein** Dokument öffnen, daher
  nennt die Demo am Ende den Pfad zum Öffnen (Datei → Öffnen).

---

## 🩹 Neu in 0.9.1 — „Systemtest braucht viel RAM?" (fünfter Feld-Report)

- **Was da Speicher braucht (und dass es wieder verschwindet):** Der
  Systemtest ist ein kompletter Server-Prozess (186 Werkzeuge + pandas),
  liest beim Generieren echte KiCad-Symbolbibliotheken und startet für den
  Connectivity-Schritt einen pcbnew-Arbeitsprozess. Alles davon endet mit
  dem Testlauf — nichts bleibt im Speicher zurück. Damit das keine
  Vermutung bleibt, **misst der Report jetzt den Peak-RAM selbst**
  („Peak-RAM: 136 MB (transient)" im Kopf).
- **Ein echter Speicher-Fresser ist gefixt:** Der Symbol-Cache hielt bis zu
  64 KOMPLETTE Bibliotheksdateien im Speicher (Stock-Libs sind bis ~40 MB
  pro Datei — potenziell Gigabytes, und das betraf auch den dauerhaft
  laufenden Warm-Server!). Jetzt sind es maximal 8 — Wiederhol-Symbole
  kommen fast immer aus einer Handvoll Libs, der Nutzen bleibt.
- **Ruhigerer Output:** Die „coroutine was never awaited"-Warnungen im
  Systemtest-Fenster waren Alarm ohne Information (bekanntes Tool-Rauschen)
  und sind gefiltert. Und die Diagnose behauptete hartkodiert „167 Tools" —
  jetzt steht dort die echte Zahl (186).

---

## 🔬 Neu in 0.9.0 — Systemtest ohne Claude (orchestrierbar auf vielen Rechnern)

- **Ein Klick prüft die ganze Maschinerie — ohne Claude, ohne Kontingent:**
  Der neue „🔬 Systemtest"-Knopf im Einrichtungs-Fenster erzeugt aus einer
  eingebauten JSON-Vorlage ein Demo-Board (Spec → Schaltplan → Platine) und
  schickt es durch die ECHTEN Werkzeuge: Tool-Registry (186), Generatoren,
  Schaltplan-/PCB-Lesen, Netzliste, Pad-Geometrie, Text-Patch (Via),
  Connectivity (wenn pcbnew da), DRC (wenn kicad-cli da) und zum Schluss
  den echten Server-Start, wie Claude ihn macht. Dauer: ~1 Minute.
- **Für Feldtests auf N Rechnern gebaut:** Kommandozeile
  `"<KiCad-Python>" -m kicad_mcp.selftest --out <ordner>` — keine GUI,
  keine Rückfragen. Bei Erfolg EINE Zeile und Exit-Code 0; nur bei Fehlern
  werden die roten Schritte ausgegeben (Exit 1). Der Report liegt als
  `selftest_report.md` + `.json` im Ausgabeordner — maschinenlesbar, zum
  Einsammeln und Zurücklesen. Fehlende Extras (pcbnew, kicad-cli) sind
  SKIP, nie FAIL.
- **Abgrenzung:** Der 🧪-E2E-Test prüft das *Claude-Verhalten* (teuer,
  15-45 min); der 🔬-Systemtest prüft das *Produkt darunter* (schnell,
  kostenlos) — zusammen decken sie den ganzen Stack ab.
- Die Demo-Vorlage (`selftest_board.json`: 5V→3V3-LDO mit LED und
  Testpunkt) ist zugleich ein Startpunkt für eigene Projekte aus Specs.

---

## 🎨 Neu in 0.8.7 — schöneres Chat-Panel (Markdown, Farben, klickbare Ampeln)

- **Antworten werden jetzt formatiert gerendert:** **fett** ist fett,
  Überschriften sind orange hervorgehoben, Listen bekommen saubere •-Punkte,
  `Code` und Codeblöcke liegen auf abgesetzter Fläche in Blau, `---` wird
  eine Trennlinie — statt roher `**`/`#`-Markdown-Zeichen im Text.
- **Board-Links bleiben voll funktionsfähig** (die Auflage!): Die
  Link-Erkennung läuft in jedem formatierten Abschnitt weiter — auch eine
  **fette** Referenz oder ein Netz in einer Überschrift ist klickbar und
  markiert/zoomt im Editor. Einzige Ausnahme: Codeblöcke bleiben
  buchstaben-treu roh (fürs 📋-Kopieren).
- **Super-Feature-Gruppen mit Farbakzent:** Verstehen=blau, Layout=violett,
  Elektrik=gelb, Fertigung=grün, Simulation=rosa, Kreativ=türkis — die
  Leiste ist auf einen Blick scanbar.
- **Ampeln unten sind jetzt größer, einzeln erklärt und klickbar:** jede
  Ampel (MCP/IPC/ngspice) hat ihren eigenen Tooltip und ihre eigene Farbe
  (grün läuft / rot kaputt / grau unbekannt); ein Klick öffnet direkt die
  Einrichtung mit Diagnose und Fixes.

---

## 🩹 Neu in 0.8.6 — „MCP NICHT verbunden" war eine Fehldiagnose (vierter Feld-Report!)

- **Der Befund aus deinem zweiten E2E-Lauf:** Wieder 34/34 „FAIL: MCP nicht
  verbunden" — aber diesmal zeigt das Log klar: die Features benutzten
  nachweislich Board-Werkzeuge (Stromtragfähigkeit, Design-Prüfung,
  Sicherheitsabstände, BOM …). **Der Server WAR verbunden.** Das frühe
  Startsignal von Claude behauptete nur etwas anderes (Kaltstart: der
  Status wird gemeldet, bevor der Server fertig hochgefahren ist) — und
  dieses eine falsche Signal stempelte den ganzen Zug: ⚠-Warnung, sinnloser
  doppelter Durchlauf (jedes Feature lief 2×!), Test-Verdikt FAIL.
- **Fix — die Wahrheit kommt von den Werkzeugen, nicht vom Startsignal:**
  Sobald ein einziges kicad-mcp-Werkzeug wirklich läuft, gilt der Zug als
  verbunden (ein nicht verbundener Server kann keine Werkzeuge anbieten) —
  Statuszeile korrigiert sich zu „MCP verbunden — Board-Tools laufen".
  Zusätzlich zählt beim Startsignal nur noch unser eigener Server, und
  „verbindet noch" wird nicht mehr als Fehler gewertet.
- **Effekt:** Keine falschen ⚠-Warnungen mehr, keine verdoppelten
  Feature-Läufe (halbe E2E-Dauer, halbes Kontingent), und der E2E-Report
  zeigt endlich echte PASS/WARN-Verdikte statt pauschal FAIL.

---

## 🩹 Neu in 0.8.5 — E2E-Lauf: 34/34 „MCP nicht verbunden" im Warm-Modus (dritter Feld-Report!)

- **Der Befund:** Der erste echte E2E-Lauf lief durch — aber jedes einzelne
  Feature scheiterte mit „MCP nicht verbunden", und der stdio-Fallback
  sprang nie an. Zwei blinde Flecken im Warm-Modus:
  1. Der Gesundheits-Check prüfte nur „Prozess lebt + Port offen" — ein
     Server, der den Port hält, aber MCP nicht (mehr) beantwortet, galt
     ewig als gesund und wurde nie ersetzt.
  2. Der stdio-Fallback griff nur, wenn der Warm-Server-**Start** scheiterte.
     Sah der Server gesund aus, kam aber Claude trotzdem nicht rein, liefen
     alle Wiederholungen in dieselbe Wand.
- **Fix 1 — echter MCP-Ping:** Vor jeder Wiederverwendung und nach jedem
  Start muss der Warm-Server jetzt ein echtes MCP-`initialize` beantworten
  (Millisekunden, lokal). Ein stummer/fremder/aufgehängter Server wird
  erkannt, gekillt und ersetzt.
- **Fix 2 — stdio-Rettungsleiter:** Meldet Claude auf einem http-Versuch
  trotzdem „MCP failed", schaltet der nächste Versuch hart auf stdio um
  („⚠ Warm-Server läuft, aber Claude kommt nicht rein — dieser Zug läuft
  über stdio …"). Der Chat (und der E2E-Test) kann damit nie mehr an einem
  kaputten http-Pfad verhungern — er wird nur langsamer und die Diagnose
  zeigt, woran http krankt.

---

## 🩹 Neu in 0.8.4 — der 🧪-E2E-Knopf war im Feld tot (zweiter Feld-Report!)

- **Der Befund:** Klick auf „🧪 E2E-Test" im Einrichtungs-Fenster — und
  nichts passiert, nicht einmal die Bestätigungs-Abfrage. Ursache: Eine
  Code-Zeile importierte das Plugin unter seinem **Repo-Namen** (`plugin.…`),
  installiert heißt der Ordner aber `claude_kicad`. Der Klick starb also im
  Feld sofort — und nur dort: in der Entwicklungsumgebung (und allen Tests)
  stimmt der Name zufällig, deshalb blieb es unsichtbar.
- **Doppelt gefixt:** (1) Der Import ist jetzt namensunabhängig (relativ),
  ein neuer Wächter-Test verbietet solche absoluten Selbst-Importe im ganzen
  Plugin für immer. (2) Stirbt künftig irgendein Knopf im
  Einrichtungs-Fenster, erscheint der Fehler als kopierbarer Dialog
  („Plugin-Fehler") statt eines stummen Nichts — tote Knöpfe können sich
  nicht mehr verstecken.

---

## 🩹 Neu in 0.8.3 — Warm-Modus steckte den stdio-Fallback an (erster Feld-Report!)

- **Der Befund aus deiner Diagnose (danke!):** Mit eingeschaltetem Warm-Server
  (`http`) schlug die Diagnose-Probe rot fehl — `Errno 10048`, Port 8331
  belegt. Ursache: Der Transport-Schalter wirkt über die Umgebung und war
  „ansteckend" — auch Prozesse, die ausdrücklich stdio sprechen sollten
  (die Diagnose-Probe und der automatische stdio-Fallback des Chats), erbten
  `http`, versuchten Port 8331 zu binden und antworteten nie auf stdio.
- **Fix: Transport wird überall explizit festgenagelt.** Die stdio-Config
  pinnt `KICAD_MCP_TRANSPORT=stdio` in ihrem Env-Block, die Probe startet
  mit `--transport stdio` (Argument schlägt Umgebung) — der Warm-Server
  selbst nutzt weiterhin seinen eigenen freien Port, nie starr 8331.
  Diagnose-Probe und stdio-Fallback funktionieren damit auch im http-Modus.

---

## 🧪 Neu in 0.8.2 — der Loop durchs Produkt (E2E-Selbsttest)

- **Ein Klick testet ALLE 34 Super-Features gegen dein echtes Board.** Im
  Einrichtungs-Fenster gibt es jetzt „🧪 E2E-Test": Jedes Feature läuft
  nacheinander als echter Claude-Zug durch den kompletten Produktpfad
  (Bridge → Server → Board → Antwort), mit Live-Fortschritt.
- **Ohne Risiko:** Der Testmodus verbietet jede Mutation — Features enden
  mit ihrem Plan am Go-Gate oder mit einer ehrlichen „Voraussetzung
  fehlt"-Meldung (die als korrekt zählt). Dein Board bleibt unangetastet.
- **Der Report schließt den Kreis:** Ergebnis ist
  `<Projekt>/.kicad-mcp/e2e_report.md` (+ JSON) — je Feature Verdikt
  (PASS/WARN/FAIL), benutzte Werkzeuge, Dauer, Fehler und Antwort-Auszug,
  Probleme zuerst. Diese Datei dem Entwicklungs-Agenten zurückgeben →
  er verbessert daraus das TATSÄCHLICHE Verhalten der Features.
  Ehrlich: ein Lauf dauert 15-45 Minuten und verbraucht Claude-Kontingent.

---

## 📈 Neu in 0.8.1 — Simulation ohne Extra-Installation + Hover im Gruppen-Menü

- **Simulation nutzt jetzt KiCads eigene ngspice-Bibliothek.** Warum überhaupt
  „zusätzlich ngspice", wenn KiCad simulieren kann? Weil Eeschemas Simulator
  ein reines GUI-Feature ist — KiCad 10 bietet dafür keine API und kein
  CLI-Kommando, für Claude war er unerreichbar. Die **Bibliothek** dahinter
  (`libngspice`, liegt jedem KiCad bei) ist aber erreichbar: `run_spice_sim`
  lädt sie jetzt direkt (isoliert in einem Kindprozess, damit ein
  Simulations-Absturz nie den Tool-Server mitreißt). Ein separates
  ngspice-Binary ist damit **nicht mehr nötig** — es bleibt als bevorzugtes
  Backend, falls installiert.
- **Gruppen-Menü mit Hover-Erklärung:** Die kompakte Gruppen-Darstellung
  bleibt — aber wenn du im aufgeklappten Menü über einem Feature stehst,
  zeigt die Statuszeile unten sofort dessen Beschreibung. Kein Klick nötig,
  um zu wissen, was ein Button tut.

---

## 🎛️ Neu in 0.8.0 — das große GUI-Update

- **🌍 Automatische Mehrsprachigkeit:** Das Panel spricht jetzt Deutsch ODER
  Englisch — automatisch nach KiCads Sprach-Einstellung bzw. System-Locale
  (umstellbar in den Einstellungen). Auch Claudes Antworten folgen der
  gewählten Sprache.
- **📦 Super-Features gruppiert:** Statt der 34-Button-Wand gibt es sechs
  Gruppen-Buttons (Verstehen & Prüfen · Layout & Skizze · Elektrik & Norm ·
  Fertigung & Kosten · Simulation · Kreativ & Brücken) — Klick öffnet das
  Menü der Gruppe. Eine Zeile statt vier, das Transkript hat wieder Platz.
- **⌨️ Mehrzeilige Eingabe:** Enter sendet, Shift+Enter bricht um — und
  eingefügte mehrzeilige Prompts kommen jetzt VOLLSTÄNDIG an (vorher wurde
  an der ersten Zeile abgeschnitten).
- **🔘 Antwort-Chips:** Wartet Claude auf eine Entscheidung, erscheinen
  Klick-Buttons unter der Antwort („Go" / „Verwerfen" / Varianten) — kein
  Tippen mehr. Enthält die Antwort Code (SPICE-Deck, Firmware-Header),
  gibt es 📋-Kopier-Buttons je Block.
- **↺ Unterhaltung überlebt:** Die Session wird pro Projekt gemerkt — Panel
  schließen und wieder öffnen setzt das Gespräch fort. „🆕 Neu" startet
  bewusst frisch.
- **🚦 Ampel-Zeile:** MCP · IPC · ngspice als Status-Punkte im Fuß — „läuft
  es gerade?" auf einen Blick, ohne Diagnose-Report.
- **⚙️ Einstellungen im Einrichtungs-Fenster:** Sprache, Transport
  (stdio/Warm-Server), ngspice-Pfad und Schritt-Limit als GUI-Seite statt
  Umgebungsvariablen. Hand-gesetzte Env-Variablen behalten Vorrang.
- **Aufgeräumt:** Das Freitext-Optionsfeld ist weg — Claude-Optionen laufen
  nur noch über das kuratierte ⚙-Dropdown (mit „zurücksetzen"-Eintrag und
  sichtbarer Anzeige der aktiven Schalter); „🔗 Auswahl einbeziehen" erklärt
  sich jetzt per Hover.

---

## 🩹 Neu in 0.7.8 — „Kein eindeutiges Board" nach Folge-Abfragen behoben

- **Der Bug:** Nach mehreren Abfragen hintereinander starben plötzlich alle
  Klick-Links der früheren Antworten und unten stand „Kein eindeutiges Board"
  — obwohl das Board offen war. **Ursache:** War KiCads API während schneller
  Folge-Abfragen kurz beschäftigt, hielt der Tool-Server das für „kein Editor
  offen" und startete unsichtbar einen ZWEITEN Editor. Zwei Instanzen auf dem
  API-Bus → jede Board-Abfrage mehrdeutig → alle Links tot.
- **Dreifach behoben:** (1) Der Server wartet bei so einem kurzen Aussetzer
  jetzt mit Backoff, statt sofort zu starten. (2) Läuft der Chat im
  KiCad-Plugin, ist das Auto-Starten eines Editors komplett gesperrt
  (`KICAD_MCP_NO_AUTO_OPEN=1`, setzt das Plugin automatisch). (3) Taucht die
  Geister-Instanz doch auf, **heilen sich die Links selbst**: Beim nächsten
  Klick wird der vom Server gestartete Editor erkannt, beendet und die
  Verbindung neu aufgebaut.
- **Warum die alte Geister-Abwehr (0.2.20) das nicht fing:** Sie kannte nur
  Editoren, die in der Prozess-Registry standen — und ausgerechnet der
  Auto-Start-Pfad hat seine nie eingetragen. Jetzt trägt JEDER vom Server
  gestartete Editor sich ein (ein Wächter-Test erzwingt das), womit
  Aufräumen beim Schließen UND die neue Selbstheilung ihn finden.

---

## 🔌 Neu in 0.7.7 — Schutzklassen (34. Super-Feature, Normwerte als Werkzeug)

- **🔌 Schutzklassen ist live** (Werkzeug Nr. 186, `get_safety_spacing`): Prüft
  das Isolationskonzept deines Geräts — Schutzklasse I/II/III nach IEC 61140
  bestimmen, dann je Spannungsgrenze die **geforderten Kriech- und
  Luftstrecken** nachschlagen: Netz-Nennspannung + Überspannungskategorie →
  Stoßspannung → Luftstrecke; Arbeitsspannung + Verschmutzungsgrad +
  Materialgruppe (FR-4 = IIIa) → Kriechstrecke; Klasse II automatisch mit
  verstärkten Werten (Kriechweg ×2, Stoßspannungs-Stufe höher).
- **Die Normzahlen stecken als datierter Snapshot im Werkzeug** (IEC-60664-1-
  Tabellen F.1/F.2/F.4, gegen publizierte Normauszüge quergeprüft) — nicht im
  Modellgedächtnis. Beispiel 230-V-Netz, OVC II, FR-4: Basis 1,5 mm Luft /
  2,5 mm Kriechweg; Klasse II verstärkt 2,0 / 5,0 mm.
- Auch der ⚡ Sicherheitsabstände-Button nutzt jetzt diese Snapshot-Werte statt
  Gedächtnis-Richtwerte. Ehrlich bleibt: Ingenieurs-Vorprüfung, keine
  Zertifizierung — Produktnormen (62368-1, 60335-1, 60601-1) können abweichen.

---

## 📈 Neu in 0.7.6 — echte SPICE-Simulation + Geister-Vorschau beim Entwirren

- **📈 Simulation rechnet jetzt wirklich** (Werkzeug Nr. 185, `run_spice_sim`):
  Claude baut aus deinem Schaltplan ein SPICE-Deck und führt es mit **ngspice**
  aus (gefunden über PATH, KiCad-Ordner oder `KICAD_MCP_NGSPICE`) — echte
  Arbeitspunkte, Verstärkungen, Eckfrequenzen statt nur Abschätzung. Ist
  ngspice nicht installiert, sagt der Button das ehrlich und liefert die
  analytische Analyse plus das fertige Deck zum Kopieren.
- **🧶 Entwirren zeigt eine Geister-Vorschau:** Vor deinem Go erscheinen die
  geplanten Zielpositionen als Kreuz-Marker mit Bauteilnamen auf dem
  Skizzen-Layer („MCP.Skizze") — du siehst die neue Anordnung direkt auf dem
  Board. Nach Umsetzung oder Ablehnung wird die Vorschau automatisch
  weggeräumt.

---

## ✨ Neu in 0.7.5 — ALLE 33 Super-Features aktiv

Die komplette Leiste ist jetzt orange: jeder Button startet einen fertigen,
geführten Auftrag. Neu dazugekommen (v1):

- **🔀 Pin-Tausch** (Vorschläge; Umsetzung nur nach Go), **🧭 Netz-Navigator**,
  **📐 Ausrichten & Anordnen** (Plan → Go → ein Zug), **👁️ Mitdenken-Review**
  (bewertet deine letzten Handänderungen), **🔤 Silk-Aufräumen**.
- **⌚ Quarz-Load-Caps**, **🌡️ Thermik**, **🌡️ Betriebstemperatur**,
  **📐 Slew-Rate**, **〰️ Impedanz**, **📉 MLCC-Derating** — Physik mit
  offengelegten Annahmen; Datenblatt-Werte aus `docs/` oder per Nachfrage.
- **🏭 DFM-Check**, **💰 Kosten-Schätzer**, **⚡ Sicherheitsabstände**
  (IEC-62368-Vorprüfung), **💾 Firmware-Pinmap** (C-Header/DeviceTree/ESPHome
  zum Kopieren).
- **📈 Simulation** (analytisch + SPICE-Deck zum Kopieren — ehrlich: noch keine
  numerische SPICE-Ausführung), **🧬 SPICE-Modelle** und **🛒 Bauteil-Sourcing**
  (beide mit Web-Suche), **📄 Datenblatt→Schaltung** (PDF → Schaltungsblock,
  Einbau nach Go), **📷 Foto→Schaltung** (Foto ins Projekt legen, Pfad nennen).

Überall gilt: Mutationen nur nach deinem **Go**, Annahmen werden offengelegt,
bei Unsicherheit wird gefragt statt geraten, jede v1-Grenze steht im Bericht
(Details: `docs/superfeatures.md`, Abschnitt „Stand 0.7.5").

---

## 🔥 Neu in 0.7.4 — Stromtragfähigkeit (13. Super-Feature, neues Werkzeug)

- **🔥 Stromtragfähigkeit ist live** — das erste Feature mit einem eigens dafür
  gebauten Rechen-Werkzeug (`check_ampacity`, Werkzeug Nr. 184): Es prüft jede
  Leiterbahn-Breite gegen den Strom, den ihr Netz tragen soll (IPC-2221,
  einstellbarer Temperaturanstieg und Kupferdicke, Innenlagen strenger).
  Der Klick liefert erst das Breiten-Inventar, dann legt Claude seine
  Strom-Annahmen offen (und fragt bei Unsicherheit nach), dann kommt der
  Prüfbericht: welches Segment auf welchem Layer zu schmal ist und wie breit
  es sein müsste. Ehrliche Grenze: bewertet Leiterbahnen, noch keine
  Kupferflächen.

---

## 📄 Neu in 0.7.3 — vier weitere Super-Features aktiv (jetzt 12)

- **📄 Datenblatt-Abgleich** — IC markieren, klicken: die Beschaltung wird gegen
  das Datenblatt (`docs/<Value>.pdf` im Projekt) geprüft — Entkopplung,
  Pin-Beschaltung, fehlende externe Bauteile. Ohne Auswahl zeigt es erst,
  welche Datenblätter vorliegen und welche fehlen (mit Download-URL).
- **💡 Board erklären** — rekonstruiert Funktionsblöcke, Schnittstellen und
  Stromversorgung aus Netzliste + Bauteilen; mit Auswahl gezielt den
  markierten Teilschaltkreis.
- **⊙ Polar-Board** — zeigt die Polar-Grid-Parameter (Zentrum, Ringe, Speichen)
  und den Radial-Workflow für runde Boards; platziert wird erst auf dein Go.
- **🖊️ Skizzen-Layer** — zeigt, was auf dem gemeinsamen Skizzen-Layer (User.9)
  liegt, und bietet Legende zeichnen oder Leeren an (erst nach deinem Go).

---

## 🧶 Neu in 0.7.2 — Entwirren aktiv, klare Auswahl-Regel, Optionen-Dropdown

- **🧶 Entwirren ist live** (das achte aktive Super-Feature): Ein Klick liest das
  Board einmal, entwirrt die Bauteil-Platzierung **im Kopf** (geprüft am
  Kreuzungs-Scorer, ohne das Board anzufassen), zeigt dir den Plan mit
  Score vorher → nachher — und ordnet erst nach deinem **Go** alles in einem
  Zug an.
- **Eine Auswahl-Regel für alle Super-Features:** Nichts markiert → das Feature
  wirkt aufs **ganze Board**. Etwas markiert → das Panel zeigt beim Klick an,
  worauf der Zug wirkt („🎯 Wirkt auf deine Auswahl: R1, C3, U2") und das
  Feature beschränkt sich genau darauf. Der separate Button „Auswahl
  entwirren" ist damit überflüssig und entfällt.
- **Aktive Features sind jetzt ORANGE beschriftet** — auf einen Blick sichtbar,
  was echt klickbar ist; „kommt bald" bleibt gedimmt.
- **Claude-Optionen mit Dropdown:** Neben dem Freitextfeld gibt es jetzt eine
  Auswahl sinnvoller Schalter (Modell Sonnet/Opus/Haiku, Fast-Modus,
  Fallback-Modell). Die Liste wird **dynamisch** aus `claude --help` deiner
  installierten Version gefiltert — es wird nie ein Schalter angeboten, den
  deine CLI nicht kennt. Auswahl ersetzt einen vorhandenen Schalter gleichen
  Namens statt ihn zu doppeln.

---

## ✨ Neu in 0.7.1 — die ersten 7 Super-Features sind AKTIV

Die Super-Feature-Buttons unter dem Chat waren bisher alle „kommt bald". Jetzt
sind die sieben, deren Werkzeuge längst an Bord sind, **echt verdrahtet**: ein
Klick startet den fertigen Auftrag im Chat — mit deiner aktuellen KiCad-Auswahl
als Kontext (markierst du vorher Bauteile, wirkt das Feature nur darauf):

- **🛡️ Design-Wächter** — semantische Prüfung jenseits des ERC (Pull-ups,
  Load-Caps, Entkopplung).
- **🚌 Bus-Radar** — alle Teilnehmer + Pins je Bus (I²C, SPI, UART …).
- **🔎 Test-Punkt-Wächter** — kritische Netze ohne Prüfpunkt-Zugang, Abdeckung in %.
- **💰 BOM-Konsolidierung** — fast-gleiche R/C-Werte auf E-Reihen-Standard (nur Vorschlag).
- **🏭 Fab-Standardteile** — No-Load-Fee-Vorzugsteile (JLCPCB Basic …) + Ersparnis.
- **🔩 Via-Optimierung** — Report, welche Blind/Buried-Vias zu Through wandelbar
  sind; umgesetzt wird erst auf dein Go.
- **✏️ Skizzen-Dirigent** — deine Linien/Bögen auf User.9 werden per Klick zu
  Kupferbahnen auf F.Cu (ein Undo-Schritt; leerer Layer → ehrliche Meldung).

Die übrigen Buttons zeigen weiterhin ihren „kommt bald"-Pitch.

---

## 🔥 Neu in 0.7.0 — Warmer Tool-Server (opt-in)

- **Der Tool-Server kann jetzt dauerhaft warm laufen.** Bisher startet Claude
  den kicad-mcp-Server bei **jeder** Chat-Nachricht neu (stdio) — der Kaltstart
  ist genau die Quelle des „MCP nicht verbunden"-Wacklers. Neu: mit
  `KICAD_MCP_TRANSPORT=http` startet das Plugin den Server **einmal pro
  KiCad-Sitzung** als lokalen HTTP-Dienst (nur `127.0.0.1`, mit zufälligem
  Zugriffs-Token) und Claude **verbindet sich nur noch** — kein Spawn, kein
  Kaltstart pro Nachricht.
- **Selbstheilend:** Vor jedem Chat-Zug wird die Gesundheit geprüft; ein
  abgestürzter/hängender Server wird automatisch ersetzt, Waisen nach einem
  KiCad-Absturz beim nächsten Start aufgeräumt, und beim Schließen von KiCad
  wird der Server sauber beendet. Klappt der Warm-Start nicht, läuft der Zug
  automatisch im bisherigen stdio-Modus weiter.
- **Diagnose zeigt den Server-Status** (läuft? PID, Port, Uptime, echter
  MCP-Ping) — die Info, die bisher fehlte.
- **Standard bleibt vorerst `stdio`** (das bisherige Verhalten, unverändert).
  Nach Validierung auf echten Windows-Setups wird der Warm-Modus der Standard;
  Rückweg ist immer ein Env-Wort: `KICAD_MCP_TRANSPORT=stdio`.

---

## 🩹 Neu in 0.6.1 — MCP-Kaltstart heilt sich selbst

- **„MCP nicht verbunden" verschwindet meistens von allein.** Der Tool-Server
  wird pro Nachricht frisch gestartet; der **erste** Start nach Update/Neustart
  ist langsam (Windows Defender scannt jede neue `.pyd`) und lief manchmal in
  einen stillen Fehlschlag — Antwort dann **ohne** Board-Werkzeuge. Neu: schlägt
  der Start fehl, wird der Zug **automatisch einmal neu gestartet** (der zweite
  Start ist warm und klappt fast immer). Sicher, weil ein Zug ohne Tool-Server
  am Board nichts geändert hat. Abschaltbar via `KICAD_MCP_CONNECT_RETRIES=0`.

---

## 🆕 Neu in 0.6.0 — Super-Features (Bedeutung statt nur Geometrie)

Neue Werkzeuge, die Claude im Chat nutzen kann — Dinge, die KiCad selbst nie
könnte:

- **🛡️ Design-Wächter** (`audit_design`) — findet stille Fehler jenseits des ERC:
  I²C-Bus ohne Pull-ups, Quarz ohne Load-Caps, IC-Versorgungspin ohne (oder mit
  zu weit entfernter) Entkopplung, Reset ohne Pull-up.
- **🚌 Bus-Radar** (`list_bus_members`) — listet alle Teilnehmer + Pins eines
  Busses (I²C, SPI, UART …) als eine Bedeutungseinheit.
- **💰 BOM-Konsolidierung** (`consolidate_bom`) — legt fast-gleiche R/C-Werte auf
  E-Reihen-Standardwerte zusammen → weniger Feeder, günstigere Bestückung.
- **🏭 Fab-Standardteile** (`suggest_preferred_parts`) — mappt R/C aufs
  No-Load-Fee-Teil des Fertigers (JLCPCB Basic …) und schätzt die Ersparnis.
- **🔎 Test-Punkt-Wächter** (`audit_test_points`) — meldet kritische Netze
  (Versorgung, Reset, Clock, Bus) ohne Prüfpunkt-/Stecker-Zugang.

---

## Auf einen Blick — die wichtigsten Bausteine

| Bereich | Was es kann |
|---|---|
| 💬 **Chat im PCB-Editor** | Frag Claude in normaler Sprache über dein offenes Board; Antworten im KiCad-Look, andockbar |
| 🔗 **Klickbare Links** | Bauteile, Netze, Pins, Layer und Koordinaten in der Antwort anklicken → KiCad markiert & zoomt hin |
| ⚙️ **Live am Board** | Claude liest und ändert das offene Board direkt (über die kicad-mcp-Werkzeuge) |
| 🛡️ **Sicher & gemeinsam** | Claude pfuscht nicht an Dateien, beide Seiten können speichern, Prozesse räumen sauber auf |
| 🩺 **Selbst-Diagnose** | Einrichtungs-Check + „Diagnose"-Knopf finden Fehler und zeigen den echten Grund |

---

## 🔗 Klickbare Elemente im Chat (Cross-Probe)

- **0.2.26** — Links wieder zuverlässig, auch während Claude aktiv ist (Verbindungs-Konkurrenz mit dem Server abgefangen).
- **0.2.23** — **Bauteil-Pins** klickbar: `U1B.33` markiert Pin 33 von Bauteil U1B.
- **0.2.21** — **Layer-Namen** klickbar: Klick setzt den aktiven Layer in KiCad.
- **0.2.18** — **Koordinaten** klickbar: springt zum nächstgelegenen Element an dieser Stelle.
- **0.2.17** — Start: **Bauteile & Netze** in der Antwort werden Links → markieren + hinzoomen. Löst „ich finde das Teil auf der großen Platine nicht".

## 💬 Aussehen & Bedienung

- **0.2.22** — **Stopp-Knopf** (laufende Antwort abbrechen), **Optionen-Feld** für Claude-Schalter (z. B. `--model sonnet`), **Tool-Aufrufe** erscheinen live im Verlauf.
- **0.2.16** — **Live-Mitschrift** der Antwort + Abbruch nur noch bei echtem Stillstand statt nach starren 5 Minuten.
- **0.2.7** — Chat-Panel **dockt** wie ein echtes KiCad-Fenster an (anheften, abreißen, verschieben).
- **0.2.6** — Chat im **Claude-Code-Look** (dunkles Terminal, Monospace, Orange, Spinner). Kein schwarzes Konsolenfenster mehr.

## 🛡️ Sicherheit & gemeinsames Arbeiten

- **0.2.24** — Claude darf **keine Shell/Dateien** mehr anfassen (Sperre repariert + Windows-PowerShell ergänzt), Verhaltensregeln werden mitgegeben, **Schritt-Limit** gegen Endlosläufe.
- **0.2.20** — Claude- und Server-Prozesse werden beim **Schließen von KiCad sauber beendet** (kein Zombie).
- **0.2.19** — **Disk-Schutz**: solange das Board in KiCad offen ist, blockiert der Server Direkt-Datei-Änderungen (sonst Speicher-Konflikt). Mutationen laufen live über IPC — beide Seiten speichern stimmig.
- **0.2.11** — Claude bekommt **Datei-Schreibverbot** (Mutationen nur über die Board-Werkzeuge), und der Server-Start wird mit einem echten Handshake geprüft.
- **0.2.9** — Abhängigkeiten landen im **plugin-eigenen Ordner**; bloßes Reden mit dem Server markiert das Board **nicht mehr als geändert**.

## 🩺 Einrichtung, Diagnose & Stabilität

- **0.2.25** — **„MCP nicht verbunden"-Ursache gefunden**: Kaltstart-Timeout (Windows-Defender scannt die frischen Dateien). Timeout großzügig erhöht, Probe misst jetzt die echte Zeit.
- **0.2.15** — **Kern-Fix**: KiCads Python ignorierte eine Pfad-Variable → Server-Start umgestellt, läuft seitdem.
- **0.2.14** — **„Diagnose"-Knopf**: sammelt alle Pfade/Versionen/den echten Fehler in einen kopierbaren Report.
- **0.2.10–0.2.13** — Abhängigkeits-Installation selbst-diagnostizierend; präzise Meldung bei unvollständiger Installation; Zielordner sichtbar.
- **0.2.8** — Erkennt einen **nicht startenden Server**, statt still ohne Werkzeuge weiterzumachen.
- **0.2.0–0.2.5** — Grundgerüst: Plugin + gebündelter Server, Einrichtungs-Check, robuste KiCad-Python-Erkennung, Selbst-Update aus dem Repo.

---

## ⚙️ Server & Werkzeuge (kicad-mcp, ohne eigene Plugin-Nummer)

- **Ein-Klick-Installskripte** (`install_plugin.bat` / `.sh`) — Plugin direkt aus dem Repo installieren.
- **Agent-Regeln** in `CLAUDE.md` gegen „Toolcall-Explosion" (nicht nach jeder Mini-Änderung rendern, bündeln, bei Stillstand abbrechen).
- **`add_vias_to_pcb`** — viele Vias in einem Rutsch setzen (statt N Einzel-Calls).
- **`ipc_markup_to_tracks`** — von Hand gezeichnete Linien auf der Markup-Ebene (User.9) in Kupfer-Leiterbahnen umwandeln.
- **`ipc_get_selection`** — „was ist gerade in KiCad ausgewählt?" ins Gespräch holen.
- **Zentraler Verbindungs-Layer** — eine wiederverwendete, robuste IPC-Verbindung (schneller, Timeout konfigurierbar, Retry bei „KiCad busy", Datei-Log).

---

*Vollständige technische Details: siehe `CHANGELOG.md`.*
