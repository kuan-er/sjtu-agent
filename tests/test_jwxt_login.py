"""Tests for ddl_checker JWXT session recovery (issue #198).

- fetch_schedule error text gives actionable repair steps and forbids the
  hallucinated "use the user's browser session" advice.
- _get_jwxt_cookies routing: credential-SSO fallback attempts when saved
  jAccount cookies are missing, and degrades gracefully without Playwright.
"""

import pytest

import ddl_checker as dc


def test_fetch_schedule_error_has_real_recovery_and_guard(monkeypatch):
    """无法取 session 的错误必须指向真实修复路径，并禁止浏览器接管幻觉。"""
    monkeypatch.setattr(dc, "_get_jwxt_cookies", lambda cfg: None)
    result = dc.fetch_schedule({}, refresh=True)

    assert "error" in result
    msg = result["error"]
    assert "sjtu-agent login" in msg          # 真实存在的修复命令
    assert "sjtu-agent setup" in msg          # 兜底核对入口
    # 防幻觉：明确禁止"用用户浏览器的 Cookie"这类不成立的方案（#198 实测）
    assert "读不到其会话" in msg


def test_get_jwxt_cookies_skips_playwright_without_any_inputs(monkeypatch):
    """无保存 cookie、无 jAccount cookie、无凭证 → 直接 None，不启动浏览器。"""
    called = {"pw": False}

    def _no_pw(*a, **kw):
        called["pw"] = True
        raise AssertionError("不应启动 Playwright")

    monkeypatch.delenv("JACCOUNT_USERNAME", raising=False)
    monkeypatch.delenv("JACCOUNT_PASSWORD", raising=False)
    monkeypatch.setattr(dc, "sync_playwright", _no_pw)

    assert dc._get_jwxt_cookies({}) is None
    assert called["pw"] is False


def test_get_jwxt_cookies_credential_fallback_attempts_and_degrades(monkeypatch):
    """有凭证但无 Playwright → 尝试路径 2 并优雅降级返回 None（不抛异常）。"""
    monkeypatch.setenv("JACCOUNT_USERNAME", "zhangsan")
    monkeypatch.setenv("JACCOUNT_PASSWORD", "secret")

    def _broken_pw(*a, **kw):
        raise RuntimeError("playwright unavailable in test")

    monkeypatch.setattr(dc, "sync_playwright", _broken_pw)
    assert dc._get_jwxt_cookies({}) is None
