from __future__ import annotations

import json

import pytest

from sjtu_agent.tui import engine
from sjtu_agent.web.session_store import SessionStore


def test_parse_sse_chunk_chat_events():
    chunk = "\n".join([
        'data: {"token":"你好"}',
        'data: {"tool_start":{"name":"get_ddls","input":{}}}',
        'data: {"approval_required":{"approval_id":"a1","tool_name":"send_email","arguments":{}}}',
        'data: {"tool_end":{"name":"get_ddls","result":"ok"}}',
        'data: {"error":"boom"}',
        "data: [DONE]",
    ])
    events = engine.parse_sse_chunk(chunk)
    assert [e["kind"] for e in events] == [
        "token",
        "tool_start",
        "approval_required",
        "tool_end",
        "error",
        "done",
    ]
    assert events[0]["text"] == "你好"
    assert events[2]["approval_id"] == "a1"


def test_parse_sse_chunk_command_events():
    chunk = "\n".join([
        'data: {"command_start":{"name":"/eat","raw":"/eat 徐汇"}}',
        'data: {"command_progress":{"stage":"running","message":"正在执行 /eat…"}}',
        'data: {"command_result":{"name":"/eat","view":"dining","text":"# 推荐","data":{"ok":true}}}',
        "data: [DONE]",
    ])
    events = engine.parse_sse_chunk(chunk)
    assert events[-1] == {"kind": "done"}
    result = events[2]
    assert result["kind"] == "command_result"
    assert result["view"] == "dining"
    assert result["data"] == {"ok": True}


def test_iter_chat_events_normalizes_stream(monkeypatch):
    def fake_stream(user_message, session_id=None):
        yield f'data: {json.dumps({"token": user_message})}\n\n'

    monkeypatch.setattr(engine, "_stream_chat", fake_stream)
    events = list(engine.iter_chat_events("s1", "hello"))
    assert events == [{"kind": "token", "text": "hello"}]


def test_iter_command_events_normalizes_stream(monkeypatch):
    def fake_stream(command, session_id=None):
        yield 'data: {"command_start":{"name":"/eat","raw":"/eat"}}\n\n'

    monkeypatch.setattr(engine, "_stream_command", fake_stream)
    events = list(engine.iter_command_events("s1", "/eat"))
    assert events[0]["name"] == "/eat"


def test_choose_event_stream_uses_command_path(monkeypatch):
    calls = []

    def fake_chat(user_message, session_id=None):
        calls.append(("chat", user_message))
        yield from ()

    def fake_command(command, session_id=None):
        calls.append(("command", command))
        yield from ()

    monkeypatch.setattr(engine, "_stream_chat", fake_chat)
    monkeypatch.setattr(engine, "_stream_command", fake_command)

    assert list(engine.choose_event_stream("s1", "/eat 徐汇")) == []
    assert calls == [("command", "/eat 徐汇")]

    list(engine.choose_event_stream("s1", "今天吃什么"))
    assert calls[-1] == ("chat", "今天吃什么")


def test_new_store_reuses_web_session_store():
    store = engine.new_store()
    assert isinstance(store, SessionStore)
    assert store.db_path.name == "web_sessions.sqlite3"


def test_tui_command_without_textual_reports_install_hint(monkeypatch, capsys):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "textual" or name.startswith("textual."):
            raise ImportError("No module named 'textual'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from sjtu_agent.tui.app import run_tui
    assert run_tui() == 1
    captured = capsys.readouterr()
    assert "pip install -e" in captured.err
