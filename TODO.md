# TODO — Eule

## Bugs (Phase 0)

- [ ] **Roundtrip-Erkennung unvollständig** — Nur 3 von 4 Roundtrips erkannt für real-ibkr. Trades vom 23.03 und 30.03 (spx-0dte-mon-put) landen als "open" statt als geschlossene Roundtrips. Die Matching-Logik greift nicht korrekt wenn nach einer Expiry (buy, price=0) am selben Tag ein neuer sell (neuer Zyklus-Start, price=0, kein trade_ref) kommt — das wird fälschlich als weiterer offener Trade gewertet.
- [ ] **Multiplier nicht berücksichtigt** — Optionen haben einen ×100 Multiplier. Der erste Trade (02.03) zeigt `entry_value: 2.45` statt `$245.00`. Ab dem 16.03 stimmt der Wert (`value: 235.00`), vermutlich weil Hase den Bug gefixt hat — Eule sollte trotzdem den Multiplier kennen und ggf. korrigieren.
- [ ] **HASE_BASE hardcoded** — `db.py` hat `Path.home() / "hase"` hardcoded. Sollte konfigurierbar sein (env var `EULE_HASE_DIR` oder config file), damit es auf Tower (`~/fin/hase`) und systematic (`~/hase`) funktioniert.

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
