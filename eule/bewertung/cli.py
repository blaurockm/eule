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


def pnl_override(
    env: str = typer.Option(..., "--env", help="Hase-Environment"),
    strategy: str = typer.Option(..., "--strategy", help="Strategie-Key"),
    date: str = typer.Option(..., "--date", help="Datum (YYYY-MM-DD)"),
    pnl_net: float = typer.Option(None, "--pnl-net", help="Korrigierter PnL-Netto-Wert"),
    pnl_realized: float = typer.Option(None, "--pnl-realized", help="Korrigierter realisierter PnL"),
    fees: float = typer.Option(None, "--fees", help="Korrigierte Gebuehren"),
    show: bool = typer.Option(False, "--show", help="Nur anzeigen, nicht aendern"),
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """PnL-Eintrag in daily_pnl korrigieren (z.B. nach manuellem Close)."""
    from datetime import date as date_cls

    from eule.db import get_env_info, RUNTIME_NAMES

    try:
        target_date = date_cls.fromisoformat(date)
    except ValueError:
        console.print(f"[red]Ungueltiges Datum:[/red] {date}")
        raise typer.Exit(1)

    try:
        conn, runtime_name = get_env_info(env)
    except (ValueError, RuntimeError) as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    try:
        # Aktuellen Eintrag lesen
        cur = conn.cursor()
        cur.execute(
            "SELECT id, pnl_realized, pnl_unrealized, fees, pnl_net, nav_end "
            "FROM daily_pnl "
            "WHERE runtime_name = %s AND strategy_key = %s AND date = %s",
            (runtime_name, strategy, target_date),
        )
        row = cur.fetchone()

        if not row:
            console.print(f"[red]Kein Eintrag gefunden[/red] fuer {env}/{strategy} am {date}")
            raise typer.Exit(1)

        row_id, old_realized, old_unrealized, old_fees, old_pnl_net, old_nav_end = row

        if show or (pnl_net is None and pnl_realized is None and fees is None):
            data = {
                "id": row_id, "env": env, "strategy": strategy, "date": date,
                "pnl_realized": old_realized, "pnl_unrealized": old_unrealized,
                "fees": old_fees, "pnl_net": old_pnl_net, "nav_end": old_nav_end,
            }
            if format == "json":
                output_json(data)
            else:
                console.print(f"\n[bold]daily_pnl[/bold] — {env}/{strategy} @ {date}")
                console.print(f"   ID:             {row_id}")
                console.print(f"   PnL Realized:   {old_realized:+,.2f}")
                console.print(f"   PnL Unrealized: {old_unrealized:+,.2f}")
                console.print(f"   Fees:           {old_fees:,.2f}")
                console.print(f"   PnL Net:        {old_pnl_net:+,.2f}")
                console.print(f"   NAV End:        {old_nav_end:,.2f}")
                console.print()
            return

        # Neue Werte berechnen
        new_realized = pnl_realized if pnl_realized is not None else old_realized
        new_fees = fees if fees is not None else old_fees
        if pnl_net is not None:
            new_pnl_net = pnl_net
        else:
            # pnl_net aus Teilwerten ableiten
            new_pnl_net = new_realized + old_unrealized - new_fees

        # nav_end anpassen: Differenz zum alten pnl_net auf nav_end addieren
        pnl_delta = new_pnl_net - old_pnl_net
        new_nav_end = old_nav_end + pnl_delta

        # Update
        cur.execute(
            "UPDATE daily_pnl "
            "SET pnl_realized = %s, fees = %s, pnl_net = %s, nav_end = %s "
            "WHERE id = %s",
            (new_realized, new_fees, new_pnl_net, new_nav_end, row_id),
        )

        # Folgetage: nav_end kaskadierend anpassen
        if pnl_delta != 0:
            cur.execute(
                "UPDATE daily_pnl "
                "SET nav_end = nav_end + %s "
                "WHERE runtime_name = %s AND strategy_key = %s AND date > %s",
                (pnl_delta, runtime_name, strategy, target_date),
            )

        if format == "json":
            output_json({
                "status": "updated", "id": row_id,
                "old_pnl_net": old_pnl_net, "new_pnl_net": new_pnl_net,
                "old_nav_end": old_nav_end, "new_nav_end": new_nav_end,
                "delta": pnl_delta,
            })
        else:
            console.print(f"\n[green]Korrigiert:[/green] {env}/{strategy} @ {date}")
            console.print(f"   PnL Net:  {old_pnl_net:+,.2f} → {new_pnl_net:+,.2f} (Δ {pnl_delta:+,.2f})")
            console.print(f"   NAV End:  {old_nav_end:,.2f} → {new_nav_end:,.2f}")
            if pnl_delta != 0:
                cur.execute(
                    "SELECT count(*) FROM daily_pnl "
                    "WHERE runtime_name = %s AND strategy_key = %s AND date > %s",
                    (runtime_name, strategy, target_date),
                )
                n_cascaded = cur.fetchone()[0]
                if n_cascaded:
                    console.print(f"   NAV-Kaskade: {n_cascaded} Folgetage angepasst")
            console.print()

    finally:
        conn.close()
