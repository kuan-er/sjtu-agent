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
