"""
sjtu_agent/commands/news.py — /news 系列命令执行（飞书 / WebUI 共享）。
"""

from __future__ import annotations

from .result import CommandResult


def _fetch_news(top_k: int = 8) -> tuple[str, list[dict]]:
    """Return (markdown_digest, json_safe_items)."""
    from sjtu_agent.news_aggregator import NewsAggregator
    from sjtu_agent.agent.chat_loop import load_agent_config
    from sjtu_agent.agent.runner import _make_client

    llm_client = None
    model = ""
    try:
        cfg = load_agent_config()
        if cfg.get("api_key") and cfg.get("model"):
            llm_client = _make_client(cfg)
            model = cfg["model"]
    except Exception:
        pass

    aggregator = NewsAggregator(llm_client=llm_client, model=model)
    md_digest, _, _, items = aggregator.run_structured(hours=24, top_k=top_k)
    return md_digest, items


def fetch_news_digest(top_k: int = 8) -> str:
    """获取校园新闻摘要，返回 Markdown（飞书沿用）。"""
    md_digest, _ = _fetch_news(top_k=top_k)
    return md_digest


def cmd_news(user_id: str, parts: list[str]) -> CommandResult:
    del user_id, parts
    digest, items = _fetch_news()
    return CommandResult(
        view="news",
        text="[news] 正在生成校园新闻摘要…\n\n" + digest,
        data={"ok": True, "digest": digest, "items": items},
    )


def cmd_news_block(user_id: str, parts: list[str]) -> CommandResult:
    del user_id
    from sjtu_agent.news_aggregator.profile import UserProfile
    category = parts[1].strip() if len(parts) > 1 else ""
    if not category:
        text = "[news] 请指定要屏蔽的分类，如 `/news_block 教务处`。可用分类：教务处、水源社区、交大新闻网、Canvas"
        return CommandResult(
            view="news_preference",
            text=text,
            data={"ok": False, "action": "block", "category": ""},
        )
    UserProfile().block_category(category)
    text = f"[news] 已屏蔽「{category}」类新闻，后续摘要将不再推送此类内容。用 `/news_reset` 可重置。"
    return CommandResult(
        view="news_preference",
        text=text,
        data={"ok": True, "action": "block", "category": category},
    )


def cmd_news_reset(user_id: str, parts: list[str]) -> CommandResult:
    del user_id, parts
    from sjtu_agent.news_aggregator.profile import UserProfile
    UserProfile().reset()
    return CommandResult(
        view="news_preference",
        text="[news] 已重置新闻画像，下次摘要将恢复默认推荐。",
        data={"ok": True, "action": "reset"},
    )
