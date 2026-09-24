"""单轮输出上限：按厂商上限与窗口余量自适应（官方 DeepSeek 384K 量级）。"""

from __future__ import annotations

from sjtu_agent.agent.runner import (
    _OUTPUT_CAP_FLOOR,
    _OUTPUT_CAP_UNKNOWN,
    _client_base_url,
    _estimate_prompt_tokens,
    _is_max_tokens_rejection,
    _max_output_tokens,
    _provider_output_cap,
)

GATEWAY = "https://models.sjtu.edu.cn/api/v1"
OFFICIAL_DEEPSEEK = "https://api.deepseek.com"


class _FakeClient:
    def __init__(self, base_url):
        self.base_url = base_url


# ── 厂商上限 ────────────────────────────────────────────────────────────────


def test_provider_caps_reflect_2026_specs():
    # DeepSeek-V4.1-Flash：384K = 393,216，思考与正文共享该配额
    assert _provider_output_cap("deepseek-flash") == 393_216
    assert _provider_output_cap("deepseek-chat") == 393_216
    assert _provider_output_cap("deepseek-reasoner") == 393_216
    assert _provider_output_cap("claude-sonnet-5") == 128_000
    # 认不出的后端：保守（其上限未公开）
    assert _provider_output_cap("some-internal-model") == _OUTPUT_CAP_UNKNOWN


# ── 自适应：min(厂商上限, 窗口 − prompt − 余量) ──────────────────────────────


def test_official_deepseek_gets_full_cap():
    assert _max_output_tokens(OFFICIAL_DEEPSEEK, "deepseek-flash", prompt_tokens=100_000) == 393_216


def test_campus_gateway_is_not_artificially_small():
    """回归：不能因为"网关上限没公开"就只给 8K —— 它承载的是同一个模型。"""
    assert _max_output_tokens(GATEWAY, "deepseek-chat", prompt_tokens=30_000) == 393_216


def test_output_cap_shrinks_with_prompt_to_fit_window():
    """max_tokens 与 prompt 共享窗口：大上下文里按上限发会被后端 400。"""
    # 致远一号 deepseek-chat 窗口 512k：512000 − 200000 − 2000
    assert _max_output_tokens(GATEWAY, "deepseek-chat", prompt_tokens=200_000) == 310_000
    # prompt 逼近窗口 → 收缩到下限，而不是发出一个必然被拒的值
    assert _max_output_tokens(GATEWAY, "deepseek-chat", prompt_tokens=511_000) == _OUTPUT_CAP_FLOOR


def test_unknown_gateway_is_bounded_by_its_conservative_window():
    """模型认识、网关不认识：厂商上限仍在，但受保守窗口的余量约束。"""
    # 128K 窗口 − 10K prompt − 2K 余量
    assert _max_output_tokens("https://my-gw.example.com/v1", "deepseek-chat", prompt_tokens=10_000) == 116_000
    # 模型也不认识 → 落到保守上限
    assert _max_output_tokens("https://my-gw.example.com/v1", "mystery-model", prompt_tokens=10_000) == 8_192


# ── 覆盖与回落 ──────────────────────────────────────────────────────────────


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("SJTU_MAX_OUTPUT_TOKENS", "32768")
    assert _max_output_tokens(GATEWAY, "deepseek-chat", prompt_tokens=400_000) == 32_768
    assert _max_output_tokens(OFFICIAL_DEEPSEEK, "deepseek-flash") == 32_768


def test_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("SJTU_MAX_OUTPUT_TOKENS", "not-a-number")
    assert _max_output_tokens(OFFICIAL_DEEPSEEK, "deepseek-flash", prompt_tokens=1_000) == 393_216


def test_max_tokens_rejection_detection():
    assert _is_max_tokens_rejection("max_tokens: 393216 is too large; max is 32768") is True
    assert _is_max_tokens_rejection("max output tokens exceeds limit") is True
    # 上下文过长不是 max_tokens 的问题，不该降档重试
    assert _is_max_tokens_rejection("maximum context length exceeded") is False


# ── prompt 估算 ─────────────────────────────────────────────────────────────


def test_estimate_prompt_tokens_counts_images_as_fixed_cost():
    """图片必须按固定视觉成本计，按 base64 长度算会把输出上限压到下限。"""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 200_000}},
                {"type": "text", "text": "这张图里是什么？"},
            ],
        }
    ]
    assert _estimate_prompt_tokens(messages) < 5_000


def test_estimate_prompt_tokens_includes_system_and_tools():
    messages = [{"role": "user", "content": "x" * 3_000}]
    base = _estimate_prompt_tokens(messages)
    assert _estimate_prompt_tokens(messages, system="y" * 3_000) > base
    assert _estimate_prompt_tokens(messages, tools=[{"name": "t", "description": "z" * 3_000}]) > base


def test_client_base_url_is_read_from_sdk_client():
    assert _client_base_url(_FakeClient(GATEWAY)) == GATEWAY
    assert _client_base_url(object()) == ""
