"""
sjtu_agent/tui/app.py — Textual 全屏聊天界面。

Textual 是可选依赖：本模块只在 `sjtu-agent tui` 被调用时通过 run_tui()
延迟导入；未安装时给出安装提示，不影响其余 CLI 功能。
"""

from __future__ import annotations

import json
import threading
from typing import Any

from .engine import cancel_turn, decide_approval, iter_chat_events, iter_command_events
from .messages import display_text
from .session_model import TuiSessionModel
from sjtu_agent.commands import is_core_command


def run_tui() -> int:
    try:
        from textual import on
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog
        from rich.markup import escape
    except ImportError as exc:
        print("未安装 Textual。运行 `pip install -e \".[tui]\"` 后重试。", file=__import__("sys").stderr)
        print(f"详细错误：{exc}", file=__import__("sys").stderr)
        return 1

    class ChatApp(App):
        """与 Web GUI 共用 session store 的 Textual 聊天客户端。"""

        CSS = """
        #main { height: 1fr; }
        #sessions { width: 32; border-right: solid $primary-darken-2; }
        #chat { width: 1fr; height: 1fr; }
        #prompt { dock: bottom; }
        """

        BINDINGS = [
            ("ctrl+n", "new_session", "新会话"),
            ("ctrl+l", "focus_prompt", "聚焦输入"),
            ("ctrl+x", "stop_turn", "停止生成"),
        ]

        def __init__(self):
            super().__init__()
            self.model = TuiSessionModel()
            self.busy = False
            self.pending_approval: dict[str, Any] | None = None

        @property
        def session_id(self) -> str | None:
            return self.model.current_id

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical():
                with Horizontal(id="main"):
                    yield ListView(id="sessions")
                    yield RichLog(id="chat", markup=True, wrap=True)
                yield Input(placeholder="输入消息，/ 执行斜杠命令，Enter 发送", id="prompt")
            yield Footer()

        @property
        def chat(self) -> RichLog:
            return self.query_one("#chat", RichLog)

        def on_mount(self) -> None:
            self.refresh_sessions()
            self.query_one("#prompt", Input).focus()

        # ── 会话 ─────────────────────────────────────────────────────────
        def action_new_session(self) -> None:
            session = self.model.create_session("新会话")
            self.pending_approval = None
            self.refresh_sessions(select_id=session.get("id"))
            self.load_session(session.get("id") or "")

        def action_focus_prompt(self) -> None:
            self.query_one("#prompt", Input).focus()

        def action_stop_turn(self) -> None:
            if not self.busy or not self.session_id:
                return
            cancel_turn(self.session_id)
            self.chat.write("\n[dim]已请求停止…[/]")

        def refresh_sessions(self, select_id: str | None = None) -> None:
            session_list = self.query_one("#sessions", ListView)
            session_list.clear()
            target = select_id or self.session_id
            for session in self.model.list_sessions():
                item = ListItem(
                    Label(session.get("title") or "未命名会话"),
                    id=f"session-{session['id']}",
                )
                session_list.append(item)
                if session["id"] == target:
                    session_list.index = len(session_list.children) - 1

        def load_session(self, session_id: str) -> None:
            self.model.select(session_id)
            chat = self.query_one("#chat", RichLog)
            chat.clear()
            session = self.model.get_current()
            chat.write(f"[bold]会话：{(session or {}).get('title', '')}[/]\n")
            for message in self.model.messages(session_id):
                text = display_text(message.get("content", ""))
                if message.get("role") == "user":
                    chat.write(f"[bold cyan]你[/] {escape(text)}")
                else:
                    chat.write(f"[bold green]Agent[/] {escape(text)}")

        @on(ListView.Selected)
        def on_session_selected(self, event) -> None:
            item_id = getattr(event.item, "id", "") or ""
            if not item_id.startswith("session-"):
                return
            self.load_session(item_id[len("session-"):])

        # ── 输入与审批 ──────────────────────────────────────────────────
        @on(Input.Submitted)
        def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            event.input.value = ""
            if not text or self.busy:
                return
            if self.pending_approval is not None:
                self.handle_approval(text)
                return
            self.start_turn(text)

        def handle_approval(self, text: str) -> None:
            approval = self.pending_approval
            self.pending_approval = None
            approved = text.lower() in {"approve", "yes", "y", "同意", "允许"}
            if not approved and text.lower() not in {"deny", "no", "n", "拒绝"}:
                self.pending_approval = approval
                self.chat.write("[yellow]请输入 approve / deny 确认审批[/]")
                return
            decide_approval(str(approval.get("approval_id", "")), approved)
            self.chat.write(f"[dim]{'已批准' if approved else '已拒绝'}：{approval.get('tool_name', '')}[/]")

        def start_turn(self, text: str) -> None:
            if self.session_id is None:
                session = self.model.create_session("新会话")
                self.refresh_sessions(select_id=session.get("id"))
            session_id = self.model.ensure_for_message(text)
            if not session_id:
                self.chat.write("[red]无法创建会话[/]")
                return

            self.busy = True
            self.chat.write(f"[bold cyan]你[/] {escape(text)}")
            command_mode = text.startswith("/") and is_core_command(text)
            self.chat.write(
                f"[dim]{'⚡ 执行命令…' if command_mode else '…'}"
            )
            threading.Thread(
                target=self._run_stream,
                args=(text, command_mode),
                daemon=True,
            ).start()

        def _run_stream(self, text: str, command_mode: bool) -> None:
            try:
                stream = (
                    iter_command_events(self.session_id, text)
                    if command_mode
                    else iter_chat_events(self.session_id, text)
                )
                for event in stream:
                    if event.get("kind") == "done":
                        break
                    self.call_from_thread(self.render_event, event)
            except Exception as exc:
                self.call_from_thread(self.render_event, {"kind": "error", "text": str(exc)})
            finally:
                self.call_from_thread(self.finish_turn)

        def render_event(self, event: dict[str, Any]) -> None:
            kind = event.get("kind")
            chat = self.query_one("#chat", RichLog)
            if kind == "token":
                chat.write(escape(str(event.get("text", ""))))
            elif kind == "tool_start":
                chat.write(f"\n🔧 {escape(str(event.get('name', '')))}")
            elif kind == "tool_end":
                chat.write(f" ✓ {escape(str(event.get('name', '')))}")
            elif kind == "approval_required":
                self.pending_approval = event
                chat.write(
                    f"\n[yellow]⚠️ 需要确认工具调用：{escape(str(event.get('tool_name', '')))}\n"
                    f"参数：{escape(json.dumps(event.get('arguments', {}), ensure_ascii=False))}\n"
                    "输入 approve 或 deny[/]"
                )
            elif kind == "command_start":
                chat.write(f"\n⚡ {escape(str(event.get('raw', '')))}")
            elif kind == "command_progress":
                chat.write(f"[dim]{escape(str(event.get('message', '')))}[/]")
            elif kind == "command_result":
                chat.write("\n" + escape(str(event.get("text", ""))))
            elif kind == "error":
                chat.write(f"\n[red]错误：{escape(str(event.get('text', '')))}[/]")
            elif kind == "cancelled":
                chat.write("\n[dim]已取消[/]")

        def finish_turn(self) -> None:
            self.busy = False
            self.pending_approval = None
            self.chat.write("\n")
            self.refresh_sessions(select_id=self.session_id)

    ChatApp().run()
    return 0
