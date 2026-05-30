"""
sjtu_agent/scheduler — 跨平台后台守护进程调度层

根据当前操作系统自动选择合适的实现：
  - macOS   → launchd（plist + launchctl）
  - Windows → Task Scheduler（schtasks 命令行）
  - Linux   → systemd 用户单元（systemctl --user）

公共接口：
  install_daemons(...)   安装并启动后台服务
  uninstall_daemons(...) 停止并卸载后台服务
  daemon_status(...)     查询后台服务状态
"""

from __future__ import annotations

import sys
from pathlib import Path


def _default_service_names() -> tuple[str, ...]:
    """根据 config.json 中的推送渠道开关返回默认服务列表。"""
    try:
        from sjtu_agent.paths import CONFIG_PATH
        import json
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    except Exception:
        cfg = {}
    names = list(available_service_names()) + ["web", "news-digest"]
    if not cfg.get("telegram_enabled", True) and "telegram-bot" in names:
        names.remove("telegram-bot")
    if not cfg.get("wechat_enabled", True) and "wechat-bot" in names:
        names.remove("wechat-bot")
    if not cfg.get("feishu_enabled", True) and "feishu-bot" in names:
        names.remove("feishu-bot")
    if not cfg.get("qq_enabled", True) and "qq-bot" in names:
        names.remove("qq-bot")
    return tuple(names)


def install_daemons(
    service_names: tuple[str, ...] | None = None,
    python_executable: Path | None = None,
    daily_report_time: tuple[int, int] = (22, 0),
    remind_interval: int = 60,
    telegram_throttle: int = 10,
    load: bool = True,
    backend: str = "taskschd",
    **platform_kwargs,
) -> dict:
    """
    安装后台守护进程。

    参数：
        service_names       要安装的服务子集，默认全部
        python_executable   使用的 Python 解释器路径，默认当前解释器
        daily_report_time   日报发送时间 (hour, minute)，默认 (22, 0)
        remind_interval     提醒检查间隔秒数（macOS/Linux 适用），默认 60
        telegram_throttle   Telegram bot 重启节流秒数（macOS 适用），默认 10
        load                是否立即加载/启动服务，默认 True
        backend             Windows 后端选择：taskschd（默认）或 psmux
        **platform_kwargs   各平台专属参数（如 macOS 的 output_dir）

    返回包含安装结果的字典。
    """
    if sys.platform == "darwin":
        from sjtu_agent.scheduler.launchd import install as _install
    elif sys.platform == "win32":
        if backend == "psmux":
            from sjtu_agent.scheduler.psmuxd import install as _install
        else:
            from sjtu_agent.scheduler.taskschd import install as _install
    elif sys.platform.startswith("linux"):
        from sjtu_agent.scheduler.systemd import install as _install
    else:
        raise RuntimeError(
            f"不支持的平台: {sys.platform}。"
            "目前支持 macOS (darwin)、Windows (win32)、Linux。"
        )

    if service_names is None:
        service_names = _default_service_names()

    return _install(
        service_names=service_names,
        python_executable=python_executable,
        daily_report_time=daily_report_time,
        remind_interval=remind_interval,
        telegram_throttle=telegram_throttle,
        load=load,
        **platform_kwargs,
    )


def uninstall_daemons(
    service_names: tuple[str, ...] | None = None,
    backend: str = "taskschd",
    **platform_kwargs,
) -> dict:
    """
    卸载后台守护进程。

    参数：
        service_names  要卸载的服务子集，默认全部
        backend        Windows 后端选择：taskschd（默认）或 psmux
    """
    if sys.platform == "darwin":
        from sjtu_agent.scheduler.launchd import uninstall as _uninstall
    elif sys.platform == "win32":
        if backend == "psmux":
            from sjtu_agent.scheduler.psmuxd import uninstall as _uninstall
        else:
            from sjtu_agent.scheduler.taskschd import uninstall as _uninstall
    elif sys.platform.startswith("linux"):
        from sjtu_agent.scheduler.systemd import uninstall as _uninstall
    else:
        raise RuntimeError(f"不支持的平台: {sys.platform}")

    return _uninstall(service_names=service_names, **platform_kwargs)


def daemon_status(
    service_names: tuple[str, ...] | None = None,
    backend: str = "taskschd",
    **platform_kwargs,
) -> dict:
    """
    查询后台守护进程状态。

    参数：
        service_names  要查询的服务子集，默认全部
        backend        Windows 后端选择：taskschd（默认）或 psmux
    返回包含各服务状态的字典。
    """
    if sys.platform == "darwin":
        from sjtu_agent.scheduler.launchd import status as _status
    elif sys.platform == "win32":
        if backend == "psmux":
            from sjtu_agent.scheduler.psmuxd import status as _status
        else:
            from sjtu_agent.scheduler.taskschd import status as _status
    elif sys.platform.startswith("linux"):
        from sjtu_agent.scheduler.systemd import status as _status
    else:
        return {"error": f"不支持的平台: {sys.platform}", "services": []}

    return _status(service_names=service_names, **platform_kwargs)


def available_service_names() -> tuple[str, ...]:
    """返回所有可用的服务名称。"""
    return ("daily-report", "morning-report", "noon-report", "remind-check",
            "telegram-bot", "wechat-bot", "feishu-bot", "qq-bot")


def current_platform_name() -> str:
    """返回当前平台的友好名称。"""
    if sys.platform == "darwin":
        return "macOS (launchd)"
    elif sys.platform == "win32":
        return "Windows (psmux / Task Scheduler)"
    elif sys.platform.startswith("linux"):
        return "Linux (systemd)"
    else:
        return sys.platform
