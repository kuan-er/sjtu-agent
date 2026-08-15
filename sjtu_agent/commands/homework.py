"""
sjtu_agent/commands/homework.py — /hw 命令执行（飞书 / WebUI 共享）。
"""

from __future__ import annotations

from .result import CommandResult


def _error(text: str, data: dict | None = None) -> CommandResult:
    return CommandResult(
        view="homework",
        text=text,
        data={"ok": False, **(data or {})},
    )


def cmd_hw(user_id: str, parts: list[str]) -> CommandResult:
    """Execute /hw sub-commands. ``user_id`` is kept for dispatch compatibility."""
    del user_id  # reserved for per-user context (e.g. /hw answer) in the future
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "do":
        if len(parts) < 3:
            return _error("用法：/hw do <序号>", {"kind": "do"})
        try:
            idx = int(parts[2])
        except ValueError:
            return _error(f"无效序号：{parts[2]}", {"kind": "do"})
        from sjtu_agent.homework_agent import run_homework_check
        text = "[homework] 🧠 解题助手模式…\n\n" + run_homework_check(specific_idx=idx)
        return CommandResult(
            view="homework",
            text=text,
            data={"ok": True, "kind": "do", "index": idx},
        )

    if sub == "brief":
        if len(parts) < 3:
            return _error("用法：/hw brief <序号>", {"kind": "brief"})
        try:
            idx = int(parts[2])
        except ValueError:
            return _error(f"无效序号：{parts[2]}", {"kind": "brief"})
        from sjtu_agent.homework_agent import run_homework_check
        text = "[homework] 正在获取摘要…\n\n" + run_homework_check(specific_idx=idx, brief=True)
        return CommandResult(
            view="homework",
            text=text,
            data={"ok": True, "kind": "brief", "index": idx},
        )

    if sub == "past":
        rest = parts[2] if len(parts) > 2 else ""
        rest_parts = rest.split(maxsplit=1)
        if rest_parts and rest_parts[0] == "do":
            try:
                idx = int(rest_parts[1])
            except (ValueError, IndexError):
                return _error("用法：/hw past do <序号>", {"kind": "past_do"})
            from sjtu_agent.homework_agent import run_homework_check
            text = "[homework] 正在分析历史作业…\n\n" + run_homework_check(
                specific_idx=idx, include_past=True)
            return CommandResult(
                view="homework",
                text=text,
                data={"ok": True, "kind": "past_do", "index": idx},
            )
        from sjtu_agent.homework_agent import fetch_homework_list
        text, items = fetch_homework_list(include_past=True)
        return CommandResult(
            view="homework",
            text=text,
            data={"ok": True, "kind": "past", "assignments": items, "include_past": True},
        )

    if sub == "list":
        from sjtu_agent.homework_agent import fetch_homework_list
        text, items = fetch_homework_list()
        return CommandResult(
            view="homework",
            text=text,
            data={"ok": True, "kind": "list", "assignments": items},
        )

    if sub == "due":
        try:
            days = int(parts[2]) if len(parts) > 2 else 3
        except ValueError:
            return _error(f"无效天数：{parts[2]}。用法：/hw due <N>", {"kind": "due"})
        from sjtu_agent.homework_agent import fetch_homework_list
        text, items = fetch_homework_list(due_within_days=days)
        return CommandResult(
            view="homework",
            text=text,
            data={"ok": True, "kind": "due", "assignments": items, "due_within_days": days},
        )

    if sub == "all":
        from sjtu_agent.homework_agent import fetch_homework_list
        text, items = fetch_homework_list(due_within_days=3650, include_past=True)
        return CommandResult(
            view="homework",
            text=text,
            data={"ok": True, "kind": "all", "assignments": items, "include_past": True},
        )

    if sub == "answer":
        return _error(
            "[homework] 请先用 /hw do <序号> 分析作业，再要答案哦~",
            {"kind": "answer"},
        )

    from sjtu_agent.homework_agent import fetch_homework_list
    text, items = fetch_homework_list()
    return CommandResult(
        view="homework",
        text=text,
        data={"ok": True, "kind": "list", "assignments": items},
    )
