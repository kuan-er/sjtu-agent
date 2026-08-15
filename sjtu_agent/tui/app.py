"""
sjtu_agent/tui/app.py — Textual 全屏聊天界面。

Textual 是可选依赖：本模块导入失败时 TEXTUAL_AVAILABLE=False，
run_tui() 给出安装提示；其余 CLI 功能不受影响。

消息区使用 VerticalScroll + Markdown widgets：
- 历史消息按条渲染，Markdown 完整排版
- 当前回复是单一 Markdown widget，流式更新
"""

from __future__ import annotations

import json
import threading
from typing import Any

from .cards import render_command_result
from .commands import command_candidates
from .engine import cancel_turn, decide_approval, iter_chat_events, iter_command_events
from .messages import display_text, parse_command_result
from .session_model import TuiSessionModel
from sjtu_agent.commands import is_core_command

try:
    from textual import on
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Markdown, Static
    TEXTUAL_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional extra
    App = None  # type: ignore[assignment]
    Binding = None  # type: ignore[assignment]
    ComposeResult = None  # type: ignore[assignment]
    Horizontal = Vertical = VerticalScroll = None  # type: ignore[assignment]
    ModalScreen = None  # type: ignore[assignment]
    Footer = Header = Input = Label = ListItem = ListView = Markdown = Static = None  # type: ignore[assignment]
    on = None  # type: ignore[assignment]
    TEXTUAL_AVAILABLE = False


if TEXTUAL_AVAILABLE:

    class RenameModal(ModalScreen[str | None]):
        """重命名当前会话的输入弹窗。"""

        CSS = """
        #rename-input { width: 60; }
        """

        BINDINGS = [("escape", "cancel", "取消")]

        def __init__(self, current_title: str):
            super().__init__()
            self.current_title = current_title

        def compose(self) -> ComposeResult:
            yield Label(f"重命名会话（当前：{self.current_title}）")
            yield Input(value=self.current_title, id="rename-input")

        def on_mount(self) -> None:
            prompt = self.query_one("#rename-input", Input)
            prompt.focus()
            prompt.cursor_position = len(prompt.value)

        @on(Input.Submitted)
        def on_input_submitted(self, event: Input.Submitted) -> None:
            self.dismiss(event.value.strip() or self.current_title)

        def action_cancel(self) -> None:
            self.dismiss(None)

    class ConfirmDeleteModal(ModalScreen[bool]):
        """删除会话二次确认。"""

        BINDINGS = [
            ("y", "confirm", "确认删除"),
            ("n", "cancel", "取消"),
            ("escape", "cancel", "取消"),
        ]

        def compose(self) -> ComposeResult:
            yield Label("⚠️ 删除当前会话？消息会从本地 SQLite 中永久移除。")
            yield Label("[y] 确认    [n/esc] 取消")

        def action_confirm(self) -> None:
            self.dismiss(True)

        def action_cancel(self) -> None:
            self.dismiss(False)

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
            Binding("ctrl+d", "delete_session", "删除会话", priority=True),
            ("ctrl+n", "new_session", "新会话"),
            ("ctrl+r", "rename_session", "重命名"),
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
            self.stream_flush_timer = None

        @property
        def session_id(self) -> str | None:
            return self.model.current_id

        def _handle_exception(self, error: Exception) -> None:
            """未捕获异常退出前，先把完整 traceback 写入运行时日志。"""
            import datetime as _dt
            import sys as _sys
            import traceback
            from sjtu_agent.paths import DATA_DIR

            try:
                log_dir = DATA_DIR / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                with (log_dir / "tui_error.log").open("a", encoding="utf-8") as fh:
                    fh.write(f"\n[{_dt.datetime.now().isoformat()}] TUI unhandled exception\n")
                    traceback.print_exception(type(error), error, error.__traceback__, file=fh)
                print(
                    f"\n[TUI] 发生未处理异常，完整 traceback 已写入 {log_dir / 'tui_error.log'}",
                    file=_sys.stderr,
                )
            except Exception:
                pass
            super()._handle_exception(error)

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
            if self.session_id:
                await self.load_session(self.session_id)
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

        def action_rename_session(self) -> None:
            session = self.model.get_current()
            if not session:
                return
            session_id = session["id"]
            old_title = session.get("title", "新会话")

            def on_result(new_title: str | None) -> None:
                if not new_title or new_title == old_title:
                    return
                self.model.rename(session_id, new_title)
                self.schedule_worker(self.after_rename(session_id), group="session-op")

            self.push_screen(RenameModal(old_title), callback=on_result)

        async def after_rename(self, session_id: str) -> None:
            await self.refresh_sessions(select_id=session_id)
            await self.load_session(session_id)

        def action_delete_session(self) -> None:
            session_id = self.session_id
            if not session_id:
                return

            def on_result(confirmed: bool) -> None:
                if not confirmed:
                    return
                if self.busy:
                    cancel_turn(session_id)
                self.busy = False
                self.pending_approval = None
                self.schedule_worker(self.after_delete(session_id), group="session-op")

            self.push_screen(ConfirmDeleteModal(), callback=on_result)

        async def after_delete(self, session_id: str) -> None:
            self.model.delete(session_id)
            remaining = self.model.list_sessions()
            await self.refresh_sessions()
            if remaining:
                await self.load_session(remaining[0]["id"])
            else:
                session = self.model.create_session("新会话")
                await self.refresh_sessions(select_id=session.get("id"))
                await self.load_session(session.get("id") or "")

        def action_stop_turn(self) -> None:
            if not self.busy or not self.session_id:
                return
            cancel_turn(self.session_id)

        async def refresh_sessions(self, select_id: str | None = None) -> None:
            try:
                session_list = self.query_one("#sessions", ListView)
                await session_list.clear()
                target = select_id or self.session_id
                items = []
                for session in self.model.list_sessions():
                    items.append(
                        ListItem(
                            Label(session.get("title") or "未命名会话"),
                            id=f"session-{session['id']}",
                        )
                    )
                await session_list.extend(items)
                if target:
                    for index, session in enumerate(self.model.list_sessions()):
                        if session["id"] == target:
                            session_list.index = index
                            break
            except Exception:
                # 会话列表刷新失败不应导致 App 退出。
                return

        async def load_session(self, session_id: str) -> None:
            try:
                self.model.select(session_id)
                await self.messages.remove_children()
                session = self.model.get_current()
                title = (session or {}).get("title", "")
                widgets: list[Markdown] = [Markdown(f"# {title}")]
                for message in self.model.messages(session_id):
                    content = message.get("content", "")
                    if message.get("role") == "user":
                        widgets.append(Markdown(f"> **你**\n\n{display_text(content)}"))
                        continue
                    payload = parse_command_result(content)
                    if payload is not None:
                        widgets.append(Markdown(render_command_result(payload)))
                    else:
                        widgets.append(Markdown(display_text(content)))
                await self.messages.mount(*widgets)
                self.messages.scroll_end(animate=False)
            except Exception:
                return

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

        def schedule_worker(self, work, group: str, *, exclusive: bool = False) -> None:
            """启动 UI worker；任何刷新错误都不允许让 App 闪退。"""
            try:
                self.run_worker(
                    work,
                    group=group,
                    exclusive=exclusive,
                    exit_on_error=False,
                )
            except Exception:
                pass

        def schedule_stream_flush(self) -> None:
            """节流刷新流式 Markdown：最多每 80ms 更新一次，避免 worker 互斥取消。"""
            if self.stream_flush_timer is not None:
                return
            try:
                self.stream_flush_timer = self.set_timer(0.08, self.flush_stream)
            except Exception:
                self.stream_flush_timer = None

        def handle_approval(self, text: str) -> None:
            try:
                approval = self.pending_approval
                self.pending_approval = None
                approved = text.lower() in {"approve", "yes", "y", "同意", "允许"}
                if not approved and text.lower() not in {"deny", "no", "n", "拒绝"}:
                    self.pending_approval = approval
                    self.stream_text += "\n\n> 请输入 approve 或 deny。"
                    self.schedule_stream_flush()
                    return
                decide_approval(str(approval.get("approval_id", "")), approved)
                self.stream_text += (
                    f"\n\n_{'已批准' if approved else '已拒绝'}："
                    f"{approval.get('tool_name', '')}_"
                )
                self.schedule_stream_flush()
            except Exception:
                return

        async def start_turn(self, text: str) -> None:
            if self.session_id is None:
                session = self.model.create_session("新会话")
                await self.refresh_sessions(select_id=session.get("id"))
            session_id = self.model.ensure_for_message(text)
            if not session_id:
                await self.messages.mount(Markdown("> 无法创建会话"))
                return

            try:
                await self.messages.mount(
                    Markdown(f"> **你**\n\n{text}"),
                    Markdown(id="stream-markdown"),
                )
                self.messages.scroll_end(animate=False)
            except Exception:
                pass

            self.stream_text = ""
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
                    self.post_thread_event(self.render_event, event, turn_session)
            except Exception as exc:
                self.post_thread_event(
                    self.render_event,
                    {"kind": "error", "text": str(exc)},
                    turn_session,
                )
            finally:
                self.post_thread_event(self.finish_turn, turn_session)

        def post_thread_event(self, callback, *args) -> None:
            """从 worker 线程安全地投递 UI 回调；app 关闭后静默丢弃。"""
            if not getattr(self, "is_running", False):
                return
            try:
                self.call_from_thread(callback, *args)
            except Exception:
                pass

        # ── 渲染（App 线程）─────────────────────────────────────────────
        def render_event(self, event: dict[str, Any], turn_session: str) -> None:
            try:
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
                    try:
                        args_text = json.dumps(event.get("arguments", {}), ensure_ascii=False)
                    except Exception:
                        args_text = str(event.get("arguments", {}))
                    self.stream_text += (
                        f"\n\n⚠️ **需要确认：{event.get('tool_name', '')}**\n\n"
                        f"```json\n{args_text}\n```\n\n"
                        "输入 `approve` 或 `deny`"
                    )
                elif kind == "command_start":
                    self.stream_text += f"\n\n⚡ `{event.get('raw', '')}`"
                elif kind == "command_progress":
                    self.stream_text += f"\n\n_{event.get('message', '')}_"
                elif kind == "command_result":
                    payload = {
                        "view": event.get("view", "markdown"),
                        "text": event.get("text", ""),
                        "data": event.get("data", {}),
                    }
                    self.stream_text += "\n\n" + render_command_result(payload)
                elif kind == "error":
                    self.stream_text += f"\n\n❌ **错误：{event.get('text', '')}**"
                elif kind == "cancelled":
                    self.stream_text += "\n\n_已取消_"
                self.schedule_stream_flush()
            except Exception:
                # 任何事件渲染失败都不得让 App 闪退。
                return

        async def flush_stream(self) -> None:
            self.stream_flush_timer = None
            try:
                markdown = self.query_one("#stream-markdown", Markdown)
                await markdown.update(self.stream_text)
                self.messages.scroll_end(animate=False)
            except Exception:
                # 会话切换 / widget 被移除 / 更新失败都不允许闪退。
                return

        def finish_turn(self, turn_session: str) -> None:
            try:
                if turn_session == self.session_id:
                    self.busy = False
                    self.pending_approval = None
                if self.stream_flush_timer is not None:
                    try:
                        self.stream_flush_timer.stop()
                    except Exception:
                        pass
                    self.stream_flush_timer = None
                # 最后一次刷新用普通 worker（非 exclusive），完成后刷新会话列表。
                self.schedule_worker(self.flush_stream(), group="stream-final")
                self.schedule_worker(
                    self.refresh_sessions(select_id=turn_session),
                    group="sessions-refresh",
                )
            except Exception:
                return


def build_app() -> "App | None":
    if not TEXTUAL_AVAILABLE:
        return None
    return ChatApp()


def run_tui() -> int:
    if not TEXTUAL_AVAILABLE:
        import sys
        print("未安装 Textual。运行 `pip install -e \".[tui]\"` 后重试。", file=sys.stderr)
        return 1
    build_app().run()  # type: ignore[union-attr]
    return 0
