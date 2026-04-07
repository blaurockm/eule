"""
Regime-Erkennung: Findet Perioden mit konsistenter Strategie-Konfiguration.

Primaer: Config-Vergleich zwischen aufeinanderfolgenden Runs (strategy_configs JSONB).
Fallback: Git-History der Strategie-JSON-Dateien.
"""

import json
import subprocess
from datetime import date, datetime

import pandas as pd

from eule.elster.data import Regime


def detect_regimes(
    runs_df: pd.DataFrame,
    strategy_key: str,
) -> list[Regime]:
    """
    Findet Regime-Grenzen durch Config-Vergleich zwischen aufeinanderfolgenden Runs.

    Args:
        runs_df: DataFrame aus load_runs_with_configs() mit columns
                 [run_id, started_at, finished_at, strategy_configs]
        strategy_key: Strategie-Name fuer Config-Extraktion

    Returns:
        Liste von Regime-Objekten, chronologisch sortiert.
        Letztes Regime hat end_date=None (aktuell laufend).
    """
    if runs_df.empty:
        return []

    # Nur Runs mit strategy_configs (nicht NULL)
    valid = runs_df[runs_df["strategy_configs"].notna()].copy()
    if valid.empty:
        return _fallback_single_regime(runs_df)

    regimes: list[Regime] = []
    current_config: dict | None = None
    current_start: date | None = None
    current_run_ids: list[str] = []

    for _, row in valid.iterrows():
        configs = row["strategy_configs"]
        if isinstance(configs, str):
            configs = json.loads(configs)

        strategy_config = configs.get(strategy_key, {})
        run_date = pd.Timestamp(row["started_at"]).date()

        if current_config is None:
            # Erstes Regime starten
            current_config = strategy_config
            current_start = run_date
            current_run_ids = [row["run_id"]]
        elif _configs_differ(current_config, strategy_config):
            # Regime-Grenze: vorheriges Regime abschliessen
            regimes.append(
                Regime(
                    start_date=current_start,
                    end_date=run_date,
                    config_snapshot=current_config,
                    days=(run_date - current_start).days,
                    run_ids=current_run_ids,
                )
            )
            # Neues Regime starten
            current_config = strategy_config
            current_start = run_date
            current_run_ids = [row["run_id"]]
        else:
            current_run_ids.append(row["run_id"])

    # Letztes (aktuelles) Regime
    if current_start is not None:
        today = date.today()
        regimes.append(
            Regime(
                start_date=current_start,
                end_date=None,
                config_snapshot=current_config or {},
                days=(today - current_start).days,
                run_ids=current_run_ids,
            )
        )

    return regimes


def detect_regimes_git(strategy_name: str) -> list[tuple[date, str]]:
    """
    Fallback: Erkennt Config-Aenderungen via Git-History.

    Returns:
        Liste von (Datum, Commit-Hash) fuer Aenderungen an strategies/{name}.json
    """
    strategy_file = f"strategies/{strategy_name}.json"
    try:
        result = subprocess.run(
            ["git", "log", "--format=%ai %H", "--", strategy_file],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        changes = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            # Format: "2026-03-11 20:31:12 +0100 abc1234..."
            parts = line.split()
            if len(parts) >= 4:
                d = datetime.strptime(parts[0], "%Y-%m-%d").date()
                commit_hash = parts[3]
                changes.append((d, commit_hash))

        return list(reversed(changes))  # chronologisch
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def count_regime_changes_in_range(
    regimes: list[Regime],
    start_date: date,
    end_date: date,
) -> int:
    """Zaehlt wie viele Regime-Grenzen in einen Zeitraum fallen."""
    count = 0
    for regime in regimes:
        if regime.start_date > start_date and regime.start_date <= end_date:
            count += 1
    return count


def config_diff(old: dict, new: dict) -> dict[str, tuple]:
    """
    Gibt geaenderte Keys zwischen zwei Configs zurueck.

    Returns: {key: (old_value, new_value)} fuer geaenderte Keys
    """
    diff = {}
    all_keys = set(old.keys()) | set(new.keys())
    for key in all_keys:
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            diff[key] = (old_val, new_val)
    return diff


def _configs_differ(a: dict, b: dict) -> bool:
    """Prueft ob zwei Configs sich inhaltlich unterscheiden."""
    return json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)


def _fallback_single_regime(runs_df: pd.DataFrame) -> list[Regime]:
    """Wenn keine strategy_configs vorhanden: gesamten Zeitraum als ein Regime."""
    if runs_df.empty:
        return []
    start = pd.Timestamp(runs_df.iloc[0]["started_at"]).date()
    return [
        Regime(
            start_date=start,
            end_date=None,
            config_snapshot={},
            days=(date.today() - start).days,
            run_ids=runs_df["run_id"].tolist(),
        )
    ]
