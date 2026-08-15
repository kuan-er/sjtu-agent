from __future__ import annotations

import sys
from types import SimpleNamespace

from sjtu_agent.commands import (
    CORE_COMMAND_REGISTRY,
    CommandResult,
    is_core_command,
    parse_command,
    run_command,
)
from sjtu_agent.commands.dining import fetch_eat_recommendation
from sjtu_agent.commands.news import fetch_news_digest


def test_parse_command():
    assert parse_command("/HW do 3") == ("/hw", ["/HW", "do", "3"])
    assert parse_command("   /news  ") == ("/news", ["/news"])
    assert parse_command("你好") is None


def test_is_core_command():
    assert is_core_command("/hw")
    assert is_core_command("/news_block 教务处")
    assert is_core_command("/eat 徐汇")
    assert not is_core_command("/unknown_cmd")
    assert not is_core_command("普通消息")


def test_run_command_returns_none_for_non_commands():
    assert run_command("普通消息") is None
    assert run_command("/unknown_cmd") is None


def test_run_command_hw_due_invalid():
    """参数校验不依赖真实 homework 数据。"""
    result = run_command("/hw due abc")
    assert isinstance(result, CommandResult)
    assert result.text == "无效天数：abc。用法：/hw due <N>"
    assert result.view == "homework"
    assert result.data["ok"] is False


def test_run_command_eat_invalid_campus():
    result = run_command("/eat 伦敦")
    assert isinstance(result, CommandResult)
    assert result.text == "[eat] 未知校区「伦敦」，可选：闵行 / 徐汇 / 张江"
    assert result.data["mode"] == "invalid_campus"
    assert result.data["valid"] == ["闵行", "徐汇", "张江"]


def test_run_command_news_block_without_category():
    result = run_command("/news_block")
    assert isinstance(result, CommandResult)
    assert "请指定要屏蔽的分类" in result.text
    assert result.data["action"] == "block"
    assert result.data["ok"] is False


def test_run_command_wraps_exceptions(monkeypatch):
    def boom(user_id, parts):
        raise RuntimeError("mock boom")

    monkeypatch.setitem(CORE_COMMAND_REGISTRY, "/boom", boom)
    result = run_command("/boom")
    assert isinstance(result, CommandResult)
    assert "[命令错误] `/boom`" in result.text
    assert "mock boom" in result.text
    assert result.data["error"] == "mock boom"


def test_news_block_updates_profile(monkeypatch):
    class FakeProfile:
        def __init__(self):
            self.blocked = None

        def block_category(self, category):
            self.blocked = category

    profile = FakeProfile()
    monkeypatch.setattr("sjtu_agent.news_aggregator.profile.UserProfile", lambda: profile)
    result = run_command("/news_block 教务处")
    assert profile.blocked == "教务处"
    assert "教务处" in result.text
    assert result.data == {"ok": True, "action": "block", "category": "教务处"}


def test_fetch_news_digest_without_llm(monkeypatch):
    class FakeAggregator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_structured(self, hours, top_k):
            assert top_k == 8
            return "mock digest", None, None, [{"title": "测试新闻"}]

    fake_module = SimpleNamespace(NewsAggregator=FakeAggregator)
    monkeypatch.setitem(sys.modules, "sjtu_agent.news_aggregator", fake_module)
    assert fetch_news_digest() == "mock digest"


def test_cmd_news_returns_structured_items(monkeypatch):
    class FakeAggregator:
        def __init__(self, **kwargs):
            pass

        def run_structured(self, hours, top_k):
            return "digest", None, None, [
                {"id": "1", "title": "新闻标题", "source": "jwc", "url": "https://example.com"},
            ]

    monkeypatch.setitem(
        sys.modules,
        "sjtu_agent.news_aggregator",
        SimpleNamespace(NewsAggregator=FakeAggregator),
    )
    result = run_command("/news")
    assert result is not None
    assert result.view == "news"
    assert result.data["digest"] == "digest"
    assert result.data["items"][0]["title"] == "新闻标题"


def test_cmd_hw_list_returns_structured_assignments(monkeypatch):
    monkeypatch.setattr(
        "sjtu_agent.homework_agent.fetch_homework_list",
        lambda **kwargs: (
            "共 1 个作业",
            [{"index": 1, "course": "数学分析", "name": "作业 1", "due": "2026-09-01"}],
        ),
    )
    result = run_command("/hw")
    assert result is not None
    assert result.view == "homework"
    assert result.data["kind"] == "list"
    assert result.data["assignments"][0]["index"] == 1


def test_fetch_eat_recommendation_ok(monkeypatch):
    result = {
        "ok": True,
        "meal_type": "午餐",
        "campus": "闵行",
        "summary": "今日适合清淡",
        "recommendations": [
            {
                "canteen_name": "一餐",
                "overall_label": "空闲",
                "overall_rate": 20,
                "reasons": ["人少"],
                "recommended_sub_areas": ["面馆", "自选"],
            }
        ],
        "has_history": True,
        "history_count": 3,
    }
    monkeypatch.setattr(
        "sjtu_agent.agent.tools._dining.tool_recommend_canteen",
        lambda campus: result,
    )
    text = fetch_eat_recommendation("闵行")
    assert "一餐" in text
    assert "面馆" in text
    assert "历史记录" in text


def test_feishu_uses_shared_handlers():
    """飞书本地只保留 Feishu 特有逻辑，核心命令必须复用共享实现。"""
    from scripts.feishu_bot import (
        _cmd_eat,
        _cmd_news,
        _cmd_news_block,
        _cmd_news_reset,
        _cmd_template,
        _fetch_eat_recommendation,
        _fetch_news_digest,
        _shared_cmd_hw,
    )
    from sjtu_agent.commands.dining import cmd_eat, fetch_eat_recommendation as shared_eat
    from sjtu_agent.commands.news import (
        cmd_news,
        cmd_news_block,
        cmd_news_reset,
        fetch_news_digest as shared_news,
    )
    from sjtu_agent.commands.template import cmd_template
    from sjtu_agent.commands.homework import cmd_hw

    assert _cmd_eat is cmd_eat
    assert _cmd_news is cmd_news
    assert _cmd_news_block is cmd_news_block
    assert _cmd_news_reset is cmd_news_reset
    assert _cmd_template is cmd_template
    assert _fetch_eat_recommendation is shared_eat
    assert _fetch_news_digest is shared_news
    assert _shared_cmd_hw is cmd_hw
