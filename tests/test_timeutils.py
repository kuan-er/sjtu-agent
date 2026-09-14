"""Tests for sjtu_agent/timeutils.py — 校园时间（北京时间）vs 用户本地时间。

覆盖：固定校园时区、user_timezone 配置覆盖、系统时区回退、双时区标签。
"""

import datetime as _dt
from zoneinfo import ZoneInfo

import pytest

from sjtu_agent import timeutils as tu


@pytest.fixture
def tz_config(monkeypatch):
    """注入 user_timezone 配置的钩子。"""
    holder = {}

    def _fake_read_config():
        return dict(holder)

    monkeypatch.setattr(tu, "_read_config", _fake_read_config)
    return holder


def test_school_tz_is_utc8():
    now = tu.school_now()
    assert now.utcoffset() == _dt.timedelta(hours=8)
    assert now.tzname() in ("CST", "Asia/Shanghai", "+08", "+0800", "China Standard Time")


def test_user_tz_respects_config_override(tz_config):
    tz_config["user_timezone"] = "America/New_York"
    assert tu.user_tz_name() == "America/New_York"
    assert tu.user_now().utcoffset() in (
        _dt.timedelta(hours=-4),  # EDT
        _dt.timedelta(hours=-5),  # EST
    )


def test_user_tz_invalid_config_falls_back(tz_config):
    tz_config["user_timezone"] = "Not/AZone"
    tu.user_tz_name()  # 不抛异常
    assert tu.user_now() is not None


def test_dual_label_same_zone_single(tz_config):
    tz_config["user_timezone"] = "Asia/Shanghai"
    now = tu.school_now()
    label = tu.dual_time_label(now)
    assert label.startswith("北京时间")
    assert "你那边" not in label


def test_dual_label_different_zone_shows_both(tz_config):
    tz_config["user_timezone"] = "America/New_York"
    # 选一个北京=上午、纽约=前一晚的时刻，保证日历不同
    now = _dt.datetime(2026, 9, 14, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    label = tu.dual_time_label(now)
    assert "9月14日" in label and "北京时间" in label
    assert "你那边" in label and "9月13日" in label


def test_differs_locally_boundary(tz_config):
    tz_config["user_timezone"] = "Asia/Shanghai"
    now = tu.school_now()
    assert tu.differs_locally(now) is False


def test_daily_report_header_abroad_leads_local(monkeypatch):
    """异地日报头行：用户当地日期在前（对齐晨/午/晚标签），北京时间为参考。

    回归用户实测：PDT 机器本地周日晚触发，旧头行读成"09/14 晚报 · 你当地
    09/13"，日期错乱。正确语义：头行锚用户当地日，课程/DDL 事实仍北京时间。
    """
    from scripts import daily_report as dr
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(tu, "_read_config", lambda: {"user_timezone": "America/Los_Angeles"})
    # 北京 09-14（周一）13:00 = PDT 09-13（周日）22:00 —— 用户周日晚的"晚报"
    now = _dt.datetime(2026, 9, 14, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    header, abroad = dr._build_date_header(now)

    assert abroad is True
    assert header.startswith("2026年09月13日（星期日，你当地）")
    assert "北京时间 09月14日" in header


def test_daily_report_header_home_single_timezone(monkeypatch):
    from scripts import daily_report as dr

    monkeypatch.setattr(tu, "_read_config", lambda: {"user_timezone": "Asia/Shanghai"})
    now = _dt.datetime(2026, 9, 14, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    header, abroad = dr._build_date_header(now)

    assert abroad is False
    assert header == "2026年09月14日（星期一）"
    assert "你当地" not in header
