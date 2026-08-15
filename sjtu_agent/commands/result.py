"""
sjtu_agent/commands/result.py — 命令执行结果统一结构。

Feishu 只消费 ``text``（Markdown）；WebUI 额外消费 ``view`` + ``data``
渲染结构化卡片。``data`` 必须是 JSON-safe 数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CommandResult:
    view: str = "markdown"
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"view": self.view, "text": self.text, "data": self.data}
