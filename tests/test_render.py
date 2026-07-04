"""Tests fuer die Telegram-/Email-Renderer in monitoring/render.py."""

from eule.monitoring.render import (
    parse_anomaly_line,
    render_alert_telegram,
    render_anomaly_email_html,
    render_daily_email_html,
    render_env_daily_telegram,
    render_weekly_email_html,
    render_weekly_telegram,
)


def _daily_data(**overrides):
    data = {
        "env": "staging-ibkr",
        "date": "2026-07-02",
        "portfolio": {
            "cash": 1011558.45,
            "equity": 1011563.62,
            "realized": 355.90,
            "unrealized": 0.0,
            "daily_pnl": 355.90,
        },
        "strategies": [
            {
                "name": "spx-0dte-always",
                "is_active_today": True,
                "stats": {"realized_pnl": 97.95, "unrealized_pnl": 0, "trades_count": 3,
                          "profit_factor": 1.36, "total_commissions": 1.03},
            },
            {
                "name": "gld-1dte-thu-put",
                "is_active_today": False,
                "stats": {"realized_pnl": 0.0, "unrealized_pnl": 0, "trades_count": 0,
                          "profit_factor": 0.0, "total_commissions": 0.0},
            },
        ],
        "fsm_states": {"spx-0dte-always": "FLAT", "gld-1dte-thu-put": "FLAT"},
        "positions": [{"name": "IBKR", "size": 100.0, "pnl": 1669.75, "strategy": None}],
    }
    data.update(overrides)
    return data


# --- render_env_daily_telegram ---

def test_env_daily_telegram_basics():
    out = render_env_daily_telegram(_daily_data())
    assert "Daily staging-ibkr" in out
    assert "Do 02.07.2026" in out
    assert "+355.90" in out
    # aktive Strategie einzeln, inaktive zusammengefasst
    assert "spx-0dte-always +97.95 (3 Trades) — FLAT" in out
    assert "Inaktiv heute:</b> gld-1dte-thu-put" in out
    assert "IBKR ×100" in out
    # kein <pre>-Block — normale Zeilen brechen auf dem Handy sauber um
    assert "<pre>" not in out


def test_env_daily_telegram_warnings_and_escaping():
    data = _daily_data(warnings=[{"message": "gap < 5min", "affects_pnl": True}])
    out = render_env_daily_telegram(data)
    assert "gap &lt; 5min" in out
    assert "POTENZIELL UNZUVERLAESSIG" in out


def test_env_daily_telegram_no_sections_when_empty():
    data = _daily_data(strategies=[], positions=[], fsm_states={})
    out = render_env_daily_telegram(data)
    assert "Aktiv heute" not in out
    assert "Positionen" not in out


# --- Anomalie-Parsing + Alert-Rendering ---

def test_parse_anomaly_line_variants():
    assert parse_anomaly_line("[WARNING] [staging-ibkr/carver] Low activity: x=0") == (
        "WARNING", "staging-ibkr/carver", "Low activity: x=0"
    )
    assert parse_anomaly_line("[CRITICAL] [host] Disk 96% belegt") == (
        "CRITICAL", "host", "Disk 96% belegt"
    )
    sev, scope, msg = parse_anomaly_line("garbage line")
    assert (sev, scope, msg) == ("WARNING", "", "garbage line")


def test_render_alert_telegram_groups_by_env():
    lines = [
        "[CRITICAL] [real-ibkr] API unreachable",
        "[WARNING] [staging-ibkr/gld-1dte-thu-put] Unexpected FSM: FLAT (expected <IN_POSITION>)",
    ]
    out = render_alert_telegram(lines)
    assert "2 Anomalien" in out
    assert "\U0001f534" in out  # CRITICAL rot
    assert "\U0001f7e1" in out  # WARNING gelb
    assert "<b>real-ibkr</b>" in out
    assert "<b>staging-ibkr</b>" in out
    assert "<b>gld-1dte-thu-put</b>" in out
    # HTML-Escaping der Message
    assert "&lt;IN_POSITION&gt;" in out


# --- Daily-Email ---

def test_daily_email_contains_env_table_and_missing():
    html = render_daily_email_html(
        {"staging-ibkr": _daily_data()},
        missing=["real-ibkr"],
        open_anomalies=["[WARNING] [staging-ibkr/x] something"],
        date_str="2026-07-02",
    )
    assert "<html>" in html
    assert "staging-ibkr" in html
    assert "<table" in html
    assert "spx-0dte-always" in html
    assert "(inaktiv)" in html
    assert "Kein EOD-JSON von: <b>real-ibkr</b>" in html
    assert "Offene Anomalien" in html


def test_daily_email_without_missing_or_anomalies():
    html = render_daily_email_html(
        {"staging-ibkr": _daily_data()}, missing=[], open_anomalies=[], date_str="2026-07-02"
    )
    assert "Kein EOD-JSON" not in html
    assert "Offene Anomalien" not in html


# --- Anomalie-Email ---

def test_anomaly_email_no_duplicate_anomaly_block():
    lines = ["[CRITICAL] [real-ibkr] API unreachable"]
    output = "Header-Zeile\nLive-Status hier\n\nANOMALIES DETECTED:\n  [CRITICAL] [real-ibkr] API unreachable [NEW]"
    html = render_anomaly_email_html(lines, output, "2026-07-03 09:17")
    # Anomalie genau einmal (im Alert-Block), nicht nochmal im Live-Status-<pre>
    assert html.count("API unreachable") == 1
    assert "Live-Status hier" in html


# --- Weekly ---

def _weekly_data():
    return [{
        "env": "staging-ibkr",
        "rows": [{
            "strategy": "carver-scalping",
            "week_return": 0.012,
            "total_return": 0.093,
            "cagr": 0.38,
            "sharpe": 1.10,
            "max_drawdown": -0.008,
            "win_rate": 0.45,
            "profit_factor": 1.3,
        }],
        "portfolio": {
            "week_return": 0.01,
            "total_return": 0.11,
            "sharpe": 0.9,
            "max_drawdown": -0.01,
        },
        "warnings": ["carver-scalping: PF 0.9 < 1.0"],
        "note": None,
    }]


def test_weekly_telegram_narrow_lines():
    out = render_weekly_telegram(_weekly_data())
    assert "carver-scalping" in out
    assert "Woche +1.2%" in out
    assert "Start +9.3%" in out
    assert "CAGR +38.0%" in out
    assert "Portfolio" in out
    assert "⚠ carver-scalping: PF 0.9 &lt; 1.0" in out
    # keine breite Spaltentabelle
    assert "<pre>" not in out
    assert max(len(line) for line in out.split("\n")) < 80


def test_weekly_telegram_negative_sharpe_visible():
    data = _weekly_data()
    data[0]["rows"][0]["sharpe"] = -1.42
    out = render_weekly_telegram(data)
    assert "Sharpe -1.42" in out


def test_weekly_telegram_week_return_missing():
    data = _weekly_data()
    data[0]["rows"][0]["week_return"] = None
    data[0]["portfolio"]["week_return"] = None
    out = render_weekly_telegram(data)
    assert "Woche —" in out


def test_weekly_telegram_note_env():
    out = render_weekly_telegram([{"env": "real-ibkr", "note": "keine Daten"}])
    assert "keine Daten" in out


def test_weekly_email_table():
    html = render_weekly_email_html(_weekly_data(), "2026-07-03")
    assert "<table" in html
    assert "carver-scalping" in html
    assert "+1.2%" in html
    assert "+9.3%" in html
    assert "PORTFOLIO" in html
    assert "NAV-Delta" in html
