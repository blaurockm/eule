"""Tests fuer eule/quotes.py."""

from unittest.mock import MagicMock

from eule.quotes import fetch_quotes, fetch_quotes_yfinance


class TestFetchQuotes:
    """Quote-Fetching Logik."""

    def test_empty_tickers(self):
        results, warnings = fetch_quotes([])
        assert results == {}
        assert warnings == []

    def test_without_ibkr_uses_yfinance(self, monkeypatch):
        """Ohne ibkr_client geht es direkt zu yfinance."""
        monkeypatch.setattr(
            "eule.quotes.fetch_quotes_yfinance",
            lambda tickers: {"AAPL": 175.0, "MSFT": 420.0},
        )
        results, warnings = fetch_quotes(["AAPL", "MSFT"], ibkr_client=None)
        assert results["AAPL"] == 175.0
        assert results["MSFT"] == 420.0

    def test_ibkr_success(self, monkeypatch):
        """IBKR liefert alle Kurse, kein Fallback noetig."""
        monkeypatch.setattr(
            "eule.quotes.fetch_quotes_ibkr",
            lambda tickers, client: {"AAPL": 175.0},
        )
        mock_client = MagicMock()
        results, warnings = fetch_quotes(["AAPL"], ibkr_client=mock_client)
        assert results["AAPL"] == 175.0

    def test_ibkr_partial_fallback(self, monkeypatch):
        """IBKR liefert nur teilweise, Rest via yfinance."""
        monkeypatch.setattr(
            "eule.quotes.fetch_quotes_ibkr",
            lambda tickers, client: {"AAPL": 175.0, "XYZ": None},
        )
        monkeypatch.setattr(
            "eule.quotes.fetch_quotes_yfinance",
            lambda tickers: {"XYZ": 42.0},
        )
        mock_client = MagicMock()
        results, warnings = fetch_quotes(["AAPL", "XYZ"], ibkr_client=mock_client)
        assert results["AAPL"] == 175.0
        assert results["XYZ"] == 42.0

    def test_ibkr_exception_falls_back(self, monkeypatch):
        """IBKR wirft Exception, Fallback zu yfinance mit Warnung."""
        import eule.quotes
        eule.quotes._ibkr_warned = False

        def ibkr_fail(tickers, client):
            raise ConnectionError("Gateway offline")

        monkeypatch.setattr("eule.quotes.fetch_quotes_ibkr", ibkr_fail)
        monkeypatch.setattr(
            "eule.quotes.fetch_quotes_yfinance",
            lambda tickers: {"AAPL": 175.0},
        )
        mock_client = MagicMock()
        results, warnings = fetch_quotes(["AAPL"], ibkr_client=mock_client)
        assert results["AAPL"] == 175.0
        assert len(warnings) == 1
        assert "IBKR Market Data nicht erreichbar" in warnings[0]

        # Reset
        eule.quotes._ibkr_warned = False
