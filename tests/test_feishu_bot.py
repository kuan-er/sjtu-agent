"""Tests for feishu_bot.py core functions — no WebSocket connection required."""

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sjtu_agent.paths import DATA_DIR


# ── _extract_text ────────────────────────────────────────────────────────────

def test_extract_text_simple():
    from scripts.feishu_bot import _extract_text
    assert _extract_text(json.dumps({"text": "hello"})) == "hello"


def test_extract_text_with_mention():
    from scripts.feishu_bot import _extract_text
    raw = json.dumps({"text": "@_user_1 你好"})
    assert _extract_text(raw) == "你好"


def test_extract_text_multiple_mentions():
    from scripts.feishu_bot import _extract_text
    raw = json.dumps({"text": "@_user_1 @_user_2 查作业"})
    assert _extract_text(raw) == "查作业"


def test_extract_text_empty():
    from scripts.feishu_bot import _extract_text
    assert _extract_text("") == ""


def test_extract_text_invalid_json():
    from scripts.feishu_bot import _extract_text
    assert _extract_text("not json") == ""


def test_extract_text_no_text_field():
    from scripts.feishu_bot import _extract_text
    assert _extract_text(json.dumps({"other": "data"})) == ""


def test_extract_text_slash_command():
    from scripts.feishu_bot import _extract_text
    raw = json.dumps({"text": "/list"})
    assert _extract_text(raw) == "/list"


# ── _extract_assistant_reply ─────────────────────────────────────────────────

def test_extract_assistant_reply_string_content():
    from scripts.feishu_bot import _extract_assistant_reply
    sess = {"messages": [{"role": "assistant", "content": "你好！"}]}
    assert _extract_assistant_reply(sess) == "你好！"


def test_extract_assistant_reply_list_content():
    from scripts.feishu_bot import _extract_assistant_reply
    sess = {"messages": [{"role": "assistant", "content": [
        {"type": "text", "text": "Part 1"},
        {"type": "text", "text": "Part 2"},
    ]}]}
    assert _extract_assistant_reply(sess) == "Part 1\nPart 2"


def test_extract_assistant_reply_empty_content():
    from scripts.feishu_bot import _extract_assistant_reply
    sess = {"messages": [{"role": "assistant", "content": ""}]}
    assert _extract_assistant_reply(sess) == "(已完成)"


def test_extract_assistant_reply_no_assistant_message():
    from scripts.feishu_bot import _extract_assistant_reply
    sess = {"messages": [{"role": "user", "content": "hello"}]}
    assert _extract_assistant_reply(sess) == "(已完成)"


def test_extract_assistant_reply_returns_last():
    from scripts.feishu_bot import _extract_assistant_reply
    sess = {"messages": [
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "third"},
    ]}
    assert _extract_assistant_reply(sess) == "third"


# ── _model_supports_vision ───────────────────────────────────────────────────

@pytest.mark.parametrize("model", [
    "gpt-4o", "gpt-4-turbo", "claude-3-opus", "claude-4-sonnet",
    "gemini-pro-vision", "qwen-vl-max", "sonnet-4", "opus-4",
])
def test_model_supports_vision_true(model):
    from scripts.feishu_bot import _model_supports_vision
    assert _model_supports_vision(model) is True


@pytest.mark.parametrize("model", [
    "deepseek-chat", "gpt-3.5-turbo", "qwen-max", "", "unknown",
])
def test_model_supports_vision_false(model):
    from scripts.feishu_bot import _model_supports_vision
    assert _model_supports_vision(model) is False


# ── _is_duplicate ────────────────────────────────────────────────────────────

def test_is_duplicate_new_message(monkeypatch):
    from scripts.feishu_bot import _is_duplicate, _SEEN_IDS
    _SEEN_IDS.clear()
    assert _is_duplicate("msg-1") is False


def test_is_duplicate_repeated_message(monkeypatch):
    from scripts.feishu_bot import _is_duplicate, _SEEN_IDS
    _SEEN_IDS.clear()
    _is_duplicate("msg-1")
    assert _is_duplicate("msg-1") is True


def test_is_duplicate_different_message(monkeypatch):
    from scripts.feishu_bot import _is_duplicate, _SEEN_IDS
    _SEEN_IDS.clear()
    _is_duplicate("msg-1")
    assert _is_duplicate("msg-2") is False


def test_is_duplicate_expired(monkeypatch):
    from scripts.feishu_bot import _is_duplicate, _SEEN_IDS, _SEEN_TTL
    _SEEN_IDS.clear()
    _is_duplicate("old-msg")
    # fast-forward time past TTL
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + _SEEN_TTL + 10)
    assert _is_duplicate("old-msg") is False  # expired -> treated as new


# ── _is_duplicate_content ────────────────────────────────────────────────────

def test_is_duplicate_content_new():
    from scripts.feishu_bot import _is_duplicate_content, _SEEN_CONTENT
    _SEEN_CONTENT.clear()
    assert _is_duplicate_content("user-a", "hello") is False


def test_is_duplicate_content_repeated():
    from scripts.feishu_bot import _is_duplicate_content, _SEEN_CONTENT
    _SEEN_CONTENT.clear()
    _is_duplicate_content("user-a", "hello")
    assert _is_duplicate_content("user-a", "hello") is True


def test_is_duplicate_content_different_user():
    from scripts.feishu_bot import _is_duplicate_content, _SEEN_CONTENT
    _SEEN_CONTENT.clear()
    _is_duplicate_content("user-a", "hello")
    assert _is_duplicate_content("user-b", "hello") is False


def test_is_duplicate_content_expired(monkeypatch):
    from scripts.feishu_bot import _is_duplicate_content, _SEEN_CONTENT, _CONTENT_DEDUP_SEC
    _SEEN_CONTENT.clear()
    _is_duplicate_content("user-a", "hello")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + _CONTENT_DEDUP_SEC + 1)
    assert _is_duplicate_content("user-a", "hello") is False


# ── _build_date_ctx ──────────────────────────────────────────────────────────

def test_build_date_ctx_format(monkeypatch):
    """Verify the date context contains expected semester info."""
    from scripts.feishu_bot import _build_date_ctx
    ctx = _build_date_ctx()
    assert "当前时间" in ctx or "每轮自动刷新" in ctx  # format present
    assert "学年" in ctx  # semester info present


# ── _new_conv_dict (requires agent config mocking) ──────────────────────────

def test_new_conv_dict_structure(monkeypatch):
    """_new_conv_dict creates the expected conversation dict shape."""
    import agent
    from sjtu_agent.feishu.conversations import FeishuConversationManager
    # Mock agent config so _make_client does not fail
    monkeypatch.setattr(agent, "load_agent_config", lambda: {"model": "deepseek-chat", "api_key": None})
    monkeypatch.setattr(agent, "_make_client", lambda cfg: None)

    mgr = FeishuConversationManager(DATA_DIR)
    conv = mgr._new_conv_dict("测试")
    assert conv["name"] == "测试"
    assert conv["messages"] == []
    assert "model_box" in conv
    assert "client_box" in conv
    assert "created_at" in conv


# ── _handle_commands — basic command routing ────────────────────────────────

def _setup_conv_mgr():
    """Set up a blank FeishuConversationManager for command testing."""
    from scripts.feishu_bot import _conv_mgr
    _conv_mgr.sessions.clear()
    _conv_mgr.locks.clear()
    return _conv_mgr


def test_handle_commands_help():
    from scripts.feishu_bot import _handle_commands
    _setup_conv_mgr()
    result = _handle_commands("test-open-id", "/help")
    assert result is not None
    assert "命令帮助" in result or "/help" in result


def test_handle_commands_list_empty():
    from scripts.feishu_bot import _handle_commands
    _setup_conv_mgr()
    result = _handle_commands("test-open-id", "/list")
    assert result is not None
    assert "对话" in result


def test_handle_commands_new_and_switch():
    from scripts.feishu_bot import _handle_commands
    _setup_conv_mgr()
    r1 = _handle_commands("test-open-id", "/new 学习")
    assert "OK" in r1
    r2 = _handle_commands("test-open-id", "/list")
    assert "学习" in r2


def test_handle_commands_not_a_command():
    from scripts.feishu_bot import _handle_commands
    result = _handle_commands("test-open-id", "你好，今天天气怎么样？")
    assert result is None


def test_handle_commands_unknown():
    from scripts.feishu_bot import _handle_commands
    _setup_conv_mgr()
    result = _handle_commands("test-open-id", "/unknown_cmd")
    assert "未知命令" in result


def test_handle_commands_name_rename():
    from scripts.feishu_bot import _handle_commands
    _setup_conv_mgr()
    _handle_commands("test-open-id", "/new 原名称")
    r = _handle_commands("test-open-id", "/name 1 新名称")
    assert "新名称" in r
    assert "OK" in r


def test_handle_commands_delete():
    from scripts.feishu_bot import _handle_commands
    _setup_conv_mgr()
    _handle_commands("test-open-id", "/new 测试1")
    _handle_commands("test-open-id", "/new 测试2")
    r = _handle_commands("test-open-id", "/delete 1")
    assert "OK" in r
    assert "已删除" in r


# ── 斜杠命令注册表 ───────────────────────────────────────────────────────────

def test_command_registry_has_news():
    """/news 已实现（修复：README/帮助宣传但分发缺失）。"""
    from scripts.feishu_bot import _COMMAND_REGISTRY
    assert "/news" in _COMMAND_REGISTRY
    assert "/hw" in _COMMAND_REGISTRY
    assert "/eat" in _COMMAND_REGISTRY
    assert "/template" in _COMMAND_REGISTRY


def test_handle_commands_unknown_and_help():
    from scripts.feishu_bot import _handle_commands
    assert "未知命令" in _handle_commands("x", "/nope")
    help_text = _handle_commands("x", "/help")
    assert "/news" in help_text
    assert "/hw" in help_text


def test_handle_commands_hw_due_invalid():
    """/hw due abc → 干净报错而非崩溃。"""
    from scripts.feishu_bot import _handle_commands
    r = _handle_commands("x", "/hw due abc")
    assert "无效天数" in r


def test_handle_commands_not_command():
    from scripts.feishu_bot import _handle_commands
    assert _handle_commands("x", "你好呀") is None
