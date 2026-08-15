from __future__ import annotations

import json

from sjtu_agent.tui.commands import command_candidates
from sjtu_agent.tui.messages import (
    COMMAND_RESULT_MARKER,
    display_text,
    parse_command_result,
    strip_date_context,
)
from sjtu_agent.tui.session_model import TuiSessionModel
from sjtu_agent.web.session_store import SessionStore


def _make_model(tmp_path):
    store = SessionStore(tmp_path / "web_sessions.sqlite3")
    return TuiSessionModel(store)


def test_session_model_creates_and_auto_titles(tmp_path):
    model = _make_model(tmp_path)
    assert model.current_id is None

    sid = model.ensure_for_message("帮我查一下明天的 DDL")
    assert sid == model.current_id
    session = model.get_current()
    assert session["title"] == "帮我查一下明天的 DDL"

    # 第二条消息不改名
    old = model.ensure_for_message("再帮我看看课表")
    assert old == sid
    assert model.get_current()["title"] == "帮我查一下明天的 DDL"


def test_session_model_delete_and_clear(tmp_path):
    model = _make_model(tmp_path)
    sid = model.ensure_for_message("第一条")
    model.store.append_message(sid, "user", "你好")
    model.store.append_message(sid, "assistant", "你好")

    assert len(model.messages(sid)) == 2
    model.clear(sid)
    assert model.messages(sid) == []

    assert model.delete(sid) is True
    assert model.current_id is None


def test_messages_parse_command_result():
    payload = {"view": "dining", "text": "# 推荐", "data": {"ok": True}}
    encoded = COMMAND_RESULT_MARKER + json.dumps(payload, ensure_ascii=False)
    assert parse_command_result(encoded) == payload
    assert parse_command_result("普通文本") is None
    assert display_text(encoded) == "# 推荐"
    assert display_text("普通文本") == "普通文本"


def test_strip_date_context_removes_injected_block():
    text = "问题\n\n## 当前时间\n现在：2026年08月16日 00:00，星期日。\n\n回答"
    assert "当前时间" not in strip_date_context(text)
    assert "问题" in strip_date_context(text)
    assert "回答" in strip_date_context(text)


def test_command_candidates_matches_webui_rules():
    names = [c["value"] for c in command_candidates("/")]
    assert "/hw" in names
    assert "/news" in names

    hw_variants = {c["value"] for c in command_candidates("/hw d")}
    assert "/hw do 3" in hw_variants
    assert "/hw due 7" in hw_variants

    assert command_candidates("普通消息") == []
    assert command_candidates("/does-not-exist") == []
