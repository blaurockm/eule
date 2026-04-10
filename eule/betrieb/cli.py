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


def _build_schedule_rows() -> tuple[str, list[dict]]:
    """Bereitet Schedule-Daten auf. Gibt (timezone, rows) zurueck."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from eule.monitoring.schedule_config import load_schedule
    from eule.monitoring.scheduler import cron_next_fire, load_state

    config = load_schedule()
    state = load_state()
    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)

    rows = []
    for name, job in config.jobs.items():
        job_state = state.get(name, {})

        # Schedule
        schedule_str = _format_cron_human(job.cron) if job.cron else f"alle {job.interval_minutes}min"

        # Action
        action_str = job.function if job.action == "internal" else f"systemd:{job.unit}"

        # Last run
        last_run_raw = job_state.get("last_run")
        last_status = job_state.get("last_status", "")
        last_run_fmt = "—"
        if last_run_raw:
            try:
                last_run_fmt = datetime.fromisoformat(last_run_raw).strftime("%d.%m. %H:%M")
            except ValueError:
                last_run_fmt = last_run_raw

        # Next fire
        next_fire_fmt = "—"
        next_fire_iso = None
        if not job.enabled:
            pass
        elif job.cron:
            nf = cron_next_fire(job.cron, now, tz)
            if nf:
                next_fire_fmt = nf.strftime("%d.%m. %H:%M")
                next_fire_iso = nf.isoformat(timespec="minutes")
        elif job.interval_minutes and last_run_raw:
            try:
                lr_dt = datetime.fromisoformat(last_run_raw)
                nf_dt = lr_dt + timedelta(minutes=job.interval_minutes)
                next_fire_fmt = nf_dt.strftime("%d.%m. %H:%M")
                next_fire_iso = nf_dt.isoformat(timespec="minutes")
            except ValueError:
                next_fire_fmt = f"+{job.interval_minutes}min"
        elif job.interval_minutes:
            next_fire_fmt = "nach Start"

        rows.append({
            "name": name,
            "action": action_str,
            "schedule": schedule_str,
            "enabled": job.enabled,
            "last_run": last_run_fmt,
            "last_run_iso": last_run_raw,
            "last_status": last_status,
            "next_fire": next_fire_fmt,
            "next_fire_iso": next_fire_iso,
            "cron": job.cron or None,
            "interval_minutes": job.interval_minutes or None,
            "function": job.function or None,
            "unit": job.unit or None,
        })

    return config.timezone, rows


def _render_html(timezone: str, rows: list[dict]) -> str:
    """Erzeugt eine selbststaendige HTML-Seite mit dem Schedule."""
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    table_rows = []
    for r in rows:
        status_class = ""
        if r["last_status"] == "ok":
            status_class = "ok"
        elif r["last_status"] and r["last_status"] != "ok":
            status_class = "error"

        enabled = "ja" if r["enabled"] else "nein"
        enabled_class = "" if r["enabled"] else "disabled"

        last_run = r["last_run"]
        if status_class == "error":
            last_run = f'{last_run} ({r["last_status"]})'

        table_rows.append(
            f'<tr class="{enabled_class}">'
            f'<td class="name">{r["name"]}</td>'
            f'<td>{r["action"]}</td>'
            f'<td>{r["schedule"]}</td>'
            f'<td class="center">{enabled}</td>'
            f'<td class="{status_class}">{last_run}</td>'
            f'<td>{r["next_fire"]}</td>'
            f"</tr>"
        )

    return f"""\
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wachtel Schedule</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; background: #0d1117; color: #c9d1d9; }}
  h1 {{ font-size: 1.4rem; color: #58a6ff; margin-bottom: 0.3rem; }}
  .meta {{ color: #8b949e; font-size: 0.85rem; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th {{ text-align: left; padding: 0.6rem 1rem; background: #161b22; color: #8b949e;
       font-weight: 600; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.05em;
       border-bottom: 1px solid #30363d; }}
  td {{ padding: 0.5rem 1rem; border-bottom: 1px solid #21262d; }}
  tr:hover {{ background: #161b22; }}
  .name {{ font-weight: 600; color: #f0f6fc; }}
  .center {{ text-align: center; }}
  .ok {{ color: #3fb950; }}
  .error {{ color: #f85149; }}
  .disabled {{ opacity: 0.4; }}
</style>
</head>
<body>
<h1>Wachtel Schedule</h1>
<div class="meta">Timezone: {timezone} &middot; Stand: {now_str}</div>
<table>
<thead>
<tr><th>Job</th><th>Aktion</th><th>Zeitplan</th><th>Aktiv</th><th>Letzter Lauf</th><th>Naechster Lauf</th></tr>
</thead>
<tbody>
{"".join(table_rows)}
</tbody>
</table>
</body>
</html>"""


@schedule_app.command(name="list")
def schedule_list(
    output_format: str = typer.Option("markdown", "--format", help="Output: markdown, json oder html"),
) -> None:
    """Alle geplanten Jobs anzeigen."""
    from eule.monitoring.schedule_config import ScheduleConfigError

    try:
        timezone, rows = _build_schedule_rows()
    except ScheduleConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    if output_format == "json":
        output_json(rows)
        return

    if output_format == "html":
        import typer as t
        t.echo(_render_html(timezone, rows))
        return

    # Default: Rich table
    from rich.table import Table

    table = Table(title=f"Schedule (timezone: {timezone})")
    table.add_column("Job", style="bold")
    table.add_column("Aktion")
    table.add_column("Zeitplan")
    table.add_column("Aktiv", justify="center")
    table.add_column("Letzter Lauf")
    table.add_column("Naechster Lauf")

    for r in rows:
        enabled_str = "[green]ja[/green]" if r["enabled"] else "[dim]nein[/dim]"

        last_run = r["last_run"]
        if r["last_status"] == "ok":
            last_run = f"[green]{last_run}[/green]"
        elif r["last_status"] and r["last_status"] != "ok":
            last_run = f'[red]{last_run} ({r["last_status"]})[/red]'

        next_fire = r["next_fire"] if r["enabled"] else "[dim]—[/dim]"

        table.add_row(r["name"], r["action"], r["schedule"], enabled_str, last_run, next_fire)

    console.print(table)
