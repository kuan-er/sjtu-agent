"""
sjtu_agent/web/attachment_store.py — Web GUI 附件存储

附件文件放在运行时数据目录 web_attachments/，元数据放在同目录 SQLite。
Web 前端使用 JSON base64 上传，避免手写 multipart 解析。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sjtu_agent.paths import DATA_DIR

ATTACH_DIR = DATA_DIR / "web_attachments"
DB_PATH = ATTACH_DIR / "attachments.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_session ON attachments(session_id, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(filename: str) -> str:
    name = Path(filename or "file").name
    return name if name and name not in {".", ".."} else "file"


class AttachmentStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = Path(base_dir or ATTACH_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "attachments.sqlite3"
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def save(self, session_id: str, filename: str, data: bytes, mime_type: str) -> dict[str, Any]:
        attachment_id = uuid.uuid4().hex[:16]
        safe_name = _safe_filename(filename)
        file_path = self.base_dir / f"{attachment_id}_{safe_name}"
        file_path.write_bytes(data)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO attachments(id, session_id, filename, mime_type, size, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (attachment_id, session_id, safe_name, mime_type or "application/octet-stream", len(data), now),
            )
        return self.get(attachment_id) or {}

    def get(self, attachment_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, session_id, filename, mime_type, size, created_at "
                "FROM attachments WHERE id = ?",
                (attachment_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["path"] = str(self.base_dir / f"{item['id']}_{item['filename']}")
        return item

    def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, session_id, filename, mime_type, size, created_at "
                "FROM attachments WHERE session_id = ? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, attachment_id: str) -> bool:
        item = self.get(attachment_id)
        if not item:
            return False
        with self._connect() as conn:
            conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        try:
            Path(item["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        return True
