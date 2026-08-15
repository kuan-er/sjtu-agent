from __future__ import annotations

import asyncio

import pytest

textual = pytest.importorskip("textual")

from textual.containers import VerticalScroll  # noqa: E402
from textual.widgets import Input, ListView, Markdown  # noqa: E402

from sjtu_agent.tui.app import TEXTUAL_AVAILABLE, build_app  # noqa: E402
from sjtu_agent.tui.session_model import TuiSessionModel  # noqa: E402
from sjtu_agent.web.session_store import SessionStore  # noqa: E402

pytestmark = pytest.mark.skipif(not TEXTUAL_AVAILABLE, reason="Textual not installed")


def _run(coro):
    return asyncio.run(coro)


def test_chat_app_mounts_sessions_and_markdown_messages(tmp_path):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    session_id = model.create_session("测试会话")["id"]
    store.append_message(session_id, "user", "你好")
    store.append_message(session_id, "assistant", "**加粗**\n\n- 列表项")

    async def scenario():
        app = build_app()
        app.model = model
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            session_list = app.query_one("#sessions", ListView)
            assert len(session_list.children) >= 1

            messages = app.query_one("#messages", VerticalScroll)
            markdown_widgets = list(messages.query(Markdown))
            assert len(markdown_widgets) >= 3  # 标题 + 用户 + 助手
            assert app.query_one("#prompt", Input) is not None

    _run(scenario())


def test_streaming_reply_updates_markdown_widget(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    model.create_session("新会话")

    def fake_stream(user_message, session_id=None):
        yield {"kind": "token", "text": "# 标题\n\n"}
        yield {"kind": "token", "text": "**加粗** 和列表：\n\n- A\n- B"}
        yield {"kind": "done"}

    monkeypatch.setattr("sjtu_agent.tui.app.iter_chat_events", fake_stream)

    async def scenario():
        app = build_app()
        app.model = model
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            prompt.focus()
            prompt.value = "hello"
            await pilot.press("enter")
            for _ in range(20):
                if not app.busy:
                    break
                await pilot.pause(0.1)

            assert app.busy is False
            stream = app.query_one("#stream-markdown", Markdown)
            assert "加粗" in stream.source
            assert "列表" in stream.source

    _run(scenario())


def test_stream_exception_is_rendered_without_thread_crash(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    model.create_session("新会话")

    def fake_stream(user_message, session_id=None):
        yield {"kind": "token", "text": "部分内容"}
        raise RuntimeError("mock stream failure")

    monkeypatch.setattr("sjtu_agent.tui.app.iter_chat_events", fake_stream)

    async def scenario():
        app = build_app()
        app.model = model
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            prompt.focus()
            prompt.value = "hello"
            await pilot.press("enter")
            for _ in range(20):
                if not app.busy:
                    break
                await pilot.pause(0.1)

            assert app.busy is False
            stream = app.query_one("#stream-markdown", Markdown)
            assert "mock stream failure" in stream.source

    _run(scenario())


def test_ui_workers_never_exit_app(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    model.create_session("新会话")

    async def scenario():
        app = build_app()
        app.model = model
        calls = []

        def fake_run_worker(work, **kwargs):
            calls.append(kwargs)
            if hasattr(work, "close"):
                work.close()  # 测试替身不执行，显式关闭避免未 await 警告
            return None

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "run_worker", fake_run_worker)
            app.render_event({"kind": "token", "text": "hello"}, app.session_id)
            assert calls
            assert calls[-1]["exit_on_error"] is False
            assert calls[-1]["exclusive"] is True

    _run(scenario())


def test_command_suggestions_fill_input(tmp_path):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    model.create_session("新会话")

    async def scenario():
        app = build_app()
        app.model = model
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            prompt.focus()
            prompt.value = "/hw d"
            prompt.post_message(Input.Changed(prompt, "/hw d"))
            await pilot.pause()

            assert app.suggestions
            chosen = app.suggestions[app.suggestion_index]["value"]
            assert chosen.startswith("/hw d")
            await pilot.press("enter")
            assert prompt.value == chosen

    _run(scenario())
