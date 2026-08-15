"""
sjtu_agent.commands — 跨入口斜杠命令：元数据、自然语言翻译与共享执行层。
"""

from .defs import COMMANDS, CommandDef, command_defs, command_prompt
from .dispatch import (
    CORE_COMMAND_REGISTRY,
    is_core_command,
    parse_command,
    run_command,
)

__all__ = [
    "COMMANDS",
    "CommandDef",
    "CORE_COMMAND_REGISTRY",
    "command_defs",
    "command_prompt",
    "is_core_command",
    "parse_command",
    "run_command",
]
