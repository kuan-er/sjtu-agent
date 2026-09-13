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
