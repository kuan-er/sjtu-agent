"""tests/test_scheduler_restart.py — restart_daemons 单元测试（facade 层）。"""

from __future__ import annotations

from pathlib import Path

from sjtu_agent.scheduler import restart_daemons


def _deployment(backend="taskschd", services=("feishu-bot",), **extra):
    dep = {
        "backend": backend,
        "services": list(services),
        "python_executable": Path("python").resolve(),
        "daily_report_time": (22, 0),
        "remind_interval": 60,
        "telegram_throttle": 10,
        "output_dir": "",
    }
    dep.update(extra)
    return dep


def test_restart_stops_then_reinstalls_with_manifest_params(monkeypatch):
    """restart = 先 uninstall（停止）再按清单参数 install（重建并启动）。"""
    dep = _deployment(daily_report_time=(21, 30), remind_interval=120)
    monkeypatch.setattr("sjtu_agent.scheduler.list_deployments", lambda: [dep])

    calls = {"uninstall": [], "install": []}
    monkeypatch.setattr(
        "sjtu_agent.scheduler.uninstall_daemons",
        lambda **kw: calls["uninstall"].append(kw) or {"removed": []},
    )
    monkeypatch.setattr(
        "sjtu_agent.scheduler.install_daemons",
        lambda **kw: calls["install"].append(kw) or {},
    )

    payload = restart_daemons()
    assert payload["any_restarted"] is True
    assert calls["uninstall"][0]["service_names"] == ("feishu-bot",)
    inst = calls["install"][0]
    assert inst["service_names"] == ("feishu-bot",)
    assert inst["daily_report_time"] == (21, 30)   # 清单参数被保留
    assert inst["remind_interval"] == 120


def test_restart_uses_deployment_backend(monkeypatch):
    """按清单记录的 backend（如 psmux）重启，而不是 Windows 默认 taskschd。"""
    dep = _deployment(backend="psmux")
    monkeypatch.setattr("sjtu_agent.scheduler.list_deployments", lambda: [dep])
    calls = {"uninstall": [], "install": []}
    monkeypatch.setattr(
        "sjtu_agent.scheduler.uninstall_daemons",
        lambda **kw: calls["uninstall"].append(kw) or {},
    )
    monkeypatch.setattr(
        "sjtu_agent.scheduler.install_daemons",
        lambda **kw: calls["install"].append(kw) or {},
    )

    restart_daemons()
    assert calls["uninstall"][0]["backend"] == "psmux"
    assert calls["install"][0]["backend"] == "psmux"


def test_restart_selected_subset_of_deployment(monkeypatch):
    """--services 只重启清单里匹配的子集，未选中的服务不动。"""
    dep = _deployment(services=("feishu-bot", "web"))
    monkeypatch.setattr("sjtu_agent.scheduler.list_deployments", lambda: [dep])
    calls = {"uninstall": [], "install": []}
    monkeypatch.setattr(
        "sjtu_agent.scheduler.uninstall_daemons",
        lambda **kw: calls["uninstall"].append(kw) or {},
    )
    monkeypatch.setattr(
        "sjtu_agent.scheduler.install_daemons",
        lambda **kw: calls["install"].append(kw) or {},
    )

    restart_daemons(service_names=("web",))
    assert calls["uninstall"][0]["service_names"] == ("web",)
    assert calls["install"][0]["service_names"] == ("web",)


def test_restart_uninstalled_service_installs_fresh(monkeypatch):
    """请求的服务从未安装过 → 直接按默认参数安装（等价首次安装并启动）。"""
    monkeypatch.setattr("sjtu_agent.scheduler.list_deployments", lambda: [])
    calls = []
    monkeypatch.setattr(
        "sjtu_agent.scheduler.install_daemons",
        lambda **kw: calls.append(kw) or {},
    )

    payload = restart_daemons(service_names=("remind-check",), backend="psmux")
    assert calls, "未安装的服务也应触发安装"
    assert calls[0]["service_names"] == ("remind-check",)
    assert calls[0]["backend"] == "psmux"
    assert payload["any_restarted"] is True


def test_restart_reports_errors_per_deployment(monkeypatch):
    """单个部署重启失败不阻断其他部署，错误信息透出。"""
    dep_ok = _deployment(services=("feishu-bot",))
    dep_bad = _deployment(backend="systemd", services=("remind-check",))
    monkeypatch.setattr(
        "sjtu_agent.scheduler.list_deployments", lambda: [dep_ok, dep_bad]
    )
    monkeypatch.setattr(
        "sjtu_agent.scheduler.uninstall_daemons", lambda **kw: {}
    )

    def _bad_install(**kw):
        if kw.get("service_names") == ("remind-check",):
            raise RuntimeError("systemctl unavailable")
        return {}

    monkeypatch.setattr("sjtu_agent.scheduler.install_daemons", _bad_install)

    payload = restart_daemons()
    assert payload["any_restarted"] is True  # 至少一个成功
    by_name = {r["services"][0]: r for r in payload["results"]}
    assert by_name["feishu-bot"]["restarted"] is True
    assert by_name["remind-check"]["restarted"] is False
    assert "systemctl unavailable" in by_name["remind-check"]["error"]


# ── CLI 层：sjtu-agent daemons restart ──────────────────────────────────────

def test_cmd_daemons_restart_forwards_services_backend(monkeypatch, capsys):
    import argparse

    import sjtu_agent.cli as cli

    seen = {}

    def fake_restart(service_names=None, backend="taskschd", **kw):
        seen["service_names"] = service_names
        seen["backend"] = backend
        return {
            "platform": "test",
            "python_executable": "py",
            "manifest_path": "x",
            "results": [
                {"backend": backend, "services": list(service_names or []), "restarted": True},
            ],
            "any_restarted": True,
        }

    monkeypatch.setattr(cli, "restart_daemons", fake_restart)
    monkeypatch.setattr(cli, "print_json", lambda payload: None)

    args = argparse.Namespace(services=["feishu-bot"], backend="psmux")
    rc = cli._cmd_daemons_restart(args)
    assert rc == 0
    assert seen == {"service_names": ("feishu-bot",), "backend": "psmux"}


def test_cmd_daemons_restart_returns_failure_code_on_error(monkeypatch):
    import argparse

    import sjtu_agent.cli as cli

    def fake_restart(service_names=None, backend="taskschd", **kw):
        return {
            "results": [
                {"backend": backend, "services": ["remind-check"], "restarted": False,
                 "error": "boom"},
            ],
            "any_restarted": False,
        }

    monkeypatch.setattr(cli, "restart_daemons", fake_restart)
    monkeypatch.setattr(cli, "print_json", lambda payload: None)

    rc = cli._cmd_daemons_restart(argparse.Namespace(services=["remind-check"], backend="taskschd"))
    assert rc == 1