"""
sjtu_agent/commands/homework.py — /hw 命令执行（飞书 / WebUI 共享）。
"""

from __future__ import annotations


def cmd_hw(user_id: str, parts: list[str]) -> str:
    """Execute /hw sub-commands. ``user_id`` is kept for dispatch compatibility."""
    del user_id  # reserved for per-user context (e.g. /hw answer) in the future
    sub = parts[1] if len(parts) > 1 else ""
    from sjtu_agent.homework_agent import run_homework_check
    if sub == "do":
        if len(parts) < 3:
            return "用法：/hw do <序号>"
        try:
            idx = int(parts[2])
        except ValueError:
            return f"无效序号：{parts[2]}"
        return "[homework] 🧠 解题助手模式…\n\n" + run_homework_check(specific_idx=idx)
    elif sub == "brief":
        if len(parts) < 3:
            return "用法：/hw brief <序号>"
        try:
            idx = int(parts[2])
        except ValueError:
            return f"无效序号：{parts[2]}"
        return "[homework] 正在获取摘要…\n\n" + run_homework_check(specific_idx=idx, brief=True)
    elif sub == "past":
        rest = parts[2] if len(parts) > 2 else ""
        rest_parts = rest.split(maxsplit=1)
        if rest_parts and rest_parts[0] == "do":
            try:
                idx = int(rest_parts[1])
            except (ValueError, IndexError):
                return "用法：/hw past do <序号>"
            return "[homework] 正在分析历史作业…\n\n" + run_homework_check(
                specific_idx=idx, include_past=True)
        return run_homework_check(list_only=True, include_past=True)
    elif sub == "list":
        return run_homework_check(list_only=True)
    elif sub == "due":
        try:
            days = int(parts[2]) if len(parts) > 2 else 3
        except ValueError:
            return f"无效天数：{parts[2]}。用法：/hw due <N>"
        return run_homework_check(due_within_days=days, list_only=True)
    elif sub == "all":
        return run_homework_check(due_within_days=3650, include_past=True, list_only=True)
    elif sub == "answer":
        return "[homework] 请先用 /hw do <序号> 分析作业，再要答案哦~"
    else:
        return run_homework_check(list_only=True)
