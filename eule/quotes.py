"""
Live-Kurse fuer Eule.

Primaer: IBKR via ibind (Client Portal Market Data API).
Fallback: yfinance.
"""

import time

from loguru import logger

_ibkr_warned = False


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


def fetch_bond_quotes_ibkr(
    isin_map: dict[str, str],
    ibkr_client,
) -> dict[str, float | None]:
    """Holt Bond-Kurse via IBKR ISIN-Suche.

    Bond-Preise sind in % vom Nennwert (z.B. 39.03 = 39.03% of par).

    Args:
        isin_map: {ticker: ISIN}
        ibkr_client: ibind IbkrClient

    Returns:
        {ticker: price_pct} — Preis in % vom Nennwert, None wenn nicht gefunden
    """
    results: dict[str, float | None] = {}

    for ticker, isin in isin_map.items():
        try:
            search_res = ibkr_client.search_contract_by_symbol(isin)
            if not hasattr(search_res, "data") or not search_res.data:
                logger.debug(f"IBKR Bond: Kein Ergebnis fuer ISIN {isin}")
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
                logger.debug(f"IBKR Bond {ticker} (ISIN {isin}): {price}%")

        except Exception as e:
            logger.debug(f"IBKR Bond {ticker} fehlgeschlagen: {e}")
            results[ticker] = None

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
