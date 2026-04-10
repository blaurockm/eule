# Eule — Roadmap

## Phase 1: Portfolio-Monitor + EP-Pipeline (abgeschlossen)

- [x] Cross-Broker Positions-Aggregation (IBKR, TR, Tradier, Willbe, IG)
- [x] Options-Dashboard (50%-Regel, DTE-Warnungen, Roll-Vorbereitung)
- [x] Bond-Tracking (Kupons, Fälligkeiten, Ratings)
- [x] Allokations-Check (Soll vs. Ist, Kategorien, Einzelposition-Limits)
- [x] Thesis-Check (Exit-Kriterien aus positions-bh.md gegen Live-Daten)
- [x] Gesamt-Briefing (Positionen + Alerts + Allokation)
- [x] Hase-Trade-Import + Roundtrip-Erkennung
- [x] Elster Performance-Reports (Sharpe, Drawdown, Win-Rate, Regime-Vergleich)
- [x] Elster Baseline-Vergleich + Portfolio-Korrelation
- [x] Precheck (Hase-Health, API-Erreichbarkeit, FSM-State)
- [x] EP-Pipeline komplett (Scan → Score → Watchlist → Fill → Close → Brief)
- [x] EP-Daten in PostgreSQL (ep_pipeline + trades)
- [x] Wachtel Telegram-Bot
- [x] Config-Management (init, show, check)
- [x] `--format json` auf allen Befehlen

## Phase 2: Trade Journal (offen)

- [ ] Multi-Broker Trade-Import (CSV, API)
- [ ] Setup-Tagging (EP, RS-Breakout, Earnings, CSP, CC, etc.)
- [ ] Fehler-Tagging (early_exit, no_stop, fomo, oversized, etc.)
- [ ] R-Multiple Tracking
- [ ] Journal-Reports (Equity Curve, Monats-/Wochenstats)
- [ ] Konsistenz-Check Journal vs. Broker-Daten

## Ideen (ungeplant)

- Web-UI (Charts, Strategie-Analyse)
- Hamster-Pipeline-Check (Data Lake Freshness, Timer-Status)
- Portfolio-Rebalancing-Vorschläge
- Watchlist-Management (strategieübergreifend)
