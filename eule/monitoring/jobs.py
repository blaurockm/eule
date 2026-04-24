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
    """Periodischer Precheck — Anomalie-Erkennung mit Dedup und Claude-Analyse.

    Enthaelt eigene Notification-Logik (Mute, Dedup, Claude-Analyse nur bei
    neuen Anomalien). Die job_config.notify/on_error steuern die Kanaele,
    aber die Entscheidung ob benachrichtigt wird, liegt hier.
    """
    import requests

    from eule.monitoring.telegram_bot import (
        _prefetch_api_data,
        _report_to_html,
        anomalies_changed,
        clear_anomaly_state,
        get_claude_failures,
        invoke_claude,
        is_muted,
        record_claude_failure,
        reset_claude_failures,
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
            escaped = (
                alert_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            alert_callback(
                f"<b>{n} Anomalie(n) erkannt:</b>\n<pre>{escaped}</pre>\n"
                f"<i>(Analyse per Email)</i>",
                parse_mode="HTML",
            )
            # Claude-Analyse via Email
            if get_claude_failures() < 3:
                try:
                    api_context = _prefetch_api_data()
                    full_context = (
                        f"Precheck-Anomalien:\n{alert_text}\n\n"
                        f"Voller Precheck:\n{output}\n\n{api_context}"
                    )
                    analysis = invoke_claude(
                        full_context,
                        "Anomalien wurden erkannt. "
                        "ZUERST: Hole Daten per Bash (curl localhost APIs, grep in Logfiles). "
                        "Du bist auf dem Server — nutze Bash direkt, KEIN ssh. "
                        "DANN: Analysiere die Ursache und liefere konkrete Loesungsvorschlaege. "
                        "Format: Kurze Diagnose pro Problem, dann konkreter Fix-Vorschlag. "
                        "Kein Smalltalk, nur Diagnose + Aktion.",
                    )
                    tz = ZoneInfo("Europe/Berlin")
                    ts = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
                    email_body = _report_to_html(
                        f"Anomalien:\n{alert_text}\n\n---\n\nAnalyse:\n{analysis}",
                        title=f"Wachtel Anomalie-Analyse — {ts}",
                    )
                    send_email(f"Wachtel: {n} Anomalie(n) — {ts}", email_body, html=True)
                    reset_claude_failures()
                except Exception:
                    record_claude_failure()

    elif exit_code == 0:
        clear_anomaly_state()


def job_daily_summary(
    alert_callback: Callable[..., None],
    email_callback: Callable[..., None],
    job_config: JobConfig,
) -> None:
    """Taegliche Zusammenfassung — basiert auf Hase's daily-summary-*.json Files.

    Hase schreibt um 22:30 Berlin (real) bzw. 23:30 (staging) ein JSON nach
    mark-to-market und faehrt dann runter. Der Job laeuft 22:45 — die
    Live-APIs sind dann absichtlich nicht mehr erreichbar. Datenquelle
    sind ausschliesslich die JSON-Files.
    """
    from eule.monitoring.telegram_bot import (
        _report_to_html,
        get_claude_failures,
        invoke_claude,
        markdown_to_telegram_html,
        record_claude_failure,
        reset_claude_failures,
        run_precheck,
        send_email,
    )

    log.info("Running daily summary")

    tz = ZoneInfo("Europe/Berlin")
    date_str = datetime.now(tz).strftime("%Y-%m-%d")

    # force_summary=True erzwingt den JSON-basierten Render-Pfad in precheck,
    # unabhaengig davon, ob is_daily_summary_time() gerade true ist.
    _, precheck_output = run_precheck(force_summary=True)
    summary_jsons = _load_daily_summary_jsons(date_str)

    if not summary_jsons:
        log.warning(f"Keine daily-summary JSONs fuer {date_str} gefunden")

    json_section = (
        json.dumps(summary_jsons, indent=2, default=str)
        if summary_jsons
        else "(keine daily-summary JSONs gefunden — Hase hat sie nicht geschrieben)"
    )
    full_context = (
        f"Daily Summary (formatiert):\n{precheck_output}\n\n"
        f"Daily Summary JSONs (raw, mit Positionen, Stats, FSM-States):\n{json_section}"
    )

    if get_claude_failures() < 3:
        try:
            summary = invoke_claude(
                full_context,
                "Erstelle die taegliche Zusammenfassung (Daily Summary). "
                "Datenquelle sind die daily-summary-*.json Files, die Hase nach "
                "Mark-to-Market schreibt. Hase ist um diese Zeit absichtlich "
                "heruntergefahren — Live-APIs werden NICHT abgefragt. "
                "Fasse fuer jedes Environment zusammen: Cash, Equity, Daily PnL "
                "(realized + unrealized), FSM-States der Strategien, offene "
                "Positionen mit signifikantem PnL. Kurz und praegnant.",
            )
            alert_callback(
                f"<b>Daily Summary</b>\n\n{markdown_to_telegram_html(summary)}",
                parse_mode="HTML",
            )
            email_body = _report_to_html(summary, title=f"Wachtel Daily Summary — {date_str}")
            send_email(f"Wachtel Daily Summary — {date_str}", email_body, html=True)
            reset_claude_failures()
        except Exception:
            record_claude_failure()
            _send_fallback(alert_callback, send_email, precheck_output, date_str)
    else:
        _send_fallback(alert_callback, send_email, precheck_output, date_str)


def _send_fallback(alert_callback, email_fn, precheck_output: str, date_str: str):
    """Fallback Daily Summary ohne Claude."""
    from eule.monitoring.telegram_bot import _report_to_html

    escaped = (
        precheck_output.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    alert_callback(
        f"<b>Daily Summary (ohne Claude)</b>\n<pre>{escaped}</pre>",
        parse_mode="HTML",
    )
    fallback_plain = f"Daily Summary (ohne Claude):\n{precheck_output}"
    email_body = _report_to_html(fallback_plain, title=f"Wachtel Daily Summary — {date_str}")
    email_fn(f"Wachtel Daily Summary — {date_str}", email_body, html=True)


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
