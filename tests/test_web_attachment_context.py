from __future__ import annotations

import pathlib

import sjtu_agent.web.server as server


def test_image_attachment_uses_vision_model(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"fake-image")

    calls = {}
    monkeypatch.setattr(
        "sjtu_agent.vision.load_vision_config",
        lambda: {"model": "qwen-vl-max"},
    )
    monkeypatch.setattr(
        "sjtu_agent.vision.analyze_image",
        lambda data, prompt: "图片里写着 DDL：5月20日交作业",
    )

    item = {
        "filename": "image.png",
        "mime_type": "image/png",
        "size": 10,
        "path": str(image_path),
    }
    note = server._attachment_note_for_chat([item])
    assert "DDL" in note
    assert "已预解析" in note
    assert "parse_local_file" not in note


def test_document_attachment_uses_parse_file(monkeypatch, tmp_path):
    file_path = tmp_path / "notes.pdf"
    file_path.write_bytes(b"%PDF fake")

    monkeypatch.setattr(
        "sjtu_agent.parsing.parse_file",
        lambda path, **kwargs: {"ok": True, "content": "PDF 内容摘要"},
    )

    item = {
        "filename": "notes.pdf",
        "mime_type": "application/pdf",
        "size": 10,
        "path": str(file_path),
    }
    note = server._attachment_note_for_chat([item])
    assert "PDF 内容摘要" in note
