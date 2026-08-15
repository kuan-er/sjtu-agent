from __future__ import annotations

import sys

from sjtu_agent.scheduler import manifest as manifest_module


def _patch_manifest_path(tmp_path, monkeypatch):
    manifest_path = tmp_path / ".daemon_manifest.json"
    monkeypatch.setattr(manifest_module, "DAEMON_MANIFEST_PATH", manifest_path)
    return manifest_path


def test_manifest_remember_and_list(tmp_path, monkeypatch):
    path = _patch_manifest_path(tmp_path, monkeypatch)

    manifest_module.remember_deployment(
        "taskschd",
        ("feishu-bot", "daily-report"),
        r"C:\repo\.venv\Scripts\python.exe",
        daily_report_time=(22, 30),
        remind_interval=120,
    )

    assert path.exists()
    deployments = manifest_module.list_deployments()
    assert len(deployments) == 1
    deployment = deployments[0]
    assert deployment["backend"] == "taskschd"
    assert deployment["services"] == ["daily-report", "feishu-bot"]
    assert deployment["daily_report_time"] == (22, 30)
    assert deployment["remind_interval"] == 120


def test_manifest_forget_services_and_backend(tmp_path, monkeypatch):
    _patch_manifest_path(tmp_path, monkeypatch)

    manifest_module.remember_deployment("psmux", ("feishu-bot", "web"), sys.executable)
    manifest_module.forget_deployment("psmux", ("web",))
    deployments = manifest_module.list_deployments()
    assert deployments[0]["services"] == ["feishu-bot"]

    manifest_module.forget_deployment("psmux")
    assert manifest_module.list_deployments() == []


def test_manifest_survives_corrupt_file(tmp_path, monkeypatch):
    path = _patch_manifest_path(tmp_path, monkeypatch)
    path.write_text("{not valid json", encoding="utf-8")

    assert manifest_module.load_manifest() == {"version": 1, "backends": {}}
