#!/usr/bin/env python3
"""
Wachtel Precheck — Deterministic health and threshold checker.

Runs every 15 minutes via the Telegram bot scheduler.
Checks Hase runtime APIs against baseline expectations.

Exit codes:
  0 = OK (no anomalies)
  1 = Anomalies detected
  2 = Daily summary time
"""

import argparse
import math
import os
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

BASELINES_DIR = Path(__file__).parent / "baselines"
PRECHECK_STATE_FILE = Path.home() / ".eule" / ".precheck_last_anomalies"
API_TIMEOUT = 5

ENVIRONMENTS = {
    "staging-ibkr": {
        "port": 8776,
        "tier": "staging",
        "schedule": {"weekdays": [0, 1, 2, 3, 4], "start": "09:00", "end": "22:30", "tz": "Europe/Berlin"},
    },
    "staging-hl": {
        "port": 8777,
        "tier": "staging",
        "schedule": None,  # 24/7
        "monitoring": False,  # Hyperliquid testnet — no actionable alerts
    },
    "real-ibkr": {
        "port": 8767,
        "tier": "production",
        "schedule": {"weekdays": [0, 1, 2, 3, 4], "start": "13:00", "end": "22:00", "tz": "Europe/Berlin"},
        "unrealized_threshold": -5000,
    },
    "real2-ibkr": {
        "port": 8768,
        "tier": "production",
        "schedule": {"weekdays": [0, 1, 2, 3, 4], "start": "13:00", "end": "22:00", "tz": "Europe/Berlin"},
        "unrealized_threshold": -1000,
    },
}

# Daily summary windows (Berlin time for IBKR, UTC for HL)
DAILY_SUMMARY_IBKR = {"hour": 21, "minute_start": 0, "minute_end": 15, "tz": "Europe/Berlin"}
DAILY_SUMMARY_HL = {"hour": 0, "minute_start": 0, "minute_end": 15, "tz": "UTC"}


def load_baselines() -> dict[str, dict]:
    """Load all baseline YAML files, keyed by strategy_name."""
    baselines = {}
    for path in BASELINES_DIR.glob("*.yaml"):
        with open(path) as f:
            bl = yaml.safe_load(f)
        baselines[bl["strategy_name"]] = bl
    return baselines


def is_trading_time(schedule: dict | None) -> bool:
    """Check if current time falls within the environment's trading schedule."""
    if schedule is None:
        return True  # 24/7

    tz = ZoneInfo(schedule["tz"])
    now = datetime.now(tz)

    if now.weekday() not in schedule["weekdays"]:
        return False

    start = time.fromisoformat(schedule["start"])
    end = time.fromisoformat(schedule["end"])
    return start <= now.time() <= end


def is_daily_summary_time() -> bool:
    """Check if we're in a daily summary window."""
    # IBKR: 21:00-21:15 Berlin
    tz_berlin = ZoneInfo("Europe/Berlin")
    now_berlin = datetime.now(tz_berlin)
    if (
        now_berlin.weekday() in [0, 1, 2, 3, 4]
        and now_berlin.hour == DAILY_SUMMARY_IBKR["hour"]
        and DAILY_SUMMARY_IBKR["minute_start"] <= now_berlin.minute <= DAILY_SUMMARY_IBKR["minute_end"]
    ):
        return True

    # HL: 00:00-00:15 UTC
    now_utc = datetime.now(ZoneInfo("UTC"))
    if (
        now_utc.hour == DAILY_SUMMARY_HL["hour"]
        and DAILY_SUMMARY_HL["minute_start"] <= now_utc.minute <= DAILY_SUMMARY_HL["minute_end"]
    ):
        return True

    return False


def api_get(port: int, endpoint: str) -> dict | list | None:
    """GET request to a Hase API endpoint. Returns parsed JSON or None on error."""
    try:
        resp = requests.get(f"http://localhost:{port}{endpoint}", timeout=API_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def api_post(port: int, endpoint: str) -> dict | None:
    """POST request to a Hase API endpoint. Returns parsed JSON or None on error."""
    try:
        resp = requests.post(f"http://localhost:{port}{endpoint}", timeout=API_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def evaluate_fsm_expectation(condition: str, expected, current_state: str, now: datetime) -> str | None:
    """
    Evaluate a single FSM expectation condition.
    Returns anomaly message if violated, None if OK or condition doesn't apply.
    """
    expected_states = expected if isinstance(expected, list) else [expected]

    if current_state in expected_states:
        return None  # State matches, no anomaly

    now_et = now.astimezone(ZoneInfo("US/Eastern"))
    now_berlin = now.astimezone(ZoneInfo("Europe/Berlin"))
    weekday = now.weekday()  # 0=Mon

    cond = condition.lower().strip()

    # "not Monday" / "not Tuesday"
    day_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
    for day_name, day_num in day_map.items():
        if cond == f"not {day_name}":
            if weekday != day_num:
                return f"Unexpected FSM: {current_state} (expected {expected})"
            return None

    # "after HH:MM ET"
    if "after" in cond and "et" in cond:
        parts = cond.replace("after", "").replace("et", "").strip()
        # Extract time, possibly with day prefix: "Monday after 10:30 ET"
        time_str = parts.split()[-1] if parts.split() else parts
        try:
            threshold = time.fromisoformat(time_str)
        except ValueError:
            return None
        # Check day prefix if present
        for day_name, day_num in day_map.items():
            if day_name in cond:
                if weekday != day_num:
                    return None  # Wrong day, condition doesn't apply
                break
        if now_et.time() >= threshold:
            return f"Unexpected FSM: {current_state} (expected {expected})"
        return None

    # "weekday after HH:MM ET"
    if "weekday" in cond and "after" in cond:
        if weekday > 4:
            return None  # Weekend
        time_str = cond.split("after")[-1].replace("et", "").strip()
        try:
            threshold = time.fromisoformat(time_str)
        except ValueError:
            return None
        if now_et.time() >= threshold:
            return f"Unexpected FSM: {current_state} (expected {expected})"
        return None

    # "trading hours (HH:MM-HH:MM CET)"
    if "trading hours" in cond and "cet" in cond:
        import re

        m = re.search(r"(\d{2}:\d{2})-(\d{2}:\d{2})", cond)
        if m:
            start = time.fromisoformat(m.group(1))
            end = time.fromisoformat(m.group(2))
            if start <= now_berlin.time() <= end and weekday <= 4:
                return f"Unexpected FSM: {current_state} (expected {expected})"
        return None

    # "outside trading hours"
    if "outside trading hours" in cond:
        # If we're in trading hours, condition doesn't apply
        # This is checked contextually — if we got here, it's outside
        if weekday > 4 or now_berlin.hour < 9 or now_berlin.hour >= 18:
            return f"Unexpected FSM: {current_state} (expected {expected})"
        return None

    # "market open first minutes"
    if "market open" in cond and "first minutes" in cond:
        if weekday <= 4 and 9 <= now_berlin.hour <= 9 and now_berlin.minute < 20:
            return f"Unexpected FSM: {current_state} (expected {expected})"
        return None

    # "any time" — always applies
    if "any time" in cond:
        return f"Unexpected FSM: {current_state} (expected {expected})"

    return None  # Unknown condition, skip


def check_environment(env_name: str, env_config: dict, baselines: dict) -> list[tuple[str, str]]:
    """
    Check a single environment. Returns list of (severity, message) tuples.
    Severity: CRITICAL or WARNING.
    """
    anomalies = []
    tier = env_config["tier"]
    port = env_config["port"]
    severity = "CRITICAL" if tier == "production" else "WARNING"

    if not env_config.get("monitoring", True):
        return []  # Monitoring disabled for this environment

    if not is_trading_time(env_config["schedule"]):
        return []

    # 1. Health check
    health = api_get(port, "/health")
    if health is None:
        anomalies.append((severity, f"[{env_name}] API unreachable"))
        return anomalies

    # 1b. Runtime-level health check (consumer thread, DB buffer, disk)
    status = api_get(port, "/status")
    if status:
        rt_health = status.get("runtime_health", {})
        rt_problems = rt_health.get("problems", [])
        for p in rt_problems:
            anomalies.append((severity, f"[{env_name}] Runtime: {p}"))

    # 2. Strategies check
    strategies = api_get(port, "/strategies")
    if strategies is None:
        anomalies.append((severity, f"[{env_name}] /strategies endpoint failed"))
        return anomalies

    now = datetime.now(ZoneInfo("UTC"))

    # Build lookup by strategy name
    strat_by_name = {}
    for s in strategies:
        name = s.get("name")
        if name:
            strat_by_name[name] = s

    # Check each baseline that belongs to this environment
    for bl_name, bl in baselines.items():
        if env_name not in bl.get("environments", []):
            continue

        strat = strat_by_name.get(bl_name)
        if strat is None:
            # Strategy not found in API — might not be loaded
            continue

        prefix = f"[{env_name}/{bl_name}]"
        bl_health = bl.get("health", {})

        # Worker checks
        worker = strat.get("worker", {})
        if bl_health.get("worker_alive") and not worker.get("alive", True):
            anomalies.append(("CRITICAL", f"{prefix} Worker thread DEAD"))

        if bl_health.get("circuit_breaker_closed") and worker.get("circuit_state") != "closed":
            cb_state = worker.get("circuit_state", "unknown")
            anomalies.append((severity, f"{prefix} Circuit breaker: {cb_state}"))

        error_count = worker.get("error_count", 0)
        max_errors = bl_health.get("max_error_count", 0)
        if error_count > max_errors:
            anomalies.append((severity, f"{prefix} Error count: {error_count} > {max_errors}"))

        queue_size = worker.get("queue_size", 0)
        max_queue = bl_health.get("max_queue_size", 50)
        if queue_size > max_queue:
            anomalies.append((severity, f"{prefix} Queue size: {queue_size} > {max_queue}"))

        # Health problems
        health_info = strat.get("health", {})
        problems = health_info.get("problems", [])
        if problems:
            anomalies.append((severity, f"{prefix} Problems: {', '.join(problems)}"))

        # FSM state checks
        display = strat.get("display", {})
        fsm_state = display.get("fsm_state", "UNKNOWN")

        # Events delta — only warn if strategy is in an active state (not FLAT/IDLE)
        events_delta = health_info.get("events_delta", 0)
        min_events = bl_health.get("min_events_delta", 1)
        if events_delta < min_events and fsm_state not in {"FLAT", "IDLE"}:
            anomalies.append(("WARNING", f"{prefix} Low activity: events_delta={events_delta}"))
        fsm_config = bl.get("fsm", {})
        valid_states = fsm_config.get("valid_states", [])

        if valid_states and fsm_state not in valid_states:
            anomalies.append((severity, f"{prefix} Invalid FSM state: {fsm_state} (valid: {valid_states})"))

        # FSM expectations (time-dependent)
        for exp in fsm_config.get("expectations", []):
            msg = evaluate_fsm_expectation(exp["condition"], exp["expected"], fsm_state, now)
            if msg:
                anomalies.append(("WARNING", f"{prefix} {msg}"))

        # Daily loss check
        stats = strat.get("stats", {})
        realized_pnl = stats.get("realized_pnl", 0.0)
        max_loss = bl.get("metrics", {}).get("max_daily_loss", {}).get("warn_below")
        if max_loss is not None and realized_pnl < max_loss:
            anomalies.append((severity, f"{prefix} Daily loss: ${realized_pnl:.2f} < ${max_loss:.2f}"))

    # 3. Broker data staleness check (via /debug/cache + /debug/universe for trading hours)
    cache = api_get(port, "/debug/cache")
    if cache and "cache_entries" in cache:
        STALENESS_THRESHOLD = 30 * 60  # 30 minutes in seconds

        # Build exchange hours lookup from universe (broker_id → {market_open, market_close, tz})
        exchange_hours = {}
        universe = api_get(port, "/debug/universe")
        if universe and "symbols" in universe:
            for sym in universe["symbols"]:
                bid = sym.get("broker_id")
                if bid and sym.get("market_close") and sym.get("exchange_timezone"):
                    exchange_hours[bid] = {
                        "market_open": time.fromisoformat(sym["market_open"]),
                        "market_close": time.fromisoformat(sym["market_close"]),
                        "tz": sym["exchange_timezone"],
                    }

        # Only check intraday frequencies — daily bars are naturally stale during the day
        INTRADAY_FREQS = {"1min", "5min", "15min", "30min", "1h"}
        # Build set of Options instrument broker_ids to skip (0DTE cache is not continuously updated)
        options_broker_ids = set()
        if universe and "symbols" in universe:
            for sym in universe["symbols"]:
                if sym.get("type") == "Options" and sym.get("broker_id"):
                    options_broker_ids.add(sym["broker_id"])
        for entry in cache["cache_entries"]:
            freq = entry.get("freq", "")
            if freq and freq not in INTRADAY_FREQS:
                continue  # Skip daily/weekly bars, staleness is expected
            broker_id = entry.get("broker_id", "")
            if broker_id in options_broker_ids:
                continue  # Options cache is not continuously ticked, skip staleness
            symbol = entry.get("symbol", "unknown")
            broker_id = entry.get("broker_id", symbol)
            age = entry.get("last_bar_age_seconds", 0)
            if age > STALENESS_THRESHOLD:
                # Check if we're outside trading hours for this instrument's exchange
                # If no hours data available (old API without broker_id/hours), skip staleness check
                hours = exchange_hours.get(broker_id) or exchange_hours.get(symbol)
                if not hours:
                    continue  # Can't determine trading hours, don't false-alarm
                tz = ZoneInfo(hours["tz"])
                now_local = datetime.now(tz).time()
                mkt_open = hours["market_open"]
                mkt_close = hours["market_close"]
                # Normal hours (open < close): skip if outside hours
                # Overnight session (open > close, e.g. 17:00-16:00): skip if in the gap
                if mkt_open < mkt_close:
                    if now_local < mkt_open or now_local > mkt_close:
                        continue  # Outside trading hours, stale data is expected
                else:
                    # Overnight: the "closed" gap is from close to open
                    if mkt_close < now_local < mkt_open:
                        continue  # In the closed gap, stale data is expected

                age_min = int(age / 60)
                anomalies.append((severity, f"[{env_name}] Broker stale data: {symbol} last bar {age_min}min ago"))

    # 4. Portfolio check (production only)
    if tier == "production":
        portfolio = api_get(port, "/portfolio")
        if portfolio:
            pnl = portfolio.get("pnl", {})
            unrealized = pnl.get("daily_unrealized_pnl", 0.0)
            threshold = env_config.get("unrealized_threshold", -1000)
            if isinstance(unrealized, (int, float)) and not math.isnan(unrealized) and unrealized < threshold:
                anomalies.append(("CRITICAL", f"[{env_name}] Portfolio unrealized: ${unrealized:.2f}"))

    # 5. Broker log health check — scan for critical broker errors
    # These errors indicate the broker connection is broken and strategies cannot trade
    hase_override = os.environ.get("EULE_HASE_DIR")
    if hase_override:
        hase_dir = Path(hase_override)
    elif env_name.startswith("staging"):
        hase_dir = Path.home() / "staging"
    else:
        hase_dir = Path.home() / "hase"
    broker_log_dir = hase_dir / "werkstatt" / "logs"
    today_str = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y%m%d")
    broker_log_pattern = f"BrokerIBKR_{env_name}_{today_str}*.log"
    broker_logs = sorted(broker_log_dir.glob(broker_log_pattern))

    BROKER_ERROR_PATTERNS = [
        "no valid result",
        "no underlying price",
        "base-price",
        "Chart data unavailable",
        "get_option_chain failed",
        "Marktdaten NICHT abonniert",
        "Keine Marktdaten",
    ]

    if broker_logs:
        latest_broker_log = broker_logs[-1]
        try:
            import re

            broker_errors: dict[str, int] = {}
            recent_broker_errors: list[str] = []
            # Only check errors from the last 30 minutes to avoid stale alerts
            now = datetime.now(ZoneInfo("Europe/Berlin"))
            cutoff = now - __import__("datetime").timedelta(minutes=30)
            cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M")

            with open(latest_broker_log) as f:
                for line in f:
                    for pattern in BROKER_ERROR_PATTERNS:
                        if pattern in line:
                            # Extract timestamp from log line (format: 2026-04-02 16:14:01.090)
                            ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", line)
                            if ts_match and ts_match.group(1) >= cutoff_str:
                                broker_errors[pattern] = broker_errors.get(pattern, 0) + 1
                                if len(recent_broker_errors) < 3:
                                    recent_broker_errors.append(line.strip()[:200])
                            break

            if broker_errors:
                total = sum(broker_errors.values())
                error_summary = ", ".join(f"{k}: {v}x" for k, v in broker_errors.items())
                anomalies.append((severity, f"[{env_name}] Broker data errors (last 30min): {total} errors ({error_summary})"))
        except Exception:
            pass  # Don't fail precheck because of log parsing errors

    # Also check via API: runtime_health problems from /status
    if status:
        broker_problems = status.get("broker_health", {}).get("problems", [])
        for p in broker_problems:
            anomalies.append((severity, f"[{env_name}] Broker: {p}"))

    # Acknowledge order/reconcile problems after reading them
    # This clears the counters so they don't re-alert on next precheck cycle
    has_actionable = any(
        "orders_rejected" in msg or "orders_cancelled" in msg or "reconcile" in msg or "unfilled" in msg for _, msg in anomalies
    )
    if has_actionable:
        api_post(port, "/acknowledge")

    return anomalies


def run_precheck(force_summary: bool = False) -> tuple[int, str]:
    """
    Run full precheck across all environments.
    Returns (exit_code, output_text).
    """
    baselines = load_baselines()
    all_anomalies = []

    for env_name, env_config in ENVIRONMENTS.items():
        anomalies = check_environment(env_name, env_config, baselines)
        all_anomalies.extend(anomalies)

    if force_summary or is_daily_summary_time():
        import json

        today_str = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")
        hase_override = os.environ.get("EULE_HASE_DIR")
        production_dir = Path(hase_override) if hase_override else Path.home() / "hase"
        log_dirs = [
            Path.home() / "staging" / "werkstatt" / "logs",  # staging
            production_dir / "werkstatt" / "logs",  # production
        ]

        # Collect summary JSONs written by runtime end_of_day
        summary_files = {}
        for log_dir in log_dirs:
            for f in sorted(log_dir.glob(f"daily-summary-*-{today_str}.json")):
                summary_files[f.stem] = f

        if force_summary and not summary_files:
            # Fallback: try API (runtime might still be up)
            lines = ["DAILY SUMMARY:"]
            for env_name, env_config in ENVIRONMENTS.items():
                port = env_config["port"]
                status = api_get(port, "/status")
                portfolio = api_get(port, "/portfolio")
                strategies = api_get(port, "/strategies")

                if status is None:
                    lines.append(f"  [{env_name}] OFFLINE (no summary JSON found)")
                    continue

                cash = "N/A"
                pnl = "N/A"
                if portfolio:
                    cash_info = portfolio.get("cash", {})
                    pnl_info = portfolio.get("pnl", {})
                    cash = f"${cash_info.get('current_cash', 0):.2f}"
                    pnl = f"${pnl_info.get('daily_realized_pnl', 0):.2f}"

                strat_count = len(strategies) if strategies else 0
                lines.append(f"  [{env_name}] Strategies: {strat_count}, Cash: {cash}, Realized PnL: {pnl}")

                if strategies:
                    for s in strategies:
                        name = s.get("name", "?")
                        display = s.get("display", {})
                        fsm = display.get("fsm_state", "?")
                        stats = s.get("stats", {})
                        rpnl = stats.get("realized_pnl", 0)
                        lines.append(f"    {name}: {fsm} (PnL: ${rpnl:.2f})")

            output = "\n".join(lines)
            if all_anomalies:
                anomaly_lines = [f"  [{sev}] {msg}" for sev, msg in all_anomalies]
                output = "ANOMALIES DETECTED:\n" + "\n".join(anomaly_lines) + "\n\n" + output
                return 1, output
            return 2, output

        # Build summary from JSON files
        lines = ["DAILY SUMMARY:"]
        for key, filepath in sorted(summary_files.items()):
            try:
                data = json.loads(filepath.read_text())
            except Exception:
                lines.append(f"  [{key}] ERROR reading summary JSON")
                continue

            env_name = data.get("env", key)
            portfolio = data.get("portfolio", {})
            strategies = data.get("strategies", [])
            fsm_states = data.get("fsm_states", {})

            cash = f"${portfolio.get('cash', 0):.2f}" if "cash" in portfolio else "N/A"
            realized = f"${portfolio.get('realized', 0):.2f}" if "realized" in portfolio else "N/A"

            lines.append(f"  [{env_name}] Strategies: {len(strategies)}, Cash: {cash}, Realized PnL: {realized}")
            for s in strategies:
                name = s.get("name", "?")
                fsm = fsm_states.get(name, "?")
                stats = s.get("stats", {})
                rpnl = stats.get("realized_pnl", 0)
                lines.append(f"    {name}: {fsm} (PnL: ${rpnl:.2f})")

        output = "\n".join(lines)
        if all_anomalies:
            anomaly_lines = [f"  [{sev}] {msg}" for sev, msg in all_anomalies]
            output = "ANOMALIES DETECTED:\n" + "\n".join(anomaly_lines) + "\n\n" + output
            return 1, output
        return 2, output

    if all_anomalies:
        # Deduplicate: only alert on NEW anomalies not seen in the last run
        import hashlib

        state_file = PRECHECK_STATE_FILE
        current_hashes = set()
        for sev, msg in all_anomalies:
            h = hashlib.md5(f"{sev}:{msg}".encode()).hexdigest()[:12]
            current_hashes.add(h)

        previous_hashes = set()
        try:
            if state_file.exists():
                prev_data = state_file.read_text().strip().split("\n")
                # First line is timestamp, rest are hashes
                if len(prev_data) > 1:
                    previous_hashes = set(prev_data[1:])
        except Exception:
            pass

        # Save current state
        try:
            state_file.write_text(datetime.now(ZoneInfo("Europe/Berlin")).isoformat() + "\n" + "\n".join(sorted(current_hashes)))
        except Exception:
            pass

        # Only report if there are NEW anomalies not seen last time
        new_hashes = current_hashes - previous_hashes
        if not new_hashes:
            # Same anomalies as last run — suppress to avoid spam
            return 0, "OK — Known anomalies still present (suppressed)"

        lines = ["ANOMALIES DETECTED:"]
        for sev, msg in all_anomalies:
            h = hashlib.md5(f"{sev}:{msg}".encode()).hexdigest()[:12]
            marker = " [NEW]" if h in new_hashes else " [KNOWN]"
            lines.append(f"  [{sev}] {msg}{marker}")
        return 1, "\n".join(lines)

    # Clear state file when no anomalies
    try:
        state_file = PRECHECK_STATE_FILE
        if state_file.exists():
            state_file.unlink()
    except Exception:
        pass

    return 0, "OK — All checks passed"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true", help="Force daily summary output regardless of time window")
    args = parser.parse_args()
    exit_code, output = run_precheck(force_summary=args.summary)
    print(output)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
