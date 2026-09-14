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


def test_strip_dsml_blocks_real_sample():
    """deepseek 流式偶发把工具调用以 DSML 原文混入正文（用户实测截图）。"""
    from sjtu_agent.agent.runner import _strip_dsml_blocks
    raw = (
        "我来搜索一下。\n"
        "<｜｜DSML｜｜ calls>\n"
        '<｜｜DSML｜｜ invoke name="web_search">\n'
        '<｜｜DSML｜｜ parameter name="query" string="true">"Quasar" OpenAI next model after Astra 2026</｜｜DSML｜｜ parameter>\n'
        "</｜｜DSML｜｜ invoke>\n"
        "</｜｜DSML｜｜ calls>"
    )
    out = _strip_dsml_blocks(raw)
    assert out == "我来搜索一下。"
    assert "DSML" not in out


def test_strip_dsml_blocks_unclosed_and_clean():
    from sjtu_agent.agent.runner import _strip_dsml_blocks
    assert _strip_dsml_blocks('回答：<｜｜DSML｜｜ invoke name="web_search">') == "回答："
    assert _strip_dsml_blocks("普通回复，没有标记") == "普通回复，没有标记"


# ── 工具参数损坏防御 + 悬空 tool_calls 自愈（飞书"出错了：Expecting ',' delimiter"）──

def test_parse_tool_args_repairs_trailing_garbage():
    """尾随垃圾可修复：合法 JSON 前缀被采用。"""
    from sjtu_agent.agent.runner import _parse_tool_args
    args, err = _parse_tool_args('{"query": "Quasar"} <杂音>')
    assert err == ""
    assert args == {"query": "Quasar"}


def test_parse_tool_args_truncated_reports_reason():
    """截断的 JSON：返回 None + 人类可读原因，不再抛异常杀死整轮。"""
    from sjtu_agent.agent.runner import _parse_tool_args
    args, err = _parse_tool_args('{"query": "Quasar", "limit"')
    assert args is None
    assert "line" in err


def test_parse_tool_args_empty_is_empty_dict():
    from sjtu_agent.agent.runner import _parse_tool_args
    assert _parse_tool_args("") == ({}, "")
    assert _parse_tool_args(None) == ({}, "")


def test_openai_loop_survives_malformed_tool_args(monkeypatch):
    """arguments 损坏 → 回填错误 tool 结果并继续，用户不再看到 JSONDecodeError。"""
    seq = [
        ("", "", {0: {"id": "t1", "name": "web_search",
                      "arguments": '{"query": "Quasar", "limit"'}}),  # 损坏
        ("搜索完成。", "", {}),                                        # 重试后作答
    ]
    calls = []

    def fake_stream_tags(stream, spinner):
        calls.append(1)
        return seq[min(len(calls) - 1, len(seq) - 1)]

    def fake_create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            reasoning_content=None, content=None, tool_calls=None))])

    monkeypatch.setattr(runner, "_stream_with_think_tags", fake_stream_tags)
    monkeypatch.setattr(runner, "_get_run_tool", lambda: lambda name, args: "{}")
    monkeypatch.setattr(runner, "print_markdown_message", lambda *a, **k: None)

    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}]
    runner._run_one_turn_openai(_fake_openai_client(fake_create), "deepseek-chat", msgs)

    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert tool_msgs and "参数解析失败" in tool_msgs[0]["content"]
    assert tool_msgs[0]["tool_call_id"] == "t1"
    # 第二次请求把错误结果带给了模型（模型有机会重试）
    assert len(calls) == 2


def test_repair_dangling_openai_tool_calls():
    """异常中断留下的无结果 tool_calls → 自愈补齐，会话不再永久 400。"""
    from sjtu_agent.agent.runner import _repair_dangling_tool_calls
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "t1", "type": "function",
                         "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "user", "content": "继续"},
    ]
    n = _repair_dangling_tool_calls(msgs)
    assert n == 1
    assert msgs[3]["role"] == "tool" and msgs[3]["tool_call_id"] == "t1"
    assert "异常中断" in msgs[3]["content"]
    assert _repair_dangling_tool_calls(msgs) == 0  # 幂等


def test_repair_dangling_anthropic_tool_calls():
    from sjtu_agent.agent.runner import _repair_dangling_tool_calls
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "web_search", "input": {}}
        ]},
        {"role": "user", "content": "继续"},
    ]
    n = _repair_dangling_tool_calls(msgs)
    assert n == 1
    fixed = msgs[2]
    assert fixed["role"] == "user"
    assert fixed["content"][0]["type"] == "tool_result"
    assert fixed["content"][0]["tool_use_id"] == "tu_1"
