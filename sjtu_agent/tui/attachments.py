"""
sjtu_agent/tui/attachments.py — TUI 附件暂存（复用 Web 白名单存储）。

用户用 `/attach <本地路径>` 添加附件后，文件被复制到运行时
web_attachments/ 目录并登记到 AttachmentStore；发送消息时只把预解析
文本注入上下文，原始路径不会交给 Agent，和 WebUI 的越权修复一致。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from sjtu_agent.web.attachment_context import attachment_note_for_chat
from sjtu_agent.web.attachment_store import AttachmentStore

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class TuiAttachments:
    """TUI 当前会话的暂存附件管理。"""

    def __init__(self, store: AttachmentStore | None = None) -> None:
        self.store = store or AttachmentStore()

    def add(self, session_id: str, raw_path: str) -> tuple[dict[str, Any] | None, str | None]:
        path = Path(raw_path or "").expanduser()
        if not path.exists():
            return None, f"文件不存在：{raw_path}"
        if not path.is_file():
            return None, f"不是文件：{raw_path}"
        try:
            size = path.stat().st_size
        except OSError as exc:
            return None, f"无法读取文件：{exc}"
        if size > MAX_ATTACHMENT_BYTES:
            return None, f"附件超过 20MB 限制：{raw_path}"

        try:
            data = path.read_bytes()
        except OSError as exc:
            return None, f"读取文件失败：{exc}"

        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        item = self.store.save(session_id, path.name, data, mime_type)
        return item, None

    def staged(self, session_id: str) -> list[dict[str, Any]]:
        return self.store.list_for_session(session_id)

    def remove(self, attachment_id: str) -> bool:
        return self.store.delete(attachment_id)

    def clear_session(self, session_id: str) -> None:
        for item in self.staged(session_id):
            self.store.delete(item["id"])


def build_attachment_note(items: list[dict[str, Any]]) -> str:
    """把暂存附件预解析后拼进用户消息（Web / TUI 同一实现）。"""
    return attachment_note_for_chat(items)
