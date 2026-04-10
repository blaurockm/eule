# CLAUDE.md

## Projekt

Eule 🦉 — Zentrales Portfolio-Monitoring, Trade-Management und Performance-Analyse CLI.
Teil des Trading-Ökosystems (Hase, Hamster, Dachs, Igel, Fuchs, Elster).

### Scope

- **Positions-Aggregation** über alle Broker (IBKR, TR, Tradier, Willbe)
- **Options-Tracking** (50%-Regel, DTE-Warnungen, Roll-Vorbereitung)
- **Allocation** (Soll vs. Ist, Kategorien: Kern, Opportunistisch, Gold, Anleihen)
- **Thesis-Check** (Exit-Kriterien aus positions-bh.md gegen Live-Daten)
- **EP-Trading** (Episodic Pivot Scanner, Trade-Tracking, Morning Briefs)
- **Hase-Performance** (Elster: Sharpe, Drawdown, Win-Rate, Regime-Vergleich)
- **Hase-Runtime-Monitoring** (Precheck, Baselines, Anomalie-Alerts)
- **Hamster-Pipeline-Check** (Data Lake Freshness, Timer-Status)
- **Telegram Bot** (Wachtel: interaktiver Monitor mit Claude-Agent)

## Spezifikation

Lies `eule-spec-prompt.md` in diesem Repo — das ist die vollständige Anforderung.

## Referenz-Dateien (auf Server "systematic")

| Was | Pfad |
|-----|------|
| Hase-Verzeichnis (Logs, Fuchs-Config) | `EULE_HASE_DIR` oder `~/fin/hase` |
| Positionen + Thesen + Exit-Kriterien | `~/fin/trading-collab/positions-bh.md` |
| B&H Playbook | `~/fin/trading-collab/buy-and-hold-playbook.md` |
| EP Daily Playbook | `~/fin/trading-collab/ep-daily-playbook.md` |
| EP-Trades (JSON) | `~/trading-collab/ep-trades.json` |
| Backlog + offene Fragen | `~/fin/trading-collab/skills-backlog.md` (Abschnitt 5) |

## Umgebungsvariablen

DB-Zugang über Umgebungsvariablen (in `~/eule/.env` auf systematic):

| Variable | Environment | Beispiel |
|----------|-------------|----------|
| `EULE_DB_REAL_IBKR` | Production IBKR | `postgresql://user:pass@host/db` |
| `EULE_DB_REAL2_IBKR` | Production IBKR #2 | `postgresql://...` |
| `EULE_DB_STAGING_IBKR` | Staging IBKR | `postgresql://...` |
| `EULE_DB_STAGING_HL` | Staging HL | `postgresql://...` |
| `EULE_HASE_DIR` | Hase-Verzeichnis (Logs, Fuchs) | `~/fin/hase` (default) |

## Regeln

- Python 3.12+, Poetry, Typer CLI
- **Jeder CLI-Befehl MUSS `--format json` unterstützen** (für Wachtel-Integration via SSH)
- Kein LLM in Eule — nur deterministische Datenverarbeitung
- Tests schreiben (pytest)
- Nicht raten. Originale lesen.

## Implementierungsstatus

### Fertig (Phase 1 + EP)

- `eule trades` — Hase PostgreSQL-Import + Roundtrip-Erkennung
- `eule envs` — Hase-Environments auflisten
- `eule positions` — Cross-Broker Positions-Aggregation (IBKR, TR, Tradier, Willbe)
- `eule options` — Options-Dashboard (50%-Regel, DTE, Rolls)
- `eule allocation` — Soll vs. Ist Allokation
- `eule briefing` — Gesamt-Briefing
- `eule thesis` — Exit-Kriterien prüfen
- `eule config` — Config init/show/check
- `eule report` — Performance-Report (Sharpe, Drawdown, Win-Rate)
- `eule compare` — Live vs. Baseline-Vergleich
- `eule portfolio` — Portfolio-Korrelationsanalyse
- `eule precheck` — Health-Check (Hase-APIs gegen Baselines)
- `eule bot` — Wachtel Telegram Bot
- `eule ep` — EP Scanner, Trades, Morning Brief + Email

### Offen (Phase 2: Trade Journal)

- Multi-Broker Trade-Import (CSV, API)
- Setup-Tagging (EP, RS-Breakout, Earnings, etc.)
- Fehler-Tagging (early_exit, no_stop, fomo, etc.)
- R-Multiple Tracking
- Journal-Reports, Equity Curve, Stats
- Konsistenz-Check Journal vs. Broker
