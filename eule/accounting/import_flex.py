"""Import von IBKR Flex-Query-CSV-Dateien als manuelle Trades.

Der Flex-Query muss folgende Felder enthalten (Trade-Section):
    UnderlyingSymbol, Symbol, TradeDate, NetCash, IBCommission,
    IBCommissionCurrency, Conid, UnderlyingConid, TradeID

Optional in derselben Datei: ConversionRate-Section (4 Spalten:
ReportDate, FromCurrency, ToCurrency, Rate). Wenn vorhanden, wird damit
NetCash in EUR umgerechnet. Trades in Fremdwaehrungen ohne FX-Eintrag
werden als Fehler gemeldet.

Mehrere Dateien werden zusammengefuehrt, dedupliziert ueber TradeID.
Trades, deren TradeID in der Hase-DB als trade_ref existiert, werden
ausgeschlossen (DB ist authoritativ fuer diese).

Ergebnis: pro (UnderlyingSymbol, Symbol, TradeDate)-Gruppe ein Eintrag
fuer manual_trades.yaml mit aufsummiertem PnL in EUR.
"""

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class FlexTrade:
    trade_id: str
    trade_date: date
    underlying: str
    symbol: str
    net_cash: float          # Original-Waehrung
    commission: float        # Original-Waehrung (negativ = bezahlt)
    currency: str
    conid: str
    underlying_conid: str


@dataclass(frozen=True)
class FxRate:
    report_date: date
    from_ccy: str
    to_ccy: str
    rate: float              # 1 from_ccy = rate * to_ccy


@dataclass(frozen=True)
class AggregatedTrade:
    """Pro (Symbol, Datum)-Gruppe ein Eintrag fuer manual_trades.yaml."""
    trade_date: date
    symbol: str
    underlying: str
    pnl_eur: float
    trade_ids: tuple[str, ...]


def _parse_date_yyyymmdd(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def parse_flex_csv(path: Path) -> tuple[list[FlexTrade], list[FxRate]]:
    """Liest eine Flex-CSV. Erkennt Trade-Zeilen (9 Spalten) und FX-Zeilen (4 Spalten)
    am Spaltenanzahl. Header-Zeilen werden uebersprungen.
    """
    trades: list[FlexTrade] = []
    fx: list[FxRate] = []

    with open(path) as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            if len(row) == 9:
                # Header-Zeile erkennen
                if row[0] == "UnderlyingSymbol" or row[2] == "TradeDate":
                    continue
                try:
                    trades.append(
                        FlexTrade(
                            trade_id=row[8],
                            trade_date=_parse_date_yyyymmdd(row[2]),
                            underlying=row[0],
                            symbol=row[1],
                            net_cash=float(row[3]),
                            commission=float(row[4]),
                            currency=row[5],
                            conid=row[6],
                            underlying_conid=row[7],
                        )
                    )
                except (ValueError, IndexError):
                    continue
            elif len(row) == 4:
                # Header oder FX-Zeile
                if row[0] == "ReportDate" or row[1] == "FromCurrency":
                    continue
                try:
                    fx.append(
                        FxRate(
                            report_date=_parse_date_yyyymmdd(row[0]),
                            from_ccy=row[1],
                            to_ccy=row[2],
                            rate=float(row[3]),
                        )
                    )
                except (ValueError, IndexError):
                    continue

    return trades, fx


def parse_flex_files(paths: list[Path]) -> tuple[list[FlexTrade], dict[tuple[date, str], float]]:
    """Liest mehrere Flex-Dateien, dedupliziert Trades ueber TradeID,
    baut FX-Lookup (TradeDate, Currency) -> EUR-Rate.
    """
    all_trades: dict[str, FlexTrade] = {}
    fx_lookup: dict[tuple[date, str], float] = {}

    for p in paths:
        trades, fx = parse_flex_csv(p)
        for t in trades:
            all_trades.setdefault(t.trade_id, t)  # erste Datei gewinnt bei Duplikat
        for r in fx:
            if r.to_ccy != "EUR":
                continue
            key = (r.report_date, r.from_ccy)
            fx_lookup[key] = r.rate

    return list(all_trades.values()), fx_lookup


def to_eur(
    trade: FlexTrade,
    fx_lookup: dict[tuple[date, str], float],
) -> float | None:
    """Konvertiert NetCash in EUR. Gibt None zurueck wenn FX-Rate fehlt."""
    if trade.currency == "EUR":
        return trade.net_cash
    rate = fx_lookup.get((trade.trade_date, trade.currency))
    if rate is None:
        return None
    return trade.net_cash * rate


def aggregate(
    trades: list[FlexTrade],
    fx_lookup: dict[tuple[date, str], float],
    skip_trade_ids: set[str],
) -> tuple[list[AggregatedTrade], list[FlexTrade], list[FlexTrade]]:
    """Aggregiert pro (Symbol, Datum). Gibt zurueck:
    - aggregierte Trades (importierbar)
    - skipped (in Hase-DB)
    - fx_missing (Currency-Konversion fehlgeschlagen)
    """
    skipped: list[FlexTrade] = []
    fx_missing: list[FlexTrade] = []
    by_group: dict[tuple[date, str, str], list[tuple[FlexTrade, float]]] = defaultdict(list)

    for t in trades:
        # FX-Konversionen (kein Underlying, Symbol wie EUR.USD) raus
        if not t.underlying or t.symbol == "" or "." in t.underlying:
            continue
        if t.trade_id in skip_trade_ids:
            skipped.append(t)
            continue
        eur = to_eur(t, fx_lookup)
        if eur is None:
            fx_missing.append(t)
            continue
        by_group[(t.trade_date, t.symbol, t.underlying)].append((t, eur))

    aggregated: list[AggregatedTrade] = []
    for (d, sym, und), rows in by_group.items():
        pnl = sum(eur for _, eur in rows)
        ids = tuple(sorted(t.trade_id for t, _ in rows))
        aggregated.append(
            AggregatedTrade(
                trade_date=d,
                symbol=sym,
                underlying=und,
                pnl_eur=pnl,
                trade_ids=ids,
            )
        )

    aggregated.sort(key=lambda a: (a.trade_date, a.symbol))
    return aggregated, skipped, fx_missing


def render_yaml(
    aggregated: list[AggregatedTrade],
    *,
    header_comment: str = "",
) -> str:
    """Rendert die aggregierten Trades als YAML-Block fuer manual_trades.yaml."""
    lines: list[str] = []
    if header_comment:
        for ln in header_comment.splitlines():
            lines.append(f"# {ln}" if ln else "#")
        lines.append("")
    lines.append("manual_trades:")
    for a in aggregated:
        # Note enthaelt Underlying + TradeIDs fuer Nachvollziehbarkeit
        ids = ",".join(a.trade_ids)
        note = f"{a.underlying} | tid={ids}"
        lines.append(
            f"  - {{ date: {a.trade_date.isoformat()}, "
            f"symbol: {_yaml_str(a.symbol)}, "
            f"pnl_eur: {a.pnl_eur:.2f}, "
            f"note: {_yaml_str(note)} }}"
        )
    return "\n".join(lines) + "\n"


def _yaml_str(s: str) -> str:
    """Quoting fuer YAML-Strings, die Sonderzeichen enthalten koennten."""
    if any(c in s for c in ",:#"):
        # einfache Quotes, ggf. doppelt vorhandene escapen
        return "'" + s.replace("'", "''") + "'"
    return s
