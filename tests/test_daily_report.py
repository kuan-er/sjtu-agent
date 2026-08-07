"""Tests for daily_report optimizations (早/中/晚报优化).

Covers:
- _feishu_bot_running(): heartbeat freshness gates feishu push
- _is_quiet_day(): skip report when no DDL / classes / news
- _section_has_content(): drop empty modules instead of writing "暂无"
"""

import datetime as dt
import json

import pytest

from scripts import daily_report
from sjtu_agent import paths


# ── _feishu_bot_running ─────────────────────────────────────────────────────

def _write_heartbeat(monkeypatch, tmp_path, last_heartbeat: str | None):
    hb = tmp_path / "feishu_heartbeat.json"
    if last_heartbeat is None:
        hb.write_text(json.dumps({"status": "stopped", "last_heartbeat": ""}), encoding="utf-8")
    else:
        hb.write_text(json.dumps({"status": "running", "last_heartbeat": last_heartbeat}), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)


def test_feishu_bot_running_fresh(monkeypatch, tmp_path):
    fresh = (dt.datetime.now() - dt.timedelta(seconds=10)).isoformat()
    _write_heartbeat(monkeypatch, tmp_path, fresh)
    assert daily_report._feishu_bot_running() is True


def test_feishu_bot_running_stale(monkeypatch, tmp_path):
    stale = (dt.datetime.now() - dt.timedelta(seconds=120)).isoformat()
    _write_heartbeat(monkeypatch, tmp_path, stale)
    assert daily_report._feishu_bot_running() is False


def test_feishu_bot_running_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    assert daily_report._feishu_bot_running() is False


def test_feishu_bot_running_empty_heartbeat(monkeypatch, tmp_path):
    _write_heartbeat(monkeypatch, tmp_path, None)
    assert daily_report._feishu_bot_running() is False


# ── _is_quiet_day ───────────────────────────────────────────────────────────

def test_is_quiet_day_all_empty():
    assert daily_report._is_quiet_day([], {"courses": []}, "") is True


def test_is_quiet_day_has_ddl():
    assert daily_report._is_quiet_day([{"name": "作业", "expired": False}], {"courses": []}, "") is False


def test_is_quiet_day_has_courses():
    assert daily_report._is_quiet_day([], {"courses": [{"name": "操作系统"}]}, "") is False


def test_is_quiet_day_has_news():
    assert daily_report._is_quiet_day([], {"courses": []}, "校园新闻") is False


def test_is_quiet_day_expired_ddl_ignored():
    """只有已过期 DDL 不算有内容。"""
    assert daily_report._is_quiet_day([{"name": "过期", "expired": True}], {"courses": []}, "") is True


# ── _section_has_content ────────────────────────────────────────────────────

def test_section_has_content_ddl():
    assert daily_report._section_has_content("ddl", [{"expired": False}], None, None, None, None) is True
    assert daily_report._section_has_content("ddl", [], None, None, None, None) is False
    assert daily_report._section_has_content("ddl", [{"expired": True}], None, None, None, None) is False


def test_section_has_content_schedule():
    assert daily_report._section_has_content("schedule", [], {"courses": [{"name": "课"}]}, None, None, None) is True
    assert daily_report._section_has_content("schedule", [], {"courses": []}, None, None, None) is False
    assert daily_report._section_has_content("schedule", [], None, None, None, None) is False


def test_section_has_content_news_and_tips():
    assert daily_report._section_has_content("news", [], None, None, None, " 新闻 ") is True
    assert daily_report._section_has_content("news", [], None, None, None, "") is False
    # tips 始终保留
    assert daily_report._section_has_content("tips", [], None, None, None, "") is True
