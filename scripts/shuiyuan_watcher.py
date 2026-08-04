#!/usr/bin/env python3
"""
水源社区帖子新回复监控脚本
监控：[SJTU-Agent] 你专属的交大生活智能体
https://shuiyuan.sjtu.edu.cn/t/topic/471260

配置（写到 config.json 中）：
  "shuiyuan_watcher": {
    "topic_id": 471260,
    "cookie": "...",                    // shuiyuan 登录 Cookie（建议直接 _forum_session=...; _t=...）
    "telegram_chat_id": 123456,          // 收件人；不填则尝试用 config.telegram_allowed_ids[0]
    "check_interval_seconds": 120
  }
复用 config.telegram_token，无需重复填写。
"""

import sys
import json
import os
import time
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sjtu_agent.paths import (
    CONFIG_PATH,
    DATA_DIR,
    atomic_write_json,
    read_json_safe,
)
from sjtu_agent.logging import get_logger

_logger = get_logger("shuiyuan_watcher")

CST = timezone(timedelta(hours=8))
STATE_FILE = DATA_DIR / ".shuiyuan_watcher_state.json"


def _load_cfg() -> dict:
    cfg = read_json_safe(CONFIG_PATH, default={})
    sub = cfg.get("shuiyuan_watcher", {}) or {}
    return {
        "topic_id": int(sub.get("topic_id") or 471260),
        "cookie": (sub.get("cookie") or "").strip(),
        "telegram_token": (cfg.get("telegram_token") or "").strip(),
        "telegram_chat_id": (
            sub.get("telegram_chat_id")
            or (cfg.get("telegram_allowed_ids") or [None])[0]
        ),
        "check_interval": int(sub.get("check_interval_seconds") or 120),
    }


def get_topic_info(topic_id: int, cookie: str):
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
    r = requests.get(f"https://shuiyuan.sjtu.edu.cn/t/{topic_id}.json", headers=headers, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    posts = data.get("post_stream", {}).get("posts", [])
    last = posts[-1] if posts else {}
    return {
        "posts_count": data.get("posts_count", 0),
        "last_post_number": last.get("post_number", 0),
        "last_post_username": last.get("username", ""),
        "last_post_cooked": last.get("cooked", ""),
    }


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:150]


def send_telegram(message: str, token: str, chat_id) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": chat_id, "text": message,
              "parse_mode": "Markdown", "disable_web_page_preview": False},
        timeout=10,
    )
    return r.status_code == 200


def load_state() -> dict:
    return read_json_safe(STATE_FILE, default={"posts_count": 0, "last_post_number": 0})


def save_state(state: dict) -> None:
    """原子写入，避免崩溃后状态丢失导致历史回复被全量补发。"""
    atomic_write_json(STATE_FILE, state)


def _ts() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def main():
    cfg = _load_cfg()
    if not cfg["cookie"] or not cfg["telegram_token"] or not cfg["telegram_chat_id"]:
        _logger.warning(
            "[shuiyuan_watcher] 缺少配置：请在 config.json 设置 shuiyuan_watcher.cookie / telegram_token / telegram_allowed_ids"
        )
        return

    topic_id = cfg["topic_id"]
    topic_url = f"https://shuiyuan.sjtu.edu.cn/t/topic/{topic_id}"
    interval = cfg["check_interval"]

    state = load_state()
    _logger.info(f"[{_ts()}] 开始监控（topic={topic_id}），当前 {state['posts_count']} 楼")

    while True:
        try:
            info = get_topic_info(topic_id, cfg["cookie"])
            if info and info["posts_count"] > state["posts_count"]:
                excerpt = strip_html(info["last_post_cooked"])
                post_link = topic_url + "/" + str(info["last_post_number"])
                msg = (
                    "💬 *水源帖子有新回复！*\n\n"
                    "*[SJTU\\-Agent] 你专属的交大生活智能体*\n"
                    f"👤 @{info['last_post_username']} 刚刚在第 {info['last_post_number']} 楼回复\n\n"
                    f"📝 *内容预览：*\n{excerpt}\n\n"
                    f"🔗 [点击查看]({post_link})"
                )
                # 先写状态再发送：宁可漏发一次也不要重发整批
                new_state = {
                    "posts_count": info["posts_count"],
                    "last_post_number": info["last_post_number"],
                }
                save_state(new_state)
                state = new_state
                if send_telegram(msg, cfg["telegram_token"], cfg["telegram_chat_id"]):
                    _logger.info(
                        f"[{_ts()}] 通知已发送！第{info['last_post_number']}楼 by {info['last_post_username']}"
                    )
            else:
                _logger.info(f"[{_ts()}] 无新回复，当前 {state['posts_count']} 楼")
        except Exception as e:
            _logger.warning(f"[{_ts()}] 出错: {e}")
        time.sleep(interval)

if __name__ == "__main__":
    main()