"""sjtu_agent/config.py — 统一配置访问层

替代全项目 30+ 处 json.loads(CONFIG_PATH.read_text())，提供：
- 单例缓存（同一进程内只读一次，除非文件变化）
- 类型化访问接口（消除 .get("key", default) 散落）
- 热重载（文件 mtime 变化时自动刷新）
- ENV_PATH .env 加载（最高优先级）

用法：
    from sjtu_agent.config import cfg
    token = cfg.canvas_token
    ids   = cfg.telegram_allowed_ids

或直接用底层 dict（向后兼容）：
    from sjtu_agent.config import cfg
    raw = cfg.raw()  # 返回完整 dict
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from sjtu_agent import paths as _paths


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _load_env() -> None:
    """加载 .env（幂等，重复调用无副作用）。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(_paths.ENV_PATH, override=False)  # 不覆盖已存在的环境变量
    except ImportError:
        pass


def _read_raw(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# ConfigStore
# ---------------------------------------------------------------------------

class ConfigStore:
    """线程安全的单例配置存储，带 mtime 热重载。"""

    _instance: "ConfigStore | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "ConfigStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = object.__new__(cls)
                    inst._data: dict = {}
                    inst._mtime: float = 0.0
                    inst._rlock = threading.RLock()
                    cls._instance = inst
        return cls._instance

    # ------------------------------------------------------------------
    # 读写底层
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        """无条件重新读取文件。"""
        _load_env()
        config_path = _paths.CONFIG_PATH
        with self._rlock:
            self._data = _read_raw(config_path)
            self._mtime = config_path.stat().st_mtime if config_path.exists() else 0.0

    def reload_if_changed(self) -> bool:
        """如果 config.json 文件变化，重新加载并返回 True，否则返回 False。"""
        config_path = _paths.CONFIG_PATH
        current_mtime = config_path.stat().st_mtime if config_path.exists() else 0.0
        if current_mtime != self._mtime:
            self._reload()
            return True
        return False

    def _ensure_loaded(self) -> None:
        config_path = _paths.CONFIG_PATH
        if not self._data and config_path.exists():
            self._reload()
        elif not self._data:
            _load_env()

    def raw(self) -> dict:
        """返回 config.json 的完整 dict 副本。"""
        self._ensure_loaded()
        with self._rlock:
            return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        self._ensure_loaded()
        with self._rlock:
            return self._data.get(key, default)

    def update(self, updates: dict) -> None:
        """将 updates 深合并写回 config.json 并刷新缓存。"""
        config_path = _paths.CONFIG_PATH
        with self._rlock:
            self._ensure_loaded()
            self._data.update(updates)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._mtime = config_path.stat().st_mtime

    def invalidate(self) -> None:
        """强制下次访问重新加载（外部写入 config.json 后调用）。"""
        with self._rlock:
            self._mtime = 0.0

    # ------------------------------------------------------------------
    # 类型化属性
    # ------------------------------------------------------------------

    # ── Canvas ──────────────────────────────────────────────────────────
    @property
    def canvas_token(self) -> str:
        return self.get("canvas_token", "")

    @property
    def canvas_base_url(self) -> str:
        return self.get("canvas_base_url", "https://oc.sjtu.edu.cn")

    # ── Telegram ────────────────────────────────────────────────────────
    @property
    def telegram_token(self) -> str:
        return self.get("telegram_token", "")

    @property
    def telegram_allowed_ids(self) -> list[int]:
        raw = self.get("telegram_allowed_ids", [])
        try:
            return [int(x) for x in raw]
        except (TypeError, ValueError):
            return []

    # ── jAccount ────────────────────────────────────────────────────────
    @property
    def jaccount_username(self) -> str:
        return os.environ.get("JACCOUNT_USERNAME", "").strip()

    @property
    def jaccount_password(self) -> str:
        return os.environ.get("JACCOUNT_PASSWORD", "").strip()

    def jaccount_credentials(self) -> tuple[str, str] | None:
        u, p = self.jaccount_username, self.jaccount_password
        return (u, p) if u and p else None

    # ── AI 好课（aihaoke） ──────────────────────────────────────────────
    @property
    def aihaoke_cookies(self) -> dict:
        return self.get("aihaoke_cookies", {})

    @property
    def aihaoke_token(self) -> str:
        return self.aihaoke_cookies.get("haoke-token", "").strip()

    @property
    def aihaoke_courses(self) -> list[dict] | None:
        return self.get("aihaoke_courses")

    # ── phycai ──────────────────────────────────────────────────────────
    @property
    def phycai_cookies(self) -> dict:
        return self.get("phycai_cookies", {})

    # ── icourse（中国大学 MOOC） ─────────────────────────────────────────
    @property
    def icourse_cookies(self) -> dict:
        return self.get("icourse_cookies", {})

    # ── WeChat ──────────────────────────────────────────────────────────
    @property
    def wechat_bot_token(self) -> str:
        return self.get("wechat_bot_token", "")

    # ── Feishu ─────────────────────────────────────────────────────────
    @property
    def feishu_app_id(self) -> str:
        return self.get("feishu_app_id", "")

    @property
    def feishu_app_secret(self) -> str:
        return self.get("feishu_app_secret", "")

    @property
    def feishu_allowed_open_ids(self) -> list[str]:
        return self.get("feishu_allowed_open_ids", [])

    # —— QQ ——--------------------------------------------------------------
    @property
    def qq_app_id(self) -> str:
        return str(self.get("qq_app_id", "")).strip()

    @property
    def qq_app_secret(self) -> str:
        return str(self.get("qq_app_secret", "")).strip()

    @property
    def qq_allowed_user_ids(self) -> list[str]:
        raw = self.get("qq_allowed_user_ids", [])
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        if isinstance(raw, str):
            parts = [p.strip() for p in raw.split(",")]
            return [p for p in parts if p]
        return []

    # ── 水源 ────────────────────────────────────────────────────────────
    @property
    def shuiyuan_user_api_key(self) -> str:
        return self.get("shuiyuan_user_api_key", "")

    @property
    def shuiyuan_cookies(self) -> dict:
        return self.get("shuiyuan_cookies", {})

    # ── 推送渠道开关 ────────────────────────────────────────────────────
    @property
    def telegram_enabled(self) -> bool:
        return bool(self.get("telegram_enabled", True))

    @property
    def wechat_enabled(self) -> bool:
        return bool(self.get("wechat_enabled", True))

    @property
    def feishu_enabled(self) -> bool:
        return bool(self.get("feishu_enabled", True))

    @property
    def qq_enabled(self) -> bool:
        return bool(self.get("qq_enabled", True))

    # ── DDL 紧急保底 ────────────────────────────────────────────────────
    @property
    def ddl_deadline_guard_enabled(self) -> bool:
        return bool(self.get("ddl_deadline_guard_enabled", True))

    @property
    def ddl_deadline_guard_minutes(self) -> int:
        return int(self.get("ddl_deadline_guard_minutes", 5))

    @property
    def ddl_deadline_guard_open_canvas(self) -> bool:
        return bool(self.get("ddl_deadline_guard_open_canvas", True))

    # ── 邮件 ────────────────────────────────────────────────────────────
    @property
    def email_imap_host(self) -> str:
        return os.environ.get("EMAIL_IMAP_HOST", "").strip()

    @property
    def email_smtp_host(self) -> str:
        return os.environ.get("EMAIL_SMTP_HOST", "").strip()

    @property
    def email_username(self) -> str:
        return os.environ.get("EMAIL_USERNAME", "").strip()

    @property
    def email_password(self) -> str:
        return os.environ.get("EMAIL_PASSWORD", "").strip()

    # ── 新闻聚合（news_digest） ─────────────────────────────────────────
    @property
    def news_digest_config(self) -> dict:
        return self.get("news_digest", {})

    # ── 通用访问 ────────────────────────────────────────────────────────
    def __repr__(self) -> str:
        return f"ConfigStore(path={CONFIG_PATH}, keys={list(self._data.keys())})"


# ---------------------------------------------------------------------------
# 模块级单例（便捷访问）
# ---------------------------------------------------------------------------

#: 全局单例，其他模块 `from sjtu_agent.config import cfg` 即可使用
cfg = ConfigStore()
