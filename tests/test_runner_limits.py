"""单轮输出上限的解析：官方端点与校园网关取不同的保守值。"""

from __future__ import annotations

from sjtu_agent.agent.runner import (
    _MAX_OUTPUT_TOKENS_DEFAULT,
    _MAX_OUTPUT_TOKENS_GATEWAY,
    _client_base_url,
    _max_output_tokens,
)


class _FakeClient:
    def __init__(self, base_url):
        self.base_url = base_url


def test_default_output_cap_is_2026_scale():
    """4096 是 GPT-3.5 时代的遗留值；推理模型思考与正文共用预算（#201）。"""
    assert _MAX_OUTPUT_TOKENS_DEFAULT == 16_384
    assert _max_output_tokens("https://api.deepseek.com") == 16_384


def test_campus_gateway_gets_conservative_cap():
    """致远一号单轮输出上限未公开 → 保守 8K；不能用官方 384K 的信心去赌。"""
    gateway = "https://models.sjtu.edu.cn/api/v1"
    assert _max_output_tokens(gateway) == _MAX_OUTPUT_TOKENS_GATEWAY
    assert _max_output_tokens(gateway) < _MAX_OUTPUT_TOKENS_DEFAULT


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("SJTU_MAX_OUTPUT_TOKENS", "32768")
    assert _max_output_tokens("https://models.sjtu.edu.cn/api/v1") == 32_768
    assert _max_output_tokens("https://api.deepseek.com") == 32_768


def test_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("SJTU_MAX_OUTPUT_TOKENS", "not-a-number")
    assert _max_output_tokens("https://api.deepseek.com") == _MAX_OUTPUT_TOKENS_DEFAULT


def test_client_base_url_is_read_from_sdk_client():
    assert _client_base_url(_FakeClient("https://models.sjtu.edu.cn/api/v1")) == (
        "https://models.sjtu.edu.cn/api/v1"
    )
    assert _client_base_url(object()) == ""
