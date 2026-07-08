"""
Job-Funktionen fuer den Wachtel-Scheduler.

Jede Funktion implementiert einen periodischen Job. Rendering der Meldungen
liegt in eule.monitoring.render (Telegram schmal, Email als HTML).
"""

import json
import os
from collections.abc import Callable
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger as log

from eule.monitoring.schedule_config import JobConfig

BERLIN = ZoneInfo("Europe/Berlin")

# Daily-Watcher: Fenster + Zustand. Hase schreibt die EOD-JSONs zu
# env-abhaengigen Zeiten (production ~22:30, staging-ibkr ~23:30) — der
# Watcher laeuft als Intervall-Job und verschickt pro Env, sobald dessen
# JSON da ist. Die Gesamt-Email geht raus, wenn alle erwarteten Envs
# geliefert haben, spaetestens zur Deadline (dann mit "fehlt"-Ausweis).
DAILY_WINDOW_START = time(22, 30)
DAILY_EMAIL_DEADLINE = time(23, 55)
DAILY_SENT_STATE = Path.home() / ".eule" / ".daily_sent.json"


def _load_daily_summary_jsons(date_str: str) -> dict[str, dict]:
    """Read the daily-summary-*.json files Hase writes after mark-to-market.

    Keyed by env name. Production schreibt ~22:30 Berlin, staging-ibkr ~23:30
    (zum Handelsende), staging-hl ~23:59.
    """
    from eule.monitoring.precheck import all_werkstatt_logs_dirs

    log_dirs = all_werkstatt_logs_dirs()
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
    import re

    import requests

    from eule.monitoring.render import render_alert_telegram, render_anomaly_email_html
    from eule.monitoring.telegram_bot import (
        anomalies_changed,
        clear_anomaly_state,
        is_muted,
        run_precheck,
        send_email,
    )

    log.info("Running scheduled precheck")
    exit_code, output = run_precheck()
    result_label = {0: "OK", 1: "ANOMALIES", 2: "SUMMARY", 3: "SUPPRESSED"}.get(exit_code, "?")
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
            n = len(anomaly_lines)

            # Deterministisch ins Log — so ist im journalctl exakt sichtbar,
            # WAS gefeuert hat.
            for line in anomaly_lines:
                log.warning(f"ANOMALY: {line}")

            alert_callback(render_alert_telegram(anomaly_lines), parse_mode="HTML")

            # Anomalien einmal prominent + Live-Status — keine Analyse,
            # nur die Daten, die der User selbst auswertet.
            ts = datetime.now(BERLIN).strftime("%Y-%m-%d %H:%M")
            email_body = render_anomaly_email_html(anomaly_lines, output, ts)
            send_email(f"Wachtel: {n} Anomalie(n) — {ts}", email_body, html=True)

    elif exit_code == 0:
        # Wirklich OK — exit 3 (bekannte Anomalien unterdrueckt) loescht das
        # Fingerprint-Gedaechtnis NICHT, sonst wuerde jede Wertaenderung
        # derselben Anomalie einen Re-Alert ausloesen.
        clear_anomaly_state()


def _load_daily_state(date_str: str) -> dict:
    """Sent-State des Daily-Watchers fuer einen Tag. Reset bei Datumswechsel."""
    try:
        state = json.loads(DAILY_SENT_STATE.read_text())
        if state.get("date") == date_str:
            return state
    except Exception:
        pass
    return {"date": date_str, "sent_envs": [], "email_sent": False}


def _save_daily_state(state: dict) -> None:
    try:
        DAILY_SENT_STATE.parent.mkdir(parents=True, exist_ok=True)
        DAILY_SENT_STATE.write_text(json.dumps(state, indent=1))
    except Exception as e:
        log.error(f"Daily-Sent-State schreiben fehlgeschlagen: {e}")


def _expected_daily_envs(now: datetime) -> list[str]:
    """Envs, von denen heute ein EOD-JSON erwartet wird: monitoring-aktiv
    und heutiger Wochentag in den Trading-Weekdays (aus der Fuchs-Config)."""
    from eule.monitoring import precheck as pc

    expected = []
    for env_name, env_config in pc.ENVIRONMENTS.items():
        if not env_config.get("monitoring", True):
            continue
        schedule = pc.load_trading_hours(env_name)
        weekdays = schedule["weekdays"] if schedule else [0, 1, 2, 3, 4, 5, 6]
        if now.weekday() in weekdays:
            expected.append(env_name)
    return expected


def job_daily_summary(
    alert_callback: Callable[..., None],
    email_callback: Callable[..., None],
    job_config: JobConfig,
    now: datetime | None = None,
) -> None:
    """Daily-Watcher — pro Env eine Telegram-Nachricht, eine Gesamt-Email.

    Laeuft als Intervall-Job (schedule.yaml: interval_minutes) und ist
    ausserhalb des Fensters ein No-op. Sobald das EOD-JSON eines erwarteten
    Envs existiert, geht dessen Daily per Telegram raus (real ~22:40,
    staging-ibkr ~23:40). Die Gesamt-Email (HTML, alle Envs + offene
    Anomalien) wird verschickt, wenn alle erwarteten Envs geliefert haben —
    spaetestens zur DAILY_EMAIL_DEADLINE, dann mit explizitem "fehlt"-Ausweis.
    Der Sent-State (~/.eule/.daily_sent.json) verhindert Doppelversand,
    auch ueber Bot-Restarts hinweg.
    """
    from eule.monitoring.precheck import load_open_anomalies
    from eule.monitoring.render import render_daily_email_html, render_env_daily_telegram
    from eule.monitoring.telegram_bot import send_email

    if now is None:
        now = datetime.now(BERLIN)
    if now.time() < DAILY_WINDOW_START:
        return

    date_str = now.strftime("%Y-%m-%d")
    state = _load_daily_state(date_str)
    expected = _expected_daily_envs(now)
    if not expected:
        return  # Wochenende / kein Handelstag

    summaries = _load_daily_summary_jsons(date_str)

    # Pro Env: Telegram sobald das JSON da ist
    for env_name in expected:
        if env_name in state["sent_envs"] or env_name not in summaries:
            continue
        log.info(f"Daily fuer {env_name} wird versendet")
        alert_callback(render_env_daily_telegram(summaries[env_name]), parse_mode="HTML")
        state["sent_envs"].append(env_name)
        _save_daily_state(state)

    # Gesamt-Email: wenn komplett, spaetestens zur Deadline
    if not state["email_sent"]:
        missing = [e for e in expected if e not in summaries]
        if not missing or now.time() >= DAILY_EMAIL_DEADLINE:
            if missing:
                log.warning(f"Daily-Email ohne EOD-JSON von: {', '.join(missing)}")
            html = render_daily_email_html(
                summaries, missing, load_open_anomalies(), date_str
            )
            send_email(f"Wachtel Daily — {date_str}", html, html=True)
            state["email_sent"] = True
            _save_daily_state(state)


def job_weekly_report(
    alert_callback: Callable[..., None],
    email_callback: Callable[..., None],
    job_config: JobConfig,
) -> None:
    """Woechentlicher Performance-Report — Telegram schmal, Email als HTML-Tabelle."""
    from eule.monitoring.render import render_weekly_email_html, render_weekly_telegram
    from eule.monitoring.telegram_bot import collect_weekly_performance, send_email

    log.info("Running weekly performance report")

    result = collect_weekly_performance("")
    if isinstance(result, str):
        # Fehlermeldung (DB nicht erreichbar etc.)
        alert_callback(f"Weekly Report fehlgeschlagen: {result}")
        return

    date_str = datetime.now(BERLIN).strftime("%Y-%m-%d")
    alert_callback(render_weekly_telegram(result), parse_mode="HTML")
    send_email(
        subject=f"Wachtel Weekly Report {date_str}",
        body=render_weekly_email_html(result, date_str),
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
