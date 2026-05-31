"""
sjtu_agent/scheduler/systemd.py — Linux systemd 用户单元实现

使用 ~/.config/systemd/user/ 下的 .service 和 .timer 文件管理后台服务，
通过 `systemctl --user` 命令加载和控制：
  - daily-report   : 每天定时触发的 timer + service
  - remind-check   : 每分钟循环触发的 timer + service
  - telegram-bot   : 开机自启的 service（Restart=always）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sjtu_agent.paths import DATA_DIR, LOG_DIR

_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"

_SERVICE_SPECS = {
    "daily-report": {
        "unit_name": "sjtu-agent-daily-report",
        "subcommand": "daily-report",
        "log": "daily_report.systemd.log",
        "restart": "no",
        "has_timer": True,
        "timer_type": "calendar",
    },
    "morning-report": {
        "unit_name": "sjtu-agent-morning-report",
        "subcommand": "daily-report --type morning",
        "log": "morning_report.systemd.log",
        "restart": "no",
        "has_timer": True,
        "timer_type": "calendar",
    },
    "noon-report": {
        "unit_name": "sjtu-agent-noon-report",
        "subcommand": "daily-report --type noon",
        "log": "noon_report.systemd.log",
        "restart": "no",
        "has_timer": True,
        "timer_type": "calendar",
    },
    "remind-check": {
        "unit_name": "sjtu-agent-remind-check",
        "subcommand": "remind-check",
        "log": "remind_check.systemd.log",
        "restart": "no",
        "has_timer": True,
        "timer_type": "interval",   # OnUnitInactiveSec
    },
    "telegram-bot": {
        "unit_name": "sjtu-agent-telegram-bot",
        "subcommand": "telegram-bot",
        "log": "telegram_bot.systemd.log",
        "restart": "always",
        "has_timer": False,
        "wants_after": "network-online.target",
    },
    "wechat-bot": {
        "unit_name": "sjtu-agent-wechat-bot",
        "subcommand": "wechat-bot",
        "log": "wechat_bot.systemd.log",
        "restart": "always",
        "has_timer": False,
        "wants_after": "network-online.target",
    },
    "feishu-bot": {
        "unit_name": "sjtu-agent-feishu-bot",
        "subcommand": "feishu-bot",
        "log": "feishu_bot.systemd.log",
        "restart": "always",
        "has_timer": False,
        "wants_after": "network-online.target",
    },
    "email-watcher": {
        "unit_name": "sjtu-agent-email-watcher",
        "subcommand": "email-watcher",
        "log": "email_watcher.systemd.log",
        "restart": "always",
        "has_timer": False,
        "wants_after": "network-online.target",
    },
    "qq-bot": {
        "unit_name": "sjtu-agent-qq-bot",
        "subcommand": "qq-bot",
        "log": "qq_bot.systemd.log",
        "restart": "always",
        "has_timer": False,
        "wants_after": "network-online.target",
    },
}


def _write_service_unit(
    unit_name: str,
    python_executable: str,
    subcommand: str,
    log_path: Path,
    restart: str,
    wants_after: str = "network.target",
) -> Path:
    """生成并写入 .service 单元文件，返回文件路径。"""
    content = f"""\
[Unit]
Description=SJTU Agent — {subcommand}
After={wants_after}

[Service]
Type=simple
ExecStart={python_executable} -m sjtu_agent {subcommand}
WorkingDirectory={DATA_DIR}
StandardOutput=append:{log_path}
StandardError=append:{log_path}
Environment=PYTHONUNBUFFERED=1
Restart={restart}
RestartSec=10

[Install]
WantedBy=default.target
"""
    unit_path = _SYSTEMD_USER_DIR / f"{unit_name}.service"
    unit_path.write_text(content, encoding="utf-8")
    return unit_path


def _write_timer_unit(
    unit_name: str,
    timer_type: str,
    daily_report_time: tuple[int, int],
    remind_interval: int,
) -> Path:
    """生成并写入 .timer 单元文件，返回文件路径。"""
    if timer_type == "calendar":
        hour, minute = daily_report_time
        schedule = f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00"
    else:
        # OnUnitInactiveSec：上次运行结束后等待 N 秒再次触发
        schedule = f"OnUnitInactiveSec={remind_interval}s\nOnBootSec=30s"

    content = f"""\
[Unit]
Description=SJTU Agent timer — {unit_name}

[Timer]
{schedule}
Persistent=true

[Install]
WantedBy=timers.target
"""
    timer_path = _SYSTEMD_USER_DIR / f"{unit_name}.timer"
    timer_path.write_text(content, encoding="utf-8")
    return timer_path


def _systemctl(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user"] + args,
        capture_output=True, text=True,
    )


def install(
    service_names: tuple[str, ...] | None = None,
    python_executable: Path | None = None,
    daily_report_time: tuple[int, int] = (22, 0),
    remind_interval: int = 60,
    load: bool = True,
    **_,
) -> dict:
    """安装 systemd 用户服务。"""
    if sys.platform != "linux" and not sys.platform.startswith("linux"):
        raise RuntimeError("systemd 安装仅支持 Linux。")

    _SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    py = str(python_executable or sys.executable)
    selected = set(service_names or _SERVICE_SPECS.keys())

    # ── 卸载不再需要的已知服务 ────────────────────────────────────────────
    for name, spec in _SERVICE_SPECS.items():
        if name in selected:
            continue
        unit_name = spec["unit_name"]
        if spec["has_timer"]:
            _systemctl(["stop", f"{unit_name}.timer"])
            _systemctl(["disable", f"{unit_name}.timer"])
            timer_path = _SYSTEMD_USER_DIR / f"{unit_name}.timer"
            if timer_path.exists():
                timer_path.unlink()
        else:
            _systemctl(["stop", f"{unit_name}.service"])
            _systemctl(["disable", f"{unit_name}.service"])
        service_path = _SYSTEMD_USER_DIR / f"{unit_name}.service"
        if service_path.exists():
            service_path.unlink()
    _systemctl(["daemon-reload"])

    written: list[dict] = []

    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue

        unit_name = spec["unit_name"]
        log_path = LOG_DIR / spec["log"]
        wants_after = spec.get("wants_after", "network.target")

        service_path = _write_service_unit(
            unit_name=unit_name,
            python_executable=py,
            subcommand=spec["subcommand"],
            log_path=log_path,
            restart=spec["restart"],
            wants_after=wants_after,
        )

        timer_path: Path | None = None
        if spec["has_timer"]:
            timer_path = _write_timer_unit(
                unit_name=unit_name,
                timer_type=spec["timer_type"],
                daily_report_time=daily_report_time,
                remind_interval=remind_interval,
            )

        item: dict = {
            "name": name,
            "unit_name": unit_name,
            "service_path": str(service_path),
            "timer_path": str(timer_path) if timer_path else None,
            "log_path": str(log_path),
        }

        if load:
            _systemctl(["daemon-reload"])
            if timer_path:
                # 启用并启动 timer（service 由 timer 触发，不直接 enable service）
                _systemctl(["enable", f"{unit_name}.timer"])
                r = _systemctl(["start", f"{unit_name}.timer"])
            else:
                # telegram-bot：直接 enable + start service
                _systemctl(["enable", f"{unit_name}.service"])
                r = _systemctl(["start", f"{unit_name}.service"])
            item["load_success"] = r.returncode == 0
            item["load_error"] = r.stderr.strip() if r.returncode != 0 else ""

        written.append(item)

    return {
        "platform": "Linux (systemd)",
        "python_executable": py,
        "unit_dir": str(_SYSTEMD_USER_DIR),
        "services": written,
        "note": "使用 'systemctl --user status sjtu-agent-*' 查看服务状态。",
    }


def uninstall(
    service_names: tuple[str, ...] | None = None,
    **_,
) -> dict:
    """停止并卸载 systemd 用户服务。"""
    selected = set(service_names or _SERVICE_SPECS.keys())
    removed: list[dict] = []

    _systemctl(["daemon-reload"])
    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue
        unit_name = spec["unit_name"]

        if spec["has_timer"]:
            _systemctl(["stop", f"{unit_name}.timer"])
            _systemctl(["disable", f"{unit_name}.timer"])
            timer_path = _SYSTEMD_USER_DIR / f"{unit_name}.timer"
            if timer_path.exists():
                timer_path.unlink()
        else:
            _systemctl(["stop", f"{unit_name}.service"])
            _systemctl(["disable", f"{unit_name}.service"])

        service_path = _SYSTEMD_USER_DIR / f"{unit_name}.service"
        if service_path.exists():
            service_path.unlink()

        removed.append({"name": name, "unit_name": unit_name})

    _systemctl(["daemon-reload"])
    _systemctl(["reset-failed"])
    return {"platform": "Linux (systemd)", "removed": removed}


def status(
    service_names: tuple[str, ...] | None = None,
    **_,
) -> dict:
    """查询 systemd 用户服务状态。"""
    selected = set(service_names or _SERVICE_SPECS.keys())
    services: list[dict] = []

    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue
        unit_name = spec["unit_name"]
        service_path = _SYSTEMD_USER_DIR / f"{unit_name}.service"
        timer_path = _SYSTEMD_USER_DIR / f"{unit_name}.timer"
        installed = service_path.exists()

        item: dict = {
            "name": name,
            "unit_name": unit_name,
            "installed": installed,
            "service_path": str(service_path),
        }
        if spec["has_timer"]:
            item["timer_path"] = str(timer_path)
            item["timer_installed"] = timer_path.exists()

        if installed:
            check_unit = f"{unit_name}.timer" if spec["has_timer"] else f"{unit_name}.service"
            r = _systemctl(["is-active", check_unit])
            item["active"] = r.stdout.strip() == "active"

        services.append(item)

    return {
        "platform": "Linux (systemd)",
        "services": services,
        "all_installed": bool(services) and all(s["installed"] for s in services),
    }
