"""
Live-Kurse fuer Eule.

Primaer: IBKR via ibind (Client Portal Market Data API).
Fallback: yfinance.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

_ibkr_warned = False


@dataclass
class QuoteDetail:
    """Detailliertes Quote-Ergebnis fuer einzelnen Ticker."""
    ticker: str
    last: float | None
    bid: float | None
    ask: float | None
    change: float | None
    change_pct: float | None
    source: str  # "ibkr" oder "yfinance"
    timestamp: datetime | None = None  # Client-seitige Abrufzeit
    md_code: str | None = None  # IBKR Feld 6509 roh (z.B. "RB", "DP", "Z")
    md_status: str | None = None  # Interpretation: realtime/snapshot/delayed/frozen/not_subscribed/no_data

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "last": self.last,
            "bid": self.bid,
            "ask": self.ask,
            "change": self.change,
            "change_pct": self.change_pct,
            "source": self.source,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "md_code": self.md_code,
            "md_status": self.md_status,
        }


def interpret_md_code(code: str | None) -> str:
    """Interpretiert IBKR Market Data Availability (Feld 6509).

    Codes sind kombiniert (z.B. "RB" = Realtime + Book, "DPB" = Delayed Snapshot Book).
    Achtung: "D" dominiert "P" — ein Delayed-Snapshot ist trotzdem verzoegert.

    Buchstaben: R=Realtime, D=Delayed, Z=Frozen, Y=Frozen Delayed,
    N=Not Subscribed, P=Snapshot, B=Book.
    """
    if not code:
        return "no_data"
    if "R" in code:
        return "realtime"
    if "D" in code:
        return "delayed"
    if "Z" in code:
        return "frozen"
    if "Y" in code or "N" in code:
        return "not_subscribed"
    if "P" in code:
        return "snapshot"
    return "unknown"


def _parse_ibkr_price(entry: dict) -> float | None:
    """Extrahiert Preis aus ibkr Snapshot-Entry.

    Feld 31 (Last) kann Prefixe haben: C=Close, H=Halted.
    Beide sind valide Preise. Fallback: Midpoint aus Bid(84)/Ask(86).
    """
    # Last Price (Feld 31)
    last_raw = str(entry.get("31", ""))
    if last_raw:
        # "C39.0270" → 39.027, "H150.00" → 150.00, "42.50" → 42.50
        cleaned = last_raw.lstrip("CHch")
        if cleaned:
            try:
                return float(cleaned)
            except (ValueError, TypeError):
                pass

    # Fallback: Midpoint aus Bid/Ask
    bid_raw = entry.get("84")
    ask_raw = entry.get("86")
    if bid_raw and ask_raw:
        try:
            bid = float(bid_raw)
            ask = float(ask_raw)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
        except (ValueError, TypeError):
            pass

    return None


def fetch_quotes_ibkr(tickers: list[str], ibkr_client) -> dict[str, float | None]:
    """Holt Kurse via ibind Client Portal API.

    1. Symbol-Suche → conid
    2. Market Data Snapshot → Last Price (Feld 31)

    Args:
        tickers: Liste von Ticker-Symbolen
        ibkr_client: ibind IbkrClient Instanz

    Returns:
        {ticker: price} — None wenn nicht gefunden
    """
    results: dict[str, float | None] = {}

    for ticker in tickers:
        try:
            # Symbol suchen → conid
            search_res = ibkr_client.search_contract_by_symbol(ticker)
            if not hasattr(search_res, "data") or not search_res.data:
                logger.debug(f"IBKR: Kein Ergebnis fuer {ticker}")
                results[ticker] = None
                continue

            conid = str(search_res.data[0].get("conid", ""))
            if not conid:
                results[ticker] = None
                continue

            # Market Data Snapshot — Feld 31 = Last Price, 84 = Bid, 86 = Ask
            params = {"conids": conid, "fields": "31,84,86"}
            snapshot = ibkr_client.get("iserver/marketdata/snapshot", params, log=False)

            # Polling — ibkr braucht manchmal mehrere Versuche
            price = None
            for _ in range(5):
                if snapshot and hasattr(snapshot, "data") and snapshot.data:
                    entry = snapshot.data[0] if isinstance(snapshot.data, list) else snapshot.data
                    price = _parse_ibkr_price(entry)
                    if price is not None:
                        break
                time.sleep(0.3)
                snapshot = ibkr_client.get("iserver/marketdata/snapshot", params, log=False)

            results[ticker] = price
            if price:
                logger.debug(f"IBKR quote {ticker}: {price}")

        except Exception as e:
            logger.debug(f"IBKR quote {ticker} fehlgeschlagen: {e}")
            results[ticker] = None

    return results


def fetch_quotes_yfinance(tickers: list[str]) -> dict[str, float | None]:
    """Holt Kurse via yfinance (Fallback).

    Args:
        tickers: Liste von Ticker-Symbolen

    Returns:
        {ticker: price} — None wenn nicht gefunden
    """
    import yfinance as yf

    results: dict[str, float | None] = {}

    if not tickers:
        return results

    try:
        data = yf.download(tickers, period="1d", progress=False, threads=True)
        if data.empty:
            return {t: None for t in tickers}

        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    series = data["Close"].dropna()
                else:
                    series = data["Close"][ticker].dropna()
                if len(series) > 0:
                    results[ticker] = float(series.iloc[-1])
                else:
                    results[ticker] = None
            except (KeyError, IndexError):
                results[ticker] = None

    except Exception as e:
        logger.warning(f"yfinance Fehler: {e}")
        results = {t: None for t in tickers}

    return results


def fetch_quotes_ibkr_by_isin(
    isin_map: dict[str, str],
    ibkr_client,
) -> dict[str, float | None]:
    """Holt Kurse via IBKR ISIN-Suche.

    Funktioniert fuer Bonds, Aktien, ETFs — alles was eine ISIN hat.
    Achtung: Bei Bonds ist der Preis in % vom Nennwert (z.B. 39.03).

    Args:
        isin_map: {ticker: ISIN}
        ibkr_client: ibind IbkrClient

    Returns:
        {ticker: price} — None wenn nicht gefunden
    """
    results: dict[str, float | None] = {}

    for ticker, isin in isin_map.items():
        try:
            search_res = ibkr_client.search_contract_by_symbol(isin)
            if not hasattr(search_res, "data") or not search_res.data:
                logger.debug(f"IBKR ISIN: Kein Ergebnis fuer {isin} ({ticker})")
                results[ticker] = None
                continue

            conid = str(search_res.data[0].get("conid", ""))
            if not conid:
                results[ticker] = None
                continue

            params = {"conids": conid, "fields": "31,84,86"}
            price = None
            for _ in range(5):
                snapshot = ibkr_client.get("iserver/marketdata/snapshot", params, log=False)
                if snapshot and hasattr(snapshot, "data") and snapshot.data:
                    entry = snapshot.data[0] if isinstance(snapshot.data, list) else snapshot.data
                    price = _parse_ibkr_price(entry)
                    if price is not None:
                        break
                time.sleep(0.5)

            results[ticker] = price
            if price is not None:
                logger.debug(f"IBKR ISIN {ticker} ({isin}): {price}")

        except Exception as e:
            logger.debug(f"IBKR ISIN {ticker} fehlgeschlagen: {e}")
            results[ticker] = None

    return results


def _parse_ibkr_float(value) -> float | None:
    """Parst einen IBKR-Snapshot-Wert als float (mit Prefix-Handling)."""
    if value is None:
        return None
    raw = str(value).lstrip("CHch")
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _init_ibkr_session(ibkr_client) -> None:
    """Initialisiert die IBKR-Session fuer Marktdaten-Abfragen.

    Muss vor Snapshot-Calls aufgerufen werden (vgl. Hase BrokerIBKR.setup).
    """
    try:
        ibkr_client.receive_brokerage_accounts()
    except Exception as e:
        logger.debug(f"receive_brokerage_accounts fehlgeschlagen: {e}")
    try:
        ibkr_client.get("iserver/marketdata/unsubscribeall")
    except Exception as e:
        logger.debug(f"unsubscribeall fehlgeschlagen: {e}")


def fetch_quote_details(tickers: list[str], ibkr_client) -> list[QuoteDetail]:
    """Holt detaillierte Quotes via IBKR (Last, Bid, Ask, Change).

    Initialisiert die IBKR-Session (receive_brokerage_accounts + unsubscribeall)
    und pollt Snapshots mit bis zu 20 Retries pro Ticker (wie Hase).

    Args:
        tickers: Liste von Ticker-Symbolen
        ibkr_client: ibind IbkrClient Instanz

    Returns:
        Liste von QuoteDetail
    """
    _init_ibkr_session(ibkr_client)

    results: list[QuoteDetail] = []

    for ticker in tickers:
        try:
            search_res = ibkr_client.search_contract_by_symbol(ticker)
            if not hasattr(search_res, "data") or not search_res.data:
                logger.debug(f"IBKR: Kein Ergebnis fuer {ticker}")
                results.append(QuoteDetail(ticker=ticker, last=None, bid=None, ask=None,
                                           change=None, change_pct=None, source="ibkr"))
                continue

            conid = str(search_res.data[0].get("conid", ""))
            if not conid:
                results.append(QuoteDetail(ticker=ticker, last=None, bid=None, ask=None,
                                           change=None, change_pct=None, source="ibkr"))
                continue

            # 31=Last, 82=Change, 83=Change%, 84=Bid, 86=Ask, 6509=Market Data Availability
            params = {"conids": conid, "fields": "31,82,83,84,86,6509"}
            detail = QuoteDetail(ticker=ticker, last=None, bid=None, ask=None,
                                 change=None, change_pct=None, source="ibkr")

            for _ in range(20):
                snapshot = ibkr_client.get("iserver/marketdata/snapshot", params, log=False)
                if snapshot and hasattr(snapshot, "data") and snapshot.data:
                    entry = snapshot.data[0] if isinstance(snapshot.data, list) else snapshot.data
                    detail.last = _parse_ibkr_price(entry)
                    detail.bid = _parse_ibkr_float(entry.get("84"))
                    detail.ask = _parse_ibkr_float(entry.get("86"))
                    detail.change = _parse_ibkr_float(entry.get("82"))
                    detail.change_pct = _parse_ibkr_float(entry.get("83"))
                    md_raw = entry.get("6509")
                    if md_raw:
                        detail.md_code = str(md_raw)
                        detail.md_status = interpret_md_code(detail.md_code)
                    if detail.last is not None:
                        detail.timestamp = datetime.now()
                        break
                time.sleep(0.3)

            results.append(detail)

        except Exception as e:
            logger.debug(f"IBKR quote detail {ticker} fehlgeschlagen: {e}")
            results.append(QuoteDetail(ticker=ticker, last=None, bid=None, ask=None,
                                       change=None, change_pct=None, source="ibkr"))

    return results


def fetch_quotes(
    tickers: list[str],
    ibkr_client=None,
) -> tuple[dict[str, float | None], list[str]]:
    """Holt Live-Kurse. Primaer IBKR, Fallback yfinance.

    Args:
        tickers: Liste von Ticker-Symbolen
        ibkr_client: Optional ibind IbkrClient. Wenn None, direkt yfinance.

    Returns:
        ({ticker: price}, [warnings])
    """
    global _ibkr_warned
    if not tickers:
        return {}, []

    warnings: list[str] = []

    # Versuch 1: IBKR
    if ibkr_client is not None:
        try:
            results = fetch_quotes_ibkr(tickers, ibkr_client)
            missing = [t for t, p in results.items() if p is None]
            if not missing:
                return results, warnings

            # Fehlende Ticker via yfinance nachschlagen
            if missing:
                logger.debug(f"IBKR: {len(missing)} Ticker ohne Kurs, versuche yfinance")
                yf_results = fetch_quotes_yfinance(missing)
                for t, p in yf_results.items():
                    if p is not None:
                        results[t] = p
                return results, warnings

        except Exception as e:
            if not _ibkr_warned:
                warnings.append(f"IBKR Market Data nicht erreichbar ({e}), nutze yfinance")
                _ibkr_warned = True

    # Versuch 2: yfinance
    results = fetch_quotes_yfinance(tickers)
    if ibkr_client is not None and not _ibkr_warned:
        warnings.append("IBKR Market Data nicht erreichbar, nutze yfinance")
        _ibkr_warned = True

    return results, warnings


# ---------------------------------------------------------------------------
# Historische Kursdaten
# ---------------------------------------------------------------------------

# Sinnvolle Default-Perioden je Bar-Groesse
_DEFAULT_PERIODS: dict[str, str] = {
    "1min": "8h",
    "5min": "8h",
    "15min": "2d",
    "30min": "2d",
    "1h": "1w",
    "4h": "1w",
    "1d": "6m",
}


@dataclass
class HistoryBar:
    """Einzelner OHLCV-Bar."""
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        return {
            "dt": self.dt.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class HistoryResult:
    """Ergebnis einer History-Abfrage."""
    ticker: str
    bar: str
    period: str
    bars: list[HistoryBar] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "ticker": self.ticker,
            "bar": self.bar,
            "period": self.period,
            "count": len(self.bars),
        }
        if self.error:
            d["error"] = self.error
        else:
            d["bars"] = [b.to_dict() for b in self.bars]
        return d


def fetch_history(
    ticker: str,
    ibkr_client,
    bar: str = "5min",
    period: str | None = None,
) -> HistoryResult:
    """Holt historische OHLCV-Daten via IBKR.

    Args:
        ticker: Ticker-Symbol (z.B. AAPL)
        ibkr_client: ibind IbkrClient Instanz
        bar: Bar-Groesse (1min, 5min, 15min, 30min, 1h, 4h, 1d)
        period: Zeitraum (z.B. 8h, 2d, 1w, 6m). Default je nach bar.

    Returns:
        HistoryResult mit OHLCV-Bars
    """
    if period is None:
        period = _DEFAULT_PERIODS.get(bar, "1d")

    _init_ibkr_session(ibkr_client)

    try:
        resp = ibkr_client.marketdata_history_by_symbol(
            symbol=ticker,
            bar=bar,
            period=period,
            outside_rth=False,
        )

        if not resp or not resp.data or "data" not in resp.data:
            return HistoryResult(ticker=ticker, bar=bar, period=period,
                                 error="Keine Daten erhalten")

        bars: list[HistoryBar] = []
        for entry in resp.data["data"]:
            ts_ms = entry.get("t", 0)
            bars.append(HistoryBar(
                dt=datetime.utcfromtimestamp(ts_ms / 1000),
                open=float(entry.get("o", 0)),
                high=float(entry.get("h", 0)),
                low=float(entry.get("l", 0)),
                close=float(entry.get("c", 0)),
                volume=float(entry.get("v", 0)),
            ))

        bars.sort(key=lambda b: b.dt)
        return HistoryResult(ticker=ticker, bar=bar, period=period, bars=bars)

    except Exception as e:
        logger.debug(f"IBKR history {ticker} fehlgeschlagen: {e}")
        return HistoryResult(ticker=ticker, bar=bar, period=period, error=str(e))
