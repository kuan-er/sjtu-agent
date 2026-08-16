"""sjtu_agent/calendar.py — SJTU academic calendar (holidays/ makeup days).

Reads a static JSON file shipped with the package:
  sjtu_agent/data/academic_calendar.json

Supports multiple semesters in one file. Dates are matched across all
semesters — no need to pre-select the active semester.

Usage::

    from sjtu_agent.calendar import AcademicCalendar

    cal = AcademicCalendar(DATA_DIR)
    ctx = cal.get_context(datetime.now(CST))
    if ctx:
        print(ctx)  # "今天是端午节，全校放假，无课程安排。"
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

_CALENDAR_FILE = "academic_calendar.json"


class AcademicCalendar:
    """Provide holiday / schedule context for date-aware prompts."""

    def __init__(self, data_dir: Path):
        self._path = data_dir / _CALENDAR_FILE
        self._data: dict | None = None
        # cache: flat lookup built from all semesters
        self._all_holidays: dict[str, str] | None = None
        self._all_makeup: dict[str, str] | None = None

    def load(self) -> dict:
        """Load raw JSON. Returns empty structure on any error."""
        if self._data is not None:
            return self._data
        try:
            from sjtu_agent.paths import PACKAGE_ROOT
            path = self._path if self._path.exists() else PACKAGE_ROOT / "data" / _CALENDAR_FILE
            self._data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {"semesters": {}}
        return self._data

    # ── internal: build flat lookups across all semesters ──────────────

    def _ensure_flattened(self) -> None:
        if self._all_holidays is not None:
            return
        self.load()
        self._all_holidays = {}
        self._all_makeup = {}
        for key, sem in self._data.get("semesters", {}).items():
            for date_str, name in sem.get("holidays", {}).items():
                self._all_holidays[date_str] = name
            for date_str, note in sem.get("makeup_days", {}).items():
                self._all_makeup[date_str] = note

    # ── public API ──────────────────────────────────────────────────────

    def is_holiday(self, date: _dt.date) -> tuple[bool, str]:
        """Return (is_holiday, label)."""
        self._ensure_flattened()
        key = date.isoformat()
        name = self._all_holidays.get(key, "")
        return (bool(name), name)

    def is_makeup_day(self, date: _dt.date) -> tuple[bool, str]:
        """Return (is_makeup, label)."""
        self._ensure_flattened()
        key = date.isoformat()
        note = self._all_makeup.get(key, "")
        return (bool(note), note)

    def get_semester(self, date: _dt.date | None = None) -> str:
        """Return the semester key active on *date* (e.g. '2025-2026-2')."""
        if date is None:
            date = _dt.date.today()
        self.load()
        for key, sem in sorted(self._data.get("semesters", {}).items()):
            try:
                start = _dt.date.fromisoformat(sem["start_date"])
                end = _dt.date.fromisoformat(sem["end_date"])
                if start <= date <= end:
                    return key
            except (KeyError, ValueError):
                continue
        return ""

    def get_context(self, date: _dt.date | None = None) -> str:
        """Build a prompt-ready context string for the given date."""
        if date is None:
            date = _dt.date.today()

        parts: list[str] = []

        is_hol, hol_name = self.is_holiday(date)
        if is_hol:
            parts.append(f"今天是{hol_name}，全校放假，无课程安排。")

        is_mk, mk_note = self.is_makeup_day(date)
        if is_mk:
            parts.append(f"今天是调休补课日（{mk_note}），按补课安排上课。")

        # 不在任何校历学期内 → 寒暑假 / 开学前空档，提示模型不要以在校事务起手
        if not self.get_semester(date):
            if date.month in (1, 2):
                break_label = "寒假"
            elif date.month in (7, 8):
                break_label = "暑假"
            else:
                break_label = "假期/非教学期"
            parts.append(
                f"当前不在校历学期内（{break_label}），多数同学不在校内；"
                "不要主动推荐食堂、课表、作业 DDL 等在校事务。"
            )
            next_start = self.next_semester_start(date)
            if next_start:
                days = (next_start - date).days
                if days > 0:
                    parts.append(f"距下一学期开学还有 {days} 天。")

        return " ".join(parts)

    def next_semester_start(self, date: _dt.date | None = None) -> _dt.date | None:
        """Return the next semester start date strictly after *date*."""
        if date is None:
            date = _dt.date.today()
        self.load()
        starts: list[_dt.date] = []
        for sem in self._data.get("semesters", {}).values():
            try:
                starts.append(_dt.date.fromisoformat(sem["start_date"]))
            except (KeyError, ValueError):
                continue
        future = sorted(d for d in starts if d > date)
        return future[0] if future else None
