from __future__ import annotations

import io
import json
import tarfile

import pytest

from sjtu_agent import config_transfer as ct


def _patch_sources(monkeypatch, source_dir):
    for name in ct._CORE_NAMES + ct._STATE_NAMES:
        monkeypatch.setattr(ct, "_PATH_BY_NAME", dict(ct._PATH_BY_NAME))
        ct._PATH_BY_NAME[name] = source_dir / name
    monkeypatch.setattr(ct, "CONFIG_PATH", source_dir / "config.json")
    monkeypatch.setattr(ct, "ENV_PATH", source_dir / ".env")
    monkeypatch.setattr(ct, "AGENT_CONFIG_PATH", source_dir / "agent_config.json")
    monkeypatch.setattr(ct, "REMINDERS_PATH", source_dir / "reminders.json")
    monkeypatch.setattr(ct, "USER_PROFILE_PATH", source_dir / "user_profile.json")
    monkeypatch.setattr(ct, "DINING_HISTORY_PATH", source_dir / "dining_history.json")


def _make_source(source_dir, with_state=False):
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "config.json").write_text(
        json.dumps({"feishu_app_id": "cli_test", "feishu_app_secret": "secret"}),
        encoding="utf-8",
    )
    (source_dir / ".env").write_text(
        "ZHIYUAN_API_KEY=sk-test\nJACCOUNT_USERNAME=zhangsan\nJACCOUNT_PASSWORD=secret\n",
        encoding="utf-8",
    )
    (source_dir / "agent_config.json").write_text(
        json.dumps({"model": "deepseek-chat", "api_key": "sk-agent"}),
        encoding="utf-8",
    )
    if with_state:
        (source_dir / "reminders.json").write_text(json.dumps([{"id": 1}]), encoding="utf-8")


def test_export_import_roundtrip(monkeypatch, tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _make_source(source)
    _patch_sources(monkeypatch, source)

    data = ct.export_bytes()
    assert not ct.is_encrypted(data)

    report = ct.import_bytes(data, target_dir=target)
    assert set(report["written"]) == {"config.json", ".env", "agent_config.json"}
    assert json.loads((target / "config.json").read_text(encoding="utf-8"))["feishu_app_id"] == "cli_test"
    assert "ZHIYUAN_API_KEY=sk-test" in (target / ".env").read_text(encoding="utf-8")


def test_encrypted_roundtrip_and_wrong_password(monkeypatch, tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _make_source(source)
    _patch_sources(monkeypatch, source)

    data = ct.export_bytes(encrypt_password="hunter2")
    assert ct.is_encrypted(data)

    with pytest.raises(ValueError):
        ct.import_bytes(data, target_dir=target, decrypt_password="wrong")

    report = ct.import_bytes(data, target_dir=target, decrypt_password="hunter2")
    assert report["written"]


def test_import_rejects_path_traversal(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        manifest = json.dumps({
            "format": "sjtu-agent-config",
            "version": 1,
            "files": ["../evil.json"],
        }).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))

    with pytest.raises(ValueError, match="不允许"):
        ct.import_bytes(buffer.getvalue(), target_dir=tmp_path / "target")


def test_import_invalid_json_is_rejected(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        manifest = json.dumps({
            "format": "sjtu-agent-config",
            "version": 1,
            "files": ["config.json"],
        }).encode("utf-8")
        for name, payload in (
            ("manifest.json", manifest),
            ("config.json", b"{not-json"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(ValueError, match="JSON"):
        ct.import_bytes(buffer.getvalue(), target_dir=tmp_path / "target")


def test_dry_run_then_import_backs_up_existing(monkeypatch, tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _make_source(source)
    _patch_sources(monkeypatch, source)

    target.mkdir()
    (target / "config.json").write_text(json.dumps({"old": True}), encoding="utf-8")
    data = ct.export_bytes()

    dry = ct.import_bytes(data, target_dir=target, dry_run=True)
    assert all(item["action"].startswith("would") for item in dry["files"])
    assert json.loads((target / "config.json").read_text(encoding="utf-8")) == {"old": True}

    report = ct.import_bytes(data, target_dir=target)
    assert json.loads((target / "config.json").read_text(encoding="utf-8"))["feishu_app_id"] == "cli_test"
    written_config = next(item for item in report["files"] if item["name"] == "config.json")
    assert written_config["backed_up"] is True
    assert written_config["backup_path"]


def test_export_fails_without_any_config(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    _patch_sources(monkeypatch, empty)
    with pytest.raises(RuntimeError, match="没有可导出"):
        ct.export_bytes()
