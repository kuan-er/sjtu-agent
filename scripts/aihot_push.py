#!/usr/bin/env python3
"""
aihot_push.py — 获取 AI HOT (aihot.virxact.com) 精选 AI 资讯，推送到飞书。

数据来源: https://aihot.virxact.com (MIT 协议，公开 API，无需 Key)
用法:
  python3 aihot_push.py           # 获取 24h 精选并推送飞书
  python3 aihot_push.py --test    # 只打印，不推送
"""

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CST = timezone(timedelta(hours=8))
_API_BASE = "https://aihot.virxact.com/api/public"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 sjtu-agent/0.2"

CATEGORY_LABELS = {
    "ai-models":   "AI 模型",
    "ai-products": "AI 产品",
    "industry":    "行业动态",
    "paper":       "论文",
    "tip":         "技巧与观点",
}


def _fetch_items(mode: str = "selected", hours: int = 24) -> list[dict]:
    """获取 AI HOT 条目。"""
    import urllib.request
    import urllib.error

    since = (datetime.now(CST) - timedelta(hours=hours)).strftime("%Y-%m-%d")
    url = f"{_API_BASE}/items?mode={mode}&since={since}&take=30"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"[aihot] API HTTP {e.code}: {e.reason}")
        return []
    except Exception as e:
        print(f"[aihot] API 请求失败: {e}")
        return []

    return data.get("items", []) if isinstance(data, dict) else []


def _fmt_time(iso_str: str) -> str:
    """ISO 时间转人类可读北京时间。"""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_cst = dt.astimezone(CST)
        now = datetime.now(CST)
        diff = now - dt_cst
        if diff < timedelta(minutes=1):
            return "刚刚"
        if diff < timedelta(hours=1):
            return f"{int(diff.total_seconds() / 60)} 分钟前"
        if diff < timedelta(hours=24):
            return f"{int(diff.total_seconds() / 3600)} 小时前"
        return dt_cst.strftime("%m-%d %H:%M")
    except Exception:
        return iso_str


def _build_markdown(items: list[dict]) -> str:
    """按分类分组，全局编号，生成 Markdown。"""
    if not items:
        return "（暂无 AI 资讯）"

    # 按分类分组
    groups: dict[str, list[dict]] = {}
    for item in items:
        cat = item.get("category", "other")
        groups.setdefault(cat, []).append(item)

    lines = ["[AI HOT] **精选资讯**", ""]
    n = 0

    for cat_key, cat_label in CATEGORY_LABELS.items():
        entries = groups.get(cat_key, [])
        if not entries:
            continue
        lines.append(f"* [{cat_label}]")
        for item in entries:
            n += 1
            title = item.get("title", "无标题")
            source = item.get("sourceUrl", "")
            ago = _fmt_time(item.get("pubDate", ""))
            summary = (item.get("summary") or "").strip()
            line = f"{n}. [{title}]({source}) · {ago}"
            if summary and len(summary) < 120:
                line += f" — {summary}"
            elif summary:
                line += f" — {summary[:117]}…"
            lines.append(line)
        lines.append("")

    lines.append(f"共 {n} 条 · 数据来自 [aihot.virxact.com](https://aihot.virxact.com)")
    return "\n".join(lines)


def _safe_print(text: str) -> None:
    """Print to Windows GBK terminal, dropping characters that can't be encoded."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))


def _html_to_post(text: str) -> list:
    """Markdown → 飞书 post 段落（复用 daily_report 的格式逻辑）。"""
    import re

    paragraphs = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        # 链接: [text](url)
        elements = []
        pos = 0
        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", para):
            prefix = para[pos:m.start()]
            if prefix:
                elements.append({"tag": "text", "text": prefix})
            elements.append({"tag": "a", "text": m.group(1), "href": m.group(2)})
            pos = m.end()
        remaining = para[pos:]
        if remaining:
            elements.append({"tag": "text", "text": remaining})
        if elements:
            paragraphs.append(elements)

    return paragraphs


def main() -> None:
    test_mode = "--test" in sys.argv

    print("[aihot] 正在获取 AI 资讯…")
    items = _fetch_items()
    if not items:
        print("[aihot] 未获取到 AI 资讯")
        return

    md = _build_markdown(items)
    if test_mode:
        _safe_print(md)
        print(f"\n[aihot] 测试模式，未推送。共 {len(items)} 条。")
        return

    post_paras = _html_to_post(md)
    from sjtu_agent.feishu_client import send_post_message

    # open_id 从 config.json 读取
    from sjtu_agent.config import cfg as _cfg
    _cfg.reload_if_changed()
    open_id = _cfg.get("feishu_open_id", "")

    if not open_id:
        print("[aihot] 未配置 feishu_open_id，跳过推送。请先在飞书 Bot 中发一条消息以记录 open_id。")
        return

    if send_post_message(open_id, post_paras):
        print(f"[aihot] 推送完成，共 {len(items)} 条")
    else:
        print("[aihot] 推送失败")


if __name__ == "__main__":
    main()
