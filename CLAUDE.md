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
- **GbR-Buchhaltung** (Joint-Account real2-ibkr: Doppik, asymmetrische Verteilung, Vercel-Share-App)

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
| `EULE_TRADINGGBR_DIR` | tradingGbr-Buchhaltungsdaten | `~/Dokumente/obsidian/tradingGbr` (default) |
| `EULE_IBKR_FLEX_TOKEN` | IBKR Flex Web Service Token (1 Jahr gueltig) | aus IBKR Account Management |
| `EULE_IBKR_FLEX_QUERY_ID` | Flex-Query-ID fuer Statement of Funds | aus IBKR Flex Queries |

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
- `eule accounting` — GbR-Buchhaltung fuer Joint-Account real2-ibkr (fetch, refresh, balances, journal, ledger, tax)

### Eule Accounting (GbR-Buchhaltung)

Modul `eule.accounting` fuer den Joint-Account hinter `EULE_DB_REAL2_IBKR`.

**Verteilungsregeln** (symmetrisch, GbR-Mitunternehmerschaft):
- Trading-Gewinne und -Verluste: 60:40 zugunsten/zulasten Operator
- Externe Kosten: 50:50 nach capital_share
- Zinsen/Dividenden: nicht erfasst (User-Entscheidung)
- Steuerlich: alles Kapitaleinkuenfte (§20 EStG, Anlage KAP) — kein Honorar

**Datenquellen** (alle in `~/Dokumente/obsidian/tradingGbr/`, Override via `EULE_TRADINGGBR_DIR`):
- `config.yaml` — Holders, Operator, Verguetungsregel, Pfad zur balances.json
- `cash.yaml` — deposits, withdrawals, transfers, externe Aufwendungen (Giro) — NUR was nicht aus IBKR kommt
- `tokens.yaml` — Token pro Holder fuer Vercel-App-URL
- `sof/*.csv` — IBKR-Statement-of-Funds-Cache (Trades + IBKR-Cash-Adjustments)

**Single source of truth**: IBKR-Statement-of-Funds (Activity Flex Query, Section
"Statement of Funds", LevelOfDetail=BaseCurrency). Wird per `eule accounting fetch`
aus dem IBKR Flex Web Service gezogen und nach `sof/sof-current.csv` geschrieben.
Aeltere Jahre als statische Archive (`sof-2024.csv`, `sof-2025.csv`) daneben.

Beim Refresh werden alle `sof/*.csv` gelesen; pro Datum gewinnt das File mit den
meisten Posten (typischerweise das umfassendste Statement). Keine persistierten
Aggregate — die Pipeline rechnet jedes Mal frisch aus dem Cache.

Klassifikation (siehe `import_sof.py`):
- AssetClass != ''                               → Trade (FUT/OPT/FOP/CASH)
- AssetClass == '' und |amount| >= 100 EUR       → Transfer (skip — bereits in cash.yaml als transfers)
- AssetClass == '' und |amount| < 100 EUR        → Fee/Adjustment (mit Vorzeichen)

Trades werden pro (Description, AssetClass, Geschaeftsjahr) ueber alle Cashflows
aggregiert (eine Buchung pro Roundtrip, Datum = Close-Date). Per-Year-Split
verhindert, dass cross-year Symbole (z.B. EUR.USD) in einem Aggregat zusammenfallen.

`CashExpense.amount_eur` darf negativ sein (Storno einer fruheren Buchung):
das Journal kehrt dann Soll/Haben um.

Beispiel-Templates: `eule/accounting/examples/*.yaml`.

**Outputs**:
- `eule accounting fetch` — IBKR Flex API → `sof/sof-current.csv`
- `eule accounting refresh` — liest sof/*.csv + cash.yaml, schreibt `web/balances.json`
- `eule accounting balances --format json` — berechnete Sicht (Saldo pro Holder)
- `eule accounting journal --year YYYY --format csv` — Doppik-Journal fuer Steuerberater
- `eule accounting ledger --year YYYY --format csv` — Hauptbuch (Konten-Salden)
- `eule accounting tax --year YYYY --format csv` — Steuer-Report (Kapitaleinkuenfte)

**Workflow**: `eule accounting fetch && eule accounting refresh && git push`
→ Vercel deployt automatisch.

**Vercel-App** (`web/`): vanilla HTML+JS, kein Build. Vercel-Project auf das Repo zeigen, Root Directory = `web/`.

**Steuerlicher Hinweis**: 60:40 als symmetrischer Verteilungsschluessel der Trading-GbR ist sauber modellierbar als Mitunternehmerschaft (alles §20 Anlage KAP). Muss aber im Gesellschaftsvertrag dokumentiert sein, sonst kann das Finanzamt den Schluessel auf 50:50 zurueckrechnen. Vor Live-Gang mit Steuerberater abstimmen. Details in `eule/accounting/README.md`.

### Offen (Phase 2: Trade Journal)

- Multi-Broker Trade-Import (CSV, API)
- Setup-Tagging (EP, RS-Breakout, Earnings, etc.)
- Fehler-Tagging (early_exit, no_stop, fomo, etc.)
- R-Multiple Tracking
- Journal-Reports, Equity Curve, Stats
- Konsistenz-Check Journal vs. Broker
