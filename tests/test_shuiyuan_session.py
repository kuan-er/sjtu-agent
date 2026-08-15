from __future__ import annotations

import json

from sjtu_agent.agent.tools import _core as core


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_session_is_valid_true(monkeypatch):
    def fake_get(url, headers, timeout):
        assert url == "https://shuiyuan.sjtu.edu.cn/session/current.json"
        assert "_forum_session=abc" in headers["Cookie"]
        return _FakeResponse(200, {"current_user": {"id": 1}})

    monkeypatch.setattr(core.requests, "get", fake_get)
    assert core._shuiyuan_session_is_valid({"_forum_session": "abc"}) is True


def test_session_is_valid_false_when_anonymous(monkeypatch):
    monkeypatch.setattr(
        core.requests,
        "get",
        lambda *a, **kw: _FakeResponse(200, {"current_user": None}),
    )
    assert core._shuiyuan_session_is_valid({"_forum_session": "abc"}) is False


def test_setup_shuiyuan_reuses_valid_cookie(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"shuiyuan_cookies": {"_forum_session": "still-valid"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(core, "CONFIG_PATH", config_path)
    monkeypatch.setattr(core, "_shuiyuan_session_is_valid", lambda cookies: True)
    monkeypatch.setattr(
        core,
        "_setup_shuiyuan_session",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not login")),
    )

    result = core.tool_setup_shuiyuan()
    assert result.get("success") is True
    assert "跳过重新登录" in result.get("message", "")
