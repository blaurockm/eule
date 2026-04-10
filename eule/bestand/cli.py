"""Bestand-CLI — Was habe ich? Positionen, Optionen, Allokation, Briefing, Thesis."""

import typer
from rich.table import Table

from eule.config import ConfigError, load_config
from eule.output import console, output_json


def positions(
    broker: str | None = typer.Option(None, "--broker", help="Nur diesen Broker anzeigen"),
    asset_type: str | None = typer.Option(None, "--type", help="Asset-Typ filtern (stock, option, bond, etf, gold_physical)"),
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Alle Positionen ueber alle Broker aggregieren und anzeigen."""
    from eule.bestand.aggregator import aggregate_positions

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    snap = aggregate_positions(cfg)

    filtered = snap.positions
    if broker:
        filtered = [p for p in filtered if p.broker == broker]
    if asset_type:
        filtered = [p for p in filtered if p.asset_type == asset_type]

    if format == "json":
        data = snap.to_dict()
        if broker or asset_type:
            data["positions"] = [p.to_dict() for p in filtered]
        output_json(data)
        return

    console.print(f"\n[bold]Eule — Positionen[/bold]")
    console.print(f"   Gesamt: {snap.total_value_eur:,.0f} {cfg.base_currency}")
    for b, total in sorted(snap.broker_totals.items()):
        console.print(f"   {b}: {total:,.0f} {cfg.base_currency}")

    if filtered:
        table = Table(title=f"Positionen ({len(filtered)})", show_lines=False, pad_edge=False)
        table.add_column("Broker", style="cyan", no_wrap=True)
        table.add_column("Ticker", style="bold", no_wrap=True)
        table.add_column("Typ", style="dim")
        table.add_column("Richtung")
        table.add_column("Menge", justify="right")
        table.add_column("Entry", justify="right", no_wrap=True)
        table.add_column("Aktuell", justify="right", no_wrap=True)
        table.add_column("P&L EUR", justify="right", no_wrap=True)
        table.add_column("%Port", justify="right", no_wrap=True)

        for p in filtered:
            pnl_str = ""
            if p.unrealized_pnl_eur is not None:
                pnl_style = "green" if p.unrealized_pnl_eur >= 0 else "red"
                pnl_str = f"[{pnl_style}]{p.unrealized_pnl_eur:+,.0f}[/{pnl_style}]"

            pct_str = f"{p.pct_of_portfolio:.1%}" if p.pct_of_portfolio else ""
            current_str = f"{p.current_price:,.2f}" if p.current_price else "-"

            table.add_row(
                p.broker, p.ticker, p.asset_type, p.direction,
                f"{p.size:,.2f}", f"{p.entry_price:,.2f}", current_str, pnl_str, pct_str,
            )

        console.print(table)
    else:
        console.print("[yellow]Keine Positionen gefunden.[/yellow]")

    if snap.errors:
        console.print(f"\n[yellow]Hinweise:[/yellow]")
        for err in snap.errors:
            console.print(f"   {err}")

    console.print()


def options(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Options-Dashboard: 50%-Regel, DTE-Warnungen, Roll-Vorbereitung."""
    from eule.bestand.aggregator import aggregate_positions
    from eule.bestand.options import analyze_options

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    snap = aggregate_positions(cfg)
    opt_list, alerts = analyze_options(
        snap.positions,
        expiry_warning_days=cfg.alerts.option_expiry_warning_days,
        fifty_pct_rule=cfg.alerts.fifty_pct_rule,
    )

    if format == "json":
        data = {
            "options": [o.to_dict() for o in opt_list],
            "alerts": [
                {"ticker": a.position.ticker, "type": a.alert_type,
                 "message": a.message, "action": a.action_suggested}
                for a in alerts
            ],
            "errors": snap.errors,
        }
        output_json(data)
        return

    console.print(f"\n[bold]Eule — Options Dashboard[/bold]")
    if not opt_list:
        console.print("[yellow]Keine Option-Positionen.[/yellow]\n")
        return

    table = Table(title=f"Optionen ({len(opt_list)})", show_lines=False, pad_edge=False)
    table.add_column("Broker", style="cyan", no_wrap=True)
    table.add_column("Ticker", style="bold", no_wrap=True)
    table.add_column("Typ")
    table.add_column("Richtung")
    table.add_column("Strike", justify="right")
    table.add_column("Verfall", no_wrap=True)
    table.add_column("DTE", justify="right")
    table.add_column("Praemie", justify="right")
    table.add_column("Aktuell", justify="right")
    table.add_column("P&L%", justify="right")

    for o in opt_list:
        dte_style = "green"
        if o.days_to_expiry <= 1:
            dte_style = "red bold"
        elif o.days_to_expiry <= 3:
            dte_style = "red"
        elif o.days_to_expiry <= 7:
            dte_style = "yellow"

        pnl_pct = ""
        if o.sold_premium > 0:
            pct = (o.sold_premium - o.current_value) / o.sold_premium * 100
            pnl_style = "green" if pct >= 0 else "red"
            pnl_pct = f"[{pnl_style}]{pct:+.0f}%[/{pnl_style}]"

        table.add_row(
            o.broker, o.ticker, o.option_type, o.direction,
            f"{o.strike:,.0f}",
            o.expiry.strftime("%d.%m.%Y") if o.expiry else "-",
            f"[{dte_style}]{o.days_to_expiry}[/{dte_style}]",
            f"{o.sold_premium:,.0f}" if o.sold_premium else "-",
            f"{o.current_value:,.0f}" if o.current_value else "-",
            pnl_pct,
        )

    console.print(table)

    if alerts:
        console.print(f"\n[bold]Alerts ({len(alerts)})[/bold]")
        for a in alerts:
            icon = {"fifty_pct": "[green]$[/green]", "expiry_warning": "[yellow]![/yellow]",
                     "expiry_urgent": "[red]!![/red]", "expiry_critical": "[red bold]!!![/red bold]"}
            console.print(f"   {icon.get(a.alert_type, '?')} {a.message}")
            console.print(f"      → {a.action_suggested}")

    if snap.errors:
        console.print(f"\n[yellow]Hinweise:[/yellow]")
        for err in snap.errors:
            console.print(f"   {err}")
    console.print()


def allocation(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Soll vs. Ist Allokation pruefen."""
    from eule.bestand.aggregator import aggregate_positions
    from eule.bestand.allocation import check_allocation

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    snap = aggregate_positions(cfg)
    checks, concentration = check_allocation(snap, cfg.allocation)

    if format == "json":
        data = {
            "total_value_eur": round(snap.total_value_eur, 2),
            "allocation": [
                {"category": c.category, "actual_pct": round(c.actual_pct, 4),
                 "actual_eur": round(c.actual_eur, 2),
                 "target_min": c.target_min, "target_max": c.target_max,
                 "status": c.status, "deviation": round(c.deviation, 4)}
                for c in checks
            ],
            "concentration_alerts": [
                {"ticker": c.ticker, "broker": c.broker,
                 "pct": round(c.pct, 4), "limit": c.limit}
                for c in concentration
            ],
            "errors": snap.errors,
        }
        output_json(data)
        return

    console.print(f"\n[bold]Eule — Allokation[/bold]")
    console.print(f"   Gesamt: {snap.total_value_eur:,.0f} EUR\n")

    table = Table(title="Soll / Ist", show_lines=False, pad_edge=False)
    table.add_column("Kategorie", style="bold")
    table.add_column("Ist %", justify="right")
    table.add_column("Ist EUR", justify="right")
    table.add_column("Soll", justify="right")
    table.add_column("Status", justify="center")

    for c in checks:
        status_style = {"ok": "green", "under": "yellow", "over": "red"}
        status_icon = {"ok": "OK", "under": "UNTER", "over": "UEBER"}
        style = status_style.get(c.status, "dim")
        icon = status_icon.get(c.status, "?")

        table.add_row(
            c.category, f"{c.actual_pct:.1%}", f"{c.actual_eur:,.0f}",
            f"{c.target_min:.0%} – {c.target_max:.0%}",
            f"[{style}]{icon}[/{style}]",
        )

    console.print(table)

    if concentration:
        console.print(f"\n[red]Konzentrations-Warnungen:[/red]")
        for c in concentration:
            console.print(f"   {c.ticker} ({c.broker}): {c.pct:.1%} > Limit {c.limit:.0%}")

    if snap.errors:
        console.print(f"\n[yellow]Hinweise:[/yellow]")
        for err in snap.errors:
            console.print(f"   {err}")
    console.print()


def briefing(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Gesamt-Briefing: Portfolio, Alerts, Allokation."""
    from datetime import date as date_cls

    from eule.bestand.aggregator import aggregate_positions
    from eule.bestand.briefing import create_briefing

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    snap = aggregate_positions(cfg)
    brief = create_briefing(cfg, snap)

    if format == "json":
        data = {
            "date": date_cls.today().isoformat(),
            "portfolio": {
                "total_value_eur": round(snap.total_value_eur, 2),
                "broker_totals": {k: round(v, 2) for k, v in snap.broker_totals.items()},
            },
            "positions": [p.to_dict() for p in snap.positions],
            "option_alerts": [
                {"ticker": a.position.ticker, "type": a.alert_type,
                 "message": a.message, "action": a.action_suggested}
                for a in brief.option_alerts
            ],
            "bond_alerts": [
                {"ticker": a.position.ticker, "type": a.alert_type,
                 "message": a.message, "action": a.action_suggested}
                for a in brief.bond_alerts
            ],
            "allocation": [
                {"category": c.category, "actual_pct": round(c.actual_pct, 4),
                 "status": c.status}
                for c in brief.allocation_checks
            ],
            "concentration_alerts": [
                {"ticker": c.ticker, "pct": round(c.pct, 4)}
                for c in brief.concentration_alerts
            ],
            "errors": brief.errors,
        }
        output_json(data)
        return

    console.print(f"\n[bold]Eule — Briefing ({date_cls.today().strftime('%d.%m.%Y')})[/bold]")
    console.print(f"\n   Gesamt: ~{snap.total_value_eur:,.0f} EUR")
    for b, total in sorted(snap.broker_totals.items()):
        console.print(f"   {b}: {total:,.0f} EUR")

    with_pnl = [(p, p.unrealized_pnl_eur or 0) for p in snap.positions if p.unrealized_pnl_eur is not None]
    if with_pnl:
        winners = sorted(with_pnl, key=lambda x: x[1], reverse=True)[:3]
        losers = sorted(with_pnl, key=lambda x: x[1])[:3]

        if winners and winners[0][1] > 0:
            w_str = ", ".join(f"{p.ticker} {pnl:+,.0f}" for p, pnl in winners if pnl > 0)
            if w_str:
                console.print(f"\n   [green]Gewinner:[/green] {w_str}")
        if losers and losers[0][1] < 0:
            l_str = ", ".join(f"{p.ticker} {pnl:+,.0f}" for p, pnl in losers if pnl < 0)
            if l_str:
                console.print(f"   [red]Verlierer:[/red] {l_str}")

    all_alerts = brief.option_alerts + brief.bond_alerts
    if all_alerts:
        console.print(f"\n   [bold]Aktionen:[/bold]")
        for a in all_alerts:
            console.print(f"   - {a.message}")
            console.print(f"     → {a.action_suggested}")

    problems = [c for c in brief.allocation_checks if c.status != "ok"]
    if problems:
        console.print(f"\n   [bold]Allokation:[/bold]")
        for c in problems:
            console.print(f"   - {c.category}: {c.actual_pct:.1%} (Ziel {c.target_min:.0%}–{c.target_max:.0%}) [{c.status.upper()}]")

    if brief.concentration_alerts:
        console.print(f"\n   [red]Konzentration:[/red]")
        for c in brief.concentration_alerts:
            console.print(f"   - {c.ticker}: {c.pct:.1%} > {c.limit:.0%}")

    if brief.errors:
        console.print(f"\n   [yellow]Hinweise:[/yellow]")
        for err in brief.errors:
            console.print(f"   {err}")

    console.print()


def thesis(
    ticker: str | None = typer.Argument(None, help="Einzelnen Ticker pruefen"),
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Exit-Kriterien aus positions-bh.md pruefen."""
    from eule.bestand.aggregator import aggregate_positions
    from eule.bestand.thesis import check_thesis, parse_thesis_file

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    if not cfg.thesis_file:
        console.print("[red]Fehler:[/red] thesis_file nicht in config.yaml konfiguriert")
        raise typer.Exit(1)

    entries = parse_thesis_file(cfg.thesis_file)
    if not entries:
        console.print("[yellow]Keine Thesis-Eintraege gefunden.[/yellow]")
        raise typer.Exit(0)

    snap = aggregate_positions(cfg)
    checks = check_thesis(entries, snap.positions, ticker_filter=ticker)

    if format == "json":
        data = {
            "entries": [
                {"ticker": e.ticker, "thesis": e.thesis,
                 "exit_criteria": e.exit_criteria}
                for e in entries
                if not ticker or e.ticker == ticker
            ],
            "checks": [
                {"ticker": c.ticker, "criterion": c.criterion,
                 "status": c.status, "detail": c.detail}
                for c in checks
            ],
            "errors": snap.errors,
        }
        output_json(data)
        return

    console.print(f"\n[bold]Eule — Thesis Check[/bold]")

    for entry in entries:
        if ticker and entry.ticker != ticker:
            continue
        console.print(f"\n   [bold]{entry.ticker}[/bold]")
        if entry.thesis:
            console.print(f"   These: {entry.thesis}")

        entry_checks = [c for c in checks if c.ticker == entry.ticker]
        for c in entry_checks:
            status_style = {
                "triggered": "red bold",
                "approaching": "yellow",
                "pending": "dim",
                "not_checkable": "dim italic",
            }
            style = status_style.get(c.status, "dim")
            console.print(f"   [{style}][{c.status.upper()}][/{style}] {c.criterion}")
            if c.detail and c.status in ("triggered", "approaching"):
                console.print(f"            {c.detail}")

    if snap.errors:
        console.print(f"\n[yellow]Hinweise:[/yellow]")
        for err in snap.errors:
            console.print(f"   {err}")
    console.print()
