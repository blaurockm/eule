"""
Rich-Tabellen-Formatierung fuer Elster CLI Output.
"""

import pandas as pd
from rich.console import Console
from rich.table import Table

from eule.elster.comparison import ComparisonResult, Status
from eule.elster.data import Regime
from eule.elster.metrics import PerformanceMetrics
from eule.elster.regimes import config_diff

console = Console()


def print_report_table(
    env_name: str,
    strategy_metrics: dict[str, PerformanceMetrics],
    portfolio_metrics: PerformanceMetrics | None,
    trade_counts: dict[str, int],
    period_label: str,
    warnings: list[str] | None = None,
) -> None:
    """Zeigt die Haupt-Report-Tabelle."""
    table = Table(title=f"Performance Report: {env_name} ({period_label})")

    table.add_column("Strategy", style="cyan")
    table.add_column("Return", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("Sortino", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("Win Rate", justify="right")
    table.add_column("PF", justify="right")
    table.add_column("Trades", justify="right")

    for name, m in strategy_metrics.items():
        table.add_row(
            name,
            _fmt_pct(m.total_return),
            _fmt_ratio(m.sharpe_ratio),
            _fmt_ratio(m.sortino_ratio),
            _fmt_pct(m.max_drawdown),
            _fmt_pct(m.win_rate, warn_below=0.5),
            _fmt_ratio(m.profit_factor, warn_below=1.0),
            str(trade_counts.get(name, 0)),
        )

    if portfolio_metrics and len(strategy_metrics) > 1:
        table.add_section()
        total_trades = sum(trade_counts.values())
        table.add_row(
            "PORTFOLIO",
            _fmt_pct(portfolio_metrics.total_return),
            _fmt_ratio(portfolio_metrics.sharpe_ratio),
            _fmt_ratio(portfolio_metrics.sortino_ratio),
            _fmt_pct(portfolio_metrics.max_drawdown),
            "",
            "",
            str(total_trades),
            style="bold",
        )

    console.print(table)

    if warnings:
        for w in warnings:
            console.print(f"[yellow]  {w}[/yellow]")


def print_comparison_table(
    strategy_name: str,
    results: list[ComparisonResult],
    baseline_source: str,
) -> None:
    """Zeigt den Live-vs-Baseline-Vergleich."""
    table = Table(title=f"Live vs. Baseline: {strategy_name}")

    table.add_column("Metrik", style="cyan")
    table.add_column("Live", justify="right")
    table.add_column("Baseline", justify="right")
    table.add_column("Status", justify="center")

    for r in results:
        live_str = _fmt_comparison_value(r.metric, r.live_value)
        baseline_str = _fmt_comparison_baseline(r)
        status_str = _fmt_status(r.status)
        table.add_row(r.metric, live_str, baseline_str, status_str)

    console.print(table)
    console.print(f"  [dim]Quelle: {baseline_source}[/dim]")


def print_regime_comparison(
    strategy_name: str,
    env_name: str,
    regimes: list[tuple[Regime, PerformanceMetrics | None]],
) -> None:
    """Zeigt Regime-Vergleich mit Config-Diffs."""
    console.print(f"\n[bold]Regime-Vergleich: {strategy_name} ({env_name})[/bold]\n")

    prev_config: dict | None = None
    for i, (regime, metrics) in enumerate(regimes, 1):
        console.print(f"[cyan]Regime {i}: {regime.label}[/cyan]")

        # Config-Diff anzeigen
        if prev_config is not None:
            diff = config_diff(prev_config, regime.config_snapshot)
            if diff:
                changes = ", ".join(f"{k}: {v[0]} -> {v[1]}" for k, v in diff.items())
                console.print(f"  Config: {changes}")
            else:
                console.print("  Config: unveraendert")
        elif regime.config_snapshot:
            # Erste Regime: Key-Parameter anzeigen
            params = _extract_key_params(regime.config_snapshot)
            if params:
                console.print(f"  Config: {params}")

        if metrics is None or regime.days < 2:
            console.print("  [dim]Zu wenig Daten fuer Metriken[/dim]")
        else:
            console.print(
                f"  Return: {_fmt_pct(metrics.total_return)}  "
                f"Sharpe: {_fmt_ratio(metrics.sharpe_ratio)}  "
                f"MaxDD: {_fmt_pct(metrics.max_drawdown)}  "
                f"WinRate: {_fmt_pct(metrics.win_rate)}"
            )

        prev_config = regime.config_snapshot
        console.print()

    # Vergleichs-Warnung
    if len(regimes) >= 2:
        curr = regimes[-1]
        prev = regimes[-2]
        if curr[1] and prev[1] and curr[0].days >= 10 and prev[0].days >= 10:
            sharpe_delta = curr[1].sharpe_ratio - prev[1].sharpe_ratio
            if sharpe_delta < -0.3:
                console.print(f"[yellow]  Aktuelles Regime underperformt (Sharpe {sharpe_delta:+.2f})[/yellow]")


def print_portfolio_analysis(
    env_name: str,
    returns_df: pd.DataFrame,
    portfolio_metrics: PerformanceMetrics,
    period_label: str,
) -> None:
    """Zeigt Portfolio-Analyse mit Korrelationsmatrix."""
    console.print(f"\n[bold]Portfolio Analysis: {env_name} ({period_label})[/bold]\n")

    # Equity-Sparkline
    if not returns_df.empty:
        portfolio_returns = returns_df.sum(axis=1)
        equity = (1 + portfolio_returns).cumprod()
        sparkline = _sparkline(equity.values)
        total_ret = _fmt_pct(portfolio_metrics.total_return)
        console.print(f"  Equity:  {sparkline}  {total_ret}")

        # Max Drawdown Sparkline
        running_max = equity.cummax()
        dd = (equity - running_max) / running_max
        dd_sparkline = _sparkline(dd.values, invert=True)
        max_dd = _fmt_pct(portfolio_metrics.max_drawdown)
        console.print(f"  MaxDD:   {dd_sparkline}  {max_dd}")

    # Korrelationsmatrix
    if returns_df.shape[1] > 1:
        console.print(f"\n  [bold]Strategie-Korrelation (taegl. Returns):[/bold]")
        corr = returns_df.corr()

        # Short names
        names = {col: col[:12] for col in corr.columns}
        header = "  " + " " * 14 + "  ".join(f"{names[c]:>10}" for c in corr.columns)
        console.print(f"[dim]{header}[/dim]")

        high_corr_pairs = []
        for row_name in corr.index:
            vals = []
            for col_name in corr.columns:
                v = corr.loc[row_name, col_name]
                if row_name == col_name:
                    vals.append(f"{'—':>10}")
                elif abs(v) > 0.6 and row_name < col_name:
                    high_corr_pairs.append((row_name, col_name, v))
                    vals.append(f"[red]{v:>10.2f}[/red]")
                else:
                    vals.append(f"{v:>10.2f}")
            console.print(f"  {names[row_name]:<14}{'  '.join(vals)}")

        for a, b, v in high_corr_pairs:
            console.print(f"\n  [yellow]{a} / {b}: Korrelation {v:.2f} — hohes gleichlaufendes Risiko[/yellow]")


# --- Formatting helpers ---


def _fmt_pct(value: float, warn_below: float | None = None) -> str:
    """Formatiert Prozentwert mit Farbe."""
    pct = value * 100 if abs(value) < 1 else value  # schon in % wenn > 1
    if abs(value) <= 1:
        pct = value * 100
    else:
        pct = value
    text = f"{pct:+.1f}%"
    if warn_below is not None and value < warn_below:
        return f"[yellow]{text}[/yellow]"
    if pct < 0:
        return f"[red]{text}[/red]"
    return f"[green]{text}[/green]"


def _fmt_ratio(value: float, warn_below: float | None = None) -> str:
    """Formatiert Ratio (Sharpe, PF, etc.)."""
    text = f"{value:.2f}"
    if warn_below is not None and value < warn_below:
        return f"[yellow]{text}[/yellow]"
    if value < 0:
        return f"[red]{text}[/red]"
    return text


def _fmt_status(status: Status) -> str:
    if status == Status.OK:
        return "[green]OK[/green]"
    if status == Status.WARNING:
        return "[yellow]WARN[/yellow]"
    if status == Status.CRITICAL:
        return "[red]CRIT[/red]"
    return "[dim]—[/dim]"


def _fmt_comparison_value(metric: str, value: float | None) -> str:
    if value is None:
        return "—"
    if "Rate" in metric:
        return f"{value * 100:.1f}%"
    if "Loss" in metric:
        return f"${value:,.0f}"
    if "Drawdown" in metric:
        return f"{value:.1f}%"
    return f"{value}"


def _fmt_comparison_baseline(r: ComparisonResult) -> str:
    parts = []
    if r.expected is not None:
        if "Rate" in r.metric:
            parts.append(f"{r.expected * 100:.1f}%")
        else:
            parts.append(f"{r.expected}")
    if r.warn_threshold is not None:
        if "Loss" in r.metric:
            parts.append(f"warn: ${r.warn_threshold:,.0f}")
        elif "Rate" in r.metric:
            parts.append(f"warn: {r.warn_threshold * 100:.0f}%")
        else:
            parts.append(f"warn: {r.warn_threshold}")
    return " ".join(parts) if parts else "—"


def _extract_key_params(config: dict) -> str:
    """Extrahiert interessante Parameter aus Config-Snapshot."""
    skip = {"strategy", "universe", "reference_data", "strategy_name"}
    params = {k: v for k, v in config.items() if k not in skip and not k.startswith("_")}
    if not params:
        return ""
    return ", ".join(f"{k}={v}" for k, v in list(params.items())[:5])


def _sparkline(values, invert: bool = False) -> str:
    """Erzeugt ASCII-Sparkline aus Werten."""
    blocks = " ▁▂▃▄▅▆▇█"
    if len(values) == 0:
        return ""

    # Auf ~50 Zeichen reduzieren
    target_len = 50
    if len(values) > target_len:
        step = len(values) / target_len
        values = [values[int(i * step)] for i in range(target_len)]

    mn, mx = min(values), max(values)
    if mn == mx:
        return blocks[4] * len(values)

    result = []
    for v in values:
        normalized = (v - mn) / (mx - mn)
        if invert:
            normalized = 1 - normalized
        idx = int(normalized * (len(blocks) - 1))
        result.append(blocks[idx])
    return "".join(result)
