"""
Elster CLI — Performance-Analyse fuer Live-Trading.

Usage:
    elster report --env real-ibkr
    elster report --env real-ibkr --strategy carver-scalping --regimes
    elster compare --env real-ibkr --strategy spx-0dte-mon-put
    elster portfolio --env real-ibkr --days 60
"""

from typing import Optional

import typer
from loguru import logger
from rich.console import Console

from eule.elster.comparison import compare_to_baseline
from eule.db import get_db_connection
from eule.elster.data import (
    get_runtime_name,
    list_strategies,
    load_baseline,
    load_daily_pnl,
    load_runs_with_configs,
    load_trades,
    nav_to_returns,
)
from eule.elster.metrics import PerformanceMetrics, calculate_metrics
from eule.elster.regimes import count_regime_changes_in_range, detect_regimes
from eule.elster.report import (
    print_comparison_table,
    print_portfolio_analysis,
    print_regime_comparison,
    print_report_table,
)

app = typer.Typer(help="Elster — Performance-Analyse fuer Live-Trading")
console = Console()

# Suppress loguru output for CLI
logger.remove()


@app.command()
def report(
    env: str = typer.Option(..., "--env", help="Environment (z.B. real-ibkr, staging-ibkr)"),
    strategy: Optional[str] = typer.Option(None, "--strategy", "-s", help="Einzelne Strategie"),
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Zeitfenster in Tagen"),
    regimes: bool = typer.Option(False, "--regimes", "-r", help="Regime-Vergleich anzeigen"),
) -> None:
    """Performance-Report fuer ein Environment."""
    try:
        conn = get_db_connection(env)
        runtime_name = get_runtime_name(env)
    except Exception as e:
        console.print(f"[red]Verbindungsfehler: {e}[/red]")
        raise typer.Exit(1)

    strategies = [strategy] if strategy else list_strategies(conn, runtime_name)
    if not strategies:
        console.print(f"[yellow]Keine Strategien gefunden fuer {runtime_name}[/yellow]")
        raise typer.Exit(0)

    # Regime-Modus: pro Strategie Regimes vergleichen
    if regimes and strategy:
        _show_regime_comparison(conn, runtime_name, strategy, env)
        return

    # Zeitfenster bestimmen
    if days:
        # Explizites Zeitfenster — auf Regime-Grenzen pruefen
        runs_df = load_runs_with_configs(conn, runtime_name)
        for strat in strategies:
            strat_regimes = detect_regimes(runs_df, strat)
            import datetime

            n_changes = count_regime_changes_in_range(
                strat_regimes,
                datetime.date.today() - datetime.timedelta(days=days),
                datetime.date.today(),
            )
            if n_changes > 0:
                console.print(
                    f"[yellow]  {strat}: {n_changes} Config-Aenderung(en) "
                    f"im Zeitraum. Nutze --regimes -s {strat} fuer saubere Aufschluesselung.[/yellow]"
                )

        df = load_daily_pnl(conn, runtime_name, days=days, strategy_key=strategy)
        period_label = f"letzte {days} Tage"
    else:
        # Default: aktuelles Regime (seit letzter Config-Aenderung)
        runs_df = load_runs_with_configs(conn, runtime_name)
        regime_start = None

        if not runs_df.empty:
            # Finde aeltestes Regime-Start-Datum ueber alle Strategien
            for strat in strategies:
                strat_regimes = detect_regimes(runs_df, strat)
                if strat_regimes:
                    current = strat_regimes[-1]
                    if regime_start is None or current.start_date < regime_start:
                        regime_start = current.start_date

        if regime_start:
            import datetime

            df = load_daily_pnl(
                conn,
                runtime_name,
                start_date=regime_start,
                end_date=datetime.date.today(),
            )
            period_label = f"Regime seit {regime_start.isoformat()}"
        else:
            # Fallback: letzte 30 Tage
            df = load_daily_pnl(conn, runtime_name, days=30)
            period_label = "letzte 30 Tage (kein Regime erkannt)"

    if df.empty:
        console.print(f"[yellow]Keine Daten fuer {runtime_name} im Zeitraum[/yellow]")
        raise typer.Exit(0)

    # Returns berechnen
    returns_df = nav_to_returns(df)
    if returns_df.empty:
        console.print("[yellow]Zu wenig Daten fuer Return-Berechnung (mind. 2 Tage)[/yellow]")
        raise typer.Exit(0)

    # Per-Strategy Metriken
    strategy_metrics: dict[str, PerformanceMetrics] = {}
    trade_counts: dict[str, int] = {}
    warnings: list[str] = []

    for strat in strategies:
        if strat not in returns_df.columns:
            continue
        m = calculate_metrics(returns_df[strat])
        strategy_metrics[strat] = m

        # Trade count
        trades_df = load_trades(conn, runtime_name, days=days, strategy_key=strat)
        trade_counts[strat] = len(trades_df)

        # Baseline-Warnungen
        baseline = load_baseline(strat)
        if baseline:
            bl_wr = baseline.get("metrics", {}).get("win_rate", {})
            if bl_wr and bl_wr.get("warn_below") and m.win_rate < bl_wr["warn_below"]:
                warnings.append(
                    f"{strat}: win_rate {m.win_rate:.0%} < baseline "
                    f"{bl_wr.get('expected', '?'):.0%} (warn: {bl_wr['warn_below']:.0%})"
                )
            if m.profit_factor < 1.0 and m.profit_factor > 0:
                warnings.append(f"{strat}: profit_factor {m.profit_factor:.1f} < 1.0 (verliert Geld)")

    # Portfolio-Metriken
    portfolio_metrics = None
    if len(strategy_metrics) > 1:
        available = [c for c in returns_df.columns if c in strategy_metrics]
        portfolio_returns = returns_df[available].sum(axis=1)
        portfolio_metrics = calculate_metrics(portfolio_returns)

    print_report_table(
        env_name=env,
        strategy_metrics=strategy_metrics,
        portfolio_metrics=portfolio_metrics,
        trade_counts=trade_counts,
        period_label=period_label,
        warnings=warnings,
    )


@app.command()
def compare(
    env: str = typer.Option(..., "--env", help="Environment"),
    strategy: str = typer.Option(..., "--strategy", "-s", help="Strategie"),
    days: int = typer.Option(90, "--days", "-d", help="Zeitfenster in Tagen"),
) -> None:
    """Live-Performance gegen Baseline vergleichen."""
    try:
        conn = get_db_connection(env)
        runtime_name = get_runtime_name(env)
    except Exception as e:
        console.print(f"[red]Verbindungsfehler: {e}[/red]")
        raise typer.Exit(1)

    baseline = load_baseline(strategy)
    if not baseline:
        console.print(f"[yellow]Keine Baseline fuer {strategy}[/yellow]")
        raise typer.Exit(1)

    df = load_daily_pnl(conn, runtime_name, days=days, strategy_key=strategy)
    if df.empty:
        console.print(f"[yellow]Keine Daten fuer {strategy}[/yellow]")
        raise typer.Exit(0)

    returns_df = nav_to_returns(df)
    if returns_df.empty or strategy not in returns_df.columns:
        console.print("[yellow]Zu wenig Daten[/yellow]")
        raise typer.Exit(0)

    metrics = calculate_metrics(returns_df[strategy])

    # pnl_net Series fuer Trade-Stats
    pnl_net = df[df["strategy_key"] == strategy]["pnl_net"]

    results = compare_to_baseline(metrics, pnl_net, baseline)
    baseline_source = f"monitoring/baselines/{strategy}.yaml"
    print_comparison_table(strategy, results, baseline_source)


@app.command()
def portfolio(
    env: str = typer.Option(..., "--env", help="Environment"),
    days: int = typer.Option(60, "--days", "-d", help="Zeitfenster in Tagen"),
) -> None:
    """Portfolio-Analyse mit Korrelationsmatrix."""
    try:
        conn = get_db_connection(env)
        runtime_name = get_runtime_name(env)
    except Exception as e:
        console.print(f"[red]Verbindungsfehler: {e}[/red]")
        raise typer.Exit(1)

    df = load_daily_pnl(conn, runtime_name, days=days)
    if df.empty:
        console.print(f"[yellow]Keine Daten fuer {runtime_name}[/yellow]")
        raise typer.Exit(0)

    returns_df = nav_to_returns(df)
    if returns_df.empty:
        console.print("[yellow]Zu wenig Daten[/yellow]")
        raise typer.Exit(0)

    portfolio_returns = returns_df.sum(axis=1)
    portfolio_metrics = calculate_metrics(portfolio_returns)

    print_portfolio_analysis(
        env_name=env,
        returns_df=returns_df,
        portfolio_metrics=portfolio_metrics,
        period_label=f"letzte {days} Tage",
    )


def _show_regime_comparison(conn, runtime_name: str, strategy: str, env: str) -> None:
    """Zeigt Regime-Vergleich fuer eine Strategie."""
    runs_df = load_runs_with_configs(conn, runtime_name)
    strat_regimes = detect_regimes(runs_df, strategy)

    if not strat_regimes:
        console.print(f"[yellow]Keine Regimes erkannt fuer {strategy}[/yellow]")
        return

    regime_data: list[tuple] = []
    for regime in strat_regimes:
        df = load_daily_pnl(
            conn,
            runtime_name,
            strategy_key=strategy,
            start_date=regime.start_date,
            end_date=regime.end_date,
        )
        metrics = None
        if not df.empty:
            returns_df = nav_to_returns(df)
            if not returns_df.empty and strategy in returns_df.columns:
                metrics = calculate_metrics(returns_df[strategy])

        regime_data.append((regime, metrics))

    print_regime_comparison(strategy, env, regime_data)


if __name__ == "__main__":
    app()
