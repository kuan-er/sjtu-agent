"""Tests for Loop phase (Phase 5): iteration budget + retry cap + converge."""

from types import SimpleNamespace

import pytest

import sjtu_agent.agent.runner as runner
from sjtu_agent.agent.runner import _MAX_TOOL_ITERATIONS, _MAX_NETWORK_RETRIES


def _fake_openai_client(create_fn):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn)))


def test_iteration_budget_constant():
    assert _MAX_TOOL_ITERATIONS >= 4
    assert _MAX_NETWORK_RETRIES >= 1


def test_openai_loop_converges_when_model_keeps_calling_tools(monkeypatch):
    """模型持续调工具 → 迭代预算耗尽后收敛（不死循环），且收敛结果入历史。"""
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        # 每个响应都返回一个 tool_call（让模型"永远想调工具"）
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            reasoning_content=None, content=None,
            tool_calls=[SimpleNamespace(
                index=0, id="t1",
                function=SimpleNamespace(name="get_ddls", arguments="{}"))]))])

    client = _fake_openai_client(fake_create)

    def fake_stream_tags(stream, spinner):
        return "", "", {0: {"id": "t1", "name": "get_ddls", "arguments": "{}"}}

    monkeypatch.setattr(runner, "_stream_with_think_tags", fake_stream_tags)
    monkeypatch.setattr(runner, "_get_run_tool", lambda: lambda name, args: "{}")
    monkeypatch.setattr(runner, "print_markdown_message", lambda *a, **k: None)

    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}]
    runner._run_one_turn_openai(client, "deepseek-chat", msgs)

    # _MAX_TOOL_ITERATIONS 次工具迭代 + 1 次收敛 create
    assert len(calls) == _MAX_TOOL_ITERATIONS + 1
    # 收敛后最后一条是 assistant（占位文案）
    assert msgs[-1]["role"] == "assistant"
    assert "工具调用上限" in msgs[-1]["content"]


def test_openai_loop_returns_when_no_tool_calls(monkeypatch):
    """模型直接给答案（无工具调用）→ 一次调用即返回。"""
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            reasoning_content=None, content="你好", tool_calls=None))])

    client = _fake_openai_client(fake_create)
    monkeypatch.setattr(runner, "_stream_with_think_tags",
                        lambda stream, spinner: ("你好", "", {}))
    monkeypatch.setattr(runner, "print_markdown_message", lambda *a, **k: None)

    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}]
    runner._run_one_turn_openai(client, "deepseek-chat", msgs)
    assert len(calls) == 1
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == "你好"


def test_converge_openai_appends_fallback_on_failure(monkeypatch):
    """收敛时若流式失败，仍补一条占位 assistant，不崩。"""
    def fake_create(**kwargs):
        raise RuntimeError("boom")

    client = _fake_openai_client(fake_create)
    msgs = [{"role": "system", "content": "S"}]
    runner._converge_openai(client, "deepseek-chat", msgs)
    assert msgs[-1]["role"] == "assistant"
    assert "工具调用上限" in msgs[-1]["content"]
