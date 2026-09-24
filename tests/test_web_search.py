from __future__ import annotations

import base64

import pytest
import requests

from sjtu_agent.agent.tools import _web_search as ws


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _rss(*items) -> str:
    body = "".join(
        f"<item><title>{title}</title><link>{url}</link>"
        f"<description>{desc}</description></item>"
        for title, url, desc in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'


def _bing_html(*items) -> str:
    blocks = "".join(
        f'<li class="b_algo"><h2><a href="{url}">{title}</a></h2><p>{desc}</p>'
        for title, url, desc in items
    )
    return f"<html><body><ol>{blocks}</ol></body></html>"


def _so360_html(*items) -> str:
    blocks = "".join(
        f'<li class="res-list"><h3 class="res-title">'
        f'<a href="https://www.so.com/link?m=shell" data-mdurl="{url}">{title}</a></h3>'
        f'<p class="res-desc"><span class="gray g-c-gray">2026年7月3日 - </span>{desc}</p></li>'
        for title, url, desc in items
    )
    return f"<html><body><ul>{blocks}</ul></body></html>"


def _router(routes, calls=None):
    """按 URL 关键字路由假响应；未命中返回空页。"""

    def fake_get(url, **kwargs):
        if calls is not None:
            calls.append(url)
        for marker, payload in routes.items():
            if marker in url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        return FakeResponse("<html></html>")

    return fake_get


@pytest.fixture(autouse=True)
def _reset_engine_state():
    """引擎冷却状态是进程内全局的，测试之间必须清干净。"""
    ws._ENGINE_BLOCKED_UNTIL.clear()
    yield
    ws._ENGINE_BLOCKED_UNTIL.clear()


# ── 基本通路 ─────────────────────────────────────────────────────────────────


def test_bing_rss_returns_real_urls(monkeypatch):
    monkeypatch.setattr(
        ws.requests,
        "get",
        _router(
            {
                "format=rss": _rss(
                    ("DeepSeek Harness 介绍", "https://example.com/dsh", "DSH 是开源 Agent 框架")
                )
            }
        ),
    )
    result = ws.tool_web_search("DeepSeek Harness DSH 是什么")
    assert result["ok"] is True
    assert result["results"][0]["url"] == "https://example.com/dsh"
    assert result["results"][0]["source"] == "example.com"


def test_bing_html_decodes_ck_a_tracking_link(monkeypatch):
    """Bing 的 ck/a 跳转壳必须还原成真实 URL（此前直接把壳链接交给模型）。"""
    real = "https://news.qq.com/rain/a/20260701A066KG00"
    token = base64.urlsafe_b64encode(real.encode()).decode().rstrip("=")
    shell = f"https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u=a1{token}&amp;ntb=1"
    monkeypatch.setattr(
        ws.requests,
        "get",
        _router(
            {
                "format=rss": "<rss></rss>",
                "/search?q=": _bing_html(("Anthropic 封号潮", shell, "大面积封号")),
            }
        ),
    )
    result = ws.tool_web_search("Anthropic 封号 争议")
    assert result["ok"] is True
    assert result["results"][0]["url"] == real
    assert result["results"][0]["source"] == "news.qq.com"


def test_opaque_tracking_link_is_dropped(monkeypatch):
    """so.com/link 这类需要额外请求才能解析的壳：宁缺毋滥，直接丢弃。"""
    monkeypatch.setattr(
        ws.requests,
        "get",
        _router({"format=rss": _rss(("某结果", "https://www.so.com/link?m=abc", "摘要"))}),
    )
    result = ws.tool_web_search("任意查询")
    assert result["ok"] is False
    assert result["results"] == []


def test_engine_owned_hosts_are_filtered(monkeypatch):
    """搜索引擎自家的翻译/图片/聚合页不是内容页。"""
    monkeypatch.setattr(
        ws.requests,
        "get",
        _router(
            {
                "format=rss": _rss(
                    ("360图片", "https://ranks.hao.360.com/", "图片搜索"),
                    ("360翻译", "https://fanyi.so.com/", "翻译"),
                    ("正常结果", "https://news.qq.com/a/1", "有效内容"),
                )
            }
        ),
    )
    result = ws.tool_web_search("任意查询")
    urls = [item["url"] for item in result["results"]]
    assert "https://news.qq.com/a/1" in urls
    assert all("360.com" not in url and "so.com" not in url for url in urls)


def test_same_domain_results_are_capped(monkeypatch):
    monkeypatch.setattr(
        ws.requests,
        "get",
        _router(
            {
                "format=rss": _rss(
                    *[(f"标题{index}", f"https://example.com/{index}", "摘要") for index in range(5)]
                )
            }
        ),
    )
    result = ws.tool_web_search("任意查询")
    assert len(result["results"]) == ws._MAX_PER_DOMAIN


# ── 质量驱动的降级 ───────────────────────────────────────────────────────────


def test_escalates_to_360_when_bing_misses_the_point(monkeypatch):
    """Bing 只给品牌官网时，必须换来源，并把真正对题的结果排到最前。"""
    brand = _rss(
        ("Home \\ Anthropic", "https://www.anthropic.com/", "AI safety company"),
        ("Anthropic - Wikipedia", "https://en.wikipedia.org/wiki/Anthropic", "encyclopedia"),
        ("Company", "https://www.anthropic.com/company", "about us"),
    )
    topical = _so360_html(
        ("Anthropic 大面积封号", "https://news.qq.com/rain/a/1", "封号 争议 持续发酵"),
        ("Anthropic 风评崩塌", "https://www.huxiu.com/article/2", "用户 不满 封号 风评 争议"),
    )
    monkeypatch.setattr(
        ws.requests, "get", _router({"format=rss": brand, "so.com/s?": topical})
    )
    result = ws.tool_web_search("Anthropic 风评 封号 用户不满")
    sources = [item["source"] for item in result["results"]]
    assert result["ok"] is True
    assert sources[0] == "huxiu.com"          # 焦点覆盖度最高者排最前
    assert "news.qq.com" in sources
    assert sources[0] != "anthropic.com"      # 品牌官网不再霸榜


def test_no_domestic_fallback_when_results_are_relevant(monkeypatch):
    """结果本来就对题时不做多余请求（省时间、少暴露）。"""
    calls: list[str] = []
    relevant = _rss(
        *[
            (
                f"上海交通大学 通知{index}",
                f"https://www{index}.sjtu.edu.cn/notice/{index}",
                "上海交通大学 地址 与 通知",
            )
            for index in range(4)
        ]
    )
    monkeypatch.setattr(
        ws.requests,
        "get",
        _router({"format=rss": relevant, "so.com/s?": _so360_html()}, calls=calls),
    )
    result = ws.tool_web_search("上海交通大学 地址")
    assert result["ok"] is True
    assert not any("so.com/s?" in url for url in calls)


def test_thin_detection_flags_brand_only_results():
    grams = ws._focus_grams("Anthropic 风评 封号")
    brand_only = [
        {"title": "Home \\ Anthropic", "snippet": "AI safety", "url": "https://www.anthropic.com/"},
        {"title": "Anthropic", "snippet": "company", "url": "https://en.wikipedia.org/wiki/Anthropic"},
        {"title": "Company", "snippet": "about", "url": "https://www.anthropic.com/company"},
    ]
    assert ws._is_thin(brand_only, grams) is True
    on_point = [
        {"title": "Anthropic 封号 风评", "snippet": "封号 风评 争议", "url": "https://news.qq.com/a"}
    ] * 3
    assert ws._is_thin(on_point, grams) is False


def test_opinion_query_gets_discussion_variant():
    variants = ws._query_variants("Anthropic 风评 封号")
    assert any("知乎" in variant for variant in variants)


# ── 关键词形态 ───────────────────────────────────────────────────────────────


def test_web_search_tries_acronym_variant(monkeypatch):
    queries: list[str] = []

    def fake_get(url, **kwargs):
        queries.append(url)
        if "q=DSH" in url and "format=rss" in url:
            return FakeResponse(_rss(("DSH 定义", "https://example.org/dsh", "DSH 是 DeepSeek Harness。")))
        return FakeResponse("<html></html>")

    monkeypatch.setattr(ws.requests, "get", fake_get)
    result = ws.tool_web_search("DeepSeek Harness DSH 的消息")
    assert result["ok"] is True
    assert result["results"][0]["title"] == "DSH 定义"
    assert any("q=DSH" in url for url in queries)


# ── 搜索即阅读（read_top） ───────────────────────────────────────────────────


def _article(body: str, paragraphs: int = 12) -> str:
    inner = "".join(f"<p>{body}</p>" for _ in range(paragraphs))
    return f"<html><head><title>文章标题</title></head><body><article>{inner}</article></body></html>"


def test_reads_top_pages_when_snippets_are_short(monkeypatch):
    rss = _rss(
        ("Anthropic 封号潮", "https://news.qq.com/a/1", "短摘要"),
        ("Anthropic 封号申诉", "https://www.huxiu.com/a/2", "短摘要"),
        ("Anthropic 封号后续", "https://www.sina.cn/a/3", "短摘要"),
    )
    monkeypatch.setattr(
        ws.requests,
        "get",
        _router(
            {
                "format=rss": rss,
                "news.qq.com/a/1": _article("腾讯新闻的正文段落，讲封号潮的来龙去脉。"),
                "huxiu.com/a/2": _article("虎嗅的正文段落，讲申诉成功率与用户不满。"),
            }
        ),
    )
    result = ws.tool_web_search("Anthropic 封号 争议")
    assert result["ok"] is True
    assert result["read"]["pages"] >= 1
    contents = [item.get("content", "") for item in result["results"]]
    assert any("腾讯新闻的正文段落" in text or "虎嗅的正文段落" in text for text in contents)


def test_read_top_zero_disables_reading(monkeypatch):
    calls: list[str] = []
    rss = _rss(("Anthropic 封号潮", "https://news.qq.com/a/1", "短摘要"))
    monkeypatch.setattr(
        ws.requests,
        "get",
        _router(
            {"format=rss": rss, "news.qq.com/a/1": _article("正文段落")}, calls=calls
        ),
    )
    result = ws.tool_web_search("Anthropic 封号 争议", read_top=0)
    assert result["ok"] is True
    assert "read" not in result
    assert not any("news.qq.com/a/1" in url for url in calls)


def test_skips_reading_when_snippets_are_rich(monkeypatch):
    """摘要已经够长时不做多余抓取（默认自动模式）。"""
    rich = "这是一段信息量足够的摘要，包含时间、地点与结论。" * 6
    calls: list[str] = []
    rss = _rss(
        *[
            (f"封号 事件标题{index}", f"https://site{index}.example.com/a", f"封号 {rich}")
            for index in range(3)
        ]
    )
    monkeypatch.setattr(ws.requests, "get", _router({"format=rss": rss}, calls=calls))
    result = ws.tool_web_search("封号")
    assert result["ok"] is True
    assert "read" not in result
    assert len(calls) == 1  # 只有搜索请求，没有正文抓取


def test_explicit_read_top_three_reads_three(monkeypatch):
    rich = "这是一段信息量足够的摘要，包含时间、地点与结论。" * 6
    rss = _rss(
        *[
            (f"封号 事件标题{index}", f"https://site{index}.example.com/a", f"封号 {rich}")
            for index in range(3)
        ]
    )
    routes = {"format=rss": rss}
    for index in range(3):
        routes[f"site{index}.example.com/a"] = _article(f"第{index}篇正文段落。")
    monkeypatch.setattr(ws.requests, "get", _router(routes))
    result = ws.tool_web_search("封号", read_top=3)
    assert result["read"]["pages"] == 3
    assert all(item.get("content") for item in result["results"][:3])


def test_read_failure_does_not_break_search(monkeypatch):
    rss = _rss(("Anthropic 封号潮", "https://news.qq.com/a/1", "短摘要"))

    def fake_get(url, **kwargs):
        if "format=rss" in url:
            return FakeResponse(rss)
        if "news.qq.com/a/1" in url:
            return FakeResponse("gone", status_code=404)
        return FakeResponse("<html></html>")

    monkeypatch.setattr(ws.requests, "get", fake_get)
    result = ws.tool_web_search("Anthropic 封号 争议")
    assert result["ok"] is True
    assert result["read"]["failed"] >= 1
    assert result["results"][0]["title"] == "Anthropic 封号潮"


def test_reading_skips_urls_resolving_to_private(monkeypatch):
    """正文抓取同样受 SSRF 校验约束：内网地址直接跳过，不发请求。"""
    from sjtu_agent.agent.tools import _web_common

    monkeypatch.setattr(_web_common, "resolves_to_private", lambda host: True)
    calls: list[str] = []
    rss = _rss(("内网页面", "https://internal.example.com/secret", "短摘要"))
    monkeypatch.setattr(ws.requests, "get", _router({"format=rss": rss}, calls=calls))
    result = ws.tool_web_search("某查询", read_top=1)
    assert result["ok"] is True
    assert result["read"]["failed"] >= 1
    assert not any("internal.example.com" in url for url in calls)


def test_long_article_is_truncated(monkeypatch):
    sentence = "这是一段很长的正文内容，用来验证截断逻辑。"
    html = (
        "<html><body><article>"
        + "".join(f"<p>第{index}段：{sentence * 20}</p>" for index in range(6))
        + "</article></body></html>"
    )
    rss = _rss(("长文", "https://news.qq.com/a/1", "短摘要"))
    monkeypatch.setattr(
        ws.requests, "get", _router({"format=rss": rss, "news.qq.com/a/1": html})
    )
    result = ws.tool_web_search("某查询", read_top=1)
    content = result["results"][0]["content"]
    assert content.endswith("[内容过长，已截断]")
    assert len(content) <= ws._READ_TOTAL_CHARS + 64


def test_auto_read_skips_off_topic_results(monkeypatch):
    """自动模式不把正文预算花在没打到问题焦点的条目上（实测别读官网首页）。"""
    calls: list[str] = []
    rss = _rss(
        ("Home \\ Anthropic", "https://www.anthropic.com/", "AI safety company"),
        ("Anthropic - Wikipedia", "https://en.wikipedia.org/wiki/Anthropic", "encyclopedia"),
    )
    monkeypatch.setattr(ws.requests, "get", _router({"format=rss": rss}, calls=calls))
    result = ws.tool_web_search("Anthropic 风评 封号 用户不满")
    assert result["ok"] is True
    assert "read" not in result
    assert not any("anthropic.com/" in url or "wikipedia.org" in url for url in calls)


def test_block_page_marks_engine_blocked_and_is_skipped(monkeypatch):
    """反爬页不能被当成「没有结果」静默吞掉：要记错误，并冷却期内不再撞。"""
    calls: list[str] = []
    brand = _rss(("Home \\ Anthropic", "https://www.anthropic.com/", "AI safety company"))
    block_page = "<html><head><title>访问异常页面</title></head><body>请稍后再试</body></html>"
    monkeypatch.setattr(
        ws.requests, "get", _router({"format=rss": brand, "so.com/s?": block_page}, calls=calls)
    )
    first = ws.tool_web_search("Anthropic 风评 封号")
    assert first["ok"] is True
    assert any("so.com/s?" in url for url in calls)
    assert not ws._engine_available("so360")
    assert any("so360" in note for note in first.get("degraded", []))

    seen = len(calls)
    second = ws.tool_web_search("Anthropic 风评 封号")
    assert second["ok"] is True
    assert not any("so.com/s?" in url for url in calls[seen:])


# ── 官方后端（auto：优先官方，失败回落抓取） ─────────────────────────────────


def _official_items(count: int = 4) -> list[dict]:
    return [
        {
            "title": f"官方结果{index}",
            "url": f"https://official{index}.example.com/a",
            "snippet": "官方搜索给出的引用摘要。",
            "source": f"official{index}.example.com",
        }
        for index in range(count)
    ]


def test_official_backend_used_when_available(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(ws.official, "enabled", lambda: True)
    monkeypatch.setattr(ws.official, "search", lambda query, max_results: _official_items())
    monkeypatch.setattr(ws.requests, "get", _router({}, calls=calls))

    result = ws.tool_web_search("某查询")
    assert result["ok"] is True
    assert result["backend"] == "deepseek-official"
    assert len(result["results"]) == 4
    assert calls == []  # 官方够用时不再打抓取栈（省时间、少暴露）


def test_official_failure_falls_back_to_scrapers(monkeypatch):
    def boom(query, max_results):
        raise ws.official.OfficialSearchError("未配置 Key")

    monkeypatch.setattr(ws.official, "enabled", lambda: True)
    monkeypatch.setattr(ws.official, "search", boom)
    rss = _rss(("Anthropic 封号潮", "https://news.qq.com/a/1", "封号 争议 摘要" * 40))
    monkeypatch.setattr(ws.requests, "get", _router({"format=rss": rss}))

    result = ws.tool_web_search("Anthropic 封号 争议")
    assert result["ok"] is True
    assert result["backend"].startswith("bing-rss")
    assert any("deepseek-official" in note for note in result.get("degraded", []))


def test_scrapers_mode_never_calls_official(monkeypatch):
    monkeypatch.setenv(ws.official.BACKEND_ENV, "scrapers")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    def should_not_run(*args, **kwargs):
        raise AssertionError("scrapers 模式下不应调用官方搜索")

    monkeypatch.setattr(ws.official, "search", should_not_run)
    rss = _rss(("封号 标题", "https://news.qq.com/a/1", "封号 摘要" * 40))
    monkeypatch.setattr(ws.requests, "get", _router({"format=rss": rss}))

    result = ws.tool_web_search("封号")
    assert result["ok"] is True
    assert result["backend"] == "bing-rss"


# ── 失败语义 ─────────────────────────────────────────────────────────────────


def test_web_search_empty_query():
    result = ws.tool_web_search("  ")
    assert result["ok"] is False
    assert "query" in result["error"]


def test_retries_once_on_transient_tls_error(monkeypatch):
    monkeypatch.setattr(ws, "_RETRY_BACKOFF", 0)
    attempts = {"n": 0}

    def fake_get(url, **kwargs):
        if "format=rss" in url:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise requests.exceptions.SSLError("handshake failed")
            return FakeResponse(_rss(("结果", "https://example.com/a", "摘要")))
        return FakeResponse("<html></html>")

    monkeypatch.setattr(ws.requests, "get", fake_get)
    result = ws.tool_web_search("任意查询")
    assert result["ok"] is True
    assert attempts["n"] == 2


def test_web_search_failure_is_structured_with_hint(monkeypatch):
    monkeypatch.setattr(ws, "_RETRY_BACKOFF", 0)

    def fake_get(*args, **kwargs):
        raise requests.exceptions.SSLError("offline")

    monkeypatch.setattr(ws.requests, "get", fake_get)
    result = ws.tool_web_search("anything")
    assert result["ok"] is False
    assert "联网搜索失败" in result["error"]
    assert "SJTU_WEB_SEARCH_PROXY" in result["hint"]
    assert result["results"] == []


def test_web_search_uses_dedicated_proxy_when_configured(monkeypatch):
    """设置 SJTU_WEB_SEARCH_PROXY 后，web_search 的请求必须走该代理。"""
    seen = {}
    monkeypatch.setenv("SJTU_WEB_SEARCH_PROXY", "http://127.0.0.1:7890")

    def fake_get(url, **kwargs):
        seen.setdefault("proxies", set()).add(
            tuple(sorted((kwargs.get("proxies") or {}).items()))
        )
        raise RuntimeError("offline")  # 只验证代理参数是否传入，不发真实请求

    monkeypatch.setattr(ws.requests, "get", fake_get)
    ws.tool_web_search("proxy check")
    assert seen["proxies"] == {
        (("http", "http://127.0.0.1:7890"), ("https", "http://127.0.0.1:7890"))
    }


def test_web_search_no_proxy_by_default(monkeypatch):
    """未配置专用搜索代理时，不主动传 proxies（尊重 HTTPS_PROXY 环境变量）。"""
    monkeypatch.delenv("SJTU_WEB_SEARCH_PROXY", raising=False)
    seen = {}

    def fake_get(url, **kwargs):
        seen["proxies"] = kwargs.get("proxies")
        raise RuntimeError("offline")

    monkeypatch.setattr(ws.requests, "get", fake_get)
    ws.tool_web_search("no proxy")
    assert seen["proxies"] is None
