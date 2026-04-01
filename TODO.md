# TODO — Eule

## Bugs (Phase 0)

- [x] **Roundtrip-Erkennung unvollständig** — Synthetische Sells (price=0, trade_ref=None) aus Hase-Rollovers gefiltert. FIFO-Matching funktioniert jetzt korrekt.
- [x] **Multiplier nicht berücksichtigt** — `fix_option_multiplier()` erkennt und korrigiert fehlenden ×100 Multiplier bei OPT/OptionContract Trades.
- [x] **HASE_BASE hardcoded** — Konfigurierbar via `EULE_HASE_DIR` env-var, Fallback `~/fin/hase`.

## Phase 1 — Positions + Monitor

- [ ] Config-System (TOML oder YAML) für Credentials, Pfade, manuelle Positionen
- [ ] IBKR Positions-Abfrage (via Hase API localhost:8767/debug/positions)
- [ ] Tradier Positions-Abfrage (REST API)
- [ ] Manuelle Positionen (Trade Republic, Willbe Gold)
- [ ] Positions-Aggregator + `eule positions`
- [ ] Options-Tracker + `eule options` (50%-Regel, Verfall, Roll)
- [ ] Bond-Tracker (Kupon, Fälligkeit, Rating)
- [ ] Allocation-Checker + `eule allocation`
- [ ] Monitor/Briefing + `eule briefing`
- [ ] Thesis-Checker + `eule thesis`

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
