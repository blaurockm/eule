"""Tests fuer den Cron-Parser in eule/monitoring/scheduler.py."""

from datetime import datetime

import pytest
from zoneinfo import ZoneInfo

from eule.monitoring.scheduler import cron_matches, cron_next_fire, _parse_cron_field


class TestParseCronField:
    """Einzelne Cron-Felder parsen."""

    def test_wildcard(self):
        assert _parse_cron_field("*", 0, 59) == set(range(0, 60))

    def test_literal(self):
        assert _parse_cron_field("15", 0, 59) == {15}

    def test_range(self):
        assert _parse_cron_field("1-5", 0, 6) == {1, 2, 3, 4, 5}

    def test_list(self):
        assert _parse_cron_field("1,3,5", 0, 6) == {1, 3, 5}

    def test_step_on_wildcard(self):
        assert _parse_cron_field("*/15", 0, 59) == {0, 15, 30, 45}

    def test_step_on_range(self):
        assert _parse_cron_field("1-10/3", 0, 59) == {1, 4, 7, 10}

    def test_complex(self):
        # "0,30" = minute 0 und 30
        assert _parse_cron_field("0,30", 0, 59) == {0, 30}


class TestCronMatches:
    """Cron-Ausdruecke gegen Datetimes matchen."""

    def test_every_minute(self):
        dt = datetime(2026, 4, 10, 14, 30)
        assert cron_matches("* * * * *", dt)

    def test_specific_time(self):
        # 21:00
        dt = datetime(2026, 4, 10, 21, 0)  # Donnerstag
        assert cron_matches("0 21 * * *", dt)

    def test_specific_time_no_match(self):
        dt = datetime(2026, 4, 10, 21, 1)
        assert not cron_matches("0 21 * * *", dt)

    def test_weekday_monday_to_friday(self):
        # 2026-04-06 ist Montag (weekday=0), 2026-04-10 ist Freitag (weekday=4)
        monday = datetime(2026, 4, 6, 21, 0)
        friday = datetime(2026, 4, 10, 21, 0)
        saturday = datetime(2026, 4, 11, 21, 0)
        assert cron_matches("0 21 * * 0-4", monday)
        assert cron_matches("0 21 * * 0-4", friday)
        assert not cron_matches("0 21 * * 0-4", saturday)

    def test_friday_only(self):
        # DoW 4 = Freitag
        friday = datetime(2026, 4, 10, 21, 15)
        thursday = datetime(2026, 4, 9, 21, 15)
        assert cron_matches("15 21 * * 4", friday)
        assert not cron_matches("15 21 * * 4", thursday)

    def test_every_15_minutes(self):
        dt_0 = datetime(2026, 4, 10, 14, 0)
        dt_15 = datetime(2026, 4, 10, 14, 15)
        dt_30 = datetime(2026, 4, 10, 14, 30)
        dt_7 = datetime(2026, 4, 10, 14, 7)
        assert cron_matches("*/15 * * * *", dt_0)
        assert cron_matches("*/15 * * * *", dt_15)
        assert cron_matches("*/15 * * * *", dt_30)
        assert not cron_matches("*/15 * * * *", dt_7)

    def test_daily_at_23(self):
        dt = datetime(2026, 4, 10, 23, 0)
        assert cron_matches("0 23 * * *", dt)

    def test_invalid_field_count_raises(self):
        with pytest.raises(ValueError, match="5 Felder"):
            cron_matches("* * *", datetime(2026, 4, 10))

    def test_specific_month(self):
        jan = datetime(2026, 1, 15, 10, 0)
        apr = datetime(2026, 4, 15, 10, 0)
        assert cron_matches("0 10 15 1 *", jan)
        assert not cron_matches("0 10 15 1 *", apr)


class TestCronNextFire:
    """Naechsten Ausfuehrungszeitpunkt berechnen."""

    def test_next_minute(self):
        tz = ZoneInfo("Europe/Berlin")
        after = datetime(2026, 4, 10, 14, 0, tzinfo=tz)
        # Jede Minute → naechster ist 14:01
        nf = cron_next_fire("* * * * *", after, tz)
        assert nf is not None
        assert nf.hour == 14
        assert nf.minute == 1

    def test_next_daily(self):
        tz = ZoneInfo("Europe/Berlin")
        # Nach 21:00 → naechster ist morgen 21:00
        after = datetime(2026, 4, 10, 21, 30, tzinfo=tz)
        nf = cron_next_fire("0 21 * * *", after, tz)
        assert nf is not None
        assert nf.day == 11
        assert nf.hour == 21
        assert nf.minute == 0

    def test_next_weekday(self):
        tz = ZoneInfo("Europe/Berlin")
        # Freitag 21:30 → naechster Mo-Fr 21:00 ist Montag
        friday_late = datetime(2026, 4, 10, 21, 30, tzinfo=tz)
        nf = cron_next_fire("0 21 * * 0-4", friday_late, tz)
        assert nf is not None
        assert nf.weekday() == 0  # Montag
        assert nf.day == 13

    def test_next_friday(self):
        tz = ZoneInfo("Europe/Berlin")
        # Montag → naechster Freitag 21:15
        monday = datetime(2026, 4, 6, 10, 0, tzinfo=tz)
        nf = cron_next_fire("15 21 * * 4", monday, tz)
        assert nf is not None
        assert nf.weekday() == 4  # Freitag
        assert nf.day == 10
        assert nf.hour == 21
        assert nf.minute == 15

    def test_returns_none_for_impossible(self):
        tz = ZoneInfo("Europe/Berlin")
        # 31. Februar gibt es nicht, aber cron_next_fire gibt trotzdem None
        # weil max 8 Tage vorwaerts gesucht wird
        after = datetime(2026, 2, 1, 0, 0, tzinfo=tz)
        nf = cron_next_fire("0 0 31 2 *", after, tz)
        assert nf is None
