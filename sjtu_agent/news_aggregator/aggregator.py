"""sjtu_agent/news_aggregator/aggregator.py — 主聚合流程。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from sjtu_agent.news_aggregator.sources.base import NewsItem
from sjtu_agent.news_aggregator.sources.jwc import JwcSource
from sjtu_agent.news_aggregator.sources.shuiyuan import ShuiyuanSource
from sjtu_agent.news_aggregator.sources.official import OfficialSource
from sjtu_agent.news_aggregator.sources.canvas import CanvasSource
from sjtu_agent.news_aggregator.profile import UserProfile
from sjtu_agent.news_aggregator.ranker import NewsRanker
from sjtu_agent.news_aggregator.digest import DigestBuilder
from sjtu_agent.news_aggregator.storage import NewsStorage


class NewsAggregator:
    """完整的新闻聚合流程。"""

    def __init__(self, llm_client=None, model: str = ""):
        self.sources = [
            JwcSource(),
            ShuiyuanSource(),
            OfficialSource(),
            CanvasSource(),
        ]
        self.profile  = UserProfile()
        self.ranker   = NewsRanker()
        self.builder  = DigestBuilder()
        self.storage  = NewsStorage()
        self.llm_client = llm_client
        self.model    = model

    def run(self, hours: int = 24, top_k: int = 8) -> tuple[str, str, list]:
        """
        完整聚合流程。
        返回 (markdown_digest, telegram_html_digest, feishu_paras)。
        """
        md_digest, html_digest, feishu_paras, _ = self._run(hours=hours, top_k=top_k)
        return md_digest, html_digest, feishu_paras

    def run_structured(self, hours: int = 24, top_k: int = 8) -> tuple[str, str, list, list[dict]]:
        """
        完整聚合流程，并额外返回 JSON-safe 的新闻条目列表。

        返回 (markdown_digest, telegram_html_digest, feishu_paras, items)。
        """
        md_digest, html_digest, feishu_paras, ranked = self._run(hours=hours, top_k=top_k)
        items = []
        for item, score, reason in ranked:
            items.append({
                "id": item.id,
                "source": item.source,
                "title": item.title,
                "summary": item.summary,
                "url": item.url,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "author": item.author,
                "category": item.category,
                "tags": list(item.tags),
                "score": round(float(score), 4),
                "reason": reason,
            })
        return md_digest, html_digest, feishu_paras, items

    def _run(self, hours: int, top_k: int):
        """共用采集/排序/落盘流程，返回 (md, html, feishu_paras, ranked)。"""
        # 1. 并发采集
        all_items: list[NewsItem] = []
        with ThreadPoolExecutor(max_workers=len(self.sources)) as pool:
            futures = {pool.submit(s.fetch_recent, hours): s for s in self.sources}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    items = fut.result()
                    all_items.extend(items)
                    print(f"[news/{src.name}] 采集到 {len(items)} 条", flush=True)
                except Exception as e:
                    print(f"[news/{src.name}] 失败：{e}", flush=True)

        print(f"[news] 总计采集 {len(all_items)} 条", flush=True)

        # 2. 去重（过滤已推送）
        all_items = self.storage.dedupe(all_items)
        print(f"[news] 去重后 {len(all_items)} 条", flush=True)

        # 3. 用户画像过滤
        all_items = [i for i in all_items if not self.profile.is_blocked(i)]

        if not all_items:
            empty_msg = "📰 今天没有新的值得关注的内容。"
            empty_post = [[{"tag": "text", "text": empty_msg}]]
            return empty_msg, empty_msg, empty_post, []

        # 4. 智能排序
        ranked = self.ranker.rank(
            all_items,
            self.profile,
            top_k=top_k,
            llm_client=self.llm_client,
            model=self.model,
        )
        print(f"[news] 排序后精选 {len(ranked)} 条", flush=True)

        # 5. 生成日报
        md_digest     = self.builder.build(ranked, self.profile)
        html_digest   = self.builder.build_telegram_html(ranked, self.profile)
        feishu_paras  = self.builder.build_feishu_post(ranked, self.profile)

        # 6. 标记已推送
        if ranked:
            self.storage.mark_pushed([item.id for item, _, _ in ranked])

        return md_digest, html_digest, feishu_paras, ranked

    def send_via_telegram(self, html_digest: str) -> bool:
        """通过 Telegram 推送日报。"""
        from sjtu_agent import paths as _paths
        from sjtu_agent.paths import read_json_safe
        import requests

        cfg = read_json_safe(_paths.CONFIG_PATH, default={})
        if not cfg.get("telegram_enabled", True):
            print("[news] Telegram 推送已关闭，跳过", flush=True)
            return False
        token = cfg.get("telegram_token", "")
        allowed_ids = [int(x) for x in cfg.get("telegram_allowed_ids", [])]
        if not token or not allowed_ids:
            print("[news] Telegram 未配置，跳过推送", flush=True)
            return False

        success = True
        for uid in allowed_ids:
            # 分块发送（Telegram 限制 4096 字符）
            text = html_digest
            while text:
                chunk = text[:4000]
                text  = text[4000:]
                try:
                    r = requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": uid,
                            "text": chunk,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                        },
                        timeout=15,
                    )
                    if not r.ok:
                        print(f"[news] Telegram 推送失败 uid={uid}: {r.text[:200]}", flush=True)
                        success = False
                except Exception as e:
                    print(f"[news] Telegram 推送异常 uid={uid}: {e}", flush=True)
                    success = False
        return success

    def send_via_feishu(self, post_paras: list[list[dict]]) -> bool:
        """通过飞书推送日报（post 格式）。"""
        from sjtu_agent import paths as _paths
        from sjtu_agent.paths import read_json_safe

        cfg = read_json_safe(_paths.CONFIG_PATH, default={})
        open_id = cfg.get("feishu_open_id", "")
        if not open_id:
            print("[news] 飞书未配置（需要 feishu_open_id），跳过推送", flush=True)
            return False

        from sjtu_agent.feishu_client import send_post_message
        return send_post_message(open_id, post_paras)

    def send_via_wechat(self, md_digest: str) -> bool:
        """通过微信 ilink Bot 推送日报（纯文本/Markdown）。"""
        from sjtu_agent import paths as _paths
        from sjtu_agent.paths import read_json_safe
        import sys, os

        cfg = read_json_safe(_paths.CONFIG_PATH, default={})
        if not cfg.get("wechat_enabled", True):
            print("[news] 微信推送已关闭，跳过", flush=True)
            return False
        token    = cfg.get("wechat_bot_token", "")
        to_user  = cfg.get("wechat_to_user_id", "")
        ctx_tok  = cfg.get("wechat_context_token", "")
        if not token or not to_user or not ctx_tok:
            print("[news] 微信未配置（需要 wechat_bot_token / wechat_to_user_id / wechat_context_token），跳过推送", flush=True)
            return False

        # 复用 wechat_bot.py 里的 ILinkClient
        root = _paths.DATA_DIR.parent  # repo root
        sys.path.insert(0, str(root))
        try:
            from wechat_bot import ILinkClient
        except ImportError:
            # fallback: 直接用 httpx 发
            try:
                import httpx, json, base64, random, uuid
                headers = {
                    "Content-Type": "application/json",
                    "AuthorizationType": "ilink_bot_token",
                    "Authorization": f"Bearer {token}",
                    "X-WECHAT-UIN": base64.b64encode(str(random.randint(0, 0xFFFFFFFF)).encode()).decode(),
                }
                body = {
                    "base_info": {"channel_version": "1.0.3"},
                    "msg": {
                        "from_user_id": "",
                        "to_user_id": to_user,
                        "client_id": f"bot-{uuid.uuid4().hex[:12]}",
                        "message_type": 2,
                        "message_state": 2,
                        "context_token": ctx_tok,
                        "item_list": [{"type": 1, "text_item": {"text": md_digest[:4000]}}],
                    },
                }
                raw = json.dumps(body, ensure_ascii=False).encode()
                headers["Content-Length"] = str(len(raw))
                r = httpx.post("https://ilinkai.weixin.qq.com/ilink/bot/sendmessage",
                               content=raw, headers=headers, timeout=35)
                return r.status_code == 200
            except Exception as e:
                print(f"[news] 微信推送异常（fallback）: {e}", flush=True)
                return False

        client = ILinkClient(token)
        # 微信消息无硬性长度限制，但分块更稳妥（每块 2000 字）
        text = md_digest
        success = True
        while text:
            chunk = text[:2000]
            text  = text[2000:]
            try:
                client.send(chunk, to_user_id=to_user, context_token=ctx_tok)
            except Exception as e:
                print(f"[news] 微信推送异常: {e}", flush=True)
                success = False
        return success
