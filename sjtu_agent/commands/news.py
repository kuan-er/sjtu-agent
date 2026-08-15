"""
sjtu_agent/commands/news.py — /news 系列命令执行（飞书 / WebUI 共享）。
"""

from __future__ import annotations


def fetch_news_digest(top_k: int = 8) -> str:
    """获取校园新闻摘要，返回 Markdown。"""
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
    md_digest, _, _ = aggregator.run(hours=24, top_k=top_k)
    return md_digest


def cmd_news(user_id: str, parts: list[str]) -> str:
    del user_id, parts
    return "[news] 正在生成校园新闻摘要…\n\n" + fetch_news_digest()


def cmd_news_block(user_id: str, parts: list[str]) -> str:
    del user_id
    from sjtu_agent.news_aggregator.profile import UserProfile
    category = parts[1].strip() if len(parts) > 1 else ""
    if not category:
        return "[news] 请指定要屏蔽的分类，如 `/news_block 教务处`。可用分类：教务处、水源社区、交大新闻网、Canvas"
    UserProfile().block_category(category)
    return f"[news] 已屏蔽「{category}」类新闻，后续摘要将不再推送此类内容。用 `/news_reset` 可重置。"


def cmd_news_reset(user_id: str, parts: list[str]) -> str:
    del user_id, parts
    from sjtu_agent.news_aggregator.profile import UserProfile
    UserProfile().reset()
    return "[news] 已重置新闻画像，下次摘要将恢复默认推荐。"
