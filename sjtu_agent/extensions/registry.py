"""Tool registry that combines built-in tools with enabled MCP tools."""

from __future__ import annotations

from sjtu_agent.extensions import mcp_client


def get_available_tools(force_refresh: bool = False) -> list[dict]:
    from sjtu_agent.agent.tools import TOOLS as builtin_tools

    return list(builtin_tools) + mcp_client.list_openai_tools(force_refresh=force_refresh)


def run_registered_tool(name: str, args: dict | None = None) -> str:
    if mcp_client.is_mcp_tool(name):
        return mcp_client.call_tool(name, args or {})
    from sjtu_agent.agent.tools import run_tool as run_builtin_tool

    return run_builtin_tool(name, args or {})
