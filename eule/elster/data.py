"""
Daten-Layer fuer Elster: PostgreSQL-Queries und DataFrame-Aufbereitung.
"""

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

try:
    import psycopg  # type: ignore
except ImportError:
    psycopg = None

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class Regime:
    """Zeitraum mit konsistenter Strategie-Konfiguration."""

    start_date: date
    end_date: date | None  # None = aktuell laufend
    config_snapshot: dict
    days: int
    run_ids: list[str]

    @property
    def label(self) -> str:
        end = self.end_date.isoformat() if self.end_date else "heute"
        return f"{self.start_date.isoformat()} -> {end} ({self.days}d)"


def get_runtime_name(env_name: str) -> str:
    """Runtime-Name fuer ein Environment (DB-Filter)."""
    from eule.db import RUNTIME_NAMES

    return RUNTIME_NAMES.get(env_name, env_name)


def load_daily_pnl(
    conn: "psycopg.Connection",
    runtime_name: str,
    days: int | None = None,
    strategy_key: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    """
    Laedt nav_end Zeitreihe aus daily_pnl.

    Entweder days (letzte N Tage) oder start_date/end_date angeben.
    Gibt per-Strategy Rows zurueck (strategy_key IS NOT NULL).
    """
    conditions = ["runtime_name = %(rn)s", "strategy_key IS NOT NULL"]
    params: dict = {"rn": runtime_name}

    if strategy_key:
        conditions.append("strategy_key = %(sk)s")
        params["sk"] = strategy_key

    if start_date and end_date:
        conditions.append("date >= %(start)s AND date <= %(end)s")
        params["start"] = start_date
        params["end"] = end_date
    elif days:
        conditions.append("date >= current_date - %(days)s")
        params["days"] = days

    where = " AND ".join(conditions)
    sql = f"""
        SELECT date, strategy_key, nav_end, pnl_realized, pnl_unrealized,
               fees, pnl_net, cash_end
        FROM daily_pnl
        WHERE {where}
        ORDER BY date, strategy_key
    """
    return pd.read_sql(sql, conn, params=params)


def load_daily_pnl_total(
    conn: "psycopg.Connection",
    runtime_name: str,
    days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    """Laedt Portfolio-Gesamtzeile (strategy_key IS NULL) aus daily_pnl."""
    conditions = ["runtime_name = %(rn)s", "strategy_key IS NULL"]
    params: dict = {"rn": runtime_name}

    if start_date and end_date:
        conditions.append("date >= %(start)s AND date <= %(end)s")
        params["start"] = start_date
        params["end"] = end_date
    elif days:
        conditions.append("date >= current_date - %(days)s")
        params["days"] = days

    where = " AND ".join(conditions)
    sql = f"""
        SELECT date, nav_end, pnl_realized, pnl_unrealized, fees, pnl_net, cash_end
        FROM daily_pnl
        WHERE {where}
        ORDER BY date
    """
    return pd.read_sql(sql, conn, params=params)


def load_trades(
    conn: "psycopg.Connection",
    runtime_name: str,
    days: int | None = None,
    strategy_key: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    """Laedt Trades fuer Trade-Statistiken."""
    conditions = ["runtime_name = %(rn)s"]
    params: dict = {"rn": runtime_name}

    if strategy_key:
        conditions.append("strategy_key = %(sk)s")
        params["sk"] = strategy_key

    if start_date and end_date:
        conditions.append("date >= %(start)s AND date <= %(end)s")
        params["start"] = start_date
        params["end"] = end_date
    elif days:
        conditions.append("date >= current_date - %(days)s")
        params["days"] = days

    where = " AND ".join(conditions)
    sql = f"""
        SELECT ts, date, strategy_key, symbol, asset_class, side,
               qty, price, value, fees
        FROM trades
        WHERE {where}
        ORDER BY ts
    """
    return pd.read_sql(sql, conn, params=params)


def load_runs_with_configs(
    conn: "psycopg.Connection",
    runtime_name: str,
) -> pd.DataFrame:
    """Laedt alle Runs mit strategy_configs fuer Regime-Erkennung."""
    sql = """
        SELECT run_id, started_at, finished_at, strategy_configs
        FROM runs
        WHERE runtime_name = %(rn)s
          AND started_at IS NOT NULL
        ORDER BY started_at
    """
    return pd.read_sql(sql, conn, params={"rn": runtime_name})


def load_baseline(strategy_name: str) -> dict | None:
    """Laedt Monitoring-Baseline YAML fuer eine Strategie."""
    if yaml is None:
        return None
    baseline_dir = Path(__file__).parent.parent / "monitoring" / "baselines"
    path = baseline_dir / f"{strategy_name}.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def nav_to_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Konvertiert daily_pnl DataFrame zu taeglichen Returns pro Strategie.

    Input: DataFrame mit columns [date, strategy_key, nav_end]
    Output: DataFrame mit date als Index, strategy_keys als Columns, Returns als Werte
    """
    if df.empty:
        return pd.DataFrame()

    nav = df.pivot_table(index="date", columns="strategy_key", values="nav_end")
    returns = nav.pct_change().iloc[1:]  # erste Zeile ist NaN
    return returns


def list_strategies(conn: "psycopg.Connection", runtime_name: str) -> list[str]:
    """Gibt alle strategy_keys zurueck die in daily_pnl fuer diesen runtime existieren."""
    sql = """
        SELECT DISTINCT strategy_key
        FROM daily_pnl
        WHERE runtime_name = %(rn)s AND strategy_key IS NOT NULL
        ORDER BY strategy_key
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"rn": runtime_name})
        return [row[0] for row in cur.fetchall()]
