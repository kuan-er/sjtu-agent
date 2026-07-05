"""Report preferences tools — customize daily report sections and instructions."""

import json

from sjtu_agent.paths import CONFIG_PATH, atomic_write_json

# ---- Default preferences ------------------------------------------------
_DEFAULT_SECTIONS = {
    "ddl": True,
    "schedule": True,
    "lab": True,
    "jwc": True,
    "news": True,
    "tips": True,
}

_DEFAULT_PREFS = {
    "sections": dict(_DEFAULT_SECTIONS),
    "custom_instructions": "",
    "per_type": {
        "morning": {},
        "noon": {},
        "evening": {},
    },
}

# ---- Tool definitions ----------------------------------------------------

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "update_report_preferences",
            "description": (
                "修改用户的早/中/晚报偏好设置。用户说「早报不要XX」「晚报加上XX」"
                "「日报多关注XX」时调用。只传需要修改的字段，未传字段保持不变。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "object",
                        "description": (
                            "要修改的日报模块显隐。键名：ddl（作业DDL）、schedule（课表）、"
                            "lab（物理实验）、jwc（教务通知）、news（校园动态）、tips（行动建议）。"
                            "值为 true=显示，false=隐藏。只传需要改的键。"
                        ),
                        "properties": {
                            "ddl": {"type": "boolean"},
                            "schedule": {"type": "boolean"},
                            "lab": {"type": "boolean"},
                            "jwc": {"type": "boolean"},
                            "news": {"type": "boolean"},
                            "tips": {"type": "boolean"},
                        },
                    },
                    "custom_instructions": {
                        "type": "string",
                        "description": (
                            "用户对日报的额外要求（自然语言），将直接注入日报生成提示词。"
                            "如「多关注物理作业」「语气轻松一点」「晚报重点提醒明天要交的」。"
                        ),
                    },
                    "report_type": {
                        "type": "string",
                        "enum": ["morning", "noon", "evening", "all"],
                        "description": (
                            "修改哪个报别的偏好。morning=早报，noon=午报，evening=晚报。"
                            "all=所有报别统一修改。默认 all。"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_report_preferences",
            "description": "查看用户当前的日报偏好设置（各模块显隐状态和自定义要求）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# ---- Internal helpers ----------------------------------------------------


def _load_prefs() -> dict:
    """Load report preferences from config.json, returning defaults if absent."""
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            prefs = cfg.get("report_preferences")
            if prefs and isinstance(prefs, dict):
                merged = dict(_DEFAULT_PREFS)
                merged["sections"] = {**_DEFAULT_SECTIONS, **prefs.get("sections", {})}
                merged["custom_instructions"] = prefs.get("custom_instructions", "")
                merged["per_type"] = prefs.get("per_type", {})
                for rt in ("morning", "noon", "evening"):
                    merged["per_type"].setdefault(rt, {})
                return merged
    except Exception:
        pass
    return dict(_DEFAULT_PREFS)


def _save_prefs(prefs: dict) -> None:
    """Write report preferences back to config.json atomically."""
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        else:
            cfg = {}
        cfg["report_preferences"] = prefs
        atomic_write_json(CONFIG_PATH, cfg)
    except Exception as e:
        raise RuntimeError(f"保存日报偏好失败: {e}")


# ---- Tool implementations ------------------------------------------------


def tool_get_report_preferences() -> dict:
    """Read current report preferences, including per-type overrides."""
    prefs = _load_prefs()
    # Surface per-type overrides so users can see effective settings
    effective = {}
    for rt in ("morning", "noon", "evening"):
        per_type = prefs.get("per_type", {}).get(rt, {})
        rt_sections = dict(prefs["sections"])
        if per_type.get("sections"):
            rt_sections.update(per_type["sections"])
        rt_custom = per_type.get("custom_instructions") or prefs.get("custom_instructions", "")
        effective[rt] = {"sections": rt_sections}
        if rt_custom:
            effective[rt]["custom_instructions"] = rt_custom
    return {"preferences": prefs, "effective": effective}


def tool_update_report_preferences(
    sections: dict | None = None,
    custom_instructions: str | None = None,
    report_type: str = "all",
) -> dict:
    """Update report preferences. Only modifies fields that are passed."""
    prefs = _load_prefs()
    changes = []

    targets = ["morning", "noon", "evening"] if report_type == "all" else [report_type]

    for rt in targets:
        per_type = prefs["per_type"].setdefault(rt, {})

        if sections:
            rt_sections = per_type.setdefault("sections", {})
            for key in ("ddl", "schedule", "lab", "jwc", "news", "tips"):
                if key in sections:
                    rt_sections[key] = sections[key]
            changes.extend(f"{rt}:{k}={sections[k]}" for k in sections)

        if custom_instructions is not None:
            per_type["custom_instructions"] = custom_instructions
            changes.append(f"{rt}:custom_instructions")

    # When report_type is "all", also update the top-level defaults
    if report_type == "all":
        if sections:
            for key in sections:
                prefs["sections"][key] = sections[key]
        if custom_instructions is not None:
            prefs["custom_instructions"] = custom_instructions

    _save_prefs(prefs)

    summary_parts = []
    if sections:
        enabled = [k for k, v in sections.items() if v]
        disabled = [k for k, v in sections.items() if not v]
        section_names = {
            "ddl": "📚作业DDL", "schedule": "📅课表", "lab": "🔬物理实验",
            "jwc": "📢教务通知", "news": "📰校园动态", "tips": "💡行动建议",
        }
        if enabled:
            summary_parts.append(f"将展示: {'、'.join(section_names.get(k, k) for k in enabled)}")
        if disabled:
            summary_parts.append(f"将隐藏: {'、'.join(section_names.get(k, k) for k in disabled)}")
    if custom_instructions is not None:
        summary_parts.append(f"自定义要求: {custom_instructions}")

    scope = "所有报别" if report_type == "all" else f"{report_type}"
    return {
        "ok": True,
        "scope": scope,
        "summary": "；".join(summary_parts) if summary_parts else "偏好已更新",
        "changes": changes,
    }
