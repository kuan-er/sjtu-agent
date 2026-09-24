"""
sjtu_agent/agent/tools/_web_search_official.py — DeepSeek 官方联网搜索（服务端工具）。

DeepSeek 没有独立的检索端点：官方搜索是 **Anthropic 兼容 Messages API 的服务端工具**
（`web_search_20250305`），一次搜索消耗一个完整模型轮次。本模块把它包成与
`_web_search.py` 相同的结果结构，供 `web_search` 在 auto 模式下优先使用；
失败/未配置 Key 时由调用方回落到免 Key 的多引擎抓取栈。

官方约定（与 DeepSeek Harness 的 `dsh-web-search-deepseek` 实现一致）：

- 端点 `{baseURL}/messages`，baseURL 默认 `https://api.deepseek.com/anthropic/v1`
  —— 注意**不是** chat-completions 的 `https://api.deepseek.com`；
- 请求头 `x-api-key` + `authorization: Bearer`，`anthropic-version: 2023-06-01`；
- 请求体：一句 `Perform a web search for the query: <q>` + `tools` 里声明
  `{"type": "web_search_20250305", "name": "web_search", "max_uses": N}`；
- 结果：`content[]` 里的 `web_search_tool_result` → `web_search_result`
  （`url` / `title` / `page_age`）；摘要来自 `text` 块的 `citations[].cited_text`，
  按 URL 拼接、按 URL 去重——**绝不从模型回复文本里抓 URL**；
- 没有 `web_search_tool_result` 块 → 结构化报错，而不是静默变成"没搜到"。

需要 **DeepSeek 官方 API Key**（`DEEPSEEK_API_KEY`）；致远一号等第三方网关不提供
这条 Anthropic Messages + 服务端工具的路径，因此它们继续走免 Key 抓取栈。
"""

from __future__ import annotations

import os
import urllib.parse

import requests

# 后端选择：auto（默认，官方优先、失败回落）| scrapers（只用免 Key 抓取）| deepseek（只用官方）
BACKEND_ENV = "SJTU_WEB_SEARCH_BACKEND"
BASE_URL_ENV = "SJTU_SEARCH_BASE_URL"
MODEL_ENV = "SJTU_SEARCH_MODEL"
MAX_USES_ENV = "SJTU_SEARCH_MAX_USES"

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic/v1"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_MAX_USES = 3
API_VERSION = "2023-06-01"
MAX_TOKENS = 4096
SEARCH_TIMEOUT = 40.0
_BACKENDS = ("auto", "scrapers", "deepseek")


class OfficialSearchError(RuntimeError):
    """官方搜索不可用或调用失败（调用方据此回落到抓取栈）。"""


def backend_mode() -> str:
    raw = (os.environ.get(BACKEND_ENV) or "auto").strip().lower()
    return raw if raw in _BACKENDS else "auto"


def resolve_api_key() -> str:
    """找出可用的 **DeepSeek 官方** Key；没有就返回空串。

    只看官方来源：`DEEPSEEK_API_KEY` 环境变量，或 agent_config.json 里配的
    DeepSeek 官方端点。致远一号等网关的 Key 不适用（没有这条 API 路径）。
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    try:  # 延迟导入：避免与 chat_loop → tools 的导入顺序打架
        from sjtu_agent.agent.chat_loop import load_agent_config

        cfg = load_agent_config() or {}
    except Exception:  # noqa: BLE001 — 配置读不到就当没有 Key
        return ""
    base_url = str(cfg.get("base_url") or "").lower()
    api_key = str(cfg.get("api_key") or "").strip()
    if api_key and "api.deepseek.com" in base_url:
        return api_key
    return ""


def enabled() -> bool:
    """auto/deepseek 模式下且有 Key 才值得尝试官方后端。"""
    if backend_mode() == "scrapers":
        return False
    return bool(resolve_api_key())


def _domain(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _search_base_url() -> str:
    return (
        os.environ.get(BASE_URL_ENV, "").strip() or DEFAULT_BASE_URL
    ).rstrip("/")


def _max_uses() -> int:
    raw = os.environ.get(MAX_USES_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_MAX_USES


def _model() -> str:
    return os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL


def _citations(blocks: list) -> dict[str, str]:
    """把每个 text 块的 citations 汇成 url → cited_text（首次出现优先）。"""
    mapping: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        for cite in block.get("citations") or []:
            if not isinstance(cite, dict):
                continue
            url = str(cite.get("url") or "").strip()
            text = cite.get("cited_text")
            if url and isinstance(text, str) and text.strip() and url not in mapping:
                mapping[url] = text.strip()
    return mapping


def _map_blocks(data: dict, max_results: int) -> list[dict] | None:
    """把 Anthropic 响应映射成搜索结果；没有结果块时返回 None。"""
    blocks = data.get("content") or []
    if not isinstance(blocks, list):
        return None
    citations = _citations(blocks)

    results: list[dict] = []
    seen: set[str] = set()
    saw_result_block = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "web_search_tool_result":
            continue
        saw_result_block = True
        payload = block.get("content")
        if isinstance(payload, dict):  # 例如 web_search_tool_result_error
            code = payload.get("error_code") or payload.get("type") or "unknown"
            raise OfficialSearchError(f"DeepSeek 原生搜索失败：{code}")
        for item in payload or []:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            entry = {
                "title": str(item.get("title") or url).strip(),
                "url": url,
                "snippet": citations.get(url, "")[:300],
                "source": _domain(url),
            }
            published = str(item.get("page_age") or "").strip()
            if published:
                entry["published"] = published
            results.append(entry)
            if len(results) >= max_results:
                return results
    return results if saw_result_block else None


def search(
    query: str,
    max_results: int = 5,
    *,
    api_key: str = "",
    timeout: float = SEARCH_TIMEOUT,
) -> list[dict]:
    """调用 DeepSeek 官方服务端搜索。失败抛 OfficialSearchError（调用方回落）。"""
    query = (query or "").strip()
    if not query:
        raise OfficialSearchError("query 不能为空")

    key = api_key or resolve_api_key()
    if not key:
        raise OfficialSearchError(
            "未配置 DeepSeek 官方 API Key（DEEPSEEK_API_KEY），官方搜索不可用"
        )

    endpoint = f"{_search_base_url()}/messages"
    body = {
        "model": _model(),
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Perform a web search for the query: {query}"}
                ],
            }
        ],
        "tools": [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": _max_uses(),
            }
        ],
    }
    headers = {
        "x-api-key": key,
        "authorization": f"Bearer {key}",
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
        "accept": "application/json",
    }

    try:
        resp = requests.post(endpoint, headers=headers, json=body, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise OfficialSearchError(f"DeepSeek 搜索请求超时（{timeout:.0f}s）") from exc
    except Exception as exc:  # noqa: BLE001
        raise OfficialSearchError(f"DeepSeek 搜索请求失败：{type(exc).__name__}") from exc

    if resp.status_code != 200:
        detail = ""
        try:
            parsed = resp.json()
            error = parsed.get("error")
            detail = error if isinstance(error, str) else (error or {}).get("message", "")
        except Exception:  # noqa: BLE001 — 错误体不是 JSON 也无所谓
            detail = ""
        raise OfficialSearchError(
            f"DeepSeek 搜索接口 HTTP {resp.status_code}" + (f"：{detail}" if detail else "")
        )

    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise OfficialSearchError("DeepSeek 搜索返回了无法解析的响应体") from exc

    results = _map_blocks(data, max_results)
    if results is None:
        raise OfficialSearchError(
            "DeepSeek 未返回 web_search_tool_result 块（本次请求可能没触发原生搜索）"
        )
    return results
