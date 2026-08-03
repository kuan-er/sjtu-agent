#!/usr/bin/env python3
"""
email_watcher.py — 交大邮箱新邮件监控，有未读邮件时通过飞书推送通知。

纯"传话者"角色：只读取邮件摘要并通知，不发送、不删除、不修改任何邮件。

用法:
  python3 email_watcher.py            # 持续运行（每 60s 检查一次）
  python3 email_watcher.py --once     # 只检查一次，立即退出

配置（config.json）:
  feishu_app_id / feishu_app_secret / feishu_open_id — 飞书推送渠道
  JACCOUNT_USERNAME / JACCOUNT_PASSWORD — 交大邮箱凭据（.env）

安全约束:
  - 永不发送邮件（不调 SMTP）
  - 永不删除/标记已读/修改邮件状态（IMAP readonly）
"""

from __future__ import annotations

import email
import email.header
import imaplib
import json
import os
import ssl
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sjtu_agent.paths import CONFIG_PATH, DATA_DIR, ENV_PATH, atomic_write_json, read_json_safe

_STATE_PATH = DATA_DIR / "email_watcher_state.json"
_CHECK_INTERVAL = 60

_IMAP_HOST = "mail.sjtu.edu.cn"
_IMAP_PORT = 993
_BODY_PREVIEW_LEN = 200

CST = timezone(timedelta(hours=8))


def _get_creds() -> tuple[str, str]:
    username = os.environ.get("EMAIL_USERNAME", "").strip()
    password = os.environ.get("EMAIL_PASSWORD", "").strip()
    if not username:
        username = os.environ.get("JACCOUNT_USERNAME", "").strip()
        if username and "@" not in username:
            username = username + "@sjtu.edu.cn"
    if not password:
        password = os.environ.get("JACCOUNT_PASSWORD", "").strip()
    return username, password


def _decode_header(value) -> str:
    if value is None:
        return ""
    try:
        parts = email.header.decode_header(value)
        return "".join(
            (t.decode(e or "utf-8") if isinstance(t, bytes) else t)
            for t, e in parts
        )
    except Exception:
        return str(value)


def _extract_body(msg) -> str:
    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body_parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body_parts.append(payload.decode(charset, errors="replace"))
        except Exception:
            pass

    text = "\n".join(body_parts).strip()
    # 去 HTML 标签回退
    if not text:
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        import re
                        charset = part.get_content_charset() or "utf-8"
                        html = payload.decode(charset, errors="replace")
                        text = re.sub(r"<[^>]+>", "", html).strip()
                except Exception:
                    pass
    return text


def _load_state() -> dict:
    """加载持久化状态：last_uid + sent_uids 集合。"""
    s = read_json_safe(_STATE_PATH)
    return {
        "last_uid": int(s.get("last_uid", 0)),
        "sent_uids": set(int(u) for u in s.get("sent_uids", [])),
    }


def _save_state(last_uid: int, sent_uids: set) -> None:
    """持久化状态，sent_uids 截断至最近 200 条防无限增长。"""
    uids_list = sorted(sent_uids)[-200:]
    atomic_write_json(_STATE_PATH, {
        "last_uid": last_uid,
        "sent_uids": uids_list,
        "last_check": datetime.now(CST).isoformat(),
    })


def _push_feishu(text: str) -> bool:
    """通过飞书 API 向用户发送私聊消息。返回是否成功。"""
    from sjtu_agent.config import cfg as _cfg
    _cfg.reload_if_changed()
    cfg = _cfg.raw()
    open_id = cfg.get("feishu_open_id", "")
    if not open_id:
        return False

    from sjtu_agent.feishu_client import send_text_message
    return send_text_message(open_id, text)


def _check_new_emails(last_uid: int, sent_uids: set) -> list[dict]:
    """IMAP readonly 连接，拉取 UID > last_uid 且不在 sent_uids 中的新邮件。"""
    username, password = _get_creds()
    if not username or not password:
        print("[email_watcher] 凭据未配置，跳过")
        return []

    ctx = ssl.create_default_context()
    try:
        m = imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT, ssl_context=ctx, timeout=30)
        m.login(username, password)
        m.select("INBOX", readonly=True)
    except Exception as e:
        print(f"[email_watcher] IMAP 连接失败: {e}")
        return []

    try:
        # 查询 UID > last_uid 的邮件
        status, data = m.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        if status != "OK" or not data or not data[0]:
            return []

        new_uids = data[0].split()
        if not new_uids:
            return []

        new_emails = []
        # 取最新 5 封（避免积压时洪水推送）
        for uid_bytes in new_uids[-5:]:
            uid = uid_bytes.decode()
            status, msg_data = m.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = _decode_header(msg["Subject"]) or "(无主题)"
            from_addr = _decode_header(msg["From"]) or "?"
            date_str = _decode_header(msg["Date"]) or ""
            body = _extract_body(msg)
            body_preview = body[:_BODY_PREVIEW_LEN].replace("\n", " ").strip()
            if len(body) > _BODY_PREVIEW_LEN:
                body_preview += "…"
            new_emails.append({
                "uid": int(uid),
                "subject": subject,
                "from": from_addr,
                "date": date_str,
                "body_preview": body_preview,
            })

        return new_emails

    except Exception as e:
        print(f"[email_watcher] 检查邮件异常: {e}")
        return []
    finally:
        try:
            m.logout()
        except Exception:
            pass


_PUSH_COOLDOWN = 30  # 两次推送之间至少间隔 30 秒


def run_once(last_push_time: float = 0.0) -> float:
    """检查一轮新邮件，推送通知，保存状态。返回更新后的 last_push_time。"""
    state = _load_state()
    new_emails = _check_new_emails(state["last_uid"], state["sent_uids"])
    if not new_emails:
        return last_push_time

    now = time.time()
    for em in new_emails:
        uid = em["uid"]
        if uid in state["sent_uids"]:
            continue
        if last_push_time and now - last_push_time < _PUSH_COOLDOWN:
            # 冷却期内跳过，下次循环重试（不推进 last_uid）
            continue

        text = (
            f"📧 新邮件\n"
            f"发件人: {em['from']}\n"
            f"主题: {em['subject']}\n"
            f"时间: {em['date']}\n"
            f"正文预览: {em['body_preview']}"
        )
        ok = _push_feishu(text)
        if ok:
            state["sent_uids"].add(uid)
            now = time.time()
            last_push_time = now
            # 每推送成功一封立刻持久化，防止中途崩溃丢状态
            new_last = max(state["last_uid"], uid)
            _save_state(new_last, state["sent_uids"])
            state["last_uid"] = new_last
        print(f"[{datetime.now(CST):%H:%M}] 新邮件 uid={uid} {em['subject'][:30]} "
              f"推送{'OK' if ok else 'FAIL'}")

    return last_push_time


def run_loop() -> None:
    """持续轮询模式。"""
    print(f"[email_watcher] 启动，间隔 {_CHECK_INTERVAL}s")
    _check_interval = _CHECK_INTERVAL
    last_push_time = 0.0
    while True:
        try:
            last_push_time = run_once(last_push_time)
        except Exception as e:
            print(f"[email_watcher] 错误: {e}")
        time.sleep(_check_interval)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="只检查一次")
    parser.add_argument("--interval", type=int, default=_CHECK_INTERVAL,
                        help=f"轮询间隔秒数（默认 {_CHECK_INTERVAL}）")
    args = parser.parse_args()

    if args.once:
        run_once(0.0)
    else:
        _CHECK_INTERVAL = args.interval
        run_loop()
