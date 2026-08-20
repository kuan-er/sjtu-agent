"""tests/test_mcp_runner_integration.py — MCP 工具真正下发到 runner 的契约。

根因（issue #149-2）：runner 的 _get_tools() 只返回静态 TOOLS，add_mcp_server
写入的 MCP 服务器工具从未进入发给模型的工具列表。这里锁住修复后的契约。
"""

import asyncio
import json
from contextlib import asynccontextmanager


class _SlowSession:
    async def list_tools(self):
        await asyncio.sleep(30)
        return None


async def _slow_wait_for_ever_ctx():  # pragma: no cover - 仅在测试中作为挂起连接替身
    await asyncio.sleep(30)


@asynccontextmanager
async def slow_open_session(server_cfg):  # pragma: no cover
    """模拟永远挂起的 MCP 连接。"""
    await asyncio.sleep(30)
    yield _SlowSession()


def _write_config(tmp_path, servers):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"mcp_servers": servers}), encoding="utf-8")
    return config_path


def test_runner_get_tools_includes_registry_mcp_tools(monkeypatch):
    """runner 的工具列表 = 内置 + MCP 动态工具（registry 聚合）。"""
    from sjtu_agent.agent import runner
    from sjtu_agent.extensions import registry

    fake_tools = [
        {"type": "function", "function": {"name": "get_ddls"}},
        {"type": "function", "function": {"name": "mcp__demo__echo"}},
    ]
    monkeypatch.setattr(
        registry, "get_available_tools", lambda force_refresh=False: list(fake_tools)
    )

    tools = runner._get_tools()
    names = [t["function"]["name"] for t in tools]
    assert "get_ddls" in names
    assert "mcp__demo__echo" in names


def test_mcp_discovery_timeout_yields_status_tool(tmp_path, monkeypatch):
    """某个 server 连接挂起时，发现超时应产出可调用的状态工具而非阻塞整轮。"""
    from sjtu_agent.extensions import mcp_client

    config_path = _write_config(tmp_path, {
        "demo": {"enabled": True, "transport": "stdio", "command": "python",
                 "args": ["server.py"], "discovery_timeout": 0.05},
    })
    monkeypatch.setattr(mcp_client, "CONFIG_PATH", config_path)
    monkeypatch.setattr(mcp_client, "_open_session", slow_open_session)
    mcp_client._TOOLS_CACHE.update({"ts": 0.0, "tools": [], "map": {}})

    tools = mcp_client.list_openai_tools(force_refresh=True)
    status = [t for t in tools if t["function"]["name"].endswith("__status")]
    assert status, "挂起的 server 应产出 __status 工具而不是拖死发现流程"
    desc = status[0]["function"]["description"]
    assert ("timeout" in desc.lower() or "unavailable" in desc.lower()
            or "超时" in desc or "不可用" in desc)


def test_add_mcp_server_returns_config_path_and_restart_guidance(tmp_path, monkeypatch):
    """add_mcp_server 成功返回值应包含正确配置路径与重启指引。"""
    from sjtu_agent.agent import tools
    from sjtu_agent.extensions import mcp_client

    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tools._mcp_skills, "CONFIG_PATH", config_path)
    monkeypatch.setattr(mcp_client, "list_openai_tools", lambda force_refresh=False: [])

    result = tools.tool_add_mcp_server(
        server_id="demo",
        transport="sse",
        url="http://127.0.0.1:8765/sse",
        acknowledge_external_mcp=True,
    )
    assert result["ok"] is True
    assert result["config_path"] == str(config_path)
    guidance = str(result.get("next_action", "")) + str(result.get("checklist", ""))
    assert "daemons restart" in guidance
    assert "重启" in guidance