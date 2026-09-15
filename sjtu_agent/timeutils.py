"""sjtu_agent/timeutils.py — 校园时间（北京时间）与用户本地时间的统一处理。

多数同学在校时两者一致；交换学期 / 海外访学 / 回国时机器时区 ≠ 北京时间，
日报与对话语境需要区分「校园日历」与「用户此刻」：

- 校园时间固定 Asia/Shanghai（+8，无夏令时）——课程、DDL、校历一律按它；
- 用户时间来自 config.json 的 `user_timezone`（IANA 名，如 America/New_York），
  未配置时自动取系统时区——个人感知的"今天/现在"按它。

时区数据在 Windows 上来自 tzdata 包（requirements.txt 已显式声明）；
缺失时退化为固定 +8 / 系统偏移，功能不中断（仅 DST 边界精度受限）。
"""
from __future__ import annotations

import datetime as _dt

SCHOOL_TZ_NAME = "Asia/Shanghai"
_SCHOOL_FIXED = _dt.timezone(_dt.timedelta(hours=8), "CST")

_WD_ZH = "一二三四五六日"


def _read_config() -> dict:
    """读运行时配置（模块级钩子，测试可 monkeypatch）。"""
    try:
        from sjtu_agent.config import ConfigStore
        return ConfigStore.get_instance().raw() or {}
    except Exception:
        return {}


def _load_zone(name: str) -> _dt.tzinfo | None:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def school_tz() -> _dt.tzinfo:
    """校园时区：Asia/Shanghai（无夏令时，等价固定 +8）。"""
    return _load_zone(SCHOOL_TZ_NAME) or _SCHOOL_FIXED


def school_now() -> _dt.datetime:
    return _dt.datetime.now(school_tz())


def user_tz_name() -> str:
    """用户时区名：config['user_timezone'] 优先，否则系统时区名。"""
    cfg = _read_config()
    name = str(cfg.get("user_timezone") or "").strip()
    if name and _load_zone(name) is not None:
        return name
    local = _dt.datetime.now().astimezone().tzinfo
    return getattr(local, "key", None) or "UTC"


def user_tz() -> _dt.tzinfo:
    zone = _load_zone(user_tz_name())
    if zone is not None:
        return zone
    return _dt.datetime.now().astimezone().tzinfo or _dt.timezone.utc


def user_now() -> _dt.datetime:
    return _dt.datetime.now(user_tz())


def fmt_zh(dt_obj: _dt.datetime) -> str:
    """'9月14日（周一）08:00' 样式的紧凑中文时间。"""
    return f"{dt_obj.month}月{dt_obj.day}日（周{_WD_ZH[dt_obj.weekday()]}）{dt_obj:%H:%M}"


def differs_locally(now_school: _dt.datetime) -> bool:
    """用户当地日历时刻与北京时间是否不同（同刻不同区 → True）。"""
    local = now_school.astimezone(user_tz())
    return local.strftime("%Y-%m-%d %H:%M") != now_school.strftime("%Y-%m-%d %H:%M")


def dual_time_label(now_school: _dt.datetime | None = None) -> str:
    """日报/语境用的双时区标签：一致时只显示北京时间，不同时两地都给。"""
    school = now_school or school_now()
    if not differs_locally(school):
        return f"北京时间 {fmt_zh(school)}"
    local = school.astimezone(user_tz())
    return f"北京时间 {fmt_zh(school)} · 你那边 {fmt_zh(local)}"


def local_tz() -> _dt.tzinfo:
    """机器系统时区（独立函数便于测试注入）。"""
    return _dt.datetime.now().astimezone().tzinfo or _dt.timezone.utc


def campus_schedule_local(hh: int, mm: int = 0) -> tuple[int, int]:
    """把校园（北京）时钟的定时推送时刻换算为机器本地等价时刻。

    日报等内容锚定校园日历（晨 8:00 / 午 12:00 / 晚 22:00 均为北京时间），
    触发也应按北京时钟——否则海外同学本地早晨收到的"晨报"，报道的其实是
    北京快结束的一天。机器在东八区时原样返回。

    注意：平台调度器（schtasks/launchd/systemd）只认安装时刻的固定本地
    时间，夏令时地区（如 PDT↔PST）漂移后需重装后台服务重新换算。
    """
    hh = hh % 24
    mm = mm % 60
    beijing = school_now()
    if not differs_locally(beijing):
        return hh, mm
    combined = _dt.datetime.combine(
        _dt.date.today(), _dt.time(hh, mm), tzinfo=school_tz()
    )
    local = combined.astimezone(local_tz())
    return local.hour, local.minute
