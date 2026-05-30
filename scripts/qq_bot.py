#!/usr/bin/env python3
"""
qq_bot.py - connect agent.py to QQ official bot (botpy).

Usage:
  python3 qq_bot.py           # run bot (WebSocket)
  python3 qq_bot.py --test    # verify appid/appsecret only

Config keys (config.json):
  qq_app_id
  qq_app_secret
  qq_allowed_user_ids   # optional whitelist; empty means allow all
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import sys
import threading
import time
import tempfile
import datetime as _dt
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sjtu_agent.paths import CONFIG_PATH

import agent
import botpy
from botpy.message import Message, DirectMessage


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mKABCDEFGHJKST]")
_MENTION_RE = re.compile(r"<@!?\d+>")
_TMP_DIR = Path(tempfile.mkdtemp(prefix="sjtu_qq_"))
_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".amr", ".silk", ".aac"}


def _load_cfg() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


cfg = _load_cfg()
APP_ID = str(cfg.get("qq_app_id", "")).strip()
APP_SECRET = str(cfg.get("qq_app_secret", "")).strip()

if not APP_ID or not APP_SECRET:
    print("❌ config.json missing qq_app_id / qq_app_secret")
    sys.exit(1)


def _verify_credentials(app_id: str, app_secret: str) -> tuple[bool | None, dict]:
    """
    Validate QQ bot credentials.
    Endpoint is used by botpy itself for access token refresh.
    """
    try:
        resp = requests.post(
            "https://bots.qq.com/app/getAppAccessToken",
            json={"appId": app_id, "clientSecret": app_secret},
            timeout=15,
        )
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code != 200:
            return False, {"http_status": resp.status_code, "response": body or resp.text[:300]}
        token = body.get("access_token", "")
        expires_in = body.get("expires_in", "")
        if token:
            return True, {"expires_in": expires_in}
        return False, {"response": body}
    except Exception as e:
        return None, {"error": str(e)}


def _build_date_ctx() -> str:
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    year = now.year
    month = now.month
    if month >= 9:
        cur_xnm, cur_xqm = year, "1"
        prev_xnm, prev_xqm = year - 1, "2"
    elif month <= 6:
        cur_xnm, cur_xqm = year - 1, "2"
        prev_xnm, prev_xqm = year - 1, "1"
    else:
        cur_xnm, cur_xqm = year - 1, "3"
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


_QQ_CTX = (
    "\n\n## 当前运行环境：QQ Bot\n"
    "你正在通过 QQ 机器人与用户交互。\n"
    "- 回复保持简洁实用，优先中文。\n"
    "- 不要要求用户在本地终端执行命令。\n"
)


_sessions: dict[str, dict] = {}
_locks: dict[str, threading.Lock] = {}
_reply_seq_lock = threading.Lock()
# Use a process-global monotonic msg_seq to avoid platform-side dedupe.
_reply_seq_global = max(1, int(time.time()) & 0x7FFFFFFF)


def _runtime_policy() -> tuple[bool, set[str]]:
    current = _load_cfg()
    enabled = bool(current.get("qq_enabled", True))
    allowed = {str(x).strip() for x in (current.get("qq_allowed_user_ids", []) or []) if str(x).strip()}
    return enabled, allowed


def _next_msg_seq(_: str = "", baseline: int | None = None) -> int:
    global _reply_seq_global
    with _reply_seq_lock:
        if baseline is not None and baseline > _reply_seq_global:
            _reply_seq_global = baseline
        _reply_seq_global += 1
        # Keep it in positive 32-bit range.
        if _reply_seq_global >= 0x7FFFFFFF:
            _reply_seq_global = 1
        return _reply_seq_global


def _get_session(user_id: str) -> dict:
    if user_id not in _sessions:
        agent_cfg = agent.load_agent_config()
        _sessions[user_id] = {
            "messages": [],
            "model_box": [agent_cfg["model"]],
            "client_box": [agent._make_client(agent_cfg)],
        }
        _locks[user_id] = threading.Lock()
    return _sessions[user_id]


def _capture_turn(sess: dict, user_text: str) -> str:
    if not sess["messages"]:
        sess["messages"].append({"role": "system", "content": agent.SYSTEM_PROMPT + _build_date_ctx() + _QQ_CTX})
    else:
        sess["messages"][0]["content"] = agent.SYSTEM_PROMPT + _build_date_ctx() + _QQ_CTX

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

    clean = _ANSI_RE.sub("", buf.getvalue())
    marker = "Agent: "
    idx = clean.rfind(marker)
    if idx == -1:
        for m in reversed(sess["messages"]):
            if m.get("role") == "assistant":
                content = m.get("content", "")
                if isinstance(content, str):
                    return content.strip() or "(已完成)"
                if isinstance(content, list):
                    texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                    return "\n".join(texts).strip() or "(已完成)"
        return "(已完成)"
    return clean[idx + len(marker):].strip()


def _normalize_text(raw: str) -> str:
    text = _MENTION_RE.sub("", raw or "")
    return re.sub(r"\s+", " ", text).strip()


def _split_text(text: str, max_len: int = 1500) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks


def _model_supports_vision(model: str) -> bool:
    m = (model or "").lower()
    return any(kw in m for kw in [
        "vision", "gpt-4o", "gpt-4-turbo", "claude-3", "claude-4",
        "gemini", "qwen-vl", "qwen3vl", "glm-4v", "internvl",
        "sonnet-4", "opus-4", "haiku-4",
    ])


def _capture_turn_multimodal(sess: dict, content: list) -> str:
    if not sess["messages"]:
        sess["messages"].append({"role": "system", "content": agent.SYSTEM_PROMPT + _build_date_ctx() + _QQ_CTX})
    else:
        sess["messages"][0]["content"] = agent.SYSTEM_PROMPT + _build_date_ctx() + _QQ_CTX
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
    path = str(url or "").split("?")[0]
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
    if media_type == "audio":
        return ".m4a"
    if media_type == "image":
        return ".jpg"
    return ".bin"


def _download_media(url: str, media_type: str = "file", filename: str = "") -> Path:
    if not url:
        raise RuntimeError("附件下载地址为空")
    resp = requests.get(url, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"下载附件失败: HTTP {resp.status_code}")
    name = (filename or "").strip()
    if not name:
        suffix = _guess_suffix_from_url(url, default="")
        if not suffix:
            suffix = _guess_suffix_from_content_type(resp.headers.get("content-type", ""), media_type=media_type)
        name = f"qq_media_{int(time.time())}{suffix}"
    save_path = _TMP_DIR / name
    save_path.write_bytes(resp.content)
    return save_path


def _extract_qq_media(message) -> dict | None:
    # Official botpy message model: media arrives via attachments.
    attachments = getattr(message, "attachments", None)
    if isinstance(attachments, list):
        for att in attachments:
            url = str(getattr(att, "url", "") or "")
            filename = str(getattr(att, "filename", "") or "")
            content_type = str(getattr(att, "content_type", "") or "").lower()
            if not url:
                continue
            if content_type.startswith("image/"):
                media_type = "image"
            elif content_type.startswith("audio/"):
                media_type = "audio"
            elif content_type.startswith("video/"):
                media_type = "video"
            else:
                media_type = "file"
            if media_type == "file" and _guess_suffix_from_url(url) in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
                media_type = "image"
            if media_type == "file" and (
                Path(filename).suffix.lower() in _AUDIO_SUFFIXES
                or _guess_suffix_from_url(url).lower() in _AUDIO_SUFFIXES
            ):
                media_type = "audio"
            return {"type": media_type, "url": url, "filename": filename}

    # Compatibility fallback for non-standard gateways.
    image = getattr(message, "image", None)
    if isinstance(image, str) and image.startswith(("http://", "https://")):
        return {"type": "image", "url": image, "filename": ""}

    return None


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


class QQAgentClient(botpy.Client):
    async def on_ready(self):
        me = getattr(getattr(self, "robot", None), "username", "")
        me_id = getattr(getattr(self, "robot", None), "id", "")
        print(f"[qq] gateway ready: username={me or '-'} id={me_id or '-'}")

    async def _reply_once(self, message, content: str) -> None:
        msg_id = str(getattr(message, "id", "") or "")
        raw_msg_seq = getattr(message, "msg_seq", None)
        try:
            baseline = int(raw_msg_seq) if raw_msg_seq is not None else None
        except Exception:
            baseline = None
        for _ in range(2):
            seq = _next_msg_seq(msg_id, baseline=baseline)
            try:
                await message.reply(content=content, msg_seq=seq)
                return
            except Exception as e:
                err = str(e)
                if "40054005" in err or "msgseq" in err.lower():
                    continue
                raise
        # 最后一轮仍失败，抛给上层日志
        raise RuntimeError("QQ reply failed after msg_seq retries")

    async def _reply_chunks(self, message, text: str) -> None:
        for chunk in _split_text(text):
            await self._reply_once(message, chunk)

    async def _process(self, message, user_id: str, text: str) -> None:
        enabled, allowed_ids = _runtime_policy()
        if not enabled:
            return

        if allowed_ids and user_id not in allowed_ids:
            await self._reply_once(
                message,
                content=(
                    "⚠️ 当前机器人设置了白名单，未授权此账号。\n"
                    f"你的 QQ 用户标识：{user_id}\n"
                    "请把这个标识回填给管理员加入白名单。"
                )
            )
            return

        media = _extract_qq_media(message)
        if not text and media is None:
            await self._reply_once(message, "请直接发送要咨询的内容。")
            return

        sess = _get_session(user_id)
        lock = _locks[user_id]
        if not lock.acquire(blocking=False):
            await self._reply_once(message, "上一条消息还在处理中，请稍候。")
            return

        try:
            model = sess["model_box"][0]
            if media is not None:
                local_path = await asyncio.to_thread(
                    _download_media,
                    media.get("url", ""),
                    media.get("type", "file"),
                    media.get("filename", ""),
                )
                if media.get("type") == "image" and _model_supports_vision(model):
                    img_bytes = local_path.read_bytes()
                    b64 = base64.b64encode(img_bytes).decode()
                    content: list = []
                    if text:
                        content.append({"type": "text", "text": text})
                    else:
                        content.append({"type": "text", "text": "用户发送了一张图片，请先描述内容，再回答用户问题。"})
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
                    reply = await asyncio.to_thread(_capture_turn_multimodal, sess, content)
                else:
                    parsed_ctx, parse_err = await asyncio.to_thread(
                        _build_parser_context,
                        local_path,
                        media.get("type", "file"),
                    )
                    user_text = (
                        f"[用户通过 QQ 发送了附件]\n"
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
                    reply = await asyncio.to_thread(_capture_turn, sess, user_text)
            else:
                reply = await asyncio.to_thread(_capture_turn, sess, text)
        except Exception as e:
            await self._reply_once(message, f"处理失败：{e}")
            return
        finally:
            lock.release()

        await self._reply_chunks(message, reply)

    async def on_at_message_create(self, message: Message):
        print("[qq] recv at_message_create")
        user_id = str(getattr(getattr(message, "author", None), "id", "") or "")
        text = _normalize_text(getattr(message, "content", ""))
        await self._process(message, user_id, text)

    async def on_direct_message_create(self, message: DirectMessage):
        # In OpenClaw/public-messages mode, C2C events are the canonical path.
        # Handling both direct_message_create and c2c_message_create can cause
        # mixed user-id sources and duplicate/conflicting replies.
        print("[qq] recv direct_message_create (ignored; use c2c path)")
        return

    async def on_group_at_message_create(self, message):  # botpy newer versions
        print("[qq] recv group_at_message_create")
        author = getattr(message, "author", None)
        user_id = str(getattr(author, "member_openid", "") or "")
        text = _normalize_text(getattr(message, "content", ""))
        await self._process(message, user_id or "group_user", text)

    async def on_c2c_message_create(self, message):  # botpy newer versions
        print("[qq] recv c2c_message_create")
        author = getattr(message, "author", None)
        user_id = str(getattr(author, "user_openid", "") or "")
        text = _normalize_text(getattr(message, "content", ""))
        await self._process(message, user_id or "c2c_user", text)


def _build_intents():
    # Keep compatibility across botpy versions.
    try:
        intents = botpy.Intents.none()
        # Required for group@ and C2C events in OpenClaw/public-messages mode.
        if hasattr(intents, "public_messages"):
            intents.public_messages = True
        intents.public_guild_messages = True
        intents.direct_message = True
        return intents
    except Exception:
        return botpy.Intents(public_messages=True, public_guild_messages=True, direct_message=True)


def _describe_intents(intents) -> str:
    names = ("public_messages", "public_guild_messages", "direct_message")
    state = {name: bool(getattr(intents, name, False)) for name in names}
    return ", ".join(f"{k}={v}" for k, v in state.items())


def _ensure_event_loop() -> asyncio.AbstractEventLoop:
    """
    Python 3.14+ no longer auto-creates a default event loop on get_event_loop().
    botpy.Client.__init__ still calls get_event_loop(), so we must provide one.
    """
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def main() -> None:
    parser = argparse.ArgumentParser(description="QQ bot entrypoint")
    parser.add_argument("--test", action="store_true", help="validate appid/appsecret and exit")
    args = parser.parse_args()

    if args.test:
        ok, detail = _verify_credentials(APP_ID, APP_SECRET)
        if ok is True:
            exp = detail.get("expires_in", "")
            print(f"✅ QQ 凭据验证成功（expires_in={exp}）")
            return
        if ok is False:
            print(f"❌ QQ 凭据验证失败：{detail}")
            sys.exit(1)
        print(f"⚠️ 无法验证凭据（网络或平台限制）：{detail}")
        return

    _ensure_event_loop()
    intents = _build_intents()
    client = QQAgentClient(intents=intents)
    print(f"✅ QQ Bot starting with appid={APP_ID}")
    print(f"[qq] intents: {_describe_intents(intents)}")
    _, allowed_ids = _runtime_policy()
    if not allowed_ids:
        print("[i] qq_allowed_user_ids is empty: allowing all users.")
    else:
        print(f"[i] qq_allowed_user_ids count={len(allowed_ids)}")
    client.run(appid=APP_ID, secret=APP_SECRET)


if __name__ == "__main__":
    main()
