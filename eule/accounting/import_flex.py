"""Import von IBKR Flex-Query-CSV-Dateien als manuelle Trades.

Pflichtfelder im Flex-Query (Trade-Section):
    UnderlyingSymbol, Symbol, TradeDate, NetCash, IBCommission,
    IBCommissionCurrency, TradeID
Optional aber empfohlen:
    Conid, UnderlyingConid, IBExecID

Plus ConversionRate-Section: Date/Time (oder ReportDate), FromCurrency,
ToCurrency, Rate.

Mehrere Dateien werden zusammengefuehrt, Trades dedupliziert ueber TradeID.
Trades, deren TradeID ODER IBExecID in der Hase-DB als trade_ref existiert,
werden ausgeschlossen (DB ist authoritativ fuer diese).

Ergebnis: pro (Symbol, TradeDate)-Gruppe ein Eintrag fuer manual_trades.yaml
mit aufsummiertem PnL in EUR.
"""

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class FlexTrade:
    trade_id: str
    ibexec_id: str           # leer wenn Spalte fehlt
    trade_date: date
    underlying: str
    symbol: str
    net_cash: float          # Original-Waehrung
    commission: float        # Original-Waehrung
    currency: str
    conid: str
    underlying_conid: str


@dataclass(frozen=True)
class FxRate:
    report_date: date
    from_ccy: str
    to_ccy: str
    rate: float


@dataclass(frozen=True)
class AggregatedTrade:
    """Pro (Symbol, Datum)-Gruppe ein Eintrag fuer manual_trades.yaml."""
    trade_date: date
    symbol: str
    underlying: str
    pnl_eur: float
    trade_ids: tuple[str, ...]


# ──────────────────────────────────────────
# Header-Detection
# ──────────────────────────────────────────


_TRADE_REQUIRED = {"TradeDate", "Symbol", "NetCash", "IBCommission"}
_FX_REQUIRED = {"FromCurrency", "ToCurrency", "Rate"}


def _is_trade_header(row: list[str]) -> bool:
    return _TRADE_REQUIRED.issubset(set(row))


def _is_fx_header(row: list[str]) -> bool:
    return _FX_REQUIRED.issubset(set(row))


def _parse_date_yyyymmdd(s: str) -> date:
    s = s.strip()
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# ──────────────────────────────────────────
# CSV-Parsing
# ──────────────────────────────────────────


def parse_flex_csv(path: Path) -> tuple[list[FlexTrade], list[FxRate]]:
    """Liest eine Flex-CSV. Erkennt Sections ueber Header-Zeilen."""
    trades: list[FlexTrade] = []
    fx: list[FxRate] = []
    current = None
    cols: dict[str, int] = {}

    def col(row: list[str], name: str, default: str = "") -> str:
        if name not in cols:
            return default
        idx = cols[name]
        return row[idx] if idx < len(row) else default

    with open(path) as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            if _is_trade_header(row):
                current = "trade"
                cols = {name: i for i, name in enumerate(row)}
                continue
            if _is_fx_header(row):
                current = "fx"
                cols = {name: i for i, name in enumerate(row)}
                continue

            if current == "trade":
                try:
                    trades.append(
                        FlexTrade(
                            trade_id=col(row, "TradeID"),
                            ibexec_id=col(row, "IBExecID"),
                            trade_date=_parse_date_yyyymmdd(col(row, "TradeDate")),
                            underlying=col(row, "UnderlyingSymbol"),
                            symbol=col(row, "Symbol"),
                            net_cash=float(col(row, "NetCash") or 0),
                            commission=float(col(row, "IBCommission") or 0),
                            currency=col(row, "IBCommissionCurrency"),
                            conid=col(row, "Conid"),
                            underlying_conid=col(row, "UnderlyingConid"),
                        )
                    )
                except (ValueError, IndexError):
                    continue
            elif current == "fx":
                date_col = "Date/Time" if "Date/Time" in cols else "ReportDate"
                try:
                    fx.append(
                        FxRate(
                            report_date=_parse_date_yyyymmdd(col(row, date_col)),
                            from_ccy=col(row, "FromCurrency"),
                            to_ccy=col(row, "ToCurrency"),
                            rate=float(col(row, "Rate") or 0),
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
            if t.trade_id and t.trade_id not in all_trades:
                all_trades[t.trade_id] = t
        for r in fx:
            if r.to_ccy != "EUR":
                continue
            fx_lookup[(r.report_date, r.from_ccy)] = r.rate

    return list(all_trades.values()), fx_lookup


def to_eur(
    trade: FlexTrade,
    fx_lookup: dict[tuple[date, str], float],
) -> float | None:
    """Konvertiert NetCash in EUR. None wenn FX-Rate fehlt."""
    if trade.currency == "EUR":
        return trade.net_cash
    rate = fx_lookup.get((trade.trade_date, trade.currency))
    if rate is None:
        return None
    return trade.net_cash * rate


# ──────────────────────────────────────────
# Aggregation
# ──────────────────────────────────────────


def aggregate(
    trades: list[FlexTrade],
    fx_lookup: dict[tuple[date, str], float],
    skip_trade_ids: set[str],
) -> tuple[list[AggregatedTrade], list[FlexTrade], list[FlexTrade]]:
    """Aggregiert pro (Symbol, Datum). skip_trade_ids matcht gegen TradeID
    UND IBExecID (d.h. trade_refs aus der Hase-DB koennen entweder TradeID
    oder ExecID sein — wir testen beides).

    Returns:
        (importierbare Eintraege, skipped, fx_missing)
    """
    skipped: list[FlexTrade] = []
    fx_missing: list[FlexTrade] = []
    by_group: dict[tuple[date, str, str], list[tuple[FlexTrade, float]]] = defaultdict(list)

    for t in trades:
        # FX-Konversionen (kein Underlying, Symbol wie EUR.USD) raus
        if not t.underlying or t.symbol == "" or "." in t.underlying:
            continue
        if t.trade_id in skip_trade_ids or (t.ibexec_id and t.ibexec_id in skip_trade_ids):
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


# ──────────────────────────────────────────
# YAML-Output
# ──────────────────────────────────────────


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
    """Quoting fuer YAML-Strings mit Sonderzeichen."""
    if any(c in s for c in ",:#"):
        return "'" + s.replace("'", "''") + "'"
    return s
