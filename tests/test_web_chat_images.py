"""Web/TUI 附件分类与图片块构造。"""

from __future__ import annotations

import base64

from sjtu_agent.web import chat_images as ci

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32


def _item(tmp_path, name: str, data: bytes, mime: str) -> dict:
    path = tmp_path / name
    path.write_bytes(data)
    return {
        "id": name,
        "filename": name,
        "path": str(path),
        "mime_type": mime,
        "size": len(data),
    }


def test_split_attachments_by_mime(tmp_path):
    png = _item(tmp_path, "a.png", PNG, "image/png")
    pdf = _item(tmp_path, "b.pdf", b"%PDF-1.4", "application/pdf")
    images, others = ci.split_attachments([png, pdf])
    assert images == [png]
    assert others == [pdf]


def test_build_image_blocks_openai_uses_real_mime(tmp_path):
    png = _item(tmp_path, "a.png", PNG, "image/png")
    blocks = ci.build_image_blocks([png], "openai")
    assert blocks[0]["type"] == "image_url"
    url = blocks[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG


def test_build_image_blocks_anthropic_uses_source(tmp_path):
    jpeg = _item(tmp_path, "b.jpg", JPEG, "image/jpeg")
    blocks = ci.build_image_blocks([jpeg], "anthropic")
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/jpeg"
    assert blocks[0]["source"]["data"] == base64.b64encode(JPEG).decode()


def test_build_image_blocks_skips_unreadable(tmp_path):
    blocks = ci.build_image_blocks([{"filename": "x.png", "path": str(tmp_path / "nope.png")}], "openai")
    assert blocks == []


def test_native_note_mentions_images_and_paths(tmp_path):
    png = _item(tmp_path, "a.png", PNG, "image/png")
    note = ci.native_image_note([png])
    assert "已随本条消息附上" in note
    assert str(png["path"]) in note


def test_text_fallback_note_explains_conversion(tmp_path):
    png = _item(tmp_path, "a.png", PNG, "image/png")
    note = ci.text_fallback_note([png])
    assert "不能直接读图" in note
    assert "a.png" in note
