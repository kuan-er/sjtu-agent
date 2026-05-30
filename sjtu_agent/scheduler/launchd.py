"""
sjtu_agent/scheduler/launchd.py — macOS launchd 实现

使用 plist 文件 + launchctl 管理后台服务。
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from sjtu_agent.paths import DATA_DIR, LOG_DIR

_DEFAULT_OUTPUT_DIR = Path.home() / "Library" / "LaunchAgents"

_SERVICE_SPECS = {
    "daily-report": {
        "label": "com.sjtu.daily-report",
        "subcommand": "daily-report",
        "log": "daily_report.launchd.log",
        "run_at_load": False,
        "schedule_type": "calendar",
    },
    "morning-report": {
        "label": "com.sjtu.morning-report",
        "subcommand": "daily-report --type morning",
        "log": "morning_report.launchd.log",
        "run_at_load": False,
        "schedule_type": "calendar",
    },
    "noon-report": {
        "label": "com.sjtu.noon-report",
        "subcommand": "daily-report --type noon",
        "log": "noon_report.launchd.log",
        "run_at_load": False,
        "schedule_type": "calendar",
    },
    "remind-check": {
        "label": "com.sjtu.remind",
        "subcommand": "remind-check",
        "log": "remind_check.launchd.log",
        "run_at_load": True,
        "schedule_type": "interval",
        "keep_alive": False,
    },
    "telegram-bot": {
        "label": "com.sjtu.telegram-bot",
        "subcommand": "telegram-bot",
        "log": "telegram_bot.launchd.log",
        "run_at_load": True,
        "schedule_type": "none",
        "keep_alive": True,
    },
    "wechat-bot": {
        "label": "com.sjtu.wechat-bot",
        "subcommand": "wechat-bot",
        "log": "wechat_bot.launchd.log",
        "run_at_load": True,
        "schedule_type": "none",
        "keep_alive": True,
    },
    "feishu-bot": {
        "label": "com.sjtu.feishu-bot",
        "subcommand": "feishu-bot",
        "log": "feishu_bot.launchd.log",
        "run_at_load": True,
        "schedule_type": "none",
        "keep_alive": True,
    },
    "qq-bot": {
        "label": "com.sjtu.qq-bot",
        "subcommand": "qq-bot",
        "log": "qq_bot.launchd.log",
        "run_at_load": True,
        "schedule_type": "none",
        "keep_alive": True,
    },
    "news-digest": {
        "label": "com.sjtu.news-digest",
        "subcommand": "news-digest",
        "log": "news_digest.launchd.log",
        "run_at_load": False,
        "schedule_type": "calendar",
    },
    "web": {
        "label": "com.sjtu.web",
        "subcommand": "web",
        "log": "web.launchd.log",
        "run_at_load": True,
        "schedule_type": "none",
        "keep_alive": True,
    },
}


def _build_plist(
    name: str,
    python_executable: Path,
    daily_report_time: tuple[int, int],
    remind_interval: int,
    telegram_throttle: int,
    news_digest_time: tuple[int, int] = (10, 0),
) -> dict:
    spec = _SERVICE_SPECS[name]
    log_path = LOG_DIR / spec["log"]
    payload: dict = {
        "Label": spec["label"],
        "ProgramArguments": [str(python_executable), "-m", "sjtu_agent", spec["subcommand"]],
        "RunAtLoad": spec["run_at_load"],
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "WorkingDirectory": str(DATA_DIR),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    if spec["schedule_type"] == "calendar":
        if name == "news-digest":
            hour, minute = news_digest_time
        elif name == "morning-report":
            hour, minute = (8, 0)
        elif name == "noon-report":
            hour, minute = (12, 0)
        else:
            hour, minute = daily_report_time
        payload["StartCalendarInterval"] = {"Hour": hour, "Minute": minute}
    elif spec["schedule_type"] == "interval":
        payload["StartInterval"] = remind_interval
    if "keep_alive" in spec:
        payload["KeepAlive"] = spec["keep_alive"]
    if name == "telegram-bot" and telegram_throttle:
        payload["ThrottleInterval"] = telegram_throttle
    return payload


def install(
    service_names: tuple[str, ...] | None = None,
    python_executable: Path | None = None,
    daily_report_time: tuple[int, int] = (22, 0),
    remind_interval: int = 60,
    telegram_throttle: int = 10,
    load: bool = True,
    output_dir: Path | None = None,
    **_,
) -> dict:
    """安装（并可选加载）launchd 服务。"""
    if load and sys.platform != "darwin":
        raise RuntimeError("launchd 安装仅支持 macOS。")

    out_dir = Path(output_dir or _DEFAULT_OUTPUT_DIR).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    py = Path(os.path.abspath(python_executable or sys.executable))
    selected = set(service_names or _SERVICE_SPECS.keys())

    # ── 卸载不再需要的已知服务 ────────────────────────────────────────────
    uid = os.getuid()
    for name in _SERVICE_SPECS:
        if name in selected:
            continue
        label = _SERVICE_SPECS[name]["label"]
        plist_path = out_dir / f"{label}.plist"
        if plist_path.exists():
            if load:
                for domain in (f"gui/{uid}", f"user/{uid}"):
                    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"],
                                   capture_output=True)
            plist_path.unlink()

    written: list[dict] = []
    for name in _SERVICE_SPECS:
        if name not in selected:
            continue
        plist_path = out_dir / f"{_SERVICE_SPECS[name]['label']}.plist"
        payload = _build_plist(name, py, daily_report_time, remind_interval, telegram_throttle)
        with plist_path.open("wb") as f:
            plistlib.dump(payload, f, sort_keys=False)
        written.append({
            "name": name,
            "label": _SERVICE_SPECS[name]["label"],
            "plist_path": str(plist_path),
            "log_path": str(LOG_DIR / _SERVICE_SPECS[name]["log"]),
        })

    load_results: list[dict] = []
    if load:
        uid = os.getuid()
        for item in written:
            plist_path = Path(item["plist_path"])
            label = item["label"]
            for domain in (f"gui/{uid}", f"user/{uid}"):
                subprocess.run(["launchctl", "bootout", f"{domain}/{label}"],
                               capture_output=True)
                subprocess.run(["launchctl", "bootout", domain, str(plist_path)],
                               capture_output=True)
                result = subprocess.run(
                    ["launchctl", "bootstrap", domain, str(plist_path)],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    load_results.append({"label": label, "domain": domain})
                    break

    return {
        "platform": "macOS (launchd)",
        "output_dir": str(out_dir),
        "python_executable": str(py),
        "services": written,
        "load_results": load_results,
    }


def uninstall(
    service_names: tuple[str, ...] | None = None,
    output_dir: Path | None = None,
    **_,
) -> dict:
    """卸载（bootout）launchd 服务并删除 plist 文件。"""
    out_dir = Path(output_dir or _DEFAULT_OUTPUT_DIR).expanduser().resolve()
    selected = set(service_names or _SERVICE_SPECS.keys())
    uid = os.getuid()
    removed: list[dict] = []

    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue
        label = spec["label"]
        plist_path = out_dir / f"{label}.plist"
        for domain in (f"gui/{uid}", f"user/{uid}"):
            subprocess.run(["launchctl", "bootout", f"{domain}/{label}"],
                           capture_output=True)
        if plist_path.exists():
            plist_path.unlink()
            removed.append({"name": name, "label": label, "plist_path": str(plist_path)})

    return {"platform": "macOS (launchd)", "removed": removed}


def status(
    service_names: tuple[str, ...] | None = None,
    output_dir: Path | None = None,
    **_,
) -> dict:
    """查询 launchd 服务状态（plist 文件是否存在）。"""
    out_dir = Path(output_dir or _DEFAULT_OUTPUT_DIR).expanduser().resolve()
    selected = set(service_names or _SERVICE_SPECS.keys())
    services: list[dict] = []

    for name, spec in _SERVICE_SPECS.items():
        if name not in selected:
            continue
        plist_path = out_dir / f"{spec['label']}.plist"
        services.append({
            "name": name,
            "label": spec["label"],
            "plist_path": str(plist_path),
            "installed": plist_path.exists(),
        })

    return {
        "platform": "macOS (launchd)",
        "services": services,
        "all_installed": bool(services) and all(s["installed"] for s in services),
    }
