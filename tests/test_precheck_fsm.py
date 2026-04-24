"""Tests for FSM expectation evaluation in monitoring/precheck.py."""

from datetime import datetime
from zoneinfo import ZoneInfo

from eule.monitoring.precheck import _condition_active, evaluate_fsm_expectation


def et(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("US/Eastern"))


# 2026 calendar: Apr 20=Mon, 21=Tue, 22=Wed, 23=Thu, 24=Fri, 25=Sat, 26=Sun.


# ---------------------------------------------------------------------------
# _condition_active
# ---------------------------------------------------------------------------


class TestNotDay:
    def test_not_tuesday_matches_on_wednesday(self):
        assert _condition_active("not Tuesday", et(2026, 4, 22, 12, 0)) is True

    def test_not_tuesday_is_inactive_on_tuesday(self):
        assert _condition_active("not Tuesday", et(2026, 4, 21, 12, 0)) is False

    def test_not_monday_matches_on_weekend(self):
        assert _condition_active("not Monday", et(2026, 4, 25, 12, 0)) is True


class TestDayWithTime:
    def test_tuesday_after_1130_et_active_tue_1145(self):
        assert _condition_active("Tuesday after 11:30 ET", et(2026, 4, 21, 11, 45)) is True

    def test_tuesday_after_1130_et_inactive_tue_1100(self):
        assert _condition_active("Tuesday after 11:30 ET", et(2026, 4, 21, 11, 0)) is False

    def test_tuesday_after_1130_et_inactive_on_wednesday(self):
        assert _condition_active("Tuesday after 11:30 ET", et(2026, 4, 22, 11, 45)) is False

    def test_wednesday_before_1600_et_active(self):
        assert _condition_active("Wednesday before 16:00 ET", et(2026, 4, 22, 14, 0)) is True

    def test_wednesday_before_1600_et_inactive_after_1600(self):
        assert _condition_active("Wednesday before 16:00 ET", et(2026, 4, 22, 16, 30)) is False

    def test_wednesday_before_1600_et_inactive_on_thursday(self):
        assert _condition_active("Wednesday before 16:00 ET", et(2026, 4, 23, 10, 0)) is False


class TestAfterBeforeWithoutDay:
    def test_after_1600_et_active_anytime(self):
        assert _condition_active("after 16:00 ET", et(2026, 4, 22, 17, 0)) is True

    def test_before_0930_et_active(self):
        assert _condition_active("before 09:30 ET", et(2026, 4, 22, 8, 0)) is True


class TestWeekday:
    def test_weekday_after_1130_active_on_wednesday(self):
        assert _condition_active("weekday after 11:30 ET", et(2026, 4, 22, 12, 0)) is True

    def test_weekday_after_1130_inactive_on_saturday(self):
        assert _condition_active("weekday after 11:30 ET", et(2026, 4, 25, 12, 0)) is False

    def test_weekday_after_1130_inactive_before_1130(self):
        assert _condition_active("weekday after 11:30 ET", et(2026, 4, 22, 11, 0)) is False


class TestCompoundAnd:
    def test_not_tue_and_not_wed_active_on_thursday(self):
        assert _condition_active("not Tuesday and not Wednesday", et(2026, 4, 23, 12, 0)) is True

    def test_not_tue_and_not_wed_inactive_on_tuesday(self):
        assert _condition_active("not Tuesday and not Wednesday", et(2026, 4, 21, 12, 0)) is False

    def test_not_tue_and_not_wed_inactive_on_wednesday(self):
        assert _condition_active("not Tuesday and not Wednesday", et(2026, 4, 22, 12, 0)) is False

    def test_not_thu_and_not_fri_active_on_monday(self):
        assert _condition_active("not Thursday and not Friday", et(2026, 4, 20, 12, 0)) is True


class TestAnyTime:
    def test_any_time_always_active(self):
        assert _condition_active("any time", et(2026, 4, 22, 3, 0)) is True


# ---------------------------------------------------------------------------
# evaluate_fsm_expectation — end-to-end 1DTE scenarios
# ---------------------------------------------------------------------------


class TestOneDTEScenarios:
    """gld-1dte-tue-put style: Entry Tue 11:00 ET, Exit Wed nominal 16:00 ET."""

    def test_wed_before_exit_in_position_ok(self):
        # On the day-after, IN_POSITION is the normal state — no anomaly.
        msg = evaluate_fsm_expectation(
            "Wednesday before 16:00 ET", "IN_POSITION", "IN_POSITION", et(2026, 4, 22, 14, 0)
        )
        assert msg is None

    def test_wed_before_exit_flat_is_anomaly(self):
        # FLAT on day-after before exit: "position gone too early" → anomaly.
        msg = evaluate_fsm_expectation(
            "Wednesday before 16:00 ET", "IN_POSITION", "FLAT", et(2026, 4, 22, 14, 0)
        )
        assert msg is not None
        assert "FLAT" in msg

    def test_thursday_in_position_is_anomaly(self):
        # Thursday — neither entry nor exit day — must be FLAT.
        msg = evaluate_fsm_expectation(
            "not Tuesday and not Wednesday", "FLAT", "IN_POSITION", et(2026, 4, 23, 12, 0)
        )
        assert msg is not None
        assert "IN_POSITION" in msg

    def test_monday_flat_is_ok(self):
        msg = evaluate_fsm_expectation(
            "not Tuesday and not Wednesday", "FLAT", "FLAT", et(2026, 4, 20, 12, 0)
        )
        assert msg is None

    def test_tuesday_after_entry_pending_fill_ok(self):
        # PENDING_FILL is part of the expected set — order is working.
        msg = evaluate_fsm_expectation(
            "Tuesday after 11:30 ET",
            ["IN_POSITION", "PENDING_FILL"],
            "PENDING_FILL",
            et(2026, 4, 21, 11, 45),
        )
        assert msg is None

    def test_tuesday_after_entry_flat_is_anomaly(self):
        # Entry time passed but strategy is still FLAT → "no setup found".
        msg = evaluate_fsm_expectation(
            "Tuesday after 11:30 ET",
            ["IN_POSITION", "PENDING_FILL"],
            "FLAT",
            et(2026, 4, 21, 11, 45),
        )
        assert msg is not None


class TestZeroDTEAlways:
    """spx-0dte-always: entry every weekday 11:00 ET, exit at market close."""

    def test_weekday_after_entry_flat_is_anomaly(self):
        msg = evaluate_fsm_expectation(
            "weekday after 11:30 ET",
            ["IN_POSITION", "PENDING_FILL"],
            "FLAT",
            et(2026, 4, 22, 12, 0),
        )
        assert msg is not None

    def test_weekend_flat_is_ok(self):
        # Weekday-gated rules don't fire on weekends.
        msg = evaluate_fsm_expectation(
            "weekday after 11:30 ET",
            ["IN_POSITION", "PENDING_FILL"],
            "FLAT",
            et(2026, 4, 25, 12, 0),
        )
        assert msg is None
