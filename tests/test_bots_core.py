"""Tests for sjtu_agent/bots/_core.py — shared conversation-core.

The critical invariant: run_one_turn relies on agent._run_one_turn APPENDING
the assistant message to sess['messages'] (runner.py does this), so reply
extraction from message history is equivalent to the old stdout "Agent: "
marker parse that telegram/wechat/qq used.
"""

import pytest


def test_model_supports_vision():
    from sjtu_agent.bots._core import model_supports_vision
    assert model_supports_vision("gpt-4o") is True
    assert model_supports_vision("claude-3-5-sonnet") is True
    assert model_supports_vision("deepseek-chat") is False
    assert model_supports_vision(None) is False  # 防御 None


def test_make_session_generic_fallback_model(monkeypatch):
    """无任何配置时的通用兜底模型为 deepseek-chat（致远一号由环境注入 public-models）。"""
    from sjtu_agent.bots import _core

    monkeypatch.setattr("sjtu_agent.agent.load_agent_config", lambda: {})
    monkeypatch.setattr(
        "sjtu_agent.news_aggregator.profile.ensure_profile_analyzed_async",
        lambda: None,
    )
    sess = _core.make_session()
    assert sess["model_box"] == ["deepseek-chat"]


def test_build_date_ctx_contains_semester():
    from sjtu_agent.bots._core import build_date_ctx
    ctx = build_date_ctx()
    assert "当前时间" in ctx
    assert "当前学期" in ctx


def test_extract_assistant_reply_text_and_list():
    from sjtu_agent.bots._core import extract_assistant_reply
    assert extract_assistant_reply(
        {"messages": [{"role": "assistant", "content": "  你好  "}]}
    ) == "你好"
    assert extract_assistant_reply(
        {"messages": [{"role": "assistant", "content": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]}]}
    ) == "A\nB"
    assert extract_assistant_reply({"messages": []}) == "(已完成)"


def test_run_one_turn_appends_and_returns(monkeypatch):
    """模拟 _run_one_turn 把 assistant 回复追加到 messages，验证提取等价。"""
    import sjtu_agent.bots._core as core

    def fake_run_one_turn(client, model, messages):
        messages.append({"role": "assistant", "content": "模拟回复"})

    monkeypatch.setattr(core, "_run_one_turn", fake_run_one_turn)

    sess = {"messages": [], "model_box": ["deepseek-chat"], "client_box": [object()]}
    reply = core.run_one_turn(sess, "你好", "【平台上下文】")

    assert reply == "模拟回复"
    assert sess["messages"][0]["role"] == "system"
    assert "你好" in sess["messages"][1]["content"] or sess["messages"][1]["content"] == "你好"
    assert sess["messages"][2]["role"] == "assistant"


def test_run_one_turn_multimodal(monkeypatch):
    import sjtu_agent.bots._core as core

    def fake_run_one_turn(client, model, messages):
        messages.append({"role": "assistant", "content": "多模态回复"})

    monkeypatch.setattr(core, "_run_one_turn", fake_run_one_turn)

    sess = {"messages": [], "model_box": ["deepseek-chat"], "client_box": [object()]}
    content = [{"type": "text", "text": "看图"}]
    reply = core.run_one_turn_multimodal(sess, content, "【平台】")

    assert reply == "多模态回复"
    # 时间注入为首个 text 元素，原始内容保留在其后
    user_content = sess["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert "当前时间" in user_content[0]["text"]
    assert user_content[1:] == content


def test_build_system_prompt_includes_skills(monkeypatch):
    """build_system_prompt 注入启用技能（修复死代码）。"""
    from sjtu_agent.agent.prompts import build_system_prompt
    monkeypatch.setattr(
        "sjtu_agent.extensions.skills.build_skill_prompt",
        lambda: "\n\n## 技能\n能力X",
    )
    result = build_system_prompt()
    assert "能力X" in result


def test_init_messages_uses_build_system_prompt(monkeypatch):
    """_core.init_messages 走 build_system_prompt（带 skills）。"""
    import sjtu_agent.bots._core as core
    monkeypatch.setattr(core, "build_system_prompt", lambda *a: "SYSTEM_WITH_SKILLS")
    sess = {"messages": [], "model_box": ["m"], "client_box": [object()]}
    core.init_messages(sess, "平台")
    assert "SYSTEM_WITH_SKILLS" in sess["messages"][0]["content"]


def test_stable_prefix_system_has_no_date(monkeypatch):
    """Phase 1 稳定前缀：system 不含时间（时间注入用户消息），保证缓存命中。"""
    import sjtu_agent.bots._core as core

    sess = {"messages": [], "model_box": ["deepseek-chat"], "client_box": [object()]}
    core.init_messages(sess, "【平台】")
    sys_content = sess["messages"][0]["content"]
    assert "当前学期" not in sys_content  # 动态时间/学期不进 system 前缀
    assert "【平台】" in sys_content

    # run_one_turn 把时间放用户消息首部
    monkeypatch.setattr(core, "_run_one_turn", lambda c, m, msgs: None)
    sess2 = {"messages": [], "model_box": ["deepseek-chat"], "client_box": [object()]}
    core.run_one_turn(sess2, "你好")
    assert "当前学期" in sess2["messages"][1]["content"]
    assert "你好" in sess2["messages"][1]["content"]
