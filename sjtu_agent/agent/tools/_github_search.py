"""
sjtu_agent/agent/tools/_github_search.py — GitHub 仓库搜索工具。

用 GitHub REST API 搜索公开仓库，返回 full_name / 链接 / 描述 / star 数，
比抓取 GitHub 网页或通用搜索引擎更准，也不容易被限流页面误导。
"""

from __future__ import annotations

import urllib.parse

import requests

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "github_repo_search",
            "description": (
                "在 GitHub 上搜索公开仓库。返回 full_name、链接、描述、star 数等。"
                "用户说「找 GitHub 项目 / 仓库 / 源码 / star / fork」时优先调用；"
                "搜索自己项目的仓库时也要用，不要用通用网页搜索。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "仓库关键词，如 sjtu-agent 或 kuan-er/sjtu-agent",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回多少条结果，默认 5，最大 10",
                    },
                },
                "required": ["query"],
            },
        },
    }
]

_USER_AGENT = "sjtu-agent"


def tool_github_repo_search(query: str, max_results: int = 5) -> dict:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "query 不能为空"}
    max_results = max(1, min(int(max_results or 5), 10))

    url = (
        "https://api.github.com/search/repositories?q="
        + urllib.parse.quote(query)
        + f"&per_page={max_results}"
    )
    try:
        resp = requests.get(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT},
            timeout=15,
        )
        if resp.status_code == 429:
            return {
                "ok": False,
                "error": "GitHub API 限流（429），请稍后再试或改用 web_search",
            }
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        return {"ok": False, "error": f"GitHub 搜索失败：{exc}"}

    results = []
    for item in (payload.get("items") or [])[:max_results]:
        results.append({
            "full_name": item.get("full_name", ""),
            "name": item.get("name", ""),
            "owner": (item.get("owner") or {}).get("login", ""),
            "url": item.get("html_url", ""),
            "description": item.get("description") or "",
            "stars": item.get("stargazers_count", 0),
            "language": item.get("language") or "",
            "updated_at": item.get("updated_at", ""),
        })

    if not results:
        return {"ok": True, "query": query, "results": [], "total_count": payload.get("total_count", 0)}
    return {"ok": True, "query": query, "results": results, "total_count": payload.get("total_count", 0)}
