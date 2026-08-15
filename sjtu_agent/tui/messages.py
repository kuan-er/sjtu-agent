"""
sjtu_agent/tui/messages.py — 消息内容解析 / 展示辅助（无 Textual 依赖）。
"""

from __future__ import annotations

import json
import re
from typing import Any

COMMAND_RESULT_MARKER = "__SJTU_COMMAND_RESULT__"

_DATE_CONTEXT_RE = re.compile(r"\n{0,2}## 当前时间[\s\S]*?(?=\n\n|$)")


def parse_command_result(content: str) -> dict[str, Any] | None:
    """解析 session 中持久化的结构化命令结果。"""
    if not isinstance(content, str) or not content.startswith(COMMAND_RESULT_MARKER):
        return None
    try:
        payload = json.loads(content[len(COMMAND_RESULT_MARKER):])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def strip_date_context(text: str) -> str:
    """去掉 Web 引擎自动注入的“当前时间”块，避免消息区显示噪声。"""
    return _DATE_CONTEXT_RE.sub("", str(text or "")).strip()


def display_text(content: str) -> str:
    """把持久化消息内容转成适合终端展示的纯文本。"""
    payload = parse_command_result(content)
    if payload is not None:
        return str(payload.get("text") or "")
    return strip_date_context(content)
