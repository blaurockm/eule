"""
Job-Funktionen fuer den Wachtel-Scheduler.

Jede Funktion implementiert einen periodischen Job. Die Funktionen
werden aus dem alten Scheduler in telegram_bot.py extrahiert und
behalten ihre bestehende Logik (Anomalie-Dedup, Claude-Analyse, etc.).
"""

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger as log

from eule.monitoring.schedule_config import JobConfig


def _load_daily_summary_jsons(date_str: str) -> dict[str, dict]:
    """Read the daily-summary-*.json files Hase writes after mark-to-market.

    Hase produces these files at ~22:30 Berlin (production) and 23:30
    Berlin (staging). The Daily Summary job runs at 22:45 — by then the
    live APIs are down on purpose, so these JSONs are the canonical source.
    """
    hase_override = os.environ.get("EULE_HASE_DIR")
    production_dir = Path(hase_override) if hase_override else Path.home() / "hase"
    log_dirs = [
        Path.home() / "staging" / "werkstatt" / "logs",
        production_dir / "werkstatt" / "logs",
    ]
    result: dict[str, dict] = {}
    for log_dir in log_dirs:
        for f in sorted(log_dir.glob(f"daily-summary-*-{date_str}.json")):
            try:
                data = json.loads(f.read_text())
                env = data.get("env", f.stem)
                result[env] = data
            except Exception as e:
                log.warning(f"Failed to read {f}: {e}")
    return result


def job_precheck(
    alert_callback: Callable[..., None],
    email_callback: Callable[..., None],
    job_config: JobConfig,
) -> None:
    """Periodischer Precheck — deterministische Anomalie-Erkennung mit Dedup.

    Kein LLM: der rohe Precheck-Output ist die Meldung. Bei neuen Anomalien
    wird der Text ins Log geschrieben (journalctl-auswertbar), per Telegram
    geschickt und per Email versendet. Die job_config.notify/on_error steuern
    die Kanaele, aber die Entscheidung ob benachrichtigt wird, liegt hier.
    """
    import requests

    from eule.monitoring.telegram_bot import (
        _report_to_html,
        anomalies_changed,
        clear_anomaly_state,
        is_muted,
        run_precheck,
        send_email,
    )

    import re

    log.info("Running scheduled precheck")
    exit_code, output = run_precheck()
    result_label = {0: "OK", 1: "ANOMALIES", 2: "SUMMARY"}.get(exit_code, "?")
    log.info(f"Precheck result: exit_code={exit_code} ({result_label})")

    # Dead-man's switch
    healthcheck_url = os.environ.get("HEALTHCHECK_URL", "")
    if healthcheck_url:
        try:
            requests.get(healthcheck_url, timeout=5)
        except Exception:
            pass

    if exit_code == 1 and not is_muted():
        # Parse anomaly lines
        anomaly_lines = []
        for line in output.split("\n"):
            line = line.strip()
            if not line or line == "ANOMALIES DETECTED:":
                continue
            if re.match(r"\[(CRITICAL|WARNING)\]", line):
                anomaly_lines.append(line)

        if anomaly_lines and anomalies_changed(anomaly_lines):
            alert_text = "\n".join(anomaly_lines)
            n = len(anomaly_lines)

            # Deterministisch ins Log — so ist im journalctl exakt sichtbar, WAS
            # gefeuert hat (frueher ging der Text nur an die KI + Telegram/Mail).
            for line in anomaly_lines:
                log.warning(f"ANOMALY: {line}")

            escaped = (
                alert_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            alert_callback(
                f"<b>{n} Anomalie(n) erkannt:</b>\n<pre>{escaped}</pre>",
                parse_mode="HTML",
            )

            # Voller deterministischer Precheck-Output per Email — keine Analyse,
            # nur die Daten, die der User selbst auswertet.
            tz = ZoneInfo("Europe/Berlin")
            ts = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
            email_body = _report_to_html(
                f"Anomalien:\n{alert_text}\n\n---\n\nVoller Precheck:\n{output}",
                title=f"Wachtel Anomalie — {ts}",
            )
            send_email(f"Wachtel: {n} Anomalie(n) — {ts}", email_body, html=True)

    elif exit_code == 0:
        clear_anomaly_state()


def job_daily_summary(
    alert_callback: Callable[..., None],
    email_callback: Callable[..., None],
    job_config: JobConfig,
) -> None:
    """Taegliche Zusammenfassung — deterministisch aus Hase's daily-summary-*.json.

    Hase schreibt um ~22:30 Berlin (real) bzw. ~23:30 (staging) ein JSON nach
    mark-to-market und faehrt dann runter. Der Job laeuft danach — die
    Live-APIs sind dann absichtlich nicht mehr erreichbar. Datenquelle sind
    ausschliesslich die JSON-Files.

    Kein LLM: der JSON-basierte Precheck-Summary-Render ist die Zusammenfassung.
    Datenqualitaets-Warnungen (Hase 'warnings'-Feld) werden vorangestellt.
    """
    from eule.monitoring.telegram_bot import (
        _report_to_html,
        run_precheck,
        send_email,
    )

    log.info("Running daily summary")

    tz = ZoneInfo("Europe/Berlin")
    now = datetime.now(tz)
    date_str = now.strftime("%Y-%m-%d")

    # force_summary=True erzwingt den JSON-basierten Render-Pfad in precheck,
    # unabhaengig davon, ob is_daily_summary_time() gerade true ist.
    _, precheck_output = run_precheck(force_summary=True)
    summary_jsons = _load_daily_summary_jsons(date_str)

    if not summary_jsons:
        log.warning(f"Keine daily-summary JSONs fuer {date_str} gefunden")

    warn_block = _format_data_quality_warnings(summary_jsons)
    if warn_block:
        log.warning(f"Daily-Summary Datenqualitaets-Warnungen:\n{warn_block}")
        summary = f"{warn_block}\n\n{precheck_output}"
    else:
        summary = precheck_output

    escaped = summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    alert_callback(f"<b>Daily Summary</b>\n<pre>{escaped}</pre>", parse_mode="HTML")
    email_body = _report_to_html(summary, title=f"Wachtel Daily Summary — {date_str}")
    send_email(f"Wachtel Daily Summary — {date_str}", email_body, html=True)


def _format_data_quality_warnings(summary_jsons: dict[str, dict]) -> str:
    """Deterministisch die Hase-'warnings' pro Env formatieren.

    Hase schreibt pro Env optional ein 'warnings'-Feld (Liste). Ist mindestens
    eine Warnung mit affects_pnl gesetzt, wird der Daily-PnL des Envs als
    potenziell unzuverlaessig markiert. Kein Schema-Raten: Dict-Warnungen werden
    ueber 'message'/'msg' gerendert, sonst als JSON; alles andere per str().
    Leere/fehlende Felder -> "".
    """
    lines: list[str] = []
    for env, data in sorted(summary_jsons.items()):
        warnings = data.get("warnings") or []
        if not warnings:
            continue
        env_name = data.get("env", env)
        affects_pnl = False
        lines.append(f"[{env_name}] Datenqualitaets-Warnungen:")
        for w in warnings:
            if isinstance(w, dict):
                msg = w.get("message") or w.get("msg") or json.dumps(w, default=str)
                if w.get("affects_pnl"):
                    affects_pnl = True
            else:
                msg = str(w)
            lines.append(f"  - {msg}")
        if affects_pnl:
            lines.append(f"  => Daily-PnL fuer {env_name} POTENZIELL UNZUVERLAESSIG")
    if not lines:
        return ""
    return "⚠ WARNUNGEN:\n" + "\n".join(lines)


def job_weekly_report(
    alert_callback: Callable[..., None],
    email_callback: Callable[..., None],
    job_config: JobConfig,
) -> None:
    """Woechentlicher Performance-Report."""
    from eule.monitoring.telegram_bot import (
        _report_to_html,
        handle_report,
        send_email,
    )

    log.info("Running weekly performance report")

    report_text = handle_report("")
    # handle_report returns Telegram-HTML (already converted via markdown_to_telegram_html).
    alert_callback(
        f"<b>Weekly Performance Report</b>\n\n{report_text}",
        parse_mode="HTML",
    )

    tz = ZoneInfo("Europe/Berlin")
    date_str = datetime.now(tz).strftime("%Y-%m-%d")
    send_email(
        subject=f"Wachtel Weekly Report {date_str}",
        body=_report_to_html(report_text),
        html=True,
    )


def job_ep_brief(
    alert_callback: Callable[..., None],
    email_callback: Callable[..., None],
    job_config: JobConfig,
) -> None:
    """EP Morning Brief — offene Positionen + Watchlist per Email."""
    from datetime import date

    from eule.ep.trades import morning_brief
    from eule.monitoring.telegram_bot import markdown_to_telegram_html
    from eule.pipeline.email import send_email

    log.info("Running EP morning brief")

    brief_text = morning_brief()
    subject = f"EP Morning Brief — {date.today().isoformat()}"

    if "email" in job_config.notify:
        send_email(subject=subject, body=brief_text)

    if "telegram" in job_config.notify:
        alert_callback(
            f"<b>{subject}</b>\n\n{markdown_to_telegram_html(brief_text)}",
            parse_mode="HTML",
        )


# Registry: function-Name aus schedule.yaml → Callable
INTERNAL_JOBS: dict[str, Callable] = {
    "precheck": job_precheck,
    "daily_summary": job_daily_summary,
    "weekly_report": job_weekly_report,
    "ep_brief": job_ep_brief,
}
