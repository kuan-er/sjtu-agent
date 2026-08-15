"""
sjtu_agent.tui — Textual 全屏终端聊天界面（可选依赖）。

入口：
    sjtu-agent tui

会话与 Web GUI 共用同一个 SQLite store（web_sessions.sqlite3）。
"""

from .engine import new_store


def run() -> int:
    from .app import run_tui
    return run_tui()


__all__ = ["new_store", "run"]
