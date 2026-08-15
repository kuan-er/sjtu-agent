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
  resync_daemons(...)    根据安装清单恢复后台服务
"""

from __future__ import annotations

import sys
from pathlib import Path

from sjtu_agent.paths import DAEMON_MANIFEST_PATH
from sjtu_agent.scheduler.manifest import (
    forget_deployment,
    list_deployments,
    remember_deployment,
)


def _platform_backend(backend: str = "taskschd") -> str:
    """返回当前平台实际使用的后端标识（Windows 的 backend 参数透传）。"""
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform == "win32":
        return "psmux" if backend == "psmux" else "taskschd"
    if sys.platform.startswith("linux"):
        return "systemd"
    return backend


def _default_service_names() -> tuple[str, ...]:
    """根据 config.json 中的推送渠道开关返回默认服务列表。"""
    from sjtu_agent.config import cfg as _cfg
    _cfg.reload_if_changed()
    cfg = _cfg.raw()
    names = list(available_service_names())
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
    actual_backend = _platform_backend(backend)
    if sys.platform == "darwin":
        from sjtu_agent.scheduler.launchd import install as _install
    elif sys.platform == "win32":
        if actual_backend == "psmux":
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

    result = _install(
        service_names=service_names,
        python_executable=python_executable,
        daily_report_time=daily_report_time,
        remind_interval=remind_interval,
        telegram_throttle=telegram_throttle,
        load=load,
        **platform_kwargs,
    )

    # 只有真正写入/加载成功才记入清单；load=False 的 write-only 调用不记录。
    if load:
        remember_deployment(
            actual_backend,
            service_names,
            python_executable or sys.executable,
            daily_report_time=daily_report_time,
            remind_interval=remind_interval,
            telegram_throttle=telegram_throttle,
            output_dir=platform_kwargs.get("output_dir"),
        )
    return result


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
    actual_backend = _platform_backend(backend)
    if sys.platform == "darwin":
        from sjtu_agent.scheduler.launchd import uninstall as _uninstall
    elif sys.platform == "win32":
        if actual_backend == "psmux":
            from sjtu_agent.scheduler.psmuxd import uninstall as _uninstall
        else:
            from sjtu_agent.scheduler.taskschd import uninstall as _uninstall
    elif sys.platform.startswith("linux"):
        from sjtu_agent.scheduler.systemd import uninstall as _uninstall
    else:
        raise RuntimeError(f"不支持的平台: {sys.platform}")

    result = _uninstall(service_names=service_names, **platform_kwargs)
    forget_deployment(actual_backend, service_names)
    return result


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


def resync_daemons(python_executable: Path | None = None) -> dict:
    """
    根据安装清单恢复此前安装过的后台服务。

    用于代码目录移动、重新 clone 或 .venv 重建之后：安装脚本和
    `sjtu-agent update` 会调用它。只有检测到服务缺失，或记录的 Python
    解释器路径与当前路径不同时才重新注册；其他情况不做任何改动。
    """
    current_py = str(python_executable or sys.executable)
    results: list[dict] = []

    for deployment in list_deployments():
        backend = deployment["backend"]
        service_names = tuple(deployment["services"])
        old_py = deployment["python_executable"]

        try:
            state = daemon_status(service_names=service_names, backend=backend)
        except Exception as exc:
            results.append({
                "backend": backend,
                "services": list(service_names),
                "resynced": False,
                "error": f"查询状态失败：{exc}",
            })
            continue

        all_ok = bool(state.get("all_installed", False) or state.get("all_running", False))
        path_changed = bool(old_py) and Path(current_py).resolve() != Path(old_py).resolve()

        if all_ok and not path_changed:
            results.append({
                "backend": backend,
                "services": list(service_names),
                "resynced": False,
                "reason": "already installed",
            })
            continue

        kwargs: dict = {
            "daily_report_time": tuple(deployment.get("daily_report_time") or (22, 0)),
            "remind_interval": int(deployment.get("remind_interval") or 60),
            "telegram_throttle": int(deployment.get("telegram_throttle") or 10),
        }
        output_dir = deployment.get("output_dir")
        if output_dir:
            kwargs["output_dir"] = Path(output_dir)

        try:
            install_daemons(
                service_names=service_names,
                python_executable=Path(current_py),
                backend=backend,
                **kwargs,
            )
            results.append({
                "backend": backend,
                "services": list(service_names),
                "resynced": True,
                "reason": "python path changed" if path_changed else "service missing",
            })
        except Exception as exc:
            results.append({
                "backend": backend,
                "services": list(service_names),
                "resynced": False,
                "error": str(exc),
            })

    return {
        "platform": current_platform_name(),
        "python_executable": current_py,
        "manifest_path": str(DAEMON_MANIFEST_PATH),
        "results": results,
        "any_resynced": any(r.get("resynced") for r in results),
    }


def available_service_names() -> tuple[str, ...]:
    """返回所有可用的服务名称。"""
    return ("daily-report", "morning-report", "noon-report", "remind-check",
            "email-watcher", "canvas-watcher", "aihot-push",
            "telegram-bot", "wechat-bot", "feishu-bot", "qq-bot",
            "web", "news-digest")


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
