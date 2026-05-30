"""
sjtu_agent/scheduler/psmuxd.py — Windows psmux 后端实现

使用 psmux (tmux on Windows) 的分离会话管理后台服务。
psmux 会话在 psmux 服务端进程存活期间保持运行，适合常驻进程管理。
命名空间统一为 sjtu-agent（-L sjtu-agent），避免与用户的日常 psmux 会话混淆。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from sjtu_agent.paths import DATA_DIR, LOG_DIR

_NAMESPACE = "sjtu-agent"

_SERVICE_SPECS = {
    "daily-report": {
        "session_name": "daily-report",
        "subcommand": "daily-report",
    },
    "morning-report": {
        "session_name": "morning-report",
        "subcommand": "daily-report --type morning",
    },
    "noon-report": {
        "session_name": "noon-report",
        "subcommand": "daily-report --type noon",
    },
    "remind-check": {
        "session_name": "remind-check",
        "subcommand": "remind-check",
    },
    "telegram-bot": {
        "session_name": "telegram-bot",
        "subcommand": "telegram-bot",
    },
    "wechat-bot": {
        "session_name": "wechat-bot",
        "subcommand": "wechat-bot",
    },
    "feishu-bot": {
        "session_name": "feishu-bot",
        "subcommand": "feishu-bot",
    },
    "qq-bot": {
        "session_name": "qq-bot",
        "subcommand": "qq-bot",
    },
    "web": {
        "session_name": "web",
        "subcommand": "web --no-browser",
    },
}


def _find_psmux() -> str | None:
    return shutil.which("psmux")


def _run_psmux(*args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_find_psmux(), "-L", _NAMESPACE, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def install(
    service_names: tuple[str, ...] | None = None,
    python_executable: Path | None = None,
    daily_report_time: tuple[int, int] = (22, 0),
    remind_interval: int = 60,
    load: bool = True,
    **_,
) -> dict:
    """启动 psmux 分离会话来运行后台服务。"""
    if not _find_psmux():
        raise RuntimeError("未找到 psmux，请先安装：winget install psmux")

    py = str(python_executable or Path(sys.executable))
    selected = set(service_names or _SERVICE_SPECS.keys())
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue

        session = spec["session_name"]
        subcommand = spec["subcommand"]
        log_path = LOG_DIR / f"{name}.psmux.log"

        # 如果会话已存在，先 kill 再重建
        if not load:
            continue

        # 尝试 kill 已有会话（忽略失败）
        subprocess.run(
            [_find_psmux(), "-L", _NAMESPACE, "kill-session", "-t", session],
            capture_output=True, timeout=5,
        )

        # 启动新会话：psmux -L sjtu-agent new -s <name> -d -- python -m sjtu_agent <sub>
        result = subprocess.run(
            [
                _find_psmux(), "-L", _NAMESPACE,
                "new-session", "-s", session, "-d",
                "--", py, "-m", "sjtu_agent", subcommand,
            ],
            capture_output=True, text=True, timeout=15,
        )
        success = result.returncode == 0
        results.append({
            "name": name,
            "session_name": session,
            "log_path": str(log_path),
            "success": success,
            "error": result.stderr.strip() if not success else "",
        })

    return {
        "platform": "Windows (psmux)",
        "namespace": _NAMESPACE,
        "python_executable": py,
        "services": results,
    }


def uninstall(
    service_names: tuple[str, ...] | None = None,
    **_,
) -> dict:
    """终止 psmux 会话（停止后台服务）。"""
    if not _find_psmux():
        return {"platform": "Windows (psmux)", "removed": []}

    selected = set(service_names or _SERVICE_SPECS.keys())
    removed: list[dict] = []

    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue

        session = spec["session_name"]
        result = subprocess.run(
            [_find_psmux(), "-L", _NAMESPACE, "kill-session", "-t", session],
            capture_output=True, timeout=5,
        )
        removed.append({
            "name": name,
            "session_name": session,
            "success": result.returncode == 0,
        })

    return {"platform": "Windows (psmux)", "removed": removed}


def status(
    service_names: tuple[str, ...] | None = None,
    **_,
) -> dict:
    """检查 psmux 会话是否存在（即服务是否运行中）。"""
    if not _find_psmux():
        return {"platform": "Windows (psmux)", "services": [], "all_installed": False}

    selected = set(service_names or _SERVICE_SPECS.keys())
    services: list[dict] = []

    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue

        session = spec["session_name"]
        result = subprocess.run(
            [_find_psmux(), "-L", _NAMESPACE, "has-session", "-t", session],
            capture_output=True, timeout=5,
        )
        running = result.returncode == 0
        services.append({
            "name": name,
            "session_name": session,
            "running": running,
        })

    return {
        "platform": "Windows (psmux)",
        "services": services,
        "all_running": bool(services) and all(s["running"] for s in services),
    }
