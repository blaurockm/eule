"""
Briefing — aggregiert alle Module in ein Gesamt-Summary.
"""

from dataclasses import dataclass

from eule.allocation import check_allocation
from eule.bonds import analyze_bonds
from eule.config import EuleConfig
from eule.models import PortfolioSnapshot
from eule.options import analyze_options


@dataclass
class Briefing:
    """Gesamt-Briefing aus allen Modulen."""

    snapshot: PortfolioSnapshot
    option_alerts: list
    bond_alerts: list
    allocation_checks: list
    concentration_alerts: list
    errors: list[str]


def create_briefing(cfg: EuleConfig, snapshot: PortfolioSnapshot) -> Briefing:
    """Erstellt ein Gesamt-Briefing."""
    _, option_alerts = analyze_options(
        snapshot.positions,
        expiry_warning_days=cfg.alerts.option_expiry_warning_days,
        fifty_pct_rule=cfg.alerts.fifty_pct_rule,
    )

    _, bond_alerts = analyze_bonds(snapshot.positions)

    allocation_checks, concentration_alerts = check_allocation(
        snapshot, cfg.allocation,
    )

    return Briefing(
        snapshot=snapshot,
        option_alerts=option_alerts,
        bond_alerts=bond_alerts,
        allocation_checks=allocation_checks,
        concentration_alerts=concentration_alerts,
        errors=snapshot.errors,
    )
