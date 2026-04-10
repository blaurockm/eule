"""Betrieb-CLI — Laeuft alles? Config, Precheck, Bot, Schedule."""

from datetime import datetime

import typer

from eule.config import ConfigError, init_config, load_config
from eule.output import console, output_json

config_app = typer.Typer(name="config", help="Eule-Konfiguration verwalten", no_args_is_help=True)


@config_app.command(name="init")
def config_init() -> None:
    """Config-Templates unter ~/.eule/ erstellen."""
    created = init_config()
    if created:
        console.print("[green]Erstellt:[/green]")
        for path in created:
            console.print(f"   {path}")
    else:
        console.print("[yellow]Alle Dateien existieren bereits.[/yellow]")


@config_app.command(name="show")
def config_show(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Aktuelle Konfiguration anzeigen (Credentials maskiert)."""
    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    if format == "json":
        data = {
            "base_currency": cfg.base_currency,
            "brokers": {
                name: {
                    "enabled": b.enabled,
                    "type": b.broker_type,
                    "env_file": b.env_file or None,
                    "positions_file": b.positions_file or None,
                }
                for name, b in cfg.brokers.items()
            },
            "allocation": {
                "targets": {t.category: {"min": t.min_pct, "max": t.max_pct}
                            for t in cfg.allocation.targets},
                "max_single_position_pct": cfg.allocation.max_single_position_pct,
            },
            "thesis_file": cfg.thesis_file,
        }
        output_json(data)
        return

    console.print("\n[bold]Eule Config[/bold]")
    console.print(f"   Waehrung: {cfg.base_currency}")
    console.print(f"\n[bold]Broker[/bold]")
    for name, b in cfg.brokers.items():
        status = "[green]aktiv[/green]" if b.enabled else "[dim]inaktiv[/dim]"
        console.print(f"   {name} ({b.broker_type}): {status}")
    console.print(f"\n[bold]Allokation[/bold]")
    for t in cfg.allocation.targets:
        console.print(f"   {t.category}: {t.min_pct:.0%} – {t.max_pct:.0%}")
    console.print()


@config_app.command(name="check")
def config_check() -> None:
    """Konfiguration validieren und Broker-Erreichbarkeit pruefen."""
    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    console.print("[bold]Config-Check[/bold]\n")
    ok = True

    for name, b in cfg.brokers.items():
        if not b.enabled:
            console.print(f"   {name}: [dim]uebersprungen (disabled)[/dim]")
            continue

        if b.env_file:
            from pathlib import Path
            env_path = Path(b.env_file).expanduser()
            if env_path.exists():
                console.print(f"   {name}: [green]env_file OK[/green] ({env_path})")
            else:
                console.print(f"   {name}: [red]env_file FEHLT[/red] ({env_path})")
                ok = False

        if b.positions_file:
            from pathlib import Path
            pos_path = Path(b.positions_file).expanduser()
            if pos_path.exists():
                console.print(f"   {name}: [green]positions_file OK[/green] ({pos_path})")
            else:
                console.print(f"   {name}: [red]positions_file FEHLT[/red] ({pos_path})")
                ok = False

    if ok:
        console.print("\n[green]Alle Checks bestanden.[/green]")
    else:
        console.print("\n[red]Einige Checks fehlgeschlagen.[/red]")
        raise typer.Exit(1)


def precheck(
    summary: bool = typer.Option(False, "--summary", help="Daily-Summary erzwingen"),
    format: str = typer.Option("text", "--format", help="Output-Format: text oder json"),
) -> None:
    """Health-Check aller Hase-Environments."""
    from eule.monitoring.precheck import run_precheck

    exit_code, output = run_precheck(force_summary=summary)

    if format == "json":
        output_json({"exit_code": exit_code, "output": output})
    else:
        console.print(output)
    raise typer.Exit(exit_code)


def bot() -> None:
    """Wachtel Telegram Bot starten."""
    from eule.monitoring.telegram_bot import main as bot_main

    bot_main()


# ── Schedule ────────────────────────────────────────

schedule_app = typer.Typer(name="schedule", help="Scheduler verwalten", no_args_is_help=True)


def _format_cron_human(cron: str) -> str:
    """Cron-Ausdruck in lesbaren Text umwandeln (best-effort)."""
    fields = cron.strip().split()
    if len(fields) != 5:
        return cron

    minute, hour, _dom, _month, dow = fields

    # Day-of-week Mapping (0=Mo)
    dow_names = {
        "*": "taeglich",
        "0-4": "Mo-Fr",
        "0": "Mo", "1": "Di", "2": "Mi", "3": "Do", "4": "Fr",
        "5": "Sa", "6": "So",
    }
    dow_str = dow_names.get(dow, f"DoW={dow}")

    return f"{dow_str} {hour}:{minute.zfill(2)}"


@schedule_app.command(name="list")
def schedule_list(
    output_format: str = typer.Option("markdown", "--format", help="Output: markdown oder json"),
) -> None:
    """Alle geplanten Jobs anzeigen."""
    from zoneinfo import ZoneInfo

    from rich.table import Table

    from eule.monitoring.schedule_config import ScheduleConfigError, load_schedule
    from eule.monitoring.scheduler import cron_next_fire, load_state

    try:
        config = load_schedule()
    except ScheduleConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    state = load_state()
    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)

    if output_format == "json":
        data = []
        for name, job in config.jobs.items():
            job_state = state.get(name, {})
            next_fire = None
            if job.cron:
                nf = cron_next_fire(job.cron, now, tz)
                if nf:
                    next_fire = nf.isoformat(timespec="minutes")
            elif job.interval_minutes:
                last_run = job_state.get("last_run")
                if last_run:
                    from datetime import datetime as dt
                    try:
                        lr = dt.fromisoformat(last_run)
                        from datetime import timedelta
                        next_fire = (lr + timedelta(minutes=job.interval_minutes)).isoformat(
                            timespec="minutes"
                        )
                    except ValueError:
                        pass

            data.append({
                "name": name,
                "action": job.action,
                "function": job.function or None,
                "unit": job.unit or None,
                "schedule": job.cron or f"alle {job.interval_minutes}min",
                "enabled": job.enabled,
                "last_run": job_state.get("last_run"),
                "last_status": job_state.get("last_status"),
                "next_fire": next_fire,
            })
        output_json(data)
        return

    table = Table(title=f"Schedule (timezone: {config.timezone})")
    table.add_column("Job", style="bold")
    table.add_column("Aktion")
    table.add_column("Zeitplan")
    table.add_column("Aktiv", justify="center")
    table.add_column("Letzter Lauf")
    table.add_column("Naechster Lauf")

    for name, job in config.jobs.items():
        job_state = state.get(name, {})

        # Schedule
        if job.cron:
            schedule_str = _format_cron_human(job.cron)
        else:
            schedule_str = f"alle {job.interval_minutes}min"

        # Action
        if job.action == "internal":
            action_str = job.function
        else:
            action_str = f"systemd:{job.unit}"

        # Enabled
        enabled_str = "[green]ja[/green]" if job.enabled else "[dim]nein[/dim]"

        # Last run
        last_run = job_state.get("last_run", "—")
        last_status = job_state.get("last_status", "")
        if last_run != "—":
            try:
                lr_dt = datetime.fromisoformat(last_run)
                last_run = lr_dt.strftime("%d.%m. %H:%M")
                if last_status == "ok":
                    last_run = f"[green]{last_run}[/green]"
                elif last_status and last_status != "ok":
                    last_run = f"[red]{last_run} ({last_status})[/red]"
            except ValueError:
                pass

        # Next fire
        next_fire = "—"
        if not job.enabled:
            next_fire = "[dim]—[/dim]"
        elif job.cron:
            nf = cron_next_fire(job.cron, now, tz)
            if nf:
                next_fire = nf.strftime("%d.%m. %H:%M")
        elif job.interval_minutes:
            lr_raw = job_state.get("last_run")
            if lr_raw:
                try:
                    lr_dt = datetime.fromisoformat(lr_raw)
                    from datetime import timedelta
                    nf_dt = lr_dt + timedelta(minutes=job.interval_minutes)
                    next_fire = nf_dt.strftime("%d.%m. %H:%M")
                except ValueError:
                    next_fire = f"+{job.interval_minutes}min"
            else:
                next_fire = "nach Start"

        table.add_row(name, action_str, schedule_str, enabled_str, last_run, next_fire)

    console.print(table)
