from __future__ import annotations

from pathlib import Path

from sjtu_agent.web.attachment_store import AttachmentStore


def test_attachment_store_crud(tmp_path):
    store = AttachmentStore(tmp_path / "web_attachments")

    item = store.save("session-1", "notes.pdf", b"%PDF-1.4 test", "application/pdf")
    assert item["filename"] == "notes.pdf"
    assert Path(item["path"]).read_bytes() == b"%PDF-1.4 test"

    listed = store.list_for_session("session-1")
    assert len(listed) == 1

    assert store.get("missing") is None
    assert store.delete(item["id"]) is True
    assert not Path(item["path"]).exists()
    assert store.list_for_session("session-1") == []
