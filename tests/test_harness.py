"""Tests for Harness layer (Phase 4): schema validation, execute_python guard, tool logging."""

from sjtu_agent.agent.tools import run_tool
from sjtu_agent.agent.tools._core import _coerce_args, _log_tool_call
from sjtu_agent.agent.tools._python_exec import _guard_code


# ── schema 校验 ─────────────────────────────────────────────────────────────

def test_coerce_required_field():
    """缺必填参数 → 明确报错（而非 TypeError 崩溃）。"""
    coerced, err = _coerce_args("get_schedule", {})
    assert err and "必填" in err


def test_coerce_boolean_and_integer():
    coerced, err = _coerce_args("get_ddls", {"classify": "true", "include_notifications": "false"})
    assert err is None
    assert coerced["classify"] is True
    assert coerced["include_notifications"] is False

    coerced, err = _coerce_args("get_schedule", {"query_type": "week", "week_offset": "1"})
    assert err is None
    assert coerced["week_offset"] == 1


def test_coerce_unknown_or_mcp_passthrough():
    """无 schema（mcp 外部工具）或未知工具 → 放行不拦截。"""
    coerced, err = _coerce_args("mcp__some_tool", {"a": 1})
    assert err is None
    assert coerced == {"a": 1}


def test_run_tool_unknown():
    assert "未知工具" in run_tool("no_such_tool", {})


# ── execute_python 守卫 ─────────────────────────────────────────────────────

def test_guard_allows_safe_code():
    assert _guard_code('print("hi"); import json; data = {"a": 1}') is None


def test_guard_blocks_destructive_git():
    assert _guard_code("git reset --hard") is not None
    assert _guard_code('subprocess.run(["git", "reset", "--hard"])') is not None
    assert _guard_code("git clean -fd") is not None
    assert _guard_code("git push origin --force") is not None


def test_guard_blocks_shell_and_delete():
    assert _guard_code('subprocess.run("rm -rf /", shell=True)') is not None
    assert _guard_code("import shutil; shutil.rmtree('/tmp/x')") is not None
    assert _guard_code('os.remove("config.json")') is not None
    assert _guard_code("import os; os.remove('agent_config.json')") is not None


def test_guard_blocks_config_write():
    assert _guard_code('Path("config.json").write_text("{}")') is not None
    assert _guard_code("CONFIG_PATH.write_text('x')") is not None


def test_guard_allows_safe_git():
    assert _guard_code("git status") is None
    assert _guard_code("git log --oneline") is None


# ── 工具调用日志（不抛异常）─────────────────────────────────────────────────

def test_log_tool_call_no_crash():
    _log_tool_call("setup_telegram", {"telegram_token": "SECRET", "flag": True}, 0.01, {"ok": True})
    _log_tool_call("get_ddls", {}, 0.5, "big result")
    _log_tool_call("x", {"nested": {"a": 1}}, 0.0, None)
