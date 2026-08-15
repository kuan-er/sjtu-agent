from __future__ import annotations

from pathlib import Path

from sjtu_agent.tui.attachments import TuiAttachments, build_attachment_note
from sjtu_agent.web.attachment_store import AttachmentStore


def _make_manager(tmp_path):
    store = AttachmentStore(tmp_path / "web_attachments")
    return TuiAttachments(store)


def test_attach_copies_file_into_whitelisted_store(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("本地文件内容", encoding="utf-8")
    manager = _make_manager(tmp_path)

    item, error = manager.add("session-1", str(source))
    assert error is None
    assert item is not None
    assert item["filename"] == "notes.txt"
    assert Path(item["path"]).is_relative_to(tmp_path / "web_attachments")
    assert Path(item["path"]).read_text(encoding="utf-8") == "本地文件内容"


def test_attach_rejects_missing_and_oversized(tmp_path):
    manager = _make_manager(tmp_path)
    _, error = manager.add("session-1", str(tmp_path / "missing.png"))
    assert "不存在" in error

    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * (21 * 1024 * 1024))
    _, error = manager.add("session-1", str(big))
    assert "20MB" in error


def test_build_attachment_note_preparses_content(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sjtu_agent.parsing.parse_file",
        lambda path, **kwargs: {"ok": True, "content": "预解析内容"},
    )
    doc = tmp_path / "notes.pdf"
    doc.write_bytes(b"%PDF fake")
    item = {
        "filename": "notes.pdf",
        "mime_type": "application/pdf",
        "size": 10,
        "path": str(doc),
    }
    note = build_attachment_note([item])
    assert "预解析内容" in note
    assert "已预解析" in note
    assert "parse_local_file" not in note


def test_remove_and_clear(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")
    manager = _make_manager(tmp_path)
    item, _ = manager.add("s1", str(source))
    assert len(manager.staged("s1")) == 1

    assert manager.remove(item["id"]) is True
    assert manager.staged("s1") == []

    manager.add("s2", str(source))
    manager.clear_session("s2")
    assert manager.staged("s2") == []
