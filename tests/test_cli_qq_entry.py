from __future__ import annotations

import argparse

import sjtu_agent.cli as cli


def test_cmd_qq_bot_dispatches_to_run_script(monkeypatch):
    called: dict[str, object] = {}

    def _fake_run_script(name: str, script_args):
        called["name"] = name
        called["args"] = list(script_args or [])
        return 0

    monkeypatch.setattr(cli, "_run_script", _fake_run_script)

    rc = cli._cmd_qq_bot(argparse.Namespace(script_args=["--test"]))
    assert rc == 0
    assert called["name"] == "qq_bot"
    assert called["args"] == ["--test"]


def test_run_script_missing_file_returns_error(monkeypatch, capsys):
    def _raise_missing(_name: str):
        raise FileNotFoundError("script not found for 'qq_bot'")

    monkeypatch.setattr(cli, "_resolve_script_path", _raise_missing)

    rc = cli._run_script("qq_bot", ["--test"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "script not found for 'qq_bot'" in captured.err


def test_cmd_install_parse_backends_dispatch(monkeypatch):
    called: dict[str, object] = {}

    def _fake_run_script(name: str, script_args):
        called["name"] = name
        called["args"] = list(script_args or [])
        return 0

    monkeypatch.setattr(cli, "_run_script", _fake_run_script)

    rc = cli._cmd_install_parse_backends(
        argparse.Namespace(backend="pdf_ocr", upgrade=True)
    )
    assert rc == 0
    assert called["name"] == "install_parse_backends"
    assert called["args"] == ["--backend", "pdf_ocr", "--upgrade"]
