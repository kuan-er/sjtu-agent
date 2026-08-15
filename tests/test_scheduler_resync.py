from __future__ import annotations

from pathlib import Path

from sjtu_agent.scheduler import resync_daemons


def test_resync_skips_when_installed_and_path_unchanged(monkeypatch):
    deployment = {
        "backend": "taskschd",
        "services": ["feishu-bot"],
        "python_executable": Path("python").resolve(),
        "daily_report_time": (22, 0),
        "remind_interval": 60,
        "telegram_throttle": 10,
        "output_dir": "",
    }
    monkeypatch.setattr("sjtu_agent.scheduler.list_deployments", lambda: [deployment])
    monkeypatch.setattr(
        "sjtu_agent.scheduler.daemon_status",
        lambda **_: {"all_installed": True, "services": [{"installed": True}]},
    )

    called = []
    monkeypatch.setattr(
        "sjtu_agent.scheduler.install_daemons",
        lambda **kwargs: called.append(kwargs) or {},
    )

    payload = resync_daemons(python_executable=deployment["python_executable"])
    assert payload["any_resynced"] is False
    assert called == []


def test_resync_reinstalls_when_service_missing(monkeypatch):
    deployment = {
        "backend": "taskschd",
        "services": ["feishu-bot"],
        "python_executable": Path("python").resolve(),
        "daily_report_time": (21, 30),
        "remind_interval": 120,
        "telegram_throttle": 10,
        "output_dir": "",
    }
    monkeypatch.setattr("sjtu_agent.scheduler.list_deployments", lambda: [deployment])
    monkeypatch.setattr(
        "sjtu_agent.scheduler.daemon_status",
        lambda **_: {"all_installed": False, "services": [{"installed": False}]},
    )

    called = []
    monkeypatch.setattr(
        "sjtu_agent.scheduler.install_daemons",
        lambda **kwargs: called.append(kwargs) or {},
    )

    payload = resync_daemons(python_executable=deployment["python_executable"])
    assert payload["any_resynced"] is True
    assert called
    kwargs = called[0]
    assert kwargs["service_names"] == ("feishu-bot",)
    assert kwargs["daily_report_time"] == (21, 30)
    assert kwargs["remind_interval"] == 120
