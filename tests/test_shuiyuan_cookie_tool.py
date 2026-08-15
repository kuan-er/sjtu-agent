from __future__ import annotations

import json

from sjtu_agent.agent.tools import _core as core


def test_parse_cookie_header():
    cookies = core._parse_shuiyuan_cookie_text("_forum_session=abc; _t=def;")
    assert cookies == [
        {"name": "_forum_session", "value": "abc"},
        {"name": "_t", "value": "def"},
    ]


def test_parse_bare_token_candidates():
    cookies = core._parse_shuiyuan_cookie_text("bare-token")
    assert {c["name"] for c in cookies} == {"_forum_session", "_t", "_discourse_session"}


def test_save_valid_bare_cookie(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(core, "CONFIG_PATH", config_path)
    monkeypatch.setattr(core, "_shuiyuan_session_is_valid", lambda cookies: cookies.get("_forum_session") == "token")

    result = core.tool_save_shuiyuan_cookie("token")
    assert result.get("success") is True
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["shuiyuan_cookies"] == {"_forum_session": "token"}


def test_save_rejects_invalid_cookie(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(core, "CONFIG_PATH", config_path)
    monkeypatch.setattr(core, "_shuiyuan_session_is_valid", lambda cookies: False)

    result = core.tool_save_shuiyuan_cookie("_forum_session=bad")
    assert result.get("success") is not True
    assert "校验未通过" in result.get("error", "")
