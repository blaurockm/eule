"""
Schedule-Config fuer den Wachtel-Scheduler.

Laedt ~/.eule/schedule.yaml — definiert alle periodischen Jobs
(interne Funktionen + systemd-Units) mit Cron/Intervall-Scheduling.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from eule.config import EULE_DIR


SCHEDULE_PATH = EULE_DIR / "schedule.yaml"
STATE_PATH = EULE_DIR / ".schedule_state.json"

VALID_ACTIONS = ("internal", "systemd")
VALID_CHANNELS = ("telegram", "email")


class ScheduleConfigError(Exception):
    """Fehler beim Laden oder Validieren der Schedule-Config."""


@dataclass(frozen=True)
class JobConfig:
    """Konfiguration eines einzelnen Scheduler-Jobs."""

    name: str
    action: str  # "internal" | "systemd"
    function: str = ""  # fuer action=internal
    unit: str = ""  # fuer action=systemd
    cron: str = ""  # 5-Feld Cron (Minute Hour DoM Month DoW)
    interval_minutes: int = 0
    notify: tuple[str, ...] = ()
    on_error: tuple[str, ...] = ("telegram",)
    timeout_minutes: int = 60
    enabled: bool = True


@dataclass(frozen=True)
class ScheduleConfig:
    """Gesamte Scheduler-Konfiguration."""

    timezone: str = "Europe/Berlin"
    jobs: dict[str, JobConfig] = field(default_factory=dict)


def _parse_job(name: str, raw: dict) -> JobConfig:
    """Parsed eine Job-Konfiguration aus dem YAML dict."""
    action = raw.get("action", "")
    if action not in VALID_ACTIONS:
        raise ScheduleConfigError(
            f"Job '{name}': action muss {VALID_ACTIONS} sein, ist '{action}'"
        )

    cron = raw.get("cron", "")
    interval = raw.get("interval_minutes", 0)
    if bool(cron) == bool(interval):
        raise ScheduleConfigError(
            f"Job '{name}': genau eins von 'cron' oder 'interval_minutes' angeben"
        )

    if action == "internal" and not raw.get("function"):
        raise ScheduleConfigError(f"Job '{name}': action=internal braucht 'function'")
    if action == "systemd" and not raw.get("unit"):
        raise ScheduleConfigError(f"Job '{name}': action=systemd braucht 'unit'")

    notify = tuple(raw.get("notify", ()))
    on_error = tuple(raw.get("on_error", ("telegram",)))
    for ch in notify + on_error:
        if ch not in VALID_CHANNELS:
            raise ScheduleConfigError(
                f"Job '{name}': unbekannter Kanal '{ch}', erlaubt: {VALID_CHANNELS}"
            )

    return JobConfig(
        name=name,
        action=action,
        function=raw.get("function", ""),
        unit=raw.get("unit", ""),
        cron=cron,
        interval_minutes=interval,
        notify=notify,
        on_error=on_error,
        timeout_minutes=raw.get("timeout_minutes", 60),
        enabled=raw.get("enabled", True),
    )


def load_schedule(path: Path | None = None) -> ScheduleConfig:
    """Laedt die Schedule-Konfiguration aus YAML.

    Args:
        path: Pfad zur schedule.yaml. Default: ~/.eule/schedule.yaml
    """
    schedule_path = path or SCHEDULE_PATH
    if not schedule_path.exists():
        raise ScheduleConfigError(
            f"Schedule-Config nicht gefunden: {schedule_path}\n"
            "Erstelle mit: eule config init"
        )

    with open(schedule_path) as f:
        raw = yaml.safe_load(f) or {}

    jobs: dict[str, JobConfig] = {}
    for name, job_raw in raw.get("jobs", {}).items():
        if isinstance(job_raw, dict):
            jobs[name] = _parse_job(name, job_raw)

    return ScheduleConfig(
        timezone=raw.get("timezone", "Europe/Berlin"),
        jobs=jobs,
    )


SCHEDULE_TEMPLATE = """\
# Wachtel Schedule — ~/.eule/schedule.yaml
#
# Cron: Minute Hour Day-of-Month Month Day-of-Week
# Day-of-Week: 0=Montag (Python-Konvention)
# Kanaele: telegram, email

timezone: Europe/Berlin

jobs:
  precheck:
    action: internal
    function: precheck
    interval_minutes: 15
    notify: [telegram]
    on_error: [telegram, email]

  daily_summary:
    action: internal
    function: daily_summary
    cron: "45 22 * * 0-4"          # Mo-Fr 22:45
    notify: [telegram, email]
    on_error: [telegram, email]

  weekly_report:
    action: internal
    function: weekly_report
    cron: "0 23 * * 4"            # Fr 23:00
    notify: [telegram, email]
    on_error: [telegram, email]

  ep_brief:
    action: internal
    function: ep_brief
    cron: "0 14 * * 0-4"          # Mo-Fr 14:00
    notify: [email]
    on_error: [telegram]

  hamster_ibkr:
    action: systemd
    unit: hamster-ibkr.service
    cron: "0 23 * * *"
    timeout_minutes: 150
    on_error: [telegram, email]

  hamster_fred:
    action: systemd
    unit: hamster-fred.service
    cron: "30 23 * * *"
    timeout_minutes: 30
    on_error: [telegram]

  hamster_derived:
    action: systemd
    unit: hamster-derived.service
    cron: "45 23 * * *"
    timeout_minutes: 60
    on_error: [telegram]

  hamster_crypto:
    action: systemd
    unit: hamster-crypto.service
    cron: "0 6 * * *"
    timeout_minutes: 30
    on_error: [telegram]
"""
