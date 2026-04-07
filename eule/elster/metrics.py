"""
Performance-Metriken fuer Live-Trading-Analyse.

Kopie von dachs/core/metrics.py — identische Berechnung fuer Live-vs-Backtest-Vergleichbarkeit.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PerformanceMetrics:
    """Container fuer Performance-Metriken."""

    # Returns
    total_return: float
    annualized_return: float
    volatility: float

    # Risk-adjusted
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float

    # Drawdown
    max_drawdown: float
    avg_drawdown: float
    max_drawdown_duration: int  # in Tagen

    # Trade-Statistiken
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float

    # Verteilung
    skewness: float
    kurtosis: float

    def to_dict(self) -> dict[str, float]:
        """Konvertiert zu Dictionary."""
        return {
            "total_return": self.total_return,
            "annualized_return": self.annualized_return,
            "volatility": self.volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "max_drawdown": self.max_drawdown,
            "avg_drawdown": self.avg_drawdown,
            "max_drawdown_duration": self.max_drawdown_duration,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "skewness": self.skewness,
            "kurtosis": self.kurtosis,
        }


def calculate_metrics(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> PerformanceMetrics:
    """
    Berechnet Performance-Metriken aus einer Return-Serie.

    Args:
        returns: Taegliche Returns
        risk_free_rate: Risikofreier Zinssatz (annualisiert)
        periods_per_year: Handelsperioden pro Jahr (252 fuer taeglich)

    Returns:
        PerformanceMetrics Objekt
    """
    returns = returns.dropna()

    if len(returns) < 2:
        return _empty_metrics()

    total_return = float((1 + returns).prod() - 1)
    n_periods = len(returns)
    years = n_periods / periods_per_year

    annualized_return = float((1 + total_return) ** (1 / years) - 1) if years > 0 else 0.0
    volatility = float(returns.std() * np.sqrt(periods_per_year))

    # Risk-Adjusted Returns
    daily_rf = risk_free_rate / periods_per_year
    excess_returns = returns - daily_rf

    if returns.std() > 0:
        sharpe_ratio = float(excess_returns.mean() / returns.std() * np.sqrt(periods_per_year))
    else:
        sharpe_ratio = 0.0

    downside_returns = returns[returns < 0]
    if len(downside_returns) > 0 and downside_returns.std() > 0:
        sortino_ratio = float(excess_returns.mean() / downside_returns.std() * np.sqrt(periods_per_year))
    else:
        sortino_ratio = 0.0

    # Drawdown-Analyse
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max

    max_drawdown = float(drawdown.min())
    avg_drawdown = float(drawdown[drawdown < 0].mean()) if (drawdown < 0).any() else 0.0
    max_drawdown_duration = _calculate_max_dd_duration(drawdown)

    if max_drawdown < 0:
        calmar_ratio = float(annualized_return / abs(max_drawdown))
    else:
        calmar_ratio = 0.0

    # Trade-Statistiken (auf Tagesbasis: positiver Tag = "Win")
    wins = returns[returns > 0]
    losses = returns[returns < 0]

    win_rate = float(len(wins) / len(returns)) if len(returns) > 0 else 0.0
    avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
    avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0

    gross_profit = wins.sum() if len(wins) > 0 else 0.0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.0
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else 0.0

    # Verteilung
    skewness = float(returns.skew())
    kurtosis = float(returns.kurtosis())

    return PerformanceMetrics(
        total_return=total_return,
        annualized_return=annualized_return,
        volatility=volatility,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        max_drawdown=max_drawdown,
        avg_drawdown=avg_drawdown,
        max_drawdown_duration=max_drawdown_duration,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        skewness=skewness,
        kurtosis=kurtosis,
    )


def _calculate_max_dd_duration(drawdown: pd.Series) -> int:
    """Berechnet die laengste Drawdown-Periode in Tagen."""
    in_drawdown = drawdown < 0
    groups = (~in_drawdown).cumsum()
    drawdown_lengths = in_drawdown.groupby(groups).sum()
    return int(drawdown_lengths.max()) if len(drawdown_lengths) > 0 else 0


def _empty_metrics() -> PerformanceMetrics:
    """Gibt leere Metriken zurueck."""
    return PerformanceMetrics(
        total_return=0.0,
        annualized_return=0.0,
        volatility=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        calmar_ratio=0.0,
        max_drawdown=0.0,
        avg_drawdown=0.0,
        max_drawdown_duration=0,
        win_rate=0.0,
        profit_factor=0.0,
        avg_win=0.0,
        avg_loss=0.0,
        skewness=0.0,
        kurtosis=0.0,
    )
