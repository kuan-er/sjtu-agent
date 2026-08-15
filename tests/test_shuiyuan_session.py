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


class _FakePage:
    url = "https://shuiyuan.sjtu.edu.cn/"

    def goto(self, *args, **kwargs):
        return None

    def wait_for_url(self, *args, **kwargs):
        return None


class _FakeContext:
    def __init__(self):
        # Playwright 的 cookies() 返回 dict 列表，而不是对象。
        self._cookies = [
            {"name": "_forum_session", "value": "profile-session", "domain": ".shuiyuan.sjtu.edu.cn"},
            {"name": "_jarvis", "value": "profile-ja", "domain": ".jaccount.sjtu.edu.cn"},
        ]

    def add_cookies(self, cookies):
        return None

    def cookies(self):
        return self._cookies

    def new_page(self):
        return _FakePage()

    def close(self):
        return None


class _FakeChromium:
    def __init__(self):
        self.launch_kwargs = None

    def launch_persistent_context(self, **kwargs):
        self.launch_kwargs = kwargs
        return _FakeContext()

    def launch(self, **kwargs):
        raise AssertionError("should use persistent context")


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_setup_shuiyuan_uses_persistent_browser_profile(monkeypatch, tmp_path):
    import playwright.sync_api as playwright_api

    config_path = tmp_path / "config.json"
    profile_dir = tmp_path / "shuiyuan_browser_profile"
    monkeypatch.setattr(core, "CONFIG_PATH", config_path)
    monkeypatch.setattr(core, "SHUIYUAN_PROFILE_DIR", profile_dir)
    monkeypatch.setattr(core, "_shuiyuan_session_is_valid", lambda cookies: True)

    chromium = _FakeChromium()
    monkeypatch.setattr(
        playwright_api,
        "sync_playwright",
        lambda: _FakePlaywright(chromium),
    )

    result = core._setup_shuiyuan_session({}, "", "")
    assert result.get("success") is True
    assert chromium.launch_kwargs is not None
    assert chromium.launch_kwargs["user_data_dir"] == str(profile_dir)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["shuiyuan_cookies"]["_forum_session"] == "profile-session"
    assert saved["jaccount_cookies"]["_jarvis"] == "profile-ja"
