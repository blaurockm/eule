# TODO — Eule

## Bugs (Phase 0)

- [x] **Roundtrip-Erkennung unvollständig** — Synthetische Sells (price=0, trade_ref=None) aus Hase-Rollovers gefiltert. FIFO-Matching funktioniert jetzt korrekt.
- [x] **Multiplier nicht berücksichtigt** — `fix_option_multiplier()` erkennt und korrigiert fehlenden ×100 Multiplier bei OPT/OptionContract Trades.
- [x] **HASE_BASE hardcoded** — Konfigurierbar via `EULE_HASE_DIR` env-var, Fallback `~/fin/hase`.

## Phase 1 — Positions + Monitor

- [x] Config-System (YAML) — `~/.eule/config.yaml` + `.env`-Dateien pro Broker
- [x] IBKR Positions-Abfrage (direkt via ibind, Client Portal API)
- [x] Tradier Positions-Abfrage (REST API via httpx)
- [x] IG Positions-Abfrage (trading_ig REST API)
- [x] Manuelle Positionen (Trade Republic, Willbe Gold) — separate YAML-Dateien
- [x] Positions-Aggregator + `eule positions` (FX-Konvertierung, Live-Kurse)
- [x] Options-Tracker + `eule options` (50%-Regel, DTE-Warnungen)
- [x] Bond-Tracker (Kupon-Berechnung, Fälligkeits-Warnungen)
- [x] Allocation-Checker + `eule allocation` (Soll/Ist, Konzentrations-Warnung)
- [x] Monitor/Briefing + `eule briefing` (Gesamt-Summary)
- [x] Thesis-Checker + `eule thesis` (positions-bh.md parsen, Exit-Kriterien)

## Offen

- [ ] **ibkr-two (LYNX)** — ibind kann nur eine Connection pro Prozess. Braucht separaten Aufruf oder Subprocess-Isolation.
- [ ] **EXSA.DE Entry-Preis** — €280.25 passt nicht zum aktuellen Kurs ~€58. Aktiensplit oder falsche Share Class pruefen.
- [ ] **Bond Live-Kurse** — Aktuell manuell in YAML. Perspektivisch: Boerse Frankfurt API oder aehnlich.

## Phase 2 — Trade Journal

- [ ] Multi-Broker Trade-Import (IBKR Flex Queries, Tradier, IG, Crypto)
- [ ] Setup-Tagging (EP, RS-Breakout, Earnings Play, CSP, CC, B&H)
- [ ] Fehler-Tagging (FOMO, kein Stop, Oversize)
- [ ] R-Multiple Tracking
- [ ] Cross-Broker P&L mit FX-Konvertierung (→ EUR)
- [ ] Performance-Reports (Weekly/Monthly, Setup-Vergleich)
- [ ] Konsistenz-Check: Journal vs. Broker-Positionen

## Referenzen

- Spec: `eule-spec-prompt.md` (in diesem Repo)
- Backlog: `~/fin/trading-collab/skills-backlog.md` (Abschnitt 5)
- Positionen: `~/fin/trading-collab/positions-bh.md`
