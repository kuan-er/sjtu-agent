"""Tests for sjtu_agent/calendar.py — AcademicCalendar."""
import datetime as _dt
from pathlib import Path

import pytest

from sjtu_agent.calendar import AcademicCalendar


@pytest.fixture
def cal():
    """Calendar pointing to the real data file shipped with the package."""
    from sjtu_agent.paths import DATA_DIR
    return AcademicCalendar(DATA_DIR)


class TestHolidays:
    def test_dragon_boat(self, cal):
        is_hol, name = cal.is_holiday(_dt.date(2026, 6, 19))
        assert is_hol is True
        assert "端午" in name

    def test_labor_day(self, cal):
        is_hol, name = cal.is_holiday(_dt.date(2026, 5, 1))
        assert is_hol is True
        assert "劳动" in name

    def test_qingming(self, cal):
        is_hol, name = cal.is_holiday(_dt.date(2026, 4, 5))
        assert is_hol is True
        assert "清明" in name

    def test_not_holiday(self, cal):
        is_hol, _ = cal.is_holiday(_dt.date(2026, 3, 15))
        assert is_hol is False

    def test_holiday_label_returned(self, cal):
        _, name = cal.is_holiday(_dt.date(2026, 4, 4))
        assert name == "清明节"

    def test_anniversary(self, cal):
        is_hol, name = cal.is_holiday(_dt.date(2026, 4, 7))
        assert is_hol is True
        assert "校庆" in name

    def test_mid_autumn(self, cal):
        is_hol, name = cal.is_holiday(_dt.date(2026, 9, 25))
        assert is_hol is True
        assert "中秋" in name

    def test_national_day(self, cal):
        is_hol, name = cal.is_holiday(_dt.date(2026, 10, 1))
        assert is_hol is True
        assert "国庆" in name


class TestMakeupDays:
    def test_labor_makeup(self, cal):
        is_mk, note = cal.is_makeup_day(_dt.date(2026, 5, 9))
        assert is_mk is True
        assert "周一" in note

    def test_not_makeup(self, cal):
        is_mk, _ = cal.is_makeup_day(_dt.date(2026, 5, 10))
        assert is_mk is False

    def test_anniversary_makeup(self, cal):
        is_mk, note = cal.is_makeup_day(_dt.date(2026, 4, 11))
        assert is_mk is True
        assert "周二" in note

    def test_national_makeup_sep(self, cal):
        is_mk, note = cal.is_makeup_day(_dt.date(2026, 9, 20))
        assert is_mk is True
        assert "周五" in note

    def test_national_makeup_oct(self, cal):
        is_mk, note = cal.is_makeup_day(_dt.date(2026, 10, 10))
        assert is_mk is True
        assert "周二" in note


class TestContext:
    def test_holiday_context(self, cal):
        ctx = cal.get_context(_dt.date(2026, 6, 19))
        assert "端午" in ctx
        assert "放假" in ctx

    def test_makeup_context(self, cal):
        ctx = cal.get_context(_dt.date(2026, 5, 9))
        assert "补课" in ctx or "调休" in ctx

    def test_normal_day_empty(self, cal):
        ctx = cal.get_context(_dt.date(2026, 3, 15))
        assert ctx == ""

    def test_today_does_not_crash(self, cal):
        """Sanity: calling with no args should not throw."""
        ctx = cal.get_context()
        assert isinstance(ctx, str)


class TestSemester:
    def test_spring_semester(self, cal):
        sem = cal.get_semester(_dt.date(2026, 3, 15))
        assert sem == "2025-2026-2"

    def test_summer_semester(self, cal):
        sem = cal.get_semester(_dt.date(2026, 7, 10))
        assert sem == "2025-2026-3"

    def test_autumn_semester(self, cal):
        sem = cal.get_semester(_dt.date(2026, 10, 1))
        assert sem == "2026-2027-1"

    def test_summer_no_holidays(self, cal):
        is_hol, _ = cal.is_holiday(_dt.date(2026, 7, 10))
        assert is_hol is False

    def test_summer_no_makeup(self, cal):
        is_mk, _ = cal.is_makeup_day(_dt.date(2026, 7, 10))
        assert is_mk is False

    def test_summer_context_empty(self, cal):
        ctx = cal.get_context(_dt.date(2026, 7, 10))
        assert ctx == ""

    def test_summer_break_context(self, cal):
        ctx = cal.get_context(_dt.date(2026, 8, 16))
        assert "暑假" in ctx
        assert "不在校内" in ctx
        assert "29 天" in ctx

    def test_winter_break_context(self, cal):
        ctx = cal.get_context(_dt.date(2026, 1, 20))
        assert "寒假" in ctx
        assert "不在校内" in ctx

    def test_next_semester_start(self, cal):
        assert cal.next_semester_start(_dt.date(2026, 8, 16)) == _dt.date(2026, 9, 14)
        assert cal.next_semester_start(_dt.date(2027, 1, 20)) is None
