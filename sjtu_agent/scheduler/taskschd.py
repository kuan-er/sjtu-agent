"""
sjtu_agent/scheduler/taskschd.py — Windows Task Scheduler 实现

使用 schtasks 命令行工具管理后台服务：
  - daily-report   : 每天定时触发（如 22:00）
  - remind-check   : 每分钟循环触发
  - telegram-bot   : 登录时自动启动，保持常驻
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sjtu_agent.paths import DATA_DIR, LOG_DIR

# Windows 任务计划程序中的任务名前缀
_TASK_PREFIX = "SJTUAgent"

_SERVICE_SPECS = {
    "daily-report": {
        "task_name": f"{_TASK_PREFIX}-DailyReport",
        "subcommand": "daily-report",
        "log": "daily_report.task.log",
        "schedule": "daily",    # 每天定时
    },
    "morning-report": {
        "task_name": f"{_TASK_PREFIX}-MorningReport",
        "subcommand": "daily-report --type morning",
        "log": "morning_report.task.log",
        "schedule": "daily",
    },
    "noon-report": {
        "task_name": f"{_TASK_PREFIX}-NoonReport",
        "subcommand": "daily-report --type noon",
        "log": "noon_report.task.log",
        "schedule": "daily",
    },
    "homework-check": {
        "task_name": f"{_TASK_PREFIX}-HomeworkCheck",
        "subcommand": "daily-report --type evening",  # placeholder, runs homework_agent
        "log": "homework_check.task.log",
        "schedule": "daily",
    },
    "remind-check": {
        "task_name": f"{_TASK_PREFIX}-RemindCheck",
        "subcommand": "remind-check",
        "log": "remind_check.task.log",
        "schedule": "minute",   # 每分钟
    },
    "telegram-bot": {
        "task_name": f"{_TASK_PREFIX}-TelegramBot",
        "subcommand": "telegram-bot",
        "log": "telegram_bot.task.log",
        "schedule": "onlogon",  # 登录时启动
    },
    "wechat-bot": {
        "task_name": f"{_TASK_PREFIX}-WeChatBot",
        "subcommand": "wechat-bot",
        "log": "wechat_bot.task.log",
        "schedule": "onlogon",  # 登录时启动
    },
    "feishu-bot": {
        "task_name": f"{_TASK_PREFIX}-FeishuBot",
        "subcommand": "feishu-bot",
        "log": "feishu_bot.task.log",
        "schedule": "onlogon",  # 登录时启动
    },
    "qq-bot": {
        "task_name": f"{_TASK_PREFIX}-QQBot",
        "subcommand": "qq-bot",
        "log": "qq_bot.task.log",
        "schedule": "onlogon",  # 登录时启动
    },
    "web": {
        "task_name": f"{_TASK_PREFIX}-Web",
        "subcommand": "web --no-browser",
        "log": "web.task.log",
        "schedule": "onlogon",  # 登录时启动
    },
}


def _task_exists(task_name: str) -> bool:
    """检查任务计划中是否已存在该任务。"""
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _delete_task(task_name: str) -> None:
    """删除任务（若存在）。"""
    subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True,
    )


def install(
    service_names: tuple[str, ...] | None = None,
    python_executable: Path | None = None,
    daily_report_time: tuple[int, int] = (22, 0),
    remind_interval: int = 60,
    load: bool = True,
    **_,
) -> dict:
    """
    安装 Windows 任务计划服务。

    注意：
      - remind_interval 在 Windows 下会被自动换算为分钟（最小精度 1 分钟）。
      - 任务以当前登录用户身份运行，不需要提升权限。
    """
    if sys.platform != "win32":
        raise RuntimeError("Task Scheduler 安装仅支持 Windows。")

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    py = str(python_executable or sys.executable)
    selected = set(service_names or _SERVICE_SPECS.keys())
    hour, minute = daily_report_time
    # remind_interval 转换为分钟，向上取整，最小 1 分钟
    remind_minutes = max(1, (remind_interval + 59) // 60)

    # ── 卸载不再需要的已知服务 ────────────────────────────────────────────
    for name, spec in _SERVICE_SPECS.items():
        if name in selected:
            continue
        task_name = spec["task_name"]
        if _task_exists(task_name):
            _delete_task(task_name)

    written: list[dict] = []
    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue

        task_name = spec["task_name"]
        subcommand = spec["subcommand"]
        log_path = LOG_DIR / spec["log"]

        # pythonw.exe 无控制台窗口，日志由各子命令的 logging 模块写入文件
        pyw = str(Path(py).with_name("pythonw.exe"))
        program = pyw
        arguments = f"-m sjtu_agent {subcommand}"

        # 先删除已存在的同名任务
        _delete_task(task_name)

        # 根据调度类型构建 schtasks 参数
        if spec["schedule"] == "daily":
            # 不同报告类型使用不同时间
            if name == "morning-report":
                hh, mm = 8, 0
            elif name == "noon-report":
                hh, mm = 12, 0
            else:
                hh, mm = hour, minute
            schtask_args = [
                "schtasks", "/Create",
                "/TN", task_name,
                "/TR", f'"{program}" {arguments}',
                "/SC", "DAILY",
                "/ST", f"{hh:02d}:{mm:02d}",
                "/F",
            ]
        elif spec["schedule"] == "minute":
            schtask_args = [
                "schtasks", "/Create",
                "/TN", task_name,
                "/TR", f'"{program}" {arguments}',
                "/SC", "MINUTE",
                "/MO", str(remind_minutes),
                "/F",
            ]
        else:  # onlogon（telegram-bot）
            schtask_args = [
                "schtasks", "/Create",
                "/TN", task_name,
                "/TR", f'"{program}" {arguments}',
                "/SC", "ONLOGON",
                "/F",
            ]

        result = subprocess.run(schtask_args, capture_output=True, text=True)
        success = result.returncode == 0
        written.append({
            "name": name,
            "task_name": task_name,
            "log_path": str(log_path),
            "success": success,
            "error": result.stderr.strip() if not success else "",
        })

        # 立即触发一次（telegram-bot / wechat-bot / web，类似 run_at_load）
        if load and success and name in ("telegram-bot", "wechat-bot", "feishu-bot", "qq-bot", "web"):
            subprocess.run(["schtasks", "/Run", "/TN", task_name], capture_output=True)

    return {
        "platform": "Windows (Task Scheduler)",
        "python_executable": py,
        "services": written,
        "note": (
            f"日报时间: {hour:02d}:{minute:02d}，"
            f"提醒检查间隔: {remind_minutes} 分钟。"
            "任务已注册到当前用户的任务计划中，重启后自动生效。"
        ),
    }


def uninstall(
    service_names: tuple[str, ...] | None = None,
    **_,
) -> dict:
    """删除 Windows 任务计划中的服务。"""
    if sys.platform != "win32":
        raise RuntimeError("Task Scheduler 卸载仅支持 Windows。")

    selected = set(service_names or _SERVICE_SPECS.keys())
    removed: list[dict] = []

    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue
        task_name = spec["task_name"]
        existed = _task_exists(task_name)
        _delete_task(task_name)
        removed.append({"name": name, "task_name": task_name, "was_present": existed})

    return {"platform": "Windows (Task Scheduler)", "removed": removed}


def status(
    service_names: tuple[str, ...] | None = None,
    **_,
) -> dict:
    """查询 Windows 任务计划服务状态。"""
    selected = set(service_names or _SERVICE_SPECS.keys())
    services: list[dict] = []

    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue
        task_name = spec["task_name"]
        installed = _task_exists(task_name)
        services.append({
            "name": name,
            "task_name": task_name,
            "installed": installed,
        })

    return {
        "platform": "Windows (Task Scheduler)",
        "services": services,
        "all_installed": bool(services) and all(s["installed"] for s in services),
    }
