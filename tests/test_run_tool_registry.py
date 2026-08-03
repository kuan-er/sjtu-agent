"""Tests for the run_tool dispatch registry.

run_tool was a 70-branch if/elif chain; it's now a _TOOL_REGISTRY dict.
These tests guard against tools being exposed to the LLM (in TOOLS) but
missing from the registry (which would make the model call fail), and
against dispatch regressions.
"""


def test_all_exposed_tools_are_registered():
    """TOOLS 列表暴露的每个工具名都能被 run_tool 分发（mcp__ 和隐藏的 execute_python 除外）。"""
    from sjtu_agent.agent.tools._core import TOOLS, _TOOL_REGISTRY
    missing = []
    for entry in TOOLS:
        name = entry["function"]["name"]
        if name.startswith("mcp__") or name == "execute_python":
            continue
        if name not in _TOOL_REGISTRY:
            missing.append(name)
    assert missing == [], f"以下工具暴露给 LLM 但未注册: {missing}"


def test_unknown_tool_returns_error():
    from sjtu_agent.agent.tools._core import run_tool
    import json
    result = json.loads(run_tool("no_such_tool", {}))
    assert "error" in result


def test_no_args_tool_ignores_extra_args():
    """无参工具（如 get_user_profile）传多余参数不应报错。"""
    from sjtu_agent.agent.tools._core import run_tool
    import json
    # 无参工具被 LLM 误传 args 时，原 if/elif 会忽略 args；注册表 _no_args 也应忽略
    result = json.loads(run_tool("get_user_profile", {"unexpected": 1}))
    assert "error" not in result


def test_arity_tool_passes_kwargs():
    """带参工具应正确展开 kwargs 调用。get_ddls 传 skip_icourse=True 不应因参数处理报错。"""
    from sjtu_agent.agent.tools._core import run_tool
    import json
    result = json.loads(run_tool("get_ddls", {"skip_icourse": True}))
    # get_ddls 会尝试拉取（可能因未配置返回空/警告），但不应因 dispatch 本身报错
    assert "error" not in result
