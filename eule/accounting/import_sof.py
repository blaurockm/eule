"""Parser + Aggregator fuer IBKR-Statement-of-Funds-CSVs.

Ein Statement of Funds (Flex-Query: Activity Flex Query → Section 'Statement of
Funds', LevelOfDetail=BaseCurrency) ist die definitive Wahrheit fuer das EUR-
Cash-Konto: jede Cash-Bewegung des Brokers steht dort mit ihrem fertig in EUR
konvertierten Wert.

Erwartete Spalten (Header der Trade-Section):
    AssetClass, Description, Conid, FXRateToBase, Amount, CurrencyPrimary,
    SettleDate, Date, ReportDate, Balance, TradePrice, TradeGross,
    TradeCommission, Expiry, TradeCode, LevelOfDetail

Klassifikation der Zeilen (siehe ``classify``):

* AssetClass != ''                     → 'trade'    (FUT/OPT/FOP/CASH)
* AssetClass == '' und |amount| >= TRANSFER_THRESHOLD → 'transfer'
  (Cash Receipts oder Disbursements — werden NICHT verbucht,
  weil sie aus cash.yaml als ``transfers`` kommen.)
* sonst                                → 'fee'      (kleine Cash-Adjustments,
                                                     i.d.R. Datafeed-Fees)
"""

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Cash-Receipts/Disbursements werden im Giro-Statement getrackt und sind
# bereits in cash.yaml als transfers gepflegt. Schwelle: 100 EUR ist gross
# genug fuer alle bekannten Transfers, klein genug um keine Fee-Posten
# faelschlich auszuschliessen.
TRANSFER_THRESHOLD = 100.0

_SOF_REQUIRED = {"AssetClass", "Amount", "Date", "Description", "LevelOfDetail"}


@dataclass(frozen=True)
class SofRow:
    posting_date: date
    amount_eur: float       # mit Vorzeichen (negativ = Cash geht weg)
    asset_class: str
    description: str


@dataclass(frozen=True)
class TradeAggregate:
    """Pro (Description, AssetClass) ein Roundtrip-Eintrag.

    Alle Tages-Cashflows eines Symbols werden zu einer Buchung zusammengefasst,
    Datum = letztes Posting-Datum (≈ Close-Date). pnl_eur traegt das Vorzeichen
    aus dem SoF (positiv = Gewinn). Damit greift die 10%-Verguetung pro
    abgeschlossenem Roundtrip, nicht pro Mark-to-Market-Tag.

    Trade-off: wird ein Symbol mehrfach gehandelt (Open-Close-Open-Close),
    fallen beide Roundtrips in einen Aggregat-Eintrag.
    """
    posting_date: date
    description: str
    asset_class: str
    pnl_eur: float
    count: int


@dataclass(frozen=True)
class FeeAggregate:
    """Pro Datum ein Aufwands-Aggregat.

    netto_eur ist die Summe aller Fee-Posten an diesem Tag — negativ wenn
    Aufwand entstanden, positiv wenn netto storniert.
    """
    posting_date: date
    netto_eur: float
    count: int


def _is_sof_header(row: list[str]) -> bool:
    return _SOF_REQUIRED.issubset(set(row))


def _parse_date_yyyymmdd(s: str) -> date:
    s = s.strip()
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def parse_sof_csv(path: Path) -> list[SofRow]:
    """Liest eine SoF-CSV. Verarbeitet alle BaseCurrency-Zeilen."""
    rows: list[SofRow] = []
    cols: dict[str, int] = {}
    in_section = False

    def col(row: list[str], name: str, default: str = "") -> str:
        if name not in cols:
            return default
        idx = cols[name]
        return row[idx] if idx < len(row) else default

    with open(path) as f:
        reader = csv.reader(f)
        for raw in reader:
            if not raw:
                continue
            if _is_sof_header(raw):
                cols = {name: i for i, name in enumerate(raw)}
                in_section = True
                continue
            if not in_section:
                continue
            if col(raw, "LevelOfDetail") != "BaseCurrency":
                continue
            try:
                amt = float(col(raw, "Amount") or 0)
            except ValueError:
                continue
            if amt == 0:
                continue
            try:
                d = _parse_date_yyyymmdd(col(raw, "Date")[:8])
            except (ValueError, IndexError):
                continue
            rows.append(
                SofRow(
                    posting_date=d,
                    amount_eur=amt,
                    asset_class=col(raw, "AssetClass"),
                    description=col(raw, "Description"),
                )
            )
    return rows


def parse_sof_files(paths: list[Path]) -> list[SofRow]:
    """Liest mehrere SoF-Files. Pro Datum gewinnt das File mit den meisten
    Posten (= das vollstaendigste).

    Identische Rows innerhalb eines Files werden NICHT dedupliziert — IBKR
    liefert haeufig mehrere identische Adjustments am selben Tag (z.B.
    mehrere Accruals desselben Typs), und das sind echte separate Posten.

    Annahme: ein File ist self-consistent fuer die Tage die es abdeckt.
    Bei ueberlappenden Date-Ranges (z.B. Archive ueberlappt mit YTD/365d)
    gewinnt fuer jeden einzelnen Tag das File mit den meisten Rows an
    diesem Tag — typischerweise das jeweils umfassendere Statement.
    """
    by_file: list[dict[date, list[SofRow]]] = []
    for p in paths:
        rows_by_date: dict[date, list[SofRow]] = defaultdict(list)
        for r in parse_sof_csv(p):
            rows_by_date[r.posting_date].append(r)
        by_file.append(rows_by_date)

    all_dates: set[date] = set()
    for rbd in by_file:
        all_dates.update(rbd.keys())

    out: list[SofRow] = []
    for d in sorted(all_dates):
        candidates = [rbd[d] for rbd in by_file if d in rbd]
        best = max(candidates, key=len)
        out.extend(best)
    return out


def classify(row: SofRow) -> str:
    """Liefert 'trade', 'transfer' oder 'fee'."""
    if row.asset_class:
        return "trade"
    if abs(row.amount_eur) >= TRANSFER_THRESHOLD:
        return "transfer"
    return "fee"


def aggregate_trades(rows: list[SofRow]) -> list[TradeAggregate]:
    """Aggregiert Trade-Posten pro (Description, AssetClass, Geschaeftsjahr).

    Datum = letztes Posting-Datum aller zugehoerigen Cashflows (Close-Date
    des Trades). Das passt zur Roundtrip-Definition der Allocator-Logik:
    eine Verguetung pro abgeschlossenem Roundtrip, nicht pro Mark-to-Market.

    Per-Year-Split: Symbole, die ueber Jahresgrenzen hinweg gehandelt werden
    (z.B. FX-Cashflows EUR.USD) wuerden sonst in einem Aggregat zusammenfallen
    und das letzte Datum bestimmen — was fuer den Steuer-Report (pro
    Geschaeftsjahr) falsch ist. Pro Jahr ein eigenes Aggregat verhindert das.
    """
    by_key: dict[tuple[str, str, int], list[SofRow]] = defaultdict(list)
    for r in rows:
        if classify(r) != "trade":
            continue
        by_key[(r.description, r.asset_class, r.posting_date.year)].append(r)

    out: list[TradeAggregate] = []
    for (desc, ac, _year), items in by_key.items():
        pnl = round(sum(r.amount_eur for r in items), 2)
        out.append(
            TradeAggregate(
                posting_date=max(r.posting_date for r in items),
                description=desc,
                asset_class=ac,
                pnl_eur=pnl,
                count=len(items),
            )
        )
    out.sort(key=lambda a: (a.posting_date, a.description))
    return out


def aggregate_fees(rows: list[SofRow]) -> list[FeeAggregate]:
    """Aggregiert Fee-Posten pro Datum (alles unter TRANSFER_THRESHOLD)."""
    by_date: dict[date, list[SofRow]] = defaultdict(list)
    for r in rows:
        if classify(r) != "fee":
            continue
        by_date[r.posting_date].append(r)

    out: list[FeeAggregate] = []
    for d, items in sorted(by_date.items()):
        netto = round(sum(r.amount_eur for r in items), 2)
        if netto == 0:
            continue
        out.append(FeeAggregate(posting_date=d, netto_eur=netto, count=len(items)))
    return out
