"""Platform setup tools — Telegram, WeChat, Feishu, QQ bot configuration."""

import json
import os

from sjtu_agent.paths import CONFIG_PATH
from sjtu_agent.config import cfg as _cfg

# ── helpers ──────────────────────────────────────────────────────────────────

def _normalize_config_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [value]
        except Exception:
            return [value]
    return [value]


# ── TOOLS schema entries ─────────────────────────────────────────────────────

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "setup_telegram",
            "description": (
                "配置 Telegram Bot：将 telegram_token 和可选的 allowed_ids 保存到 config.json，"
                "然后可以用 sjtu-agent telegram-bot 启动 Bot。"
                "用户说「接入Telegram」「配置Telegram」「怎么用Telegram」「Telegram bot」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "telegram_token": {
                        "type": "string",
                        "description": "BotFather 给出的 Bot Token，格式如 1234567890:ABCdefGHI…",
                    },
                    "allowed_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "允许使用 Bot 的 Telegram user_id 列表（整数）。留空则 Bot 启动后会显示任意用户的 chat_id，可先留空再补填。",
                    },
                },
                "required": ["telegram_token"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_wechat",
            "description": (
                "配置微信 ilink Bot：打印登录二维码，让用户扫码完成微信接入，"
                "bot_token 自动保存到 config.json。"
                "用户说「接入微信」「配置微信」「微信 bot」「微信推送」时调用。"
                "注意：扫码登录必须在终端完成，此工具会打印二维码并等待用户扫码确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_feishu",
            "description": (
                "配置飞书 Bot 凭据（App ID 和 App Secret）。"
                "用户在 https://open.feishu.cn/app 创建企业自建应用，开启 Bot 能力、"
                "添加 im:message 权限、订阅 im.message.receive_v1 事件（WebSocket 模式）后，"
                "从「凭证与基础信息」页面获取 App ID 和 App Secret。"
                "用户说「接入飞书」「配置飞书」「飞书 bot」「飞书推送」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "feishu_app_id": {
                        "type": "string",
                        "description": "飞书应用的 App ID（cli_ 开头）",
                    },
                    "feishu_app_secret": {
                        "type": "string",
                        "description": "飞书应用的 App Secret",
                    },
                    "feishu_allowed_open_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "（可选）允许使用 Bot 的飞书用户 open_id 列表。留空则允许所有人。用户在飞书中给 Bot 发消息后，日志会显示其 open_id。",
                    },
                },
                "required": ["feishu_app_id", "feishu_app_secret"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_qq",
            "description": (
                "配置 QQ 官方机器人凭据（AppID + AppSecret）并保存到 config.json。"
                "调用后会尝试请求 QQ OpenAPI 校验凭据可用性。"
                "请先登录 https://q.qq.com/ ，进入机器人平台并创建机器人，再获取 AppID 与 AppSecret。"
                "如果某些字段已配置，可只传需要修改的字段；未传字段会保留原值。"
                "建议首次先不填 qq_allowed_user_ids，待目标用户给 Bot 发消息后再回填白名单。"
                "注意 qq_allowed_user_ids 填的是 QQ 用户标识（openid/id），不是 QQ 号。"
                '用户说"接入QQ""配置QQ Bot""QQ机器人"时调用。'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "qq_app_id": {
                        "type": "string",
                        "description": "QQ 机器人 AppID（在 https://q.qq.com/qqbot/openclaw/ 获取）。不传则保留当前值。",
                    },
                    "qq_app_secret": {
                        "type": "string",
                        "description": "QQ 机器人 AppSecret（在 https://q.qq.com/qqbot/openclaw/ 获取）。不传则保留当前值。",
                    },
                    "qq_allowed_user_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选白名单用户标识（openid/id，不是 QQ 号）。不传则保留当前值；传空数组 [] 表示清空白名单（允许所有用户）。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_add_user",
            "description": (
                "将一个 QQ 用户标识加入 qq_allowed_user_ids 白名单。"
                "如果没有 user_id，先提示用户：让待加入账号在 QQ 里给 Bot 发一条消息，"
                "从机器人提示/日志中拿到「QQ 用户标识」后再回填。"
                "注意这里填的是用户标识（openid/id），不是 QQ 号。"
                "用户说『增加QQ用户』『添加QQ白名单用户』时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "qq_user_id": {
                        "type": "string",
                        "description": "要加入白名单的 QQ 用户标识（openid/id，不是 QQ 号）。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_list_users",
            "description": (
                "列出当前 qq_allowed_user_ids 白名单。用户说『QQ用户列表』『查看QQ白名单』时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_remove_user",
            "description": (
                "从 qq_allowed_user_ids 删除一个用户标识。"
                "用户说『删除QQ用户』『移除QQ白名单用户』时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "qq_user_id": {
                        "type": "string",
                        "description": "要移除的 QQ 用户标识（openid/id，不是 QQ 号）。",
                    },
                },
                "required": ["qq_user_id"],
            },
        },
    },
]


# ── WeChat ───────────────────────────────────────────────────────────────────

def tool_setup_wechat() -> dict:
    try:
        import httpx as _httpx
        import io as _io
        import base64 as _b64

        _ilink_base = "https://ilinkai.weixin.qq.com"
        resp = _httpx.get(f"{_ilink_base}/ilink/bot/get_bot_qrcode?bot_type=3", timeout=15)
        data = resp.json()
        qrcode_key = data["qrcode"]
        qrcode_url = data["qrcode_img_content"]

        qr_b64 = ""
        try:
            import qrcode as _qrcode
            qr = _qrcode.QRCode(border=2)
            qr.add_data(qrcode_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            qr_b64 = _b64.b64encode(buf.getvalue()).decode()
        except Exception:
            pass

        if qr_b64:
            return {
                "success": False,
                "pending": True,
                "qr_base64": qr_b64,
                "qr_url": qrcode_url,
                "qrcode_key": qrcode_key,
                "message": "请用微信扫描上方二维码。扫码成功后会自动更新状态。",
                "ilink_base": _ilink_base,
            }

        try:
            import qrcode as _qrcode
            qr = _qrcode.QRCode(border=1)
            qr.add_data(qrcode_url)
            qr.make(fit=True)
            print("\n请用微信扫描以下二维码：\n")
            qr.print_ascii(invert=True)
        except Exception:
            print(f"\n二维码链接（可手动打开）：{qrcode_url}\n")

        import time as _time
        deadline = _time.monotonic() + 300
        while _time.monotonic() < deadline:
            try:
                status_resp = _httpx.get(
                    f"{_ilink_base}/ilink/bot/get_qrcode_status?qrcode={qrcode_key}",
                    timeout=10,
                )
                status = status_resp.json()
            except Exception:
                _time.sleep(3)
                continue

            code = status.get("code", -1)
            if code == 0:
                token      = status["bot_token"]
                account_id = status.get("account_id", "")
                user_id    = status.get("user_id", "")
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
                cfg["wechat_bot_token"]   = token
                cfg["wechat_account_id"] = account_id
                cfg["wechat_user_id"]    = user_id
                CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
                _started = _try_start_wechat_daemon()
                return {
                    "success": True,
                    "saved": True,
                    "message": "微信 Bot 登录成功！" + ("守护进程已自动启动。" if _started else "请运行 sjtu-agent wechat-bot。"),
                    "daemon_started": _started,
                }
            elif code in (1, 2):
                _time.sleep(2)
            else:
                return {"success": False, "error": f"二维码已过期（code={code}）"}

        return {"success": False, "error": "扫码超时（5分钟）"}

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "hint": "请在终端运行 python3 wechat_bot.py --login 完成扫码",
        }


def _try_start_wechat_daemon() -> bool:
    import subprocess as _sp
    try:
        uid = os.getuid()
        result = _sp.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/com.sjtu.wechat-bot"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


# ── Telegram ─────────────────────────────────────────────────────────────────

def tool_setup_telegram(telegram_token: str, allowed_ids: list | None = None) -> dict:
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": f"config.json 读取失败，已中止保存以保护现有配置: {e}"}

    cfg["telegram_token"] = telegram_token.strip()
    if allowed_ids is not None:
        cfg["telegram_allowed_ids"] = [int(i) for i in allowed_ids]

    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    token_valid: bool | None = None
    bot_info: dict = {}
    try:
        import requests as _req
        resp = _req.get(
            f"https://api.telegram.org/bot{telegram_token.strip()}/getMe",
            timeout=10,
        )
        if resp.status_code == 200:
            token_valid = True
            bot_info = resp.json().get("result", {})
        else:
            token_valid = False
    except Exception:
        token_valid = None

    result: dict = {
        "saved": True,
        "token_valid": token_valid,
        "bot_username": bot_info.get("username", ""),
        "bot_name": bot_info.get("first_name", ""),
        "allowed_ids_set": allowed_ids or [],
        "next_steps": [
            "运行 `sjtu-agent telegram-bot` 启动 Bot（长轮询模式）。",
            "在 Telegram 中发送 /id 给 Bot，可以获得自己的 user_id，然后把它添加到白名单。",
            "如果还没有 Bot Token，先在 Telegram 里找 @BotFather，发 /newbot 创建。",
        ],
    }
    if not allowed_ids:
        result["tip"] = (
            "当前白名单为空，Bot 启动后会对所有发消息的用户返回其 chat_id，"
            "方便你确认自己的 user_id 后再来用 setup_telegram 补填白名单。"
        )
    return result


# ── Feishu ───────────────────────────────────────────────────────────────────

def tool_setup_feishu(feishu_app_id: str = "", feishu_app_secret: str = "", allowed_open_ids: list | None = None) -> dict:
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": f"config.json 读取失败，已中止保存以保护现有配置: {e}"}

    if feishu_app_id:
        cfg["feishu_app_id"] = feishu_app_id.strip()
    if feishu_app_secret:
        cfg["feishu_app_secret"] = feishu_app_secret.strip()
    if allowed_open_ids is not None:
        cfg["feishu_allowed_open_ids"] = allowed_open_ids

    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    valid: bool | None = None
    app_info: dict = {}
    try:
        import requests as _req
        resp = _req.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": cfg.get("feishu_app_id", ""),
                "app_secret": cfg.get("feishu_app_secret", ""),
            },
            timeout=10,
        )
        if resp.status_code == 200:
            body = resp.json()
            if body.get("code") == 0:
                valid = True
                app_info["tenant_access_token_ok"] = True
            else:
                valid = False
                app_info["error"] = body.get("msg", f"code={body.get('code')}")
        else:
            valid = False
            app_info["error"] = f"HTTP {resp.status_code}"
    except Exception:
        valid = None

    result: dict = {
        "saved": True,
        "valid": valid,
        "allowed_open_ids_set": allowed_open_ids or [],
        "next_steps": [
            "运行 `sjtu-agent feishu-bot` 启动 Bot（WebSocket 长连接模式）。",
            "在飞书搜索你的应用名称，进入对话即可使用。",
            "需要后台常驻运行 `sjtu-agent install-daemons` 安装守护进程。",
            "如尚未创建飞书应用，前往 https://open.feishu.cn/app 创建企业自建应用。",
        ],
    }
    if not allowed_open_ids:
        result["tip"] = (
            "当前白名单为空，Bot 启动后允许所有人对话。"
            "如需限制，在飞书给 Bot 发一条消息后查看日志中的 open_id，"
            "再用 setup_feishu 补填 allowed_open_ids 白名单。"
        )
    if app_info:
        result["app_info"] = app_info
    return result


# ── QQ ───────────────────────────────────────────────────────────────────────

def tool_setup_qq(
    qq_app_id: str = "",
    qq_app_secret: str = "",
    qq_allowed_user_ids: list | None = None,
) -> dict:
    import requests as _req
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": f"config.json 读取失败，已中止保存以保护现有配置: {e}"}

    if qq_app_id:
        cfg["qq_app_id"] = str(qq_app_id).strip()
    if qq_app_secret:
        cfg["qq_app_secret"] = str(qq_app_secret).strip()
    if qq_allowed_user_ids is not None:
        cfg["qq_allowed_user_ids"] = [str(x).strip() for x in qq_allowed_user_ids if str(x).strip()]

    effective_app_id = str(cfg.get("qq_app_id", "")).strip()
    effective_app_secret = str(cfg.get("qq_app_secret", "")).strip()
    if not effective_app_id or not effective_app_secret:
        return {
            "saved": False,
            "error": "qq_app_id 和 qq_app_secret 仍不完整，请补全后重试。",
            "current_state": {
                "qq_app_id_set": bool(effective_app_id),
                "qq_app_secret_set": bool(effective_app_secret),
                "qq_allowed_user_ids_count": len(cfg.get("qq_allowed_user_ids", []) or []),
            },
            "next_action": "请补充缺失字段；已存在字段可不传以保留原值。",
        }

    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    valid: bool | None = None
    details: dict = {}
    try:
        resp = _req.post(
            "https://bots.qq.com/app/getAppAccessToken",
            json={
                "appId": effective_app_id,
                "clientSecret": effective_app_secret,
            },
            timeout=10,
        )
        body = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
        if resp.status_code == 200 and body.get("access_token"):
            valid = True
            details = {"expires_in": body.get("expires_in")}
        else:
            valid = False
            details = {"http_status": resp.status_code, "response": body or resp.text[:300]}
    except Exception as e:
        valid = None
        details = {"error": str(e)}

    result = {
        "saved": True,
        "valid": valid,
        "details": details,
        "app_id_set": bool(effective_app_id),
        "app_secret_set": bool(effective_app_secret),
        "allowed_user_ids_set": cfg.get("qq_allowed_user_ids", []),
        "next_steps": [
            "请让要加入白名单的 QQ 账号给 Bot 发送一条消息，获取「QQ 用户标识」。",
            "把该用户标识回填给我（可直接调用 qq_add_user 或 setup_qq 填 qq_allowed_user_ids）。",
        ],
    }
    allowed_ids = cfg.get("qq_allowed_user_ids", []) or []
    if not allowed_ids:
        result["tip"] = (
            "当前白名单为空，Bot 启动后允许所有人对话。"
            "如需限制：先让目标用户给 Bot 发一条消息，获取其「QQ 用户标识」，"
            "再用 setup_qq 补填 qq_allowed_user_ids。"
            "注意这里填的是用户标识（openid/id），不是 QQ 号。"
        )
    else:
        result["tip"] = (
            f"当前已设置 {len(allowed_ids)} 个白名单用户标识。"
            "仅列表内用户可用 Bot。"
            "若需调整，请再次调用 setup_qq 更新 qq_allowed_user_ids。"
        )
    return result


def _load_cfg_for_qq_users() -> dict:
    _cfg.reload_if_changed()
    return _cfg.raw()


def _save_cfg_for_qq_users(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalized_qq_user_list(raw: list | None) -> list[str]:
    values = raw or []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        user_id = str(item).strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        out.append(user_id)
    return out


def tool_qq_add_user(qq_user_id: str = "") -> dict:
    user_id = str(qq_user_id).strip()
    if not user_id:
        return {
            "saved": False,
            "action_required": True,
            "message": "请先让要添加的 QQ 账号给 Bot 发一条消息。",
            "next_action": (
                "用户会在机器人提示或日志里看到「QQ 用户标识」。"
                "把该标识回填给我后，我再调用 qq_add_user 完成添加。"
            ),
            "note": "这里需要的是用户标识（openid/id），不是 QQ 号。",
        }

    cfg = _load_cfg_for_qq_users()
    existing = _normalized_qq_user_list(cfg.get("qq_allowed_user_ids", []))
    if user_id in existing:
        return {
            "saved": True,
            "added": False,
            "message": "该用户标识已在白名单中。",
            "qq_allowed_user_ids": existing,
            "count": len(existing),
        }

    existing.append(user_id)
    cfg["qq_allowed_user_ids"] = existing
    _save_cfg_for_qq_users(cfg)
    return {
        "saved": True,
        "added": True,
        "qq_allowed_user_ids": existing,
        "count": len(existing),
        "next_steps": [
            "已加入 QQ 白名单。",
            "请重启 `sjtu-agent qq-bot` 使白名单变更生效。",
        ],
    }


def tool_qq_list_users() -> dict:
    cfg = _load_cfg_for_qq_users()
    users = _normalized_qq_user_list(cfg.get("qq_allowed_user_ids", []))
    return {
        "qq_allowed_user_ids": users,
        "count": len(users),
        "allow_all": len(users) == 0,
        "tip": (
            "白名单为空时表示允许所有用户。"
            if not users
            else "仅列表内用户可使用 QQ Bot。"
        ),
    }


def tool_qq_remove_user(qq_user_id: str) -> dict:
    user_id = str(qq_user_id).strip()
    if not user_id:
        return {"saved": False, "error": "qq_user_id 不能为空。"}

    cfg = _load_cfg_for_qq_users()
    existing = _normalized_qq_user_list(cfg.get("qq_allowed_user_ids", []))
    if user_id not in existing:
        return {
            "saved": True,
            "removed": False,
            "message": "该用户标识不在白名单中。",
            "qq_allowed_user_ids": existing,
            "count": len(existing),
        }

    kept = [item for item in existing if item != user_id]
    cfg["qq_allowed_user_ids"] = kept
    _save_cfg_for_qq_users(cfg)
    return {
        "saved": True,
        "removed": True,
        "qq_allowed_user_ids": kept,
        "count": len(kept),
        "next_steps": [
            "已从 QQ 白名单移除。",
            "请重启 `sjtu-agent qq-bot` 使白名单变更生效。",
        ],
    }
