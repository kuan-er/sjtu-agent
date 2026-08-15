"""
sjtu_agent/tui/app.py — Textual 全屏聊天界面。

Textual 是可选依赖：本模块只在 `sjtu-agent tui` 被调用时通过 run_tui()
延迟导入；未安装时给出安装提示，不影响其余 CLI 功能。

消息区使用 VerticalScroll + Markdown widgets：
- 历史消息按条渲染，Markdown 完整排版
- 当前回复是单一 Markdown widget，流式更新
"""

from __future__ import annotations

import json
import threading
from typing import Any

from .commands import command_candidates
from .engine import cancel_turn, decide_approval, iter_chat_events, iter_command_events
from .messages import display_text
from .session_model import TuiSessionModel
from sjtu_agent.commands import is_core_command


def run_tui() -> int:
    try:
        from textual import on
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical, VerticalScroll
        from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Markdown, Static
    except ImportError as exc:
        import sys
        print("未安装 Textual。运行 `pip install -e \".[tui]\"` 后重试。", file=sys.stderr)
        print(f"详细错误：{exc}", file=sys.stderr)
        return 1

    class ChatApp(App):
        """与 Web GUI 共用 session store 的 Textual 聊天客户端。"""

        CSS = """
        #main { height: 1fr; }
        #sessions { width: 32; border-right: solid $primary-darken-2; }
        #messages { width: 1fr; height: 1fr; padding: 1 2; }
        #messages Markdown { margin: 0 0 1 0; }
        #prompt { dock: bottom; }
        #suggestions {
            height: auto;
            max-height: 10;
            padding: 0 1;
            color: $text-muted;
        }
        """

        BINDINGS = [
            ("ctrl+n", "new_session", "新会话"),
            ("ctrl+l", "focus_prompt", "聚焦输入"),
            ("ctrl+x", "stop_turn", "停止生成"),
            ("tab", "next_suggestion", "下一个建议"),
            ("shift+tab", "previous_suggestion", "上一个建议"),
        ]

        def __init__(self):
            super().__init__()
            self.model = TuiSessionModel()
            self.busy = False
            self.pending_approval: dict[str, Any] | None = None
            self.suggestions: list[dict[str, Any]] = []
            self.suggestion_index = 0
            self.stream_text = ""
            self.turn_session: str | None = None

        @property
        def session_id(self) -> str | None:
            return self.model.current_id

        @property
        def messages(self) -> VerticalScroll:
            return self.query_one("#messages", VerticalScroll)

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical():
                with Horizontal(id="main"):
                    yield ListView(id="sessions")
                    yield VerticalScroll(id="messages")
                yield Input(placeholder="输入消息，/ 执行斜杠命令，Enter 发送", id="prompt")
                yield Static(id="suggestions")
            yield Footer()

        async def on_mount(self) -> None:
            await self.refresh_sessions()
            self.query_one("#prompt", Input).focus()

        # ── 会话 ─────────────────────────────────────────────────────────
        async def action_new_session(self) -> None:
            if self.busy and self.session_id:
                cancel_turn(self.session_id)
            session = self.model.create_session("新会话")
            self.pending_approval = None
            self.busy = False
            await self.refresh_sessions(select_id=session.get("id"))
            await self.load_session(session.get("id") or "")

        def action_focus_prompt(self) -> None:
            self.query_one("#prompt", Input).focus()

        def action_stop_turn(self) -> None:
            if not self.busy or not self.session_id:
                return
            cancel_turn(self.session_id)

        async def refresh_sessions(self, select_id: str | None = None) -> None:
            session_list = self.query_one("#sessions", ListView)
            await session_list.clear()
            target = select_id or self.session_id
            for index, session in enumerate(self.model.list_sessions()):
                item = ListItem(
                    Label(session.get("title") or "未命名会话"),
                    id=f"session-{session['id']}",
                )
                session_list.append(item)
                if session["id"] == target:
                    session_list.index = index

        async def load_session(self, session_id: str) -> None:
            self.model.select(session_id)
            await self.messages.remove_children()
            session = self.model.get_current()
            title = (session or {}).get("title", "")
            await self.messages.mount(Markdown(f"# {title}"))
            for message in self.model.messages(session_id):
                text = display_text(message.get("content", ""))
                if message.get("role") == "user":
                    await self.messages.mount(Markdown(f"> **你**\n\n{text}"))
                else:
                    await self.messages.mount(Markdown(text))
            self.messages.scroll_end(animate=False)

        @on(ListView.Selected)
        async def on_session_selected(self, event) -> None:
            item_id = getattr(event.item, "id", "") or ""
            if not item_id.startswith("session-"):
                return
            target = item_id[len("session-"):]
            if target == self.session_id:
                return
            if self.busy and self.session_id:
                cancel_turn(self.session_id)
            self.busy = False
            self.pending_approval = None
            await self.load_session(target)

        # ── / 命令补全 ──────────────────────────────────────────────────
        def render_suggestions(self) -> None:
            widget = self.query_one("#suggestions", Static)
            if not self.suggestions:
                widget.update("")
                return
            lines = []
            for index, candidate in enumerate(self.suggestions):
                prefix = "> " if index == self.suggestion_index else "  "
                lines.append(
                    f"{prefix}{candidate.get('icon', '')} {candidate['value']} — "
                    f"{candidate.get('description', '')}"
                )
            widget.update("\n".join(lines))

        @on(Input.Changed)
        def on_input_changed(self, event: Input.Changed) -> None:
            self.suggestions = command_candidates(event.value)
            self.suggestion_index = 0
            self.render_suggestions()

        def action_next_suggestion(self) -> None:
            if not self.suggestions:
                return
            self.suggestion_index = (self.suggestion_index + 1) % len(self.suggestions)
            self.render_suggestions()

        def action_previous_suggestion(self) -> None:
            if not self.suggestions:
                return
            self.suggestion_index = (self.suggestion_index - 1) % len(self.suggestions)
            self.render_suggestions()

        # ── 输入与审批 ──────────────────────────────────────────────────
        @on(Input.Submitted)
        async def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            if not text or self.busy:
                return
            if self.pending_approval is not None:
                self.handle_approval(text)
                event.input.value = ""
                return
            if self.suggestions and text.startswith("/"):
                candidate = self.suggestions[self.suggestion_index]
                event.input.value = candidate["value"]
                self.suggestions = []
                self.render_suggestions()
                event.input.focus()
                return
            event.input.value = ""
            await self.start_turn(text)

        def handle_approval(self, text: str) -> None:
            approval = self.pending_approval
            self.pending_approval = None
            approved = text.lower() in {"approve", "yes", "y", "同意", "允许"}
            if not approved and text.lower() not in {"deny", "no", "n", "拒绝"}:
                self.pending_approval = approval
                self.stream_text += "\n\n> 请输入 approve 或 deny。"
                self.run_worker(self.flush_stream(), group="stream", exclusive=True)
                return
            decide_approval(str(approval.get("approval_id", "")), approved)
            self.stream_text += (
                f"\n\n_{'已批准' if approved else '已拒绝'}："
                f"{approval.get('tool_name', '')}_"
            )
            self.run_worker(self.flush_stream(), group="stream", exclusive=True)

        async def start_turn(self, text: str) -> None:
            if self.session_id is None:
                session = self.model.create_session("新会话")
                await self.refresh_sessions(select_id=session.get("id"))
            session_id = self.model.ensure_for_message(text)
            if not session_id:
                await self.messages.mount(Markdown("> 无法创建会话"))
                return

            await self.messages.mount(Markdown(f"> **你**\n\n{text}"))
            self.stream_text = ""
            await self.messages.mount(Markdown(id="stream-markdown"))

            self.busy = True
            self.turn_session = session_id
            command_mode = text.startswith("/") and is_core_command(text)
            threading.Thread(
                target=self._run_stream,
                args=(text, command_mode, session_id),
                daemon=True,
            ).start()

        def _run_stream(self, text: str, command_mode: bool, turn_session: str) -> None:
            try:
                stream = (
                    iter_command_events(turn_session, text)
                    if command_mode
                    else iter_chat_events(turn_session, text)
                )
                for event in stream:
                    if event.get("kind") == "done":
                        break
                    self.call_from_thread(self.render_event, event, turn_session)
            except Exception as exc:
                self.call_from_thread(
                    self.render_event,
                    {"kind": "error", "text": str(exc)},
                    turn_session,
                )
            finally:
                self.call_from_thread(self.finish_turn, turn_session)

        # ── 渲染（App 线程）─────────────────────────────────────────────
        def render_event(self, event: dict[str, Any], turn_session: str) -> None:
            if turn_session != self.session_id:
                return
            kind = event.get("kind")
            if kind == "token":
                self.stream_text += str(event.get("text", ""))
            elif kind == "tool_start":
                self.stream_text += f"\n\n🔧 **{event.get('name', '')}**"
            elif kind == "tool_end":
                self.stream_text += f"\n\n✅ **{event.get('name', '')}** 完成"
            elif kind == "approval_required":
                self.pending_approval = event
                self.stream_text += (
                    f"\n\n⚠️ **需要确认：{event.get('tool_name', '')}**\n\n"
                    f"```json\n{json.dumps(event.get('arguments', {}), ensure_ascii=False)}\n```\n\n"
                    "输入 `approve` 或 `deny`"
                )
            elif kind == "command_start":
                self.stream_text += f"\n\n⚡ `{event.get('raw', '')}`"
            elif kind == "command_progress":
                self.stream_text += f"\n\n_{event.get('message', '')}_"
            elif kind == "command_result":
                self.stream_text += "\n\n" + str(event.get("text", ""))
            elif kind == "error":
                self.stream_text += f"\n\n❌ **错误：{event.get('text', '')}**"
            elif kind == "cancelled":
                self.stream_text += "\n\n_已取消_"
            self.run_worker(self.flush_stream(), group="stream", exclusive=True)

        async def flush_stream(self) -> None:
            try:
                markdown = self.query_one("#stream-markdown", Markdown)
            except Exception:
                return
            await markdown.update(self.stream_text)
            self.messages.scroll_end(animate=False)

        def finish_turn(self, turn_session: str) -> None:
            if turn_session == self.session_id:
                self.busy = False
                self.pending_approval = None
            self.run_worker(
                self.refresh_sessions(select_id=turn_session),
                group="sessions-refresh",
            )

    ChatApp().run()
    return 0
