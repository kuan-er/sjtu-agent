"""Shared Feishu API client — used by feishu_bot, daily_report, and email_watcher.

Provides cached tenant_access_token retrieval and message sending, so the
three callers no longer duplicate the auth + POST logic.
"""

import json
import threading
import time

import requests

from sjtu_agent.paths import CONFIG_PATH
from sjtu_agent.config import cfg as _cfg

_TENANT_TOKEN: str = ""
_TENANT_TOKEN_EXPIRES_AT: float = 0.0
_TOKEN_LOCK = threading.Lock()


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """Return a cached tenant_access_token, refreshing if expired.

    Raises RuntimeError when the Feishu API returns an error code.
    """
    global _TENANT_TOKEN, _TENANT_TOKEN_EXPIRES_AT
    now = time.time()
    with _TOKEN_LOCK:
        if _TENANT_TOKEN and now < _TENANT_TOKEN_EXPIRES_AT - 30:
            return _TENANT_TOKEN

        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise RuntimeError(f"获取 tenant_access_token 失败: {data or resp.text[:200]}")

        _TENANT_TOKEN = data["tenant_access_token"]
        expires_in = int(data.get("expire", 7200) or 7200)
        _TENANT_TOKEN_EXPIRES_AT = now + max(60, expires_in)
        return _TENANT_TOKEN


def _load_feishu_config() -> dict | None:
    _cfg.reload_if_changed()
    cfg = _cfg.raw()
    if not cfg.get("feishu_app_id") or not cfg.get("feishu_app_secret"):
        return None
    return cfg


def send_text_message(open_id: str, text: str) -> bool:
    """Send a text message to a Feishu user via the REST API.  Returns True on success."""
    cfg = _load_feishu_config()
    if not cfg:
        return False
    try:
        token = get_tenant_access_token(cfg["feishu_app_id"], cfg["feishu_app_secret"])
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": open_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            timeout=15,
        )
        return resp.status_code == 200 and resp.json().get("code") == 0
    except Exception:
        return False


def send_post_message(open_id: str, post_paras: list) -> bool:
    """Send a post-format message, chunking if needed.  Returns True on success."""
    cfg = _load_feishu_config()
    if not cfg:
        return False
    try:
        token = get_tenant_access_token(cfg["feishu_app_id"], cfg["feishu_app_secret"])
        para_chunks = [post_paras[i:i + 25] for i in range(0, len(post_paras), 25)]
        for chunk in para_chunks:
            content = {"zh_cn": {"title": "", "content": chunk}}
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": open_id,
                    "msg_type": "post",
                    "content": json.dumps(content, ensure_ascii=False),
                },
                timeout=15,
            )
            if resp.status_code != 200 or resp.json().get("code") != 0:
                return False
        return True
    except Exception:
        return False
