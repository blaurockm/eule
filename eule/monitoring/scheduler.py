"""
Config-getriebener Scheduler fuer den Wachtel-Bot.

Ersetzt den alten hartcodierten Scheduler-Thread. Liest Jobs aus
schedule.yaml und fuehrt sie per Cron oder Intervall aus.
Unterstuetzt interne Python-Funktionen und systemd-Unit-Triggering.
"""

import json
import re
import subprocess
import threading
import time as time_module
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger as log

from eule.monitoring.schedule_config import STATE_PATH, JobConfig, ScheduleConfig


# ---------------------------------------------------------------------------
# Cron Parser (5-Feld: Minute Hour DoM Month DoW, DoW 0=Montag)
# ---------------------------------------------------------------------------


def _parse_cron_field(field: str, min_val: int, max_val: int) -> set[int]:
    """Parsed ein einzelnes Cron-Feld in eine Menge erlaubter Werte."""
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if "/" in part:
            base, step_str = part.split("/", 1)
            step = int(step_str)
            if base == "*":
                start, end = min_val, max_val
            elif "-" in base:
                lo, hi = base.split("-", 1)
                start, end = int(lo), int(hi)
            else:
                start, end = int(base), max_val
            values.update(range(start, end + 1, step))
        elif "-" in part:
            lo, hi = part.split("-", 1)
            values.update(range(int(lo), int(hi) + 1))
        elif part == "*":
            values.update(range(min_val, max_val + 1))
        else:
            values.add(int(part))
    return values


def cron_matches(expr: str, dt: datetime) -> bool:
    """Prueft ob ein 5-Feld Cron-Ausdruck auf eine datetime passt.

    Felder: Minute Hour Day-of-Month Month Day-of-Week
    Day-of-Week: 0=Montag (Python-Konvention).
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Cron-Ausdruck braucht 5 Felder, hat {len(fields)}: '{expr}'")

    minutes = _parse_cron_field(fields[0], 0, 59)
    hours = _parse_cron_field(fields[1], 0, 23)
    days = _parse_cron_field(fields[2], 1, 31)
    months = _parse_cron_field(fields[3], 1, 12)
    weekdays = _parse_cron_field(fields[4], 0, 6)

    return (
        dt.minute in minutes
        and dt.hour in hours
        and dt.day in days
        and dt.month in months
        and dt.weekday() in weekdays
    )


def cron_next_fire(expr: str, after: datetime, tz: ZoneInfo | None = None) -> datetime | None:
    """Berechnet den naechsten Ausfuehrungszeitpunkt nach 'after'.

    Iteriert minutenweise vorwaerts, max 8 Tage. Gibt None zurueck
    wenn kein Match gefunden wird (z.B. unmoeglich Cron-Ausdruck).
    """
    # Auf naechste volle Minute aufrunden
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=8)

    while candidate <= limit:
        if cron_matches(expr, candidate):
            return candidate
        candidate += timedelta(minutes=1)

    return None


# ---------------------------------------------------------------------------
# State-File (letzte Ausfuehrung pro Job)
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    """Laedt den Scheduler-State aus ~/.eule/.schedule_state.json."""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict) -> None:
    """Speichert den Scheduler-State atomar (tmp + rename)."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.rename(STATE_PATH)


def load_state() -> dict:
    """Public accessor fuer CLI (eule schedule list)."""
    return _load_state()


# ---------------------------------------------------------------------------
# systemd Helper
# ---------------------------------------------------------------------------


def _run_systemctl(*args: str) -> tuple[int, str]:
    """Fuehrt systemctl --user <args> aus und gibt (exit_code, output) zurueck."""
    cmd = ["systemctl", "--user", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode, output
    except Exception as e:
        return 1, str(e)


# ---------------------------------------------------------------------------
# Scheduler Thread
# ---------------------------------------------------------------------------


class Scheduler(threading.Thread):
    """Config-getriebener Scheduler — ersetzt den alten hartcodierten Scheduler."""

    def __init__(
        self,
        config: ScheduleConfig,
        alert_callback: Callable[[str], None],
        email_callback: Callable[..., None],
        job_registry: dict[str, Callable] | None = None,
    ):
        super().__init__(daemon=True, name="scheduler")
        self.config = config
        self.tz = ZoneInfo(config.timezone)
        self.alert_callback = alert_callback
        self.email_callback = email_callback
        self.running = True

        # Dedup-State: Cron-Jobs → "YYYY-MM-DD HH:MM", Interval-Jobs → monotonic timestamp
        self._last_cron_fire: dict[str, str] = {}
        self._last_interval_run: dict[str, float] = {}

        # Persistent state (letzte Ergebnisse)
        self._state = _load_state()

        # Job-Registry (lazy import, damit telegram_bot.py nicht zirkulaer importiert)
        self._job_registry = job_registry

    def _get_job_registry(self) -> dict[str, Callable]:
        if self._job_registry is None:
            from eule.monitoring.jobs import INTERNAL_JOBS
            self._job_registry = INTERNAL_JOBS
        return self._job_registry

    def run(self):
        log.info("Scheduler gestartet ({} Jobs)", len(self.config.jobs))
        # Startup grace period
        time_module.sleep(30)

        while self.running:
            try:
                now_wall = datetime.now(self.tz)
                now_mono = time_module.monotonic()

                for name, job in self.config.jobs.items():
                    if not job.enabled:
                        continue
                    if self._should_fire(name, job, now_wall, now_mono):
                        self._execute_job(name, job)

            except Exception as e:
                log.error(f"Scheduler-Fehler: {e}")

            time_module.sleep(30)

    def _should_fire(self, name: str, job: JobConfig, now_wall: datetime, now_mono: float) -> bool:
        if job.cron:
            minute_key = now_wall.strftime("%Y-%m-%d %H:%M")
            if self._last_cron_fire.get(name) == minute_key:
                return False
            if cron_matches(job.cron, now_wall):
                self._last_cron_fire[name] = minute_key
                return True
        elif job.interval_minutes:
            last = self._last_interval_run.get(name, 0.0)
            if now_mono - last >= job.interval_minutes * 60:
                self._last_interval_run[name] = now_mono
                return True
        return False

    def _execute_job(self, name: str, job: JobConfig):
        log.info(f"Job '{name}' wird ausgefuehrt (action={job.action})")
        try:
            if job.action == "internal":
                self._run_internal(name, job)
            elif job.action == "systemd":
                self._run_systemd(name, job)
            self._update_state(name, "ok")
        except Exception as e:
            log.error(f"Job '{name}' fehlgeschlagen: {e}")
            self._update_state(name, f"error: {e}")
            self._notify_error(name, job, str(e))

    def _run_internal(self, name: str, job: JobConfig):
        registry = self._get_job_registry()
        fn = registry.get(job.function)
        if fn is None:
            raise ValueError(f"Unbekannte Job-Funktion: '{job.function}'")
        fn(
            alert_callback=self.alert_callback,
            email_callback=self.email_callback,
            job_config=job,
        )

    def _run_systemd(self, name: str, job: JobConfig):
        """Startet eine systemd-Unit und ueberwacht sie in einem Hintergrund-Thread."""
        code, output = _run_systemctl("start", job.unit)
        if code != 0:
            raise RuntimeError(f"Start fehlgeschlagen fuer {job.unit}: {output}")

        log.info(f"systemd-Unit '{job.unit}' gestartet, Monitor-Thread laeuft")
        t = threading.Thread(
            target=self._monitor_systemd_unit,
            args=(name, job),
            daemon=True,
            name=f"monitor-{job.unit}",
        )
        t.start()

    def _monitor_systemd_unit(self, name: str, job: JobConfig):
        """Pollt den Status einer systemd-Unit bis sie fertig ist oder Timeout."""
        deadline = time_module.monotonic() + job.timeout_minutes * 60
        while time_module.monotonic() < deadline:
            time_module.sleep(30)
            _, state = _run_systemctl("is-active", job.unit)
            state = state.strip()

            if state == "inactive":
                log.info(f"systemd-Job '{name}' ({job.unit}) erfolgreich abgeschlossen")
                self._update_state(name, "ok")
                return

            if state == "failed":
                _, status_output = _run_systemctl("status", job.unit)
                error_msg = f"{job.unit} fehlgeschlagen:\n{status_output}"
                log.error(f"systemd-Job '{name}': {error_msg}")
                self._update_state(name, "failed")
                self._notify_error(name, job, error_msg)
                return

        # Timeout
        timeout_msg = f"{job.unit} laeuft noch nach {job.timeout_minutes} Minuten"
        log.warning(f"systemd-Job '{name}': {timeout_msg}")
        self._update_state(name, "timeout")
        self._notify_error(name, job, timeout_msg)

    def _update_state(self, name: str, status: str):
        self._state[name] = {
            "last_run": datetime.now(self.tz).isoformat(timespec="seconds"),
            "last_status": status,
        }
        try:
            _save_state(self._state)
        except Exception as e:
            log.error(f"State-File schreiben fehlgeschlagen: {e}")

    def _notify_error(self, name: str, job: JobConfig, message: str):
        """Sendet Fehler-Benachrichtigung ueber konfigurierte Kanaele."""
        text = f"Scheduler-Fehler [{name}]:\n{message}"
        for ch in job.on_error:
            try:
                if ch == "telegram":
                    self.alert_callback(text)
                elif ch == "email":
                    from eule.monitoring.telegram_bot import _report_to_html
                    self.email_callback(
                        f"Wachtel: Fehler bei {name}",
                        _report_to_html(text, title=f"Scheduler-Fehler: {name}"),
                        html=True,
                    )
            except Exception as e:
                log.error(f"Fehler beim Senden ({ch}) fuer Job '{name}': {e}")

    def stop(self):
        self.running = False
