"""Dining recommendation tools — canteen crowd, info, history, and recommendations.

Provides tools for the LLM to fetch real-time canteen crowd data, look up
canteen knowledge (dishes/hours/location), get personalized recommendations,
record dining choices, and review dining history with learned preferences.

Data files:
- Real-time crowd: from campuslife.sjtu.edu.cn API (via CanteenCrowdChecker)
- Canteen knowledge: CANTEEN_KNOWLEDGE_PATH (static, shipped with package)
- Dining history:  DINING_HISTORY_PATH (user data, grows over time)
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import ddl_checker as dc  # for dc.CST (UTC+8)

from sjtu_agent.paths import (
    DINING_HISTORY_PATH,
    CANTEEN_KNOWLEDGE_PATH,
    atomic_write_json,
    read_json_safe,
)
from sjtu_agent.data.canteen_crowd import _crowd_label  # canonical crowd→label mapping

# ══════════════════════════════════════════════════════════════════════════════
# Tool definitions (OpenAI function-calling format)
# ══════════════════════════════════════════════════════════════════════════════

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "get_canteen_crowd",
            "description": (
                "获取交大食堂实时拥挤度数据。"
                "返回各食堂当前拥挤度百分比、等级（空闲/适中/较挤/拥挤/爆满）、趋势（上升/下降/平稳）。"
                "用户说「食堂人多吗」「哪个食堂不挤」「现在食堂拥挤度」「哪里有空位」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campus": {
                        "type": "string",
                        "enum": ["闵行", "徐汇", "张江"],
                        "description": "校区筛选，留空则返回全部校区",
                    },
                    "canteen_id": {
                        "type": "integer",
                        "description": "指定食堂 ID（100-800 闵行，1000 徐汇，1200 张江）获取子区域详细拥挤度，不传则返回所有食堂概览",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canteen_info",
            "description": (
                "查询食堂详细知识（楼层分布、推荐菜品、营业时间、位置描述）。"
                "当用户问「XX食堂有什么好吃的」「XX食堂怎么样」「XX食堂有哪些窗口」「XX食堂在哪」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "canteen_id": {
                        "type": "integer",
                        "description": "食堂 ID（100-800 闵行，1000 徐汇，1200 张江）",
                    },
                    "canteen_name": {
                        "type": "string",
                        "description": "食堂名称（如「第一餐饮大楼」「三餐」「哈乐」「玉兰苑」），支持模糊匹配",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_canteen",
            "description": (
                "综合实时拥挤度、用户历史用餐偏好、食堂特色知识，为用户推荐最佳就餐地点。"
                "返回按综合评分排序的推荐列表，附带推荐理由。"
                "用户说「去哪吃」「推荐食堂」「今天吃什么」「哪里吃饭好」「有什么推荐」时调用。"
                "自动推断当前餐段（早餐/午餐/晚餐/夜宵），不需要先问用户。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campus": {
                        "type": "string",
                        "enum": ["闵行", "徐汇", "张江"],
                        "description": "校区，默认闵行",
                    },
                    "meal_type": {
                        "type": "string",
                        "enum": ["早餐", "午餐", "晚餐", "夜宵"],
                        "description": "餐段。留空则自动根据当前时间推断",
                    },
                    "crowd_tolerance": {
                        "type": "string",
                        "enum": ["低", "中", "高"],
                        "description": "拥挤容忍度。低=只推荐空闲的，高=不太在意拥挤。留空从用户历史推断",
                    },
                    "cuisine_preference": {
                        "type": "string",
                        "description": "菜系偏好关键词，如「米线」「西餐」「面食」「川菜」「减脂餐」「清真」，留空从历史偏好推断",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "返回推荐数量，默认 4",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_dining_choice",
            "description": (
                "记录用户本次就餐选择（去了哪个食堂/餐厅），用于积累偏好数据改进后续推荐。"
                "当用户明确说「我去XX吃了」「今天在XX吃」「选了XX」「就去XX吧」时调用。"
                "也应在推荐后用户做出选择时自动调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "canteen_id": {
                        "type": "integer",
                        "description": "食堂 ID（100-800 闵行，1000 徐汇，1200 张江）",
                    },
                    "canteen_name": {
                        "type": "string",
                        "description": "食堂名称",
                    },
                    "sub_area": {
                        "type": "string",
                        "description": "具体窗口/区域（可选）",
                    },
                    "rating": {
                        "type": "integer",
                        "description": "评价 1-5（可选），如用户说「很好吃」=5、「一般」=3、「不好吃」=1",
                    },
                    "note": {
                        "type": "string",
                        "description": "备注，如喜欢什么菜、排队情况、用户反馈（可选）",
                    },
                    "meal_type": {
                        "type": "string",
                        "enum": ["早餐", "午餐", "晚餐", "夜宵"],
                        "description": "餐段。留空则自动从当前时间推断",
                    },
                },
                "required": ["canteen_id", "canteen_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dining_history",
            "description": (
                "查看用户的就餐历史记录和学习到的偏好。"
                "返回最近的用餐记录和自动统计的偏好信息（常去食堂、时间规律、菜系偏好、拥挤容忍度等）。"
                "用户说「我最近去过哪里吃」「我常去哪个食堂」「我的饮食偏好」「我都吃了什么」时调用。"
                "也应在每次 recommend_canteen 之前调用以获取最新偏好。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "最近多少条记录，默认 10",
                    },
                },
                "required": [],
            },
        },
    },
]

# ══════════════════════════════════════════════════════════════════════════════
# Persistence helpers
# ══════════════════════════════════════════════════════════════════════════════


def _load_history() -> list[dict]:
    """Load dining history, newest first. Returns [] on missing/corrupt file."""
    return read_json_safe(DINING_HISTORY_PATH, default=[])


def _load_knowledge() -> dict:
    """Load the static canteen knowledge base. Returns {'canteens': []} on error."""
    if not CANTEEN_KNOWLEDGE_PATH.exists():
        return {"canteens": []}
    try:
        return json.loads(CANTEEN_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"canteens": []}


def _infer_meal_type(now: _dt.datetime | None = None) -> str:
    """Infer current meal type from time of day (CST)."""
    if now is None:
        now = _dt.datetime.now(dc.CST)
    h = now.hour
    if 6 <= h < 10:
        return "早餐"
    elif 10 <= h < 14:
        return "午餐"
    elif 14 <= h < 17:
        return "午餐"  # late lunch still maps to 午餐
    elif 17 <= h < 20:
        return "晚餐"
    elif 20 <= h < 23:
        return "夜宵"
    return "午餐"  # default for off-hours


def _get_next_id() -> int:
    history = _load_history()
    return max((r.get("id", 0) for r in history), default=0) + 1


# ══════════════════════════════════════════════════════════════════════════════
# Tool implementations
# ══════════════════════════════════════════════════════════════════════════════


def tool_get_canteen_crowd(campus: str = "", canteen_id: int = 0) -> dict:
    """Fetch real-time canteen crowd data."""
    from sjtu_agent.data.canteen_crowd import CanteenCrowdChecker

    checker = CanteenCrowdChecker()

    if canteen_id:
        detail = checker.get_canteen_detail(canteen_id)
        if not detail["ok"]:
            return {"ok": False, "error": detail.get("error", "获取失败")}
        d = detail["detail"]

        def _sub_info(s: dict) -> dict:
            """Extract sub-area info, treating rate=0 as no-data."""
            rates = s.get("curRates")
            if not rates:
                return {
                    "name": s.get("name", "?"),
                    "is_open": bool(s.get("isOpen")),
                    "close_desc": s.get("closeDesc") or "",
                    "current_rate": None,
                    "current_label": "无数据",
                }
            rate = rates[-1]["rate"]
            if rate == 0:
                return {
                    "name": s.get("name", "?"),
                    "is_open": bool(s.get("isOpen")),
                    "close_desc": s.get("closeDesc") or "",
                    "current_rate": None,
                    "current_label": "无数据",
                }
            return {
                "name": s.get("name", "?"),
                "is_open": bool(s.get("isOpen")),
                "close_desc": s.get("closeDesc") or "",
                "current_rate": round(rate, 1),
                "current_label": _crowd_label(rate),
            }

        return {
            "ok": True,
            "canteen_id": canteen_id,
            "schedule_desc": d.get("scheduleDesc", ""),
            "subs": [_sub_info(s) for s in d.get("subs", [])],
        }

    # Return all canteens overview
    result = checker.get_all_crowd(campus=campus)
    if not result["ok"]:
        return {"ok": False, "error": result.get("error", "获取失败")}

    simplified = []
    for c in result["canteens"]:
        simplified.append({
            "id": c["id"],
            "name": c["name"],
            "campus": c["campus"],
            "is_operational": c["is_operational"],
            "is_dining": c["is_dining"],
            "schedule_desc": c["schedule_desc"],
            "overall_rate": c["overall_rate"],
            "overall_label": c["overall_label"],
            "sub_areas": [
                {"name": s["name"], "rate": s["current_rate"], "label": s["current_label"], "trend": s["trend"]}
                for s in c["subs"] if s["current_rate"] is not None
            ],
        })
    return {
        "ok": True,
        "fetched_at": result["fetched_at"],
        "campus": result["campus"],
        "total": result["total"],
        "canteens": simplified,
    }


def tool_get_canteen_info(canteen_id: int = 0, canteen_name: str = "") -> dict:
    """Look up structured canteen knowledge."""
    knowledge = _load_knowledge()
    canteens_kb = knowledge.get("canteens", [])

    if not canteens_kb:
        return {"ok": False, "error": "食堂知识库未加载"}

    # Search by ID first
    if canteen_id:
        for c in canteens_kb:
            if c.get("id") == canteen_id:
                return {"ok": True, "canteen": c}

    # Search by name (fuzzy)
    if canteen_name:
        # Try exact match first
        for c in canteens_kb:
            if c.get("name") == canteen_name:
                return {"ok": True, "canteen": c}

        # Fuzzy: check if canteen_name is a substring or shorthand
        shorthand_map = {
            "一餐": 100, "二餐": 200, "三餐": 300, "四餐": 400,
            "五餐": 500, "六餐": 600, "七餐": 700,
            "第一": 100, "第二": 200, "第三": 300, "第四": 400,
            "第五": 500, "第六": 600, "第七": 700,
        }
        for shorthand, cid in shorthand_map.items():
            if shorthand in canteen_name or canteen_name in shorthand:
                for c in canteens_kb:
                    if c.get("id") == cid:
                        return {"ok": True, "canteen": c}

        # Broader fuzzy: check if name contains the query
        for c in canteens_kb:
            cn = c.get("name", "")
            if canteen_name in cn or cn in canteen_name:
                return {"ok": True, "canteen": c}

    # No match — return list of available names
    return {
        "ok": False,
        "error": f"未找到匹配的食堂",
        "available": [{"id": c["id"], "name": c["name"], "campus": c["campus"]}
                       for c in canteens_kb if c.get("id", 0) > 0],
    }


def tool_recommend_canteen(
    campus: str = "闵行",
    meal_type: str = "",
    crowd_tolerance: str = "",
    cuisine_preference: str = "",
    top_n: int = 4,
) -> dict:
    """Personalized canteen recommendation combining crowd + history + knowledge."""
    from sjtu_agent.data.canteen_crowd import CanteenCrowdChecker

    now = _dt.datetime.now(dc.CST)
    if not meal_type:
        meal_type = _infer_meal_type(now)

    # 1. Fetch real-time crowd data
    checker = CanteenCrowdChecker()
    crowd_result = checker.get_all_crowd(campus=campus)
    if not crowd_result.get("ok"):
        return {
            "ok": False,
            "error": "暂时无法获取食堂实时拥挤度数据",
            "detail": crowd_result.get("error", ""),
            "tip": "可以稍后再试，或尝试用 get_canteen_info 查看食堂基本信息",
        }

    canteens_data = crowd_result.get("canteens", [])
    if not canteens_data:
        return {"ok": False, "error": f"{campus}校区暂未查到食堂数据"}

    # 2. Load history and compute stats
    history = _load_history()
    freq_stats = _compute_frequency_stats(history)
    time_stats = _compute_time_stats(history)
    last_visits = _compute_last_visits(history)
    cuisine_history = _compute_cuisine_history(history)

    # 3. Infer crowd_tolerance from history if not specified
    if not crowd_tolerance:
        crowd_tolerance = _infer_crowd_tolerance(history)

    # 4. Load knowledge for cuisine matching
    knowledge = _load_knowledge()
    canteens_kb = knowledge.get("canteens", [])

    # 5. Score each canteen
    scored = []
    for c in canteens_data:
        score, reasons = _score_canteen(
            c, meal_type, now,
            freq_stats, time_stats, last_visits, cuisine_history,
            crowd_tolerance, cuisine_preference, canteens_kb,
        )
        if score >= 0:  # negative score = filtered out
            scored.append((score, c, reasons))

    # 6. Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # 7. Format recommendations
    top = scored[:top_n]
    recommendations = []
    for score, c, reasons in top:
        # Grab matching knowledge for extra dish hints
        kb = next((k for k in canteens_kb if k.get("id") == c["id"]), None)
        best_sub_areas = _get_best_sub_areas(kb, cuisine_preference, cuisine_history) if kb else []

        recommendations.append({
            "canteen_id": c["id"],
            "canteen_name": c["name"],
            "campus": c["campus"],
            "overall_rate": c["overall_rate"],
            "overall_label": c["overall_label"],
            "is_dining": c["is_dining"],
            "schedule_desc": c["schedule_desc"],
            "score": round(score, 1),
            "reasons": reasons,
            "recommended_sub_areas": best_sub_areas[:3],
        })

    # 8. Build natural-language summary
    summary = _build_summary(recommendations, meal_type, crowd_result, history)

    return {
        "ok": True,
        "fetched_at": crowd_result.get("fetched_at", ""),
        "meal_type": meal_type,
        "campus": campus,
        "total_canteens": len(canteens_data),
        "has_history": len(history) > 0,
        "history_count": len(history),
        "recommendations": recommendations,
        "summary": summary,
    }


def tool_record_dining_choice(
    canteen_id: int,
    canteen_name: str,
    sub_area: str = "",
    rating: int = 0,
    note: str = "",
    meal_type: str = "",
) -> dict:
    """Record the user's dining choice to history."""
    from sjtu_agent.data.canteen_crowd import CanteenCrowdChecker

    now = _dt.datetime.now(dc.CST)
    if not meal_type:
        meal_type = _infer_meal_type(now)

    # Enrich: try to fetch current crowd for the chosen canteen
    crowd_rate = None
    crowd_label = None
    try:
        crowd = CanteenCrowdChecker().get_all_crowd()
        if crowd.get("ok"):
            for c in crowd.get("canteens", []):
                if c["id"] == canteen_id:
                    crowd_rate = c.get("overall_rate")
                    crowd_label = c.get("overall_label")
                    break
    except Exception:
        pass

    entry = {
        "id": _get_next_id(),
        "timestamp": now.isoformat(),
        "canteen_id": canteen_id,
        "canteen_name": canteen_name,
        "sub_area": sub_area,
        "meal_type": meal_type,
        "rating": rating,
        "note": note,
        "crowd_when_chosen": crowd_rate,
        "crowd_label_when_chosen": crowd_label,
    }

    history = _load_history()
    history.insert(0, entry)  # newest first
    # Cap at 200 to prevent unbounded growth
    if len(history) > 200:
        history = history[:200]

    atomic_write_json(DINING_HISTORY_PATH, history)

    msg = f"已记录：{meal_type}在{canteen_name}"
    if sub_area:
        msg += f"（{sub_area}）"
    if rating:
        stars = "⭐" * rating
        msg += f" 评价 {stars}"
    msg += "。下次推荐会更准确！"

    return {"ok": True, "recorded": entry, "message": msg}


def tool_get_dining_history(limit: int = 10) -> dict:
    """Retrieve dining history with derived preference stats."""
    history = _load_history()
    recent = history[:limit]

    if not history:
        return {
            "ok": True,
            "has_history": False,
            "message": "暂无用餐记录。开始使用推荐功能后，我会自动学习你的偏好。",
            "records": [],
            "stats": {},
        }

    # Compute preference stats
    freq_stats = _compute_frequency_stats(history)
    time_stats = _compute_time_stats(history)
    cuisine_history = _compute_cuisine_history(history)
    crowd_tolerance = _infer_crowd_tolerance(history)

    # Top canteens by weighted frequency
    top_canteens = sorted(freq_stats.items(), key=lambda x: x[1], reverse=True)
    top_canteen_names = []
    canteen_ids = {c["id"]: c["name"] for c in _load_knowledge().get("canteens", [])}
    # Map IDs to names from the knowledge base
    name_map = {}
    for c in _load_knowledge().get("canteens", []):
        name_map[c["id"]] = c["name"]
    # Also map from history records
    for r in history:
        name_map[r.get("canteen_id", 0)] = r.get("canteen_name", "")

    for cid, count in top_canteens[:5]:
        top_canteen_names.append({
            "canteen_id": cid,
            "canteen_name": name_map.get(cid, str(cid)),
            "weighted_visits": round(count, 1),
        })

    # Time patterns
    time_patterns = {}
    for cid, meals in time_stats.items():
        cname = name_map.get(cid, str(cid))
        time_patterns[cname] = {m: c for m, c in meals.items()}

    return {
        "ok": True,
        "has_history": True,
        "total_records": len(history),
        "recent_records": recent,
        "stats": {
            "top_canteens": top_canteen_names,
            "time_patterns": time_patterns,
            "preferred_cuisines": sorted(cuisine_history.items(), key=lambda x: x[1], reverse=True),
            "inferred_crowd_tolerance": crowd_tolerance,
            "total_visits": len(history),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# Scoring engine
# ══════════════════════════════════════════════════════════════════════════════

# Weights for the composite score (sum to 1.0)
W_CROWD = 0.35
W_FREQ = 0.20
W_TIME = 0.15
W_RECENCY = 0.10
W_CUISINE = 0.20



def _compute_frequency_stats(history: list[dict], now=None) -> dict[int, float]:
    """Count visits per canteen with recency weighting.

    Visits in last 7 days: 2x weight
    Visits in last 30 days: 1.5x weight
    Older: 1x weight
    """
    if now is None:
        now = _dt.datetime.now(dc.CST)
    freq: dict[int, float] = {}
    for r in history:
        cid = r.get("canteen_id", 0)
        if not cid:
            continue
        try:
            ts = _dt.datetime.fromisoformat(r["timestamp"])
        except (ValueError, KeyError):
            ts = now
        days_ago = (now - ts).days
        if days_ago <= 7:
            weight = 2.0
        elif days_ago <= 30:
            weight = 1.5
        else:
            weight = 1.0
        freq[cid] = freq.get(cid, 0) + weight
    return freq


def _compute_time_stats(history: list[dict]) -> dict[int, dict[str, int]]:
    """Build {canteen_id: {meal_type: visit_count}} matrix."""
    stats: dict[int, dict[str, int]] = {}
    for r in history:
        cid = r.get("canteen_id", 0)
        mt = r.get("meal_type", "")
        if not cid or not mt:
            continue
        if cid not in stats:
            stats[cid] = {}
        stats[cid][mt] = stats[cid].get(mt, 0) + 1
    return stats


def _compute_last_visits(history: list[dict]) -> dict[int, _dt.datetime]:
    """Build {canteen_id: last_visit_datetime}."""
    last: dict[int, _dt.datetime] = {}
    for r in history:
        cid = r.get("canteen_id", 0)
        if not cid or cid in last:
            continue  # history is newest-first, first hit = most recent
        try:
            last[cid] = _dt.datetime.fromisoformat(r["timestamp"])
        except (ValueError, KeyError):
            pass
    return last


def _compute_cuisine_history(history: list[dict]) -> dict[str, int]:
    """Count cuisine keyword mentions in history notes."""
    # This is a simple keyword counter. In the future this could be more
    # sophisticated (e.g., NLP on notes, or matching against knowledge base).
    keywords: dict[str, int] = {}
    cuisine_tags = {"米线", "面食", "西餐", "川菜", "湘菜", "本帮菜", "清真",
                    "小吃", "减脂", "轻食", "麻辣烫", "麻辣香锅", "咖喱",
                    "铁板", "火锅", "点心", "快餐", "素食", "烧烤", "茶餐厅"}
    for r in history:
        text = (r.get("note", "") + " " + r.get("sub_area", "")).lower()
        for tag in cuisine_tags:
            if tag.lower() in text:
                keywords[tag] = keywords.get(tag, 0) + 1
    return keywords


def _infer_crowd_tolerance(history: list[dict]) -> str:
    """Infer crowd tolerance from the crowd levels when user chose canteens."""
    rates = [r["crowd_when_chosen"] for r in history
             if r.get("crowd_when_chosen") is not None]
    if not rates:
        return "中"  # default
    avg = sum(rates) / len(rates)
    if avg <= 15:
        return "低"
    elif avg >= 40:
        return "高"
    return "中"


def _score_canteen(
    c: dict,
    meal_type: str,
    now: _dt.datetime,
    freq_stats: dict[int, float],
    time_stats: dict[int, dict[str, int]],
    last_visits: dict[int, _dt.datetime],
    cuisine_history: dict[str, int],
    crowd_tolerance: str,
    cuisine_preference: str,
    canteens_kb: list[dict],
) -> tuple[float, list[str]]:
    """Score a single canteen. Returns (score, reasons). Negative score = filtered."""
    reasons: list[str] = []

    # ── Hard filters ──
    # Exclude non-operational (holiday/renovation)
    if not c.get("is_operational"):
        return (-1.0, [])

    cid = c["id"]

    # ── Crowd score (0-100) ──
    if not c.get("is_dining"):
        crowd_score = 10.0
        reasons.append("当前非供餐时段，数据为上一餐段末残留")
    elif c["overall_rate"] is not None:
        rate = c["overall_rate"]
        crowd_score = max(0, 100 - rate)
        # Apply tolerance modifier
        if crowd_tolerance == "低":
            # Penalize canteens over 15% more heavily
            if rate > 15:
                crowd_score *= 0.3
        elif crowd_tolerance == "高":
            # Dampen crowd penalty above 40%
            if rate > 40:
                crowd_score += (rate - 40) * 0.5
        if crowd_score >= 70:
            reasons.append(f"当前{c['overall_label']}（拥挤度 {rate}%）")
    else:
        crowd_score = 30.0
        reasons.append("暂无拥挤度数据")

    # ── Frequency score (0-100) ──
    freq_count = freq_stats.get(cid, 0)
    max_freq = max(freq_stats.values()) if freq_stats else 1
    if freq_count == 0:
        freq_score = 20.0  # base score for new places
    else:
        freq_score = (freq_count / max_freq) * 100
        if freq_score >= 60:
            reasons.append("你常来这里")

    # ── Time match score (0-100) ──
    time_for_canteen = time_stats.get(cid, {})
    match_count = time_for_canteen.get(meal_type, 0)
    max_time_match = max(
        (ts.get(meal_type, 0) for ts in time_stats.values()),
        default=0,
    )
    if max_time_match > 0:
        time_score = (match_count / max_time_match) * 100
        if match_count >= 3:
            reasons.append(f"{meal_type}常来此处")
    else:
        time_score = 10.0

    # ── Recency score (0-100) — encourage variety ──
    last = last_visits.get(cid)
    if last is None:
        recency_score = 80.0  # never visited, encourage exploration
    else:
        days_since = max(0, (now - last).days)
        if days_since <= 1:
            recency_score = 20.0  # very recent, cooldown
            reasons.append("今天刚去过，换个口味？")
        else:
            recency_score = min(100, days_since * 10)
            if days_since >= 7:
                reasons.append(f"已 {days_since} 天没去，可以再去看看")

    # ── Cuisine score (0-100) ──
    cuisine_score = 50.0
    canteen_kb = next((k for k in canteens_kb if k.get("id") == cid), None)

    # If explicit cuisine_preference, match against canteen knowledge
    if cuisine_preference and canteen_kb:
        cuisine_score = _match_cuisine(cuisine_preference, canteen_kb)
        if cuisine_score >= 70:
            reasons.append(f"匹配你的菜系偏好「{cuisine_preference}」")
    elif cuisine_history and canteen_kb:
        # Use top historical cuisine
        top_cuisine = max(cuisine_history, key=cuisine_history.get) if cuisine_history else ""
        if top_cuisine:
            cuisine_score = _match_cuisine(top_cuisine, canteen_kb)

    # ── Composite score ──
    total = (
        W_CROWD * crowd_score
        + W_FREQ * freq_score
        + W_TIME * time_score
        + W_RECENCY * recency_score
        + W_CUISINE * cuisine_score
    )

    # Cap at 100
    return (min(total, 100.0), reasons)


def _match_cuisine(preference: str, canteen_kb: dict) -> float:
    """Score how well a canteen matches a cuisine preference. Returns 0-100.

    Base neutral score is 50. Matches add a bonus proportional to coverage;
    no match applies a mild penalty (30) to distinguish from "no preference."
    """
    pref_lower = preference.lower()
    total_tags = 0
    matched_tags = 0

    for floor in canteen_kb.get("floors", []):
        for sa in floor.get("sub_areas", []):
            total_tags += 1
            area_text = json.dumps(sa, ensure_ascii=False).lower()
            if pref_lower in area_text:
                matched_tags += 1
                continue
            # Also check recommended dishes
            for dish in sa.get("recommended_dishes", []):
                if pref_lower in dish.lower():
                    matched_tags += 0.5

    # Also check special_services
    for svc in canteen_kb.get("special_services", []):
        total_tags += 1
        svc_text = json.dumps(svc, ensure_ascii=False).lower()
        if pref_lower in svc_text:
            matched_tags += 1

    if total_tags == 0:
        return 50.0

    match_ratio = matched_tags / total_tags
    if match_ratio > 0:
        # Any match boosts above the 50 neutral baseline
        return min(100, 50.0 + match_ratio * 50.0)
    else:
        # Explicit preference with zero matches — mild penalty
        return 30.0


def _get_best_sub_areas(
    canteen_kb: dict | None,
    cuisine_preference: str,
    cuisine_history: dict[str, int],
) -> list[str]:
    """Pick recommended sub-areas from knowledge base."""
    if not canteen_kb:
        return []

    candidates: list[tuple[str, float]] = []

    for floor in canteen_kb.get("floors", []):
        for sa in floor.get("sub_areas", []):
            score = 0.0
            name = sa.get("name", "")
            if sa.get("is_popular"):
                score += 3
            if sa.get("recommended_dishes"):
                score += 2
            # Cuisine match bonus
            cuisine = sa.get("cuisine", "")
            if cuisine_preference and cuisine_preference.lower() in cuisine.lower():
                score += 5
            for ck, count in cuisine_history.items():
                if ck.lower() in cuisine.lower():
                    score += min(count, 3) * 1.5
            if score > 0:
                candidates.append((name, score))

    # Also check special_services
    for svc in canteen_kb.get("special_services", []):
        score = 0.5
        name = svc.get("name", "")
        candidates.append((name, score))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in candidates]


def _build_summary(
    recommendations: list[dict],
    meal_type: str,
    crowd_result: dict,
    history: list[dict],
) -> str:
    """Build a natural-language summary of the recommendations."""
    if not recommendations:
        return f"当前暂无合适的{meal_type}推荐，请检查食堂运营状态。"

    parts = []

    # Top picks
    top = recommendations[0]
    parts.append(f"首选推荐**{top['canteen_name']}**")
    if top["overall_rate"] is not None:
        parts.append(f"（拥挤度 {top['overall_rate']}%，{top['overall_label']}）")

    if len(recommendations) > 1:
        alt_names = ", ".join(r["canteen_name"] for r in recommendations[1:3])
        parts.append(f"，备选 {alt_names}")

    parts.append(f"。数据时间: {crowd_result.get('fetched_at', '未知')}。")

    # New user hint
    if not history:
        parts.append("尚无用餐记录，推荐完全基于实时拥挤度和食堂特色。")
        parts.append("选择食堂后我会自动记录，下次推荐会更符合你的口味。")

    return "".join(parts)
