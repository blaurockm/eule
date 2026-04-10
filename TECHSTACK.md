# Eule — Tech Stack

## Runtime

| Komponente | Technologie |
|-----------|-------------|
| Sprache | Python 3.12+ |
| CLI-Framework | Typer |
| Terminal-Output | Rich |
| Logging | Loguru |
| Package-Management | Poetry |

## Daten

| Komponente | Technologie |
|-----------|-------------|
| Datenbank | PostgreSQL (via psycopg 3) |
| Config | YAML (`~/.eule/config.yaml`) |
| Credentials | `.env`-Dateien (python-dotenv) |
| FX/Kurse | yfinance, Broker-APIs |

## Broker-Anbindungen

| Broker | Typ | Library |
|--------|-----|---------|
| IBKR | API (OAuth) | ibind |
| Tradier | REST API | httpx |
| IG | REST API | trading-ig |
| Trade Republic | Manuell | YAML-Positions-Datei |
| Willbe | Manuell | YAML-Positions-Datei |

## Analyse

| Komponente | Technologie |
|-----------|-------------|
| DataFrames | pandas |
| Numerik | numpy |
| Charts | matplotlib (TkAgg) |

## Infrastruktur

| Komponente | Detail |
|-----------|--------|
| Deployment | `systematic` VServer, `git pull` |
| Tests | pytest |
| HTTP-Client | httpx, requests |
| Email (EP Brief) | SMTP via email-Modul |
| Email (EP Scan) | IMAP (Barchart-Screener) |
| Bot | Telegram Bot API (Wachtel) |

## Modulstruktur

```
eule/
├── cli.py              # Dispatcher (~40 Zeilen, registriert Bereichs-CLIs)
├── output.py           # Shared Output (JSON, Console)
├── models.py           # Datenklassen (HaseTrade, Roundtrip, Position, ...)
├── config.py           # Config-Loader (~/.eule/)
├── db.py               # PostgreSQL-Verbindung, Hase-Environments
├── quotes.py           # Live-Kurse
├── fx.py               # Währungsumrechnung
│
├── bestand/            # Was habe ich?
│   ├── cli.py          # positions, options, allocation, briefing, thesis
│   ├── aggregator.py   # Cross-Broker Positions-Aggregation
│   ├── options.py      # Options-Dashboard
│   ├── bonds.py        # Anleihen-Tracking
│   ├── allocation.py   # Allokations-Check
│   ├── thesis.py       # Thesis-Parser + Prüfung
│   └── briefing.py     # Gesamt-Briefing
│
├── bewertung/          # Wie läuft es?
│   ├── cli.py          # trades, envs, report, compare, portfolio
│   └── trades.py       # Trade-Import, Roundtrip-Erkennung
│
├── pipeline/           # Was kommt als nächstes?
│   ├── cli.py          # EP scan, add, fill, close, trades, brief, ...
│   └── email.py        # Email-Versand (Morning Brief)
│
├── betrieb/            # Läuft alles?
│   └── cli.py          # precheck, config (init/show/check), bot
│
├── brokers/            # Broker-Adapter (ibkr, tradier, ig, manual)
├── ep/                 # EP-Logik (scanner, db, trades)
├── elster/             # Performance-Logik (data, metrics, report, regimes)
└── monitoring/         # Precheck-Logik + Telegram-Bot
```
