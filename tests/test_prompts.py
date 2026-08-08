"""Tests for SYSTEM_PROMPT module structure (Phase 3: prompt audit)."""

from sjtu_agent.agent.prompts import (
    _BOT_SETUP,
    _CORE_PRINCIPLES,
    _DOMAIN_GUIDE,
    _TOOL_ROUTING,
    SYSTEM_PROMPT,
)


def test_system_prompt_is_module_concatenation():
    """SYSTEM_PROMPT = 各模块常量拼接（行为不变，仅结构化）。"""
    assert SYSTEM_PROMPT == _CORE_PRINCIPLES + _TOOL_ROUTING + _DOMAIN_GUIDE + _BOT_SETUP


def test_no_hardcoded_recent_updates():
    """近期更新已移出前缀（改为 get_recent_updates 工具按需读 CHANGELOG）。"""
    assert "近期更新" not in SYSTEM_PROMPT
    # 路由提示在（指引 agent 调工具）
    assert "get_recent_updates" in SYSTEM_PROMPT


def test_key_sections_present():
    """关键段都保留在对应模块。"""
    assert "核心原则" in _CORE_PRINCIPLES
    assert "工具选择策略" in _TOOL_ROUTING
    assert "食堂用餐推荐" in _DOMAIN_GUIDE
    assert "Telegram Bot 配置" in _BOT_SETUP
