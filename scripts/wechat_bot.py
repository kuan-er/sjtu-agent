#!/usr/bin/env python3
"""
wechat_bot.py — 通过微信 ilink AI Bot 平台将 agent.py 接入微信

基于 Smoky 逆向的 ilink 协议：
  https://medium.com/@gymayong/我逆向了腾讯微信-ilink-协议...

工作原理：
  1. 首次运行：调用 ilink 接口获取登录二维码，用微信扫码
  2. 扫码后获得 bot_token，持久化到 config.json
  3. 长轮询 getupdates 接口接收消息
  4. 把消息交给 agent.py 处理，把回复通过 sendmessage 发回
  5. 可主动调用 WeChatBot.push(text) 发送消息（如日报推送）

注意事项（来自文章踩坑总结）：
  - 需要用户至少先给 bot 发一条消息，才能获取 context_token
  - context_token 必须持久化保存，每次 getupdates 都会刷新它
  - sendmessage 成功时响应体是 {}，不代表失败
  - 不支持群消息，只能 1 对 1

用法：
  python3 wechat_bot.py          # 正常运行（长轮询）
  python3 wechat_bot.py --login  # 强制重新扫码登录
  python3 wechat_bot.py --push "消息内容"  # 主动推送一条消息后退出
  python3 wechat_bot.py --test   # 测试 token 连通性

配置（config.json）:
  wechat_bot_token     : 扫码后自动保存，无需手动填写
  wechat_context_token : 从用户消息中自动获取，无需手动填写
  wechat_to_user_id    : 接收推送的用户 ilink_user_id（首次收到消息后自动保存）
"""

import argparse
import base64
import io
import json
import logging
import os
import random
import re
import sys
import threading
import time
import tempfile
import uuid
import datetime as _dt
from pathlib import Path

import httpx
import qrcode  # pip install qrcode[pil]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sjtu_agent.paths import CONFIG_PATH

import agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("wechat_bot")

# ── ilink 接口常量 ─────────────────────────────────────────────────────────────

ILINK_BASE = "https://ilinkai.weixin.qq.com"

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mKABCDEFGHJKST]')
_TMP_DIR = Path(tempfile.mkdtemp(prefix="sjtu_wechat_"))


# ── ilink HTTP 客户端 ──────────────────────────────────────────────────────────

class ILinkClient:
    """封装 ilink 协议的 HTTP 请求层。"""

    def __init__(self, token: str):
        self.token = token
        self._cursor: str = ""  # getupdates 的游标

    # ---- 辅助 ----------------------------------------------------------------

    def _headers(self) -> dict:
        """生成每次请求的随机 UIN 头。"""
        uin = base64.b64encode(str(random.randint(0, 0xFFFFFFFF)).encode()).decode()
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.token}",
            "X-WECHAT-UIN": uin,
        }

    def _post(self, endpoint: str, body: dict) -> dict:
        """POST 到 ilink bot 接口，自动注入 base_info 和 Content-Length。"""
        body["base_info"] = {"channel_version": "1.0.3"}
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = self._headers()
        headers["Content-Length"] = str(len(raw))
        resp = httpx.post(
            f"{ILINK_BASE}/ilink/bot/{endpoint}",
            content=raw,
            headers=headers,
            timeout=35,
        )
        text = resp.text.strip()
        if text and text != "{}":
            return resp.json()
        return {"ret": 0}

    # ---- 消息接收 -------------------------------------------------------------

    def get_updates(self) -> list[dict]:
        """长轮询拉取新消息，自动更新游标，返回消息列表。"""
        result = self._post("getupdates", {"get_updates_buf": self._cursor})
        self._cursor = result.get("get_updates_buf", self._cursor)
        msgs = result.get("msgs", [])
        return msgs if isinstance(msgs, list) else []

    # ---- 消息发送 -------------------------------------------------------------

    def send(self, text: str, to_user_id: str = "", context_token: str = "") -> dict:
        """发送文本消息。必须有 context_token。"""
        return self._post("sendmessage", {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": f"bot-{uuid.uuid4().hex[:12]}",
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            }
        })

    def send_typing(self, context_token: str) -> None:
        """发送"正在输入"状态。"""
        try:
            cfg = _load_cfg()
            ticket = cfg.get("wechat_typing_ticket", "")
            if not ticket:
                # 获取 typing ticket
                r = self._post("getconfig", {})
                ticket = r.get("typing_ticket", "")
            if ticket:
                self._post("sendtyping", {
                    "context_token": context_token,
                    "typing_ticket": ticket,
                })
        except Exception:
            pass  # typing 失败不影响主流程


# ── 登录流程 ──────────────────────────────────────────────────────────────────

def do_login() -> tuple[str, str, str]:
    """
    执行扫码登录流程，返回 (bot_token, account_id, user_id)。
    在终端打印二维码，等待用户扫码确认。
    """
    print("\n正在获取微信登录二维码…")
    resp = httpx.get(f"{ILINK_BASE}/ilink/bot/get_bot_qrcode?bot_type=3", timeout=15)
    print(f"[debug] get_bot_qrcode HTTP {resp.status_code}")
    print(f"[debug] get_bot_qrcode headers: {dict(resp.headers)}")
    print(f"[debug] get_bot_qrcode body: {resp.text}")
    resp.raise_for_status()
    data = resp.json()
    qrcode_key = data["qrcode"]
    qrcode_url = data["qrcode_img_content"]
    print(f"[debug] qrcode key: {qrcode_key}")
    print(f"[debug] qrcode url: {qrcode_url}")

    # 在终端打印 ASCII 二维码
    qr = qrcode.QRCode(border=1)
    qr.add_data(qrcode_url)
    qr.make(fit=True)
    print("\n请用微信扫描以下二维码（微信 → 搜一搜 → 扫一扫，或首页右上角+）：\n")
    qr.print_ascii(invert=True)
    print(f"\n二维码链接（可手动打开）：{qrcode_url}\n")

    print("等待扫码确认…")
    poll_count = 0
    last_status = None
    while True:
        poll_count += 1
        try:
            status_resp = httpx.get(
                f"{ILINK_BASE}/ilink/bot/get_qrcode_status?qrcode={qrcode_key}",
                headers={"iLink-App-ClientVersion": "1"},
                timeout=40,
            )
            try:
                status = status_resp.json()
            except Exception:
                print(f"[debug] poll #{poll_count} HTTP {status_resp.status_code} non-JSON body: {status_resp.text!r}")
                time.sleep(2)
                continue
        except httpx.ReadTimeout:
            # 正常行为，继续轮询
            continue
        except Exception as e:
            logger.warning(f"轮询扫码状态出错（继续重试）：{e}")
            time.sleep(2)
            continue

        s = status.get("status", "")
        # 只在状态变化或异常时打印完整 body，避免刷屏
        if s != last_status or s not in ("waiting", "scaned"):
            print(f"[debug] poll #{poll_count} HTTP {status_resp.status_code} status={s!r} full body: {status}")
            last_status = s

        if s == "scaned":
            print("✅ 已扫码，请在手机上确认登录…")
        elif s == "confirmed":
            bot_token  = status["bot_token"]
            account_id = status.get("ilink_bot_id", "")
            user_id    = status.get("ilink_user_id", "")
            print(f"\n✅ 登录成功！")
            return bot_token, account_id, user_id
        elif s == "expired":
            raise RuntimeError("二维码已过期，请重新运行")
        time.sleep(2)


# ── 配置读写 ──────────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _save_cfg(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_wechat_fields(**kwargs) -> None:
    """更新 config.json 中的微信相关字段。"""
    cfg = _load_cfg()
    cfg.update(kwargs)
    _save_cfg(cfg)


# ── 会话管理 ──────────────────────────────────────────────────────────────────

_sess: dict = {}
_sess_lock = threading.Lock()


def _get_or_create_session() -> dict:
    with _sess_lock:
        if not _sess:
            agent_cfg = agent.load_agent_config()
            _sess.update({
                "messages":   [],
                "model_box":  [agent_cfg["model"]],
                "client_box": [agent._make_client(agent_cfg)],
            })
    return _sess


def _build_date_ctx() -> str:
    now   = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    year  = now.year
    month = now.month
    if month >= 9:
        cur_xnm, cur_xqm   = year,     "1"
        prev_xnm, prev_xqm = year - 1, "2"
    elif month <= 6:
        cur_xnm, cur_xqm   = year - 1, "2"
        prev_xnm, prev_xqm = year - 1, "1"
    else:
        cur_xnm, cur_xqm   = year - 1, "3"
        prev_xnm, prev_xqm = year - 1, "2"
    return (
        f"\n\n## 当前时间（每轮自动刷新）\n"
        f"现在：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[now.weekday()]}。\n"
        f"当前学期：{cur_xnm}-{cur_xnm+1}学年第{cur_xqm}学期。\n"
        f"「上学期」={prev_xnm}-{prev_xnm+1}学年第{prev_xqm}学期"
        f"（query_grades: year='{prev_xnm}', semester='{prev_xqm}'）。\n"
        f"「本学期」={cur_xnm}-{cur_xnm+1}学年第{cur_xqm}学期"
        f"（query_grades: year='{cur_xnm}', semester='{cur_xqm}'）。"
    )


def _init_messages(sess: dict) -> None:
    if sess["messages"]:
        return
    sess["messages"].append({
        "role": "system",
        "content": agent.SYSTEM_PROMPT + _build_date_ctx(),
    })


def _capture_turn(sess: dict, user_text: str) -> str:
    """运行一轮对话，返回 Agent 回复文本。"""
    _init_messages(sess)
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        sess["messages"][0]["content"] = agent.SYSTEM_PROMPT + _build_date_ctx()
    sess["messages"].append({"role": "user", "content": user_text})

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        agent._run_one_turn(
            sess["client_box"][0],
            sess["model_box"][0],
            sess["messages"],
        )
    finally:
        sys.stdout = old_stdout

    raw   = buf.getvalue()
    clean = _ANSI_RE.sub("", raw)
    marker = "Agent: "
    idx = clean.rfind(marker)
    if idx == -1:
        for m in reversed(sess["messages"]):
            if m.get("role") == "assistant":
                content = m.get("content", "")
                if isinstance(content, str):
                    return content.strip() or "(已完成)"
                elif isinstance(content, list):
                    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip() or "(已完成)"
        return "(已完成)"
    return clean[idx + len(marker):].strip()


def _model_supports_vision(model: str) -> bool:
    m = (model or "").lower()
    return any(kw in m for kw in [
        "vision", "gpt-4o", "gpt-4-turbo", "claude-3", "claude-4",
        "gemini", "qwen-vl", "qwen3vl", "glm-4v", "internvl",
        "sonnet-4", "opus-4", "haiku-4",
    ])


def _capture_turn_multimodal(sess: dict, content: list) -> str:
    _init_messages(sess)
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        sess["messages"][0]["content"] = agent.SYSTEM_PROMPT + _build_date_ctx()
    sess["messages"].append({"role": "user", "content": content})

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        agent._run_one_turn(
            sess["client_box"][0],
            sess["model_box"][0],
            sess["messages"],
        )
    finally:
        sys.stdout = old_stdout

    clean = _ANSI_RE.sub("", buf.getvalue())
    marker = "Agent: "
    idx = clean.rfind(marker)
    if idx == -1:
        for m in reversed(sess["messages"]):
            if m.get("role") == "assistant":
                c = m.get("content", "")
                if isinstance(c, str):
                    return c.strip() or "(已完成)"
                if isinstance(c, list):
                    texts = [b.get("text", "") for b in c if b.get("type") == "text"]
                    return "\n".join(texts).strip() or "(已完成)"
        return "(已完成)"
    return clean[idx + len(marker):].strip()


def _guess_suffix_from_url(url: str, default: str = ".bin") -> str:
    path = url.split("?")[0]
    suffix = Path(path).suffix.lower()
    return suffix or default


def _guess_suffix_from_content_type(content_type: str, media_type: str = "file") -> str:
    ct = (content_type or "").lower()
    if "jpeg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    if "pdf" in ct:
        return ".pdf"
    if "mpeg" in ct:
        return ".mp3"
    if "wav" in ct:
        return ".wav"
    if "mp4" in ct:
        return ".mp4"
    if "ogg" in ct:
        return ".ogg"
    if "plain" in ct:
        return ".txt"
    if media_type == "audio":
        return ".m4a"
    if media_type == "image":
        return ".jpg"
    return ".bin"


def _download_media(url: str, media_type: str = "file", filename: str = "") -> Path:
    if not url:
        raise RuntimeError("附件下载地址为空")
    resp = httpx.get(url, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    name = (filename or "").strip()
    if not name:
        suffix = _guess_suffix_from_url(url, default="")
        if not suffix:
            suffix = _guess_suffix_from_content_type(resp.headers.get("content-type", ""), media_type=media_type)
        name = f"wechat_media_{int(time.time())}{suffix}"
    save_path = _TMP_DIR / name
    save_path.write_bytes(resp.content)
    return save_path


def _extract_url_from_media_blob(blob: dict) -> str:
    if not isinstance(blob, dict):
        return ""
    # Doc-first: OpenClaw/iLink official wrappers may expose these direct URL fields.
    for key in ("url", "download_url", "file_url"):
        val = blob.get(key)
        if isinstance(val, str) and val.startswith(("http://", "https://")):
            return val
    # Common nested key in iLink message schema.
    media = blob.get("media")
    if isinstance(media, dict):
        for key in ("url", "download_url", "file_url"):
            val = media.get(key)
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                return val
    # Compatibility fallback for non-standard gateways.
    for val in blob.values():
        if isinstance(val, str) and val.startswith(("http://", "https://")):
            return val
        if isinstance(val, dict):
            nested = _extract_url_from_media_blob(val)
            if nested:
                return nested
    return ""


def _extract_message_payload(item_list: list[dict]) -> tuple[str, dict | None]:
    text = ""
    media: dict | None = None
    for item in item_list:
        try:
            itype = int(item.get("type"))
        except Exception:
            itype = 0
        if itype == 1 and not text:
            text = item.get("text_item", {}).get("text", "").strip()
            continue
        if media is not None:
            continue

        # Official item mapping:
        # 2=image_item, 3=voice_item, 4=file_item, 5=video_item
        if itype == 2:
            blob = item.get("image_item", {}) if isinstance(item.get("image_item", {}), dict) else {}
            media = {
                "type": "image",
                "url": _extract_url_from_media_blob(blob),
                "filename": str(blob.get("file_name", "") or blob.get("name", "") or "").strip(),
                "stt_text": "",
            }
            continue

        if itype == 3:
            blob = item.get("voice_item", {}) if isinstance(item.get("voice_item", {}), dict) else {}
            stt_text = str(blob.get("text", "") or "").strip()
            media = {
                "type": "audio",
                "url": _extract_url_from_media_blob(blob),
                "filename": str(blob.get("file_name", "") or blob.get("name", "") or "").strip(),
                "stt_text": stt_text,
            }
            continue

        if itype == 4:
            blob = item.get("file_item", {}) if isinstance(item.get("file_item", {}), dict) else {}
            media = {
                "type": "file",
                "url": _extract_url_from_media_blob(blob),
                "filename": str(blob.get("file_name", "") or blob.get("name", "") or "").strip(),
                "stt_text": "",
            }
            continue

        if itype == 5:
            blob = item.get("video_item", {}) if isinstance(item.get("video_item", {}), dict) else {}
            media = {
                "type": "video",
                "url": _extract_url_from_media_blob(blob),
                "filename": str(blob.get("file_name", "") or blob.get("name", "") or "").strip(),
                "stt_text": "",
            }
            continue

    return text, media


def _build_parser_context(local_path: Path, media_type: str = "file", max_chars: int = 3000) -> tuple[str, str]:
    def _infer_backend_missing(parse_result: dict) -> str:
        text = " ".join(
            [
                str((parse_result or {}).get("error", "") or ""),
                str((parse_result or {}).get("content", "") or ""),
                " ".join(str(x) for x in ((parse_result or {}).get("warnings") or [])),
            ]
        ).lower()
        if "pdf ocr backend missing" in text or "pypdfium2" in text:
            return "pdf_ocr"
        if "whisper backend is not installed" in text or "asr backend missing" in text:
            return "whisper"
        if "paddleocr backend is not installed" in text or "ocr backend missing" in text or "ppt ocr backend missing" in text:
            return "paddleocr"
        return ""

    try:
        strategy = "auto"
        if media_type == "image":
            strategy = "paddleocr"
        elif media_type == "audio":
            strategy = "whisper"
        parse_result = agent.tool_parse_local_file(
            str(local_path),
            max_chars=4000,
            start_page=1,
            strategy=strategy,
        )
        if not (parse_result or {}).get("ok") and strategy in {"paddleocr", "whisper"}:
            parse_result = agent.tool_parse_local_file(
                str(local_path),
                max_chars=4000,
                start_page=1,
                strategy="auto",
            )
        extracted = (parse_result or {}).get("content", "")
        if extracted:
            parser_name = parse_result.get("parser", "unknown")
            return (
                f"\n\n以下是附件提取的文字内容（parser={parser_name}）：\n```\n{extracted[:max_chars]}\n```",
                "",
            )
        err = (parse_result or {}).get("error", "")
        warnings = (parse_result or {}).get("warnings") or []
        backend = _infer_backend_missing(parse_result or {})
        if backend:
            warn_text = "；".join(str(x) for x in warnings if x)
            return (
                (
                    "\n\n[附件解析状态]\n"
                    f"{warn_text or err or '检测到 OCR/ASR 解析模块缺失'}\n"
                    f"建议：先询问用户是否安装解析模块；若用户同意，再调用 install_parse_backend(backend='{backend}') 安装后重试解析。"
                ),
                "",
            )
        return "", (err or ("；".join(str(x) for x in warnings if x) or "解析结果为空"))
    except Exception as ex:
        return "", str(ex)


# ── 消息处理 ──────────────────────────────────────────────────────────────────

_reply_lock = threading.Lock()


def handle_message(client: ILinkClient, msg: dict) -> None:
    """处理一条收到的微信消息，调用 agent 并回复。"""
    cfg = _load_cfg()
    if not cfg.get("wechat_enabled", True):
        return

    ctx_token   = msg.get("context_token", "")
    from_user   = msg.get("from_user_id", "")
    item_list   = msg.get("item_list", [])

    text, media = _extract_message_payload(item_list)
    if not ctx_token or (not text and media is None):
        return

    # 保存 context_token 和 to_user_id（供主动推送使用）
    _save_wechat_fields(
        wechat_context_token=ctx_token,
        wechat_to_user_id=from_user,
    )

    if text:
        logger.info(f"收到消息 from={from_user[:8]}…：{text[:50]}")
    else:
        logger.info(f"收到媒体消息 from={from_user[:8]}…：{(media or {}).get('type', 'unknown')}")

    # 发送"正在输入"状态
    client.send_typing(ctx_token)

    # 调用 agent 处理
    if _reply_lock.locked():
        client.send("⏳ 正在处理上一条消息，请稍候…", to_user_id=from_user, context_token=ctx_token)
        return

    with _reply_lock:
        try:
            sess  = _get_or_create_session()
            model = sess["model_box"][0]
            reply = ""
            if media is not None:
                media_type = media.get("type", "file")
                media_url = media.get("url", "")
                stt_text = str(media.get("stt_text", "") or "").strip()

                # Voice item may include platform STT text even when no direct media URL is exposed.
                if media_type == "audio" and not media_url and stt_text:
                    prompt = (
                        "[用户发送了语音消息，平台转写如下]\n"
                        f"{stt_text}\n\n"
                        "请根据这段转写内容回答用户；若信息不足，再追问。"
                    )
                    if text:
                        prompt += f"\n\n用户补充：{text}"
                    reply = _capture_turn(sess, prompt)
                    _send_chunks(client, reply, from_user, ctx_token)
                    return

                if not media_url:
                    raise RuntimeError("该媒体消息未提供可下载 URL（可能仅包含加密 CDN 引用）")

                local_path = _download_media(
                    media_url,
                    media_type=media_type,
                    filename=media.get("filename", ""),
                )
                if media_type == "image" and _model_supports_vision(model):
                    img_bytes = local_path.read_bytes()
                    b64 = base64.b64encode(img_bytes).decode()
                    content: list = []
                    if text:
                        content.append({"type": "text", "text": text})
                    else:
                        content.append({"type": "text", "text": "用户发送了一张图片，请先描述图片内容，再回答用户问题。"})
                    if agent._is_anthropic_model(model):
                        content.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                        })
                    else:
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        })
                    reply = _capture_turn_multimodal(sess, content)
                else:
                    parsed_ctx, parse_err = _build_parser_context(local_path, media_type=media.get("type", "file"))
                    user_text = (
                        f"[用户通过微信发送了附件]\n"
                        f"  类型：{media.get('type', 'file')}\n"
                        f"  文件名：{local_path.name}\n"
                        f"  本地路径：{local_path}\n"
                        f"  文件大小：{local_path.stat().st_size // 1024} KB"
                    )
                    if parsed_ctx:
                        user_text += parsed_ctx
                    else:
                        user_text += f"\n\n（附件解析失败：{parse_err}）"
                    if text:
                        user_text += f"\n\n用户补充：{text}"
                    user_text += "\n\n请根据已提取内容回答；若信息不足，再向用户追问。"
                    reply = _capture_turn(sess, user_text)
            else:
                reply = _capture_turn(sess, text)
            # 微信消息长度限制约 4096，超出则分段发送
            _send_chunks(client, reply, from_user, ctx_token)
        except Exception as e:
            logger.error(f"处理消息时出错：{e}", exc_info=True)
            try:
                client.send(f"❌ 出错了：{e}", to_user_id=from_user, context_token=ctx_token)
            except Exception:
                pass


def _send_chunks(client: ILinkClient, text: str, to_user: str, ctx_token: str,
                 max_len: int = 3000) -> None:
    """将长文本分段发送（微信单条消息有长度限制）。"""
    while text:
        chunk = text[:max_len]
        text  = text[max_len:]
        client.send(chunk, to_user_id=to_user, context_token=ctx_token)
        if text:
            time.sleep(0.3)  # 避免发送过快


# ── 主循环 ────────────────────────────────────────────────────────────────────

def run_bot(client: ILinkClient) -> None:
    """主消息循环：长轮询接收消息并处理。"""
    logger.info("✅ 微信 bot 已启动，等待消息…")
    cfg = _load_cfg()
    to_user = cfg.get("wechat_to_user_id", "")
    ctx_token = cfg.get("wechat_context_token", "")

    # 启动通知
    if to_user and ctx_token:
        startup_time = _dt.datetime.now().strftime("%H:%M")
        try:
            client.send(
                f"✅ SJTU Agent 已上线  {startup_time}\n直接发消息开始对话。",
                to_user_id=to_user,
                context_token=ctx_token,
            )
        except Exception:
            pass

    consecutive_errors = 0
    while True:
        try:
            msgs = client.get_updates()
            consecutive_errors = 0
            for msg in msgs:
                threading.Thread(
                    target=handle_message,
                    args=(client, msg),
                    daemon=True,
                ).start()
        except httpx.ReadTimeout:
            # 长轮询正常超时，继续
            pass
        except Exception as e:
            consecutive_errors += 1
            wait = min(5 * consecutive_errors, 60)
            logger.warning(f"getupdates 出错（{consecutive_errors}次），{wait}s 后重试：{e}")
            time.sleep(wait)


# ── WeChatBot 封装类（供外部调用主动推送） ─────────────────────────────────────

class WeChatBot:
    """
    简洁封装，供 daily_report.py / remind_check.py 等外部脚本调用。
    只要 config.json 里有 wechat_bot_token + wechat_context_token + wechat_to_user_id
    就能直接 push 消息。
    """

    def __init__(self, token: str, to_user_id: str, context_token: str):
        self._client       = ILinkClient(token)
        self._to_user_id   = to_user_id
        self._context_token = context_token

    @classmethod
    def from_config(cls, cfg_path: str | None = None) -> "WeChatBot":
        path = Path(cfg_path) if cfg_path else CONFIG_PATH
        cfg  = json.loads(path.read_text(encoding="utf-8"))
        token = cfg.get("wechat_bot_token", "")
        to    = cfg.get("wechat_to_user_id", "")
        ct    = cfg.get("wechat_context_token", "")
        if not token:
            raise RuntimeError("config.json 中未找到 wechat_bot_token，请先运行 wechat_bot.py 扫码登录")
        if not ct:
            raise RuntimeError("未找到 wechat_context_token，请先让对方给 bot 发一条消息")
        return cls(token, to, ct)

    def refresh_context_token(self) -> None:
        """先拉一次 getupdates 以刷新 context_token。"""
        msgs = self._client.get_updates()
        for msg in msgs:
            ct = msg.get("context_token", "")
            if ct:
                self._context_token = ct
                _save_wechat_fields(wechat_context_token=ct)

    def push(self, text: str) -> None:
        """主动推送消息（先刷新 context_token 确保投递成功）。"""
        self.refresh_context_token()
        _send_chunks(self._client, text, self._to_user_id, self._context_token)

    def send(self, text: str) -> None:
        """直接发送（不刷新 context_token）。"""
        _send_chunks(self._client, text, self._to_user_id, self._context_token)


# ── 供 remind_check.py 调用的接口 ────────────────────────────────────────────

def send_reminder_via_wechat(title: str, subtitle: str, body: str) -> None:
    """向微信推送提醒通知（由 remind_check.py 调用）。"""
    try:
        bot = WeChatBot.from_config()
        text = f"🔔 {title}\n{subtitle}"
        if body:
            text += f"\n{body}"
        bot.push(text)
    except Exception as e:
        logger.warning(f"微信推送失败：{e}")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SJTU Agent 微信 Bot (ilink 协议)")
    parser.add_argument("--login",  action="store_true", help="强制重新扫码登录")
    parser.add_argument("--test",   action="store_true", help="测试 token 连通性后退出")
    parser.add_argument("--push",   metavar="MSG",       help="主动推送一条消息后退出")
    args = parser.parse_args()

    cfg = _load_cfg()
    token = cfg.get("wechat_bot_token", "")

    # ── 登录 ──────────────────────────────────────────────────────────────────
    if args.login or not token:
        token, account_id, user_id = do_login()
        _save_wechat_fields(
            wechat_bot_token=token,
            wechat_account_id=account_id,
            wechat_user_id=user_id,
        )
        print(f"token 已保存到 {CONFIG_PATH}")
        print("\n⚠️  请用微信给你的 Bot 发一条任意消息，系统才能获取 context_token（首次必须）。")
        print("之后就可以直接运行 python3 wechat_bot.py 了。\n")
        # --login 模式只做扫码登录，保存完 token 后立即退出，不启动消息循环
        # 消息循环需要用户在 agent 外单独运行 python3 wechat_bot.py
        sys.exit(0)

    client = ILinkClient(token)

    # ── 测试模式 ───────────────────────────────────────────────────────────────
    if args.test:
        try:
            msgs = client.get_updates()
            print(f"✅ token 有效，长轮询返回 {len(msgs)} 条消息")
        except Exception as e:
            print(f"❌ token 无效或网络错误：{e}")
            sys.exit(1)
        sys.exit(0)

    # ── 主动推送模式 ───────────────────────────────────────────────────────────
    if args.push:
        cfg = _load_cfg()
        to    = cfg.get("wechat_to_user_id", "")
        ct    = cfg.get("wechat_context_token", "")
        if not to or not ct:
            print("❌ 未找到 wechat_to_user_id 或 wechat_context_token")
            print("   请先让用户给 bot 发一条消息，或先启动 bot 接收一次消息。")
            sys.exit(1)
        # 先刷新一次 context_token
        client.get_updates()
        cfg2 = _load_cfg()
        ct   = cfg2.get("wechat_context_token", ct)
        to   = cfg2.get("wechat_to_user_id", to)
        _send_chunks(client, args.push, to, ct)
        print("✅ 消息已发送")
        sys.exit(0)

    # ── 正常运行：启动消息循环 ────────────────────────────────────────────────
    cfg2 = _load_cfg()
    if not cfg2.get("wechat_context_token"):
        print("⚠️  还没有 context_token。")
        print("   请在微信里找到你刚才登录的 Bot（微信搜索「AI小助手」或你绑定的 bot 名），")
        print("   给它发一条任意消息（如「你好」），bot 就会记录 context_token。")
        print("   你也可以不管这个提示，直接等待——当用户发来第一条消息时会自动记录。\n")

    run_bot(client)


if __name__ == "__main__":
    main()
