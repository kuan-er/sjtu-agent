"""Tests for A: shared log_turn helper (画像积累接入 feishu/wechat/qq)."""

from sjtu_agent.news_aggregator import profile
from sjtu_agent.bots._core import log_turn


def test_log_turn_calls_log_conversation(monkeypatch):
    calls = []
    monkeypatch.setattr(profile, "log_conversation", lambda u, r: calls.append((u, r)))
    log_turn("你好", "你好呀")
    assert calls == [("你好", "你好呀")]


def test_log_turn_empty_args(monkeypatch):
    calls = []
    monkeypatch.setattr(profile, "log_conversation", lambda u, r: calls.append((u, r)))
    log_turn("", "")
    assert calls == [("", "")]


def test_log_turn_silent_on_error(monkeypatch):
    def boom(u, r):
        raise RuntimeError("fail")

    monkeypatch.setattr(profile, "log_conversation", boom)
    log_turn("hi", "yo")  # 不应抛出


def test_wechat_capture_turn_logs(monkeypatch):
    from scripts import wechat_bot

    seen = {}
    monkeypatch.setattr(wechat_bot, "run_one_turn", lambda sess, text: "回复")
    monkeypatch.setattr(wechat_bot, "_log_turn", lambda u, r: seen.update(u=u, r=r))

    reply = wechat_bot._capture_turn({"messages": []}, "你好")
    assert reply == "回复"
    assert seen == {"u": "你好", "r": "回复"}
