"""Small synchronous adapter around the MCP Python client.

The agent runner is synchronous today. This module keeps MCP integration
behind a simple sync API by opening a short-lived MCP session for discovery
and calls. It favors reliability over long-lived process management.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from queue import Queue
from typing import Any

from sjtu_agent.paths import CONFIG_PATH, read_json_safe


_CACHE_TTL_SECONDS = 60
_TOOLS_CACHE: dict[str, Any] = {"ts": 0.0, "tools": [], "map": {}}
_NAME_RE = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize(value: str) -> str:
    cleaned = _NAME_RE.sub("_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "tool"


def _load_servers() -> dict[str, dict]:
    cfg = read_json_safe(CONFIG_PATH, {})
    servers = cfg.get("mcp_servers", {})
    return servers if isinstance(servers, dict) else {}


def _enabled_servers() -> dict[str, dict]:
    result = {}
    for server_id, server_cfg in _load_servers().items():
        if not isinstance(server_cfg, dict):
            continue
        if not server_cfg.get("enabled", False):
            continue
        result[str(server_id)] = server_cfg
    return result


def is_mcp_tool(name: str) -> bool:
    return name.startswith("mcp__")


def _run_async(coro_factory):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    q: Queue[tuple[bool, Any]] = Queue(maxsize=1)

    def _worker() -> None:
        try:
            q.put((True, asyncio.run(coro_factory())))
        except Exception as exc:
            q.put((False, exc))

    thread = threading.Thread(target=_worker, name="sjtu-agent-mcp-async", daemon=True)
    thread.start()
    ok, value = q.get()
    thread.join()
    if ok:
        return value
    raise value


def _unique_name(public_name: str, used: set[str]) -> str:
    candidate = public_name[:64]
    if candidate not in used:
        used.add(candidate)
        return candidate
    index = 2
    while True:
        suffix = f"_{index}"
        candidate = public_name[: 64 - len(suffix)] + suffix
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


@asynccontextmanager
async def _open_session(server_cfg: dict):
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:
        raise RuntimeError("MCP client dependency is not installed. Run `pip install -e .`.") from exc

    transport = str(server_cfg.get("transport", "stdio")).lower()
    if transport == "stdio":
        command = str(server_cfg.get("command", "")).strip()
        if not command:
            raise ValueError("stdio MCP server requires `command`.")
        params = StdioServerParameters(
            command=command,
            args=[str(x) for x in server_cfg.get("args", [])],
            env=server_cfg.get("env") if isinstance(server_cfg.get("env"), dict) else None,
            cwd=server_cfg.get("cwd") or None,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
        return

    if transport == "sse":
        url = str(server_cfg.get("url", "")).strip()
        if not url:
            raise ValueError("sse MCP server requires `url`.")
        async with sse_client(
            url,
            headers=server_cfg.get("headers") if isinstance(server_cfg.get("headers"), dict) else None,
            timeout=float(server_cfg.get("timeout", 10)),
            sse_read_timeout=float(server_cfg.get("sse_read_timeout", 300)),
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
        return

    if transport in {"streamable_http", "http"}:
        url = str(server_cfg.get("url", "")).strip()
        if not url:
            raise ValueError("streamable_http MCP server requires `url`.")
        async with streamablehttp_client(
            url,
            headers=server_cfg.get("headers") if isinstance(server_cfg.get("headers"), dict) else None,
            timeout=float(server_cfg.get("timeout", 30)),
            sse_read_timeout=float(server_cfg.get("sse_read_timeout", 300)),
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
        return

    raise ValueError(f"Unsupported MCP transport: {transport}")


def _tool_to_openai(server_id: str, mcp_tool: Any, index: int) -> tuple[dict, dict]:
    original_name = getattr(mcp_tool, "name", "") or f"tool_{index}"
    public_name = f"mcp__{_sanitize(server_id)}__{_sanitize(original_name)}"
    description = getattr(mcp_tool, "description", "") or f"MCP tool `{original_name}` from `{server_id}`."
    input_schema = getattr(mcp_tool, "inputSchema", None) or getattr(mcp_tool, "input_schema", None) or {}
    if hasattr(input_schema, "model_dump"):
        input_schema = input_schema.model_dump(exclude_none=True)
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}, "required": []}
    input_schema.setdefault("type", "object")
    input_schema.setdefault("properties", {})

    tool = {
        "type": "function",
        "function": {
            "name": public_name,
            "description": f"[MCP:{server_id}] {description}",
            "parameters": input_schema,
        },
    }
    meta = {"server_id": server_id, "tool_name": original_name, "public_name": tool["function"]["name"]}
    return tool, meta


async def _list_tools_async() -> tuple[list[dict], dict[str, dict]]:
    tools: list[dict] = []
    name_map: dict[str, dict] = {}
    used_names: set[str] = set()
    for server_id, server_cfg in _enabled_servers().items():
        try:
            async with _open_session(server_cfg) as session:
                result = await session.list_tools()
        except Exception as exc:
            public_name = _unique_name(f"mcp__{_sanitize(server_id)}__status", used_names)
            tools.append({
                "type": "function",
                "function": {
                    "name": public_name,
                    "description": f"[MCP:{server_id}] Report why this MCP server is unavailable.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            })
            name_map[public_name] = {
                "server_id": server_id,
                "tool_name": "__status__",
                "error": str(exc),
            }
            continue
        for idx, tool_obj in enumerate(getattr(result, "tools", []) or []):
            tool, meta = _tool_to_openai(server_id, tool_obj, idx)
            public_name = _unique_name(meta["public_name"], used_names)
            tool["function"]["name"] = public_name
            meta["public_name"] = public_name
            tools.append(tool)
            name_map[meta["public_name"]] = meta
    return tools, name_map


def list_openai_tools(force_refresh: bool = False) -> list[dict]:
    now = time.monotonic()
    if not force_refresh and now - float(_TOOLS_CACHE.get("ts", 0)) < _CACHE_TTL_SECONDS:
        return list(_TOOLS_CACHE.get("tools", []))
    tools, name_map = _run_async(lambda: _list_tools_async())
    _TOOLS_CACHE.update({"ts": now, "tools": tools, "map": name_map})
    return list(tools)


def _content_to_jsonable(content: Any) -> Any:
    if hasattr(content, "text"):
        return getattr(content, "text")
    if hasattr(content, "model_dump"):
        return content.model_dump(exclude_none=True)
    return str(content)


async def _call_tool_async(public_name: str, arguments: dict | None) -> dict:
    if public_name not in _TOOLS_CACHE.get("map", {}):
        tools, name_map = await _list_tools_async()
        _TOOLS_CACHE.update({"ts": time.monotonic(), "tools": tools, "map": name_map})
    meta = _TOOLS_CACHE.get("map", {}).get(public_name)
    if not meta:
        return {"error": f"Unknown MCP tool: {public_name}"}
    if meta.get("error"):
        return {"error": meta["error"], "server": meta.get("server_id")}
    if meta.get("tool_name") == "__status__":
        return {"error": meta.get("error", "MCP server unavailable"), "server": meta.get("server_id")}

    server_id = meta["server_id"]
    server_cfg = _enabled_servers().get(server_id)
    if not server_cfg:
        return {"error": f"MCP server is disabled or missing: {server_id}"}

    timeout = float(server_cfg.get("call_timeout", 120))
    async with _open_session(server_cfg) as session:
        result = await session.call_tool(
            meta["tool_name"],
            arguments or {},
            read_timeout_seconds=timedelta(seconds=timeout),
        )
    content = [_content_to_jsonable(item) for item in (getattr(result, "content", []) or [])]
    return {
        "ok": not bool(getattr(result, "isError", False)),
        "server": server_id,
        "tool": meta["tool_name"],
        "content": content,
    }


def call_tool(public_name: str, arguments: dict | None = None) -> str:
    try:
        payload = _run_async(lambda: _call_tool_async(public_name, arguments or {}))
    except Exception as exc:
        payload = {"error": str(exc), "tool": public_name}
    return json.dumps(payload, ensure_ascii=False)
