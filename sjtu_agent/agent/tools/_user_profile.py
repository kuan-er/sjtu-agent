"""User profile tools — read and update local user persona."""

import json
import datetime as _dt

from sjtu_agent.paths import USER_PROFILE_PATH


TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "update_user_profile",
            "description": (
                "将本轮对话中观察到的用户信息更新到本地用户画像文件。"
                "每当你从对话中了解到用户的新信息（姓名/学号/专业/课程偏好/作息/情绪状态/"
                "近期压力/兴趣爱好/特殊事件等），就调用此工具记录。"
                "不要等用户主动说「更新画像」，而是每轮对话结束前自动判断是否有新信息需要记录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "object",
                        "description": (
                            "要更新的字段（只传有新信息的字段，不要覆盖未提及的字段）。\n"
                            "常用字段示例：\n"
                            "  name: str — 姓名或昵称\n"
                            "  major: str — 专业\n"
                            "  grade: str — 年级（如 大二）\n"
                            "  courses: list[str] — 正在上的课程\n"
                            "  stress_level: str — 近期压力（low/medium/high/overwhelmed）\n"
                            "  mood: str — 情绪（happy/normal/tired/anxious/sad）\n"
                            "  recent_events: list[str] — 近期重要事件（考试/答辩/面试/生日等）\n"
                            "  hobbies: list[str] — 兴趣爱好\n"
                            "  sleep_pattern: str — 作息（如 late_night/normal/early）\n"
                            "  last_active: str — 最后活跃时间（ISO 格式，自动填当前时间）\n"
                            "  care_notes: list[str] — 需要定期关怀提示（如 '明天考物理'）\n"
                            "  preferred_canteens: list[str] — 偏好的食堂（如 ['第三餐饮大楼', '玉兰苑']）\n"
                            "  preferred_cuisines: list[str] — 偏好的菜系（如 ['米线', '西餐', '面食', '减脂餐']）\n"
                            "  dietary_restrictions: list[str] — 饮食限制（如 ['清真', '素食']）\n"
                            "  crowd_tolerance: str — 拥挤容忍度（低/中/高）\n"
                            "  custom: dict — 其他自定义字段"
                        ),
                        "additionalProperties": True,
                    },
                    "reason": {
                        "type": "string",
                        "description": "简述为什么更新这些字段（供调试参考）",
                    },
                },
                "required": ["updates"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_profile",
            "description": (
                "读取当前用户画像，了解用户的基本信息、情绪状态、近期事件等。"
                "在准备给用户发送关怀消息或个性化回复前先调用，确保不重复关怀。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def tool_get_user_profile() -> dict:
    if not USER_PROFILE_PATH.exists():
        return {"exists": False, "profile": {}}
    try:
        profile = json.loads(USER_PROFILE_PATH.read_text(encoding="utf-8"))
        return {"exists": True, "profile": profile}
    except Exception as e:
        return {"exists": False, "error": str(e), "profile": {}}


def tool_update_user_profile(updates: dict, reason: str = "") -> dict:
    profile: dict = {}
    if USER_PROFILE_PATH.exists():
        try:
            profile = json.loads(USER_PROFILE_PATH.read_text(encoding="utf-8"))
        except Exception:
            profile = {}

    def deep_merge(base: dict, patch: dict) -> dict:
        for k, v in patch.items():
            if k in base and isinstance(base[k], list) and isinstance(v, list):
                existing = base[k]
                for item in v:
                    if item not in existing:
                        existing.append(item)
            elif k in base and isinstance(base[k], dict) and isinstance(v, dict):
                deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    profile = deep_merge(profile, updates)
    now = _dt.datetime.now().isoformat()
    # 记录每个字段的写入时间，供时效性过滤（_is_fresh）判断过期
    ts = profile.setdefault("_timestamps", {})
    for k in updates.keys():
        ts[k] = now
    profile["last_updated"] = now

    USER_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USER_PROFILE_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "updated_keys": list(updates.keys()), "reason": reason}
