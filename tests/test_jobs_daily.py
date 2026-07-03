"""Tests fuer den Daily-Watcher (job_daily_summary in monitoring/jobs.py).

Der Watcher laeuft als Intervall-Job: pro Env eine Telegram-Nachricht sobald
dessen EOD-JSON existiert, eine Gesamt-Email wenn alle erwarteten Envs
geliefert haben (spaetestens zur Deadline mit "fehlt"-Ausweis).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import eule.monitoring.jobs as jobs
from eule.monitoring.schedule_config import JobConfig

BERLIN = ZoneInfo("Europe/Berlin")

JOB = JobConfig(name="daily_summary", action="internal", function="daily_summary",
                interval_minutes=10)


# 2026-07-01 = Mittwoch
def _wed(h, m):
    return datetime(2026, 7, 1, h, m, tzinfo=BERLIN)


def _summary(env):
    return {"env": env, "date": "2026-07-01", "portfolio": {"daily_pnl": 1.0},
            "strategies": [], "fsm_states": {}}


class Recorder:
    def __init__(self):
        self.telegram: list[str] = []
        self.emails: list[str] = []

    def alert(self, text, **kwargs):
        self.telegram.append(text)

    def email(self, subject, body, html=False):
        self.emails.append(subject)
        return True


def _setup(monkeypatch, tmp_path, summaries: dict, expected: list[str]):
    rec = Recorder()
    monkeypatch.setattr(jobs, "DAILY_SENT_STATE", tmp_path / "daily_sent.json")
    monkeypatch.setattr(jobs, "_load_daily_summary_jsons", lambda date_str: summaries)
    monkeypatch.setattr(jobs, "_expected_daily_envs", lambda now: expected)
    monkeypatch.setattr("eule.monitoring.telegram_bot.send_email", rec.email)
    monkeypatch.setattr("eule.monitoring.precheck.load_open_anomalies", lambda: [])
    return rec


def _run(rec, now):
    jobs.job_daily_summary(rec.alert, rec.email, JOB, now=now)


def test_noop_before_window(monkeypatch, tmp_path):
    rec = _setup(monkeypatch, tmp_path, {"real-ibkr": _summary("real-ibkr")}, ["real-ibkr"])
    _run(rec, _wed(21, 0))
    assert rec.telegram == []
    assert rec.emails == []


def test_env_sent_once_when_json_appears(monkeypatch, tmp_path):
    rec = _setup(monkeypatch, tmp_path, {"real-ibkr": _summary("real-ibkr")},
                 ["real-ibkr", "staging-ibkr"])
    _run(rec, _wed(22, 40))
    assert len(rec.telegram) == 1
    assert "real-ibkr" in rec.telegram[0]
    # Email noch nicht — staging fehlt und Deadline nicht erreicht
    assert rec.emails == []

    # Zweiter Lauf: kein Doppelversand
    _run(rec, _wed(22, 50))
    assert len(rec.telegram) == 1


def test_email_when_all_expected_present(monkeypatch, tmp_path):
    summaries = {"real-ibkr": _summary("real-ibkr"),
                 "staging-ibkr": _summary("staging-ibkr")}
    rec = _setup(monkeypatch, tmp_path, summaries, ["real-ibkr", "staging-ibkr"])
    _run(rec, _wed(23, 40))
    assert len(rec.telegram) == 2
    assert len(rec.emails) == 1
    assert "Wachtel Daily" in rec.emails[0]

    # Kein Doppelversand der Email
    _run(rec, _wed(23, 50))
    assert len(rec.emails) == 1


def test_email_fallback_at_deadline_with_missing(monkeypatch, tmp_path):
    rec = _setup(monkeypatch, tmp_path, {"real-ibkr": _summary("real-ibkr")},
                 ["real-ibkr", "staging-ibkr"])
    _run(rec, _wed(23, 40))
    assert rec.emails == []  # staging fehlt, Deadline nicht erreicht

    _run(rec, _wed(23, 56))
    assert len(rec.emails) == 1  # Deadline: Versand trotz fehlendem Env


def test_state_survives_restart(monkeypatch, tmp_path):
    summaries = {"real-ibkr": _summary("real-ibkr")}
    rec = _setup(monkeypatch, tmp_path, summaries, ["real-ibkr"])
    _run(rec, _wed(22, 40))
    assert len(rec.telegram) == 1
    assert len(rec.emails) == 1  # alle erwarteten da -> Email sofort

    # "Restart": neuer Recorder, gleicher State auf Platte
    rec2 = _setup(monkeypatch, tmp_path, summaries, ["real-ibkr"])
    _run(rec2, _wed(23, 0))
    assert rec2.telegram == []
    assert rec2.emails == []


def test_state_resets_on_new_date(monkeypatch, tmp_path):
    summaries = {"real-ibkr": _summary("real-ibkr")}
    rec = _setup(monkeypatch, tmp_path, summaries, ["real-ibkr"])
    _run(rec, _wed(22, 40))
    assert len(rec.telegram) == 1

    # Naechster Tag (Donnerstag 2026-07-02): State-Reset, Versand erneut
    thu = datetime(2026, 7, 2, 22, 40, tzinfo=BERLIN)
    _run(rec, thu)
    assert len(rec.telegram) == 2


def test_no_expected_envs_is_noop(monkeypatch, tmp_path):
    rec = _setup(monkeypatch, tmp_path, {}, [])
    _run(rec, _wed(23, 0))
    assert rec.telegram == []
    assert rec.emails == []
