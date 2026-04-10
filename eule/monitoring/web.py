"""
Wachtel Web — Leichtgewichtiges Dashboard fuer Eule.

Stdlib http.server, keine Dependencies. Ruft die gleichen
Datenfunktionen wie die CLI auf und rendert HTML.
"""

import json
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from loguru import logger as log


DEFAULT_PORT = 8780


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
            # Trading-Wochentage pro Strategie cachen
            strat_weekdays: dict[str, list[int] | None] = {}
            for strat in strategies:
                strat_weekdays[strat] = get_trading_weekdays(strat)

            for strat in strategies:
                if strat not in returns_df.columns:
                    continue

                # Returns auf konfigurierte Trading-Tage filtern
                strat_returns = returns_df[strat]
                weekdays = strat_weekdays[strat]
                ppy = 252
                if weekdays:
                    strat_returns = filter_trading_days(strat_returns, weekdays)
                    ppy = trading_periods_per_year(weekdays)

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

            # Portfolio-Zeile
            avail = [c for c in returns_df.columns if c in strategies]
            if len(avail) > 1:
                port_ret = returns_df[avail].sum(axis=1)
                pm = calculate_metrics(port_ret)
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

            # Tages-PnL pro Strategie (14 Tage, nur Trading-Tage)
            pnl_df = load_daily_pnl(conn, runtime_name, days=14)
            if not pnl_df.empty and "pnl_net" in pnl_df.columns:
                content += f"<h2>{env_name} — Tages-PnL (14d)</h2>"
                for strat in sorted(strategies):
                    strat_pnl = pnl_df[pnl_df["strategy_key"] == strat].copy()
                    if strat_pnl.empty:
                        continue

                    weekdays = strat_weekdays.get(strat)
                    if weekdays:
                        strat_pnl = strat_pnl[
                            pd.DatetimeIndex(strat_pnl["date"]).weekday.isin(weekdays)
                        ]
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
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        handler = ROUTES.get(path)

        if handler is None:
            self.send_error(404)
            return

        try:
            html = handler()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        except Exception:
            tb = traceback.format_exc()
            log.error(f"Web handler error for {path}: {tb}")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_page("Fehler", _error_block(f"<pre>{tb}</pre>"), path).encode("utf-8"))

    def log_message(self, format, *args):
        log.info(f"Web: {args[0]}")


def serve(port: int = DEFAULT_PORT):
    """Startet den Wachtel Web-Server."""
    server = HTTPServer(("0.0.0.0", port), Handler)
    log.info(f"Wachtel Web auf http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Web-Server gestoppt")
    finally:
        server.server_close()
