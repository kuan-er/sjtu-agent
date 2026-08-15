from __future__ import annotations

import asyncio
import threading
from pathlib import Path

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
            app.schedule_worker(app.flush_stream(), group="test")
            assert calls
            assert calls[-1]["exit_on_error"] is False

            app.render_event({"kind": "token", "text": "hello"}, app.session_id)
            assert app.stream_flush_timer is not None

    _run(scenario())


def test_high_frequency_stream_does_not_crash_app(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    model.create_session("新会话")

    def fake_stream(user_message, session_id=None):
        for index in range(200):
            yield {"kind": "token", "text": f"token-{index} "}
            if index % 20 == 0:
                import time
                time.sleep(0.005)
        yield {"kind": "done"}

    monkeypatch.setattr("sjtu_agent.tui.app.iter_chat_events", fake_stream)

    async def scenario():
        app = build_app()
        app.model = model
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            prompt.focus()
            prompt.value = "stress"
            await pilot.press("enter")
            for _ in range(200):
                if not app.busy:
                    break
                await pilot.pause(0.05)

            assert app.busy is False
            stream = app.query_one("#stream-markdown", Markdown)
            assert "token-199" in stream.source

    _run(scenario())


def test_rename_session_modal(tmp_path):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    session_id = model.create_session("旧名字")["id"]

    async def scenario():
        app = build_app()
        app.model = model
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+r")
            await pilot.pause()
            rename_input = app.screen.query_one("#rename-input", Input)
            rename_input.value = "新名字"
            await pilot.press("enter")
            for _ in range(20):
                if model.get_current()["title"] == "新名字":
                    break
                await pilot.pause(0.05)

            assert model.get_current()["title"] == "新名字"
            assert model.get_current()["id"] == session_id

    _run(scenario())


def test_delete_session_modal_creates_replacement(tmp_path):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    old_id = model.create_session("待删除")["id"]

    async def scenario():
        app = build_app()
        app.model = model
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+d")
            await pilot.pause()
            await pilot.press("y")
            for _ in range(20):
                if model.store.get_session(old_id) is None:
                    break
                await pilot.pause(0.05)

            assert model.store.get_session(old_id) is None
            assert model.list_sessions()
            assert app.session_id == model.list_sessions()[0]["id"]

    _run(scenario())


def test_attach_command_uses_whitelisted_store_and_preparses(tmp_path, monkeypatch):
    from sjtu_agent.tui.attachments import TuiAttachments
    from sjtu_agent.web.attachment_store import AttachmentStore

    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    model.create_session("新会话")
    source = tmp_path / "作业.pdf"
    source.write_bytes(b"%PDF fake")

    sent_messages = []

    def fake_stream(session_id, user_message):
        sent_messages.append(user_message)
        yield {"kind": "done"}

    monkeypatch.setattr("sjtu_agent.tui.app.iter_chat_events", fake_stream)
    monkeypatch.setattr(
        "sjtu_agent.parsing.parse_file",
        lambda path, **kwargs: {"ok": True, "content": "预解析出的作业内容"},
    )

    async def scenario():
        app = build_app()
        app.model = model
        app.attachments = TuiAttachments(AttachmentStore(tmp_path / "web_attachments"))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            prompt = app.query_one("#prompt", Input)
            prompt.focus()
            prompt.value = f"/attach {source}"
            await pilot.press("enter")
            await pilot.pause()
            assert len(app.staged_attachment_ids) == 1
            item = app.attachments.store.get(app.staged_attachment_ids[0])
            assert Path(item["path"]).is_relative_to(tmp_path / "web_attachments")

            prompt.focus()
            prompt.value = "帮我看看附件"
            await pilot.press("enter")
            for _ in range(20):
                if not app.busy:
                    break
                await pilot.pause(0.05)

            assert sent_messages
            assert "预解析出的作业内容" in sent_messages[-1]
            assert str(source) not in sent_messages[-1]

    _run(scenario())


def test_attachment_parsing_runs_off_the_ui_thread(tmp_path, monkeypatch):
    from sjtu_agent.tui.attachments import TuiAttachments
    from sjtu_agent.web.attachment_store import AttachmentStore

    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    model = TuiSessionModel(store)
    model.create_session("新会话")
    source = tmp_path / "image.png"
    source.write_bytes(b"fake-image")

    started = threading.Event()
    release = threading.Event()
    sent_messages = []

    def fake_parse(path, **kwargs):
        started.set()
        release.wait(timeout=10)
        return {"ok": True, "content": "图片解析内容"}

    def fake_stream(session_id, user_message):
        sent_messages.append(user_message)
        yield {"kind": "done"}

    monkeypatch.setattr("sjtu_agent.parsing.parse_file", fake_parse)
    monkeypatch.setattr("sjtu_agent.tui.app.iter_chat_events", fake_stream)

    async def scenario():
        app = build_app()
        app.model = model
        app.attachments = TuiAttachments(AttachmentStore(tmp_path / "web_attachments"))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)

            prompt.focus()
            prompt.value = f"/attach {source}"
            await pilot.press("enter")
            await pilot.pause()

            prompt.focus()
            prompt.value = "看图"
            await pilot.press("enter")
            for _ in range(50):
                if started.is_set():
                    break
                await pilot.pause(0.05)

            assert started.is_set()
            assert app.busy is True
            assert app.query_one("#attach-progress", Markdown) is not None

            release.set()
            for _ in range(50):
                if not app.busy:
                    break
                await pilot.pause(0.05)

            assert sent_messages
            assert "图片解析内容" in sent_messages[-1]

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
