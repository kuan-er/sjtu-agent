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
                        body_parts.append(payload.decode("utf-8", errors="replace"))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                body_parts.append(payload.decode("utf-8", errors="replace"))
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
                        html = payload.decode("utf-8", errors="replace")
                        text = re.sub(r"<[^>]+>", "", html).strip()
                except Exception:
                    pass
    return text


def _push_feishu(text: str) -> bool:
    """通过飞书 API 向用户发送私聊消息。返回是否成功。"""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False
    app_id = cfg.get("feishu_app_id", "")
    app_secret = cfg.get("feishu_app_secret", "")
    open_id = cfg.get("feishu_open_id", "")
    if not app_id or not app_secret or not open_id:
        return False

    import requests
    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret}, timeout=10,
        )
        if r.status_code != 200 or r.json().get("code") != 0:
            return False
        token = r.json()["tenant_access_token"]
    except Exception:
        return False

    body = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}"},
            json=body, timeout=15,
        )
        return r.status_code == 200 and r.json().get("code") == 0
    except Exception:
        return False


def _check_new_emails() -> list[dict]:
    """IMAP readonly 连接，拉取上次最大 UID 之后的新邮件。返回新邮件列表。"""
    username, password = _get_creds()
    if not username or not password:
        print("[email_watcher] 凭据未配置，跳过")
        return []

    state = read_json_safe(_STATE_PATH)
    last_uid = int(state.get("last_uid", 0))

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


def run_once() -> None:
    """检查一轮新邮件，推送通知，保存状态。"""
    new_emails = _check_new_emails()
    if not new_emails:
        print(f"[{datetime.now(CST):%H:%M}] 无新邮件")
        return

    max_uid = new_emails[-1]["uid"]
    for em in new_emails:
        text = (
            f"📧 新邮件\n"
            f"发件人: {em['from']}\n"
            f"主题: {em['subject']}\n"
            f"时间: {em['date']}\n"
            f"正文预览: {em['body_preview']}"
        )
        ok = _push_feishu(text)
        print(f"[{datetime.now(CST):%H:%M}] 新邮件 uid={em['uid']} {em['subject'][:30]} "
              f"推送{'OK' if ok else 'FAIL'}")

    atomic_write_json(_STATE_PATH, {"last_uid": max_uid, "last_check": datetime.now(CST).isoformat()})


def run_loop() -> None:
    """持续轮询模式。"""
    print(f"[email_watcher] 启动，间隔 {_CHECK_INTERVAL}s")
    _check_interval = _CHECK_INTERVAL
    while True:
        try:
            run_once()
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
        run_once()
    else:
        _CHECK_INTERVAL = args.interval
        run_loop()
