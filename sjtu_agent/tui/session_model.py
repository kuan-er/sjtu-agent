"""
sjtu_agent/tui/session_model.py — TUI 会话状态模型（与 Web 共享 SQLite）。

所有操作直接落在 web_sessions.sqlite3；Web GUI 和 TUI 读取同一份数据。
"""

from __future__ import annotations

from typing import Any

from sjtu_agent.web.session_store import SessionStore


def _title_from_message(text: str) -> str:
    return " ".join((text or "").split())[:24] or "新会话"


class TuiSessionModel:
    """TUI 使用的会话模型；无 Textual 依赖，可独立测试。"""

    def __init__(self, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore()
        self.current_id: str | None = None

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.store.list_sessions()

    def get_current(self) -> dict[str, Any] | None:
        if not self.current_id:
            return None
        return self.store.get_session(self.current_id)

    def create_session(self, title: str = "新会话") -> dict[str, Any]:
        session = self.store.create_session(title)
        self.current_id = session.get("id")
        return session

    def select(self, session_id: str) -> None:
        self.current_id = session_id if self.store.get_session(session_id) else None

    def rename(self, session_id: str, title: str) -> dict[str, Any] | None:
        return self.store.rename_session(session_id, title)

    def delete(self, session_id: str) -> bool:
        ok = self.store.delete_session(session_id)
        if ok and self.current_id == session_id:
            self.current_id = None
        return ok

    def clear(self, session_id: str) -> None:
        self.store.clear_messages(session_id)

    def messages(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_messages(session_id or self.current_id or "")

    def ensure_for_message(self, text: str) -> str:
        """确保存在当前会话；首条消息自动命名（与 Web 前端规则一致）。"""
        if not self.current_id:
            self.create_session("新会话")
        assert self.current_id is not None

        session = self.store.get_session(self.current_id)
        if session and session.get("title") in ("", "新会话"):
            title = _title_from_message(text)
            if title != "新会话":
                self.store.rename_session(self.current_id, title)
        return self.current_id
