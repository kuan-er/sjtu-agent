"""
sjtu_agent/commands/dispatch.py — 核心斜杠命令注册表与统一错误处理。

Feishu 用自己的外层 _handle_commands 处理会话命令 / 自然语言触发，
再落到这里的 CORE_COMMAND_REGISTRY；WebUI 直接调用 run_command()。
"""

from __future__ import annotations

from collections.abc import Callable

from .dining import cmd_eat
from .homework import cmd_hw
from .news import cmd_news, cmd_news_block, cmd_news_reset
from .template import cmd_template

CommandHandler = Callable[[str, list[str]], str]

CORE_COMMAND_REGISTRY: dict[str, CommandHandler] = {
    "/hw": cmd_hw,
    "/news": cmd_news,
    "/news_block": cmd_news_block,
    "/news_reset": cmd_news_reset,
    "/eat": cmd_eat,
    "/template": cmd_template,
}


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """Parse a slash command into (lowercase name, parts)."""
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return None
    parts = raw.split(maxsplit=2)
    return parts[0].lower(), parts


def is_core_command(text: str) -> bool:
    parsed = parse_command(text)
    return bool(parsed and parsed[0] in CORE_COMMAND_REGISTRY)


def run_command(text: str, user_id: str = "") -> str | None:
    """Execute a shared core command.

    Returns the command result text, or ``None`` when the input is not a
    core slash command. Exceptions are converted to the same readable
    format Feishu used, so the Web endpoint never crashes on a bad command.
    """
    parsed = parse_command(text)
    if parsed is None:
        return None
    cmd, parts = parsed
    handler = CORE_COMMAND_REGISTRY.get(cmd)
    if handler is None:
        return None
    try:
        return handler(user_id, parts)
    except Exception as exc:
        import traceback
        return f"[命令错误] `{cmd}` 执行出错：{exc}\n```\n{traceback.format_exc()[-300:]}\n```"
