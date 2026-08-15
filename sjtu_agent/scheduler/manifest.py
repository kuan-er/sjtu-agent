"""
sjtu_agent/scheduler/manifest.py — 后台服务安装清单

安装后台服务时把（后端、服务列表、Python 路径、调度参数）记到运行时数据目录，
供安装脚本和 `sjtu-agent update` 在代码目录移动 / venv 重建后自动恢复服务，
避免用户每次重装都要手动重新执行 install-daemons。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from sjtu_agent.paths import DAEMON_MANIFEST_PATH, atomic_write_json, read_json_safe

_MANIFEST_VERSION = 1


def _empty_manifest() -> dict[str, Any]:
    return {"version": _MANIFEST_VERSION, "backends": {}}


def load_manifest() -> dict[str, Any]:
    """读取后台服务安装清单。文件缺失或损坏时返回空清单。"""
    data = read_json_safe(DAEMON_MANIFEST_PATH, default=_empty_manifest())
    if not isinstance(data, dict):
        return _empty_manifest()
    data.setdefault("version", _MANIFEST_VERSION)
    data.setdefault("backends", {})
    return data


def save_manifest(manifest: dict[str, Any]) -> None:
    manifest["version"] = _MANIFEST_VERSION
    atomic_write_json(DAEMON_MANIFEST_PATH, manifest)


def _normalise_services(service_names: tuple[str, ...] | list[str] | None) -> list[str]:
    return sorted({str(name) for name in (service_names or []) if str(name)})


def remember_deployment(
    backend: str,
    service_names: tuple[str, ...] | list[str],
    python_executable: str | Path,
    *,
    daily_report_time: tuple[int, int] | list[int] | None = None,
    remind_interval: int = 60,
    telegram_throttle: int = 10,
    output_dir: str | Path | None = None,
) -> None:
    """记录一次成功的后台服务安装。"""
    services = _normalise_services(service_names)
    if not services:
        return

    hour, minute = tuple(daily_report_time or (22, 0))
    manifest = load_manifest()
    backends = manifest.setdefault("backends", {})
    backends[backend] = {
        "services": services,
        "python_executable": str(python_executable),
        "daily_report_time": [int(hour), int(minute)],
        "remind_interval": int(remind_interval or 60),
        "telegram_throttle": int(telegram_throttle or 10),
    }
    if output_dir:
        backends[backend]["output_dir"] = str(output_dir)
    save_manifest(manifest)


def forget_deployment(
    backend: str,
    service_names: tuple[str, ...] | list[str] | None = None,
) -> None:
    """从清单中移除服务；若后端已没有服务则移除整个后端条目。"""
    manifest = load_manifest()
    backends = manifest.get("backends", {})
    entry = backends.get(backend)
    if not entry:
        return

    if service_names is None:
        backends.pop(backend, None)
    else:
        removed = set(service_names)
        entry["services"] = [s for s in entry.get("services", []) if s not in removed]
        if not entry["services"]:
            backends.pop(backend, None)

    manifest["backends"] = backends
    save_manifest(manifest)


def list_deployments() -> list[dict[str, Any]]:
    """返回清单中的所有部署条目（用于更新前暂停 / 更新后恢复）。"""
    deployments: list[dict[str, Any]] = []
    for backend, entry in load_manifest().get("backends", {}).items():
        services = _normalise_services(entry.get("services"))
        if not services:
            continue
        deployments.append({
            "backend": backend,
            "services": services,
            "python_executable": entry.get("python_executable", ""),
            "daily_report_time": tuple(entry.get("daily_report_time") or (22, 0)),
            "remind_interval": int(entry.get("remind_interval") or 60),
            "telegram_throttle": int(entry.get("telegram_throttle") or 10),
            "output_dir": entry.get("output_dir") or "",
        })
    return deployments
