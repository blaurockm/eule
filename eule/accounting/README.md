# eule.accounting — GbR-Buchhaltung

Doppelte Buchführung für den Joint-Account `real2-ibkr` (IBKR-Konto, Holder A
und B als GbR). Erzeugt aus dem IBKR-Statement-of-Funds + manuell gepflegten
Cash-Bewegungen:

- **berechnete Sicht** für die mobile Vercel-App (Saldo pro Holder, letzte
  Trades — Token-basierte URL pro Holder)
- **Doppik-Reports** (Journal, Hauptbuch, Steuer-Report) für den Steuerberater

Zentrale Eigenschaft: deterministische Erzeugung aus reproduzierbaren Quellen.
Kein LLM, keine persistierten Aggregate — nur Cache-CSVs + User-YAMLs.

## Verteilungsregeln (symmetrisch)

Trade-PnL wird mit asymmetrischem Schlüssel verteilt — **in beide Richtungen**:

| | Operator A | Other B |
|---|---|---|
| Gewinn 100 EUR | +60 EUR | +40 EUR |
| Verlust −100 EUR | −60 EUR | −40 EUR |

Steuerlich: gemeinsamer Verteilungsschlüssel der Mitunternehmerschaft, alles
**Kapitaleinkünfte (§20 EStG, Anlage KAP)**. Kein Honorar / keine Anlage S.

Externe Aufwendungen werden nach reinem `capital_share` aufgeteilt (50:50 bei
50:50-Kapital). Anteilig nicht als Werbungskosten abziehbar (§20 Abs. 9 EStG).

> **Steuer-Hinweis:** der asymmetrische Schlüssel (60:40) muss im
> Gesellschaftsvertrag dokumentiert sein, sonst riskierst du, dass das
> Finanzamt das auf 50:50 zurückrechnet. Vor Live-Gang mit Steuerberater
> abstimmen.

## Datenfluss

```
IBKR Flex Web Service                  User-managed (Obsidian-Vault)
  Activity Flex Query                    ~/Dokumente/obsidian/tradingGbr/
  (Statement of Funds,                       config.yaml
   LevelOfDetail=BaseCurrency)               cash.yaml   ← nur was NICHT aus IBKR kommt
        │                                    tokens.yaml
        │ eule accounting fetch              sof/        ← Cache (CSV-Archiv)
        ▼                                       sof-current.csv      (rolling, ueberschrieben)
  ~/Dokumente/obsidian/tradingGbr/sof/         sof-2024.csv         (Archive, einmal gezogen)
        │                                       sof-2025.csv         (Archive, einmal gezogen)
        │
        │ eule accounting refresh
        ▼
  web/balances.json (für Vercel) + ledger/journal/tax CSV
```

User-Workflow: `eule accounting fetch && eule accounting refresh && git push`
→ Vercel deployt automatisch.

## Kontenrahmen

| Konto | Typ | Bezeichnung |
|-------|-----|-------------|
| 0100 | Eigenkapital | Kapital A |
| 0110 | Eigenkapital | Kapital B |
| 1100 | Aktiv | Giro-Referenzkonto (Durchlauf) |
| 1200 | Aktiv | Verrechnung Broker (real2-ibkr) |
| 4000 | Erlös | Trading-Gewinne (netto, realisiert) |
| 5000 | Aufwand | Trading-Verluste (netto, realisiert) |
| 6000 | Aufwand | Externe Aufwendungen |
| 9000 | Privat | Privatentnahme A |
| 9010 | Privat | Privatentnahme B |

## Buchungslogik

Gewinn-Roundtrip mit `pnl > 0`:
```
1200 an 4000      pnl                             Verrechnung gewinnt
4000 an 0(op)00   capital_share*pnl + premium*pnl Anteil Operator (=60)
4000 an 0(ot)00   capital_share*pnl - premium*pnl Anteil Other (=40)
```

Verlust-Roundtrip mit `pnl ≤ 0` (premium-Anteil zulasten Operator):
```
5000 an 1200      |pnl|
0(op)00 an 5000   |capital_share*pnl + premium*pnl|   Verlustanteil Operator (=60)
0(ot)00 an 5000   |capital_share*pnl - premium*pnl|   Verlustanteil Other (=40)
```

Externe Kosten K mit `paid_from`:
```
6000 an 1100|1200   K                    je nach Quelle (Giro oder Broker direkt)
0H00 an 6000        K * capital_share    pro Holder
```

Negativer `amount_eur` in einem Expense ist ein Storno (Reversal einer
früheren Buchung) — Soll/Haben werden umgekehrt.

Einlage / Entnahme / Transfer: siehe Docstring in `journal.py`.

## Datenquellen

Alle in `~/Dokumente/obsidian/tradingGbr/` (Override via
`EULE_TRADINGGBR_DIR`). Beispiele unter `eule/accounting/examples/*.yaml`.

| Pfad | Inhalt | Pflege |
|---|---|---|
| `config.yaml` | Holders, Operator, Premium-pct, Pfad zur balances.json | manuell, einmalig |
| `cash.yaml` | deposits, withdrawals, transfers, externe Aufwendungen (Giro) | manuell |
| `tokens.yaml` | Token pro Holder für Vercel-App-URL | manuell |
| `sof/*.csv` | IBKR-Statement-of-Funds-Cache (Trades + IBKR-Cash-Adjustments) | via `fetch` |

### config.yaml — Premium-Schlüssel

```yaml
performance_fee:
  pct: 0.10                       # 10% → 60:40-Verteilung
  recipient: A                    # Operator (kein Effekt mehr in symmetrischer Logik)
  base: per_winning_roundtrip     # Legacy-Feld (unter symmetrischer Logik irrelevant)
```

`pct` wirkt symmetrisch: Operator bekommt `(0.5 + 0.10)*pnl`, Other bekommt
`(0.5 - 0.10)*pnl`. Bei Verlust kehrt sich das natürlich automatisch um.

## CLI-Befehle

```
eule accounting fetch                    # IBKR Flex API -> sof/sof-current.csv
eule accounting refresh                  # liest alles, schreibt web/balances.json
eule accounting balances [--format json]
eule accounting journal [--year YYYY] [--format csv]
eule accounting ledger  [--year YYYY] [--format csv]
eule accounting tax     [--year YYYY] [--format csv]
```

### fetch — IBKR Flex Web Service

`fetch` ruft die konfigurierte Activity Flex Query (Section *Statement of
Funds*, LevelOfDetail=BaseCurrency, Format=CSV) ab und schreibt das Resultat
als `sof/sof-current.csv`. Voraussetzung: `EULE_IBKR_FLEX_TOKEN` und
`EULE_IBKR_FLEX_QUERY_ID` in `.env`.

Token + Query-ID anlegen im IBKR Account Management:
- *Reporting → Settings → Flex Web Service* → Token generieren (1 Jahr gültig)
- *Reporting → Flex Queries* → Activity Flex Query mit Section *Statement of
  Funds*, LevelOfDetail=BaseCurrency, Format=CSV anlegen

Die Flex-Query ist auf 365 Tage limitiert. Für Historie länger als 1 Jahr:
einmalige Custom-Date-Range-Queries pro Jahr ziehen und als
`sof/sof-{jahr}.csv` ins Cache-Verzeichnis legen.

### refresh — alles berechnen

`refresh` liest:
1. `sof/*.csv` → Trade-Aggregate + IBKR-Cash-Adjustments
2. `cash.yaml` → manuelle deposits/withdrawals/transfers/externe Aufwendungen
3. `config.yaml` → Verteilungsregeln

→ Schreibt `web/balances.json` für die Vercel-App.

Beim Mehrfach-Lesen mehrerer SoF-CSVs gewinnt **pro Datum** das File mit den
meisten Posten an dem Tag (typischerweise das umfassendere Statement). Damit
ist es egal, wenn `sof-current.csv` zeitlich mit `sof-2025.csv` überlappt.

## Vercel-App (`web/`)

Vanilla HTML+JS, kein Build. Vercel-Project auf das Repo zeigen, Root
Directory = `web/`. Publish-Workflow: `eule accounting refresh && git push`.

Token-basierte URLs:
```
https://eule-info.vercel.app/?t=<token-aus-tokens.yaml>
```

`balances.json` enthält pro Token:
- `balance_broker` / `balance_giro` (proportional aufgeteilt)
- `recent_trades` (letzte 3 Roundtrips global)

## Architektur

```
eule/accounting/
├── __init__.py
├── README.md              # diese Datei
├── allocator.py           # Symmetrische 60:40-Verteilung
├── balances.py            # Berechnete Sicht (Saldo pro Holder)
├── cash.py                # Loader cash.yaml — CashLedger + Filter-Helper
├── chart.py               # Kontenrahmen
├── cli.py                 # typer accounting_app
├── config.py              # Loader config.yaml — AccountingConfig
├── examples/              # Beispiel-YAMLs
├── export.py              # JSON + CSV-Writer
├── fetch.py               # IBKR Flex Web Service Client
├── import_sof.py          # SoF-CSV-Parser + Aggregator
├── journal.py             # Buchungs-Generator (Roundtrips + Cash → Postings)
├── ledger.py              # Hauptbuch / Konto-Salden aus Postings
├── models.py              # Posting, HolderBalance, AccountBalance
├── state.py               # SoF + cash.yaml -> (Roundtrips, CashLedger)
└── tax.py                 # Steuer-Report (Anlage KAP)
```

Tests in `tests/test_accounting*.py`. Keine Side-Effects beim Importieren
(außer Konfiguration laden bei CLI-Calls).
