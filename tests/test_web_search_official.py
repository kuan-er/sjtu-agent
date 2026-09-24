from __future__ import annotations

import pytest

from sjtu_agent.agent.tools import _web_search_official as official


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _payload(results, citations=None):
    """按官方（Anthropic 兼容）响应结构拼一个 body。"""
    content = []
    if citations:
        content.append(
            {
                "type": "text",
                "text": "总结……",
                "citations": [
                    {"type": "web_search_result_location", "url": url, "cited_text": text}
                    for url, text in citations.items()
                ],
            }
        )
    content.append(
        {
            "type": "web_search_tool_result",
            "tool_use_id": "toolu_1",
            "content": [
                {"type": "web_search_result", "url": url, "title": title, "page_age": age}
                for url, title, age in results
            ],
        }
    )
    return {"id": "msg_1", "type": "message", "role": "assistant", "content": content}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        official.BACKEND_ENV,
        official.BASE_URL_ENV,
        official.MODEL_ENV,
        official.MAX_USES_ENV,
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_parses_results_and_sends_expected_request(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers or {}
        seen["body"] = json or {}
        return _Response(
            200,
            _payload(
                [
                    ("https://news.qq.com/a/1", "Anthropic 封号潮", "2026-07-01"),
                    ("https://www.huxiu.com/a/2", "Anthropic 连坐封号", None),
                ],
                citations={
                    "https://news.qq.com/a/1": "大面积封号，申诉通道形同虚设。",
                },
            ),
        )

    monkeypatch.setattr(official.requests, "post", fake_post)
    results = official.search("Anthropic 封号 争议", 5, api_key="sk-test")

    assert seen["url"] == f"{official.DEFAULT_BASE_URL}/messages"
    assert seen["headers"]["x-api-key"] == "sk-test"
    assert seen["headers"]["anthropic-version"] == official.API_VERSION
    tool = seen["body"]["tools"][0]
    assert tool["type"] == "web_search_20250305" and tool["name"] == "web_search"
    assert "Anthropic 封号 争议" in seen["body"]["messages"][0]["content"][0]["text"]

    assert [item["source"] for item in results] == ["news.qq.com", "huxiu.com"]
    assert results[0]["snippet"].startswith("大面积封号")
    assert results[0]["published"] == "2026-07-01"
    assert "published" not in results[1]  # page_age 缺失时不硬塞空字段


def test_missing_result_block_raises(monkeypatch):
    monkeypatch.setattr(
        official.requests,
        "post",
        lambda *a, **k: _Response(200, {"content": [{"type": "text", "text": "没有搜索"}]}),
    )
    with pytest.raises(official.OfficialSearchError) as excinfo:
        official.search("随便", 5, api_key="sk-test")
    assert "web_search_tool_result" in str(excinfo.value)


def test_native_search_error_block_raises(monkeypatch):
    monkeypatch.setattr(
        official.requests,
        "post",
        lambda *a, **k: _Response(
            200,
            {
                "content": [
                    {
                        "type": "web_search_tool_result",
                        "content": {
                            "type": "web_search_tool_result_error",
                            "error_code": "max_uses_exceeded",
                        },
                    }
                ]
            },
        ),
    )
    with pytest.raises(official.OfficialSearchError) as excinfo:
        official.search("随便", 5, api_key="sk-test")
    assert "max_uses_exceeded" in str(excinfo.value)


def test_dedupes_by_url_and_caps_results(monkeypatch):
    monkeypatch.setattr(
        official.requests,
        "post",
        lambda *a, **k: _Response(
            200,
            _payload(
                [
                    ("https://a.com/1", "A", None),
                    ("https://a.com/1", "A 重复", None),
                    ("https://b.com/2", "B", None),
                    ("https://c.com/3", "C", None),
                ]
            ),
        ),
    )
    results = official.search("随便", 2, api_key="sk-test")
    assert len(results) == 2
    assert [item["url"] for item in results] == ["https://a.com/1", "https://b.com/2"]


def test_http_error_message_includes_status_and_detail(monkeypatch):
    monkeypatch.setattr(
        official.requests,
        "post",
        lambda *a, **k: _Response(401, {"error": {"message": "invalid api key"}}),
    )
    with pytest.raises(official.OfficialSearchError) as excinfo:
        official.search("随便", 5, api_key="sk-bad")
    message = str(excinfo.value)
    assert "401" in message and "invalid api key" in message


def test_resolve_api_key_reads_env_and_ignores_non_deepseek_config(monkeypatch):
    assert official.resolve_api_key() == ""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert official.resolve_api_key() == "sk-env"

    # 只有 agent_config 时：必须是 DeepSeek 官方端点才认
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    import importlib

    # 注意：sjtu_agent.agent 里 chat_loop 这个名字是**函数**，要按模块导入
    chat_loop = importlib.import_module("sjtu_agent.agent.chat_loop")

    monkeypatch.setattr(
        chat_loop,
        "load_agent_config",
        lambda: {"base_url": "https://models.sjtu.edu.cn/api/v1", "api_key": "zhiyuan-key"},
    )
    assert official.resolve_api_key() == ""

    monkeypatch.setattr(
        chat_loop,
        "load_agent_config",
        lambda: {"base_url": "https://api.deepseek.com", "api_key": "sk-config"},
    )
    assert official.resolve_api_key() == "sk-config"


def test_backend_mode_and_enabled(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert official.backend_mode() == "auto"
    assert official.enabled() is True

    monkeypatch.setenv(official.BACKEND_ENV, "scrapers")
    assert official.enabled() is False

    monkeypatch.setenv(official.BACKEND_ENV, "deepseek")
    assert official.enabled() is True

    monkeypatch.setenv(official.BACKEND_ENV, "乱写")
    assert official.backend_mode() == "auto"  # 非法值回落 auto

    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert official.enabled() is False


def test_base_url_and_limits_are_configurable(monkeypatch):
    monkeypatch.setenv(official.BASE_URL_ENV, "https://gateway.internal/anthropic/v1/")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["body"] = json or {}
        return _Response(200, _payload([("https://a.com/1", "A", None)]))

    monkeypatch.setattr(official.requests, "post", fake_post)
    official.search("随便", 5, api_key="sk-test")
    assert seen["url"] == "https://gateway.internal/anthropic/v1/messages"
    assert seen["body"]["tools"][0]["max_uses"] == official.DEFAULT_MAX_USES
