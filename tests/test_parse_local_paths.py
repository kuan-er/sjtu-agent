from __future__ import annotations

from pathlib import Path

from sjtu_agent.agent.tools import _core as core


def test_web_attachments_path_is_allowed(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(core, "ROOT", root)
    monkeypatch.setattr(core, "ASSIGNMENTS_DIR", root / "assignments")
    monkeypatch.setattr(core, "DATA_DIR", data_dir)

    allowed = core._resolve_allowed_local_file(str(data_dir / "web_attachments" / "a.png"))
    assert allowed == (data_dir / "web_attachments" / "a.png").resolve()


def test_runtime_credentials_are_still_rejected(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(core, "ROOT", root)
    monkeypatch.setattr(core, "ASSIGNMENTS_DIR", root / "assignments")
    monkeypatch.setattr(core, "DATA_DIR", data_dir)

    assert core._resolve_allowed_local_file(str(data_dir / "config.json")) is None
    assert core._resolve_allowed_local_file(str(data_dir / ".env")) is None


def test_feishu_media_path_is_allowed(monkeypatch, tmp_path):
    """飞书媒体暂存目录必须可读（否则 bot 解析图片/文件会'路径越权'）。"""
    root = tmp_path / "repo"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(core, "ROOT", root)
    monkeypatch.setattr(core, "ASSIGNMENTS_DIR", root / "assignments")
    monkeypatch.setattr(core, "DATA_DIR", data_dir)

    allowed = core._resolve_allowed_local_file(str(data_dir / "feishu_media" / "img_xxx.jpg"))
    assert allowed == (data_dir / "feishu_media" / "img_xxx.jpg").resolve()


def test_feishu_media_parse_via_tool_ok(monkeypatch, tmp_path):
    """飞书媒体目录中的文件能被 tool_parse_local_file 正常解析（回归：越权修复）。"""
    root = tmp_path / "repo"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(core, "ROOT", root)
    monkeypatch.setattr(core, "ASSIGNMENTS_DIR", root / "assignments")
    monkeypatch.setattr(core, "DATA_DIR", data_dir)

    media_dir = data_dir / "feishu_media"
    media_dir.mkdir(parents=True)
    f = media_dir / "note.txt"
    f.write_text("hello media", encoding="utf-8")

    from sjtu_agent.agent.tools import tool_parse_local_file

    r = tool_parse_local_file(str(f), max_chars=200, strategy="auto")
    assert "路径越权" not in str(r)
    assert "hello media" in str(r)
