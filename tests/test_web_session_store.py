from __future__ import annotations

from sjtu_agent.web.session_store import SessionStore


def test_session_store_crud(tmp_path):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")

    session = store.create_session("测试会话")
    session_id = session["id"]
    assert session["title"] == "测试会话"

    store.append_message(session_id, "user", "你好")
    store.append_message(session_id, "assistant", "你好，有什么可以帮你？")

    listed = store.list_sessions()
    assert len(listed) == 1
    assert listed[0]["message_count"] == 2

    messages = store.list_messages(session_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]

    renamed = store.rename_session(session_id, "改名")
    assert renamed["title"] == "改名"

    store.clear_messages(session_id)
    assert store.list_messages(session_id) == []

    assert store.delete_session(session_id) is True
    assert store.list_sessions() == []
