"""
Broker-Adapter fuer Eule.

Jeder Broker implementiert BrokerAdapter und liefert Positionen.
"""

from abc import ABC, abstractmethod

from loguru import logger

from eule.models import AccountSummary, Position


class BrokerAdapter(ABC):
    """Basis-Klasse fuer Broker-Anbindungen.

    Subklassen implementieren _fetch_positions_raw() und _fetch_balance_raw().
    Error-Handling ist in der Basisklasse — kein Broker-Fehler crasht den Lauf.
    """

    name: str

    @abstractmethod
    def _fetch_positions_raw(self) -> list[Position]:
        """Positionen vom Broker laden. Darf Exceptions werfen."""
        ...

    @abstractmethod
    def _fetch_balance_raw(self) -> AccountSummary | None:
        """Kontouebersicht laden. Darf Exceptions werfen."""
        ...

    def fetch_positions(self) -> tuple[list[Position], list[str]]:
        """Positionen + Fehler-Liste. Faengt Broker-spezifische Fehler ab."""
        try:
            return self._fetch_positions_raw(), []
        except Exception as e:
            logger.warning(f"[{self.name}] Fehler beim Laden der Positionen: {e}")
            return [], [f"{self.name}: {e}"]

    def fetch_balance(self) -> AccountSummary | None:
        """Kontouebersicht laden. Gibt None bei Fehlern."""
        try:
            return self._fetch_balance_raw()
        except Exception as e:
            logger.warning(f"[{self.name}] Fehler beim Laden der Balance: {e}")
            return None
