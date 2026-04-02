"""Tests fuer eule/fx.py."""

import pytest

from eule.fx import (
    convert_to_eur,
    get_fx_rate,
    reset_cache,
    FALLBACK_RATES_TO_EUR,
)


@pytest.fixture(autouse=True)
def _reset_fx_cache():
    """Cache vor jedem Test zuruecksetzen."""
    reset_cache()
    yield
    reset_cache()


class TestGetFxRate:
    """FX-Rate Abfrage."""

    def test_same_currency(self):
        assert get_fx_rate("EUR", "EUR") == 1.0
        assert get_fx_rate("USD", "USD") == 1.0

    def test_usd_to_eur_fallback(self, monkeypatch):
        """Mit Fallback-Raten (ECB nicht erreichbar)."""
        import eule.fx
        monkeypatch.setattr(eule.fx, "_fetch_ecb_rates", lambda: (_ for _ in ()).throw(ConnectionError("test")))
        rate = get_fx_rate("USD", "EUR")
        assert rate == FALLBACK_RATES_TO_EUR["USD"]

    def test_eur_to_usd_fallback(self, monkeypatch):
        import eule.fx
        monkeypatch.setattr(eule.fx, "_fetch_ecb_rates", lambda: (_ for _ in ()).throw(ConnectionError("test")))
        rate = get_fx_rate("EUR", "USD")
        # EUR → USD = 1.0 / FALLBACK_RATES_TO_EUR["USD"]
        expected = 1.0 / FALLBACK_RATES_TO_EUR["USD"]
        assert abs(rate - expected) < 0.01

    def test_unknown_currency_returns_one(self, monkeypatch):
        import eule.fx
        monkeypatch.setattr(eule.fx, "_fetch_ecb_rates", lambda: (_ for _ in ()).throw(ConnectionError("test")))
        rate = get_fx_rate("XYZ", "EUR")
        assert rate == 1.0

    def test_cache_reuse(self, monkeypatch):
        """Zweiter Aufruf nutzt Cache, kein erneuter Fetch."""
        import eule.fx
        call_count = 0

        def mock_fetch():
            nonlocal call_count
            call_count += 1
            return {"EUR": 1.0, "USD": 0.91}

        monkeypatch.setattr(eule.fx, "_fetch_ecb_rates", mock_fetch)
        get_fx_rate("USD", "EUR")
        get_fx_rate("USD", "EUR")
        assert call_count == 1


class TestConvertToEur:
    """Betrags-Konvertierung."""

    def test_eur_unchanged(self):
        assert convert_to_eur(100.0, "EUR") == 100.0

    def test_usd_to_eur(self, monkeypatch):
        import eule.fx
        monkeypatch.setattr(eule.fx, "_fetch_ecb_rates", lambda: {"EUR": 1.0, "USD": 0.90})
        result = convert_to_eur(100.0, "USD")
        assert abs(result - 90.0) < 0.01
