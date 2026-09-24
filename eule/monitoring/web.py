"""
Wachtel Web — Leichtgewichtiges Dashboard fuer Eule.

Stdlib http.server, keine Dependencies. Ruft die gleichen
Datenfunktionen wie die CLI auf und rendert HTML.
"""

import json
import mimetypes
import os
import re
import traceback
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from loguru import logger as log


DEFAULT_PORT = 8780
DEFAULT_BIND = "0.0.0.0"


# ---------------------------------------------------------------------------
# HTML Template Helpers
# ---------------------------------------------------------------------------

_CSS = """\
body { font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 2rem;
       background: #0d1117; color: #c9d1d9; }
h1 { font-size: 1.4rem; color: #58a6ff; margin-bottom: 0.3rem; }
h2 { font-size: 1.1rem; color: #8b949e; margin-top: 2rem; margin-bottom: 0.5rem; }
.meta { color: #8b949e; font-size: 0.85rem; margin-bottom: 1.5rem; }
nav { margin-bottom: 2rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }
nav a { color: #c9d1d9; background: #21262d; padding: 0.4rem 0.8rem;
        border-radius: 6px; text-decoration: none; font-size: 0.85rem; }
nav a:hover, nav a.active { background: #30363d; color: #58a6ff; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; margin-bottom: 1.5rem; }
th { text-align: left; padding: 0.5rem 0.8rem; background: #161b22; color: #8b949e;
     font-weight: 600; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em;
     border-bottom: 1px solid #30363d; }
td { padding: 0.4rem 0.8rem; border-bottom: 1px solid #21262d; }
tr:hover { background: #161b22; }
.r { text-align: right; }
.c { text-align: center; }
.bold { font-weight: 600; color: #f0f6fc; }
.green { color: #3fb950; }
.red { color: #f85149; }
.yellow { color: #d29922; }
.dim { color: #484f58; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
        padding: 1rem 1.5rem; margin-bottom: 1rem; }
.card-title { font-size: 0.75rem; text-transform: uppercase; color: #8b949e;
              letter-spacing: 0.05em; margin-bottom: 0.3rem; }
.card-value { font-size: 1.5rem; font-weight: 600; color: #f0f6fc; }
.cards { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
.error { background: #3d1214; border-color: #f85149; color: #f85149;
         padding: 1rem; border-radius: 6px; margin-bottom: 1rem; }
details { margin-bottom: 0.3rem; }
summary { cursor: pointer; padding: 0.4rem 0; font-size: 0.9rem; }
summary:hover { color: #58a6ff; }
a { color: #58a6ff; }
pre { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
      padding: 0.8rem; overflow-x: auto; font-size: 0.8rem; }
.env { background: #161b22; border: 1px solid #30363d; border-left: 4px solid #30363d;
       border-radius: 8px; padding: 0.8rem 1.2rem 1.2rem; margin-bottom: 1.2rem; }
.env-prod { border-color: #f85149; }
.env h2 { margin-top: 0.5rem; }
.badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px;
         font-size: 0.7rem; letter-spacing: 0.05em; text-transform: uppercase;
         background: #21262d; color: #8b949e; vertical-align: middle; }
.badge-prod { background: #3d1214; color: #f85149; border: 1px solid #f85149; }
.strat { border-top: 1px solid #30363d; padding: 0.9rem 0 0.3rem; }
.strat-name { font-size: 1rem; font-weight: 600; color: #f0f6fc; }
.status-msg { font-size: 1rem; color: #f0f6fc; margin: 0.4rem 0; }
.strat-meta { color: #8b949e; font-size: 0.8rem; margin-bottom: 0.4rem; }
.actions { display: flex; gap: 0.4rem; flex-wrap: wrap; margin: 0.6rem 0 0.2rem; }
.actions form { margin: 0; }
button { font-family: inherit; font-size: 0.85rem; padding: 0.55rem 0.9rem;
         min-height: 2.6rem; border-radius: 6px; background: #21262d; color: #c9d1d9;
         border: 1px solid #30363d; cursor: pointer; }
button:hover { background: #30363d; color: #58a6ff; }
button.danger { border-color: #f85149; color: #f85149; }
button.danger:hover { background: #3d1214; color: #f85149; }
button[disabled] { opacity: 0.35; cursor: not-allowed; }
.ok-block { background: #12261a; border: 1px solid #3fb950; color: #3fb950;
            padding: 1rem; border-radius: 6px; margin-bottom: 1rem; }
.warn-block { background: #2b2411; border: 1px solid #d29922; color: #d29922;
              padding: 1rem; border-radius: 6px; margin-bottom: 1rem; }
"""

_NAV_ITEMS = [
    ("/", "Dashboard"),
    ("/positions", "Positionen"),
    ("/options", "Optionen"),
    ("/allocation", "Allokation"),
    ("/performance", "Performance"),
    ("/schedule", "Schedule"),
    ("/precheck", "Precheck"),
    ("/ep", "EP-Trades"),
    ("/hase", "Hase"),
    ("/docs/", "Docs"),
]


def _page(title: str, content: str, active_path: str = "/") -> str:
    nav = "".join(
        f'<a href="{href}" class="{"active" if href == active_path else ""}">{label}</a>'
        for href, label in _NAV_ITEMS
    )
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return f"""\
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wachtel — {title}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Wachtel Dashboard</h1>
<div class="meta">Stand: {now_str}</div>
<nav>{nav}</nav>
{content}
</body>
</html>"""


def _table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> str:
    """Erzeugt eine HTML-Tabelle. aligns: 'l', 'r', 'c' pro Spalte."""
    aligns = aligns or ["l"] * len(headers)
    cls_map = {"r": "r", "c": "c", "l": ""}
    ths = "".join(f'<th class="{cls_map.get(a, "")}">{h}</th>' for h, a in zip(headers, aligns))
    trs = []
    for row in rows:
        tds = "".join(f'<td class="{cls_map.get(aligns[i], "")}">{cell}</td>'
                      for i, cell in enumerate(row))
        trs.append(f"<tr>{tds}</tr>")
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{''.join(trs)}</tbody></table>"


def _card(title: str, value: str, cls: str = "") -> str:
    return f'<div class="card"><div class="card-title">{title}</div><div class="card-value {cls}">{value}</div></div>'


def _color(val: float, fmt: str = "+,.0f", neutral: float = 0) -> str:
    cls = "green" if val > neutral else "red" if val < neutral else ""
    return f'<span class="{cls}">{val:{fmt}}</span>'


def _error_block(msg: str) -> str:
    return f'<div class="error">{msg}</div>'


# ---------------------------------------------------------------------------
# Page Handlers
# ---------------------------------------------------------------------------


def _page_dashboard() -> str:
    parts = []

    # Portfolio-Ueberblick
    try:
        from eule.bestand.aggregator import aggregate_positions
        from eule.config import load_config
        cfg = load_config()
        snap = aggregate_positions(cfg)

        cards = [_card("Portfolio", f"{snap.total_value_eur:,.0f} EUR")]
        for broker, total in sorted(snap.broker_totals.items()):
            cards.append(_card(broker, f"{total:,.0f} EUR"))
        parts.append(f'<h2>Portfolio</h2><div class="cards">{"".join(cards)}</div>')
    except Exception as e:
        parts.append(_error_block(f"Portfolio: {e}"))

    # Precheck-Status
    try:
        from eule.monitoring.precheck import run_precheck
        exit_code, output = run_precheck()
        labels = {0: ("OK", "green"), 1: ("ANOMALIEN", "red"), 2: ("SUMMARY", "yellow")}
        label, cls = labels.get(exit_code, ("?", ""))
        parts.append(f'<h2>Precheck</h2><div class="cards">{_card("Status", label, cls)}</div>')
        if exit_code == 1:
            parts.append(f"<pre>{output}</pre>")
    except Exception as e:
        parts.append(_error_block(f"Precheck: {e}"))

    # Schedule (letzte Laeufe)
    try:
        from eule.monitoring.scheduler import load_state
        state = load_state()
        if state:
            rows = []
            for name, s in state.items():
                status = s.get("last_status", "")
                cls = "green" if status == "ok" else "red" if status else ""
                last = s.get("last_run", "—")
                if last != "—":
                    try:
                        last = datetime.fromisoformat(last).strftime("%d.%m. %H:%M")
                    except ValueError:
                        pass
                rows.append([f'<span class="bold">{name}</span>',
                             f'<span class="{cls}">{status}</span>', last])
            parts.append("<h2>Letzte Jobs</h2>")
            parts.append(_table(["Job", "Status", "Letzter Lauf"], rows))
    except Exception as e:
        parts.append(_error_block(f"Schedule: {e}"))

    return _page("Dashboard", "\n".join(parts), "/")


def _page_positions() -> str:
    try:
        from eule.bestand.aggregator import aggregate_positions
        from eule.config import load_config
        cfg = load_config()
        snap = aggregate_positions(cfg)
    except Exception as e:
        return _page("Positionen", _error_block(str(e)), "/positions")

    cards = [_card("Gesamt", f"{snap.total_value_eur:,.0f} EUR")]
    for broker, total in sorted(snap.broker_totals.items()):
        cards.append(_card(broker, f"{total:,.0f} EUR"))

    rows = []
    for p in snap.positions:
        pnl = ""
        if p.unrealized_pnl_eur is not None:
            pnl = _color(p.unrealized_pnl_eur)
        current = f"{p.current_price:,.2f}" if p.current_price else "—"
        pct = f"{p.pct_of_portfolio:.1%}" if p.pct_of_portfolio else ""
        rows.append([
            p.broker,
            f'<span class="bold">{p.ticker}</span>',
            p.asset_type, p.direction,
            f"{p.size:,.2f}", f"{p.entry_price:,.2f}",
            current, pnl, pct,
        ])

    content = f'<div class="cards">{"".join(cards)}</div>'
    content += _table(
        ["Broker", "Ticker", "Typ", "Richtung", "Menge", "Entry", "Aktuell", "P&L EUR", "%Port"],
        rows,
        ["l", "l", "l", "l", "r", "r", "r", "r", "r"],
    )

    if snap.errors:
        content += "<h2>Hinweise</h2><ul>" + "".join(f"<li>{e}</li>" for e in snap.errors) + "</ul>"

    return _page("Positionen", content, "/positions")


def _page_options() -> str:
    try:
        from eule.bestand.aggregator import aggregate_positions
        from eule.bestand.options import analyze_options
        from eule.config import load_config
        cfg = load_config()
        snap = aggregate_positions(cfg)
        opt_list, alerts = analyze_options(
            snap.positions,
            expiry_warning_days=cfg.alerts.option_expiry_warning_days,
            fifty_pct_rule=cfg.alerts.fifty_pct_rule,
        )
    except Exception as e:
        return _page("Optionen", _error_block(str(e)), "/options")

    if not opt_list:
        return _page("Optionen", "<p>Keine Option-Positionen.</p>", "/options")

    rows = []
    for o in opt_list:
        dte_cls = ""
        if o.days_to_expiry <= 1:
            dte_cls = "red bold"
        elif o.days_to_expiry <= 3:
            dte_cls = "red"
        elif o.days_to_expiry <= 7:
            dte_cls = "yellow"

        pnl_pct = ""
        if o.sold_premium > 0:
            pct = (o.sold_premium - o.current_value) / o.sold_premium * 100
            pnl_pct = _color(pct, fmt="+.0f")

        expiry = o.expiry.strftime("%d.%m.%Y") if o.expiry else "—"
        rows.append([
            o.broker,
            f'<span class="bold">{o.ticker}</span>',
            o.option_type, o.direction,
            f"{o.strike:,.0f}", expiry,
            f'<span class="{dte_cls}">{o.days_to_expiry}</span>',
            f"{o.sold_premium:,.0f}" if o.sold_premium else "—",
            f"{o.current_value:,.0f}" if o.current_value else "—",
            pnl_pct,
        ])

    content = _table(
        ["Broker", "Ticker", "Typ", "Richtung", "Strike", "Verfall", "DTE", "Praemie", "Aktuell", "P&L%"],
        rows,
        ["l", "l", "l", "l", "r", "l", "r", "r", "r", "r"],
    )

    if alerts:
        content += "<h2>Alerts</h2><ul>"
        for a in alerts:
            cls = "green" if a.alert_type == "fifty_pct" else "yellow" if "warning" in a.alert_type else "red"
            content += f'<li><span class="{cls}">{a.message}</span> — {a.action_suggested}</li>'
        content += "</ul>"

    return _page("Optionen", content, "/options")


def _page_allocation() -> str:
    try:
        from eule.bestand.aggregator import aggregate_positions
        from eule.bestand.allocation import check_allocation
        from eule.config import load_config
        cfg = load_config()
        snap = aggregate_positions(cfg)
        checks, concentration = check_allocation(snap, cfg.allocation)
    except Exception as e:
        return _page("Allokation", _error_block(str(e)), "/allocation")

    cards = [_card("Gesamt", f"{snap.total_value_eur:,.0f} EUR")]

    rows = []
    for c in checks:
        cls = {"ok": "green", "under": "yellow", "over": "red"}.get(c.status, "")
        label = {"ok": "OK", "under": "UNTER", "over": "UEBER"}.get(c.status, "?")
        rows.append([
            f'<span class="bold">{c.category}</span>',
            f"{c.actual_pct:.1%}", f"{c.actual_eur:,.0f}",
            f"{c.target_min:.0%} – {c.target_max:.0%}",
            f'<span class="{cls}">{label}</span>',
        ])

    content = f'<div class="cards">{"".join(cards)}</div>'
    content += _table(
        ["Kategorie", "Ist %", "Ist EUR", "Soll", "Status"],
        rows,
        ["l", "r", "r", "r", "c"],
    )

    if concentration:
        content += "<h2>Konzentrations-Warnungen</h2><ul>"
        for c in concentration:
            content += f'<li class="red">{c.ticker} ({c.broker}): {c.pct:.1%} &gt; Limit {c.limit:.0%}</li>'
        content += "</ul>"

    return _page("Allokation", content, "/allocation")


def _page_schedule() -> str:
    try:
        from eule.betrieb.cli import _build_schedule_rows, _render_html
        timezone, rows = _build_schedule_rows()
    except Exception as e:
        return _page("Schedule", _error_block(str(e)), "/schedule")

    tbl_rows = []
    for r in rows:
        status_cls = ""
        if r["last_status"] == "ok":
            status_cls = "green"
        elif r["last_status"] and r["last_status"] != "ok":
            status_cls = "red"

        enabled = "ja" if r["enabled"] else '<span class="dim">nein</span>'
        last = r["last_run"]
        if status_cls == "red":
            last = f'{last} ({r["last_status"]})'

        tbl_rows.append([
            f'<span class="bold">{r["name"]}</span>',
            r["action"], r["schedule"], enabled,
            f'<span class="{status_cls}">{last}</span>',
            r["next_fire"],
        ])

    content = _table(
        ["Job", "Aktion", "Zeitplan", "Aktiv", "Letzter Lauf", "Naechster Lauf"],
        tbl_rows,
        ["l", "l", "l", "c", "l", "l"],
    )
    content += f'<div class="meta">Timezone: {timezone}</div>'

    return _page("Schedule", content, "/schedule")


def _page_precheck() -> str:
    try:
        from eule.monitoring.precheck import run_precheck
        exit_code, output = run_precheck()
    except Exception as e:
        return _page("Precheck", _error_block(str(e)), "/precheck")

    labels = {0: ("OK", "green"), 1: ("ANOMALIEN", "red"), 2: ("SUMMARY", "yellow")}
    label, cls = labels.get(exit_code, ("?", ""))

    content = f'<div class="cards">{_card("Status", label, cls)}</div>'
    content += f"<pre>{output}</pre>"

    return _page("Precheck", content, "/precheck")


def _page_ep() -> str:
    try:
        from eule.ep.trades import get_active_trades, get_watchlist, _get_filled_data
    except Exception as e:
        return _page("EP-Trades", _error_block(str(e)), "/ep")

    active = get_active_trades()
    watch = get_watchlist()

    content = ""

    if active:
        rows = []
        for t in active:
            shares, price = _get_filled_data(t.id)
            rows.append([
                f'<span class="dim">{t.id}</span>',
                f'<span class="bold">{t.ticker}</span>',
                t.status, f"${price:.2f}", f"${t.stop_plan:.2f}",
                str(shares), t.setup_type,
            ])
        content += f"<h2>Offene Positionen ({len(active)})</h2>"
        content += _table(
            ["ID", "Ticker", "Status", "Entry", "Stop", "Shares", "Setup"],
            rows,
            ["l", "l", "l", "r", "r", "r", "l"],
        )
    else:
        content += '<p class="dim">Keine offenen EP-Positionen.</p>'

    if watch:
        rows = []
        for t in watch:
            rows.append([
                f'<span class="dim">{t.id}</span>',
                f'<span class="bold">{t.ticker}</span>',
                t.setup_type, f"${t.entry_plan:.2f}", f"${t.stop_plan:.2f}",
                str(t.planned_shares), f"${t.risk_total:.0f}",
            ])
        content += f"<h2>Watchlist ({len(watch)})</h2>"
        content += _table(
            ["ID", "Ticker", "Setup", "Entry", "Stop", "Shares", "Risiko"],
            rows,
            ["l", "l", "l", "r", "r", "r", "r"],
        )

    return _page("EP-Trades", content, "/ep")


def _page_performance() -> str:
    try:
        import psycopg

        from eule.db import get_db_url
        from eule.elster.data import (
            filter_active_days,
            get_trading_weekdays,
            list_strategies,
            load_baseline,
            load_daily_pnl,
            load_trades,
            nav_to_returns,
            portfolio_nav_returns,
            trading_periods_per_year,
        )
        from eule.elster.metrics import calculate_metrics
    except ImportError as e:
        return _page("Performance", _error_block(f"Elster nicht verfuegbar: {e}"), "/performance")

    runtime_names = {
        "real-ibkr": "ibkr-one",
        "real2-ibkr": "ibkr-two",
        "staging-ibkr": "ibkr-paper",
        "staging-hl": "hl-paper",
    }

    try:
        db_url = get_db_url("real-ibkr")
        conn = psycopg.connect(db_url, autocommit=True)
    except Exception as e:
        return _page("Performance", _error_block(f"DB-Verbindung: {e}"), "/performance")

    content = ""
    try:
        for env_name, runtime_name in runtime_names.items():
            strategies = list_strategies(conn, runtime_name)
            if not strategies:
                continue

            df = load_daily_pnl(conn, runtime_name, days=30)
            if df.empty:
                content += f'<h2>{env_name}</h2><p class="dim">Keine Daten (30d)</p>'
                continue

            returns_df = nav_to_returns(df)
            if returns_df.empty:
                content += f'<h2>{env_name}</h2><p class="dim">Zu wenig Daten</p>'
                continue

            rows = []
            warnings = []

            for strat in strategies:
                if strat not in returns_df.columns:
                    continue

                # Nur aktive Tage (Return != 0) — Weekday-Config nur fuer Annualisierung
                strat_returns = filter_active_days(returns_df[strat])
                weekdays = get_trading_weekdays(strat)
                ppy = trading_periods_per_year(weekdays) if weekdays else 252

                m = calculate_metrics(strat_returns, periods_per_year=ppy)
                trades_df = load_trades(conn, runtime_name, days=30, strategy_key=strat)

                ret = _color(m.total_return * 100, fmt="+.1f")
                sharpe = f"{m.sharpe_ratio:.2f}" if m.sharpe_ratio != 0 else "—"
                mdd = f"{m.max_drawdown * 100:.1f}%"
                wr = f"{m.win_rate * 100:.0f}%"
                pf_val = m.profit_factor
                pf = f"{pf_val:.1f}" if pf_val > 0 else "—"
                pf_cls = "red" if 0 < pf_val < 1.0 else ""

                rows.append([
                    f'<span class="bold">{strat}</span>',
                    f"{ret}%",
                    sharpe, mdd, wr,
                    f'<span class="{pf_cls}">{pf}</span>',
                    str(len(trades_df)) if trades_df is not None else "—",
                ])

                # Baseline-Warnungen
                baseline = load_baseline(strat)
                if baseline:
                    bl_wr = baseline.get("metrics", {}).get("win_rate", {})
                    if bl_wr and bl_wr.get("warn_below") and m.win_rate < bl_wr["warn_below"]:
                        warnings.append(f'{strat}: WR {m.win_rate:.0%} &lt; warn {bl_wr["warn_below"]:.0%}')
                if 0 < pf_val < 1.0:
                    warnings.append(f"{strat}: PF {pf_val:.1f} &lt; 1.0")

            # Portfolio-Zeile (NAV-basiert, nicht Summe der Einzel-Returns)
            avail = [c for c in returns_df.columns if c in strategies]
            if len(avail) > 1:
                pm = calculate_metrics(portfolio_nav_returns(df))
                rows.append([
                    '<span class="bold">PORTFOLIO</span>',
                    f"{_color(pm.total_return * 100, fmt='+.1f')}%",
                    f"{pm.sharpe_ratio:.2f}" if pm.sharpe_ratio != 0 else "—",
                    f"{pm.max_drawdown * 100:.1f}%",
                    "", "", "",
                ])

            content += f"<h2>{env_name} (30 Tage)</h2>"
            content += _table(
                ["Strategie", "Return", "Sharpe", "MaxDD", "WR", "PF", "Trades"],
                rows,
                ["l", "r", "r", "r", "r", "r", "r"],
            )

            if warnings:
                content += '<div class="error">' + "<br>".join(f"⚠ {w}" for w in warnings) + "</div>"

            # Tages-PnL pro Strategie (nur aktive Tage, min. 4 pro Strategie)
            MIN_PNL_DAYS = 4
            pnl_df_base = load_daily_pnl(conn, runtime_name, days=14)
            if not pnl_df_base.empty and "pnl_net" in pnl_df_base.columns:
                content += f"<h2>{env_name} — Tages-PnL</h2>"
                for strat in sorted(strategies):
                    # Mit 14 Tagen starten, bei Bedarf erweitern
                    strat_pnl = pnl_df_base[pnl_df_base["strategy_key"] == strat].copy()
                    strat_pnl = strat_pnl[strat_pnl["pnl_net"].abs() > 0.005]

                    if len(strat_pnl) < MIN_PNL_DAYS:
                        # Groesseres Fenster laden (7 Wochen deckt auch 1x/Woche ab)
                        wider_df = load_daily_pnl(
                            conn, runtime_name, days=49, strategy_key=strat,
                        )
                        if not wider_df.empty:
                            strat_pnl = wider_df[wider_df["pnl_net"].abs() > 0.005].copy()
                            # Auf die letzten MIN_PNL_DAYS begrenzen
                            strat_pnl = strat_pnl.sort_values("date").tail(MIN_PNL_DAYS)

                    if strat_pnl.empty:
                        continue

                    total = strat_pnl["pnl_net"].sum()
                    n_days = len(strat_pnl)
                    pnl_rows = []
                    for _, row in strat_pnl.sort_values("date", ascending=False).iterrows():
                        dt = row["date"]
                        val = float(row["pnl_net"])
                        day_str = dt.strftime("%a %d.%m.")
                        pnl_rows.append([day_str, _color(val, fmt="+,.0f")])

                    header_text = (
                        f'<span class="bold">{strat}</span> '
                        f'<span class="dim">({n_days}d, gesamt: {_color(total, fmt="+,.0f")})</span>'
                    )
                    content += f"<details><summary>{header_text}</summary>"
                    content += _table(["Datum", "PnL"], pnl_rows, ["l", "r"])
                    content += "</details>"

    finally:
        conn.close()

    if not content:
        content = '<p class="dim">Keine Performance-Daten verfuegbar.</p>'

    return _page("Performance", content, "/performance")


# ---------------------------------------------------------------------------
# Hase-Steuerung (/hase)
# ---------------------------------------------------------------------------

# Lese-Calls sollen die Seite nicht blockieren wenn eine Runtime steht.
HASE_READ_TIMEOUT = 1.5
# Steuer-Calls: die Control-API wartet selbst bis 5s auf den Worker-Thread.
HASE_ACTION_TIMEOUT = 10.0

HASE_ACTIONS = ("pause", "resume", "disable", "combos", "flatten", "kill")
HASE_DESTRUCTIVE_ACTIONS = ("flatten", "kill")

_STRATEGY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _hase_environments() -> dict:
    """Env -> {"port": int, "tier": str}. Single source of truth ist precheck."""
    from eule.monitoring.precheck import ENVIRONMENTS

    return ENVIRONMENTS


def _hase_get(port: int, endpoint: str):
    """GET auf die lokale Runtime-API. None wenn nicht erreichbar."""
    import requests

    try:
        resp = requests.get(f"http://127.0.0.1:{port}{endpoint}", timeout=HASE_READ_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _hase_post(port: int, endpoint: str, body: dict | None = None) -> tuple[int | None, object]:
    """POST auf die lokale Runtime-API.

    Returns (status_code, payload). status_code None = API nicht erreichbar,
    payload ist dann die Fehlermeldung. Sonst geparstes JSON oder roher Text.
    """
    import requests

    try:
        resp = requests.post(
            f"http://127.0.0.1:{port}{endpoint}", json=body, timeout=HASE_ACTION_TIMEOUT
        )
    except Exception as e:
        return None, str(e)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text


def _pretty(payload) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _detail(payload) -> str:
    """Fehlertext aus einer FastAPI-Antwort ({"detail": ...}) ziehen."""
    if isinstance(payload, dict) and "detail" in payload:
        d = payload["detail"]
        return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False, default=str)
    return _pretty(payload)


def validate_hase_action(form: dict) -> str | None:
    """Prueft eine Steuer-Anfrage. Gibt Fehlermeldung zurueck oder None wenn ok."""
    env = form.get("env", "")
    strategy = form.get("strategy", "")
    action = form.get("action", "")

    if env not in _hase_environments():
        return f"Unbekanntes Environment: {env!r}"
    if not _STRATEGY_NAME_RE.match(strategy):
        return f"Ungueltiger Strategie-Name: {strategy!r}"
    if action not in HASE_ACTIONS:
        return f"Unbekannte Aktion: {action!r}"
    if action == "combos":
        value = form.get("value", "")
        if not re.fullmatch(r"\d{1,3}", value):
            return f"Ungueltiger Wert fuer num_combos: {value!r}"
    return None


def _hase_back_link() -> str:
    return '<p><a href="/hase">&larr; zurueck zur Steuerung</a></p>'


def _action_form(env: str, strategy: str, action: str, label: str, cls: str = "",
                 value: str | None = None, confirm: bool = False,
                 disabled: bool = False) -> str:
    """Ein Button = ein Mini-Formular (Aktionen laufen nie ueber GET)."""
    hidden = (
        f'<input type="hidden" name="env" value="{escape(env, quote=True)}">'
        f'<input type="hidden" name="strategy" value="{escape(strategy, quote=True)}">'
        f'<input type="hidden" name="action" value="{escape(action, quote=True)}">'
    )
    if value is not None:
        hidden += f'<input type="hidden" name="value" value="{escape(value, quote=True)}">'
    if confirm:
        hidden += '<input type="hidden" name="confirm" value="yes">'
    dis = " disabled" if disabled else ""
    return (
        f'<form method="post" action="/hase/action">{hidden}'
        f'<button type="submit" class="{cls}"{dis}>{label}</button></form>'
    )


def _render_hase_strategy(env: str, port: int, strat: dict, positions: list, pending: list) -> str:
    """Eine Strategie-Zeile inkl. Live-Stand und Aktions-Buttons."""
    name = strat.get("name") or strat.get("class") or "?"
    display = strat.get("display") or {}
    health = strat.get("health") or {}
    stats = strat.get("stats") or {}
    worker = strat.get("worker") or {}

    fsm = display.get("fsm_state") or "—"
    status_msg = display.get("status_message") or "—"
    next_action = display.get("next_action_time")

    parts = [f'<div class="strat"><div class="strat-name">{escape(str(name))} '
             f'<span class="badge">{escape(str(fsm))}</span></div>']
    parts.append(f'<div class="status-msg">{escape(str(status_msg))}</div>')

    meta = []
    if next_action:
        meta.append(f"naechste Aktion: {escape(str(next_action))}")
    if strat.get("is_active_today") is False:
        meta.append("heute kein Trading-Tag")
    if worker and worker.get("alive") is False:
        meta.append('<span class="red">Worker-Thread tot</span>')
    realized = stats.get("realized_pnl")
    unrealized = stats.get("unrealized_pnl")
    if realized is not None or unrealized is not None:
        meta.append(
            f"PnL heute: realized {_color(float(realized or 0.0), fmt='+,.2f')} / "
            f"unrealized {_color(float(unrealized or 0.0), fmt='+,.2f')}"
        )
    if stats.get("trades_count"):
        meta.append(f"Trades: {stats['trades_count']}")
    if meta:
        parts.append(f'<div class="strat-meta">{" &middot; ".join(meta)}</div>')

    problems = health.get("problems") or []
    if problems:
        parts.append(_error_block("<br>".join(escape(str(p)) for p in problems)))

    # Positionen dieser Strategie (Attribution ueber strategy_key)
    own_pos = [p for p in positions if p.get("strategy_key") == name]
    if own_pos:
        rows = []
        for p in own_pos:
            prod = p.get("product") or {}
            label = p.get("key") or prod.get("instr") or p.get("broker_id") or "?"
            strike = prod.get("strike")
            if strike is not None:
                label = f"{label} {prod.get('option_type', '')} {strike}"
            rows.append([
                escape(str(label)),
                f"{float(p.get('curr_count') or 0.0):,.0f}",
                f"{float(p.get('curr_price') or 0.0):,.2f}",
                _color(float(p.get("daily_pnl") or 0.0), fmt="+,.2f"),
            ])
        parts.append(_table(["Position", "Stueck", "Kurs", "PnL"], rows, ["l", "r", "r", "r"]))

    own_pending = [o for o in pending if o.get("strategy_key") == name]
    if own_pending:
        rows = []
        for o in own_pending:
            rows.append([
                escape(str(o.get("order_id") or "?")),
                escape(str(o.get("instr_key") or "")),
                escape(str(o.get("side") or "")),
                f"{float(o.get('size') or 0.0):,.0f}",
                escape(str(o.get("broker_state") or "")),
            ])
        parts.append(_table(["Order", "Instrument", "Seite", "Menge", "Status"], rows,
                            ["l", "l", "l", "r", "l"]))

    # Aktionen
    buttons = [
        _action_form(env, name, "pause", "Pause"),
        _action_form(env, name, "resume", "Resume"),
        _action_form(env, name, "disable", "Disable"),
    ]

    # Combos +/-: aktuellen Wert und Bounds aus der Control-API holen
    params_info = _hase_get(port, f"/strategy/{name}/params") or {}
    mutable = params_info.get("mutable") or {}
    params = params_info.get("params") or strat.get("params") or {}
    spec = mutable.get("num_combos")
    if spec:
        try:
            current = int(params.get("num_combos"))
        except (TypeError, ValueError):
            current = None
        lo = spec.get("min")
        hi = spec.get("max")
        if current is not None:
            bounds = f" ({lo}–{hi})" if lo is not None and hi is not None else ""
            buttons.append(
                _action_form(env, name, "combos", "&minus;", value=str(current - 1),
                             disabled=lo is not None and current - 1 < lo)
            )
            buttons.append(
                f'<button type="button" disabled>num_combos: {current}{bounds}</button>'
            )
            buttons.append(
                _action_form(env, name, "combos", "+", value=str(current + 1),
                             disabled=hi is not None and current + 1 > hi)
            )

    buttons.append(_action_form(env, name, "flatten", "Flatten…", cls="danger"))
    buttons.append(_action_form(env, name, "kill", "Kill…", cls="danger"))
    parts.append(f'<div class="actions">{"".join(buttons)}</div>')

    parts.append("</div>")
    return "".join(parts)


def _render_hase_env(env_name: str, meta: dict) -> str:
    """Karte fuer ein Environment. Nicht erreichbare API ist kein Fehlerfall."""
    port = meta.get("port")
    is_prod = meta.get("tier") == "production"
    cls = "env env-prod" if is_prod else "env"
    badge = ('<span class="badge badge-prod">Echtgeld</span>' if is_prod
             else f'<span class="badge">{escape(str(meta.get("tier", "")))}</span>')
    head = f'<h2>{escape(env_name)} <span class="dim">:{port}</span> {badge}</h2>'

    strategies = _hase_get(port, "/strategies")
    if strategies is None:
        return (f'<div class="{cls}">{head}'
                '<p class="dim">API nicht erreichbar (Runtime gestoppt?)</p></div>')

    portfolio = _hase_get(port, "/portfolio") or {}
    orders = _hase_get(port, "/debug/orders/pending") or {}
    positions = portfolio.get("positions") or []
    pending = orders.get("haendler_pending") or []

    body = [head]

    cash = portfolio.get("cash") or {}
    pnl = portfolio.get("pnl") or {}
    if cash or pnl:
        currency = cash.get("currency", "")
        cards = [
            _card("Cash", f"{float(cash.get('current_cash') or 0.0):,.0f} {currency}"),
            _card("Realized heute", f"{float(pnl.get('daily_realized_pnl') or 0.0):+,.0f}",
                  "green" if float(pnl.get("daily_realized_pnl") or 0.0) >= 0 else "red"),
            _card("Unrealized heute", f"{float(pnl.get('daily_unrealized_pnl') or 0.0):+,.0f}",
                  "green" if float(pnl.get("daily_unrealized_pnl") or 0.0) >= 0 else "red"),
            _card("Positionen", str(portfolio.get("positions_count", len(positions)))),
        ]
        body.append(f'<div class="cards">{"".join(cards)}</div>')

    if not strategies:
        body.append('<p class="dim">Keine Strategien geladen (Monitoring-Modus?).</p>')
    for strat in strategies:
        body.append(_render_hase_strategy(env_name, port, strat, positions, pending))

    return f'<div class="{cls}">{"".join(body)}</div>'


def _page_hase() -> str:
    envs = _hase_environments()
    parts = [_render_hase_env(name, meta) for name, meta in envs.items()]
    parts.append('<div class="meta">Aktionen wirken sofort auf die laufende Runtime. '
                 "Flatten und Kill zeigen zuerst eine Dry-Run-Vorschau. "
                 "Param-Aenderungen lehnt die Runtime bei offener Position ab (409).</div>")
    return _page("Hase", "\n".join(parts), "/hase")


def _hase_result_page(env: str, strategy: str, action: str, status: int | None, payload) -> str:
    title = f"{action} — {strategy}"
    if status is None:
        body = _error_block(f"API von {escape(env)} nicht erreichbar: {escape(str(payload))}")
    elif status == 200:
        body = (f'<div class="ok-block">OK — <b>{escape(action)}</b> auf '
                f"<b>{escape(strategy)}</b> ({escape(env)})</div>")
        body += f"<pre>{escape(_pretty(payload))}</pre>"
    elif status == 409:
        body = (f'<div class="warn-block">Im aktuellen Zustand nicht erlaubt (409): '
                f"{escape(_detail(payload))}</div>")
    else:
        body = _error_block(f"Fehler {status}: {escape(_detail(payload))}")
    return _page(title, body + _hase_back_link(), "/hase")


def _hase_dryrun_page(env: str, strategy: str, action: str, status: int | None, payload) -> str:
    """Vorschau-Seite fuer flatten/kill mit Bestaetigungs-Formular."""
    if status != 200:
        return _hase_result_page(env, strategy, action, status, payload)

    data = payload if isinstance(payload, dict) else {}
    inner = data.get("flatten") if action == "kill" and isinstance(data.get("flatten"), dict) else data
    to_close = inner.get("positions_to_close") or []
    to_cancel = inner.get("pending_to_cancel") or []

    body = [f'<div class="warn-block">Vorschau (dry run) fuer <b>{escape(action)}</b> auf '
            f"<b>{escape(strategy)}</b> in <b>{escape(env)}</b>. Es wurde noch nichts "
            "ausgefuehrt.</div>"]

    if to_close:
        rows = [[escape(str(p.get("key") or p.get("broker_id") or "?")),
                 escape(str(p.get("side") or "")),
                 f"{float(p.get('size') or 0.0):,.0f}"] for p in to_close]
        body.append("<h2>Positionen die geschlossen werden</h2>")
        body.append(_table(["Position", "Gegen-Seite", "Menge"], rows, ["l", "l", "r"]))
    if to_cancel:
        rows = [[escape(str(o.get("order_id") or "?")),
                 escape(str(o.get("side") or "")),
                 f"{float(o.get('size') or 0.0):,.0f}"] for o in to_cancel]
        body.append("<h2>Orders die gecancelt werden</h2>")
        body.append(_table(["Order", "Seite", "Menge"], rows, ["l", "l", "r"]))
    if not to_close and not to_cancel:
        body.append('<p class="dim">Nichts zu tun — keine offenen Positionen, keine '
                    "pending Orders.</p>")
    if action == "kill":
        body.append('<p>Zusaetzlich wird die Strategie fuer den Rest des Tages '
                    "<b>disabled</b>.</p>")

    body.append(f"<pre>{escape(_pretty(payload))}</pre>")
    body.append('<div class="actions">')
    body.append(_action_form(env, strategy, action, f"{action} jetzt ausfuehren",
                             cls="danger", confirm=True))
    body.append('</div>')
    body.append(_hase_back_link())
    return _page(f"{action} — Vorschau", "".join(body), "/hase")


def handle_hase_action(form: dict) -> str:
    """Fuehrt eine Steuer-Aktion aus und rendert die Ergebnis-Seite."""
    err = validate_hase_action(form)
    if err:
        return _page("Steuerung", _error_block(escape(err)) + _hase_back_link(), "/hase")

    env = form["env"]
    strategy = form["strategy"]
    action = form["action"]
    port = _hase_environments()[env]["port"]

    if action == "combos":
        status, payload = _hase_post(port, f"/strategy/{strategy}/params",
                                     body={"num_combos": int(form["value"])})
        return _hase_result_page(env, strategy, action, status, payload)

    if action in HASE_DESTRUCTIVE_ACTIONS and form.get("confirm") != "yes":
        status, payload = _hase_post(port, f"/strategy/{strategy}/{action}?dry_run=true")
        return _hase_dryrun_page(env, strategy, action, status, payload)

    status, payload = _hase_post(port, f"/strategy/{strategy}/{action}")
    return _hase_result_page(env, strategy, action, status, payload)


# ---------------------------------------------------------------------------
# Statische Docs (/docs/)
# ---------------------------------------------------------------------------

DEFAULT_DOCS_DIR = "/srv/hase/docs-site"


def docs_dir() -> Path:
    """Basis-Verzeichnis der gebauten MkDocs-Site."""
    return Path(os.environ.get("EULE_DOCS_DIR", DEFAULT_DOCS_DIR))


def resolve_docs_path(rel_path: str, base: Path | None = None) -> Path | None:
    """Loest einen /docs/-Request auf eine Datei auf.

    None wenn das Ziel ausserhalb von `base` liegt (Traversal, Symlink) oder
    nicht existiert. Verzeichnisse werden auf index.html abgebildet.
    """
    base = base or docs_dir()
    try:
        base_res = base.resolve()
    except OSError:
        return None
    if not base_res.is_dir():
        return None

    rel = unquote(rel_path).lstrip("/")
    try:
        target = (base_res / rel).resolve()
    except OSError:
        return None
    if target != base_res and not target.is_relative_to(base_res):
        return None
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        return None
    return target


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------

ROUTES: dict[str, callable] = {
    "/": _page_dashboard,
    "/positions": _page_positions,
    "/options": _page_options,
    "/allocation": _page_allocation,
    "/performance": _page_performance,
    "/schedule": _page_schedule,
    "/precheck": _page_precheck,
    "/ep": _page_ep,
    "/hase": _page_hase,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        raw_path = urlparse(self.path).path

        if raw_path == "/docs" or raw_path.startswith("/docs/"):
            self._serve_docs(raw_path[len("/docs"):])
            return

        path = raw_path.rstrip("/") or "/"
        handler = ROUTES.get(path)

        if handler is None:
            self.send_error(404)
            return

        try:
            self._send_html(handler())
        except Exception:
            self._send_error_page(path)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path != "/hase/action":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
            form = {k: v[0] for k, v in parse_qs(body).items()}
            self._send_html(handle_hase_action(form))
        except Exception:
            self._send_error_page(path)

    def _send_html(self, html: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _send_error_page(self, path: str):
        tb = traceback.format_exc()
        log.error(f"Web handler error for {path}: {tb}")
        self._send_html(_page("Fehler", _error_block(f"<pre>{escape(tb)}</pre>"), path), 500)

    def _serve_docs(self, rel_path: str):
        """Statisches Fileserving der gebauten MkDocs-Site."""
        base = docs_dir()
        if not base.is_dir():
            self.send_error(404, "Docs nicht verfuegbar",
                            f"Verzeichnis {base} fehlt (EULE_DOCS_DIR).")
            return

        target = resolve_docs_path(rel_path, base)
        if target is None:
            self.send_error(404)
            return

        ctype, _ = mimetypes.guess_type(target.name)
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"

        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        log.info(f"Web: {args[0]}")


def serve(port: int = DEFAULT_PORT, bind: str = DEFAULT_BIND):
    """Startet den Wachtel Web-Server."""
    server = HTTPServer((bind, port), Handler)
    log.info(f"Wachtel Web auf http://{bind}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Web-Server gestoppt")
    finally:
        server.server_close()
