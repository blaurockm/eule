# eule.accounting — GbR-Buchhaltung

Doppelte Buchführung für den Joint-Account `real2-ibkr` (IBKR-Konto, Holder A
und B als GbR). Erzeugt aus User-gepflegten YAMLs + IBKR-Statement-of-Funds:

- **berechnete Sicht** für die mobile Vercel-App (Saldo pro Holder, letzte
  Trades — Token-basierte URL pro Holder)
- **Doppik-Reports** (Journal, Hauptbuch, Steuer-Report) für den Steuerberater

Zentrale Eigenschaft: deterministische Erzeugung aus reproduzierbaren Quellen.
Kein LLM, kein State außerhalb der YAMLs und der CSVs.

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
IBKR-Flex-Query                    User-managed (Obsidian-Vault)
  Statement of Funds                  ~/Dokumente/obsidian/tradingGbr/
    (Activity Flex Query,                config.yaml
     Section "Statement of Funds",        cash.yaml
     LevelOfDetail=BaseCurrency)          tokens.yaml
        │
        │ eule accounting import-sof
        ▼
  manual_trades.yaml ◄─────────────────┐
                                       │
                          eule accounting refresh
                                       │
                                       ▼
                            web/balances.json (für Vercel)
                            ledger/journal/tax CSV (für Steuerberater)
```

User-Workflow: `eule accounting refresh && git push` → Vercel deployt
automatisch.

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

| Datei | Inhalt | Pflege |
|---|---|---|
| `config.yaml` | Holders, Operator, Premium-pct, Pfad zur balances.json | manuell, einmalig |
| `cash.yaml` | Einlagen, Entnahmen, Transfers, externe Kosten + IBKR-Cash-Adjustments | manuell + via `import-sof` |
| `manual_trades.yaml` | Trade-PnL pro (Symbol, AssetClass) | via `import-sof` |
| `tokens.yaml` | Token pro Holder für Vercel-App-URL | manuell |

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
eule accounting refresh                 # Lädt alles, schreibt web/balances.json
eule accounting balances [--format json]
eule accounting journal [--year YYYY] [--format csv]
eule accounting ledger  [--year YYYY] [--format csv]
eule accounting tax     [--year YYYY] [--format csv]
eule accounting import-sof <files...>   # SoF → manual_trades.yaml + cash.yaml-Block
```

### import-sof — SoF als Single Source of Truth

Statement of Funds (Activity Flex Query, Section *Statement of Funds*,
LevelOfDetail=BaseCurrency) liefert für jede Cash-Bewegung den fertig in EUR
konvertierten Wert. Damit ist es die definitive Wahrheit für das EUR-Cash-
Konto — bevorzugt gegenüber Trade-Confirmations, weil:

- Keine FX-Drift zwischen Trade-Date-Konvertierung und tatsächlichem
  Saldo (Open- vs. Close-Leg-Kurse).
- Alle Cash-Posten (Trades, Other Fees, Adjustments) in einem Format.
- Storno-Buchungen werden korrekt mit Vorzeichen abgebildet.

Klassifikation:

| Bedingung | Bedeutung | Behandlung |
|---|---|---|
| `AssetClass != ''` | Trade (FUT/OPT/FOP/CASH) | aggregiert pro `(Description, AssetClass)` → manual_trades.yaml |
| `AssetClass == '' && \|amount\| ≥ 100` | Cash Receipt / Disbursement | **skipped** — bereits in cash.yaml als `transfers` |
| `AssetClass == '' && \|amount\| < 100` | Fee / Adjustment | aggregiert pro Datum (mit Vorzeichen) → cash.yaml expenses-Block |

Trades werden über alle Tagesposten hinweg aggregiert (`Datum = Close-Date`),
damit die 60:40-Verteilung pro abgeschlossenem Roundtrip greift und nicht pro
Mark-to-Market-Tag.

Limit pro Flex-Query: 365 Tage. Mehrere CSVs einfach zusammen übergeben:

```sh
eule accounting import-sof \
    ~/Downloads/sof-2024.csv \
    ~/Downloads/sof-2025.csv \
    --out-trades ~/Dokumente/obsidian/tradingGbr/manual_trades.yaml
```

Dedupliziert über `(date, amount, asset_class, description)`.

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
├── cash.py                # Loader cash.yaml — CashLedger
├── chart.py               # Kontenrahmen
├── cli.py                 # typer accounting_app
├── config.py              # Loader config.yaml — AccountingConfig
├── examples/              # Beispiel-YAMLs
├── export.py              # JSON + CSV-Writer
├── import_sof.py          # IBKR-Statement-of-Funds-Importer
├── journal.py             # Buchungs-Generator (Roundtrips + Cash → Postings)
├── ledger.py              # Hauptbuch / Konto-Salden aus Postings
├── manual_trades.py       # Loader manual_trades.yaml
├── models.py              # Posting, HolderBalance, AccountBalance
└── tax.py                 # Steuer-Report (Anlage KAP)
```

Tests in `tests/test_accounting.py`. Keine Side-Effects beim Importieren
(außer Konfiguration laden bei CLI-Calls).
