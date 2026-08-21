"""sjtu_agent/feishu/conversations.py — multi-conversation management for Feishu bot.

FeishuConversationManager owns per-user session state: multiple named
conversations per user, persisted to disk with atomic saves.
"""
from __future__ import annotations

import json
import threading
import datetime as _dt
from pathlib import Path


class FeishuConversationManager:
    """Manages multiple named conversations per Feishu user.

    Usage::

        mgr = FeishuConversationManager(DATA_DIR)
        mgr.load()                          # restore from disk
        conv, meta, lock = mgr.get_active("ou_xxx")
        result = mgr.handle_command("ou_xxx", "/new", ["/new", "作业讨论"])
    """

    def __init__(self, data_dir: Path, max_age_days: int = 30):
        self._data_dir = data_dir
        self._sessions_file = data_dir / "feishu_sessions.json"
        self._max_age_days = max_age_days

        # per-user state
        self.sessions: dict[str, dict] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.meta_lock = threading.RLock()
        self._save_lock = threading.Lock()

    # ── persistence ──────────────────────────────────────────────────────

    def load(self) -> None:
        """Restore session state from disk."""
        if not self._sessions_file.exists():
            return
        try:
            import agent
            with self._save_lock:
                data = json.loads(self._sessions_file.read_text(encoding="utf-8"))
            cutoff = _dt.datetime.now().timestamp() - self._max_age_days * 86400
            with self.meta_lock:
                for open_id, meta in data.items():
                    convs = []
                    for c in meta.get("conversations", []):
                        if c.get("saved_at", 0) < cutoff:
                            continue
                        agent_cfg = agent.load_agent_config()
                        c["model_box"] = [agent_cfg.get("model", "deepseek-chat")]
                        c["client_box"] = [agent._make_client(agent_cfg) if agent_cfg else None]
                        convs.append(c)
                    if convs:
                        self.sessions[open_id] = {
                            "conversations": convs,
                            "current_idx": min(meta.get("current_idx", 0), len(convs) - 1),
                            "next_name_id": meta.get("next_name_id", len(convs) + 1),
                        }
                        self.locks[open_id] = threading.Lock()
            if self.sessions:
                total = sum(len(m["conversations"]) for m in self.sessions.values())
                print(f"[feishu] 已恢复 {len(self.sessions)} 个用户的 {total} 个对话")
        except Exception as e:
            print(f"[feishu] 会话恢复失败: {e}")

    def save(self) -> None:
        """Persist current session state to disk (last 200 msgs per conversation)."""
        try:
            self._sessions_file.parent.mkdir(parents=True, exist_ok=True)
            now_ts = _dt.datetime.now().timestamp()
            with self.meta_lock:
                data = {}
                for open_id, meta in self.sessions.items():
                    data[open_id] = {
                        "current_idx": meta["current_idx"],
                        "next_name_id": meta["next_name_id"],
                        "conversations": [{
                            "name": c["name"],
                            "messages": c["messages"][-200:],
                            "created_at": c["created_at"],
                            "saved_at": now_ts,
                        } for c in meta["conversations"]],
                    }
            with self._save_lock:
                self._sessions_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[feishu] 会话保存失败: {e}")

    # ── user / conversation access ────────────────────────────────────────

    def _new_conv_dict(self, name: str) -> dict:
        import agent
        agent_cfg = agent.load_agent_config()
        return {
            "name": name,
            "messages": [],
            "model_box": [agent_cfg.get("model", "deepseek-chat")],
            "client_box": [agent._make_client(agent_cfg) if agent_cfg.get("api_key") else None],
            "created_at": _dt.datetime.now().strftime("%m-%d %H:%M"),
        }

    def ensure_user(self, open_id: str) -> None:
        with self.meta_lock:
            if open_id not in self.sessions:
                self.sessions[open_id] = {
                    "conversations": [self._new_conv_dict("默认")],
                    "current_idx": 0,
                    "next_name_id": 1,
                }
                self.locks[open_id] = threading.Lock()

    def get_active(self, open_id: str) -> tuple[dict, dict, threading.Lock]:
        """Return (conversation, user_meta, lock) for the active conversation."""
        self.ensure_user(open_id)
        with self.meta_lock:
            meta = self.sessions[open_id]
            idx = meta["current_idx"]
            conv = meta["conversations"][idx]
            return conv, meta, self.locks[open_id]

    # ── command dispatch ──────────────────────────────────────────────────

    def handle_command(self, open_id: str, cmd: str, parts: list[str]) -> str | None:
        """Dispatch conversation commands. Returns result string or None if not handled."""
        self.ensure_user(open_id)
        with self.meta_lock:
            meta = self.sessions[open_id]
            convs = meta["conversations"]
            n = len(convs)

            if cmd == "/list":
                return self._cmd_list(convs, meta, n)
            if cmd == "/new":
                return self._cmd_new(convs, meta, n, parts)
            if cmd == "/switch":
                return self._cmd_switch(convs, meta, n, parts)
            if cmd == "/name":
                return self._cmd_name(convs, meta, n, parts)
            if cmd == "/delete":
                return self._cmd_delete(convs, meta, n, parts)
            if cmd == "/history":
                return self._cmd_history(convs, meta)
            return None

    def _cmd_list(self, convs, meta, n) -> str:
        lines = [f"共 {n} 个对话："]
        for i, c in enumerate(convs):
            marker = " ← 当前" if i == meta["current_idx"] else ""
            msg_count = len([m for m in c["messages"] if m.get("role") == "user"])
            lines.append(f"  [{i+1}] {c['name']}（{msg_count} 条消息, {c['created_at']}）{marker}")
        return "\n".join(lines)

    def _cmd_new(self, convs, meta, n, parts) -> str:
        name = parts[1].strip() if len(parts) > 1 else f"对话 {meta['next_name_id']}"
        meta["next_name_id"] += 1
        convs.append(self._new_conv_dict(name))
        meta["current_idx"] = len(convs) - 1
        self.save()
        return f"[OK] 已创建并切换到对话「{name}」（序号 {len(convs)}）"

    def _cmd_switch(self, convs, meta, n, parts) -> str:
        if len(parts) < 2:
            return "用法：/switch <序号>，用 /list 查看序号"
        try:
            idx = int(parts[1]) - 1
        except ValueError:
            return f"无效序号：{parts[1]}"
        if idx < 0 or idx >= n:
            return f"无效序号，共 {n} 个对话（1~{n}）"
        meta["current_idx"] = idx
        self.save()
        return f"[OK] 已切换到对话「{convs[idx]['name']}」（序号 {idx + 1}）"

    def _cmd_name(self, convs, meta, n, parts) -> str:
        if len(parts) < 3:
            return "用法：/name <序号> <新名称>"
        try:
            idx = int(parts[1]) - 1
        except ValueError:
            return f"无效序号：{parts[1]}"
        if idx < 0 or idx >= n:
            return f"无效序号，共 {n} 个对话（1~{n}）"
        old_name = convs[idx]["name"]
        convs[idx]["name"] = parts[2].strip()
        self.save()
        return f"[OK] 已将对话 [{idx + 1}]「{old_name}」重命名为「{convs[idx]['name']}」"

    def _cmd_delete(self, convs, meta, n, parts) -> str:
        if len(parts) < 2:
            return "用法：/delete <序号>"
        try:
            idx = int(parts[1]) - 1
        except ValueError:
            return f"无效序号：{parts[1]}"
        if idx < 0 or idx >= n:
            return f"无效序号，共 {n} 个对话（1~{n}）"
        if n <= 1:
            return "[X] 至少保留一个对话"
        name = convs[idx]["name"]
        del convs[idx]
        if meta["current_idx"] >= len(convs):
            meta["current_idx"] = len(convs) - 1
        elif meta["current_idx"] > idx:
            meta["current_idx"] -= 1
        self.save()
        return f"[OK] 已删除对话「{name}」，当前对话：「{convs[meta['current_idx']]['name']}」"

    def _cmd_history(self, convs, meta) -> str:
        conv = convs[meta["current_idx"]]
        user_msgs = [m for m in conv["messages"] if m.get("role") == "user"]
        if not user_msgs:
            return f"对话「{conv['name']}」暂无消息记录。"
        lines = [f"对话「{conv['name']}」最近 {min(len(user_msgs), 10)} 条消息："]
        for i, m in enumerate(user_msgs[-10:]):
            lines.append(f"  {i+1}. {m.get('content', '')[:60]}")
        return "\n".join(lines)
