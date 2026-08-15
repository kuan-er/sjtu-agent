from __future__ import annotations

import argparse

import sjtu_agent.cli as cli


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        "services": None,
        "python_executable": "python",
        "daily_report_time": (22, 0),
        "remind_interval": 60,
        "write_only": False,
        "no_browser": False,
        "backend": "taskschd",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_should_open_web_by_default():
    assert cli._should_open_web_after_install(_args()) is True


def test_should_open_web_when_web_explicitly_selected():
    assert cli._should_open_web_after_install(_args(services=["feishu-bot", "web"])) is True


def test_should_not_open_web_when_web_not_selected():
    assert cli._should_open_web_after_install(_args(services=["feishu-bot"])) is False


def test_should_not_open_web_with_no_browser():
    assert cli._should_open_web_after_install(_args(no_browser=True)) is False
    assert cli._should_open_web_after_install(_args(services=["web"], no_browser=True)) is False


def test_cmd_install_daemons_skips_browser_when_web_not_selected(monkeypatch, capsys):
    monkeypatch.setattr(cli, "install_daemons", lambda **kwargs: {"services": []})
    monkeypatch.setattr(cli, "print_json", lambda payload: None)

    rc = cli._cmd_install_daemons(_args(services=["feishu-bot"]))
    assert rc == 0
    assert "跳过浏览器启动" in capsys.readouterr().out


def test_cmd_install_daemons_no_browser(monkeypatch):
    monkeypatch.setattr(cli, "install_daemons", lambda **kwargs: {"services": []})
    monkeypatch.setattr(cli, "print_json", lambda payload: None)

    rc = cli._cmd_install_daemons(_args(services=["web"], no_browser=True))
    assert rc == 0
