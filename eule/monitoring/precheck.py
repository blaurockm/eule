#!/usr/bin/env python3
"""
Wachtel Precheck — Deterministic health and threshold checker.

Runs every 15 minutes via the Telegram bot scheduler.
Checks Hase runtime APIs against baseline expectations.

Exit codes:
  0 = OK (no anomalies)
  1 = Anomalies detected (mindestens eine NEUE)
  2 = Daily summary (--summary)
  3 = Nur bekannte Anomalien (unterdrueckt — kein Re-Alert)
"""

import argparse
import math
import os
import shutil
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
    },
    "staging-hl": {
        "port": 8777,
        "tier": "staging",
        "monitoring": False,  # Hyperliquid testnet — no actionable alerts
    },
    "real-ibkr": {
        "port": 8767,
        "tier": "production",
        "unrealized_threshold": -5000,
    },
    "real2-ibkr": {
        "port": 8768,
        "tier": "production",
        "unrealized_threshold": -1000,
    },
}


# Trading-Hours sind NICHT hier hartkodiert — single source of truth ist die
# Fuchs-Config. Eule liest sie bei Bedarf aus den JSON-Files.
_FUCHS_CONFIG_PATHS = {
    "production": "fuchs-config.production.json",
    "staging": "fuchs-config.staging.json",
}


def _fuchs_config_path(env_name: str) -> Path:
    """Return path to the Fuchs config file responsible for env_name.

    Override via EULE_HASE_DIR; sonst ~/staging fuer staging-* und ~/hase
    fuer real-*.
    """
    override = os.environ.get("EULE_HASE_DIR")
    if override:
        base = Path(override)
    elif env_name.startswith("staging"):
        base = Path.home() / "staging"
    else:
        base = Path.home() / "hase"
    filename = _FUCHS_CONFIG_PATHS["staging" if env_name.startswith("staging") else "production"]
    return base / filename


def load_trading_hours(env_name: str) -> dict | None:
    """Lese trading_hours aus der Fuchs-Config fuer dieses Environment.

    Reihenfolge: per-environment override (`environments[env].trading_hours`)
    → Supervisor-Default (`supervisor.trading_hours`) → None (= 24/7).

    Wird die Config nicht gefunden (z.B. auf dem Dev-Rechner), wird None
    zurueckgegeben — dann pruefen wir 24/7. Auf systematic existieren die
    Files immer.

    Format passt zu is_trading_time / is_in_startup_or_shutdown_window:
    {"weekdays": [...], "start": "HH:MM", "end": "HH:MM", "tz": "..."}.
    """
    import json

    path = _fuchs_config_path(env_name)
    try:
        with open(path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return None

    env_cfg = cfg.get("environments", {}).get(env_name, {})
    th = env_cfg.get("trading_hours") or cfg.get("supervisor", {}).get("trading_hours")
    if not th:
        return None
    return {
        "weekdays": th.get("weekdays", [0, 1, 2, 3, 4]),
        "start": th["start"],
        "end": th["end"],
        "tz": th.get("timezone", "Europe/Berlin"),
    }

# EOD-Deadline: Puffer nach Trading-Hours-Ende, bis zu dem Hase sein
# EOD-JSON geschrieben haben muss (production: M2M ~30min nach Handelsschluss,
# staging: zum Handelsende). Cap 23:44, damit die Deadline vor Mitternacht
# liegt UND der 15-min-Precheck-Takt garantiert noch einen Lauf im
# Overdue-Fenster desselben Tages hat.
EOD_DEADLINE_BUFFER_MINUTES = 60
EOD_DEADLINE_CAP = time(23, 44)
EOD_DEADLINE_DEFAULT = time(22, 59)  # wenn keine Trading-Hours auffindbar


def _anomaly_key(sev: str, msg: str) -> str:
    """Stabiler Kurzschluessel einer Anomalie (fuer Lauf-zu-Lauf-Dedup)."""
    import hashlib

    return hashlib.md5(f"{sev}:{msg}".encode()).hexdigest()[:12]


def _load_anomaly_state() -> dict[str, str]:
    """State des letzten Laufs: {key: '[SEV] msg'}. Leeres Dict bei Fehlern
    oder Alt-Format (einmaliger Re-Alert nach Format-Umstellung ist ok)."""
    import json

    try:
        data = json.loads(PRECHECK_STATE_FILE.read_text())
        return dict(data.get("anomalies", {}))
    except Exception:
        return {}


def _save_anomaly_state(anomalies: dict[str, str]) -> None:
    import json

    try:
        PRECHECK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PRECHECK_STATE_FILE.write_text(json.dumps({
            "ts": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
            "anomalies": anomalies,
        }, indent=1))
    except Exception:
        pass


def load_open_anomalies() -> list[str]:
    """Zuletzt gesehene (ggf. unterdrueckte) Anomalie-Zeilen — fuer die
    Daily-Email. Leer wenn der letzte Lauf OK war (State wird dann geloescht)."""
    return list(_load_anomaly_state().values())


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


# Grace-Window: Hase braucht ~15-20s zum Hochfahren plus ~1min Init.
# In dieser Zeit sind APIs nicht / nur teilweise erreichbar — ein Precheck
# in dem Fenster ist sinnlos und wuerde nur False-Positives erzeugen.
STARTUP_SHUTDOWN_GRACE_SECONDS = 120


def is_in_startup_or_shutdown_window(
    schedule: dict | None,
    grace_seconds: int = STARTUP_SHUTDOWN_GRACE_SECONDS,
    now: datetime | None = None,
) -> bool:
    """True wenn wir innerhalb `grace_seconds` nach Start oder vor Ende liegen.

    Waehrend dieses Fensters faehrt Fuchs den Hase-Prozess hoch bzw. Hase
    macht Mark-to-Market und beendet sich — APIs sind nicht / nur teilweise
    erreichbar. Precheck soll dort schweigen.
    """
    if schedule is None:
        return False
    tz = ZoneInfo(schedule["tz"])
    if now is None:
        now = datetime.now(tz)
    else:
        now = now.astimezone(tz)
    if now.weekday() not in schedule["weekdays"]:
        return False
    start = time.fromisoformat(schedule["start"])
    end = time.fromisoformat(schedule["end"])
    today = now.date()
    start_dt = datetime.combine(today, start, tzinfo=tz)
    end_dt = datetime.combine(today, end, tzinfo=tz)
    delta_after_start = (now - start_dt).total_seconds()
    delta_before_end = (end_dt - now).total_seconds()
    if 0 <= delta_after_start < grace_seconds:
        return True
    if 0 <= delta_before_end < grace_seconds:
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


_DAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _condition_active(condition: str, now: datetime) -> bool:
    """Return True if the condition's temporal precondition applies right now.

    Supported grammar:
      - "not Monday" / "not Tuesday" / ...
      - "Monday after 10:30 ET" / "Wednesday before 16:00 ET"
      - "after 16:00 ET" / "before 09:30 ET" (no day qualifier)
      - "weekday" / "weekday after HH:MM ET" / "weekday before HH:MM ET"
      - "trading hours (HH:MM-HH:MM CET)"
      - "outside trading hours"
      - "market open first minutes"
      - "any time"
      - Compounds: "X and Y" — every part must be active.
    """
    import re as _re

    cond = condition.lower().strip()

    # Compound: every part must be active
    if _re.search(r"\s+and\s+", cond):
        parts = [p.strip() for p in _re.split(r"\s+and\s+", cond)]
        return all(_condition_active(p, now) for p in parts)

    now_et = now.astimezone(ZoneInfo("US/Eastern"))
    now_berlin = now.astimezone(ZoneInfo("Europe/Berlin"))
    weekday = now.weekday()

    for day_name, day_num in _DAY_MAP.items():
        if cond == f"not {day_name}":
            return weekday != day_num

    if cond == "weekday":
        return weekday <= 4

    # Time-based conditions with "after" / "before"
    for keyword in ("after", "before"):
        if keyword in cond and "et" in cond:
            prefix, _, suffix = cond.partition(keyword)
            time_str = suffix.replace("et", "").strip()
            try:
                threshold = time.fromisoformat(time_str)
            except ValueError:
                continue

            prefix = prefix.strip()
            # "weekday after/before HH:MM ET"
            if prefix == "weekday":
                if weekday > 4:
                    return False
            else:
                # Optional "Xday" prefix
                day_match = None
                for day_name, day_num in _DAY_MAP.items():
                    if day_name in prefix:
                        day_match = day_num
                        break
                if day_match is not None and weekday != day_match:
                    return False

            return now_et.time() >= threshold if keyword == "after" else now_et.time() < threshold

    if "trading hours" in cond and "cet" in cond:
        m = _re.search(r"(\d{2}:\d{2})-(\d{2}:\d{2})", cond)
        if m:
            start = time.fromisoformat(m.group(1))
            end = time.fromisoformat(m.group(2))
            return start <= now_berlin.time() <= end and weekday <= 4
        return False

    if "outside trading hours" in cond:
        return weekday > 4 or now_berlin.hour < 9 or now_berlin.hour >= 18

    if "market open" in cond and "first minutes" in cond:
        return weekday <= 4 and now_berlin.hour == 9 and now_berlin.minute < 20

    if "any time" in cond:
        return True

    return False


def evaluate_fsm_expectations(
    expectations: list[dict],
    current_state: str,
    now: datetime,
) -> str | None:
    """Evaluate FSM expectations as OR-union of all temporally active conditions.

    Conditions can overlap in time (e.g. "trading hours (09:15-17:25)" and
    "market open first minutes" both active 09:15-09:19). A single state must
    only satisfy *one* of the active conditions to be acceptable — we union
    their expected sets and alert only if the current state is in none of them.

    Returns an anomaly message if no active condition allows current_state,
    or None otherwise (including when no condition is active).
    """
    active: list[tuple[str, list[str]]] = []
    for exp in expectations:
        cond = exp["condition"]
        if not _condition_active(cond, now):
            continue
        expected = exp["expected"]
        states = expected if isinstance(expected, list) else [expected]
        active.append((cond, states))

    if not active:
        return None

    allowed: set[str] = set()
    for _, states in active:
        allowed.update(states)

    if current_state in allowed:
        return None

    conditions = [c for c, _ in active]
    return f"Unexpected FSM: {current_state} (expected {sorted(allowed)} for active conditions: {conditions})"


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

    schedule = load_trading_hours(env_name)
    if not is_trading_time(schedule):
        return []

    # Waehrend Start- bzw. Stop-Fenster (~2 min) ist die API nicht / nur
    # teilweise erreichbar. Pruefen ist dort sinnlos.
    if is_in_startup_or_shutdown_window(schedule):
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

    # Universe once: resolves per-strategy market close (universe_keys -> close)
    # and the broker_id-keyed exchange hours used by the staleness check below.
    # Single source: /debug/universe.
    universe = api_get(port, "/debug/universe")
    universe_by_key = {}
    exchange_hours = {}
    options_broker_ids = set()
    if universe and "symbols" in universe:
        for sym in universe["symbols"]:
            has_hours = sym.get("market_close") and sym.get("exchange_timezone")
            key = sym.get("key")
            bid = sym.get("broker_id")
            if key and has_hours:
                universe_by_key[key] = {
                    "market_close": time.fromisoformat(sym["market_close"]),
                    "tz": sym["exchange_timezone"],
                }
            if bid and has_hours and sym.get("market_open"):
                exchange_hours[bid] = {
                    "market_open": time.fromisoformat(sym["market_open"]),
                    "market_close": time.fromisoformat(sym["market_close"]),
                    "tz": sym["exchange_timezone"],
                }
            if sym.get("type") == "Options" and bid:
                options_broker_ids.add(bid)

    # 0DTE expiry is no longer asserted live: a cash-settled 0DTE spread stays
    # IN_POSITION after its market close (no CLOSE order — it expires worthless)
    # until Hase's end_of_day routine resolves it. Resolution is validated from
    # the EOD JSON in check_eod_json(), not from the live FSM.

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

        # FSM expectations are a LIVE assertion of the state a strategy should be
        # in — valid only while its market is open. After the strategy's market
        # close the position is settling (0DTE expires worthless, no CLOSE order);
        # resolution is then validated from the EOD JSON (check_eod_json), not live.
        close_utc = _strategy_market_close_utc(strat, universe_by_key, now)
        if close_utc is None or now < close_utc:
            msg = evaluate_fsm_expectations(fsm_config.get("expectations", []), fsm_state, now)
            if msg:
                anomalies.append(("WARNING", f"{prefix} {msg}"))

        # Daily loss check
        stats = strat.get("stats", {})
        realized_pnl = stats.get("realized_pnl", 0.0)
        max_loss = bl.get("metrics", {}).get("max_daily_loss", {}).get("warn_below")
        if max_loss is not None and realized_pnl < max_loss:
            anomalies.append((severity, f"{prefix} Daily loss: ${realized_pnl:.2f} < ${max_loss:.2f}"))

    # 3. Broker data staleness check (uses the prefetched universe hours above:
    #    exchange_hours keyed by broker_id, options_broker_ids to skip).
    cache = api_get(port, "/debug/cache")
    if cache and "cache_entries" in cache:
        STALENESS_THRESHOLD = 30 * 60  # 30 minutes in seconds
        # Only check intraday frequencies — daily bars are naturally stale during the day
        INTRADAY_FREQS = {"1min", "5min", "15min", "30min", "1h"}
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


_WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _is_0dte_strategy(name: str) -> bool:
    """Erkennen anhand des Namens — alle 0DTE-Strategien haben 0dte im Namen."""
    return "0dte" in name.lower()


# A 0DTE strategy must be flat once Hase's end_of_day routine has run: the option
# expired and the position settled. Anything else in the EOD JSON is a real anomaly.
_EOD_RESOLVED_0DTE_STATES = {"FLAT"}


def _strategy_market_close_utc(
    strat: dict, universe_by_key: dict, now: datetime
) -> datetime | None:
    """Latest market close (UTC, today) across the strategy's traded instruments.

    Resolves the strategy's ``universe_keys`` against the prefetched
    ``/debug/universe`` hours. Returns None when no key resolves — callers then
    keep the live FSM check as a safe fallback (status quo before close).
    """
    closes = []
    for k in strat.get("universe_keys") or []:
        u = universe_by_key.get(k)
        if not u:
            continue
        tzinfo = ZoneInfo(u["tz"])
        today_local = now.astimezone(tzinfo).date()
        close_dt = datetime.combine(today_local, u["market_close"], tzinfo=tzinfo)
        closes.append(close_dt.astimezone(ZoneInfo("UTC")))
    return max(closes) if closes else None


def _eod_summary_path(env_name: str, today_str: str) -> Path | None:
    """Path to today's end_of_day daily-summary JSON for an env, if it exists."""
    hase_override = os.environ.get("EULE_HASE_DIR")
    production_dir = Path(hase_override) if hase_override else Path.home() / "hase"
    candidates = [
        Path.home() / "staging" / "werkstatt" / "logs" / f"daily-summary-{env_name}-{today_str}.json",
        production_dir / "werkstatt" / "logs" / f"daily-summary-{env_name}-{today_str}.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def eod_deadline(env_name: str) -> time:
    """EOD-Deadline (Berlin) fuer ein Env, abgeleitet aus den Trading-Hours.

    Trading-Ende + EOD_DEADLINE_BUFFER_MINUTES, gecappt auf EOD_DEADLINE_CAP.
    Single source of truth ist die Fuchs-Config — frueher waren die Deadlines
    hier hartkodiert und liefen bei Config-Aenderungen still auseinander
    (22:59-vs-23:30-Falschalarm). Ohne auffindbare Trading-Hours gilt
    EOD_DEADLINE_DEFAULT.

    Annahme: Trading-Hours-TZ ist Europe/Berlin (wie in beiden Fuchs-Configs).
    """
    schedule = load_trading_hours(env_name)
    if schedule is None:
        return EOD_DEADLINE_DEFAULT
    end = time.fromisoformat(schedule["end"])
    total_min = end.hour * 60 + end.minute + EOD_DEADLINE_BUFFER_MINUTES
    cap_min = EOD_DEADLINE_CAP.hour * 60 + EOD_DEADLINE_CAP.minute
    total_min = min(total_min, cap_min)
    return time(total_min // 60, total_min % 60)


def _eod_json_overdue(env_name: str, env_config: dict, now: datetime) -> bool:
    """True when today's EOD JSON should already exist but doesn't.

    Distinguishes the benign post-close/pre-EOD gap from a runtime that never
    completed its trading day. The latter writes no JSON at all — exactly the
    failure that hid the 11.-19.06. staging outage for 8 days: a never-started
    runtime produced no EOD JSON, and Wachtel stayed silent because the absence
    of the file was treated as "not written yet".

    Gated to the env's trading weekdays. Die Overdue-Deadline kommt aus
    eod_deadline() (Trading-Ende + Puffer aus der Fuchs-Config). Market
    holidays sind nicht modelliert (gleiche Grenze wie die uebrige
    Trading-Hours-Gating-Logik).
    """
    schedule = load_trading_hours(env_name)
    weekdays = schedule["weekdays"] if schedule else [0, 1, 2, 3, 4]
    now_berlin = now.astimezone(ZoneInfo("Europe/Berlin"))
    if now_berlin.weekday() not in weekdays:
        return False
    return now_berlin.time() >= eod_deadline(env_name)


def check_eod_json(env_name: str, env_config: dict) -> list[tuple[str, str]]:
    """Phase 3: validate the end_of_day JSON once Hase has written it.

    Independent of the live API and of trading hours — works after the runtime
    has shut down. Confirms every 0DTE strategy resolved to FLAT at end of day.
    A 0DTE strategy still IN_POSITION in the JSON means the EOD routine did not
    resolve it (no expiry/settlement booked) — a genuine anomaly worth alerting.

    A missing JSON is benign only in the post-close/pre-EOD gap; once the EOD
    deadline has passed (_eod_json_overdue) it means the runtime never completed
    its day and gets alerted — this is the gap that hid the 11.-19.06. outage.
    1DTE strategies legitimately hold overnight and are excluded by
    _is_0dte_strategy ("1dte" != "0dte").
    """
    import json

    if not env_config.get("monitoring", True):
        return []

    now = datetime.now(ZoneInfo("Europe/Berlin"))
    today_str = now.strftime("%Y-%m-%d")
    severity = "CRITICAL" if env_config["tier"] == "production" else "WARNING"
    path = _eod_summary_path(env_name, today_str)
    if path is None:
        if _eod_json_overdue(env_name, env_config, now):
            return [(
                severity,
                f"[{env_name}] KEIN EOD-JSON heute ({today_str}) — Runtime hat den "
                f"Handelstag nicht abgeschlossen (nie gestartet / gecrasht?)",
            )]
        return []  # benign post-close/pre-EOD gap
    try:
        data = json.loads(path.read_text())
    except Exception:
        return [(severity, f"[{env_name}] EOD-JSON nicht lesbar: {path.name}")]

    anomalies = []
    for name, fsm in data.get("fsm_states", {}).items():
        if _is_0dte_strategy(name) and fsm not in _EOD_RESOLVED_0DTE_STATES:
            anomalies.append((
                severity,
                f"[{env_name}/{name}] EOD nicht aufgeloest: FSM={fsm} im JSON (erwartet FLAT)",
            ))
    return anomalies


def check_action_times(env_name: str, env_config: dict) -> list[tuple[str, str]]:
    """Statischer Config-Sanity-Check: action_time darf nicht NACH dem Trading-
    Hours-Ende des Environments liegen — sonst feuert die Action nie.

    Portiert aus Fuchs ``supervisor._check_action_times_vs_trading_hours``
    (verschwindet mit Fuchs in Phase 6). Liest die Strategy-JSONs aus dem
    ``strategies/``-Verzeichnis neben der zustaendigen Fuchs-Config und
    vergleicht ``action_time`` (in die TH-Zeitzone konvertiert) gegen das
    Tagesende der effektiven Trading-Hours.

    Unabhaengig von den Trading-Hours (statischer Config-Fehler, jederzeit
    relevant) — die Dedup in run_precheck verhindert Alarm-Spam. Fehlt die
    Config (Dev-Rechner), gibt es nichts zu pruefen -> [].
    """
    import json

    if not env_config.get("monitoring", True):
        return []

    # 24/7-Environments (kein Tagesende) koennen action_time nicht verpassen.
    schedule = load_trading_hours(env_name)
    if schedule is None:
        return []

    config_path = _fuchs_config_path(env_name)
    try:
        cfg = json.loads(config_path.read_text())
    except (FileNotFoundError, ValueError):
        return []

    env_cfg = cfg.get("environments", {}).get(env_name, {})
    if not env_cfg.get("enabled", True):
        return []

    strat_dir = config_path.parent / "strategies"
    th_tz = ZoneInfo(schedule["tz"])
    th_end = time.fromisoformat(schedule["end"])
    th_end_min = th_end.hour * 60 + th_end.minute
    severity = "CRITICAL" if env_config["tier"] == "production" else "WARNING"

    anomalies = []
    for strat_file in env_cfg.get("strategy_files", []):
        strat_path = strat_dir / strat_file
        if not strat_path.exists():
            continue
        try:
            strat_config = json.loads(strat_path.read_text())
        except ValueError:
            continue

        action_time_str = strat_config.get("action_time")
        if not action_time_str or action_time_str == "force":
            continue

        action_tz_str = strat_config.get("action_time_tz", "US/Eastern")
        try:
            hh, mm = map(int, action_time_str.split(":"))
            # Sample-Datetime (heute) nur zur Zeitzonen-Konversion — wie in Fuchs.
            sample = datetime.now(ZoneInfo(action_tz_str)).replace(
                hour=hh, minute=mm, second=0, microsecond=0
            )
            action_in_th = sample.astimezone(th_tz)
            action_min = action_in_th.hour * 60 + action_in_th.minute
            if action_min > th_end_min:
                anomalies.append((
                    severity,
                    f"[{env_name}/{strat_file}] action_time {action_time_str} {action_tz_str} "
                    f"= {action_in_th.strftime('%H:%M')} {schedule['tz']}, aber Trading-Hours "
                    f"enden {schedule['end']} — Action feuert NIE",
                ))
        except (ValueError, KeyError):
            continue

    return anomalies


def check_host_disk() -> list[tuple[str, str]]:
    """Env-agnostischer Host-Disk-Watchdog (WARN 85 % / CRIT 95 % belegt).

    Portiert aus Fuchs ``supervisor._check_disk_space`` (verschwindet mit Fuchs
    in Phase 6). Laeuft auf dem Host und feuert daher AUCH wenn kein Runtime
    laeuft — genau das 11.-19.06.-Szenario: ein voller Datentraeger loeschte das
    staging-venv, und der Runtime startete nie wieder. Der Runtime-eigene Check
    (`/status` runtime_health, `<1GB`) greift nur solange er laeuft.

    Alarm != Aufraeumen — die Retention (logrotate) bleibt ein separater
    Host-Task (DOCKER-ARCH.md §7).
    """
    try:
        usage = shutil.disk_usage(Path.home())
    except Exception:
        return []
    used_pct = usage.used / usage.total * 100.0
    free_gb = usage.free / 1024**3
    if used_pct >= 95.0:
        return [(
            "CRITICAL",
            f"[host] Disk {used_pct:.0f}% belegt ({free_gb:.1f}GB frei) — "
            f"voller Datentraeger killte staging 11.-19.06.",
        )]
    if used_pct >= 85.0:
        return [("WARNING", f"[host] Disk {used_pct:.0f}% belegt ({free_gb:.1f}GB frei)")]
    return []


def _strategy_status_note(strat: dict, now: datetime) -> str:
    """Annotation fuer Strategie-Status.

    0DTE-Auflaesung nach Boersenschluss wird nicht mehr live bewertet (siehe
    check_eod_json) — hier bleibt nur der Hinweis fuer heute inaktive Strategien.
    Liefert leeren String wenn keine Erklaerung noetig.
    """
    if not strat.get("is_active_today", True):
        return "nicht aktiv heute"

    return ""


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration (e.g. '5h 23min', '47min')."""
    total_min = int(seconds // 60)
    if total_min < 60:
        return f"{total_min}min"
    hours = total_min // 60
    minutes = total_min % 60
    return f"{hours}h {minutes}min"


def env_status_header() -> str:
    """Build header lines describing current time and per-env trading-hours status.

    Schmales Layout (zwei Zeilen pro Env), damit der Output in Telegram auf
    dem Handy nicht umbricht. Quelle der Trading-Hours: Fuchs-Configs
    (siehe load_trading_hours).
    """
    tz = ZoneInfo("Europe/Berlin")
    now = datetime.now(tz)
    weekday_de = _WEEKDAYS_DE[now.weekday()]
    time_str = now.strftime("%Y-%m-%d %H:%M")

    lines = [
        f"{time_str} {weekday_de} (Berlin)",
    ]

    for env_name, env_config in ENVIRONMENTS.items():
        schedule = load_trading_hours(env_name)

        if schedule is None:
            hours_str = "24/7"
            state = "ACTIVE"
        else:
            wd = schedule["weekdays"]
            if wd == [0, 1, 2, 3, 4]:
                wd_str = "Mo-Fr"
            elif wd == [0, 1, 2, 3, 4, 5, 6]:
                wd_str = "7d"
            else:
                wd_str = ",".join(_WEEKDAYS_DE[d][:2] for d in wd)
            hours_str = f"{schedule['start']}-{schedule['end']} {wd_str}"

            sched_tz = ZoneInfo(schedule["tz"])
            now_local = now.astimezone(sched_tz)
            start = time.fromisoformat(schedule["start"])
            end = time.fromisoformat(schedule["end"])
            today = now_local.date()
            start_dt = datetime.combine(today, start, tzinfo=sched_tz)
            end_dt = datetime.combine(today, end, tzinfo=sched_tz)

            if now_local.weekday() not in wd:
                state = "INACTIVE (kein Werktag)"
            elif now_local < start_dt:
                state = f"INACTIVE (Start in {_format_duration((start_dt - now_local).total_seconds())})"
            elif now_local > end_dt:
                state = "INACTIVE (Tagesende erreicht)"
            else:
                # active — check startup/shutdown grace
                after_start = (now_local - start_dt).total_seconds()
                before_end = (end_dt - now_local).total_seconds()
                if 0 <= after_start < STARTUP_SHUTDOWN_GRACE_SECONDS:
                    state = f"STARTUP ({_format_duration(after_start)} seit Start)"
                elif 0 <= before_end < STARTUP_SHUTDOWN_GRACE_SECONDS:
                    state = f"SHUTDOWN ({_format_duration(before_end)} bis Tagesende)"
                else:
                    state = f"ACTIVE (seit {schedule['start']}, {_format_duration(after_start)})"

        if not env_config.get("monitoring", True):
            state += " [monitoring disabled]"
        lines.append(f"{env_name}  {hours_str}")
        lines.append(f"  {state}")

    return "\n".join(lines)


def env_data_block(now: datetime | None = None, baselines: dict | None = None) -> str:
    """Live-Status pro ACTIVE Environment (Cash/Equity/PnL + Strategien).

    Holt /strategies + /portfolio fuer jedes monitoring-aktive Env, das
    aktuell ACTIVE ist (nicht in Startup/Shutdown-Fenster). Inaktive Envs
    werden nicht angezeigt — der Header zeigt sie ohnehin als INACTIVE.

    Falls baselines uebergeben werden, wird pro Strategie das `character`-
    Feld als zweite Zeile angehaengt (Domaenen-Wissen aus dem Baseline-File,
    nicht aus dem Agent-Prompt).
    """
    if now is None:
        now = datetime.now(ZoneInfo("Europe/Berlin"))
    if baselines is None:
        baselines = load_baselines()

    blocks: list[str] = []
    for env_name, env_config in ENVIRONMENTS.items():
        if not env_config.get("monitoring", True):
            continue
        schedule = load_trading_hours(env_name)
        if not is_trading_time(schedule):
            continue
        if is_in_startup_or_shutdown_window(schedule):
            continue

        port = env_config["port"]
        strategies = api_get(port, "/strategies")
        portfolio = api_get(port, "/portfolio")

        if strategies is None or portfolio is None:
            blocks.append(f"  [{env_name}] API nicht erreichbar (siehe Anomalien)")
            continue

        cash_info = portfolio.get("cash", {}) or {}
        cash = cash_info.get("current_cash", 0)
        currency = cash_info.get("currency", "USD")
        pnl_info = portfolio.get("pnl", {}) or {}
        d_realized = pnl_info.get("daily_realized_pnl", 0)
        d_unrealized = pnl_info.get("daily_unrealized_pnl", 0)
        equity = portfolio.get("equity_check", {}).get("internal_equity", 0)

        env_lines = [
            f"[{env_name}]",
            f"  Cash {currency} {cash:,.2f}",
            f"  Equity {equity:,.2f}",
            f"  PnL real {d_realized:+,.2f} / unreal {d_unrealized:+,.2f}",
        ]
        for s in strategies:
            name = s.get("name", "?")
            fsm = s.get("display", {}).get("fsm_state", "?")
            stats = s.get("stats", {}) or {}
            rpnl = stats.get("realized_pnl", 0) or 0
            upnl = stats.get("unrealized_pnl", 0) or 0
            note = _strategy_status_note(s, now)
            note_str = f" — {note}" if note else ""
            env_lines.append(f"  {name}  {fsm}{note_str}")
            if rpnl or upnl:
                env_lines.append(f"    rPnL {rpnl:+.2f}  uPnL {upnl:+.2f}")
            character = (baselines.get(name) or {}).get("character")
            if character:
                env_lines.append(f"    Char: {character}")
        blocks.append("\n".join(env_lines))

    if not blocks:
        return ""
    return "Live-Status:\n" + "\n\n".join(blocks)


def run_precheck(force_summary: bool = False) -> tuple[int, str]:
    """
    Run full precheck across all environments.
    Returns (exit_code, output_text).
    """
    baselines = load_baselines()
    all_anomalies = []
    header = env_status_header()
    data_block = env_data_block(baselines=baselines)
    if data_block:
        header = header + "\n\n" + data_block

    for env_name, env_config in ENVIRONMENTS.items():
        # Live checks (gated by trading hours) + EOD-JSON validation (works after
        # the runtime has shut down, hence outside the trading-hours gate) +
        # statischer action_time-Config-Check (ungated, jederzeit relevant).
        all_anomalies.extend(check_environment(env_name, env_config, baselines))
        all_anomalies.extend(check_eod_json(env_name, env_config))
        all_anomalies.extend(check_action_times(env_name, env_config))

    # Host-Disk-Watchdog: env-agnostisch, einmal pro Lauf (feuert auch ohne Runtime).
    all_anomalies.extend(check_host_disk())

    if force_summary:
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

        if not summary_files:
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
                return 1, header + "\n\n" + output
            return 2, header + "\n\n" + output

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
            return 1, header + "\n\n" + output
        return 2, header + "\n\n" + output

    if all_anomalies:
        # Deduplicate: only alert on NEW anomalies not seen in the last run
        current = {_anomaly_key(sev, msg): f"[{sev}] {msg}" for sev, msg in all_anomalies}
        previous_keys = set(_load_anomaly_state().keys())
        _save_anomaly_state(current)

        # Only report if there are NEW anomalies not seen last time
        new_keys = set(current) - previous_keys
        if not new_keys:
            # Gleiche Anomalien wie beim letzten Lauf — kein Re-Alert, aber
            # sichtbar auflisten (frueher stand hier nur "suppressed", ohne
            # zu sagen WELCHE Anomalien offen sind).
            lines = [f"OK — {len(current)} bekannte Anomalie(n) (unterdrueckt):"]
            lines.extend(f"  {line}" for line in current.values())
            return 3, header + "\n\n" + "\n".join(lines)

        lines = ["ANOMALIES DETECTED:"]
        for sev, msg in all_anomalies:
            marker = " [NEW]" if _anomaly_key(sev, msg) in new_keys else " [KNOWN]"
            lines.append(f"  [{sev}] {msg}{marker}")
        return 1, header + "\n\n" + "\n".join(lines)

    # Clear state file when no anomalies
    try:
        state_file = PRECHECK_STATE_FILE
        if state_file.exists():
            state_file.unlink()
    except Exception:
        pass

    return 0, header + "\n\nOK — All checks passed"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true", help="Force daily summary output regardless of time window")
    args = parser.parse_args()
    exit_code, output = run_precheck(force_summary=args.summary)
    print(output)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
