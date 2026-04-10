"""
Allocation-Checker — Soll vs. Ist Vergleich.
"""

from dataclasses import dataclass

from eule.config import AllocationConfig
from eule.models import Position, PortfolioSnapshot


@dataclass(frozen=True)
class AllocationCheck:
    """Ergebnis der Allokations-Pruefung fuer eine Kategorie."""

    category: str
    actual_pct: float
    actual_eur: float
    target_min: float
    target_max: float
    status: str  # ok, under, over
    deviation: float  # Abweichung zum naechsten Zielrand


@dataclass(frozen=True)
class ConcentrationAlert:
    """Warnung bei zu hoher Einzelposition-Konzentration."""

    ticker: str
    broker: str
    pct: float
    limit: float


def check_allocation(
    snapshot: PortfolioSnapshot,
    config: AllocationConfig,
) -> tuple[list[AllocationCheck], list[ConcentrationAlert]]:
    """Prueft Ist-Allokation gegen Soll-Targets.

    Returns:
        (allocation_checks, concentration_alerts)
    """
    checks: list[AllocationCheck] = []
    concentration_alerts: list[ConcentrationAlert] = []

    # Kategorie-Checks
    for target in config.targets:
        actual_pct = snapshot.category_pcts.get(target.category, 0.0)
        actual_eur = snapshot.category_totals.get(target.category, 0.0)

        if actual_pct < target.min_pct:
            status = "under"
            deviation = target.min_pct - actual_pct
        elif actual_pct > target.max_pct:
            status = "over"
            deviation = actual_pct - target.max_pct
        else:
            status = "ok"
            deviation = 0.0

        checks.append(AllocationCheck(
            category=target.category,
            actual_pct=actual_pct,
            actual_eur=actual_eur,
            target_min=target.min_pct,
            target_max=target.max_pct,
            status=status,
            deviation=deviation,
        ))

    # Einzelposition-Konzentration
    max_pct = config.max_single_position_pct
    for p in snapshot.positions:
        if p.pct_of_portfolio and p.pct_of_portfolio > max_pct:
            concentration_alerts.append(ConcentrationAlert(
                ticker=p.ticker,
                broker=p.broker,
                pct=p.pct_of_portfolio,
                limit=max_pct,
            ))

    return checks, concentration_alerts
