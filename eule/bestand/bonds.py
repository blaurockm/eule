"""
Bond-Tracker — Kupon, Faelligkeit, Rating.
"""

from dataclasses import dataclass
from datetime import date
from dateutil.relativedelta import relativedelta

from eule.models import BondPosition, Position


@dataclass(frozen=True)
class BondAlert:
    """Alert fuer eine Anleihe-Position."""

    position: BondPosition
    alert_type: str  # maturity_warning, coupon_upcoming
    message: str
    action_suggested: str


def _compute_next_coupon(maturity: date, frequency: str) -> date | None:
    """Berechnet den naechsten Kupon-Termin basierend auf Faelligkeit und Frequenz.

    Kupon-Termine werden rueckwaerts ab Faelligkeit berechnet.
    """
    if frequency == "annual":
        step = relativedelta(years=1)
    elif frequency == "semi-annual":
        step = relativedelta(months=6)
    elif frequency == "quarterly":
        step = relativedelta(months=3)
    else:
        return None

    today = date.today()
    coupon_date = maturity
    # Rueckwaerts gehen bis wir in die Vergangenheit kommen
    while coupon_date > today:
        prev = coupon_date - step
        if prev <= today:
            return coupon_date
        coupon_date = prev

    # Alle Kupons in der Vergangenheit → naechster ist Faelligkeit
    return maturity


def analyze_bonds(
    positions: list[Position],
    maturity_warning_days: int = 90,
    coupon_warning_days: int = 30,
) -> tuple[list[BondPosition], list[BondAlert]]:
    """Analysiert Anleihe-Positionen und generiert Alerts.

    Returns:
        (bond_positions, alerts)
    """
    bonds = [p for p in positions if isinstance(p, BondPosition)]
    alerts: list[BondAlert] = []
    today = date.today()

    for bond in bonds:
        # Faelligkeits-Warnung
        if bond.maturity_date:
            days = (bond.maturity_date - today).days
            if days <= maturity_warning_days:
                alerts.append(BondAlert(
                    position=bond,
                    alert_type="maturity_warning",
                    message=f"{bond.ticker}: Faelligkeit in {days} Tagen ({bond.maturity_date})",
                    action_suggested="Re-Investition planen",
                ))

        # Naechster Kupon
        if bond.maturity_date and bond.coupon_rate > 0:
            next_coupon = _compute_next_coupon(bond.maturity_date, bond.coupon_frequency)
            if next_coupon:
                days_to_coupon = (next_coupon - today).days
                if 0 < days_to_coupon <= coupon_warning_days:
                    alerts.append(BondAlert(
                        position=bond,
                        alert_type="coupon_upcoming",
                        message=f"{bond.ticker}: Kupon-Zahlung in {days_to_coupon} Tagen ({next_coupon})",
                        action_suggested="Kupon-Eingang pruefen",
                    ))

    return bonds, alerts
