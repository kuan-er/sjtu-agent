"""Tests for feishu_bot.py core functions — no WebSocket connection required."""

import json
import sys
import threading
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


# ── 超时机制（issue: 飞书请求时间阈值放宽 + 进程/API 失效区分） ─────────────

def _boom():
    raise ValueError("boom")


def test_run_fn_with_timeout_success():
    from scripts.feishu_bot import _run_fn_with_timeout
    assert _run_fn_with_timeout(lambda x: x * 2, 2, 21) == 42


def test_run_fn_with_timeout_propagates_exception():
    from scripts.feishu_bot import _run_fn_with_timeout
    with pytest.raises(ValueError, match="boom"):
        _run_fn_with_timeout(_boom, 2)


def test_run_fn_with_timeout_unlimited_waits_for_result():
    """timeout<=0 表示不限时：像其他 bot 一样等待完成，不抛超时。"""
    from scripts.feishu_bot import _run_fn_with_timeout
    assert _run_fn_with_timeout(lambda: "ok", 0) == "ok"


def test_run_fn_with_timeout_progress_callback():
    """等待期间按 progress_interval 调用 on_progress，传递增的已等待秒数。"""
    from scripts.feishu_bot import _run_fn_with_timeout
    pings = []

    def worker():
        time.sleep(0.25)
        return "done"

    result = _run_fn_with_timeout(
        worker, 2, on_progress=pings.append, progress_interval=0.05,
    )
    assert result == "done"
    assert len(pings) >= 2
    assert pings == sorted(pings)
    assert all(isinstance(p, float) for p in pings)


def test_run_fn_with_timeout_timeout_keeps_worker_and_reports_late():
    """超时抛 TimeoutError；worker 不会被杀死，完成后通过 on_late 回调上报。"""
    from scripts.feishu_bot import _run_fn_with_timeout
    worker_done = threading.Event()
    late_reported = threading.Event()
    late = {}

    def worker():
        time.sleep(0.3)
        worker_done.set()
        return "late-result"

    def on_late(**kw):
        late.update(kw)
        late_reported.set()

    with pytest.raises(TimeoutError):
        _run_fn_with_timeout(worker, 0.05, on_late=on_late)

    assert worker_done.wait(2), "worker 应继续跑完"
    assert late_reported.wait(2), "on_late 应在 worker 完成后被调用"
    assert late.get("result") == "late-result"
    assert late["elapsed"] >= 0.2


def test_run_fn_with_timeout_late_reports_exception():
    """worker 在超时后以异常结束，on_late 拿到 exception。"""
    from scripts.feishu_bot import _run_fn_with_timeout
    late_reported = threading.Event()
    late = {}

    def worker():
        time.sleep(0.3)
        raise ValueError("late-error")

    def on_late(**kw):
        late.update(kw)
        late_reported.set()

    with pytest.raises(TimeoutError):
        _run_fn_with_timeout(worker, 0.05, on_late=on_late)
    assert late_reported.wait(2)
    assert isinstance(late["exception"], ValueError)


def test_capture_timeout_default_600(monkeypatch):
    from scripts import feishu_bot
    monkeypatch.setattr(feishu_bot, "_load_cfg", lambda: {})
    assert feishu_bot._capture_timeout_secs() == 600


def test_capture_timeout_from_config(monkeypatch):
    from scripts import feishu_bot
    monkeypatch.setattr(feishu_bot, "_load_cfg", lambda: {"feishu_capture_timeout": 300})
    assert feishu_bot._capture_timeout_secs() == 300


def test_capture_timeout_zero_means_unlimited(monkeypatch):
    from scripts import feishu_bot
    monkeypatch.setattr(feishu_bot, "_load_cfg", lambda: {"feishu_capture_timeout": 0})
    assert feishu_bot._capture_timeout_secs() == 0


def test_progress_interval_default_120(monkeypatch):
    from scripts import feishu_bot
    monkeypatch.setattr(feishu_bot, "_load_cfg", lambda: {})
    assert feishu_bot._progress_interval_secs() == 120


def test_progress_interval_from_config(monkeypatch):
    from scripts import feishu_bot
    monkeypatch.setattr(feishu_bot, "_load_cfg", lambda: {"feishu_progress_interval": 60})
    assert feishu_bot._progress_interval_secs() == 60


def test_run_turn_detached_isolates_live_messages(monkeypatch):
    """worker 在拷贝上跑；调用方不提交就永远碰不到线上会话（防超时后污染）。"""
    from scripts import feishu_bot
    live = [{"role": "user", "content": "previous"}]
    conv = {
        "messages": live,
        "model_box": ["deepseek-chat"],
        "client_box": [None],
    }

    def fake_capture(sess, user_text, open_id=""):
        sess["messages"].append({"role": "user", "content": user_text})
        sess["messages"].append({"role": "assistant", "content": "reply content"})
        return "reply content"

    monkeypatch.setattr(feishu_bot, "_capture_turn", fake_capture)
    reply, new_messages = feishu_bot._run_turn_detached(conv, "hello")

    assert reply == "reply content"
    # 线上会话从未被触碰（同一对象、同一内容）
    assert conv["messages"] is live
    assert conv["messages"] == [{"role": "user", "content": "previous"}]
    # 工作副本包含全部历史 + 本轮新增（user + assistant）
    assert new_messages[0] is live[0]
    assert len(new_messages) == 3


def test_run_turn_detached_multimodal(monkeypatch):
    from scripts import feishu_bot
    conv = {"messages": [], "model_box": ["deepseek-chat"], "client_box": [None]}
    seen = {}

    def fake_multimodal(sess, content, open_id=""):
        seen["sess"], seen["content"] = sess, content
        sess["messages"].append({"role": "user", "content": content})
        sess["messages"].append({"role": "assistant", "content": "described"})
        return "described"

    monkeypatch.setattr(feishu_bot, "_capture_turn_multimodal", fake_multimodal)
    content = [{"type": "text", "text": "img"}]
    reply, new_messages = feishu_bot._run_turn_detached(conv, "", "ou_x", multimodal_content=content)

    assert reply == "described"
    assert seen["content"] is content
    assert len(new_messages) == 2
    assert conv["messages"] == []  # 未提交


class _FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kw):
        self.kwargs = kw
        return None


class _FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kw):
        self.kwargs = kw
        return None


class _FakeChatResource:
    """client.chat（OpenAI 风格）"""

    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    def __init__(self):
        self.chat = _FakeChatResource()


class _FakeAnthropicClient:
    def __init__(self):
        self.messages = _FakeMessages()
    # 满足 duck-type：Anthropic 风格走 client.messages.create


def test_probe_api_health_openai_ok():
    from scripts.feishu_bot import _probe_api_health
    client = _FakeOpenAIClient()
    res = _probe_api_health("deepseek-chat", client)
    assert res["ok"] is True
    assert client.chat.completions.kwargs["max_tokens"] == 1
    assert client.chat.completions.kwargs["timeout"] == 15


def test_probe_api_health_anthropic_ok():
    from scripts.feishu_bot import _probe_api_health
    client = _FakeAnthropicClient()
    res = _probe_api_health("claude-sonnet-4", client)
    assert res["ok"] is True
    assert client.messages.kwargs["max_tokens"] == 1


def test_probe_api_health_reports_failure():
    from scripts.feishu_bot import _probe_api_health

    class _DownCompletions:
        def create(self, **kw):
            raise ConnectionError("cannot reach api")

    client = _FakeOpenAIClient()
    client.chat.completions = _DownCompletions()
    res = _probe_api_health("deepseek-chat", client)
    assert res["ok"] is False
    assert "cannot reach" in res["detail"]


def test_build_timeout_message_api_abnormal():
    from scripts.feishu_bot import _build_timeout_message
    msg = _build_timeout_message(120, {"ok": False, "detail": "ConnectionError: down"})
    assert "120" in msg
    assert "API" in msg
    assert "ConnectionError" in msg
    assert "feishu_capture_timeout" not in msg


def test_build_timeout_message_api_ok_but_slow():
    from scripts.feishu_bot import _build_timeout_message
    msg = _build_timeout_message(300, {"ok": True, "detail": "API 端点可达"})
    assert "300" in msg
    assert "API" in msg
    assert "feishu_capture_timeout" in msg


def test_build_timeout_message_no_probe():
    from scripts.feishu_bot import _build_timeout_message
    msg = _build_timeout_message(600, None)
    assert "600" in msg
    assert "feishu_capture_timeout" in msg


# ── _process_in_thread 编排（成功提交 / 超时探测 / 心跳限频） ────────────────

class _FakeConvMgr:
    def __init__(self, conv):
        self.conv = conv
        self.saved = 0

    def get_active(self, open_id):
        return self.conv, {"current_idx": 0}, threading.Lock()

    def save(self):
        self.saved += 1


def _run_process_in_thread(monkeypatch, conv, text="hello", *, capture_timeout=5,
                           progress_interval=60, turn_impl=None, probe=None):
    from scripts import feishu_bot
    replies = []
    mgr = _FakeConvMgr(conv)
    monkeypatch.setattr(feishu_bot, "_conv_mgr", mgr)
    monkeypatch.setattr(feishu_bot, "_reply_text", lambda mid, t: replies.append(t))
    monkeypatch.setattr(feishu_bot, "log_turn", lambda *a, **k: None)
    monkeypatch.setattr(feishu_bot, "_try_extract_memory", lambda *a, **k: None)
    monkeypatch.setattr(feishu_bot, "_capture_timeout_secs", lambda: capture_timeout)
    monkeypatch.setattr(feishu_bot, "_progress_interval_secs", lambda: progress_interval)
    monkeypatch.setattr(feishu_bot, "_probe_api_health", lambda *a, **k: (probe or {"ok": True, "detail": "可达"}))
    if turn_impl is not None:
        monkeypatch.setattr(feishu_bot, "_run_turn_detached", turn_impl)
    else:
        def default_turn(conv, user_text, open_id="", multimodal_content=None):
            conv["messages"].append({"role": "user", "content": user_text})
            conv["messages"].append({"role": "assistant", "content": "reply content"})
            return "reply content", conv["messages"]
        monkeypatch.setattr(feishu_bot, "_run_turn_detached", default_turn)
    feishu_bot._process_in_thread("ou_1", "msg-1", text)
    return replies, mgr, conv


def test_process_in_thread_success_commits_messages(monkeypatch):
    conv = {"messages": [], "model_box": ["deepseek-chat"], "client_box": [None]}
    replies, mgr, conv = _run_process_in_thread(monkeypatch, conv)
    assert replies == ["reply content"]
    assert len(conv["messages"]) == 2  # user + assistant 已提交
    assert mgr.saved == 1


def test_process_in_thread_timeout_no_commit_probe_reply(monkeypatch):
    conv = {"messages": [], "model_box": ["deepseek-chat"], "client_box": [None]}

    def slow_turn(conv, user_text, open_id="", multimodal_content=None):
        time.sleep(0.3)
        # 只改自己的副本，不碰线上 conv（模拟真实 detached 语义）
        new_msgs = list(conv["messages"]) + [{"role": "assistant", "content": "late"}]
        return "late", new_msgs

    replies, mgr, conv = _run_process_in_thread(
        monkeypatch, conv, capture_timeout=0.05, turn_impl=slow_turn,
        probe={"ok": False, "detail": "ConnectionError: down"},
    )
    assert any("检测到 API 端点异常" in r and "ConnectionError" in r for r in replies)
    assert conv["messages"] == []  # 超时不提交，残留线程无法污染会话


def test_process_in_thread_progress_pings_capped(monkeypatch):
    conv = {"messages": [], "model_box": ["deepseek-chat"], "client_box": [None]}

    def slow_turn(conv, user_text, open_id="", multimodal_content=None):
        time.sleep(0.25)
        conv["messages"].append({"role": "assistant", "content": "done"})
        return "done", conv["messages"]

    replies, mgr, conv = _run_process_in_thread(
        monkeypatch, conv, capture_timeout=5, progress_interval=0.02, turn_impl=slow_turn,
    )
    pings = [r for r in replies if "仍在处理中" in r]
    assert len(pings) >= 1
    assert len(pings) <= 3  # _MAX_PROGRESS_PINGS 限频
    assert replies[-1] == "done"
    assert len(conv["messages"]) == 1  # 成功提交
