from __future__ import annotations

from sjtu_agent.agent.tools._web_search import tool_web_search


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_web_search_parses_bing_results(monkeypatch):
    html = """
    <html><body>
    <li class="b_algo">
      <h2><a href="https://example.com/dsh">DeepSeek Harness 介绍</a></h2>
      <p>DeepSeek Harness 是一个终端智能体框架。</p>
    </li>
    </body></html>
    """
    monkeypatch.setattr("sjtu_agent.agent.tools._web_search.requests.get", lambda *a, **k: FakeResponse(html))
    result = tool_web_search("DSH 是什么意思")
    assert result["ok"] is True
    assert result["results"][0]["title"] == "DeepSeek Harness 介绍"
    assert result["results"][0]["url"] == "https://example.com/dsh"
    assert "终端智能体" in result["results"][0]["snippet"]


def test_web_search_falls_back_to_duckduckgo(monkeypatch):
    html = """
    <html><body>
    <a rel="nofollow" href="https://example.org/item" class="result-link">Fallback Result</a>
    <td class="result-snippet">fallback snippet</td>
    </body></html>
    """
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bing blocked")
        return FakeResponse(html)

    monkeypatch.setattr("sjtu_agent.agent.tools._web_search.requests.get", fake_get)
    result = tool_web_search("fallback query")
    assert result["ok"] is True
    assert calls["n"] == 2
    assert result["results"][0]["title"] == "Fallback Result"


def test_web_search_empty_query():
    result = tool_web_search("  ")
    assert result["ok"] is False
    assert "query" in result["error"]


def test_web_search_failure_is_structured(monkeypatch):
    def fake_get(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr("sjtu_agent.agent.tools._web_search.requests.get", fake_get)
    result = tool_web_search("anything")
    assert result["ok"] is False
    assert "联网搜索失败" in result["error"]
    assert result["results"] == []


def test_web_search_tries_acronym_and_full_name_variants(monkeypatch):
    queries = []

    def fake_get(url, **kwargs):
        queries.append(url)
        if "DSH" in url and "DeepSeek" not in url:
            return FakeResponse(
                '<li class="b_algo"><h2><a href="https://example.org/dsh">DSH 定义</a></h2>'
                "<p>DSH 是 DeepSeek Harness。</p></li>"
            )
        return FakeResponse("<html></html>")

    monkeypatch.setattr("sjtu_agent.agent.tools._web_search.requests.get", fake_get)
    result = tool_web_search("DeepSeek Harness DSH 的消息")
    assert result["ok"] is True
    assert result["results"][0]["title"] == "DSH 定义"
    assert any("DSH" in url and "DeepSeek" not in url for url in queries)


def test_web_search_uses_dedicated_proxy_when_configured(monkeypatch):
    """设置 SJTU_WEB_SEARCH_PROXY 后，web_search 的请求必须走该代理。"""
    seen = {}
    monkeypatch.setenv("SJTU_WEB_SEARCH_PROXY", "http://127.0.0.1:7890")

    def fake_get(url, **kwargs):
        seen.setdefault("proxies", set()).add(
            tuple(sorted((kwargs.get("proxies") or {}).items()))
        )
        raise RuntimeError("offline")  # 只验证代理参数是否传入，不发真实请求

    monkeypatch.setattr("sjtu_agent.agent.tools._web_search.requests.get", fake_get)
    tool_web_search("proxy check")
    assert seen["proxies"] == {(
        ("http", "http://127.0.0.1:7890"),
        ("https", "http://127.0.0.1:7890"),
    )}


def test_web_search_no_proxy_by_default(monkeypatch):
    """未配置专用搜索代理时，不主动传 proxies（尊重 HTTPS_PROXY 环境变量）。"""
    monkeypatch.delenv("SJTU_WEB_SEARCH_PROXY", raising=False)
    seen = {}

    def fake_get(url, **kwargs):
        seen["proxies"] = kwargs.get("proxies")
        raise RuntimeError("offline")

    monkeypatch.setattr("sjtu_agent.agent.tools._web_search.requests.get", fake_get)
    tool_web_search("no proxy")
    assert seen["proxies"] is None
