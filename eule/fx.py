"""
FX-Raten fuer Eule.

Primaer: ECB Daily Reference Rates (kostenlos, kein Auth).
Fallback: Hardcoded Raten.
"""

from xml.etree import ElementTree

import httpx
from loguru import logger

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_NS = {"gesmes": "http://www.gesmes.org/xml/2002-08-01",
           "eurofxref": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}

# Fallback-Raten (ungefaehr, Stand Maerz 2026)
FALLBACK_RATES_TO_EUR: dict[str, float] = {
    "EUR": 1.0,
    "USD": 0.92,
    "GBP": 1.17,
    "CHF": 1.05,
    "JPY": 0.006,
    "CAD": 0.67,
    "AUD": 0.59,
    "SEK": 0.088,
    "NOK": 0.086,
    "DKK": 0.134,
}

# Cache pro CLI-Aufruf
_rate_cache: dict[str, float] | None = None
_used_fallback: bool = False


def _fetch_ecb_rates() -> dict[str, float]:
    """Holt aktuelle ECB-Referenzkurse. Gibt {CCY: rate_to_EUR} zurueck."""
    resp = httpx.get(ECB_URL, timeout=10.0)
    resp.raise_for_status()

    root = ElementTree.fromstring(resp.content)
    cube_parent = root.find(".//eurofxref:Cube/eurofxref:Cube", ECB_NS)
    if cube_parent is None:
        raise ValueError("ECB XML: Cube-Element nicht gefunden")

    rates: dict[str, float] = {"EUR": 1.0}
    for cube in cube_parent.findall("eurofxref:Cube", ECB_NS):
        ccy = cube.get("currency")
        rate_str = cube.get("rate")
        if ccy and rate_str:
            # ECB gibt "1 EUR = X CCY" → wir brauchen "1 CCY = ? EUR"
            rates[ccy] = 1.0 / float(rate_str)

    return rates


def _get_rates() -> dict[str, float]:
    """Gibt gecachte Raten zurueck, laedt bei Bedarf."""
    global _rate_cache, _used_fallback
    if _rate_cache is not None:
        return _rate_cache

    try:
        _rate_cache = _fetch_ecb_rates()
        _used_fallback = False
        logger.debug(f"ECB-Raten geladen: {len(_rate_cache)} Waehrungen")
    except Exception as e:
        logger.warning(f"ECB-Raten nicht verfuegbar ({e}), nutze Fallback-Raten")
        _rate_cache = FALLBACK_RATES_TO_EUR.copy()
        _used_fallback = True

    return _rate_cache


def get_fx_rate(from_ccy: str, to_ccy: str) -> float:
    """Gibt den Wechselkurs von from_ccy nach to_ccy zurueck.

    Beispiel: get_fx_rate("USD", "EUR") → 0.92
    """
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()
    if from_ccy == to_ccy:
        return 1.0

    rates = _get_rates()

    # Beide Raten sind "CCY → EUR"
    from_to_eur = rates.get(from_ccy)
    to_to_eur = rates.get(to_ccy)

    if from_to_eur is None:
        logger.warning(f"FX-Rate fuer {from_ccy} nicht verfuegbar, nutze 1.0")
        return 1.0
    if to_to_eur is None:
        logger.warning(f"FX-Rate fuer {to_ccy} nicht verfuegbar, nutze 1.0")
        return 1.0

    # from → EUR → to: (1 from = from_to_eur EUR) → (1 from = from_to_eur / to_to_eur to)
    return from_to_eur / to_to_eur


def convert_to_eur(amount: float, from_ccy: str) -> float:
    """Konvertiert einen Betrag in EUR."""
    return amount * get_fx_rate(from_ccy, "EUR")


def used_fallback_rates() -> bool:
    """Gibt True zurueck wenn Fallback-Raten verwendet wurden."""
    return _used_fallback


def reset_cache() -> None:
    """Cache zuruecksetzen (fuer Tests)."""
    global _rate_cache, _used_fallback
    _rate_cache = None
    _used_fallback = False
