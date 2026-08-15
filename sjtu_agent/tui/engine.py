"""
sjtu_agent/tui/engine.py — TUI 聊天引擎适配层。

TUI 与 Web GUI 共用同一套 SSE 引擎（sjtu_agent.web.server）和同一个
SQLite SessionStore，因此两边看到的会话、消息和命令结果天然同步。
本模块不依赖 Textual，可独立单元测试。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from sjtu_agent.commands import is_core_command
from sjtu_agent.web.server import (
    _decide_approval,
    _mark_cancelled,
    _stream_chat,
    _stream_command,
)
from sjtu_agent.web.session_store import SessionStore


def parse_sse_chunk(chunk: str) -> list[dict[str, Any]]:
    """把一段 SSE 文本解析为归一化事件列表。"""
    events: list[dict[str, Any]] = []
    for line in chunk.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            events.append({"kind": "done"})
            continue
        if not payload:
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if "token" in obj:
            events.append({"kind": "token", "text": obj.get("token", "")})
        elif "tool_start" in obj:
            events.append({"kind": "tool_start", **obj["tool_start"]})
        elif "tool_end" in obj:
            events.append({"kind": "tool_end", **obj["tool_end"]})
        elif "approval_required" in obj:
            events.append({"kind": "approval_required", **obj["approval_required"]})
        elif "cancelled" in obj:
            events.append({"kind": "cancelled"})
        elif "command_start" in obj:
            events.append({"kind": "command_start", **obj["command_start"]})
        elif "command_progress" in obj:
            events.append({"kind": "command_progress", **obj["command_progress"]})
        elif "command_result" in obj:
            events.append({"kind": "command_result", **obj["command_result"]})
        elif "error" in obj:
            events.append({"kind": "error", "text": str(obj.get("error", ""))})
    return events


def iter_chat_events(session_id: str, user_message: str) -> Iterator[dict[str, Any]]:
    """发送普通聊天消息，yield 归一化事件（token / tool_start / ...）。"""
    for chunk in _stream_chat(user_message, session_id=session_id):
        yield from parse_sse_chunk(chunk)


def iter_command_events(session_id: str, command: str) -> Iterator[dict[str, Any]]:
    """执行共享斜杠命令，yield command_start / progress / result 事件。"""
    for chunk in _stream_command(command, session_id=session_id):
        yield from parse_sse_chunk(chunk)


def choose_event_stream(session_id: str, text: str) -> Iterator[dict[str, Any]]:
    """按输入类型选择命令执行流或普通聊天流（与 WebUI 规则一致）。"""
    if text.startswith("/") and is_core_command(text):
        return iter_command_events(session_id, text)
    return iter_chat_events(session_id, text)


def decide_approval(approval_id: str, approved: bool) -> bool:
    """审批 Web/TUI 共享引擎中的危险工具调用。"""
    return _decide_approval(approval_id, approved)


def cancel_turn(session_id: str) -> None:
    """标记当前会话的生成任务为取消（与 /api/chat/cancel 同一通道）。"""
    _mark_cancelled(session_id or "__legacy__")


def new_store() -> SessionStore:
    """TUI 与 Web 共用同一默认 SessionStore（web_sessions.sqlite3）。"""
    return SessionStore()
