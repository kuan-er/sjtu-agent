"""
sjtu_agent/web/server.py — 本地 Web 配置界面 HTTP Server

使用方式：
    sjtu-agent web                # 启动后自动打开浏览器
    sjtu-agent web --port 8080    # 自定义端口
    sjtu-agent web --no-browser   # 不自动打开浏览器
    sjtu-agent web --host 0.0.0.0 --no-browser  # 服务器监听所有网卡
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from sjtu_agent.paths import ENV_PATH, CONFIG_PATH, AGENT_CONFIG_PATH, _get_or_create_web_token
from sjtu_agent.web.attachment_store import AttachmentStore
from sjtu_agent.web.session_store import SessionStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
_WEB_TOKEN = ""
_session_store = SessionStore()
_attachment_store = AttachmentStore()

def _check_auth(handler) -> bool:
    """Check the sjtu_token cookie against the stored web token."""
    import http.cookies
    cookie_header = handler.headers.get("Cookie", "")
    cookies = http.cookies.SimpleCookie(cookie_header)
    token = cookies.get("sjtu_token")
    return token and token.value == _WEB_TOKEN

# ── 预设 API 提供商 ──────────────────────────────────────────────────────────

PRESETS = {
    "zhiyuan": {
        "label": "致远一号（交大官方）",
        "base_url": "https://models.sjtu.edu.cn/api/v1",
        "model": "deepseek-chat",
        "env_key": "ZHIYUAN_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
        "env_key": "ANTHROPIC_API_KEY",
    },
    "custom": {
        "label": "自定义",
        "base_url": "",
        "model": "",
        "env_key": "OPENAI_API_KEY",
    },
}


# ── .env 读写 ────────────────────────────────────────────────────────────────

def _read_env() -> dict[str, str]:
    """读取 .env 文件，返回 key→value 字典（不含注释）。"""
    result: dict[str, str] = {}
    if not ENV_PATH.exists():
        return result
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _write_env(updates: dict[str, str]) -> None:
    """将 updates 中的键值合并写回 .env，保留原有行的顺序和注释。"""
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    written_keys: set[str] = set()

    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                lines.append(line)
                continue
            if "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    lines.append(f'{k}={updates[k]}')
                    written_keys.add(k)
                    continue
            lines.append(line)

    for k, v in updates.items():
        if k not in written_keys:
            lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_config() -> dict:
    """读取 config.json，失败返回空字典。"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_config(updates: dict) -> None:
    """将 updates 深合并写回 config.json。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = _read_config()
    cfg.update(updates)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_agent_config() -> dict:
    if not AGENT_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_agent_config(data: dict) -> None:
    AGENT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 脱敏显示 ─────────────────────────────────────────────────────────────────

def _mask(value: str, keep: int = 4) -> str:
    """保留前 keep 个字符，其余替换为 *。"""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * min(len(value) - keep, 20)


# ── API 状态检测 ──────────────────────────────────────────────────────────────

def _get_status() -> dict:
    """返回各项配置是否就绪。"""
    env = _read_env()
    cfg = _read_config()
    agent_cfg = _read_agent_config()

    # 判断 LLM API 是否配置
    has_zhiyuan = bool(env.get("ZHIYUAN_API_KEY"))
    has_deepseek = bool(env.get("DEEPSEEK_API_KEY"))
    has_openai = bool(env.get("OPENAI_API_KEY") or agent_cfg.get("api_key"))
    has_anthropic = bool(env.get("ANTHROPIC_API_KEY"))
    has_api = has_zhiyuan or has_deepseek or has_openai or has_anthropic

    # Canvas
    has_canvas = bool(cfg.get("canvas_token") and not cfg.get("canvas_token", "").startswith("YOUR_"))

    # jAccount
    has_jaccount = bool(env.get("JACCOUNT_USERNAME") and env.get("JACCOUNT_PASSWORD"))

    # Telegram
    has_telegram = bool(cfg.get("telegram_token"))

    # Feishu / Lark
    has_feishu = bool(cfg.get("feishu_app_id") and cfg.get("feishu_app_secret"))

    # aihaoke / phycai cookies
    has_aihaoke = bool(cfg.get("aihaoke_cookies", {}).get("haoke-token"))
    has_phycai = bool(cfg.get("phycai_cookies"))

    # MOOC
    has_mooc = bool(env.get("MOOC_USERNAME") and env.get("MOOC_PASSWORD"))

    # WeChat (ilink) — token is auto-saved after QR scan
    has_wechat = bool(cfg.get("wechat_bot_token"))

    # Push channel toggles
    telegram_enabled = bool(cfg.get("telegram_enabled", True))
    wechat_enabled = bool(cfg.get("wechat_enabled", True))
    feishu_enabled = bool(cfg.get("feishu_enabled", True))

    return {
        "api": has_api,
        "canvas": has_canvas,
        "jaccount": has_jaccount,
        "telegram": has_telegram,
        "feishu": has_feishu,
        "aihaoke": has_aihaoke,
        "phycai": has_phycai,
        "mooc": has_mooc,
        "wechat": has_wechat,
        "zhiyuan": has_zhiyuan,
        "deepseek": has_deepseek,
        "openai": has_openai,
        "anthropic": has_anthropic,
        "telegram_enabled": telegram_enabled,
        "wechat_enabled": wechat_enabled,
        "feishu_enabled": feishu_enabled,
    }


def _get_config_values() -> dict:
    """返回当前配置值（API Key 脱敏）。"""
    env = _read_env()
    cfg = _read_config()
    agent_cfg = _read_agent_config()

    # agent_config.json 是用户在 Web UI 中显式保存的配置，应优先回显。
    saved_provider = str(agent_cfg.get("provider", "") or "").strip().lower()
    provider = saved_provider if saved_provider in PRESETS else ""
    if not provider and agent_cfg.get("api_key"):
        provider = "custom"
    elif not provider and env.get("ZHIYUAN_API_KEY"):
        provider = "zhiyuan"
    elif not provider and env.get("DEEPSEEK_API_KEY"):
        provider = "deepseek"
    elif not provider and env.get("ANTHROPIC_API_KEY"):
        provider = "anthropic"
    elif not provider and env.get("OPENAI_API_KEY"):
        provider = "openai"
    elif not provider:
        provider = "custom"

    # 从 agent_config.json 读 base_url / model
    base_url = agent_cfg.get("base_url", "")
    model = agent_cfg.get("model", "")

    # 如果 agent_config 没有，根据 provider 给默认值
    if not base_url and provider in PRESETS:
        base_url = PRESETS[provider]["base_url"]
    if not model and provider in PRESETS:
        model = PRESETS[provider]["model"]

    # API key（脱敏）
    raw_key = agent_cfg.get("api_key", "")
    if not raw_key:
        if provider == "zhiyuan":
            raw_key = env.get("ZHIYUAN_API_KEY", "")
        elif provider == "deepseek":
            raw_key = env.get("DEEPSEEK_API_KEY", "")
        elif provider == "anthropic":
            raw_key = env.get("ANTHROPIC_API_KEY", "")
        elif provider == "openai":
            raw_key = env.get("OPENAI_API_KEY", "")

    return {
        "provider": provider,
        "api_key_masked": _mask(raw_key),
        "api_key_set": bool(raw_key),
        "base_url": base_url,
        "model": model,
        "jaccount_username": env.get("JACCOUNT_USERNAME", ""),
        "jaccount_password_set": bool(env.get("JACCOUNT_PASSWORD")),
        "canvas_token_masked": _mask(cfg.get("canvas_token", "")),
        "canvas_token_set": bool(cfg.get("canvas_token") and not cfg.get("canvas_token", "").startswith("YOUR_")),
        "canvas_base_url": cfg.get("canvas_base_url", "https://oc.sjtu.edu.cn"),
        "mooc_username": env.get("MOOC_USERNAME", ""),
        "mooc_password_set": bool(env.get("MOOC_PASSWORD")),
        "telegram_token_masked": _mask(cfg.get("telegram_token", "")),
        "telegram_token_set": bool(cfg.get("telegram_token")),
        "telegram_allowed_ids": cfg.get("telegram_allowed_ids", []),
        "feishu_app_id": cfg.get("feishu_app_id", ""),
        "feishu_app_id_set": bool(cfg.get("feishu_app_id")),
        "feishu_app_secret_masked": _mask(cfg.get("feishu_app_secret", "")),
        "feishu_app_secret_set": bool(cfg.get("feishu_app_secret")),
        "feishu_allowed_open_ids": cfg.get("feishu_allowed_open_ids", []),
        "wechat_set": bool(cfg.get("wechat_bot_token")),
        "telegram_enabled": bool(cfg.get("telegram_enabled", True)),
        "wechat_enabled": bool(cfg.get("wechat_enabled", True)),
        "feishu_enabled": bool(cfg.get("feishu_enabled", True)),
        "presets": PRESETS,
    }


# ── 聊天会话（旧版全局内存 + 新版 SQLite 会话） ────────────────────────────────

_legacy_chat_history: list[dict] = []   # 旧版无 session_id 的全局会话
_cancel_lock = threading.Lock()
_cancel_flags: dict[str, bool] = {}


def _mark_cancelled(key: str) -> None:
    with _cancel_lock:
        _cancel_flags[key] = True


def _clear_cancel_flag(key: str) -> None:
    with _cancel_lock:
        _cancel_flags.pop(key, None)


def _is_cancelled(key: str) -> bool:
    with _cancel_lock:
        return bool(_cancel_flags.get(key, False))


_approval_lock = threading.Lock()
_approval_states: dict[str, dict] = {}

_WEB_APPROVAL_TOOLS = {
    "send_email": "会发送真实邮件",
    "download_assignments": "会下载课程作业文件到本地",
    "setup_shuiyuan": "会启动浏览器并登录水源社区",
    "setup_course_community": "会登录选课社区",
    "setup_qq": "会写入 QQ Bot 配置",
    "tool_install_parse_backend": "会执行 pip 安装 OCR/ASR 依赖",
}


def _approval_required(tool_name: str) -> bool:
    return tool_name in _WEB_APPROVAL_TOOLS


def _register_approval(key: str, approval_id: str) -> None:
    event = threading.Event()
    with _approval_lock:
        _approval_states[approval_id] = {
            "key": key,
            "event": event,
            "decision": False,
        }


def _wait_for_approval(approval_id: str, timeout: float = 300.0) -> tuple[bool, bool]:
    with _approval_lock:
        state = _approval_states.get(approval_id)
    if not state:
        return False, False
    deadline = time.monotonic() + timeout
    while True:
        if state["event"].wait(0.2):
            timed_out = False
            break
        if _is_cancelled(state.get("key", "")):
            timed_out = False
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
    with _approval_lock:
        decision = bool(state.get("decision", False))
        _approval_states.pop(approval_id, None)
    return decision, timed_out


def _decide_approval(approval_id: str, approved: bool) -> bool:
    with _approval_lock:
        state = _approval_states.get(approval_id)
    if not state:
        return False
    state["decision"] = bool(approved)
    state["event"].set()
    return True


def _system_message(_agent) -> dict:
    return {"role": "system", "content": _agent.build_system_prompt()}


def _build_history(_agent, session_id: str | None) -> list[dict]:
    """构建本轮要发送的 history。

    新版会话持久化只保存 user/assistant 消息，system 前缀每轮注入；
    旧版无 session_id 时沿用全局内存列表，行为与之前一致。
    """
    if session_id:
        messages = [
            {"role": row["role"], "content": row["content"]}
            for row in _session_store.list_messages(session_id)
        ]
        history = [_system_message(_agent), *messages]
    else:
        global _legacy_chat_history
        if not _legacy_chat_history:
            _legacy_chat_history.append(_system_message(_agent))
        history = _legacy_chat_history
    return history


def _attachment_content(item: dict) -> str:
    """复用飞书 Bot 的媒体解析能力，提前提取附件内容。

    图片优先走独立视觉模型，其次走 OCR；其他文件走 parse_file 统一解析。
    避免让主 Agent 再调用 parse_local_file 并触发 OCR 安装询问。
    """
    path = Path(item["path"])
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"（附件读取失败：{exc}）"

    mime_type = str(item.get("mime_type") or "")
    if mime_type.startswith("image/"):
        try:
            from sjtu_agent.vision import analyze_image, load_vision_config
            if load_vision_config() is not None:
                return analyze_image(data, "请描述这张图片并提取其中的文字。")
        except Exception:
            pass

    try:
        from sjtu_agent.parsing import parse_file
        parsed = parse_file(str(path), max_chars=8000, strategy="auto")
        if parsed.get("ok") and str(parsed.get("content") or "").strip():
            return str(parsed.get("content"))
        return "（未能自动提取该附件内容）"
    except Exception as exc:
        return f"（附件解析失败：{exc}）"


def _attachment_note_for_chat(items: list[dict]) -> str:
    if not items:
        return ""
    lines = ["\n\n[用户通过 Web 上传了附件，内容已预解析]"]
    for item in items:
        lines.append(
            f"\n附件：{item['filename']}（type={item.get('mime_type')}, size={item.get('size')}B）"
            f"\n解析结果：{_attachment_content(item)}"
        )
    return "\n".join(lines)


def _get_chat_client():
    """根据当前配置创建客户端。
    与 agent.py 的 _make_client 保持一致：
    - claude 模型 → Anthropic SDK（带 claude-cli UA，兼容中转代理）
    - 其他模型   → OpenAI SDK
    """
    agent_cfg = _read_agent_config()
    env = _read_env()
    provider = str(agent_cfg.get("provider", "") or "").strip().lower()
    base_url = agent_cfg.get("base_url") or None
    model = agent_cfg.get("model", "deepseek-chat")
    ua = agent_cfg.get("user_agent", "claude-cli/1.0.57")
    api_key = agent_cfg.get("api_key", "")

    if model.startswith("claude"):
        api_key = api_key or env.get("ANTHROPIC_API_KEY", "")
        from anthropic import Anthropic
        client = Anthropic(
            api_key=api_key,
            base_url=base_url,
            default_headers={"user-agent": ua},
            timeout=120.0,
        )
        return client, model, "anthropic"

    if not api_key:
        if provider == "zhiyuan":
            api_key = env.get("ZHIYUAN_API_KEY", "")
        elif provider == "deepseek":
            api_key = env.get("DEEPSEEK_API_KEY", "")
        elif provider == "openai":
            api_key = env.get("OPENAI_API_KEY", "")
        elif provider == "custom":
            api_key = ""
        else:
            api_key = (
                env.get("ZHIYUAN_API_KEY")
                or env.get("DEEPSEEK_API_KEY")
                or env.get("OPENAI_API_KEY")
                or ""
            )

    if model.startswith("claude"):
        from anthropic import Anthropic
        client = Anthropic(
            api_key=api_key,
            base_url=base_url,
            default_headers={"user-agent": ua},
            timeout=120.0,
        )
        return client, model, "anthropic"
    else:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)
        return client, model, "openai"


def _stream_chat(user_message: str, session_id: str | None = None):
    """生成器：将 user_message 发给 LLM，支持完整 tool_use 循环，以 SSE 格式 yield 数据行。
    事件类型：
      {token: "..."} — 正文增量
      {tool_start: {name, input}} — 工具调用开始
      {tool_end: {name, result}} — 工具返回
      {error: "..."} — 错误
      [DONE] — 结束
    """
    import datetime as _dt

    # 延迟导入 agent 模块（避免循环依赖 + 启动时间）
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    import agent as _agent

    _now = _dt.datetime.now()
    _date_ctx = (
        f"\n\n## 当前时间\n"
        f"现在：{_now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[_now.weekday()]}。"
    )

    history = _build_history(_agent, session_id)
    user_entry = {"role": "user", "content": _date_ctx + "\n\n" + user_message}
    history.append(user_entry)
    turn_start = len(history)
    cancel_key = session_id or "__legacy__"
    _clear_cancel_flag(cancel_key)
    if session_id:
        _session_store.append_message(session_id, "user", user_entry["content"])

    try:
        client, model, proto = _get_chat_client()
    except Exception as exc:
        if not session_id:
            history.pop()
        yield f"data: {json.dumps({'error': f'创建客户端失败：{exc}'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    MAX_TOOL_ROUNDS = 20

    def _sse(obj):
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    cancelled = False
    try:
        if proto == "anthropic":
            inner = _stream_chat_anthropic(client, model, _agent, MAX_TOOL_ROUNDS, _sse, history, cancel_key)
        else:
            inner = _stream_chat_openai(client, model, _agent, MAX_TOOL_ROUNDS, _sse, history, cancel_key)

        for chunk in inner:
            if _is_cancelled(cancel_key):
                cancelled = True
                yield _sse({"cancelled": True})
                break
            yield chunk
    except Exception as exc:
        yield _sse({"error": str(exc)})

    # 流结束后落盘本轮新增的 assistant 文本（新版会话）。
    if session_id and not cancelled:
        for message in history[turn_start:]:
            if message.get("role") == "assistant" and isinstance(message.get("content"), str):
                _session_store.append_message(session_id, "assistant", message["content"])

    _clear_cancel_flag(cancel_key)
    yield "data: [DONE]\n\n"


def _web_run_tool(fn_name, fn_args, _agent, cancel_key, _sse):
    """执行单个工具，负责 tool_start / approval / tool_end SSE 事件。

    危险工具会先等待 Web UI 审批；超时或拒绝时返回结构化错误结果，
    让 LLM 继续生成解释而不是中断会话。
    """
    yield _sse({"tool_start": {"name": fn_name, "input": fn_args}})

    result: str
    if _approval_required(fn_name):
        approval_id = uuid.uuid4().hex[:12]
        _register_approval(cancel_key, approval_id)
        yield _sse({
            "approval_required": {
                "approval_id": approval_id,
                "tool_name": fn_name,
                "arguments": fn_args,
                "risk_hint": _WEB_APPROVAL_TOOLS[fn_name],
            }
        })
        approved, timed_out = _wait_for_approval(approval_id)
        if timed_out:
            result = json.dumps({"ok": False, "error": "用户审批超时，已取消该工具执行"}, ensure_ascii=False)
        elif not approved:
            result = json.dumps({"ok": False, "error": "用户在 Web UI 中拒绝了该工具执行"}, ensure_ascii=False)
        else:
            result = _agent.run_tool(fn_name, fn_args)
    else:
        result = _agent.run_tool(fn_name, fn_args)

    result_preview = result[:500] if len(result) > 500 else result
    yield _sse({"tool_end": {"name": fn_name, "result": result_preview}})
    return result


def _stream_chat_anthropic(client, model, _agent, max_rounds, _sse, history, cancel_key):
    """Anthropic Messages API 流式 + tool_use 循环。"""

    # 上下文质量管理（web 独立流式不经过 runner._run_one_turn）
    from sjtu_agent.agent.context import trim_session
    trim_session(history)

    system_msg = ""
    for m in history:
        if m["role"] == "system":
            system_msg = m["content"]
            break
    api_msgs = [m for m in history if m["role"] != "system"]
    tools = _agent._anthropic_tools()

    for _round in range(max_rounds):
        full_text = ""
        content_blocks: list[dict] = []
        tool_inputs: dict[int, str] = {}

        try:
            with client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system_msg,
                messages=api_msgs,
                tools=tools,
            ) as stream:
                for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "content_block_start":
                        block = event.content_block
                        btype = getattr(block, "type", "")
                        if btype == "text":
                            content_blocks.append({"type": "text", "text": ""})
                        elif btype == "tool_use":
                            content_blocks.append({
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": {},
                            })
                            tool_inputs[len(content_blocks) - 1] = ""
                    elif etype == "content_block_delta":
                        delta = event.delta
                        dtype = getattr(delta, "type", "")
                        if dtype == "text_delta":
                            chunk = delta.text
                            full_text += chunk
                            if content_blocks and content_blocks[-1].get("type") == "text":
                                content_blocks[-1]["text"] += chunk
                            yield _sse({"token": chunk})
                        elif dtype == "input_json_delta":
                            idx = event.index
                            tool_inputs[idx] = tool_inputs.get(idx, "") + delta.partial_json
        except Exception as exc:
            if history and history[-1]["role"] == "user":
                history.pop()
            yield _sse({"error": str(exc)})
            return

        # 解析 tool_use input
        for idx, raw_json in tool_inputs.items():
            if idx < len(content_blocks) and content_blocks[idx].get("type") == "tool_use":
                try:
                    content_blocks[idx]["input"] = json.loads(raw_json or "{}")
                except Exception:
                    content_blocks[idx]["input"] = {}

        has_tool_use = any(b.get("type") == "tool_use" for b in content_blocks)
        api_msgs.append({"role": "assistant", "content": content_blocks})

        if not has_tool_use:
            history.append({"role": "assistant", "content": full_text})
            return

        # 执行工具（危险工具会等待 Web UI 审批）
        tool_results = []
        for b in content_blocks:
            if b.get("type") != "tool_use":
                continue
            fn_name = b["name"]
            fn_args = b["input"] if isinstance(b["input"], dict) else {}
            result = yield from _web_run_tool(fn_name, fn_args, _agent, cancel_key, _sse)
            tool_results.append({"type": "tool_result", "tool_use_id": b["id"], "content": result})
        api_msgs.append({"role": "user", "content": tool_results})

    # 超出 max_rounds 仍在调工具：强制一次无工具调用合成最终回复
    try:
        fb_text = ""
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=system_msg,
            messages=api_msgs,
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = event.delta
                    if getattr(delta, "type", "") == "text_delta":
                        fb_text += delta.text
                        yield _sse({"token": delta.text})
        history.append({"role": "assistant", "content": fb_text or full_text})
    except Exception as exc:
        yield _sse({"error": str(exc)})
        history.append({"role": "assistant", "content": full_text})


def _stream_chat_openai(client, model, _agent, max_rounds, _sse, history, cancel_key):
    """OpenAI Chat Completions 流式 + tool_calls 循环。"""

    # 上下文质量管理（与 runner._run_one_turn 同钩子，web 独立流式不经过它）
    from sjtu_agent.agent.context import trim_session
    trim_session(history)
    messages = list(history)

    for _round in range(max_rounds):
        full_text = ""
        tool_calls_map: dict[int, dict] = {}

        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=_agent.TOOLS,
                tool_choice="auto",
                stream=True,
                timeout=180,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    full_text += delta.content
                    yield _sse({"token": delta.content})
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                        if tc.id:
                            tool_calls_map[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_map[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_map[idx]["arguments"] += tc.function.arguments
        except Exception as exc:
            if history and history[-1]["role"] == "user":
                history.pop()
            yield _sse({"error": str(exc)})
            return

        if not tool_calls_map:
            history.append({"role": "assistant", "content": full_text})
            messages.append({"role": "assistant", "content": full_text})
            return

        # 构建 assistant 消息
        from openai.types.chat import ChatCompletionMessageToolCall
        from openai.types.chat.chat_completion_message_tool_call import Function
        from openai.types.chat import ChatCompletionMessage

        tc_objs = []
        for idx in sorted(tool_calls_map):
            e = tool_calls_map[idx]
            tc_objs.append(ChatCompletionMessageToolCall(
                id=e["id"], type="function",
                function=Function(name=e["name"], arguments=e["arguments"]),
            ))
        assistant_msg = ChatCompletionMessage(
            role="assistant", content=full_text or None, tool_calls=tc_objs,
        )
        messages.append(assistant_msg)

        # 执行工具（危险工具会等待 Web UI 审批）
        for tc in tc_objs:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments or "{}")
            result = yield from _web_run_tool(fn_name, fn_args, _agent, cancel_key, _sse)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    # 超出 max_rounds 仍在调工具：强制一次无工具调用合成最终回复
    try:
        fb_text = ""
        fb_stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            timeout=180,
        )
        for chunk in fb_stream:
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            if d.content:
                fb_text += d.content
                yield _sse({"token": d.content})
        history.append({"role": "assistant", "content": fb_text or full_text})
    except Exception as exc:
        yield _sse({"error": str(exc)})
        history.append({"role": "assistant", "content": full_text})


def _test_api(api_key: str, base_url: str, model: str) -> dict:
    """发送一条测试消息，返回是否成功和延迟。"""
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai 包未安装"}

    if not api_key:
        return {"ok": False, "error": "API Key 为空"}
    if not base_url:
        return {"ok": False, "error": "Base URL 为空"}
    if not model:
        return {"ok": False, "error": "模型名为空"}

    try:
        import httpx
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=15.0,  # 15s 超时，防止卡住
        )
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        latency = int((time.time() - t0) * 1000)
        # 部分非标准 API 实现可能返回 str 或其他类型
        if hasattr(resp, "choices") and resp.choices:
            reply = resp.choices[0].message.content or ""
        else:
            reply = str(resp)[:80]
        return {"ok": True, "latency_ms": latency, "reply": reply}
    except Exception as exc:
        err = str(exc)
        # 提取关键错误信息
        if "401" in err or "Unauthorized" in err or "invalid_api_key" in err.lower():
            err = "API Key 无效（401 Unauthorized）"
        elif "timed out" in err.lower() or "timeout" in err.lower():
            err = "连接超时（15s），请检查 Base URL 是否正确"
        elif "Connection" in err or "connect" in err.lower():
            err = "无法连接到服务器，请检查 Base URL 和网络"
        elif "model" in err.lower() and ("not found" in err.lower() or "not exist" in err.lower()):
            err = f"模型 '{model}' 不存在，请确认模型名称"
        elif "choices" in err:
            err = "API 响应格式不兼容（非 OpenAI 标准格式）"
        return {"ok": False, "error": err}


def _wechat_qr_status(qrcode_key: str, ilink_base: str) -> dict:
    """轮询微信二维码扫码状态，扫码成功后保存 token 并尝试启动守护进程。"""
    try:
        import httpx as _httpx
        resp = _httpx.get(
            f"{ilink_base}/ilink/bot/get_qrcode_status?qrcode={qrcode_key}",
            timeout=10,
        )
        status = resp.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}

    code = status.get("code", -1)
    if code == 0:
        token      = status["bot_token"]
        account_id = status.get("account_id", "")
        user_id    = status.get("user_id", "")
        cfg = _read_config()
        cfg["wechat_bot_token"]   = token
        cfg["wechat_account_id"] = account_id
        cfg["wechat_user_id"]    = user_id
        _write_config(cfg)
        started = False
        try:
            import subprocess as _sp, os as _os
            uid = _os.getuid()
            r = _sp.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/com.sjtu.wechat-bot"],
                capture_output=True, timeout=10,
            )
            started = r.returncode == 0
        except Exception:
            pass
        return {"status": "success", "daemon_started": started}
    elif code in (1, 2):
        return {"status": "pending"}
    else:
        return {"status": "expired", "code": code}


def _start_feishu_bot() -> dict:
    """安装（若需要）并通过 launchctl 启动飞书 bot。"""
    import sys as _sys
    if _sys.platform != "darwin":
        return {"ok": False, "error": "目前仅支持 macOS 通过 launchd 自动启动；其他平台请手动运行 `sjtu-agent feishu-bot`。"}
    cfg = _read_config()
    if not (cfg.get("feishu_app_id") and cfg.get("feishu_app_secret")):
        return {"ok": False, "error": "请先填写并保存飞书 App ID / App Secret"}
    try:
        from sjtu_agent.scheduler import install_daemons
        from pathlib import Path as _P
        install_daemons(
            service_names=("feishu-bot",),
            python_executable=_P(_sys.executable),
            load=True,
        )
    except Exception as exc:
        return {"ok": False, "error": f"安装 launchd 服务失败：{exc}"}
    try:
        import subprocess as _sp, os as _os
        uid = _os.getuid()
        r = _sp.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/com.sjtu.feishu-bot"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return {"ok": True, "message": "✅ 飞书 bot 已启动；现在可以在飞书里搜索 bot 私聊试试。"}
        return {"ok": False, "error": f"launchctl kickstart 失败：{r.stderr.strip() or r.stdout.strip()}"}
    except Exception as exc:
        return {"ok": False, "error": f"launchctl 调用失败：{exc}"}


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # 静默日志，只打印错误
        pass

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        ext = path.suffix.lower()
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".svg": "image/svg+xml",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
            ".ttf": "font/ttf",
        }.get(ext, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("/", "/index.html", "/legacy", "/legacy.html"):
            self.send_response(200)
            self.send_header("Set-Cookie", f"sjtu_token={_WEB_TOKEN}; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            index_name = "legacy.html" if path.startswith("/legacy") else "index.html"
            data = (STATIC_DIR / index_name).read_bytes()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        elif path == "/api/commands":
            if not _check_auth(self): self._send_json({"error":"unauthorized"}, 403); return
            from sjtu_agent.commands import command_defs
            self._send_json({"commands": command_defs()})
        elif path == "/api/commands/resolve":
            if not _check_auth(self): self._send_json({"error":"unauthorized"}, 403); return
            from urllib.parse import parse_qs
            from sjtu_agent.commands import command_prompt
            qs = parse_qs(urlparse(self.path).query)
            text = (qs.get("text") or [""])[0]
            self._send_json({"prompt": command_prompt(text)})
        elif path == "/api/config":
            if not _check_auth(self): self._send_json({"error":"unauthorized"}, 403); return
            self._send_json(_get_config_values())
        elif path == "/api/status":
            if not _check_auth(self): self._send_json({"error":"unauthorized"}, 403); return
            self._send_json(_get_status())
        elif path == "/api/sessions":
            if not _check_auth(self): self._send_json({"error":"unauthorized"}, 403); return
            self._send_json({"sessions": _session_store.list_sessions()})
        elif path.startswith("/api/attachments"):
            if not _check_auth(self): self._send_json({"error":"unauthorized"}, 403); return
            if path == "/api/attachments":
                from urllib.parse import parse_qs
                qs = parse_qs(urlparse(self.path).query)
                session_id = (qs.get("session_id") or [""])[0]
                self._send_json({"attachments": _attachment_store.list_for_session(session_id) if session_id else []})
            else:
                match = re.fullmatch(r"/api/attachments/([^/]+)", path)
                if match:
                    item = _attachment_store.get(match.group(1))
                    if not item:
                        self._send_json({"error": "attachment not found"}, 404)
                        return
                    file_path = Path(item["path"])
                    self.send_response(200)
                    self.send_header("Content-Type", item["mime_type"])
                    disposition = "attachment" if "download=1" in urlparse(self.path).query else "inline"
                    self.send_header(
                        "Content-Disposition",
                        f'{disposition}; filename="{item["filename"]}"',
                    )
                    data = file_path.read_bytes()
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._send_json({"error": "not found"}, 404)
        elif path.startswith("/api/sessions/"):
            if not _check_auth(self): self._send_json({"error":"unauthorized"}, 403); return
            match = re.fullmatch(r"/api/sessions/([^/]+)/messages", path)
            if match:
                session = _session_store.get_session(match.group(1))
                if not session:
                    self._send_json({"error": "session not found"}, 404)
                    return
                self._send_json({
                    "session": session,
                    "messages": _session_store.list_messages(match.group(1)),
                })
            else:
                self._send_json({"error": "not found"}, 404)
        elif path == "/api/wechat/qr_status":
            if not _check_auth(self): self._send_json({"error":"unauthorized"}, 403); return
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            key = qs.get("key", [""])[0]
            ilink_base = qs.get("ilink_base", ["https://ilinkai.weixin.qq.com"])[0]
            from urllib.parse import urlparse as _urlparse
            _host = _urlparse(ilink_base).hostname or ""
            if _host not in ("ilinkai.weixin.qq.com", "api.weixin.qq.com"):
                self._send_json({"status": "error", "error": "invalid ilink_base"}, 400)
            elif not key:
                self._send_json({"status": "error", "error": "missing key"}, 400)
            else:
                self._send_json(_wechat_qr_status(key, ilink_base))
        else:
            # 尝试静态文件 — 防路径遍历
            file_path = (STATIC_DIR / path.lstrip("/")).resolve()
            if not file_path.is_relative_to(STATIC_DIR.resolve()):
                self._send_json({"error": "not found"}, status=404)
                return
            if file_path.is_file():
                self._send_file(file_path)
            else:
                self.send_response(404)
                self.end_headers()

    def do_POST(self):
        if not _check_auth(self):
            self._send_json({"error": "unauthorized"}, status=403)
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config/save":
            body = self._read_body()
            self._handle_save(body)
        elif path == "/api/test/api":
            body = self._read_body()
            result = _test_api(
                api_key=body.get("api_key", ""),
                base_url=body.get("base_url", ""),
                model=body.get("model", ""),
            )
            self._send_json(result)
        elif path == "/api/sessions":
            body = self._read_body()
            session = _session_store.create_session(body.get("title") or "")
            self._send_json(session, 201)
        elif path == "/api/attachments":
            body = self._read_body()
            session_id = body.get("session_id") or ""
            filename = body.get("filename") or "file"
            data_b64 = body.get("data_base64") or ""
            mime_type = body.get("mime_type") or "application/octet-stream"
            if not session_id or not _session_store.get_session(session_id):
                self._send_json({"error": "session not found"}, 404)
                return
            if not data_b64:
                self._send_json({"error": "附件内容为空"}, 400)
                return
            try:
                data = base64.b64decode(data_b64, validate=False)
            except Exception as exc:
                self._send_json({"error": f"附件 base64 无效：{exc}"}, 400)
                return
            if len(data) > 20 * 1024 * 1024:
                self._send_json({"error": "附件不能超过 20MB"}, 400)
                return
            self._send_json(_attachment_store.save(session_id, filename, data, mime_type), 201)
        elif path.startswith("/api/approvals/"):
            match = re.fullmatch(r"/api/approvals/([^/]+)", path)
            if not match:
                self._send_json({"error": "not found"}, 404)
                return
            body = self._read_body()
            approved = body.get("decision") in {"approve", True, "true"}
            if not _decide_approval(match.group(1), approved):
                self._send_json({"error": "approval not found or expired"}, 404)
                return
            self._send_json({"ok": True})
        elif path.startswith("/api/sessions/") and path.endswith("/messages"):
            match = re.fullmatch(r"/api/sessions/([^/]+)/messages", path)
            if not match:
                self._send_json({"error": "not found"}, 404)
                return
            if not _session_store.get_session(match.group(1)):
                self._send_json({"error": "session not found"}, 404)
                return
            body = self._read_body()
            role = body.get("role")
            content = str(body.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                self._send_json({"error": "role/content 无效"}, 400)
                return
            _session_store.append_message(match.group(1), role, content)
            self._send_json({"ok": True})
        elif path == "/api/chat/cancel":
            body = self._read_body()
            session_id = body.get("session_id") or "__legacy__"
            _mark_cancelled(session_id)
            self._send_json({"ok": True})
        elif path == "/api/chat":
            body = self._read_body()
            user_msg = body.get("message", "").strip()
            if not user_msg:
                self._send_json({"error": "消息不能为空"}, 400)
                return
            session_id = body.get("session_id") or None
            if session_id and not _session_store.get_session(session_id):
                self._send_json({"error": "session not found"}, 404)
                return
            attachment_items = []
            for attachment_id in body.get("attachment_ids") or []:
                item = _attachment_store.get(str(attachment_id))
                if item and item.get("session_id") == session_id:
                    attachment_items.append(item)
            attachment_note = _attachment_note_for_chat(attachment_items)
            if attachment_note:
                user_msg += attachment_note
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            for chunk in _stream_chat(user_msg, session_id=session_id):
                try:
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
        elif path == "/api/chat/clear":
            body = self._read_body()
            session_id = body.get("session_id") or None
            if session_id:
                if not _session_store.get_session(session_id):
                    self._send_json({"error": "session not found"}, 404)
                    return
                _session_store.clear_messages(session_id)
            else:
                global _legacy_chat_history
                _legacy_chat_history = []
            self._send_json({"ok": True})
        elif path == "/api/feishu/start":
            self._send_json(_start_feishu_bot())
        else:
            self.send_response(404)
            self.end_headers()

    def do_PATCH(self):
        if not _check_auth(self):
            self._send_json({"error": "unauthorized"}, status=403)
            return
        path = urlparse(self.path).path.rstrip("/")
        match = re.fullmatch(r"/api/sessions/([^/]+)", path)
        if not match:
            self.send_response(404)
            self.end_headers()
            return
        body = self._read_body()
        session = _session_store.rename_session(match.group(1), body.get("title", ""))
        if not session:
            self._send_json({"error": "session not found"}, 404)
            return
        self._send_json(session)

    def do_DELETE(self):
        if not _check_auth(self):
            self._send_json({"error": "unauthorized"}, status=403)
            return
        path = urlparse(self.path).path.rstrip("/")
        attachment_match = re.fullmatch(r"/api/attachments/([^/]+)", path)
        if attachment_match:
            deleted = _attachment_store.delete(attachment_match.group(1))
            self._send_json({"deleted": deleted}, 200 if deleted else 404)
            return
        match = re.fullmatch(r"/api/sessions/([^/]+)", path)
        if not match:
            self.send_response(404)
            self.end_headers()
            return
        if not _session_store.delete_session(match.group(1)):
            self._send_json({"error": "session not found"}, 404)
            return
        self._send_json({"ok": True})

    def _handle_save(self, body: dict) -> None:
        section = body.get("section", "")
        try:
            if section == "api":
                self._save_api(body)
            elif section == "credentials":
                self._save_credentials(body)
            elif section == "telegram":
                self._save_telegram(body)
            elif section == "feishu":
                self._save_feishu(body)
            elif section == "push_channels":
                self._save_push_channels(body)
            else:
                self._send_json({"ok": False, "error": f"未知 section: {section}"}, 400)
                return
            self._send_json({"ok": True})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def _save_api(self, body: dict) -> None:
        provider = body.get("provider", "custom")
        api_key = body.get("api_key", "").strip()
        base_url = body.get("base_url", "").strip()
        model = body.get("model", "").strip()

        # 写 .env
        env_updates: dict[str, str] = {}
        if api_key:
            preset = PRESETS.get(provider, PRESETS["custom"])
            env_key = preset["env_key"]
            if provider == "zhiyuan":
                env_updates["ZHIYUAN_API_KEY"] = api_key
            elif provider == "deepseek":
                env_updates["DEEPSEEK_API_KEY"] = api_key
            elif provider == "anthropic":
                env_updates["ANTHROPIC_API_KEY"] = api_key
            elif provider == "openai":
                env_updates[env_key] = api_key
        if env_updates:
            _write_env(env_updates)

        # 写 agent_config.json
        agent_cfg = _read_agent_config()
        agent_cfg["provider"] = provider if provider in PRESETS else "custom"
        if base_url:
            agent_cfg["base_url"] = base_url
        if model:
            agent_cfg["model"] = model
        if api_key and provider in ("custom", "openai", "anthropic"):
            agent_cfg["api_key"] = api_key
        elif provider == "zhiyuan":
            agent_cfg.pop("api_key", None)
        _write_agent_config(agent_cfg)

    def _save_credentials(self, body: dict) -> None:
        env_updates: dict[str, str] = {}
        cfg_updates: dict = {}

        if body.get("jaccount_username"):
            env_updates["JACCOUNT_USERNAME"] = body["jaccount_username"].strip()
        if body.get("jaccount_password"):
            env_updates["JACCOUNT_PASSWORD"] = body["jaccount_password"].strip()
        if body.get("mooc_username"):
            env_updates["MOOC_USERNAME"] = body["mooc_username"].strip()
        if body.get("mooc_password"):
            env_updates["MOOC_PASSWORD"] = body["mooc_password"].strip()

        if body.get("canvas_token"):
            cfg_updates["canvas_token"] = body["canvas_token"].strip()
        if body.get("canvas_base_url"):
            cfg_updates["canvas_base_url"] = body["canvas_base_url"].strip()

        if env_updates:
            _write_env(env_updates)
        if cfg_updates:
            _write_config(cfg_updates)

    def _save_telegram(self, body: dict) -> None:
        cfg_updates: dict = {}
        if body.get("telegram_token"):
            cfg_updates["telegram_token"] = body["telegram_token"].strip()
        if "telegram_allowed_ids" in body:
            ids = body["telegram_allowed_ids"]
            if isinstance(ids, list):
                cfg_updates["telegram_allowed_ids"] = [int(i) for i in ids if str(i).strip().lstrip("-").isdigit()]
            elif isinstance(ids, str):
                raw_ids = [i.strip() for i in re.split(r"[,\s]+", ids) if i.strip()]
                cfg_updates["telegram_allowed_ids"] = [int(i) for i in raw_ids if i.lstrip("-").isdigit()]
        if cfg_updates:
            _write_config(cfg_updates)


    def _save_feishu(self, body: dict) -> None:
        cfg_updates: dict = {}
        if body.get("feishu_app_id"):
            cfg_updates["feishu_app_id"] = body["feishu_app_id"].strip()
        if body.get("feishu_app_secret"):
            cfg_updates["feishu_app_secret"] = body["feishu_app_secret"].strip()
        if "feishu_allowed_open_ids" in body:
            ids = body["feishu_allowed_open_ids"]
            if isinstance(ids, list):
                cfg_updates["feishu_allowed_open_ids"] = [str(i).strip() for i in ids if str(i).strip()]
            elif isinstance(ids, str):
                raw_ids = [i.strip() for i in re.split(r"[,\s]+", ids) if i.strip()]
                cfg_updates["feishu_allowed_open_ids"] = raw_ids
        if cfg_updates:
            _write_config(cfg_updates)

    def _save_push_channels(self, body: dict) -> None:
        cfg_updates: dict = {}
        if "telegram_enabled" in body:
            cfg_updates["telegram_enabled"] = bool(body["telegram_enabled"])
        if "wechat_enabled" in body:
            cfg_updates["wechat_enabled"] = bool(body["wechat_enabled"])
        if "feishu_enabled" in body:
            cfg_updates["feishu_enabled"] = bool(body["feishu_enabled"])
        if cfg_updates:
            _write_config(cfg_updates)

        # 后台同步守护进程状态（macOS 上自动启停 bot 服务）
        # 注意：不能调用 install_daemons，因为它会处理 web 服务并触发
        # launchctl bootout，导致当前 Web Server 进程被自杀。
        def _sync() -> None:
            import sys as _sys, os, subprocess, plistlib
            if _sys.platform != "darwin":
                return
            try:
                from pathlib import Path as _P
                from sjtu_agent.scheduler.launchd import (
                    _SERVICE_SPECS, _build_plist, _DEFAULT_OUTPUT_DIR,
                )

                out_dir = _P(_DEFAULT_OUTPUT_DIR).expanduser().resolve()
                py = _P(_sys.executable).absolute()
                uid = os.getuid()

                for key, enabled in cfg_updates.items():
                    name = {
                        "telegram_enabled": "telegram-bot",
                        "wechat_enabled": "wechat-bot",
                        "feishu_enabled": "feishu-bot",
                    }.get(key)
                    if name is None or name not in _SERVICE_SPECS:
                        continue
                    spec = _SERVICE_SPECS[name]
                    label = spec["label"]
                    plist_path = out_dir / f"{label}.plist"

                    if enabled:
                        payload = _build_plist(
                            name, py, (22, 0), 60, 10, (10, 0),
                        )
                        with plist_path.open("wb") as f:
                            plistlib.dump(payload, f, sort_keys=False)
                        for domain in (f"gui/{uid}", f"user/{uid}"):
                            subprocess.run(
                                ["launchctl", "bootout", f"{domain}/{label}"],
                                capture_output=True,
                            )
                            r = subprocess.run(
                                ["launchctl", "bootstrap", domain, str(plist_path)],
                                capture_output=True, text=True,
                            )
                            if r.returncode == 0:
                                break
                    else:
                        for domain in (f"gui/{uid}", f"user/{uid}"):
                            subprocess.run(
                                ["launchctl", "bootout", f"{domain}/{label}"],
                                capture_output=True,
                            )
                        if plist_path.exists():
                            plist_path.unlink()
            except Exception:
                pass

        threading.Thread(target=_sync, daemon=True).start()


# ── 启动入口 ──────────────────────────────────────────────────────────────────

def start(port: int = 7860, open_browser: bool = True, host: str = "127.0.0.1") -> None:
    global _WEB_TOKEN
    _WEB_TOKEN = _get_or_create_web_token()

    server = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"\n🌐  SJTU Agent 配置界面已启动：{url}")
    print(f"     访问令牌（自动通过 Cookie 传递）：{_WEB_TOKEN}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("     [!] 正在监听非回环地址，请勿在无 HTTPS 反代的情况下直接暴露公网。")
    print("     按 Ctrl+C 关闭\n")

    if open_browser:
        # 延迟 0.5s 再打开，确保 server 已就绪
        browser_url = f"http://127.0.0.1:{port}"
        def _open():
            time.sleep(0.5)
            webbrowser.open(browser_url)
        threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n配置界面已关闭。")
        server.server_close()
