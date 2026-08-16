"""Tests for SYSTEM_PROMPT module structure (Phase 3: prompt audit)."""

from sjtu_agent.agent.prompts import (
    _BOT_SETUP,
    _CORE_PRINCIPLES,
    _DOMAIN_GUIDE,
    _PROJECT_IDENTITY,
    _TOOL_ROUTING,
    SYSTEM_PROMPT,
)


def test_system_prompt_is_module_concatenation():
    """SYSTEM_PROMPT = 各模块常量拼接（行为不变，仅结构化）。"""
    assert SYSTEM_PROMPT == _PROJECT_IDENTITY + _CORE_PRINCIPLES + _TOOL_ROUTING + _DOMAIN_GUIDE + _BOT_SETUP


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
    # Bot 配置引导移出前缀，只留路由
    assert "Bot 接入配置" in _BOT_SETUP
    assert "get_bot_setup_guide" in SYSTEM_PROMPT


def test_bot_setup_guides_out_of_prefix():
    """4 平台详细引导已移出 system prompt（改为按需工具）。"""
    assert "open.feishu.cn" not in SYSTEM_PROMPT
    assert "BotFather" not in SYSTEM_PROMPT


def test_unknown_info_requires_web_search():
    assert "web_search" in SYSTEM_PROMPT
    assert "github_repo_search" in SYSTEM_PROMPT
    assert "黑话" in SYSTEM_PROMPT
    assert "不认识的缩写" in SYSTEM_PROMPT
    assert "流口水" in SYSTEM_PROMPT


def test_project_identity_included():
    assert "https://github.com/kuan-er/sjtu-agent" in _PROJECT_IDENTITY
    assert "kuan-er" in _PROJECT_IDENTITY
    assert "github_repo_search" in _TOOL_ROUTING


def test_startup_adapts_to_academic_calendar():
    assert "寒暑假" in _DOMAIN_GUIDE
    assert "不要提作业 DDL、课表、食堂" in _DOMAIN_GUIDE
    assert "不要在每个新会话开始时自动调用 check_setup" in _DOMAIN_GUIDE
