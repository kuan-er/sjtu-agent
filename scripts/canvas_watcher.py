#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sjtu_agent.canvas_client import CanvasError, make_client_from_config
from sjtu_agent.canvas_monitor import merged_canvas_monitor_config
from sjtu_agent.notifications import send_notification
from sjtu_agent.logging import get_logger
from sjtu_agent.paths import (
    CANVAS_MONITOR_STATE_PATH,
    CONFIG_PATH,
    LOG_DIR,
    atomic_write_json,
    read_json_safe,
)

CST = timezone(timedelta(hours=8))


_logger = get_logger("canvas_watcher")


def _log(message: str) -> None:
    _logger.info(message)


def _load_cfg() -> dict:
    return read_json_safe(CONFIG_PATH, default={})


def _monitor_cfg(cfg: dict) -> dict:
    return merged_canvas_monitor_config(cfg)


def _load_state(path: Path) -> dict:
    state = read_json_safe(path, default={})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("items", {})
    return state


def _save_state(path: Path, state: dict) -> None:
    state["last_checked_at"] = datetime.now(CST).isoformat()
    atomic_write_json(path, state)


def _select_courses(client, monitor: dict) -> list[dict]:
    courses = client.list_courses().get("courses", [])
    ids = {int(value) for value in monitor.get("course_ids") or []}
    filters = [
        str(value).lower()
        for value in monitor.get("course_filters") or []
        if str(value).strip()
    ]
    if ids:
        return [course for course in courses if int(course["course_id"]) in ids]
    if filters:
        return [
            course for course in courses
            if any(
                pattern in str(course.get("name", "")).lower()
                or pattern in str(course.get("course_code", "")).lower()
                for pattern in filters
            )
        ]
    return courses


def _announcement_signature(item: dict) -> dict:
    return {
        "title": item.get("title"),
        "posted_at": item.get("posted_at"),
        "updated_at": item.get("updated_at"),
        "message_hash": item.get("message_hash"),
    }


def _quiz_signature(item: dict) -> dict:
    return {
        "title": item.get("title"),
        "unlock_at": item.get("unlock_at"),
        "due_at": item.get("due_at"),
        "lock_at": item.get("lock_at"),
        "published": item.get("published"),
        "locked_for_user": item.get("locked_for_user"),
        "question_count": item.get("question_count"),
        "points_possible": item.get("points_possible"),
    }


def _assignment_signature(item: dict) -> dict:
    return {
        "name": item.get("name"),
        "unlock_at": item.get("unlock_at"),
        "due_at": item.get("due_at"),
        "lock_at": item.get("lock_at"),
        "published": item.get("published"),
        "submission_types": item.get("submission_types"),
    }


def _changed_fields(old: dict, new: dict) -> list[str]:
    return [key for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)]


def _record_event(
    state: dict,
    key: str,
    signature: dict,
    event: dict,
    baseline: bool,
    events: list[dict],
) -> None:
    existing = state["items"].get(key)
    if existing is None:
        state["items"][key] = {"signature": signature}
        if not baseline:
            events.append(event)
        return

    old_signature = existing.get("signature") or {}
    changed = _changed_fields(old_signature, signature)
    if changed:
        state["items"][key] = {"signature": signature}
        event["changed_fields"] = changed
        event["old_signature"] = old_signature
        event["new_signature"] = signature
        events.append(event)


def _collect_events(
    client,
    courses: list[dict],
    monitor: dict,
    state: dict,
    baseline: bool,
) -> list[dict]:
    events: list[dict] = []
    for course in courses:
        course_id = int(course["course_id"])
        course_name = course.get("name", f"课程{course_id}")
        if monitor.get("include_announcements", True):
            for item in client.list_announcements(course_id, limit=50).get("announcements", []):
                key = f"announcement:{course_id}:{item.get('id')}"
                _record_event(
                    state,
                    key,
                    _announcement_signature(item),
                    {
                        "type": "new_announcement",
                        "course": course,
                        "item": item,
                        "title": "Canvas 新公告",
                        "subtitle": course_name,
                        "body": (
                            f"{item.get('title', '')}\n"
                            f"{item.get('summary', '')}\n"
                            f"{item.get('html_url', '')}"
                        ),
                    },
                    baseline,
                    events,
                )

        if monitor.get("include_quizzes", True):
            for item in client.list_quizzes(course_id, include_past=False).get("quizzes", []):
                item_id = item.get("quiz_id") or item.get("assignment_id")
                key = f"quiz:{course_id}:{item_id}"
                event_type = "new_quiz"
                title = "Canvas 新 Quiz"
                if key in state.get("items", {}):
                    event_type = "quiz_changed"
                    title = "Canvas Quiz 时间变化"
                _record_event(
                    state,
                    key,
                    _quiz_signature(item),
                    {
                        "type": event_type,
                        "course": course,
                        "item": item,
                        "title": title,
                        "subtitle": course_name,
                        "body": (
                            f"{item.get('title', '')}\n"
                            f"due: {item.get('due_at')}\n"
                            f"unlock: {item.get('unlock_at')}\n"
                            f"lock: {item.get('lock_at')}\n"
                            f"{item.get('html_url', '')}"
                        ),
                    },
                    baseline,
                    events,
                )

        if monitor.get("include_assignments", False):
            for item in client.list_assignments(course_id, include_past=False).get("assignments", []):
                key = f"assignment:{course_id}:{item.get('assignment_id')}"
                _record_event(
                    state,
                    key,
                    _assignment_signature(item),
                    {
                        "type": "new_assignment",
                        "course": course,
                        "item": item,
                        "title": "Canvas 新作业",
                        "subtitle": course_name,
                        "body": (
                            f"{item.get('name', '')}\n"
                            f"due: {item.get('due_at')}\n"
                            f"{item.get('html_url', '')}"
                        ),
                    },
                    baseline,
                    events,
                )
    return events


def check_once(
    *,
    cfg: dict | None = None,
    client=None,
    state_path: Path = CANVAS_MONITOR_STATE_PATH,
    test_mode: bool = False,
) -> dict:
    cfg = cfg or _load_cfg()
    monitor = _monitor_cfg(cfg)
    if not monitor.get("enabled", True):
        return {"ok": True, "enabled": False, "events_count": 0, "events": []}

    client = client or make_client_from_config()
    state = _load_state(state_path)
    baseline = bool(
        monitor.get("baseline_on_first_run", True)
        and not state.get("last_checked_at")
        and not state.get("items")
    )
    courses = _select_courses(client, monitor)
    events = _collect_events(client, courses, monitor, state, baseline)
    notification_results = []
    for event in events:
        notification_results.append(send_notification(
            cfg,
            event["title"],
            event["subtitle"],
            event["body"],
            channels=monitor.get("notify_channels") or ["system", "telegram", "feishu"],
            test_mode=test_mode,
        ))
    _save_state(state_path, state)
    return {
        "ok": True,
        "enabled": True,
        "baseline_created": baseline,
        "courses_count": len(courses),
        "events_count": len(events),
        "events": events,
        "notifications": notification_results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor Canvas course updates.")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--test", action="store_true", help="print would-send notifications only")
    args = parser.parse_args(argv)

    while True:
        try:
            result = check_once(test_mode=args.test)
            _log(json.dumps({
                "events_count": result.get("events_count"),
                "baseline": result.get("baseline_created"),
            }, ensure_ascii=False))
        except CanvasError as exc:
            _log(f"Canvas watcher skipped: {exc.message}")
        except Exception as exc:
            _log(f"Canvas watcher error: {exc}")
        if args.once:
            return 0
        cfg = _load_cfg()
        interval = int(_monitor_cfg(cfg).get("interval_seconds", 300))
        time.sleep(max(30, interval))


if __name__ == "__main__":
    raise SystemExit(main())
