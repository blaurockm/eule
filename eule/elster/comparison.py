"""
Live-Performance vs. Baseline-Vergleich.
"""

from dataclasses import dataclass
from enum import Enum

from eule.elster.metrics import PerformanceMetrics


class Status(Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    NO_DATA = "—"


@dataclass
class ComparisonResult:
    metric: str
    live_value: float | None
    expected: float | None
    warn_threshold: float | None
    status: Status

    @property
    def delta_str(self) -> str:
        if self.live_value is None or self.expected is None:
            return ""
        delta = self.live_value - self.expected
        return f"{delta:+.1f}" if abs(delta) < 100 else f"{delta:+,.0f}"


def compare_to_baseline(
    metrics: PerformanceMetrics,
    daily_pnl_net: "pd.Series | None",
    baseline: dict,
) -> list[ComparisonResult]:
    """
    Vergleicht Live-Metriken gegen Monitoring-Baseline-Erwartungen.

    Args:
        metrics: Berechnete PerformanceMetrics
        daily_pnl_net: Series von taeglichem pnl_net (fuer max_daily_loss, consecutive losses)
        baseline: Geladenes Baseline-YAML dict

    Returns:
        Liste von ComparisonResult
    """
    results: list[ComparisonResult] = []
    bl_metrics = baseline.get("metrics", {})

    # Win Rate
    wr = bl_metrics.get("win_rate", {})
    if wr:
        expected = wr.get("expected")
        warn = wr.get("warn_below")
        live = metrics.win_rate
        status = Status.OK
        if warn is not None and live < warn:
            status = Status.WARNING
        results.append(
            ComparisonResult(
                metric="Win Rate",
                live_value=live,
                expected=expected,
                warn_threshold=warn,
                status=status,
            )
        )

    # Max Daily Loss
    mdl = bl_metrics.get("max_daily_loss", {})
    if mdl and daily_pnl_net is not None and len(daily_pnl_net) > 0:
        warn = mdl.get("warn_below")
        worst_day = float(daily_pnl_net.min())
        status = Status.OK
        if warn is not None and worst_day < warn:
            status = Status.WARNING
        results.append(
            ComparisonResult(
                metric="Max Daily Loss",
                live_value=worst_day,
                expected=None,
                warn_threshold=warn,
                status=status,
            )
        )

    # Trade Frequency
    tf = bl_metrics.get("trade_frequency", {})
    if tf and daily_pnl_net is not None and len(daily_pnl_net) > 0:
        expected_per_week = tf.get("expected_per_week")
        # Approximiere: Anzahl Tage mit Trades / Wochen
        trading_days = len(daily_pnl_net[daily_pnl_net != 0])
        total_weeks = max(1, len(daily_pnl_net) / 5)
        actual_per_week = trading_days / total_weeks
        results.append(
            ComparisonResult(
                metric="Trades/Woche",
                live_value=round(actual_per_week, 1),
                expected=expected_per_week,
                warn_threshold=None,
                status=Status.OK,
            )
        )

    # Max Consecutive Losses
    mcl = bl_metrics.get("max_consecutive_losses", {})
    if mcl and daily_pnl_net is not None and len(daily_pnl_net) > 0:
        warn = mcl.get("warn_threshold")
        max_consec = _max_consecutive_losses(daily_pnl_net)
        status = Status.OK
        if warn is not None and max_consec >= warn:
            status = Status.WARNING
        results.append(
            ComparisonResult(
                metric="Max Consec. Loss",
                live_value=max_consec,
                expected=None,
                warn_threshold=warn,
                status=status,
            )
        )

    # Metriken ohne Baseline-Pendant (nur anzeigen)
    results.append(
        ComparisonResult(
            metric="Sharpe (ann.)",
            live_value=round(metrics.sharpe_ratio, 2),
            expected=None,
            warn_threshold=None,
            status=Status.NO_DATA,
        )
    )
    results.append(
        ComparisonResult(
            metric="Max Drawdown",
            live_value=round(metrics.max_drawdown * 100, 1),
            expected=None,
            warn_threshold=None,
            status=Status.NO_DATA,
        )
    )

    return results


def _max_consecutive_losses(pnl_series: "pd.Series") -> int:
    """Berechnet die laengste Serie negativer Tage."""
    max_consec = 0
    current = 0
    for val in pnl_series:
        if val < 0:
            current += 1
            max_consec = max(max_consec, current)
        else:
            current = 0
    return max_consec
