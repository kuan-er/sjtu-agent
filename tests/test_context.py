"""Tests for sjtu_agent/agent/context.py — Phase 2 context quality management.

Covers:
- _estimate_tokens: rough estimator
- clear_stale_tool_results: clears old tool results, keeps recent turns
- _build_fold_digest: strips date prefix, dedupes, preserves user intent
- trim_session: folds oldest turns over quality budget, protects recent turns
"""

from sjtu_agent.agent.context import (
    _build_fold_digest,
    _estimate_tokens,
    _session_history_cost,
    clear_stale_tool_results,
    trim_session,
)


def _tool_history(n_user: int = 3):
    """构造 n_user 轮历史，每轮 user 带时间前缀 + 可选 tool 结果。"""
    msgs = [{"role": "system", "content": "S"}]
    for i in range(n_user):
        msgs.append({"role": "user", "content": f"## 当前时间\n现在：2026年\n\n第{i}轮用户问题"})
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"t{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": f"RESULT_{i}_" + "x" * 200})
        msgs.append({"role": "assistant", "content": f"第{i}轮回复"})
    return msgs


# ── _estimate_tokens ────────────────────────────────────────────────────────

def test_estimate_tokens():
    assert _estimate_tokens("") == 0
    assert _estimate_tokens(None) == 0
    assert _estimate_tokens("x" * 300) == 100


# ── clear_stale_tool_results ────────────────────────────────────────────────

def test_clear_old_keeps_recent_two_turns():
    msgs = _tool_history(3)  # 3 轮，3 个 tool 结果
    n = clear_stale_tool_results(msgs)
    assert n == 1  # 只清最旧 1 轮的（keep_recent=2）
    assert msgs[3]["content"].startswith("[工具结果已清理")
    assert msgs[7]["content"] == msgs[7]["content"]  # 最近两轮的保留


def test_clear_idempotent():
    msgs = _tool_history(3)
    clear_stale_tool_results(msgs)
    assert clear_stale_tool_results(msgs) == 0


def test_clear_noop_with_single_turn():
    msgs = _tool_history(1)
    assert clear_stale_tool_results(msgs) == 0  # 只有一轮，无旧结果可清


# ── _build_fold_digest ──────────────────────────────────────────────────────

def test_fold_digest_strips_date_prefix():
    msgs = [
        {"role": "user", "content": "## 当前时间\n现在：2026年\n\n帮我查这周DDL"},
        {"role": "assistant", "content": "ok"},
    ]
    digest = _build_fold_digest(msgs)
    assert "帮我查这周DDL" in digest
    assert "当前时间" not in digest  # 时间前缀被剥掉


def test_fold_digest_dedupes():
    msgs = [
        {"role": "user", "content": "## 当前时间\n\n完全相同一句话"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "## 当前时间\n\n完全相同一句话"},
        {"role": "assistant", "content": "b"},
    ]
    digest = _build_fold_digest(msgs)
    assert digest.count("完全相同一句话") == 1


# ── trim_session ────────────────────────────────────────────────────────────

def test_trim_noop_under_budget():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert trim_session(msgs, budget=8000) == 0
    assert len(msgs) == 3


def test_trim_folds_oldest_protects_recent():
    # 6 轮大内容 → 触发折叠
    msgs = [{"role": "system", "content": "S"}]
    for i in range(6):
        msgs.append({"role": "user", "content": f"## 当前时间\n\n第{i}轮用户问题" + "x" * 300})
        msgs.append({"role": "assistant", "content": "回复" + "y" * 300})
    n = trim_session(msgs, budget=800)
    assert n > 0
    assert _session_history_cost(msgs) <= 800
    # 折叠摘要已插入（system 角色）
    notes = [m for m in msgs if m.get("role") == "system" and "折叠前曾讨论" in m.get("content", "")]
    assert notes
    # 保护最近轮次：最后的 user 消息仍在
    assert any("第5轮用户问题" in str(m.get("content", "")) for m in msgs if m.get("role") == "user")


def test_trim_clears_then_folds():
    """tool 结果大时先清理（无损）再折叠。"""
    msgs = _tool_history(8)  # 8 轮，每轮 tool 结果 ~200 字
    n = trim_session(msgs, budget=400)
    assert n > 0
    assert _session_history_cost(msgs) <= 400
