"""Recent-updates tool — reads CHANGELOG.md when the user asks "what's new".

近期更新信息原本硬编码在 system prompt 里（每次发版都改，还破坏缓存前缀）。
现在移出到 CHANGELOG.md，Agent 需要时按需读取。"""

from pathlib import Path

# sjtu_agent/agent/tools/_changelog.py → repo root（CHANGELOG.md 所在）
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "get_recent_updates",
            "description": (
                "读取项目 CHANGELOG，返回最近的版本更新内容。"
                "用户问「最近更新了什么」「有什么新功能」「新版变化」「更新日志」时调用，"
                "不要凭记忆编造更新内容。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def tool_get_recent_updates() -> str:
    """返回 CHANGELOG.md 最近的更新条目（按 Markdown 展示）。"""
    changelog = _REPO_ROOT / "CHANGELOG.md"
    if not changelog.exists():
        return "暂无更新日志。"
    try:
        text = changelog.read_text(encoding="utf-8").strip()
    except Exception:
        return "读取更新日志失败。"
    # 只返回最近的版本块（前 ~70 行，约 3-4 个版本）
    lines = text.split("\n")
    return "\n".join(lines[:70])
