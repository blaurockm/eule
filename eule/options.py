"""
Options-Tracker — 50%-Regel, DTE-Warnungen, Roll-Vorbereitung.
"""

from dataclasses import dataclass
from datetime import date

from eule.models import OptionPosition, Position


@dataclass(frozen=True)
class OptionAlert:
    """Alert fuer eine Option-Position."""

    position: OptionPosition
    alert_type: str  # fifty_pct, expiry_warning, expiry_urgent, expiry_critical
    message: str
    action_suggested: str


def analyze_options(
    positions: list[Position],
    expiry_warning_days: list[int] | None = None,
    fifty_pct_rule: bool = True,
) -> tuple[list[OptionPosition], list[OptionAlert]]:
    """Analysiert Option-Positionen und generiert Alerts.

    Returns:
        (option_positions, alerts)
    """
    if expiry_warning_days is None:
        expiry_warning_days = [7, 3, 1]
    expiry_warning_days = sorted(expiry_warning_days, reverse=True)

    options = [p for p in positions if isinstance(p, OptionPosition)]
    alerts: list[OptionAlert] = []

    for opt in options:
        # 50%-Regel: Bei Short Options, wenn >= 50% der Praemie verdient
        if fifty_pct_rule and opt.direction == "short" and opt.sold_premium > 0:
            profit = opt.sold_premium - opt.current_value
            profit_pct = profit / opt.sold_premium * 100 if opt.sold_premium > 0 else 0
            if profit_pct >= 50:
                alerts.append(OptionAlert(
                    position=opt,
                    alert_type="fifty_pct",
                    message=f"{opt.ticker}: {profit_pct:.0f}% Gewinn erreicht (Ziel 50%)",
                    action_suggested="Schliessen und neuen Zyklus starten",
                ))

        # DTE-Warnungen — strengster passender Threshold gewinnt
        if opt.expiry:
            dte = (opt.expiry - date.today()).days
            matched_alert = None
            # Aufsteigend iterieren: kleinster Threshold = strengster Alert
            for threshold in sorted(expiry_warning_days):
                if dte <= threshold:
                    if threshold <= 1:
                        alert_type = "expiry_critical"
                        action = "Sofort handeln — Verfall morgen!"
                    elif threshold <= 3:
                        alert_type = "expiry_urgent"
                        action = "Roll oder Schliessen planen"
                    else:
                        alert_type = "expiry_warning"
                        action = "Verfall beobachten, Roll vorbereiten"
                    matched_alert = OptionAlert(
                        position=opt,
                        alert_type=alert_type,
                        message=f"{opt.ticker}: {dte} Tage bis Verfall ({opt.expiry})",
                        action_suggested=action,
                    )
                    break  # Strengster Match gefunden
            if matched_alert:
                alerts.append(matched_alert)

    return options, alerts
