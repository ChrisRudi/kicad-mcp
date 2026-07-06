# Versionsübersicht — Claude für KiCad (Plugin)

Was jede Version gebracht hat, in einfacher Sprache. Neueste zuerst.
Aktuelle Version: **0.25.5**

---

## 👁️ Neu in 0.25.5 — Qualitätsmessung sieht Platzhalter-Bauteile echt

- Die Layout-Messung kannte die wahre Größe generierter Platzhalter-Symbole
  nicht — der Optimierer konnte Bauteile unbemerkt hineinschieben. Jetzt
  rechnet sie mit der exakten Platzhalter-Geometrie.
- Noch mehr Grundabstand zwischen Bauteilen (Faktor 1.8).

---

## 🏷️ Neu in 0.25.4 — Labels sitzen im Kasten, Stecker rücken heran

- **Globale Netz-Labels:** Der Text steht jetzt IM Pfeilkasten statt daneben
  (es fehlten zwei KiCad-Formatfelder in der Ausgabe).
- **Stecker:** J-/P-/X-Verbinder kleben nicht mehr an der Blattkante mit
  riesiger Leerfläche zur Schaltung — sie rücken auf festen Abstand an die
  Schaltung heran.

---

## 📏 Neu in 0.25.3 — Fremde Bauteile rücken auseinander

- Bauteile, die elektrisch nichts miteinander zu tun haben, halten jetzt
  garantiert einen Rasterpunkt (2,54 mm) mehr Abstand als verbundene —
  „Nähe ohne Grund" verschwindet aus den Layouts. Verbundene Bauteile
  dürfen weiterhin dicht beieinander stehen, die gehören ja zusammen.

---

## 🌬️ Neu in 0.25.2 — Mehr Luft um große ICs

- Bauteile rücken großen ICs (ab 10 Pins) nicht mehr auf die Pelle: deren
  Pin-Beschriftungen und Netz-Labels bekommen einen garantierten Freiraum
  von ~5 mm. Beim 2-stelligen Zähler verschwanden damit alle
  Beschriftungs-Kollisionen an den Zähler-ICs.

---

## 🛡️ Neu in 0.25.1 — Kondensator bleibt Kondensator

- **Bug aus dem Universaltest (2-stelliger 99-Hz-Zähler):** Ein 100-nF-
  Kondensator bekam durch die unscharfe Symbolsuche ein MOSFET-Symbol
  („100n" steckt in „BSC100N10NSFG") — mit Kurzschluss als Folge. Jetzt
  dürfen Widerstände, Kondensatoren, Spulen und Dioden bei der Symbolwahl
  nie mehr die Bauteilklasse wechseln. Das elektrische Prüf-Gate hatte den
  Fehler sofort gemeldet — genau dafür ist es da.

---

## 🪞 Neu in 0.25.0 — Wiederholte Schaltungsteile sehen gleich aus

- **Symmetrie-Regel:** Besteht eine Schaltung aus wiederholten gleichen
  Teilen (die zwei Hälften eines Multivibrators, die Glieder einer
  LED-Kette), erkennt der Generator das jetzt und zeichnet jede Instanz
  IDENTISCH — nebeneinander in Leseordnung (D1→D6 statt Zickzack).
  Der Optimierer verschiebt solche Gruppen nur noch als Ganzes.
- **Blatt-Zentrierung:** Das fertige Layout wird auf die Blattmitte
  geschoben statt links oben zu kleben.

---

## 🩹 Neu in 0.24.1 — „Zeichne mir…" funktioniert jetzt auch mit knappen Angaben

- **Feld-Bug behoben:** „Zeichne einen astabilen Multivibrator" schlug fehl,
  weil der Schaltungs-Generator übertrieben vollständige Bauteil-Angaben
  verlangte (Footprint, Pin-Namen, Pin-Typen). Jetzt reicht das, was man
  natürlich sagt — Referenz, Wert, Pin-Nummern; den Rest ergänzt der Server
  sinnvoll (Footprint-Default nach Bauteilklasse).
- Fehlermeldungen nennen jetzt das erwartete Minimalformat, statt den
  Assistenten in Wiederholungsversuche zu treiben.

---

## 🛡️ Neu in 0.24.0 — Codequalität: Kopien abgeräumt und per CI-Wächter verriegelt

- **Alle größeren Code-Kopien im Server konsolidiert** (S-Expression-Helfer,
  Track-Emitter, Datei-Editier-Abschluss, Warm-Worker-Boilerplate,
  Netzlisten-Parser ×2, BOM-Kern, Tool-Präambeln) — und ein neuer
  CI-Wächter lässt ab jetzt keine neue Groß-Kopie mehr durch.
- **102 stille Fehler-Schlucker auditiert:** jeder trägt jetzt seinen Grund
  als Kommentar, sechs diagnose-wichtige Stellen schreiben ins Log,
  sechs fangen gezielter. Felddiagnosen enden nicht mehr im Nichts.
- Schaltplan-Ausgabe nachweislich unverändert (byte-gleich auf allen
  10 Demo-Schaltungen), Roundtrip 10/10.

---

## 🧹 Neu in 0.23.0 — Doppelungen raus, Runde 2 (−650 Zeilen)

- **Eine Bus-Erkennung statt zwei:** Welche Netze zusammen einen Bus bilden
  (I²C, SPI, UART …), entscheidet jetzt überall dieselbe Logik — das
  Bus-Radar und die bus-bewusste Bauteil-Platzierung können nicht mehr
  unterschiedlicher Meinung sein. Schaltplan-Ausgabe nachweislich unverändert
  (byte-gleich auf allen 10 Demo-Schaltungen).
- **Alte PDF-Extraktions-Skripte entfernt** (einmalige Werkzeuge, mit denen
  die Schaltungs-Templates gewonnen wurden — die Templates selbst bleiben).

---

## ⚖️ Neu in 0.22.0 — Ein Qualitäts-Richter statt zwei (−953 Zeilen)

- Der alte Schaltplan-Bewerter (0–100-Punkte, schaute nur auf die geplante
  Platzierung) ist entfernt. Die Benchmark-Werkzeuge messen jetzt das
  **fertig gezeichnete Blatt** mit derselben an Profi-Schaltplänen geeichten
  Metrik, die auch der Layout-Optimierer nutzt — badness 0 = Profi-Niveau,
  mit Aufschlüsselung, WAS genau stört (z. B. „Label 'THR' auf Widerstand").
- Ein Urteil statt zwei, die auseinanderlaufen können; 953 Zeilen weniger
  Code bei identischer Schaltplan-Ausgabe (byte-gleich verifiziert).

---

## 📐 Neu in 0.21.0 — Widerstände & Kondensatoren an GND/VCC stehen senkrecht

- **Neue Zeichenregel:** Ein R, C oder L, der an ein Power-Netz geht, wird
  senkrecht gestellt — Pins oben/unten, das GND-/VCC-Symbol direkt darüber
  bzw. darunter. Genau so zeichnet es jedes professionelle Schaltbild
  (Pull-up-Widerstand, Abblock-Kondensator).
- Der Layout-Optimierer respektiert die Konvention: er darf solche Bauteile
  weiter verschieben und um 180° drehen, aber nicht mehr quer legen.
- Elektrik unverändert bewacht: Netzlisten-Roundtrip 10/10, Ausgabe
  byte-deterministisch.

---

## 🚀 Neu in 0.20.1 — Code-Diät & Tempo (erste Runde)

- **36 % schnellere Schaltplan-Erzeugung** (106 → 68 ms je Emission): die
  Symbol-Auflösung wird jetzt gemerkt statt 528× pro Blatt neu gesucht.
  Ausgabe bleibt byte-identisch.
- **143 Zeilen toter Code entfernt** (vom Netzlisten-Umbau verwaiste
  Router-Altpfade).
- **Optimierungsplan** für weitere Tempo-/Kürzungs-Schritte in
  ``docs/optimierungsplan_schematic.md`` (mit Profil-Messwerten und der
  eisernen Regel: Roundtrip 10/10 + Determinismus nach jedem Schritt).

---

## 🧹 Neu in 0.20.0 — Kosmetik: mehr Luft, Pin-Zonen-Schutz, deterministisch

- **„Einfach mehr Luft lassen":** Platzierungs-Abstände +20 % (Faktor 1.4→1.7)
  und eine neue Enge-Metrik (Körper-Spalt < 2.54 mm zählt als Gedränge, an den
  Profi-Referenzen geeicht). Der BME280 im USB-Hub hat jetzt Platz für seine
  Netz-Beschriftungen.
- **Labels raus aus der Pin-Zone:** Beschriftungen, die längs durch die
  Pin-Nummern-Spalte schreiben oder in ein Bauteil ragen, werden jetzt erkannt
  (Metrik), beim Zeichnen vermieden (Richtungswahl mit Sonde) und vom
  Aufräumer repariert. Heilungs-Labels sitzen an der Stub-Spitze statt am Pin.
- **Keine Fallback-Winkel mehr durch kleine Bauteile:** Der L-Bend-Notweg
  prüft gegen das volle Hindernis-Set; Routen werden zusätzlich geometrisch
  gegen Körper-Innenzonen geprüft.
- **Generator jetzt 100 % deterministisch:** Drei versteckte Zufallsquellen
  (String-Mengen-Iteration in Verbindungsgraph, Cap-Zuteilung und
  Pin-Emission) machten jede Platzierung von Lauf zu Lauf verschieden —
  gefunden und fixiert. Gleiche Eingabe = byte-gleicher Schaltplan.
- Ergebnis: 7/10 Kits badness 0 unter der VERSCHÄRFTEN Metrik; Rest je eine
  Text-Berührung. Netzlisten-Roundtrip weiterhin 10/10.

---

## ⚡ Neu in 0.19.0 — Netzlisten-Roundtrip: gezeichnet = gewollt (10/10)

- **Der neue harte Beweis** (Nutzer-Vorschlag): Aus dem GEZEICHNETEN Schaltplan
  wird mit KiCads eigener Engine eine Netzliste extrahiert und pin-genau mit
  der Soll-Netzliste verglichen. Vorher waren ALLE 10 Beispiel-Schaltungen
  elektrisch falsch (Kurzschlüsse, zerfallene Netze, offene Pins) — obwohl sie
  hübsch aussahen. Jetzt: **10/10 identisch**, auch nach dem Optimierer.
- **Junction-Punkte:** Jeder T-Abzweig trägt jetzt den Verbindungspunkt
  (Nutzer-Regel: „wenn aus einer geraden Leitung eine Leitung abzweigt, muss
  ein Punkt das kennzeichnen").
- **Power-Symbole an jedem Versorgungs-Pin** statt langer Power-Drähte quer
  übers Blatt — wie in Profi-Schaltplänen; GND/VCC vereint KiCad global.
- **Kurzschluss-Prävention beim Zeichnen:** Jede Leitung/jeder Stub wird vor
  dem Zeichnen gegen alle fremden Netze UND alle Pins geprüft; bei Konflikt
  weicht er aus (andere Richtung/Länge) oder das Netz wird per gleichnamigem
  Label verbunden. Bauteile, deren Pins aufeinander gerieten, werden
  automatisch verschoben.
- **Pin-Namen schlagen Pin-Nummern:** Kits adressieren Pins nach Bedeutung
  („IN1", „DRAIN", „NRST") — die Zuordnung zum Bibliotheks-Symbol geht jetzt
  über den Namen (inkl. ~{RST}-Dekoration, TXD0/MODE0-Doppelnamen, gestapelte
  GND-Pins, DRAIN→D-Synonyme). Falsche Kit-Nummern können keine fremden
  Funktions-Pins mehr treffen.
- Dauerhafter Wächter: ``tests/test_netlist_roundtrip.py`` prüft alle 10 Kits
  bei jedem Testlauf (übersprungen ohne kicad-cli).

---

## 🏆 Neu in 0.18.0 — alle 10 Beispiel-Schaltungen mit Bestnote

- **Lesbare Beschriftungen an gedrehten Bauteilen:** KiCad zeichnet den
  Referenz-/Wert-Text relativ zur Bauteil-Drehung — bei liegenden
  Kondensatoren/Widerständen stand „C1" mitten in „10u" als vertikaler
  Buchstabensalat. Jetzt wird der Text gegenrotiert: Referenz oben, Wert
  darunter, waagrecht lesbar — an jedem gedrehten Bauteil, in jeder Schaltung.
- **Kein Riesen-Symbol mehr bei kleinen Bauteil-Beschreibungen:** Die
  Symbol-Suche prüft jetzt, ob die Pin-Zahl des gefundenen Bibliotheks-Symbols
  zur Beschreibung passt. Ein Teil mit 11 beschriebenen Pins bekommt nicht mehr
  das erstbeste 176-Pin-BGA-Monster (der Ethernet-Fall), sondern eine kompakte
  Box in der richtigen Größe. Teile mit passender Pin-Zahl (z. B. der STM32 im
  USB-Hub) behalten ihr echtes Symbol.
- **Ergebnis: 10 von 10 Demo-Schaltungen erreichen badness 0** — zum ersten
  Mal, inklusive Ethernet (vorher 1296).

---

## 🔌 Neu in 0.17.0 — Stubs an den ICs, keine Busse mehr über die Bauteile

- **Nutzer:** „noch immer viele lokale Busse über die Bauteile drübergezeichnet
  und keine Stubs an den ICs???" — beides behoben.
- **Stubs an jedem Pin:** Jeder verdrahtete Anschluss bekommt jetzt eine kurze
  Leitung aus dem Bauteil heraus (wie es ein Profi-Schaltbild zeichnet), bevor
  der Draht abbiegt. Dadurch hat jeder IC-Pin einen sauberen Anschluss-Stummel.
- **Keine Leitungen mehr quer durch die Bauteile:** Der Router startet die
  Verdrahtung jetzt außerhalb des Körpers (an der Stub-Spitze) und modelliert
  gedrehte Bauteile mit der richtigen Breite/Höhe — ein waagrechter Widerstand
  ist jetzt ein waagrechtes Hindernis. So wird kein Bus mehr mitten durch einen
  Widerstand oder IC gezogen.
- **Der Sauberkeits-Check sieht es jetzt auch:** „Leitung quer durchs Bauteil"
  wird korrekt gemessen (vorher blind für Busse über große ICs). Ergebnis:
  9 von 10 Beispiel-Schaltungen mit Bestnote (badness 0); nur das Ethernet-Kit
  bleibt, weil sein STM32-Symbol völlig überdimensioniert aus der Lib kommt
  (ein Symbol-Problem, keine Routing-Frage).

---

## 🔄 Neu in 0.16.1 — Beschriftungen weichen Leitungen aus

- Liegt eine Beschriftung über einer fremden Leitung (oder einem Bauteil), wird
  sie jetzt automatisch auf die andere Seite gedreht/gespiegelt, wo Platz ist —
  die zugehörige kurze Leitung folgt mit. Die Verbindung bleibt exakt gleich.
- Ergebnis: Beschriftungen überdecken keine Leitungen mehr, wo es Platz gibt
  (z. B. USB-Hub jetzt sauber, AC/DC-Versorgung komplett Bestnote).

---

## 🧹 Neu in 0.16.0 — nichts überdeckt mehr etwas; Labels mit 5-mm-Leitung

- Der Sauberkeits-Check deckt jetzt **alle** Kombinationen ab: Bauteile,
  Beschriftungen und Leitungen dürfen sich **gegenseitig nicht überdecken** —
  inkl. Beschriftung-auf-Beschriftung und Beschriftung-auf-Leitung, die vorher
  durchrutschten.
- Die Prüfung „Label auf Bauteil" schaut jetzt auf den ganzen **Text** (nicht nur
  den Anker) — damit ist der Fall „Label liegt auf einem Kondensator"
  (Motortreiber) erkannt und behoben.
- **Beschriftungen bekommen jetzt 5 mm Leitung**, genau wie jedes Bauteil — sie
  hängen nicht mehr direkt am Pin, sondern stehen frei daneben.

---

## ✅ Neu in 0.15.3 — Leitungen übereinander jetzt komplett weg

- Der Generator führt jetzt beim Zeichnen zwei Leitungen, die auf derselben
  Linie übereinander liegen, automatisch zu einer zusammen. Ergebnis: bei ALLEN
  Schaltungen liegt keine Leitung mehr über einer anderen.
- Dadurch sind jetzt **8 von 9 Demo-Schaltungen komplett sauber** (Bestnote) —
  auch die vorher hartnäckigen (USB-Hub, Buck-Wandler). Nur die Ethernet-
  Schaltung bleibt der bekannte Sonderfall (überdimensioniertes MCU-Symbol).
  Sich kreuzende Leitungen sind dabei erlaubt.

---

## 〰️ Neu in 0.15.2 — keine Leitungen übereinander (Kreuzungen sind ok)

- Neue Prüfung: zwei Leitungen dürfen nicht **übereinander** liegen (auf
  derselben Linie). Der Aufräum-Lauf zieht das jetzt auseinander.
- **Kreuzungen** (zwei Leitungen überkreuzen sich in einem Punkt) sind dagegen
  **ausdrücklich erlaubt** — die zählen nicht mehr als Fehler. So blockiert eine
  normale, unvermeidbare Kreuzung nicht mehr die Bestnote.

---

## 🏷️ Neu in 0.15.1 — IC-Beschriftung liegt nicht mehr auf den Anschlüssen

- Bei ICs (Chips mit vielen Anschlüssen) standen Name und Wert („U1 / 74HC595")
  bisher rechts auf den Anschluss-Namen — alles überlagert, unlesbar.
- Jetzt stehen Name über und Wert unter dem Chip. Die Anschluss-Namen (SER,
  SRCLK, QA…) sind wieder frei lesbar. Kleine Bauteile (Widerstände, Cs) bleiben
  wie gehabt.

---

## 🔌 Neu in 0.15.0 — Versorgung als saubere Symbole (näher an den Originalen)

- Wir richten den Generator jetzt an echten Profi-Schaltbildern aus. Erster
  Schritt: **Versorgungs-Leitungen (5 V, 3,3 V …) werden als kompakte Power-
  Symbole gezeichnet** statt als wiederholte Textbeschriftung.
- Wirkung z. B. bei der LED-Ring-Schaltung: die vielen „P5V"-Texte verschwinden,
  die Versorgung sieht aus wie von Hand gezeichnet. Die Verbindung bleibt exakt
  gleich — nur die Darstellung wird sauber.
- Das ist der Auftakt zu mehreren Schritten, die die erzeugten Schaltpläne
  strukturell an die Originale annähern.

---

## 🚧 Neu in 0.14.3 — keine Leitung läuft durch ein Bauteil

- Neue Prüfung: eine Leitung darf nie quer durch ein anderes Bauteil verlaufen.
- Gute Nachricht beim Nachmessen: das passiert schon jetzt nirgends — der
  Router legt die Leitungen bereits um die Bauteile herum. Die Prüfung ist
  daher ein **Wächter**: sie sorgt dafür, dass der Aufräum-Lauf (der Bauteile
  verschiebt) das auch nie kaputt macht, und schlägt bei künftigen Regressionen
  Alarm.

---

## 🔤 Neu in 0.14.2 — auch die Beschriftungen liegen nicht mehr übereinander

- **Am gerenderten Bild aufgefallen:** bei eng gepackten Widerständen/Kondensatoren
  überlappte die **Beschriftung** (z. B. „R1 / 1k") zweier Bauteile, obwohl die
  Bauteile selbst nicht überlappten. Die Aufräum-Messung hat das bisher nicht
  gesehen.
- Jetzt misst sie es — geeicht an den Profi-Vorlagen (die 0 haben). Der
  Aufräum-Lauf zieht die Bauteile so weit auseinander, dass sich auch die
  Beschriftungen nicht mehr in die Quere kommen. **8 von 9 Demo-Schaltungen sind
  damit vollständig sauber** (die Ethernet-Schaltung bleibt der bekannte
  Sonderfall mit dem überdimensionierten Mikrocontroller-Symbol).

---

## 🔎 Neu in 0.14.1 — Aufräum-Messung war zu blind, jetzt ehrlich

- **Wichtige Korrektur:** die Qualitäts-Messung hat Bauteile fälschlich als
  winzige Kästchen betrachtet und dadurch echte Überlappungen übersehen. Ein
  Programmierfehler (eine falsche „+=“-Zeile) ließ sie bei fast jedem Bauteil
  auf eine Notgröße zurückfallen. Behoben — die Messung sieht jetzt die echte
  Bauteil-Größe.
- **Ehrliches Ergebnis:** 8 von 9 Demo-Schaltungen werden sauber wie ein
  Profi-Plan (Bestnote). Die Ethernet-Schaltung nicht — sie benutzt für 11
  benötigte Anschlüsse ein riesiges 176-Pin-Mikrocontroller-Symbol (22 cm hoch);
  darin lassen sich die Beschriftungen nicht sinnvoll unterbringen. Das ist eine
  Frage der Symbol-Wahl, kein Layout-Fehler — bleibt als Aufgabe offen.
- **Beschriftungen zeigen jetzt zuverlässiger vom Bauteil weg** (sie suchen sich
  aktiv freien Raum), und der Aufräum-Lauf hat ein Zeitlimit, damit die
  Erzeugung nie minutenlang hängt.

---

## ✨ Neu in 0.14.0 — Schaltpläne werden automatisch aufgeräumt (Selbst-Optimierung)

- **Der Generator platziert nicht nur, er OPTIMIERT jetzt.** Nach dem Platzieren
  läuft eine echte Such-Schleife: sie verschiebt und dreht Bauteile in kleinen
  Schritten, zeichnet den fertigen Schaltplan jedes Mal neu und misst ihn — und
  behält einen Schritt nur, wenn der Plan dadurch **lesbarer** wird.
- **Gemessen wird gegen echte Profi-Schaltbilder** (die offiziellen KiCad-Demos).
  Ergebnis: alle 10 Demo-Schaltungen erreichen jetzt **die gleiche Bestnote wie
  ein von Hand gezeichneter Profi-Plan** — nichts liegt mehr übereinander (auch
  keine Beschriftung), alle Netz-Labels zeigen vom Bauteil weg in freien Raum,
  keine Leitung kreuzt eine andere, alles rechtwinklig auf dem Raster.
- **Ganz nebenbei 35× schneller:** ein Fehler im Symbol-Zwischenspeicher ließ
  jeden Schaltplan ~18 Sekunden brauchen; jetzt sind es ~0,5 Sekunden. Das gilt
  für JEDE Schaltplan-Erzeugung, nicht nur die neue Optimierung.

---

## 📐 Neu in 0.13.0 — Layout-Regeln aus echten Schaltbildern abgeleitet

- **Die 10 Schaltplan-Regeln sind jetzt nicht mehr erfunden, sondern aus echten,
  professionell gezeichneten KiCad-Referenz-Schaltbildern abgeleitet** (die
  offiziellen Demos „sallen_key" und „rectifier"): angesehen, Konventionen
  notiert, als Regeln formuliert — jede mit Beleg, woher sie stammt.
- Neu aufgenommen (aus der Referenz sichtbar, bei uns noch offen): Signal fließt
  als Kette entlang einer oberen Versorgungs- und unteren Masse-Schiene,
  Reihen-Bauteile liegen horizontal / Quer-nach-Masse-Bauteile vertikal,
  Versorgung als eigener Block. Die alten, erfundenen Regeln sind raus.
- Das Verhalten selbst ist unverändert (kein Überlappen, ≥5 mm Draht, Raster) —
  die neuen Struktur-Regeln sind als „geplant" markiert; sie umzusetzen ist der
  nächste große Hebel Richtung „sieht aus wie von Hand gezeichnet".

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
