"""Extension loading for skills and external MCP tools."""

from sjtu_agent.extensions.registry import get_available_tools, run_registered_tool
from sjtu_agent.extensions.skills import build_skill_prompt, enabled_skill_names

__all__ = [
    "get_available_tools",
    "run_registered_tool",
    "build_skill_prompt",
    "enabled_skill_names",
]
