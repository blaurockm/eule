"""Pipeline-CLI — Was kommt als naechstes? EP Scanner, Trades, Pipeline-Management."""

import typer
from rich.table import Table

from eule.output import console, output_json

ep_app = typer.Typer(name="ep", help="Episodic Pivot Scanner + Trades")


@ep_app.command(name="scan")
def ep_scan(
    days: int = typer.Option(1, "--days", help="Emails der letzten N Tage"),
    mark_read: bool = typer.Option(False, "--mark-read", help="Emails als gelesen markieren"),
    min_gap: float = typer.Option(8.0, "--min-gap", help="Minimaler Gap in %"),
    output_format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Barchart-Screener-Emails fetchen, parsen und auto-scoren."""
    from eule.ep.scanner import scan

    candidates = scan(days=days, mark_read=mark_read, min_gap=min_gap)

    if output_format == "json":
        output_json([{
            "symbol": c.symbol, "pct_change": c.pct_change, "volume": c.volume,
            "close_position": round(c.close_position, 2), "auto_score": c.auto_score,
            "screener_type": c.screener_type, "score_details": c.score_details,
        } for c in candidates])
        return

    if not candidates:
        console.print("[yellow]Keine EP-Kandidaten gefunden.[/yellow]")
        return

    table = Table(title=f"EP Scanner ({len(candidates)} Kandidaten)")
    table.add_column("Symbol", style="bold")
    table.add_column("Gap%", justify="right")
    table.add_column("Close Pos", justify="right")
    table.add_column("Volume", justify="right")
    table.add_column("Auto-Score", justify="center")
    table.add_column("Typ", style="dim")

    for c in candidates:
        gap_style = "green" if c.gap_ok else "yellow"
        close_style = "green" if c.close_ok else "yellow"
        table.add_row(
            c.symbol,
            f"[{gap_style}]{c.pct_change:+.1f}%[/{gap_style}]",
            f"[{close_style}]{c.close_position:.2f}[/{close_style}]",
            f"{c.volume:,}",
            f"{c.auto_score}/2",
            c.screener_type,
        )

    console.print(table)
    console.print("\n[dim]Auto-Score: 2 von 10 Kriterien (Gap >=10%, Close >=0.75). "
                  "Fuer vollstaendiges Scoring: Claude fragen.[/dim]")


@ep_app.command(name="trades")
def ep_trades(
    output_format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """EP-Trades und Watchlist anzeigen."""
    from eule.ep.trades import get_active_trades, get_watchlist, _get_filled_data

    active = get_active_trades()
    watch = get_watchlist()

    if output_format == "json":
        active_out = []
        for t in active:
            shares, price = _get_filled_data(t.id)
            active_out.append({
                "id": t.id, "ticker": t.ticker, "status": t.status,
                "filled_price": price, "filled_shares": shares,
                "stop": t.stop_plan, "setup_type": t.setup_type,
            })
        output_json({
            "active": active_out,
            "watchlist": [{
                "id": t.id, "ticker": t.ticker, "entry": t.entry_plan,
                "stop": t.stop_plan, "shares": t.planned_shares,
                "risk": round(t.risk_total, 2), "setup_type": t.setup_type,
            } for t in watch],
        })
        return

    if active:
        table = Table(title=f"Offene EP-Positionen ({len(active)})")
        table.add_column("ID", style="dim")
        table.add_column("Ticker", style="bold")
        table.add_column("Status")
        table.add_column("Entry", justify="right")
        table.add_column("Stop", justify="right")
        table.add_column("Shares", justify="right")
        table.add_column("Setup", style="dim")

        for t in active:
            shares, price = _get_filled_data(t.id)
            table.add_row(
                t.id, t.ticker, t.status,
                f"${price:.2f}", f"${t.stop_plan:.2f}",
                str(shares), t.setup_type,
            )
        console.print(table)
    else:
        console.print("[dim]Keine offenen EP-Positionen.[/dim]")

    if watch:
        console.print()
        table = Table(title=f"EP Watchlist ({len(watch)})")
        table.add_column("ID", style="dim")
        table.add_column("Ticker", style="bold")
        table.add_column("Setup")
        table.add_column("Entry", justify="right")
        table.add_column("Stop", justify="right")
        table.add_column("Shares", justify="right")
        table.add_column("Risiko", justify="right")

        for t in watch:
            table.add_row(
                t.id, t.ticker, t.setup_type,
                f"${t.entry_plan:.2f}", f"${t.stop_plan:.2f}",
                str(t.planned_shares), f"${t.risk_total:.0f}",
            )
        console.print(table)


@ep_app.command(name="brief")
def ep_brief(
    send_email: bool = typer.Option(False, "--email", help="Brief per Email senden"),
    output_format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Pre-Market Morning Brief."""
    from eule.ep.trades import morning_brief, get_active_trades, get_watchlist

    if output_format == "json":
        active = get_active_trades()
        watch = get_watchlist()
        output_json({
            "active": [{"id": t.id, "ticker": t.ticker, "stop": t.stop_plan,
                        "setup_type": t.setup_type} for t in active],
            "watchlist": [{"id": t.id, "ticker": t.ticker, "entry": t.entry_plan,
                           "stop": t.stop_plan, "shares": t.planned_shares} for t in watch],
        })
        return

    brief_text = morning_brief()

    if send_email:
        from datetime import date
        from eule.pipeline.email import send_email as _send
        subject = f"EP Morning Brief — {date.today().isoformat()}"
        _send(subject=subject, body=brief_text)
        console.print(f"[green]Email gesendet.[/green]")
    else:
        console.print(brief_text)


@ep_app.command(name="add")
def ep_add(
    ticker: str = typer.Argument(help="Ticker-Symbol"),
    entry: float = typer.Option(..., "--entry", help="Geplanter Entry-Preis"),
    stop: float = typer.Option(..., "--stop", help="Stop-Loss"),
    catalyst: str = typer.Option("", "--catalyst", help="Catalyst/Grund"),
    setup_type: str = typer.Option("ep-swing", "--setup", help="Setup-Typ"),
    shares: int = typer.Option(0, "--shares", help="Geplante Shares (0 = auto aus Risk)"),
    risk: float = typer.Option(500, "--risk", help="Max Risiko in USD"),
    broker: str = typer.Option("IBKR/real-ibkr", "--broker", help="Broker-Account"),
    output_format: str = typer.Option("markdown", "--format", help="Output-Format"),
) -> None:
    """Neuen EP-Pipeline-Eintrag erstellen."""
    from datetime import date as date_cls
    from eule.db import get_db_connection
    from eule.ep.db import EPPipeline, upsert_pipeline

    risk_per_share = abs(entry - stop)
    if risk_per_share == 0:
        console.print("[red]Entry und Stop duerfen nicht gleich sein.[/red]")
        raise typer.Exit(1)

    if shares == 0:
        shares = int(risk / risk_per_share)

    today = date_cls.today()
    pipeline_id = f"ep-{today.isoformat()}-{ticker.lower()}"

    ep = EPPipeline(
        id=pipeline_id,
        ticker=ticker.upper(),
        status="watch",
        setup_type=setup_type,
        catalyst=catalyst,
        entry_plan=entry,
        stop_plan=stop,
        risk_per_share=risk_per_share,
        planned_shares=shares,
        target_r1=round(entry + risk_per_share, 2),
        target_r2=round(entry + 2 * risk_per_share, 2),
        target_r3=round(entry + 3 * risk_per_share, 2),
        broker_account=broker,
    )

    conn = get_db_connection("real-ibkr")
    try:
        upsert_pipeline(conn, ep)
    finally:
        conn.close()

    if output_format == "json":
        output_json({"id": pipeline_id, "ticker": ep.ticker, "status": ep.status,
                       "entry": ep.entry_plan, "stop": ep.stop_plan, "shares": ep.planned_shares})
    else:
        console.print(f"[green]Pipeline-Eintrag erstellt:[/green] {pipeline_id}")
        console.print(f"  {ep.ticker} | Entry: ${ep.entry_plan:.2f} | Stop: ${ep.stop_plan:.2f} | "
                       f"Shares: {ep.planned_shares} | Risk: ${ep.risk_total:.0f}")


@ep_app.command(name="fill")
def ep_fill(
    pipeline_id: str = typer.Argument(help="Pipeline-ID"),
    price: float = typer.Option(..., "--price", help="Fill-Preis"),
    shares: int = typer.Option(..., "--shares", help="Anzahl Shares"),
    fill_date: str = typer.Option("", "--date", help="Fill-Datum (YYYY-MM-DD, default: heute)"),
    broker: str = typer.Option("IBKR", "--broker", help="Broker"),
    output_format: str = typer.Option("markdown", "--format", help="Output-Format"),
) -> None:
    """Fill fuer EP-Trade erfassen (schreibt in trades-Tabelle)."""
    from datetime import date as date_cls
    from eule.db import get_db_connection
    from eule.ep.db import record_fill, get_pipeline

    d = date_cls.fromisoformat(fill_date) if fill_date else date_cls.today()

    conn = get_db_connection("real-ibkr")
    try:
        record_fill(conn, pipeline_id, d, price, shares, broker=broker)
        entry = get_pipeline(conn, pipeline_id)
    finally:
        conn.close()

    if output_format == "json":
        output_json({"id": pipeline_id, "fill_price": price, "fill_shares": shares,
                       "fill_date": d.isoformat(), "status": entry.status if entry else "?"})
    else:
        console.print(f"[green]Fill erfasst:[/green] {pipeline_id}")
        console.print(f"  {shares}x ${price:.2f} am {d.isoformat()}")
        if entry:
            console.print(f"  Status: {entry.status}")


@ep_app.command(name="close")
def ep_close(
    pipeline_id: str = typer.Argument(help="Pipeline-ID"),
    price: float = typer.Option(..., "--price", help="Exit-Preis"),
    shares: int = typer.Option(..., "--shares", help="Anzahl Shares"),
    reason: str = typer.Option("", "--reason", help="Grund fuer Exit"),
    close_date: str = typer.Option("", "--date", help="Exit-Datum (YYYY-MM-DD, default: heute)"),
    broker: str = typer.Option("IBKR", "--broker", help="Broker"),
    output_format: str = typer.Option("markdown", "--format", help="Output-Format"),
) -> None:
    """EP-Trade schliessen (schreibt Sell-Trade in trades-Tabelle)."""
    from datetime import date as date_cls
    from eule.db import get_db_connection
    from eule.ep.db import close_pipeline, get_pipeline

    d = date_cls.fromisoformat(close_date) if close_date else date_cls.today()

    conn = get_db_connection("real-ibkr")
    try:
        close_pipeline(conn, pipeline_id, d, price, shares, reason=reason, broker=broker)
        entry = get_pipeline(conn, pipeline_id)
    finally:
        conn.close()

    if output_format == "json":
        output_json({"id": pipeline_id, "exit_price": price, "exit_shares": shares,
                       "status": entry.status if entry else "?"})
    else:
        console.print(f"[green]Exit erfasst:[/green] {pipeline_id}")
        console.print(f"  {shares}x ${price:.2f} am {d.isoformat()}")
        if reason:
            console.print(f"  Grund: {reason}")
        if entry:
            console.print(f"  Status: {entry.status}")


@ep_app.command(name="update")
def ep_update(
    pipeline_id: str = typer.Argument(help="Pipeline-ID"),
    status: str = typer.Option(..., "--status", help="Neuer Status (watch/ordered/idea/invalid)"),
    output_format: str = typer.Option("markdown", "--format", help="Output-Format"),
) -> None:
    """Status eines Pipeline-Eintrags aendern."""
    from eule.db import get_db_connection
    from eule.ep.db import update_status

    conn = get_db_connection("real-ibkr")
    try:
        update_status(conn, pipeline_id, status)
    finally:
        conn.close()

    if output_format == "json":
        output_json({"id": pipeline_id, "status": status})
    else:
        console.print(f"[green]Status aktualisiert:[/green] {pipeline_id} → {status}")


@ep_app.command(name="drop")
def ep_drop(
    pipeline_id: str = typer.Argument(help="Pipeline-ID"),
    output_format: str = typer.Option("markdown", "--format", help="Output-Format"),
) -> None:
    """Pipeline-Eintrag als invalid markieren."""
    from eule.db import get_db_connection
    from eule.ep.db import update_status

    conn = get_db_connection("real-ibkr")
    try:
        update_status(conn, pipeline_id, "invalid")
    finally:
        conn.close()

    if output_format == "json":
        output_json({"id": pipeline_id, "status": "invalid"})
    else:
        console.print(f"[yellow]Markiert als invalid:[/yellow] {pipeline_id}")
