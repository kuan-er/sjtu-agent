"""Tests for startup profile analysis (issue #113 #4).

Covers:
- build_profile_ctx(): reads user_profile.json → compact context string
- UserProfile.needs_deep_update(): decides whether LLM re-analysis is due
"""

import datetime as dt
import json

import pytest

from sjtu_agent import paths
from sjtu_agent.bots._core import build_profile_ctx
from sjtu_agent.news_aggregator.profile import UserProfile


def _write_profile(monkeypatch, tmp_path, data: dict):
    p = tmp_path / "user_profile.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(paths, "USER_PROFILE_PATH", p)
    return p


def test_build_profile_ctx_empty(monkeypatch, tmp_path):
    """画像文件不存在 → 返回空串。"""
    monkeypatch.setattr(paths, "USER_PROFILE_PATH", tmp_path / "user_profile.json")
    assert build_profile_ctx() == ""


def test_build_profile_ctx_broken_json(monkeypatch, tmp_path):
    """画像文件损坏 → 返回空串而非崩溃。"""
    p = tmp_path / "user_profile.json"
    p.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(paths, "USER_PROFILE_PATH", p)
    assert build_profile_ctx() == ""


def test_build_profile_ctx_content(monkeypatch, tmp_path):
    """有 persona + 结构化字段 + interests → 输出精选字段，过滤低权重话题。"""
    _write_profile(monkeypatch, tmp_path, {
        "persona_summary": "计算机系大三学生，正在准备考研",
        "name": "小明",
        "major": "计算机",
        "courses": ["操作系统", "算法"],
        "interests": {"考研": 0.9, "编程": 0.7, "无关": 0.01},
        "care_notes": ["明天考物理"],
    })
    ctx = build_profile_ctx()
    assert "用户画像" in ctx
    assert "计算机系大三学生" in ctx
    assert "姓名: 小明" in ctx
    assert "操作系统" in ctx
    assert "考研" in ctx          # top interest
    assert "无关" not in ctx       # 低权重话题被过滤
    assert "明天考物理" in ctx     # care_notes


def test_build_profile_ctx_no_persona_but_fields(monkeypatch, tmp_path):
    """无 persona 但有结构字段 → 仍输出字段。"""
    _write_profile(monkeypatch, tmp_path, {"name": "小红", "mood": "tired"})
    ctx = build_profile_ctx()
    assert "姓名: 小红" in ctx
    assert "情绪: tired" in ctx


def test_needs_deep_update(monkeypatch, tmp_path):
    p = _write_profile(monkeypatch, tmp_path, {})
    prof = UserProfile()

    # 无 persona → 需要分析
    p.write_text(json.dumps({"conversation_count": 5, "persona_summary": ""}), encoding="utf-8")
    assert prof.needs_deep_update() is True

    # persona 存在且计数未增长 → 不需要
    p.write_text(json.dumps({
        "conversation_count": 5, "persona_summary": "x", "last_analyzed_count": 5,
    }), encoding="utf-8")
    assert prof.needs_deep_update() is False

    # 分析后有新对话 → 需要
    p.write_text(json.dumps({
        "conversation_count": 8, "persona_summary": "x", "last_analyzed_count": 5,
    }), encoding="utf-8")
    assert prof.needs_deep_update() is True


# ── 时效性（issue：bot 念旧账）─────────────────────────────────────────────

def _ts(days_ago: float) -> str:
    return (dt.datetime.now() - dt.timedelta(days=days_ago)).isoformat()


def test_expiry_drops_stale_care_notes(monkeypatch, tmp_path):
    _write_profile(monkeypatch, tmp_path, {
        "care_notes": ["明天考物理"],
        "_timestamps": {"care_notes": _ts(3)},
        "last_updated": _ts(3),
    })
    ctx = build_profile_ctx()
    assert "明天考物理" not in ctx
    assert "关怀提醒" not in ctx


def test_expiry_keeps_fresh_care_notes(monkeypatch, tmp_path):
    _write_profile(monkeypatch, tmp_path, {
        "care_notes": ["明天考物理"],
        "_timestamps": {"care_notes": _ts(0)},
        "last_updated": _ts(0),
    })
    assert "明天考物理" in build_profile_ctx()


def test_persona_expired_dropped(monkeypatch, tmp_path):
    """40 天前的 persona（如"下周开始小学期"）应过期不注入。"""
    _write_profile(monkeypatch, tmp_path, {
        "persona_summary": "下周开始小学期",
        "_timestamps": {"persona_summary": _ts(40)},
        "last_updated": _ts(40),
    })
    ctx = build_profile_ctx()
    assert "小学期" not in ctx


def test_persona_fresh_with_caveat(monkeypatch, tmp_path):
    _write_profile(monkeypatch, tmp_path, {
        "persona_summary": "嵌入式方向",
        "_timestamps": {"persona_summary": _ts(0)},
        "last_updated": _ts(0),
    })
    ctx = build_profile_ctx()
    assert "嵌入式方向" in ctx
    assert "与当前时间/学期矛盾" in ctx  # 防误导标注


def test_stable_fields_never_expire(monkeypatch, tmp_path):
    """姓名/专业是稳定字段，200 天前写入仍应显示。"""
    _write_profile(monkeypatch, tmp_path, {
        "name": "小明", "major": "计算机",
        "_timestamps": {"name": _ts(200), "major": _ts(200)},
        "last_updated": _ts(200),
    })
    ctx = build_profile_ctx()
    assert "小明" in ctx
    assert "计算机" in ctx


def test_is_fresh_helper():
    from sjtu_agent.news_aggregator.profile import _is_fresh
    data = {"_timestamps": {"care_notes": _ts(1 / 24)}, "last_updated": _ts(0)}  # 1 小时前
    assert _is_fresh(data, "care_notes") is True
    data["_timestamps"]["care_notes"] = _ts(3)  # 3 天前 → 过期
    assert _is_fresh(data, "care_notes") is False
    assert _is_fresh(data, "name") is True  # 稳定字段恒 fresh


def test_update_user_profile_records_timestamps(monkeypatch, tmp_path):
    from sjtu_agent.agent.tools import _user_profile as up_mod

    p = tmp_path / "user_profile.json"
    monkeypatch.setattr(up_mod, "USER_PROFILE_PATH", p)  # patch 模块全局（顶层 import 绑定）
    up_mod.tool_update_user_profile({"mood": "happy"}, reason="test")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["_timestamps"]["mood"]  # 有记录时间
    assert data["last_updated"]
