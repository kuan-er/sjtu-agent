#!/usr/bin/env python3
"""
care_check.py — 定时主动关怀脚本

每天运行两次（如早8点 / 晚10点），读取用户画像，
根据情绪状态、近期事件、作息等信息判断是否需要发送关怀消息，
并通过 Telegram Bot 推送给白名单用户。

调度方式：由 launchd（macOS）或 Task Scheduler（Windows）定时触发。
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sjtu_agent.paths import (
    CONFIG_PATH,
    USER_PROFILE_PATH,
    CARE_STATE_PATH,
    ENV_PATH,
    atomic_write_json,
    read_json_safe,
)
from dotenv import load_dotenv
load_dotenv(ENV_PATH)

import ddl_checker as dc

# CST 时区，避免 datetime.now() 受系统时区/DST 影响导致冷却期错乱
CST = timezone(timedelta(hours=8))

# ── 关怀冷却期（同一类型的关怀最少间隔多少小时）────────────────────────────
_CARE_COOLDOWN: dict[str, int] = {
    "morning":    22,   # 早安关怀
    "evening":    22,   # 晚间关怀
    "ddl_urgent": 6,    # 紧急 DDL 提醒
    "stress":     12,   # 压力关怀
    "mood_low":   8,    # 情绪低落关怀
    "late_night": 8,    # 熬夜提醒
    "birthday":   23,   # 生日
    "exam":       6,    # 考试前提醒
}


def _load_profile() -> dict:
    return read_json_safe(USER_PROFILE_PATH, default={})


def _load_care_state() -> dict:
    return read_json_safe(CARE_STATE_PATH, default={})


def _save_care_state(state: dict) -> None:
    """原子写入关怀状态。"""
    try:
        atomic_write_json(CARE_STATE_PATH, state)
    except Exception as e:
        # 写入失败保留旧状态，下次冷却期判断仍用旧时间戳；好过把状态丢空
        print(f"[care_check] 状态写入失败（保留旧状态）: {e}", flush=True)


def _can_send(state: dict, care_type: str) -> bool:
    """检查该类型的关怀是否已过冷却期。"""
    last_ts = state.get(f"last_{care_type}")
    if not last_ts:
        return True
    try:
        last_dt = datetime.fromisoformat(last_ts)
        # 兼容历史 naive 时间戳（之前版本写入时未带时区）
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=CST)
        cooldown_h = _CARE_COOLDOWN.get(care_type, 12)
        return datetime.now(CST) - last_dt > timedelta(hours=cooldown_h)
    except Exception:
        return True


def _mark_sent(state: dict, care_type: str) -> None:
    state[f"last_{care_type}"] = datetime.now(CST).isoformat()


def _send_care(message: str) -> bool:
    """通过 Telegram 推送关怀消息，返回是否发送成功。"""
    from sjtu_agent.config import cfg as _cfg
    from sjtu_agent.notifications import send_notification
    _cfg.reload_if_changed()
    result = send_notification(_cfg.raw(), "", "", message, channels=["telegram"])
    return bool(result["ok"])


def _get_urgent_ddls() -> list[dict]:
    """获取24小时内截止的 DDL 列表。"""
    try:
        cfg = dc.load_config()
        import ddl_checker as dc2
        import datetime as _dt
        now = _dt.datetime.now(dc2.CST)
        deadline = now + timedelta(hours=24)
        all_ddl = []
        try:
            all_ddl.extend(dc2.fetch_canvas(cfg))
        except Exception as e:
            print(f"[care] fetch_canvas 拉取失败: {e}")
        try:
            all_ddl.extend(dc2.fetch_aihaoke(cfg))
        except Exception as e:
            print(f"[care] fetch_aihaoke 拉取失败: {e}")
        urgent = []
        for item in all_ddl:
            due = item.get("due")
            if due and now < due <= deadline and not item.get("submitted"):
                hours_left = int((due - now).total_seconds() / 3600)
                urgent.append({
                    "name": item["name"],
                    "course": item["course"],
                    "due": due.strftime("%m月%d日 %H:%M"),
                    "hours_left": hours_left,
                })
        return sorted(urgent, key=lambda x: x["hours_left"])
    except Exception as e:
        print(f"[care_check] 获取 DDL 出错: {e}", flush=True)
        return []


def run_care_check() -> None:
    now = datetime.now(CST)
    hour = now.hour
    profile = _load_profile()
    state = _load_care_state()
    messages_to_send: list[str] = []
    care_types_sent: list[str] = []

    name = profile.get("name", "")
    greeting = f"{name}，" if name else ""
    stress = profile.get("stress_level", "")
    mood = profile.get("mood", "")
    sleep_pattern = profile.get("sleep_pattern", "")
    recent_events = profile.get("recent_events", [])
    care_notes = profile.get("care_notes", [])

    # ── 早安关怀（7:00-9:00）──────────────────────────────────────────────
    if 7 <= hour < 9 and _can_send(state, "morning"):
        lines = [f"☀️ {greeting}早上好！"]

        # 检查今日 DDL
        urgent_ddls = _get_urgent_ddls()
        if urgent_ddls:
            lines.append(f"\n⚠️ <b>今日截止</b>（{len(urgent_ddls)}项）：")
            for ddl in urgent_ddls[:3]:
                lines.append(f"• {ddl['course']} · {ddl['name']}  还有 {ddl['hours_left']}h")

        # 考试加油
        exam_events = [e for e in recent_events if "考" in e or "exam" in e.lower()]
        if exam_events:
            lines.append(f"\n🎯 记得好好准备：{exam_events[0]}" )

        # 压力高时额外鼓励
        if stress in ("high", "overwhelmed"):
            lines.append("\n💪 这几天压力有点大，一步一步来，你能行！有什么需要帮忙的随时说～")

        messages_to_send.append("\n".join(lines))
        care_types_sent.append("morning")

    # ── 晚间关怀（21:30-23:00）────────────────────────────────────────────
    elif 21 <= hour < 23 and _can_send(state, "evening"):
        lines = [f"🌙 {greeting}晚上好！"]

        # 明日 DDL
        urgent_ddls = _get_urgent_ddls()
        if urgent_ddls:
            lines.append(f"\n⏰ <b>明天截止</b>（{len(urgent_ddls)}项），记得安排时间：")
            for ddl in urgent_ddls[:2]:
                lines.append(f"• {ddl['course']} · {ddl['name']}")

        if mood in ("tired", "anxious", "sad"):
            lines.append("\n🫂 今天辛苦了，早点休息，明天会更好！")
        else:
            lines.append("\n好好休息，明天继续加油 💫")

        messages_to_send.append("\n".join(lines))
        care_types_sent.append("evening")

    # ── 熬夜提醒（0:00-2:00）─────────────────────────────────────────────
    elif 0 <= hour < 2 and _can_send(state, "late_night"):
        if sleep_pattern == "late_night" or mood in ("anxious", "tired"):
            msg = f"🌙 {greeting}都这么晚了，记得别太熬夜哦！\n身体健康最重要，休息好才能更高效 💤"
            messages_to_send.append(msg)
            care_types_sent.append("late_night")

    # ── 情绪关怀（任何时段，基于 mood 字段）──────────────────────────────
    if mood == "sad" and _can_send(state, "mood_low") and not messages_to_send:
        msg = (
            f"🤗 {greeting}最近还好吗？\n"
            "如果心情不太好，可以找我聊聊，也可以去走走，换个心情 🌿"
        )
        messages_to_send.append(msg)
        care_types_sent.append("mood_low")

    # ── 特殊关怀备注（care_notes）─────────────────────────────────────────
    if care_notes and 8 <= hour < 22 and _can_send(state, "stress") and not messages_to_send:
        note = care_notes[0]  # 取第一条
        msg = f"📌 {greeting}提醒你：{note}\n需要我帮什么忙吗？"
        messages_to_send.append(msg)
        care_types_sent.append("stress")

    # ── 发送并记录 ──────────────────────────────────────────────────────
    if messages_to_send:
        for msg in messages_to_send:
            sent = _send_care(msg)
            if sent:
                print(f"[care_check] 已发送关怀消息: {msg[:30]}…", flush=True)
        for ct in care_types_sent:
            _mark_sent(state, ct)
        _save_care_state(state)
    else:
        print(f"[care_check] {now.strftime('%H:%M')} 暂无需要发送的关怀消息", flush=True)


if __name__ == "__main__":
    run_care_check()
