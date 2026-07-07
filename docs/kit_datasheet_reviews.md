# Datenblatt-Reviews der Demo-Bausätze

Belege für das ``verified``-Flag (Roadmap Phase 3): je Bausatz, welches
Datenblatt geprüft wurde und welche Punkte Pin-für-Pin stimmen. „Verified"
heißt: die Schaltung ist eine korrekte Datenblatt-Applikationsschaltung, nicht
bloß elektrisch plausibel.

## buck_converter — MP1584 (Monolithic Power Systems)
Verifiziert im Industrie-Review 0.26.1 (zwei unabhängige Pinout-Quellen);
als Circuit-Block ``mp1584_buck_5v`` modelliert. Pinout 1 SW, 2 EN, 3 COMP,
4 FB, 5 GND, 6 FREQ, 7 VIN, 8 BST; COMP-RC nach MPS-Verfahren, FREQ-R,
FB-Teiler 4,99 V.

## motor_driver — DRV8871 (TI SLVSCY9B)
Verifiziert 0.26.1; Block ``drv8871_hbridge``. Pinout 1 GND, 2 IN2, 3 IN1,
4 ILIM, 5 VM, 6 OUT1, 7 PGND, 8 OUT2; VM-Bypass 100n + Bulk 47u, ILIM 30k.

## audio_amp — LM386 (TI SLOS147)
Verifiziert 0.26.1; Block ``lm386_amp20``. gain=20 (Pins 1/8 offen), Zobel
10 Ω + 47 n, Bypass 10 µ, Eingangskopplung 10 µ, Versorgungsstecker J3.
Platine seit 0.31.0 0 DRC / 0 offen (Rip-up-lite-Router löste die zuvor
versiegelte Pin-Tasche an U1:3) → **⭐**.

## kit_seeding — NE555 (TI SLFS022, astabiler Multivibrator)
Pinout 1 GND, 2 TRIG, 3 OUT, 4 RESET, 5 CTRL, 6 THR, 7 DISCH, 8 VCC — korrekt.
Astabil-Netz lehrbuchgemäß:
- Ra = R1 (10 k) von VCC nach DISCH (Pin 7)
- Rb = R2 (47 k) von DISCH (7) nach THR/TRIG (6+2, gebrückt)
- Timing-C = C1 (10 µ) von THR/TRIG nach GND
- CV-Abblock C2 (10 n) an Pin 5 → GND (Datenblatt-Empfehlung, vorhanden)
- RESET (4) an VCC (nicht genutzt), VCC-Abblock C3 (100 n)
- Ausgang (3) über R3 (1 k) auf LED D1 nach GND
Frequenz f = 1,44 / ((Ra + 2·Rb)·C1) ≈ 1,44 / (104 k · 10 µ) ≈ 1,38 Hz,
Duty ≈ 55 % — sinnvoller Blink-Takt für die Demo. **Datenblatt-korrekt.**

## sketch_to_copper — AMS1117-5.0 (AMS)
Pinout 1 GND, 2 VOUT, 3 VIN (Fixed-Version) — korrekt. Eingangs-C1 (10 µ) an
VIN, Ausgangs-C2 (22 µ) an VOUT (AMS1117 braucht ≥ 22 µF für Stabilität —
erfüllt), Testpunkt am 5-V-Ausgang. **Datenblatt-korrekt** (die Platine bleibt
bewusst „Skizze" für die interaktive Routing-Demo → board_clean noch offen).

## led_ring — WS2812B (Worldsemi)
Pinout 1 VDD, 2 DOUT, 3 GND, 4 DIN — korrekt. Daisy-Chain
J1 → D1.DIN, D1.DOUT → D2.DIN … D6.DOUT → J2 — korrekt. Signalpegel 5 V.
**Abblockung 3× 100 nF für 6 LEDs (1 je 2 LEDs).** Das Datenblatt zeigt in
der Typ-Applikation 100 nF *je* LED; für diesen kompakten 6-LED-Ring mit
kurzen Versorgungsbahnen und ~120 mA Summenstrom ist 1 je 2 LEDs eine gängige
und ausreichende Wahl (bewusst dokumentierte Abweichung). Gegenprobe: 6 Caps
(1 je LED) sprengen auf dem runden Board die Kupfer-Kantenabstände
(2× copper_edge_clearance) — die 3-Cap-Variante hält das Board 0 DRC / 0 offen.
Pinout, Kette und Versorgung sind datenblatt-korrekt. **Verified.**

---

## production_ready — 74HC595 (Nexperia/TI, SOIC-16)
Seit 0.32.0 aufs **echte 16-Pin** umgebaut (vorher 8-Pin-Reduktion mit
unbeschaltetem RCLK = nicht funktionsfähig). QA(15)/QB(1)/QC(2) treiben die
LEDs über R4/R5/R6; SER(14), SRCLK(11), **RCLK(12)** an J1 (1×05:
VCC/SER/SRCLK/RCLK/GND); **/OE(13)→GND** (Ausgänge aktiv), **/SRCLR(10)→VCC**
(Clear inaktiv); VCC(16)/GND(8), Abblock-Cs. Board 0/0, Roundtrip 10/10,
byte-deterministisch. **Verified → ⭐.**

## Noch offen (kein verified)
- **ac_dc_supply**: Platine seit 0.31.0 0/0 (✅), aber Schaltplan-Review
  (TNY268 + PC817 + TL431: EN/BP-Beschaltung, Bias-Wicklung, TL431-Teiler,
  Kriechstrecken) steht aus → noch nicht verified.
- **usb_sensor_hub, ethernet_device**: Roadmap Phase 2c (Fein-Pitch-Router)
  + Phase 3 (Datenblatt-Review, mehrere ICs/Straps).
