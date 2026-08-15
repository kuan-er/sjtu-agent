"""
sjtu_agent/tui/commands.py — TUI 斜杠命令补全候选（纯逻辑）。

规则与 WebUI 的 command panel 一致：命令名前缀匹配，输入“命令名 + 参数”
时补全该命令的示例变体。
"""

from __future__ import annotations

from typing import Any

from sjtu_agent.commands import command_defs


def command_candidates(query: str, limit: int = 8) -> list[dict[str, Any]]:
    q = (query or "").lstrip().lower()
    if not q.startswith("/"):
        return []
    q_name = q.split(maxsplit=1)[0] if " " in q else q

    seen: set[str] = set()
    result: list[dict[str, Any]] = []

    def push(value: str, command: dict[str, Any]) -> None:
        if not value or value in seen:
            return
        seen.add(value)
        result.append({
            "value": value,
            "label": command.get("label", ""),
            "icon": command.get("icon", ""),
            "description": command.get("description", ""),
            "kind": "command" if value == command.get("name") else "example",
        })

    for command in command_defs():
        name = command.get("name", "")
        if name.lower().startswith(q):
            push(name, command)
        if name.lower() == q_name:
            for example in command.get("examples", []):
                if example.lower().startswith(q):
                    push(example, command)
    return result[:limit]
