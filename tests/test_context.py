"""Tests for sjtu_agent/agent/context.py — Phase 2 context quality management.

Covers:
- _estimate_tokens: rough estimator
- clear_stale_tool_results: clears old tool results, keeps recent turns
- _build_fold_digest: strips date prefix, dedupes, preserves user intent
- trim_session: folds oldest turns over quality budget, protects recent turns
- model_context_window / context_budget: 2026 口径的窗口与预算推导
"""

from sjtu_agent.agent.context import (
    _BUDGET_CAP,
    _BUDGET_FLOOR,
    _build_fold_digest,
    _estimate_tokens,
    _session_history_cost,
    clear_stale_tool_results,
    context_budget,
    is_campus_gateway,
    model_context_window,
    trim_session,
)


# ── 2026 口径：窗口与预算 ───────────────────────────────────────────────────


def test_model_context_window_known_and_unknown():
    """没给 base_url 时退回按模型名推断（老调用路径的向后兼容）。"""
    assert model_context_window("deepseek-flash") == 1_000_000
    assert model_context_window("deepseek-chat") == 1_000_000
    assert model_context_window("claude-sonnet-5") == 1_000_000
    assert model_context_window("gpt-5.6-terra") == 1_000_000
    assert model_context_window("gpt-4o") == 128_000
    # 认不出来的模型必须保守（宁可早折，也别撑爆对方窗口）
    assert model_context_window("some-internal-gateway-model") == 128_000
    assert model_context_window("") == 128_000


def test_context_window_follows_backend_not_just_model_name():
    """同一个调用名在不同后端上窗口不同——这是本次调整的核心。

    致远一号官方指南（claw.sjtu.edu.cn/guide/sjtu-api）：deepseek-chat /
    deepseek-reasoner = 512k，qwen / qwen3.8-27b = 256k —— 都**不是**官方
    DeepSeek 的 1M。按模型名一刀切会把预算算到窗口外面去。
    """
    gateway = "https://models.sjtu.edu.cn/api/v1"
    assert model_context_window("deepseek-chat", gateway) == 512_000
    assert model_context_window("deepseek-reasoner", gateway) == 512_000
    assert model_context_window("qwen", gateway) == 256_000
    assert model_context_window("qwen3.8-27b", gateway) == 256_000
    # 网关上认不出的调用名 → 按已公开的最小窗口
    assert model_context_window("public-models", gateway) == 256_000

    # 官方 DeepSeek：按官方口径
    assert model_context_window("deepseek-flash", "https://api.deepseek.com") == 1_000_000
    assert model_context_window("deepseek-chat", "https://api.deepseek.com/v1") == 1_000_000

    # 其它第三方网关：未公开 → 不猜，保守 128K
    assert model_context_window("deepseek-chat", "https://my-gw.example.com/v1") == 128_000


def test_context_window_env_declares_deployment(monkeypatch):
    """部署方最清楚自己的窗口：SJTU_CONTEXT_WINDOW 优先于所有推断。"""
    monkeypatch.setenv("SJTU_CONTEXT_WINDOW", "800000")
    assert model_context_window("deepseek-chat", "https://my-gw.example.com/v1") == 800_000
    assert model_context_window("anything", "https://models.sjtu.edu.cn/api/v1") == 800_000


def test_is_campus_gateway():
    assert is_campus_gateway("https://models.sjtu.edu.cn/api/v1") is True
    assert is_campus_gateway("https://api.deepseek.com") is False
    assert is_campus_gateway("") is False


def test_context_budget_scales_with_window_and_backend():
    """1M 官方窗口 → 500K；致远一号 512k → 256K；未知网关 128K → 64K。"""
    assert context_budget("deepseek-flash", base_url="https://api.deepseek.com") == 500_000
    assert context_budget("deepseek-chat", base_url="https://models.sjtu.edu.cn/api/v1") == 256_000
    assert context_budget("unknown-model", base_url="https://my-gw.example.com/v1") == 64_000
    assert context_budget("unknown-model") == 64_000


def test_context_budget_env_override_is_clamped(monkeypatch):
    monkeypatch.setenv("SJTU_CONTEXT_BUDGET", "123456")
    assert context_budget("deepseek-flash") == 123_456
    monkeypatch.setenv("SJTU_CONTEXT_BUDGET", "999999999")
    assert context_budget("deepseek-flash") == _BUDGET_CAP
    monkeypatch.setenv("SJTU_CONTEXT_BUDGET", "1")
    assert context_budget("deepseek-flash") == _BUDGET_FLOOR


def test_context_budget_explicit_override_wins(monkeypatch):
    """显式 override 是调用方权威：环境变量与夹取都不覆盖它。"""
    monkeypatch.setenv("SJTU_CONTEXT_BUDGET", "123456")
    assert context_budget("deepseek-flash", override=8_000) == 8_000


def test_context_budget_default_constant_used_when_budget_omitted():
    """trim_session 不传预算时用模块常量（未指定模型 → 64K）。"""
    from sjtu_agent.agent import context

    assert context.SESSION_QUALITY_BUDGET == context_budget()


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


# ── 多模态计费与单轮保护（deepseek-flash 发图实测反馈） ──────────────────────

def test_multimodal_image_cost_is_fixed_not_base64_length():
    """图片块按固定视觉成本计费，绝不按 base64 长度计（否则一张图击穿预算）。"""
    from sjtu_agent.agent import context
    huge_b64 = "A" * 3_000_000
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": [
            {"type": "text", "text": "这是什么"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + huge_b64}},
        ]},
    ]
    cost = context._message_cost(msgs[1])
    assert cost < 10_000  # base64 按长度算是百万级"token"
    assert trim_session(msgs) == 0
    assert any(isinstance(m.get("content"), list) for m in msgs)  # 图片消息仍在


def test_single_turn_never_folded():
    """唯一/最新用户消息绝不折叠——折叠掉模型只会说'看不到你的问题'。"""
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "帮我看看这个" + "x" * 500_000},
    ]
    n = trim_session(msgs, budget=1000)
    assert n == 0
    assert any(m.get("role") == "user" for m in msgs)
    assert not any(
        m.get("role") == "system" and "已折叠" in str(m.get("content", ""))
        for m in msgs
    )
