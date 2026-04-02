"""
Positions-Aggregator — sammelt Positionen aus allen Brokern.
"""

from collections import defaultdict
from dataclasses import replace
from datetime import datetime

from loguru import logger

from eule.brokers import BrokerAdapter
from eule.brokers.ibkr import IbkrAdapter
from eule.brokers.ig import IgAdapter
from eule.brokers.manual import ManualAdapter
from eule.brokers.tradier import TradierAdapter
from eule.config import BrokerConfig, ConfigError, EuleConfig
from eule.fx import convert_to_eur, get_fx_rate, used_fallback_rates
from eule.models import Position, PortfolioSnapshot
from eule.quotes import fetch_quotes


def create_adapter(config: BrokerConfig) -> BrokerAdapter:
    """Erstellt den passenden BrokerAdapter fuer eine Broker-Config."""
    match config.broker_type:
        case "ibkr":
            return IbkrAdapter(config)
        case "tradier":
            return TradierAdapter(config)
        case "ig":
            return IgAdapter(config)
        case "manual":
            return ManualAdapter(config)
        case _:
            raise ConfigError(f"Unbekannter Broker-Typ: {config.broker_type}")


def aggregate_positions(cfg: EuleConfig) -> PortfolioSnapshot:
    """Sammelt Positionen aus allen aktiven Brokern, konvertiert FX, berechnet Anteile."""

    all_positions: list[Position] = []
    all_errors: list[str] = []
    ibkr_client = None
    quote_ticker_map: dict[str, str] = {}  # ticker → yfinance-Ticker
    price_transforms: dict[str, str] = {}  # ticker → Transformation (z.B. "oz_to_gram")

    # Adapter erstellen und Positionen laden
    for name, broker_cfg in cfg.brokers.items():
        if not broker_cfg.enabled:
            continue

        try:
            adapter = create_adapter(broker_cfg)
        except ConfigError as e:
            all_errors.append(str(e))
            continue

        # ibind Client merken fuer Quotes
        if isinstance(adapter, IbkrAdapter):
            try:
                ibkr_client = adapter.get_client()
            except Exception:
                pass

        positions, errors = adapter.fetch_positions()

        # quote_ticker und price_transform von ManualAdaptern sammeln (NACH fetch)
        if isinstance(adapter, ManualAdapter):
            quote_ticker_map.update(adapter.quote_ticker_map)
            price_transforms.update(adapter.price_transform)
        all_positions.extend(positions)
        all_errors.extend(errors)

    # Live-Kurse fuer Positionen ohne current_price
    tickers_needing_quotes = [
        p.ticker for p in all_positions
        if p.current_price is None and p.ticker
    ]
    if tickers_needing_quotes:
        # quote_ticker_map: eigentlichen Ticker → yfinance-Ticker
        actual_tickers = [quote_ticker_map.get(t, t) for t in tickers_needing_quotes]
        quotes, quote_warnings = fetch_quotes(actual_tickers, ibkr_client=ibkr_client)
        all_errors.extend(quote_warnings)

        # Ergebnisse zurueck auf Original-Ticker mappen
        reverse_map = {quote_ticker_map.get(t, t): t for t in tickers_needing_quotes}
        ticker_quotes: dict[str, float | None] = {}
        for qt, price in quotes.items():
            orig = reverse_map.get(qt, qt)
            if price is not None and orig in price_transforms:
                if price_transforms[orig] == "oz_to_gram":
                    price = price / 31.1035  # Troy-Unze → Gramm
            ticker_quotes[orig] = price

        updated = []
        for p in all_positions:
            if p.current_price is None and p.ticker in ticker_quotes and ticker_quotes[p.ticker] is not None:
                price = ticker_quotes[p.ticker]
                mv = abs(p.size * price)
                pnl = (price - p.entry_price) * p.size if p.entry_price else None
                updated.append(replace(p, current_price=price, market_value=mv, unrealized_pnl=pnl))
            else:
                updated.append(p)
        all_positions = updated

    # Fuer Positionen ohne current_price: market_value aus entry_price schaetzen
    final_positions = []
    for p in all_positions:
        if p.market_value is None and p.entry_price:
            mv = abs(p.size * p.entry_price)
            p = replace(p, market_value=mv)
        final_positions.append(p)
    all_positions = final_positions

    # FX-Konvertierung → EUR
    base_ccy = cfg.base_currency
    fx_rates: dict[str, float] = {}
    converted = []
    for p in all_positions:
        if p.currency != base_ccy:
            rate_key = f"{p.currency}/{base_ccy}"
            if rate_key not in fx_rates:
                fx_rates[rate_key] = get_fx_rate(p.currency, base_ccy)
            rate = fx_rates[rate_key]

            mv_eur = p.market_value * rate if p.market_value else None
            pnl_eur = p.unrealized_pnl * rate if p.unrealized_pnl is not None else None
            converted.append(replace(p, market_value_eur=mv_eur, unrealized_pnl_eur=pnl_eur))
        else:
            converted.append(replace(
                p,
                market_value_eur=p.market_value,
                unrealized_pnl_eur=p.unrealized_pnl,
            ))
    all_positions = converted

    if used_fallback_rates():
        all_errors.append("FX-Raten: ECB nicht erreichbar, nutze Fallback-Raten")

    # Totals berechnen
    total_eur = sum(p.market_value_eur or 0 for p in all_positions)
    broker_totals: dict[str, float] = defaultdict(float)
    category_totals: dict[str, float] = defaultdict(float)

    for p in all_positions:
        mv = p.market_value_eur or 0
        broker_totals[p.broker] += mv
        category_totals[p.category] += mv

    # Prozente berechnen
    category_pcts = {cat: val / total_eur if total_eur > 0 else 0
                     for cat, val in category_totals.items()}

    # pct_of_portfolio setzen
    final = []
    for p in all_positions:
        pct = (p.market_value_eur or 0) / total_eur if total_eur > 0 else 0
        final.append(replace(p, pct_of_portfolio=pct))

    return PortfolioSnapshot(
        positions=final,
        total_value_eur=total_eur,
        broker_totals=dict(broker_totals),
        category_totals=dict(category_totals),
        category_pcts=category_pcts,
        timestamp=datetime.now().isoformat(timespec="seconds"),
        fx_rates=fx_rates,
        errors=all_errors,
    )
