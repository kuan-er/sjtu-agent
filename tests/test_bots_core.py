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
    assert sess["messages"][1]["content"] == content
