#!/usr/bin/env python3
"""
Wachtel — Hase Monitoring Telegram Bot.

Daemon process that:
- Polls Telegram for user messages
- Runs precheck.py on a 15-minute schedule
- Sends alerts on anomalies
- Invokes Claude Code for analysis on demand
"""

import glob as glob_mod
import html as html_mod
import json
import logging
import os
import queue
import re
import smtplib
import subprocess
import threading
import time as time_module
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("wachtel")

# --- Configuration ---

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5592934887")
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "")

MONITORING_DIR = Path(__file__).parent
PRECHECK_SCRIPT = MONITORING_DIR / "precheck.py"
EULE_ROOT = MONITORING_DIR.parent.parent  # eule project root


def _hase_root(env: str | None = None) -> Path:
    """Hase-Installation fuer ein Environment.

    Production (real-*) liegt unter ~/hase/, Staging unter ~/staging/.
    Auf dem Entwicklungsrechner kann EULE_HASE_DIR alles ueberschreiben.
    """
    override = os.environ.get("EULE_HASE_DIR")
    if override:
        return Path(override)
    if env and env.startswith("staging"):
        return Path.home() / "staging"
    return Path.home() / "hase"

TELEGRAM_POLL_TIMEOUT = 30
MAX_MESSAGE_LENGTH = 4096

# --- Fuchs Process Control ---

_FUCHS_SERVICES = {
    "staging-ibkr": "fuchs-staging.service",
    "staging-hl": "fuchs-staging.service",
    "real-ibkr": "fuchs-supervisor.service",
    "real2-ibkr": "fuchs-supervisor.service",
}

# Reverse: which envs share a service?
_SERVICE_ENVS: dict[str, list[str]] = {}
for _env, _svc in _FUCHS_SERVICES.items():
    _SERVICE_ENVS.setdefault(_svc, []).append(_env)

_FUCHS_CONFIGS = {
    "staging-ibkr": "fuchs-config.staging.json",
    "staging-hl": "fuchs-config.staging.json",
    "real-ibkr": "fuchs-config.production.json",
    "real2-ibkr": "fuchs-config.production.json",
}

# Alert dedup: only re-alert when the anomaly set changes (not on every precheck cycle)

# --- Telegram API ---


def tg_request(method: str, **kwargs) -> dict | None:
    """Make a Telegram Bot API request."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        # For long-polling getUpdates, HTTP timeout must exceed Telegram's poll timeout
        http_timeout = kwargs.get("timeout", 0) + 10 if method == "getUpdates" else 30
        resp = requests.post(url, json=kwargs, timeout=http_timeout)
        data = resp.json()
        if not data.get("ok"):
            log.error(f"Telegram API error: {data}")
            return None
        return data.get("result")
    except Exception as e:
        log.error(f"Telegram request failed: {e}")
        return None


def markdown_to_telegram_html(text: str) -> str:
    """Convert Claude's markdown to Telegram-compatible HTML.

    Telegram supports: <b>, <i>, <code>, <pre>, <a>, <s>, <u>.
    Code blocks and Markdown tables become <pre> (monospace, good for alignment).
    """
    code_blocks: list[str] = []

    def _save_block(match):
        code_blocks.append(match.group(1) or "")
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    text = re.sub(r"```\w*\n?(.*?)```", _save_block, text, flags=re.DOTALL)

    # Markdown tables: 2+ consecutive lines that start and end with '|'.
    # Telegram has no table tags, so the only way to keep alignment is <pre>.
    def _save_table(match):
        code_blocks.append(match.group(0).rstrip("\n"))
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00\n"

    text = re.sub(
        r"(?:^[ \t]*\|.*\|[ \t]*\n?){2,}",
        _save_table,
        text,
        flags=re.MULTILINE,
    )

    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\n)\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"^#{1,4}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    for i, block in enumerate(code_blocks):
        escaped = block.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CODEBLOCK{i}\x00", f"<pre>{escaped}</pre>")

    return text


def send_photo(photo_path: str, caption: str = ""):
    """Send a photo to the configured chat."""
    if not BOT_TOKEN:
        log.warning("No TELEGRAM_BOT_TOKEN set, skipping photo")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            data = {"chat_id": CHAT_ID}
            if caption:
                data["caption"] = caption
            resp = requests.post(url, data=data, files={"photo": f}, timeout=30)
            result = resp.json()
            if not result.get("ok"):
                log.error(f"Telegram sendPhoto error: {result}")
    except Exception as e:
        log.error(f"sendPhoto failed: {e}")


def send_message(text: str, parse_mode: str | None = None, reply_markup: dict | None = None):
    """Send a message to the configured chat. Splits long messages."""
    if not BOT_TOKEN:
        log.warning("No TELEGRAM_BOT_TOKEN set, skipping message")
        return

    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        kwargs: dict = {"chat_id": CHAT_ID, "text": chunk}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        # Only attach buttons to the last chunk
        if reply_markup and i == len(chunks) - 1:
            kwargs["reply_markup"] = reply_markup
        result = tg_request("sendMessage", **kwargs)
        if result is None and parse_mode:
            # Retry without parse_mode (formatting might be broken)
            kwargs2: dict = {"chat_id": CHAT_ID, "text": chunk}
            if reply_markup and i == len(chunks) - 1:
                kwargs2["reply_markup"] = reply_markup
            tg_request("sendMessage", **kwargs2)


def answer_callback_query(callback_query_id: str, text: str = ""):
    """Acknowledge a callback query (dismiss the 'loading' indicator on the button)."""
    tg_request("answerCallbackQuery", callback_query_id=callback_query_id, text=text)


def edit_message(message_id: int, text: str, parse_mode: str | None = None):
    """Edit an existing message (used to update confirmation messages after button press)."""
    kwargs: dict = {"chat_id": CHAT_ID, "message_id": message_id, "text": text}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    tg_request("editMessageText", **kwargs)


def _inline_keyboard(buttons: list[tuple[str, str]]) -> dict:
    """Build an InlineKeyboardMarkup from a list of (label, callback_data) tuples."""
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]}


def _register_bot_commands():
    """Register bot commands so they appear in Telegram's '/' menu."""
    commands = [
        {"command": "status", "description": "Precheck ausfuehren"},
        {"command": "summary", "description": "Tages-Summary (deterministisch)"},
        {"command": "fstatus", "description": "Fuchs Service Status"},
        {"command": "fstart", "description": "Fuchs Service starten"},
        {"command": "fstop", "description": "Fuchs Service stoppen"},
        {"command": "frestart", "description": "Fuchs Service neustarten"},
        {"command": "emergency", "description": "Emergency Stop setzen"},
        {"command": "flogs", "description": "Runtime-Log anzeigen"},
        {"command": "report", "description": "Performance Report"},
        {"command": "equity", "description": "Equity-Kurve als Chart"},
        {"command": "baseline", "description": "Baseline-YAML anzeigen"},
        {"command": "mute", "description": "Alerts stummschalten"},
        {"command": "unmute", "description": "Alerts wieder aktivieren"},
    ]
    tg_request("setMyCommands", commands=commands)
    log.info(f"Registered {len(commands)} bot commands")


def split_message(text: str) -> list[str]:
    """Split a message into chunks that fit Telegram's limit."""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    chunks = []
    while text:
        if len(text) <= MAX_MESSAGE_LENGTH:
            chunks.append(text)
            break
        # Try to split at newline
        split_at = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if split_at == -1:
            split_at = MAX_MESSAGE_LENGTH
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# --- Email via Fuchs SMTP Config ---

_email_config: dict | None = None


def _load_email_config() -> dict | None:
    """Load SMTP config from fuchs-config (production or staging).

    Uses the SMTP credentials regardless of the 'enabled' flag —
    that flag controls Fuchs alerting, not Wachtel email sending.
    """
    global _email_config
    if _email_config is not None:
        return _email_config

    config_path = _hase_root("real-ibkr") / "fuchs-config.production.json"
    if not config_path.exists():
        config_path = _hase_root("staging-ibkr") / "fuchs-config.staging.json"
    if not config_path.exists():
        log.warning("No fuchs-config found for email")
        return None

    try:
        data = json.loads(config_path.read_text())
        email = data.get("alerting", {}).get("email", {})
        if not email.get("smtp_host"):
            log.warning("No smtp_host in fuchs-config email section")
            return None
        _email_config = email
        return _email_config
    except Exception as e:
        log.error(f"Failed to load email config: {e}")
        return None


def send_email(subject: str, body: str, html: bool = False) -> bool:
    """Send an email using SMTP credentials from fuchs-config.

    Args:
        subject: Email subject
        body: Email body (plain text or HTML)
        html: If True, send as HTML email

    Returns:
        True on success, False on failure
    """
    cfg = _load_email_config()
    if not cfg:
        log.warning("Email not configured — skipping")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = cfg.get("from_address", cfg["smtp_user"])
    msg["To"] = ", ".join(cfg["to_addresses"])
    msg["Subject"] = subject

    if html:
        msg.attach(MIMEText(body, "html"))
    else:
        msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587)) as server:
            server.starttls()
            server.login(cfg["smtp_user"], cfg["smtp_password"])
            server.send_message(msg)
        log.info(f"Email sent: {subject}")
        return True
    except Exception as e:
        log.error(f"Failed to send email: {e}")
        return False


def _report_to_html(report_text: str, title: str = "Wachtel Weekly Performance Report") -> str:
    """Convert report text (with markdown/Telegram HTML) to a proper HTML email body."""
    # If the text already contains Telegram HTML tags (<b>, <code>, <pre>),
    # wrap it in a full HTML document with styling.
    has_html_tags = bool(re.search(r"<(b|code|pre|i)>", report_text))

    if has_html_tags:
        return _telegram_html_to_email(report_text, title)

    # Plain text / markdown conversion
    lines = report_text.split("\n")
    html_parts = [
        "<html><body style='font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;'>",
        f"<h2 style='color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 8px;'>{title}</h2>",
    ]
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                html_parts.append("</pre>")
                in_code = False
            else:
                html_parts.append(
                    "<pre style='background: #f4f4f4; padding: 12px; font-size: 13px; overflow-x: auto; border-radius: 4px;'>"
                )
                in_code = True
            continue
        if in_code:
            html_parts.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        elif line.startswith("**") and line.endswith("**"):
            html_parts.append(f"<h3 style='color: #333; margin-top: 16px;'>{line.strip('*')}</h3>")
        elif line.strip().startswith("---"):
            html_parts.append("<hr style='border: none; border-top: 1px solid #ddd; margin: 12px 0;'>")
        elif re.match(r"^\s*[⚠🔴🟡]", line):
            html_parts.append(f"<p style='color: #cc6600;'>{line}</p>")
        elif line.strip():
            html_parts.append(f"<p style='margin: 4px 0; line-height: 1.5;'>{line}</p>")

    if in_code:
        html_parts.append("</pre>")
    html_parts.append("<hr style='border: none; border-top: 1px solid #ddd; margin-top: 24px;'>")
    html_parts.append("<p style='color: #999; font-size: 11px;'>Wachtel Monitoring</p>")
    html_parts.append("</body></html>")
    return "\n".join(html_parts)


def _telegram_html_to_email(text: str, title: str) -> str:
    """Convert Telegram HTML (<b>, <code>, <pre>) to a full HTML email document."""
    # Telegram HTML is a subset — just wrap in a styled document
    body = text
    # Convert \n to <br> outside of <pre> blocks
    parts = re.split(r"(<pre>.*?</pre>)", body, flags=re.DOTALL)
    converted = []
    for part in parts:
        if part.startswith("<pre>"):
            # Style pre blocks
            part = part.replace(
                "<pre>",
                "<pre style='background: #f4f4f4; padding: 12px; font-size: 13px; overflow-x: auto; border-radius: 4px;'>",
                1,
            )
            converted.append(part)
        else:
            converted.append(part.replace("\n", "<br>\n"))
    body = "".join(converted)

    # Style inline code
    body = body.replace("<code>", "<code style='background: #f0f0f0; padding: 2px 5px; border-radius: 3px; font-size: 13px;'>")

    return f"""<html>
<body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px; color: #1a1a1a; line-height: 1.6;">
<h2 style="color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 8px;">{title}</h2>
{body}
<hr style="border: none; border-top: 1px solid #ddd; margin-top: 24px;">
<p style="color: #999; font-size: 11px;">Wachtel Monitoring</p>
</body></html>"""


# --- Precheck Execution ---


def run_precheck(force_summary: bool = False) -> tuple[int, str]:
    """Run precheck.py and return (exit_code, output)."""
    cmd = ["python", str(PRECHECK_SCRIPT)]
    if force_summary:
        cmd.append("--summary")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(EULE_ROOT),
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "Precheck timed out after 30s"
    except Exception as e:
        return 1, f"Precheck execution failed: {e}"


# --- Command Handlers ---


def handle_status() -> str:
    """Handle /status command — run precheck, return formatted output."""
    exit_code, output = run_precheck()
    prefix = {0: "OK", 1: "ANOMALIES", 2: "SUMMARY"}.get(exit_code, "?")
    escaped = output.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<b>[{prefix}]</b>\n<pre>{escaped}</pre>"


def handle_summary() -> str:
    """Handle /summary — deterministischer Tages-Summary (JSON-basierter Render)."""
    _, output = run_precheck(force_summary=True)
    escaped = output.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<pre>{escaped}</pre>"


def handle_baseline(args: str) -> str:
    """Handle /baseline <name> — show baseline YAML."""
    name = args.strip()
    if not name:
        files = sorted(MONITORING_DIR.glob("baselines/*.yaml"))
        names = [f.stem for f in files]
        listing = "\n".join(f"  {n}" for n in names)
        return f"<b>Verfuegbare Baselines:</b>\n<pre>{listing}</pre>"

    path = MONITORING_DIR / "baselines" / f"{name}.yaml"
    if not path.exists():
        return f"Baseline '{name}' nicht gefunden."
    yaml_text = path.read_text()
    escaped = yaml_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<b>{name}.yaml</b>\n<pre>{escaped}</pre>"


def _load_database_url() -> str | None:
    """Load DATABASE_URL from environment variables."""
    try:
        from eule.db import get_db_url
        return get_db_url("real-ibkr")
    except Exception:
        return None


def handle_equity(args: str) -> str | None:
    """Handle /equity <strategy> — generate and send equity curve chart.

    Returns error message string, or None on success (photo sent directly).
    """
    import tempfile

    strategy = args.strip()

    db_url = _load_database_url()
    if not db_url:
        return "DATABASE_URL nicht gefunden (run/staging/ibkr/.env)."

    try:
        import psycopg
    except ImportError:
        return "psycopg nicht installiert."

    try:
        conn = psycopg.connect(db_url)
        if not strategy:
            # List available strategies
            cur = conn.execute(
                "SELECT DISTINCT strategy_key, runtime_name, count(*), min(date), max(date) "
                "FROM daily_pnl WHERE strategy_key IS NOT NULL "
                "GROUP BY strategy_key, runtime_name ORDER BY runtime_name, strategy_key"
            )
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return "Keine Strategie-Daten in der DB."
            lines = ["Verfuegbare Strategien:", ""]
            for sk, rn, cnt, d_min, d_max in rows:
                lines.append(f"  {sk}  ({rn}, {cnt} Tage, {d_min} - {d_max})")
            lines.append("")
            lines.append("Nutzung: /equity <strategy_name>")
            return "\n".join(lines)

        # Query equity data
        cur = conn.execute(
            "SELECT date, nav_end, runtime_name FROM daily_pnl WHERE strategy_key = %s ORDER BY date",
            (strategy,),
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return f"Keine Daten fuer '{strategy}'. /equity ohne Argument zeigt verfuegbare Strategien."

    except Exception as e:
        return f"DB-Fehler: {e}"

    # Generate chart
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt

        dates = [r[0] for r in rows]
        navs = [float(r[1]) for r in rows]
        runtime = rows[0][2]

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(dates, navs, "b-o", markersize=4, linewidth=1.5)
        ax.set_title(f"Equity: {strategy} ({runtime})", fontsize=14)
        ax.set_xlabel("Date")
        ax.set_ylabel("NAV")
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        fig.autofmt_xdate()
        fig.tight_layout()

        photo_path = tempfile.mktemp(suffix=".png", prefix=f"equity_{strategy}_")
        fig.savefig(photo_path, dpi=120)
        plt.close(fig)

        # Compute summary
        start_nav = navs[0]
        end_nav = navs[-1]
        ret_pct = (end_nav - start_nav) / start_nav * 100 if start_nav else 0
        caption = f"{strategy} ({runtime})\n{dates[0]} - {dates[-1]}\nNAV: {start_nav:.0f} -> {end_nav:.0f} ({ret_pct:+.1f}%)"

        send_photo(photo_path, caption=caption)

        # Clean up
        os.unlink(photo_path)
        return None  # success, photo already sent

    except Exception as e:
        return f"Chart-Fehler: {e}"


def handle_report(args: str) -> str:
    """Handle /report [env] — weekly performance report via Elster."""
    db_url = _load_database_url()
    if not db_url:
        return "DATABASE_URL nicht gefunden."

    try:
        import psycopg

        from eule.elster.data import (
            filter_trading_days,
            get_trading_weekdays,
            list_strategies,
            load_baseline,
            load_daily_pnl,
            load_trades,
            nav_to_returns,
            trading_periods_per_year,
        )
        from eule.elster.metrics import calculate_metrics
    except ImportError as e:
        return f"Elster nicht verfuegbar: {e}"

    # Welche Environments?
    env_arg = args.strip()
    if env_arg:
        envs = {env_arg: _RUNTIME_NAMES.get(env_arg, env_arg)}
    else:
        envs = dict(_RUNTIME_NAMES)  # alle

    parts: list[str] = []
    try:
        conn = psycopg.connect(db_url, autocommit=True)
    except Exception as e:
        return f"DB-Verbindung fehlgeschlagen: {e}"

    try:
        for env_name, runtime_name in envs.items():
            strategies = list_strategies(conn, runtime_name)
            if not strategies:
                continue

            df = load_daily_pnl(conn, runtime_name, days=7)
            if df.empty:
                parts.append(f"**{env_name}**: keine Daten (7d)")
                continue

            returns_df = nav_to_returns(df)
            if returns_df.empty:
                parts.append(f"**{env_name}**: zu wenig Daten")
                continue

            lines = [f"**{env_name}** (7 Tage)"]
            lines.append("```")
            lines.append(f"{'Strategy':<22} {'Ret':>7} {'Sharpe':>7} {'MaxDD':>7} {'WR':>6} {'PF':>6}")
            lines.append("-" * 60)

            for strat in strategies:
                if strat not in returns_df.columns:
                    continue
                # Returns auf konfigurierte Trading-Tage filtern
                strat_returns = returns_df[strat]
                weekdays = get_trading_weekdays(strat)
                ppy = 252
                if weekdays:
                    strat_returns = filter_trading_days(strat_returns, weekdays)
                    ppy = trading_periods_per_year(weekdays)
                m = calculate_metrics(strat_returns, periods_per_year=ppy)
                trades_df = load_trades(conn, runtime_name, days=7, strategy_key=strat)
                ret = f"{m.total_return * 100:+.1f}%"
                sharpe = f"{m.sharpe_ratio:.2f}" if m.sharpe_ratio != 0 else "—"
                mdd = f"{m.max_drawdown * 100:.1f}%"
                wr = f"{m.win_rate * 100:.0f}%"
                pf = f"{m.profit_factor:.1f}" if m.profit_factor > 0 else "—"
                name = strat[:22]
                lines.append(f"{name:<22} {ret:>7} {sharpe:>7} {mdd:>7} {wr:>6} {pf:>6}")

            # Portfolio-Zeile
            if len([s for s in strategies if s in returns_df.columns]) > 1:
                avail = [c for c in returns_df.columns if c in strategies]
                port_ret = returns_df[avail].sum(axis=1)
                pm = calculate_metrics(port_ret)
                lines.append("-" * 60)
                ret = f"{pm.total_return * 100:+.1f}%"
                sharpe = f"{pm.sharpe_ratio:.2f}" if pm.sharpe_ratio != 0 else "—"
                mdd = f"{pm.max_drawdown * 100:.1f}%"
                lines.append(f"{'PORTFOLIO':<22} {ret:>7} {sharpe:>7} {mdd:>7}")

            lines.append("```")

            # Warnungen
            for strat in strategies:
                if strat not in returns_df.columns:
                    continue
                m = calculate_metrics(returns_df[strat])
                baseline = load_baseline(strat)
                if baseline:
                    bl_wr = baseline.get("metrics", {}).get("win_rate", {})
                    if bl_wr and bl_wr.get("warn_below") and m.win_rate < bl_wr["warn_below"]:
                        lines.append(f"  ⚠ {strat}: WR {m.win_rate:.0%} < warn {bl_wr['warn_below']:.0%}")
                if 0 < m.profit_factor < 1.0:
                    lines.append(f"  ⚠ {strat}: PF {m.profit_factor:.1f} < 1.0")

            parts.append("\n".join(lines))

    finally:
        conn.close()

    if not parts:
        return "Keine Performance-Daten verfuegbar."
    return markdown_to_telegram_html("\n\n".join(parts))


# Environment → runtime_name Mapping (DB uses runtime_name, not env name)
_RUNTIME_NAMES = {
    "staging-ibkr": "ibkr-paper",
    "staging-hl": "hl-paper",
    "real-ibkr": "ibkr-one",
    "real2-ibkr": "ibkr-two",
}


def handle_help() -> str:
    """Deterministische Befehlsuebersicht (ersetzt die fruehere Freitext-KI)."""
    return (
        "Wachtel ist ein deterministischer Monitor (kein LLM).\n\n"
        "<b>Monitoring:</b> /status, /summary, /report, /equity, /baseline\n"
        "<b>Fuchs:</b> /fstatus, /fstart, /fstop, /frestart, /emergency, /flogs\n"
        "<b>Sonstiges:</b> /mute, /unmute"
    )


# --- Mute Logic ---

_mute_until: datetime | None = None
_mute_lock = threading.Lock()


def is_muted() -> bool:
    with _mute_lock:
        if _mute_until is None:
            return False
        if datetime.now() >= _mute_until:
            return False
        return True


def set_mute(minutes: int):
    global _mute_until
    with _mute_lock:
        _mute_until = datetime.now() + timedelta(minutes=minutes)


def clear_mute():
    global _mute_until
    with _mute_lock:
        _mute_until = None


# --- Fuchs Process Control Handlers ---


def _run_systemctl(action: str, service: str) -> tuple[int, str]:
    """Run systemctl --user <action> <service> and return (exit_code, output)."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", action, service],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "Timeout nach 30s"
    except Exception as e:
        return 1, str(e)


def _validate_env(env: str) -> str | None:
    """Validate env argument. Returns error message or None if valid."""
    if not env or env not in _FUCHS_SERVICES:
        return f"Unbekanntes Environment. Verfuegbar: {', '.join(_FUCHS_SERVICES.keys())}"
    return None


def _sibling_warning(env: str) -> str:
    """Warn about sibling environments affected by service restart/stop."""
    service = _FUCHS_SERVICES.get(env, "")
    siblings = _SERVICE_ENVS.get(service, [])
    if len(siblings) > 1:
        return f"\nBetrifft ALLE Envs in {service}: {', '.join(siblings)}."
    return ""


def _confirm_buttons(action: str, env: str) -> dict:
    """Build inline keyboard with Ja/Abbrechen buttons."""
    return _inline_keyboard(
        [
            ("\u2705 Ja", f"{action}:{env}:yes"),
            ("\u274c Abbrechen", f"{action}:{env}:no"),
        ]
    )


def handle_fstatus() -> str:
    """Show systemctl status of all Fuchs services."""
    seen = set()
    lines = ["<b>Fuchs Service Status</b>\n"]
    for env, service in _FUCHS_SERVICES.items():
        if service in seen:
            continue
        seen.add(service)
        code, output = _run_systemctl("is-active", service)
        state = output.strip()
        emoji = "\U0001f7e2" if state == "active" else "\U0001f534"
        envs = ", ".join(_SERVICE_ENVS.get(service, []))
        lines.append(f"{emoji} <b>{service}</b> ({envs}): {state}")
    return "\n".join(lines)


def handle_fstart(env: str) -> str:
    """Start a Fuchs service."""
    err = _validate_env(env)
    if err:
        return err
    service = _FUCHS_SERVICES[env]
    code, output = _run_systemctl("start", service)
    if code == 0:
        return f"\U0001f7e2 {service} gestartet."
    return f"\U0001f534 Fehler beim Start von {service}: {output}"


def handle_fstop(env: str) -> tuple[str, dict]:
    """Return confirmation prompt with inline buttons for stopping."""
    err = _validate_env(env)
    if err:
        return err, {}
    service = _FUCHS_SERVICES[env]
    text = f"\u26a0\ufe0f <b>ACHTUNG:</b> Stoppt <b>{service}</b>.{_sibling_warning(env)}"
    return text, _confirm_buttons("fstop", env)


def handle_frestart(env: str) -> tuple[str, dict]:
    """Return confirmation prompt with inline buttons for restarting."""
    err = _validate_env(env)
    if err:
        return err, {}
    service = _FUCHS_SERVICES[env]
    text = f"\u26a0\ufe0f <b>ACHTUNG:</b> Startet <b>{service}</b> neu.{_sibling_warning(env)}"
    return text, _confirm_buttons("frestart", env)


def handle_emergency(env: str) -> tuple[str, dict]:
    """Return confirmation prompt with inline buttons for emergency stop."""
    err = _validate_env(env)
    if err:
        return err, {}
    service = _FUCHS_SERVICES[env]
    config_file = _FUCHS_CONFIGS[env]
    text = (
        f"\U0001f6a8 <b>EMERGENCY STOP</b>\n"
        f"Setzt emergency_stop=true in {config_file} und startet {service} neu.\n"
        f"{env} wird NICHT mehr automatisch gestartet."
        f"{_sibling_warning(env)}"
    )
    return text, _confirm_buttons("emergency", env)


def handle_flogs(env: str) -> str:
    """Show last 20 lines of the latest runtime log for an environment."""
    err = _validate_env(env)
    if err:
        return err
    if env.startswith("real"):
        log_base = Path.home() / "hase" / "werkstatt" / "logs"
    else:
        log_base = Path.home() / "staging" / "werkstatt" / "logs"
    pattern = str(log_base / f"hase_{env}_RUNTIME_*.log")
    files = sorted(glob_mod.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        return f"Keine Logdateien gefunden fuer {env} in {log_base}"
    latest = files[0]
    try:
        result = subprocess.run(["tail", "-20", latest], capture_output=True, text=True, timeout=10)
        log_text = result.stdout.strip()
        if not log_text:
            return f"Logdatei leer: {latest}"
        escaped = html_mod.escape(log_text[:3800])
        return f"<b>Letzte 20 Zeilen</b> ({Path(latest).name}):\n<pre>{escaped}</pre>"
    except Exception as e:
        return f"Fehler beim Lesen: {e}"


def handle_callback(callback_query: dict) -> None:
    """Handle inline keyboard button presses for confirmations."""
    cb_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    message_id = message.get("message_id", 0)

    # Authorize
    chat_id = str(message.get("chat", {}).get("id", ""))
    if chat_id != CHAT_ID:
        answer_callback_query(cb_id, "Nicht autorisiert.")
        return

    parts = data.split(":")
    if len(parts) != 3:
        answer_callback_query(cb_id, "Ungueltige Aktion.")
        return

    action, env, choice = parts

    if choice == "no":
        answer_callback_query(cb_id, "Abgebrochen.")
        edit_message(message_id, "\u274c Abgebrochen.", parse_mode="HTML")
        return

    if choice != "yes":
        answer_callback_query(cb_id, "Ungueltige Antwort.")
        return

    # Execute the confirmed action
    service = _FUCHS_SERVICES.get(env, "")
    if not service:
        answer_callback_query(cb_id, "Unbekanntes Environment.")
        return

    answer_callback_query(cb_id, "Wird ausgefuehrt...")

    if action == "fstop":
        code, output = _run_systemctl("stop", service)
        if code == 0:
            result_text = f"\U0001f534 {service} gestoppt."
        else:
            result_text = f"Fehler beim Stoppen: {output}"

    elif action == "frestart":
        code, output = _run_systemctl("restart", service)
        if code == 0:
            result_text = f"\U0001f7e2 {service} neugestartet."
        else:
            result_text = f"Fehler beim Restart: {output}"

    elif action == "emergency":
        config_path = _hase_root(env) / _FUCHS_CONFIGS[env]
        try:
            with open(config_path) as f:
                config = json.load(f)
            config["environments"][env]["emergency_stop"] = True
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            log.warning(f"emergency_stop set for {env} in {config_path} (server-side edit, sync to repo!)")
        except Exception as e:
            edit_message(message_id, f"Fehler beim Setzen von emergency_stop: {e}", parse_mode="HTML")
            return
        code, output = _run_systemctl("restart", service)
        if code == 0:
            result_text = (
                f"\U0001f6a8 emergency_stop fuer {env} gesetzt. {service} neugestartet.\n"
                f"<i>Config auf Server geaendert — bei Gelegenheit ins Repo uebernehmen.</i>"
            )
        else:
            result_text = f"emergency_stop gesetzt, aber Restart fehlgeschlagen: {output}"
    else:
        result_text = "Unbekannte Aktion."

    edit_message(message_id, result_text, parse_mode="HTML")


# --- Alert Deduplication ---
# Tracks ALL anomaly fingerprints seen since last OK. Only alerts on fingerprints
# that have never been seen before. Prevents flapping when anomalies come and go.

_seen_anomaly_fingerprints: set[str] = set()
_anomaly_lock = threading.Lock()


def _anomaly_fingerprint(line: str) -> str:
    """Extract stable identity from an anomaly line, ignoring variable values.

    '[WARNING] staging-ibkr/carver-scalping: events_delta=0 (min: 5)'
    → 'staging-ibkr/carver-scalping:events_delta'

    '[CRITICAL] real-ibkr/spx-0dte-mon-put: FSM IN_POSITION (expected FLAT/IDLE)'
    → 'real-ibkr/spx-0dte-mon-put:FSM'

    # "Problems:" lines use just the identity to avoid flapping
    # when individual coins go in/out of staleness threshold
    '[WARNING] [staging-hl/crypto-trendconv-v7d] Problems: stale data: atom ..., 1 order(s) unfilled ...'
    → '[staging-hl/crypto-trendconv-v7d] Problems'
    """
    # Strip severity prefix
    m = re.match(r"\[(CRITICAL|WARNING)\]\s*(.*)", line)
    if not m:
        return line
    body = m.group(2)
    # Split at colon: "env/strategy: problem_description"
    parts = body.split(":", 1)
    identity = parts[0].strip()
    if len(parts) > 1:
        detail = parts[1].strip()
        # "Problems:" lines contain comma-separated sub-problems whose details
        # flap as coins cross the staleness threshold. Use just "Problems" as
        # the category so the fingerprint stays stable while any problems exist.
        if identity.endswith("Problems"):
            return identity
        # Other anomaly types: first word is stable enough
        problem = detail.split("=")[0].split()[0] if detail else ""
        return f"{identity}:{problem}"
    return identity


def anomalies_changed(current_alerts: list[str]) -> bool:
    """Return True only if a genuinely NEW anomaly appeared.

    Tracks all fingerprints seen since last OK. A fingerprint that was seen
    before (even if it temporarily resolved and came back) does NOT trigger
    a new alert. Only truly new fingerprints trigger.

    This prevents flapping: if strategy A oscillates between having problems
    and not, it only alerts once — when it first appears.
    """
    with _anomaly_lock:
        current = {_anomaly_fingerprint(a) for a in current_alerts}
        new_fingerprints = current - _seen_anomaly_fingerprints
        _seen_anomaly_fingerprints.update(current)
        return len(new_fingerprints) > 0


def clear_anomaly_state():
    """Clear tracked anomalies (e.g., when precheck returns OK)."""
    with _anomaly_lock:
        _seen_anomaly_fingerprints.clear()


# --- Telegram Poller Thread ---


class TelegramPoller(threading.Thread):
    """Long-polling thread for Telegram updates (messages + callback queries)."""

    def __init__(self, message_queue: queue.Queue, callback_queue: queue.Queue):
        super().__init__(daemon=True, name="telegram-poller")
        self.message_queue = message_queue
        self.callback_queue = callback_queue
        self.offset = 0
        self.running = True

    def run(self):
        log.info("Telegram poller started")
        while self.running:
            try:
                updates = tg_request(
                    "getUpdates",
                    offset=self.offset,
                    timeout=TELEGRAM_POLL_TIMEOUT,
                    allowed_updates=["message", "callback_query"],
                )
                if updates:
                    for update in updates:
                        self.offset = update["update_id"] + 1
                        msg = update.get("message")
                        if msg:
                            self.message_queue.put(msg)
                        cb = update.get("callback_query")
                        if cb:
                            self.callback_queue.put(cb)
            except Exception as e:
                log.error(f"Poller error: {e}")
                time_module.sleep(10)

    def stop(self):
        self.running = False


# --- Main Bot ---


def main():
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set")
        return

    log.info("Wachtel starting...")

    _register_bot_commands()

    msg_queue: queue.Queue = queue.Queue()
    cb_queue: queue.Queue = queue.Queue()

    # Start poller
    poller = TelegramPoller(msg_queue, cb_queue)
    poller.start()

    # Start scheduler (config-getrieben aus schedule.yaml)
    from eule.monitoring.schedule_config import ScheduleConfigError, load_schedule
    from eule.monitoring.scheduler import Scheduler

    try:
        schedule_config = load_schedule()
    except ScheduleConfigError as e:
        log.warning(f"Schedule-Config nicht geladen: {e} — Scheduler deaktiviert")
        schedule_config = None

    if schedule_config:
        scheduler = Scheduler(
            schedule_config,
            alert_callback=send_message,
            email_callback=send_email,
        )
        scheduler.start()
    else:
        scheduler = None

    send_message("Wachtel gestartet. /status fuer aktuellen Check.")
    log.info("Wachtel running")

    try:
        while True:
            # Process callback queries (button presses) first — non-blocking
            while not cb_queue.empty():
                try:
                    cb = cb_queue.get_nowait()
                    log.info(f"Callback: {cb.get('data', '?')}")
                    handle_callback(cb)
                except queue.Empty:
                    break
                except Exception as e:
                    log.error(f"Callback handling error: {e}")

            try:
                msg = msg_queue.get(timeout=2)
            except queue.Empty:
                continue

            # Security: only accept messages from allowed chat
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id != CHAT_ID:
                log.warning(f"Ignoring message from unauthorized chat: {chat_id}")
                continue

            text = msg.get("text", "").strip()
            if not text:
                continue

            log.info(f"Received: {text}")

            # Route commands
            if text.startswith("/status"):
                response = handle_status()
            elif text.startswith("/summary"):
                response = handle_summary()
            elif text.startswith("/mute"):
                parts = text.split()
                minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
                set_mute(minutes)
                response = f"Alerts fuer {minutes} Minuten stummgeschaltet."
            elif text.startswith("/unmute"):
                clear_mute()
                response = "Alerts wieder aktiviert."
            elif text.startswith("/equity"):
                args = text.replace("/equity", "", 1).strip()
                send_message("Chart wird generiert...")
                response = handle_equity(args)
                if response is None:
                    continue  # photo already sent
            elif text.startswith("/report"):
                args = text.replace("/report", "", 1).strip()
                send_message("Report wird erstellt...")
                response = handle_report(args)
            elif text.startswith("/baseline"):
                args = text.replace("/baseline", "", 1).strip()
                response = handle_baseline(args)
            # --- Fuchs Process Control ---
            elif text.startswith("/fstatus"):
                response = handle_fstatus()
            elif text.startswith("/fstart"):
                response = handle_fstart(text.replace("/fstart", "", 1).strip())
            elif text.startswith("/fstop"):
                result = handle_fstop(text.replace("/fstop", "", 1).strip())
                if isinstance(result, tuple):
                    send_message(result[0], parse_mode="HTML", reply_markup=result[1])
                    continue
                response = result
            elif text.startswith("/frestart"):
                result = handle_frestart(text.replace("/frestart", "", 1).strip())
                if isinstance(result, tuple):
                    send_message(result[0], parse_mode="HTML", reply_markup=result[1])
                    continue
                response = result
            elif text.startswith("/emergency"):
                result = handle_emergency(text.replace("/emergency", "", 1).strip())
                if isinstance(result, tuple):
                    send_message(result[0], parse_mode="HTML", reply_markup=result[1])
                    continue
                response = result
            elif text.startswith("/flogs"):
                response = handle_flogs(text.replace("/flogs", "", 1).strip())
            else:
                # Jeder unbekannte Befehl UND jeder Freitext -> deterministische Hilfe.
                response = handle_help()

            log.info(f"Sending response ({len(response)} chars)")
            send_message(response, parse_mode="HTML")

            # "email" / "mail" irgendwo in der Nachricht → Antwort auch per Mail
            if re.search(r"\b(e?-?mail)\b", text, re.IGNORECASE):
                subject = f"Wachtel: {text[:60]}"
                send_email(subject, _report_to_html(response), html=True)
                send_message("(auch per Email gesendet)")

    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        poller.stop()
        if scheduler:
            scheduler.stop()
        log.info("Wachtel stopped")


if __name__ == "__main__":
    main()
