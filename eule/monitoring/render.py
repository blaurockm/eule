"""
Renderer fuer Wachtel-Meldungen (Telegram + Email).

Telegram: schmale Layouts ohne breite Spaltentabellen — normale Textzeilen
mit <b>/<code>, damit der Client auf dem Handy sauber umbricht.
Email: vollstaendige HTML-Dokumente mit Tabellen (inline CSS, email-safe).

Datenquelle der Dailys sind die daily-summary-*.json Files von Hase
(Schema: env, date, timestamp, strategies[], portfolio{}, positions[],
fsm_states{}, optional warnings[]).
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

_WEEKDAYS_DE_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

_SEVERITY_EMOJI = {"CRITICAL": "\U0001f534", "WARNING": "\U0001f7e1"}  # 🔴 🟡


def _esc(text: str) -> str:
    """HTML-Escaping fuer Telegram-HTML und Email-HTML."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_money(value: float) -> str:
    return f"{value:+,.2f}"


def _date_de(date_str: str) -> str:
    """'2026-07-02' -> 'Do 02.07.2026'. Unparsebare Strings unveraendert."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    return f"{_WEEKDAYS_DE_SHORT[d.weekday()]} {d.strftime('%d.%m.%Y')}"


def env_warnings(data: dict) -> list[str]:
    """Datenqualitaets-Warnungen eines Envs als Textzeilen.

    Hase schreibt optional ein 'warnings'-Feld (Liste). Kein Schema-Raten:
    Dict-Warnungen werden ueber 'message'/'msg' gerendert, sonst als JSON;
    alles andere per str(). Ist affects_pnl gesetzt, wird eine Zeile
    angehaengt, die den Daily-PnL als potenziell unzuverlaessig markiert.
    """
    import json

    lines: list[str] = []
    affects_pnl = False
    for w in data.get("warnings") or []:
        if isinstance(w, dict):
            msg = w.get("message") or w.get("msg") or json.dumps(w, default=str)
            if w.get("affects_pnl"):
                affects_pnl = True
        else:
            msg = str(w)
        lines.append(msg)
    if affects_pnl:
        lines.append("Daily-PnL POTENZIELL UNZUVERLAESSIG")
    return lines


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def render_env_daily_telegram(data: dict) -> str:
    """Per-Env Daily als kurze Telegram-Nachricht (HTML, kein <pre>).

    Aktive Strategien einzeln mit PnL/Trades/FSM, inaktive zusammengefasst
    in einer Zeile — das ist der groesste Lesbarkeitsgewinn gegenueber dem
    alten Spalten-Dump.
    """
    env = data.get("env", "?")
    date_str = data.get("date", "?")
    portfolio = data.get("portfolio", {}) or {}
    fsm_states = data.get("fsm_states", {}) or {}

    daily_pnl = portfolio.get("daily_pnl", 0.0) or 0.0
    equity = portfolio.get("equity")
    lines = [f"\U0001f989 <b>Daily {_esc(env)}</b> — {_esc(_date_de(date_str))}"]
    pnl_line = f"PnL <b>{_fmt_money(daily_pnl)}</b>"
    if equity is not None:
        pnl_line += f" · Equity {equity:,.0f}"
    lines.append(pnl_line)

    warnings = env_warnings(data)
    if warnings:
        lines.append("")
        lines.append("⚠ <b>Warnungen:</b>")
        lines.extend(f"• {_esc(w)}" for w in warnings)

    active: list[str] = []
    inactive: list[str] = []
    for s in data.get("strategies", []):
        name = s.get("name", "?")
        stats = s.get("stats", {}) or {}
        if s.get("is_active_today", True):
            rpnl = stats.get("realized_pnl", 0.0) or 0.0
            trades = stats.get("trades_count", 0) or 0
            fsm = fsm_states.get(name, "?")
            detail = f"{_fmt_money(rpnl)}"
            if trades:
                detail += f" ({trades} Trades)"
            active.append(f"• {_esc(name)} {detail} — {_esc(fsm)}")
        else:
            inactive.append(name)

    if active:
        lines.append("")
        lines.append("<b>Aktiv heute:</b>")
        lines.extend(active)
    if inactive:
        lines.append("")
        lines.append(f"<b>Inaktiv heute:</b> {_esc(', '.join(inactive))}")

    positions = data.get("positions") or []
    if positions:
        pos_str = ", ".join(
            f"{p.get('name', '?')} ×{p.get('size', 0):g}" for p in positions
        )
        lines.append("")
        lines.append(f"<b>Positionen:</b> {_esc(pos_str)}")

    return "\n".join(lines)


_ANOMALY_RE = re.compile(r"\[(CRITICAL|WARNING)\]\s*(?:\[([^\]]+)\]\s*)?(.*)")


def parse_anomaly_line(line: str) -> tuple[str, str, str]:
    """'[WARNING] [env/strat] msg' -> (severity, scope, msg).

    Zeilen ohne Scope-Klammer ([host]-Zeilen haben eine) bekommen scope ''.
    Unparsebare Zeilen landen komplett in msg (severity 'WARNING').
    """
    m = _ANOMALY_RE.match(line.strip())
    if not m:
        return "WARNING", "", line.strip()
    return m.group(1), m.group(2) or "", m.group(3).strip()


def render_alert_telegram(anomaly_lines: list[str]) -> str:
    """Anomalie-Alert fuer Telegram: pro Env gruppiert, 🔴/🟡, ohne <pre>."""
    tz = ZoneInfo("Europe/Berlin")
    ts = datetime.now(tz).strftime("%H:%M")
    n = len(anomaly_lines)
    lines = [f"\U0001f6a8 <b>{n} Anomalie{'n' if n != 1 else ''}</b> — {ts}"]

    by_env: dict[str, list[tuple[str, str, str]]] = {}
    for raw in anomaly_lines:
        sev, scope, msg = parse_anomaly_line(raw)
        env, _, strat = scope.partition("/")
        by_env.setdefault(env or "?", []).append((sev, strat, msg))

    for env, items in by_env.items():
        lines.append("")
        lines.append(f"<b>{_esc(env)}</b>")
        for sev, strat, msg in items:
            emoji = _SEVERITY_EMOJI.get(sev, "❓")
            prefix = f"{emoji} <b>{_esc(strat)}</b>: " if strat else f"{emoji} "
            lines.append(f"{prefix}{_esc(msg)}")

    return "\n".join(lines)


def render_weekly_telegram(envs_data: list[dict]) -> str:
    """Weekly-Performance fuer Telegram: pro Strategie kurze Zeilen statt
    60-Zeichen-Tabelle."""
    lines = ["\U0001f4ca <b>Weekly Report</b> (7 Tage)"]
    for env in envs_data:
        lines.append("")
        lines.append(f"<b>{_esc(env['env'])}</b>")
        note = env.get("note")
        if note:
            lines.append(f"  {_esc(note)}")
            continue
        for row in env.get("rows", []):
            lines.append(f"• {_esc(row['strategy'])}")
            lines.append(
                f"  Ret {row['total_return'] * 100:+.1f}% · "
                f"Sharpe {_fmt_metric(row['sharpe'])} · "
                f"DD {row['max_drawdown'] * 100:.1f}%"
            )
            lines.append(
                f"  WR {row['win_rate'] * 100:.0f}% · PF {_fmt_metric(row['profit_factor'])}"
            )
        port = env.get("portfolio")
        if port:
            lines.append(
                f"Σ <b>Portfolio</b> {port['total_return'] * 100:+.1f}% · "
                f"Sharpe {_fmt_metric(port['sharpe'])} · "
                f"DD {port['max_drawdown'] * 100:.1f}%"
            )
        for warn in env.get("warnings", []):
            lines.append(f"⚠ {_esc(warn)}")
    return "\n".join(lines)


def _fmt_metric(value: float) -> str:
    """Metrik-Wert oder '—' wenn nicht berechenbar (0/negativ-PF-Konvention)."""
    return f"{value:.2f}" if value > 0 else "—"


# ---------------------------------------------------------------------------
# Email (HTML)
# ---------------------------------------------------------------------------

_EMAIL_STYLE = {
    "body": "font-family: Arial, sans-serif; max-width: 720px; margin: 0 auto; "
            "padding: 20px; color: #1a1a1a; line-height: 1.5;",
    "h2": "color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 8px;",
    "h3": "color: #333; margin: 24px 0 8px 0;",
    "table": "border-collapse: collapse; width: 100%; font-size: 13px;",
    "th": "text-align: left; padding: 6px 10px; background: #f0f0f0; "
          "border-bottom: 2px solid #ccc;",
    "th_num": "text-align: right; padding: 6px 10px; background: #f0f0f0; "
              "border-bottom: 2px solid #ccc;",
    "td": "padding: 5px 10px; border-bottom: 1px solid #e5e5e5;",
    "td_num": "padding: 5px 10px; border-bottom: 1px solid #e5e5e5; "
              "text-align: right; font-variant-numeric: tabular-nums;",
    "warn": "background: #fff8e6; border-left: 4px solid #e6a700; "
            "padding: 8px 12px; margin: 8px 0;",
    "crit": "background: #fdeaea; border-left: 4px solid #cc2222; "
            "padding: 8px 12px; margin: 8px 0;",
    "muted": "color: #999; font-size: 11px;",
    "kpi": "display: inline-block; margin: 0 24px 8px 0;",
    "kpi_label": "color: #777; font-size: 11px; text-transform: uppercase;",
    "kpi_value": "font-size: 18px; font-weight: bold;",
}


def _pnl_color(value: float) -> str:
    if value > 0:
        return "#1a7a2e"
    if value < 0:
        return "#cc2222"
    return "#1a1a1a"


def _email_document(title: str, body: str) -> str:
    return (
        f"<html><body style='{_EMAIL_STYLE['body']}'>"
        f"<h2 style='{_EMAIL_STYLE['h2']}'>{_esc(title)}</h2>"
        f"{body}"
        f"<hr style='border: none; border-top: 1px solid #ddd; margin-top: 24px;'>"
        f"<p style='{_EMAIL_STYLE['muted']}'>Wachtel Monitoring</p>"
        f"</body></html>"
    )


def _kpi(label: str, value: str, color: str = "#1a1a1a") -> str:
    return (
        f"<span style='{_EMAIL_STYLE['kpi']}'>"
        f"<span style='{_EMAIL_STYLE['kpi_label']}'>{_esc(label)}</span><br>"
        f"<span style='{_EMAIL_STYLE['kpi_value']} color: {color};'>{_esc(value)}</span>"
        f"</span>"
    )


def _env_daily_email_section(data: dict) -> str:
    """Eine Env-Sektion der Daily-Email: KPIs, Warnungen, Strategie-Tabelle."""
    env = data.get("env", "?")
    portfolio = data.get("portfolio", {}) or {}
    fsm_states = data.get("fsm_states", {}) or {}
    daily_pnl = portfolio.get("daily_pnl", 0.0) or 0.0
    realized = portfolio.get("realized", 0.0) or 0.0
    unrealized = portfolio.get("unrealized", 0.0) or 0.0

    parts = [f"<h3 style='{_EMAIL_STYLE['h3']}'>{_esc(env)}</h3>"]
    parts.append("<div>")
    parts.append(_kpi("PnL heute", _fmt_money(daily_pnl), _pnl_color(daily_pnl)))
    parts.append(_kpi("Realized", _fmt_money(realized), _pnl_color(realized)))
    parts.append(_kpi("Unrealized", _fmt_money(unrealized), _pnl_color(unrealized)))
    if "equity" in portfolio:
        parts.append(_kpi("Equity", f"{portfolio['equity']:,.0f}"))
    if "cash" in portfolio:
        parts.append(_kpi("Cash", f"{portfolio['cash']:,.0f}"))
    parts.append("</div>")

    for w in env_warnings(data):
        parts.append(f"<div style='{_EMAIL_STYLE['warn']}'>⚠ {_esc(w)}</div>")

    strategies = data.get("strategies", [])
    if strategies:
        s = _EMAIL_STYLE
        rows = [
            f"<tr><th style='{s['th']}'>Strategie</th><th style='{s['th']}'>FSM</th>"
            f"<th style='{s['th_num']}'>Trades</th><th style='{s['th_num']}'>rPnL</th>"
            f"<th style='{s['th_num']}'>uPnL</th><th style='{s['th_num']}'>PF</th>"
            f"<th style='{s['th_num']}'>Kommission</th></tr>"
        ]
        for strat in strategies:
            name = strat.get("name", "?")
            stats = strat.get("stats", {}) or {}
            fsm = fsm_states.get(name, "?")
            rpnl = stats.get("realized_pnl", 0.0) or 0.0
            upnl = stats.get("unrealized_pnl", 0.0) or 0.0
            pf = stats.get("profit_factor", 0.0) or 0.0
            active = strat.get("is_active_today", True)
            name_html = _esc(name) if active else f"{_esc(name)} <span style='color:#999'>(inaktiv)</span>"
            rows.append(
                f"<tr><td style='{s['td']}'>{name_html}</td>"
                f"<td style='{s['td']}'>{_esc(fsm)}</td>"
                f"<td style='{s['td_num']}'>{stats.get('trades_count', 0)}</td>"
                f"<td style='{s['td_num']} color: {_pnl_color(rpnl)};'>{_fmt_money(rpnl)}</td>"
                f"<td style='{s['td_num']} color: {_pnl_color(upnl)};'>{_fmt_money(upnl)}</td>"
                f"<td style='{s['td_num']}'>{_fmt_metric(pf)}</td>"
                f"<td style='{s['td_num']}'>{stats.get('total_commissions', 0.0):.2f}</td></tr>"
            )
        parts.append(f"<table style='{s['table']}'>{''.join(rows)}</table>")

    positions = data.get("positions") or []
    if positions:
        pos_str = ", ".join(f"{p.get('name', '?')} ×{p.get('size', 0):g}" for p in positions)
        parts.append(f"<p><b>Positionen:</b> {_esc(pos_str)}</p>")

    return "".join(parts)


def render_daily_email_html(
    summaries: dict[str, dict],
    missing: list[str],
    open_anomalies: list[str],
    date_str: str,
) -> str:
    """Gesamt-Daily als HTML-Email: alle Envs, fehlende explizit, offene Anomalien."""
    parts = []

    if missing:
        parts.append(
            f"<div style='{_EMAIL_STYLE['crit']}'>"
            f"⚠ Kein EOD-JSON von: <b>{_esc(', '.join(missing))}</b> — "
            f"Envs haben den Handelstag nicht abgeschlossen.</div>"
        )

    for env in sorted(summaries):
        parts.append(_env_daily_email_section(summaries[env]))

    if open_anomalies:
        parts.append(f"<h3 style='{_EMAIL_STYLE['h3']}'>Offene Anomalien</h3>")
        for line in open_anomalies:
            sev, _, _ = parse_anomaly_line(line)
            style = _EMAIL_STYLE["crit"] if sev == "CRITICAL" else _EMAIL_STYLE["warn"]
            parts.append(f"<div style='{style}'><code>{_esc(line)}</code></div>")

    return _email_document(f"Wachtel Daily — {_date_de(date_str)}", "".join(parts))


def render_anomaly_email_html(anomaly_lines: list[str], precheck_output: str, ts: str) -> str:
    """Anomalie-Email: Anomalien einmal prominent, darunter der Live-Status.

    precheck_output ist der volle run_precheck-Output; der ANOMALIES-Block
    daraus wird weggeschnitten (sonst stuenden die Anomalien doppelt drin).
    """
    parts = []
    for line in anomaly_lines:
        sev, _, _ = parse_anomaly_line(line)
        style = _EMAIL_STYLE["crit"] if sev == "CRITICAL" else _EMAIL_STYLE["warn"]
        parts.append(f"<div style='{style}'><code>{_esc(line)}</code></div>")

    live_status = precheck_output.split("ANOMALIES DETECTED:")[0].strip()
    if live_status:
        parts.append(f"<h3 style='{_EMAIL_STYLE['h3']}'>Live-Status</h3>")
        parts.append(
            f"<pre style='background: #f4f4f4; padding: 12px; font-size: 12px; "
            f"overflow-x: auto; border-radius: 4px;'>{_esc(live_status)}</pre>"
        )

    return _email_document(f"Wachtel Anomalie — {ts}", "".join(parts))


def render_weekly_email_html(envs_data: list[dict], date_str: str) -> str:
    """Weekly-Performance als HTML-Email mit Tabellen pro Env."""
    s = _EMAIL_STYLE
    parts = []
    for env in envs_data:
        parts.append(f"<h3 style='{s['h3']}'>{_esc(env['env'])} (7 Tage)</h3>")
        note = env.get("note")
        if note:
            parts.append(f"<p>{_esc(note)}</p>")
            continue
        rows = [
            f"<tr><th style='{s['th']}'>Strategie</th><th style='{s['th_num']}'>Return</th>"
            f"<th style='{s['th_num']}'>Sharpe</th><th style='{s['th_num']}'>MaxDD</th>"
            f"<th style='{s['th_num']}'>WR</th><th style='{s['th_num']}'>PF</th></tr>"
        ]
        for row in env.get("rows", []):
            ret = row["total_return"]
            rows.append(
                f"<tr><td style='{s['td']}'>{_esc(row['strategy'])}</td>"
                f"<td style='{s['td_num']} color: {_pnl_color(ret)};'>{ret * 100:+.1f}%</td>"
                f"<td style='{s['td_num']}'>{_fmt_metric(row['sharpe'])}</td>"
                f"<td style='{s['td_num']}'>{row['max_drawdown'] * 100:.1f}%</td>"
                f"<td style='{s['td_num']}'>{row['win_rate'] * 100:.0f}%</td>"
                f"<td style='{s['td_num']}'>{_fmt_metric(row['profit_factor'])}</td></tr>"
            )
        port = env.get("portfolio")
        if port:
            ret = port["total_return"]
            rows.append(
                f"<tr><td style='{s['td']}'><b>PORTFOLIO</b></td>"
                f"<td style='{s['td_num']} color: {_pnl_color(ret)};'><b>{ret * 100:+.1f}%</b></td>"
                f"<td style='{s['td_num']}'>{_fmt_metric(port['sharpe'])}</td>"
                f"<td style='{s['td_num']}'>{port['max_drawdown'] * 100:.1f}%</td>"
                f"<td style='{s['td_num']}'></td><td style='{s['td_num']}'></td></tr>"
            )
        parts.append(f"<table style='{s['table']}'>{''.join(rows)}</table>")
        for warn in env.get("warnings", []):
            parts.append(f"<div style='{s['warn']}'>⚠ {_esc(warn)}</div>")

    if not parts:
        parts.append("<p>Keine Performance-Daten verfuegbar.</p>")

    return _email_document(f"Wachtel Weekly Report — {date_str}", "".join(parts))
