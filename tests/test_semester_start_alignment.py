"""Tests for ddl_checker._effective_semester_start — 校历优先的周次锚点对齐。

官方校历（academic_calendar.json 随包数据）覆盖当天所在学期时，
学期第一周周一取校历 start_date；未覆盖时回退 config['semester_start']。
"""

import datetime as _dt

import ddl_checker as dc


def test_calendar_wins_over_stale_cfg():
    """开学后：上学期遗留的 semester_start 不再生效，自动对齐到秋季第1周。"""
    cfg = {"semester_start": "2026-03-02"}
    assert dc._effective_semester_start(cfg, now=_dt.date(2026, 9, 15)) == "2026-09-14"
    assert dc._effective_semester_start(cfg, now=_dt.date(2026, 12, 25)) == "2026-09-14"


def test_cfg_fallback_outside_calendar():
    """寒暑假空档期：校历未覆盖 → 沿用手动配置（保持旧行为）。"""
    cfg = {"semester_start": "2026-03-02"}
    assert dc._effective_semester_start(cfg, now=_dt.date(2026, 8, 28)) == "2026-03-02"


def test_empty_when_no_calendar_coverage_and_no_cfg():
    assert dc._effective_semester_start({}, now=_dt.date(2026, 8, 28)) == ""


def test_stale_cfg_cannot_produce_weeks_in_new_term():
    """端到端口径：9/15 用旧锚点应得不出荒谬周次（自动对齐后=第1周）。"""
    cfg = {"semester_start": "2026-03-02"}
    start_str = dc._effective_semester_start(cfg, now=_dt.date(2026, 9, 15))
    week = (_dt.date(2026, 9, 15) - _dt.date.fromisoformat(start_str)).days // 7 + 1
    assert week == 1
