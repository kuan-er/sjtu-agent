"""
sjtu_agent/agent/tools/_web_search.py — 公开网页搜索工具。

无 API Key，抓取搜索引擎 HTML 结果页并解析标题 / 链接 / 摘要。
模型应把它用于：不认识的缩写 / 黑话 / 术语、知识截止后的新信息、
需要时效性的事实。搜索失败时返回结构化错误，由模型如实说明。
"""

from __future__ import annotations

import html
import os
import re
import urllib.parse

import requests

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "联网搜索公开网页，返回标题、链接和摘要。"
                "当用户提到你不认识的缩写、黑话、产品名、人物、事件，"
                "或问题涉及知识截止后的时效性信息时，必须先调用本工具，"
                "不得凭空猜测或假装知道。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，尽量保留用户原始说法",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回多少条结果，默认 5，最大 8",
                    },
                },
                "required": ["query"],
            },
        },
    }
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    text = _TAG_RE.sub(" ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _proxy_config() -> dict | None:
    """web_search 专用代理：只让搜索引擎请求走代理，其余流量完全不碰。

    设置环境变量 SJTU_WEB_SEARCH_PROXY=http://host:port 后，本工具固定走该
    代理（覆盖 HTTPS_PROXY 全局项）；未设置时保持默认（尊重 HTTP_PROXY /
    HTTPS_PROXY 环境变量，或直连）。适合"服务器配了代理但不想全局开、
    也不担心校园账号走代理"的场景。
    """
    proxy = os.environ.get("SJTU_WEB_SEARCH_PROXY", "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _search_bing(query: str, max_results: int) -> list[dict]:
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    resp = requests.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=12,
        proxies=_proxy_config(),
    )
    resp.raise_for_status()
    page = resp.text

    results: list[dict] = []
    # Bing 结果块：<li class="b_algo">…<h2><a href=…>标题</a></h2>…<p>摘要</p>
    for match in re.finditer(
        r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r"<p[^>]*>(.*?)</p>",
        page,
        re.DOTALL | re.IGNORECASE,
    ):
        title = _clean(match.group(2))
        snippet = _clean(match.group(3))
        link = html.unescape(match.group(1))
        if not title or not link:
            continue
        results.append({"title": title, "url": link, "snippet": snippet[:300]})
        if len(results) >= max_results:
            break
    return results


def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query)
    resp = requests.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=12,
        proxies=_proxy_config(),
    )
    resp.raise_for_status()
    page = resp.text

    links = re.findall(
        r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        page,
        re.DOTALL | re.IGNORECASE,
    )
    snippets = re.findall(
        r'<td[^>]*class=["\']result-snippet["\'][^>]*>(.*?)</td>',
        page,
        re.DOTALL | re.IGNORECASE,
    )

    results: list[dict] = []
    for index, (raw_link, raw_title) in enumerate(links):
        link = html.unescape(raw_link)
        title = _clean(raw_title)
        if not title or not link:
            continue
        snippet = _clean(snippets[index]) if index < len(snippets) else ""
        results.append({"title": title, "url": link, "snippet": snippet[:300]})
        if len(results) >= max_results:
            break
    return results


def _merge_results(batches: list[list[dict]], max_results: int) -> list[dict]:
    seen: set[str] = set()
    merged: list[dict] = []
    for batch in batches:
        for item in batch:
            key = item.get("url", "")
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= max_results:
                return merged
    return merged


def _query_variants(query: str) -> list[str]:
    """原始查询 + 缩写单独查询，提高黑话命中率。"""
    variants = [query]
    acronyms = [token for token in re.findall(r"[A-Za-z0-9]+", query)
                if token.isupper() and 2 <= len(token) <= 6]
    if acronyms:
        variants.append(" ".join(acronyms))
    base = re.sub(r"\b[A-Z]{2,6}\b", " ", query)
    base = re.sub(r"\s+", " ", base).strip()
    if base and base != query:
        variants.append(base)
    return variants[:3]


def tool_web_search(query: str, max_results: int = 5) -> dict:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "query 不能为空"}
    max_results = max(1, min(int(max_results or 5), 8))

    errors: list[str] = []
    batches: list[list[dict]] = []
    for variant in _query_variants(query):
        for searcher in (_search_bing, _search_duckduckgo):
            try:
                results = searcher(variant, max_results)
                if results:
                    batches.append(results)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        merged = _merge_results(batches, max_results)
        if len(merged) >= 2:
            return {"ok": True, "query": query, "results": merged}
        batches = list(merged and [merged] or [])

    merged = _merge_results(batches, max_results)
    if merged:
        return {"ok": True, "query": query, "results": merged}

    return {
        "ok": False,
        "query": query,
        "error": "联网搜索失败（" + "；".join(errors[-2:]) + "）",
        "results": [],
    }
