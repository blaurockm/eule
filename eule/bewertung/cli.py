"""Bewertung-CLI — Wie laeuft es? Trades, Performance, Vergleiche."""

import typer
from rich.table import Table

from eule.db import get_env_info, list_environments
from eule.output import console, output_json
from eule.bewertung.trades import (
    detect_roundtrips,
    get_open_trades,
    load_trades,
    summarize_roundtrips,
)


def trades(
    env: str = typer.Option(
        "real-ibkr", "--env",
        help="Hase-Environment (real-ibkr, real2-ibkr)",
    ),
    strategy: str | None = typer.Option(None, "--strategy", help="Nur diese Strategie"),
    days: int | None = typer.Option(None, "--days", help="Nur letzte N Tage"),
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
    show_open: bool = typer.Option(False, "--open", help="Zeige offene Positionen (ohne Exit)"),
) -> None:
    """Hase-Trades laden, Roundtrips erkennen und anzeigen."""
    try:
        conn, runtime_name = get_env_info(env)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    try:
        raw_trades = load_trades(conn, runtime_name, strategy_key=strategy, days=days)
    finally:
        conn.close()

    if not raw_trades:
        if format == "json":
            output_json({"trades": [], "roundtrips": [], "summary": {}, "open": []})
        else:
            console.print("[yellow]Keine Trades gefunden.[/yellow]")
        raise typer.Exit(0)

    roundtrips = detect_roundtrips(raw_trades)
    open_positions = get_open_trades(raw_trades)
    summary = summarize_roundtrips(roundtrips)

    if format == "json":
        data = {
            "env": env,
            "runtime_name": runtime_name,
            "trades_count": len(raw_trades),
            "roundtrips": [r.to_dict() for r in roundtrips],
            "summary": summary,
            "open": [t.to_dict() for t in open_positions],
        }
        output_json(data)
        return

    console.print(f"\n🦉 [bold]Eule — Trades[/bold] ({env} / {runtime_name})")
    console.print(f"   {len(raw_trades)} Trades geladen\n")

    if roundtrips:
        table = Table(title=f"Roundtrips ({len(roundtrips)})", show_lines=False, pad_edge=False)
        table.add_column("Strategie", style="cyan", no_wrap=True)
        table.add_column("Entry", style="green", no_wrap=True)
        table.add_column("Exit", style="red", no_wrap=True)
        table.add_column("Tage", justify="right")
        table.add_column("Entry $", justify="right", no_wrap=True)
        table.add_column("P&L $", justify="right", no_wrap=True)
        table.add_column("%", justify="right", no_wrap=True)
        table.add_column("Typ", style="dim")

        for r in roundtrips:
            pnl_style = "green" if r.pnl >= 0 else "red"
            exit_type = "EXP" if r.exit_is_expiry else "CLS"
            table.add_row(
                r.strategy_key,
                r.entry_date.strftime("%d.%m."),
                r.exit_date.strftime("%d.%m."),
                str(r.holding_days),
                f"{r.entry_value:.2f}",
                f"[{pnl_style}]{r.pnl:+.2f}[/{pnl_style}]",
                f"[{pnl_style}]{r.pnl_percent:+.1f}%[/{pnl_style}]",
                exit_type,
            )

        console.print(table)

    if summary["count"] > 0:
        console.print(f"\n📊 [bold]Summary[/bold]")
        console.print(f"   Roundtrips: {summary['count']} ({summary['winners']}W / {summary['losers']}L)")
        console.print(f"   Win Rate: {summary['win_rate']}%")
        console.print(f"   Total P&L: ${summary['total_pnl']:.2f}")
        console.print(f"   Avg P&L: ${summary['avg_pnl']:.2f}")
        console.print(f"   Avg Holding: {summary['avg_holding_days']} Tage")
        console.print(f"   Expired: {summary['expired_count']}")

    if show_open and open_positions:
        console.print(f"\n⏳ [bold]Offene Positionen ({len(open_positions)})[/bold]")
        open_table = Table(show_lines=False, pad_edge=False)
        open_table.add_column("Strategie", style="cyan", no_wrap=True)
        open_table.add_column("Entry", style="green", no_wrap=True)
        open_table.add_column("Qty", justify="right")
        open_table.add_column("Price", justify="right", no_wrap=True)
        open_table.add_column("Value", justify="right", no_wrap=True)

        for t in open_positions:
            open_table.add_row(
                t.strategy_key,
                t.date.strftime("%d.%m."),
                f"{t.qty:.0f}",
                f"{t.price:.4f}",
                f"{t.value:.2f}",
            )

        console.print(open_table)
    elif show_open:
        console.print("\n[green]Keine offenen Positionen.[/green]")

    console.print()


def list_envs(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Verfuegbare Hase-Environments anzeigen."""
    envs = list_environments()
    if format == "json":
        output_json({"environments": envs})
    else:
        console.print("\n🦉 [bold]Verfuegbare Environments[/bold]")
        for e in envs:
            console.print(f"   • {e}")
        console.print()


def report(
    env: str = typer.Option("real-ibkr", "--env", help="Hase-Environment"),
    strategy: str = typer.Option(None, "--strategy", help="Einzelne Strategie"),
    days: int = typer.Option(None, "--days", help="Letzte N Tage"),
    regimes: bool = typer.Option(False, "--regimes", help="Regime-Vergleich"),
) -> None:
    """Performance-Report fuer ein Environment."""
    from eule.elster.cli import report as _report
    _report(env=env, strategy=strategy, days=days, regimes=regimes)


def compare(
    env: str = typer.Option("real-ibkr", "--env", help="Hase-Environment"),
    strategy: str = typer.Option(None, "--strategy", help="Einzelne Strategie"),
    days: int = typer.Option(None, "--days", help="Letzte N Tage"),
) -> None:
    """Live-Performance gegen Baseline vergleichen."""
    from eule.elster.cli import compare as _compare
    _compare(env=env, strategy=strategy, days=days)


def portfolio_report(
    env: str = typer.Option("real-ibkr", "--env", help="Hase-Environment"),
    days: int = typer.Option(60, "--days", help="Letzte N Tage"),
) -> None:
    """Portfolio-Analyse mit Korrelationsmatrix."""
    from eule.elster.cli import portfolio as _portfolio
    _portfolio(env=env, days=days)
