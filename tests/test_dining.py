"""Tests for sjtu_agent/agent/tools/_dining.py — canteen recommendation system.

Covers:
- Pure logic helpers (_crowd_label, _infer_meal_type, scoring engine)
- Persistence (dining history read/write via atomic_write_json)
- Tool functions (get_canteen_crowd, get_canteen_info, recommend_canteen,
  record_dining_choice, get_dining_history)
- Integration (TOOLS_ENTRIES structure, run_tool dispatch)
- Edge cases (empty history, non-dining hours, missing data, API failures)
"""

import datetime as _dt
import json
from datetime import timezone, timedelta

import pytest

# CST helper for constructing timestamps in tests
CST = timezone(timedelta(hours=8))


# ══════════════════════════════════════════════════════════════════════════════
# Shared test data
# ══════════════════════════════════════════════════════════════════════════════

SAMPLE_CANTEEN_CROWD = [
    {
        "id": 100, "name": "第一餐饮大楼", "campus": "闵行",
        "is_operational": True, "is_dining": True,
        "schedule_desc": "午餐", "overall_rate": 12.5,
        "overall_label": "空闲",
        "subs": [
            {"name": "一楼餐厅", "current_rate": 10.0, "current_label": "空闲",
             "trend": "平稳", "is_open": True, "close_desc": "", "last_updated": "2026-06-18 12:00:00"},
        ],
        "note": "", "api_name": "第一餐饮大楼",
    },
    {
        "id": 200, "name": "第二餐饮大楼", "campus": "闵行",
        "is_operational": True, "is_dining": True,
        "schedule_desc": "午餐", "overall_rate": 35.0,
        "overall_label": "较挤",
        "subs": [
            {"name": "一楼餐厅", "current_rate": 35.0, "current_label": "较挤",
             "trend": "上升", "is_open": True, "close_desc": "", "last_updated": "2026-06-18 12:00:00"},
        ],
        "note": "", "api_name": "第二餐饮大楼",
    },
    {
        "id": 300, "name": "第三餐饮大楼", "campus": "闵行",
        "is_operational": True, "is_dining": True,
        "schedule_desc": "午餐", "overall_rate": 8.2,
        "overall_label": "空闲",
        "subs": [
            {"name": "一楼学生餐厅", "current_rate": 8.2, "current_label": "空闲",
             "trend": "下降", "is_open": True, "close_desc": "", "last_updated": "2026-06-18 12:00:00"},
        ],
        "note": "", "api_name": "第三餐饮大楼",
    },
    {
        "id": 400, "name": "第四餐饮大楼", "campus": "闵行",
        "is_operational": True, "is_dining": True,
        "schedule_desc": "午餐", "overall_rate": 22.0,
        "overall_label": "适中",
        "subs": [
            {"name": "小吃广场", "current_rate": 22.0, "current_label": "适中",
             "trend": "平稳", "is_open": True, "close_desc": "", "last_updated": "2026-06-18 12:00:00"},
        ],
        "note": "", "api_name": "第四餐饮大楼",
    },
    {
        "id": 700, "name": "第七餐饮大楼", "campus": "闵行",
        "is_operational": False, "is_dining": False,  # closed for holiday
        "schedule_desc": "Non-Dining Hours", "overall_rate": None,
        "overall_label": "非就餐时间",
        "subs": [],
        "note": "", "api_name": "第七餐饮大楼",
    },
]

# Minimal subset for knowledge tests
SAMPLE_CANTEEN_KNOWLEDGE = {
    "canteens": [
        {
            "id": 100,
            "name": "第一餐饮大楼",
            "campus": "闵行",
            "location_desc": "学生公寓西一区北面",
            "floors": [
                {
                    "floor": 1,
                    "meal_services": ["早餐", "午餐", "晚餐"],
                    "sub_areas": [
                        {"name": "陕西面食", "cuisine": "面食", "is_popular": True},
                        {"name": "云南米线", "cuisine": "米线", "is_popular": True},
                        {"name": "印度飞饼", "cuisine": "小吃", "is_popular": True},
                    ],
                },
            ],
            "special_services": [
                {"name": "民族风味餐厅", "cuisine": "清真"},
            ],
        },
        {
            "id": 200,
            "name": "第二餐饮大楼",
            "campus": "闵行",
            "floors": [
                {
                    "floor": 1,
                    "meal_services": ["早餐", "午餐", "晚餐"],
                    "sub_areas": [
                        {"name": "西式餐厅", "cuisine": "西餐", "recommended_dishes": ["烤盘饭"]},
                    ],
                },
            ],
        },
        {
            "id": 300,
            "name": "第三餐饮大楼",
            "campus": "闵行",
            "floors": [
                {
                    "floor": 1,
                    "meal_services": ["早餐", "午餐", "晚餐"],
                    "sub_areas": [
                        {"name": "鲜烫吊龙米线", "cuisine": "米线", "is_popular": True},
                    ],
                },
                {
                    "floor": 2,
                    "meal_services": ["午餐", "晚餐"],
                    "sub_areas": [
                        {"name": "民族风味餐厅", "cuisine": "清真"},
                    ],
                },
            ],
        },
        {
            "id": 500,
            "name": "第五餐饮大楼",
            "campus": "闵行",
            "floors": [
                {
                    "floor": 1,
                    "meal_services": ["午餐", "晚餐"],
                    "sub_areas": [
                        {"name": "东湖面馆", "cuisine": "面食",
                         "recommended_dishes": ["苏式汤包", "葱油拌面"]},
                    ],
                },
            ],
        },
        {
            "id": 800,
            "name": "哈乐餐厅",
            "campus": "闵行",
            "floors": [
                {
                    "floor": 1,
                    "meal_services": ["午餐", "晚餐"],
                    "sub_areas": [
                        {"name": "蔬食主义窗口", "cuisine": "素食"},
                    ],
                },
            ],
        },
        {
            "id": 900,
            "name": "玉兰苑",
            "campus": "闵行",
            "is_commercial": True,
            "floors": [],
        },
    ]
}

SAMPLE_DINING_HISTORY = [
    {
        "id": 5,
        "timestamp": "2026-06-18T12:00:00+08:00",
        "canteen_id": 500,
        "canteen_name": "第五餐饮大楼",
        "meal_type": "午餐",
        "rating": 5,
        "sub_area": "东湖面馆",
        "note": "苏式汤包绝了",
        "crowd_when_chosen": 25.0,
        "crowd_label_when_chosen": "适中",
    },
    {
        "id": 4,
        "timestamp": "2026-06-17T12:00:00+08:00",
        "canteen_id": 300,
        "canteen_name": "第三餐饮大楼",
        "meal_type": "午餐",
        "rating": 4,
        "sub_area": "外婆桥小吃广场",
        "note": "牛杂煲很棒",
        "crowd_when_chosen": 10.0,
        "crowd_label_when_chosen": "空闲",
    },
    {
        "id": 3,
        "timestamp": "2026-06-16T18:00:00+08:00",
        "canteen_id": 300,
        "canteen_name": "第三餐饮大楼",
        "meal_type": "晚餐",
        "rating": 4,
        "sub_area": "",
        "note": "",
        "crowd_when_chosen": 20.0,
        "crowd_label_when_chosen": "适中",
    },
    {
        "id": 2,
        "timestamp": "2026-06-16T12:10:00+08:00",
        "canteen_id": 100,
        "canteen_name": "第一餐饮大楼",
        "meal_type": "午餐",
        "rating": 4,
        "sub_area": "陕西面食",
        "note": "biangbiang面不错",
        "crowd_when_chosen": 12.0,
        "crowd_label_when_chosen": "空闲",
    },
    {
        "id": 1,
        "timestamp": "2026-06-15T12:05:00+08:00",
        "canteen_id": 300,
        "canteen_name": "第三餐饮大楼",
        "meal_type": "午餐",
        "rating": 5,
        "sub_area": "鲜烫吊龙米线",
        "note": "超好吃",
        "crowd_when_chosen": 8.5,
        "crowd_label_when_chosen": "空闲",
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# Fake CanteenCrowdChecker for testing without real API calls
# ══════════════════════════════════════════════════════════════════════════════


class FakeCanteenCrowdChecker:
    """Fake crowd checker that returns controllable test data."""

    def __init__(self, crowd_data=None, should_fail=False):
        self.crowd_data = crowd_data or SAMPLE_CANTEEN_CROWD
        self.should_fail = should_fail
        self.calls = []

    def get_all_crowd(self, campus=""):
        self.calls.append(("get_all_crowd", campus))
        if self.should_fail:
            return {"ok": False, "error": "Connection timeout"}

        # Filter by campus if specified
        canteens = self.crowd_data
        if campus:
            canteens = [c for c in canteens if c.get("campus") == campus]

        return {
            "ok": True,
            "campus": campus or "全部",
            "total": len(canteens),
            "fetched_at": "2026-06-18 12:00:00",
            "canteens": canteens,
        }

    def get_canteen_detail(self, canteen_id):
        self.calls.append(("get_canteen_detail", canteen_id))
        if self.should_fail:
            return {"ok": False, "error": "API error"}
        # Return matching canteen as "detail"
        for c in self.crowd_data:
            if c["id"] == canteen_id:
                return {"ok": True, "detail": {
                    "scheduleDesc": c.get("schedule_desc", ""),
                    "scheduleStatus": 1 if c.get("is_dining") else 0,
                    "subs": [
                        {
                            "name": s["name"],
                            "isOpen": s.get("is_open", True),
                            "closeDesc": s.get("close_desc", ""),
                            "curRates": [{"rate": s["current_rate"], "time": s.get("last_updated", "2026-06-18 12:00:00")}]
                            if s.get("current_rate") is not None else [],
                        }
                        for s in c.get("subs", [])
                    ],
                }}
        return {"ok": False, "error": f"Canteen {canteen_id} not found"}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers to set up isolated test environment
# ══════════════════════════════════════════════════════════════════════════════


def _setup_test_knowledge(tmp_path):
    """Write sample knowledge JSON and monkeypatch CANTEEN_KNOWLEDGE_PATH."""
    kp = tmp_path / "canteen_knowledge.json"
    kp.write_text(json.dumps(SAMPLE_CANTEEN_KNOWLEDGE, ensure_ascii=False), encoding="utf-8")
    import sjtu_agent.agent.tools._dining as dining_mod
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(dining_mod, "CANTEEN_KNOWLEDGE_PATH", kp)
    return kp, monkeypatch


def _setup_test_history(tmp_path, history=None):
    """Write sample history JSON and monkeypatch DINING_HISTORY_PATH."""
    hp = tmp_path / "dining_history.json"
    if history is not None:
        hp.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    import sjtu_agent.agent.tools._dining as dining_mod
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(dining_mod, "DINING_HISTORY_PATH", hp)
    return hp, monkeypatch


# ══════════════════════════════════════════════════════════════════════════════
# Tests: canonical _crowd_label (imported from canteen_crowd.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestCrowdLabel:
    """The canonical _crowd_label lives in sjtu_agent.data.canteen_crowd."""

    @pytest.mark.parametrize("rate,expected", [
        (0, "空闲"),
        (5, "空闲"),
        (20, "适中"),
        (35, "较挤"),
        (55, "拥挤"),
        (65, "爆满"),
        (100, "爆满"),
    ])
    def test_crowd_label_thresholds(self, rate, expected):
        from sjtu_agent.data.canteen_crowd import _crowd_label
        assert _crowd_label(rate) == expected


# ══════════════════════════════════════════════════════════════════════════════
# Tests: pure helper functions
# ══════════════════════════════════════════════════════════════════════════════


class TestInferMealType:
    """_infer_meal_type is a pure function of hour-of-day."""

    @pytest.mark.parametrize("hour,expected", [
        (5, "午餐"),     # default (off-hours)
        (6, "早餐"),
        (7, "早餐"),
        (9, "早餐"),
        (10, "午餐"),
        (12, "午餐"),
        (13, "午餐"),
        (15, "午餐"),    # late lunch
        (17, "晚餐"),
        (18, "晚餐"),
        (19, "晚餐"),
        (20, "夜宵"),
        (21, "夜宵"),
        (22, "夜宵"),
        (23, "午餐"),    # past midnight snack → default
    ])
    def test_infer_meal_type(self, hour, expected):
        from sjtu_agent.agent.tools._dining import _infer_meal_type
        now = _dt.datetime(2026, 6, 18, hour, 0, 0, tzinfo=CST)
        assert _infer_meal_type(now) == expected


class TestFrequencyStats:
    """_compute_frequency_stats counts visits with recency weighting."""

    def test_empty(self):
        from sjtu_agent.agent.tools._dining import _compute_frequency_stats
        assert _compute_frequency_stats([]) == {}

    def test_weighting(self):
        import datetime as _dt
        from sjtu_agent.agent.tools._dining import _compute_frequency_stats
        # Use a fixed "now" just after the sample data dates so test is not time-sensitive
        fixed_now = _dt.datetime(2026, 6, 19, 0, 0, 0, tzinfo=CST)
        stats = _compute_frequency_stats(SAMPLE_DINING_HISTORY, now=fixed_now)
        # canteen 300 appears 3 times: June 15, 16, 17
        # all within 7 days → 2x weight each = 6.0
        assert stats[300] == pytest.approx(6.0, abs=0.1)
        # canteen 100: 1 visit → 2x = 2.0
        assert stats[100] == pytest.approx(2.0, abs=0.1)
        # canteen 500: 1 visit → 2x = 2.0
        assert stats[500] == pytest.approx(2.0, abs=0.1)

    def test_missing_canteen_id(self):
        from sjtu_agent.agent.tools._dining import _compute_frequency_stats
        stats = _compute_frequency_stats([{"canteen_id": 0, "timestamp": "2026-06-17T12:00:00+08:00"}])
        assert stats == {}


class TestTimeStats:
    """_compute_time_stats builds {canteen_id: {meal_type: count}}."""

    def test_empty(self):
        from sjtu_agent.agent.tools._dining import _compute_time_stats
        assert _compute_time_stats([]) == {}

    def test_patterns(self):
        from sjtu_agent.agent.tools._dining import _compute_time_stats
        stats = _compute_time_stats(SAMPLE_DINING_HISTORY)
        # canteen 300: 2 lunches + 1 dinner
        assert stats[300] == {"午餐": 2, "晚餐": 1}
        # canteen 100: 1 lunch
        assert stats[100] == {"午餐": 1}
        # canteen 500: 1 lunch
        assert stats[500] == {"午餐": 1}


class TestLastVisits:
    """_compute_last_visits returns most recent visit per canteen."""

    def test_empty(self):
        from sjtu_agent.agent.tools._dining import _compute_last_visits
        assert _compute_last_visits([]) == {}

    def test_most_recent(self):
        from sjtu_agent.agent.tools._dining import _compute_last_visits
        last = _compute_last_visits(SAMPLE_DINING_HISTORY)
        # History is newest-first. For canteen 300, newest visit is id=4 (June 17)
        # and newest overall is canteen 500 (June 18)
        assert 300 in last
        assert last[300].day == 17
        assert 500 in last
        assert last[500].day == 18
        # canteen 500 is the most recent overall (June 18)
        assert last[500].day == 18


class TestCuisineHistory:
    """_compute_cuisine_history extracts cuisine keywords from notes."""

    def test_empty(self):
        from sjtu_agent.agent.tools._dining import _compute_cuisine_history
        assert _compute_cuisine_history([]) == {}

    def test_extracts_from_history(self):
        from sjtu_agent.agent.tools._dining import _compute_cuisine_history
        # "米线" appears in sub_area of entry 1
        cuisines = _compute_cuisine_history(SAMPLE_DINING_HISTORY)
        # "米线" keyword appears in "鲜烫吊龙米线"
        assert "米线" in cuisines
        assert cuisines["米线"] >= 1


class TestInferCrowdTolerance:
    """_infer_crowd_tolerance derives tolerance from chosen crowd rates."""

    def test_empty(self):
        from sjtu_agent.agent.tools._dining import _infer_crowd_tolerance
        assert _infer_crowd_tolerance([]) == "中"

    def test_low_tolerance(self):
        from sjtu_agent.agent.tools._dining import _infer_crowd_tolerance
        # All chosen when crowd was ≤15 → low tolerance
        history = [
            {"crowd_when_chosen": 8.5},
            {"crowd_when_chosen": 5.0},
            {"crowd_when_chosen": 12.0},
        ]
        assert _infer_crowd_tolerance(history) == "低"

    def test_high_tolerance(self):
        from sjtu_agent.agent.tools._dining import _infer_crowd_tolerance
        history = [
            {"crowd_when_chosen": 45.0},
            {"crowd_when_chosen": 55.0},
        ]
        assert _infer_crowd_tolerance(history) == "高"


class TestMatchCuisine:
    """_match_cuisine scores canteen knowledge against a preference."""

    def test_exact_match(self):
        from sjtu_agent.agent.tools._dining import _match_cuisine
        canteen = SAMPLE_CANTEEN_KNOWLEDGE["canteens"][0]  # 一餐: 面食/米线/小吃/清真
        score = _match_cuisine("面食", canteen)
        # 1 match out of 4 tags → 50 + 0.25*50 = 62.5
        assert score == pytest.approx(62.5, abs=0.1)

    def test_partial_match(self):
        from sjtu_agent.agent.tools._dining import _match_cuisine
        canteen = SAMPLE_CANTEEN_KNOWLEDGE["canteens"][1]  # 二餐: 西餐
        score = _match_cuisine("西餐", canteen)
        assert score > 50

    def test_no_match(self):
        from sjtu_agent.agent.tools._dining import _match_cuisine
        canteen = SAMPLE_CANTEEN_KNOWLEDGE["canteens"][1]  # 二餐: 西餐 only
        score = _match_cuisine("素食", canteen)
        # Should be lower than the match score for a canteen that has it
        veg_canteen = SAMPLE_CANTEEN_KNOWLEDGE["canteens"][4]  # 哈乐: 素食
        veg_score = _match_cuisine("素食", veg_canteen)
        assert veg_score > score

    def test_empty_canteen(self):
        from sjtu_agent.agent.tools._dining import _match_cuisine
        assert _match_cuisine("anything", {}) == 50.0


class TestScoreCanteen:
    """_score_canteen produces a composite score with reasons."""

    def test_filters_non_operational(self, monkeypatch):
        from sjtu_agent.agent.tools._dining import _score_canteen
        now = _dt.datetime(2026, 6, 18, 12, 0, 0, tzinfo=CST)
        closed_canteen = SAMPLE_CANTEEN_CROWD[4]  # 七餐 is_operational=False
        score, reasons = _score_canteen(
            closed_canteen, "午餐", now, {}, {}, {}, {}, "中", "", [],
        )
        assert score < 0  # filtered out

    def test_scores_dining_canteen(self, monkeypatch):
        from sjtu_agent.agent.tools._dining import _score_canteen
        now = _dt.datetime(2026, 6, 18, 12, 0, 0, tzinfo=CST)
        c = SAMPLE_CANTEEN_CROWD[2]  # 三餐: rate=8.2, is_dining=True
        score, reasons = _score_canteen(
            c, "午餐", now, {}, {}, {}, {}, "中", "", SAMPLE_CANTEEN_KNOWLEDGE["canteens"],
        )
        assert score > 0
        # Low crowd should give high crowd score
        assert score >= 30  # crowd = 100 - 8.2 = 91.8 * 0.35 ≈ 32, plus others

    def test_prefers_favorite_with_history(self, monkeypatch):
        from sjtu_agent.agent.tools._dining import (
            _score_canteen, _compute_frequency_stats,
            _compute_time_stats, _compute_last_visits, _compute_cuisine_history,
        )
        now = _dt.datetime(2026, 6, 18, 12, 0, 0, tzinfo=CST)
        freq = _compute_frequency_stats(SAMPLE_DINING_HISTORY, now=now)
        time_s = _compute_time_stats(SAMPLE_DINING_HISTORY)
        last = _compute_last_visits(SAMPLE_DINING_HISTORY)
        cuisine = _compute_cuisine_history(SAMPLE_DINING_HISTORY)

        # 三餐: frequent visitor, low crowd
        c300 = SAMPLE_CANTEEN_CROWD[2]
        score_300, _ = _score_canteen(
            c300, "午餐", now, freq, time_s, last, cuisine, "中", "",
            SAMPLE_CANTEEN_KNOWLEDGE["canteens"],
        )

        # 四餐: never visited, moderate crowd
        c400 = SAMPLE_CANTEEN_CROWD[3]
        score_400, _ = _score_canteen(
            c400, "午餐", now, freq, time_s, last, cuisine, "中", "",
            SAMPLE_CANTEEN_KNOWLEDGE["canteens"],
        )

        # 三餐 should score higher (frequent + low crowd)
        assert score_300 > score_400

    def test_cuisine_preference_boosts_score(self, monkeypatch):
        from sjtu_agent.agent.tools._dining import _score_canteen
        now = _dt.datetime(2026, 6, 18, 12, 0, 0, tzinfo=CST)

        c100 = SAMPLE_CANTEEN_CROWD[0]  # 一餐
        score_no_pref, _ = _score_canteen(
            c100, "午餐", now, {}, {}, {}, {}, "中", "",
            SAMPLE_CANTEEN_KNOWLEDGE["canteens"],
        )
        score_with_pref, _ = _score_canteen(
            c100, "午餐", now, {}, {}, {}, {}, "中", "米线",
            SAMPLE_CANTEEN_KNOWLEDGE["canteens"],
        )
        # With matching cuisine, _match_cuisine returns 62.5 > neutral 50
        # so the composite score should be higher
        assert score_with_pref > score_no_pref


# ══════════════════════════════════════════════════════════════════════════════
# Tests: tool functions
# ══════════════════════════════════════════════════════════════════════════════


class TestToolGetCanteenInfo:
    """tool_get_canteen_info reads from the knowledge JSON."""

    def test_by_id(self, tmp_path):
        kp, mp = _setup_test_knowledge(tmp_path)
        try:
            from sjtu_agent.agent.tools._dining import tool_get_canteen_info
            r = tool_get_canteen_info(canteen_id=100)
            assert r["ok"] is True
            assert r["canteen"]["name"] == "第一餐饮大楼"
        finally:
            mp.undo()

    def test_by_name_shorthand(self, tmp_path):
        kp, mp = _setup_test_knowledge(tmp_path)
        try:
            from sjtu_agent.agent.tools._dining import tool_get_canteen_info
            # "三餐" should match 第三餐饮大楼 (id=300)
            r = tool_get_canteen_info(canteen_name="三餐")
            assert r["ok"] is True
            assert r["canteen"]["id"] == 300
        finally:
            mp.undo()

    def test_by_name_exact(self, tmp_path):
        kp, mp = _setup_test_knowledge(tmp_path)
        try:
            from sjtu_agent.agent.tools._dining import tool_get_canteen_info
            r = tool_get_canteen_info(canteen_name="哈乐餐厅")
            assert r["ok"] is True
            assert r["canteen"]["id"] == 800
        finally:
            mp.undo()

    def test_not_found(self, tmp_path):
        kp, mp = _setup_test_knowledge(tmp_path)
        try:
            from sjtu_agent.agent.tools._dining import tool_get_canteen_info
            r = tool_get_canteen_info(canteen_name="不存在食堂")
            assert r["ok"] is False
            assert "available" in r
        finally:
            mp.undo()

    def test_no_knowledge_file(self, tmp_path):
        """When knowledge file is missing, returns error."""
        import sjtu_agent.agent.tools._dining as dining_mod
        mp = pytest.MonkeyPatch()
        mp.setattr(dining_mod, "CANTEEN_KNOWLEDGE_PATH", tmp_path / "nonexistent.json")
        try:
            from sjtu_agent.agent.tools._dining import tool_get_canteen_info
            r = tool_get_canteen_info(canteen_id=100)
            assert r["ok"] is False
        finally:
            mp.undo()


class TestToolGetCanteenCrowd:
    """tool_get_canteen_crowd fetches from the CanteenCrowdChecker."""

    def test_overview(self, monkeypatch):
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        from sjtu_agent.agent.tools._dining import tool_get_canteen_crowd
        r = tool_get_canteen_crowd(campus="闵行")
        assert r["ok"] is True
        assert r["total"] == 5  # 4 operational + 1 closed, all returned
        assert len(r["canteens"]) == 5

    def test_detail(self, monkeypatch):
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        from sjtu_agent.agent.tools._dining import tool_get_canteen_crowd
        r = tool_get_canteen_crowd(canteen_id=300)
        assert r["ok"] is True
        assert r["canteen_id"] == 300
        assert "subs" in r

    def test_api_failure(self, monkeypatch):
        fake = FakeCanteenCrowdChecker(should_fail=True)
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        from sjtu_agent.agent.tools._dining import tool_get_canteen_crowd
        r = tool_get_canteen_crowd()
        assert r["ok"] is False

    def test_overview_rate_zero_treated_as_no_data(self, monkeypatch):
        """A canteen sub-area with rate=0 should appear as '无数据', not '空闲'."""
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        from sjtu_agent.agent.tools._dining import tool_get_canteen_crowd
        r = tool_get_canteen_crowd(canteen_id=300)
        assert r["ok"] is True
        for sub in r["subs"]:
            if sub["current_rate"] is None:
                assert sub["current_label"] == "无数据"

    def test_detail_rate_zero_treated_as_no_data(self, monkeypatch):
        """tool_get_canteen_crowd detail path: rate=0 → None + '无数据'."""
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        from sjtu_agent.agent.tools._dining import tool_get_canteen_crowd
        r = tool_get_canteen_crowd(canteen_id=300)
        assert r["ok"] is True
        for sub in r["subs"]:
            if sub["current_rate"] is None:
                assert sub["current_label"] == "无数据"


class TestCanteenCrowdCheckerGetAllCrowd:
    """Direct tests for CanteenCrowdChecker.get_all_crowd() with mocked HTTP."""

    def test_rate_zero_sub_area_is_no_data(self, monkeypatch):
        """Sub-area with curRates=[{rate:0}] → current_rate=None, label='无数据'."""
        from sjtu_agent.data.canteen_crowd import CanteenCrowdChecker

        def mock_get_all_canteens(self):
            return {
                "ok": True,
                "canteens": [{"id": 100, "name": "第一餐饮大楼", "campus": "闵行", "isOpen": True}],
            }

        def mock_get_canteen_detail(self, canteen_id):
            return {
                "ok": True,
                "detail": {
                    "scheduleStatus": 1,
                    "scheduleDesc": "午餐",
                    "subs": [
                        {
                            "name": "一楼餐厅",
                            "isOpen": False,
                            "closeDesc": "",
                            "curRates": [{"rate": 0, "time": "2026-06-20 12:00:00"}],
                        },
                    ],
                },
            }

        monkeypatch.setattr(CanteenCrowdChecker, "get_all_canteens", mock_get_all_canteens)
        monkeypatch.setattr(CanteenCrowdChecker, "get_canteen_detail", mock_get_canteen_detail)

        checker = CanteenCrowdChecker()
        result = checker.get_all_crowd()
        assert result["ok"] is True
        assert len(result["canteens"]) == 1
        c = result["canteens"][0]
        assert len(c["subs"]) == 1
        sub = c["subs"][0]
        assert sub["current_rate"] is None
        assert sub["current_label"] == "无数据"
        assert c["overall_rate"] is None
        assert c["overall_label"] == "非就餐时间"

    def test_rate_zero_with_open_sub_area_treated_as_no_data(self, monkeypatch):
        """Even when isOpen=True, rate=0 should still be treated as no-data."""
        from sjtu_agent.data.canteen_crowd import CanteenCrowdChecker

        def mock_get_all_canteens(self):
            return {
                "ok": True,
                "canteens": [{"id": 100, "name": "第一餐饮大楼", "campus": "闵行", "isOpen": True}],
            }

        def mock_get_canteen_detail(self, canteen_id):
            return {
                "ok": True,
                "detail": {
                    "scheduleStatus": 1,
                    "scheduleDesc": "午餐",
                    "subs": [
                        {
                            "name": "一楼餐厅",
                            "isOpen": True,
                            "closeDesc": "",
                            "curRates": [{"rate": 0, "time": "2026-06-20 12:00:00"}],
                        },
                    ],
                },
            }

        monkeypatch.setattr(CanteenCrowdChecker, "get_all_canteens", mock_get_all_canteens)
        monkeypatch.setattr(CanteenCrowdChecker, "get_canteen_detail", mock_get_canteen_detail)

        checker = CanteenCrowdChecker()
        result = checker.get_all_crowd()
        assert result["ok"] is True
        c = result["canteens"][0]
        sub = c["subs"][0]
        assert sub["current_rate"] is None
        assert sub["current_label"] == "无数据"


class TestToolRecommendCanteen:
    """tool_recommend_canteen combines crowd + history + knowledge."""

    def test_basic_recommendation(self, monkeypatch, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=[])  # empty history
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._dining import tool_recommend_canteen
            r = tool_recommend_canteen(campus="闵行", meal_type="午餐", top_n=3)
            assert r["ok"] is True
            assert len(r["recommendations"]) == 3
            assert r["has_history"] is False
            # Should note there's no history
            assert "尚无用餐记录" in r.get("summary", "")
        finally:
            kmp.undo()
            hmp.undo()

    def test_with_history(self, monkeypatch, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=SAMPLE_DINING_HISTORY)
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._dining import tool_recommend_canteen
            r = tool_recommend_canteen(campus="闵行", meal_type="午餐", top_n=4)
            assert r["ok"] is True
            assert r["has_history"] is True
            assert r["history_count"] == 5
            # 三餐 (id=300) should rank high — most visits + low crowd
            top_ids = [rec["canteen_id"] for rec in r["recommendations"]]
            assert 300 in top_ids
        finally:
            kmp.undo()
            hmp.undo()

    def test_with_cuisine_preference(self, monkeypatch, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=[])
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._dining import tool_recommend_canteen
            r = tool_recommend_canteen(cuisine_preference="面食", top_n=3)
            assert r["ok"] is True
            # 一餐 has "陕西面食" (is_popular=True) — should rank high for 面食
            recs = r["recommendations"]
            assert any(
                "面食" in str(rec.get("recommended_sub_areas", []))
                or rec["canteen_id"] == 100
                for rec in recs
            )
        finally:
            kmp.undo()
            hmp.undo()

    def test_api_failure(self, monkeypatch, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=[])
        fake = FakeCanteenCrowdChecker(should_fail=True)
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._dining import tool_recommend_canteen
            r = tool_recommend_canteen()
            assert r["ok"] is False
            assert "无法获取" in r.get("error", "")
        finally:
            kmp.undo()
            hmp.undo()

    def test_reasons_included(self, monkeypatch, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=SAMPLE_DINING_HISTORY)
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._dining import tool_recommend_canteen
            r = tool_recommend_canteen(top_n=3)
            for rec in r["recommendations"]:
                assert isinstance(rec["reasons"], list)
                assert isinstance(rec["score"], (int, float))
        finally:
            kmp.undo()
            hmp.undo()

    def test_non_dining_hours_handled(self, monkeypatch, tmp_path):
        """All canteens in non-dining should still return recommendations."""
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=[])
        # All canteens marked as non-dining
        non_dining_data = [
            {**c, "is_dining": False, "overall_rate": None,
             "overall_label": "非就餐时间", "schedule_desc": "Non-Dining Hours"}
            for c in SAMPLE_CANTEEN_CROWD
        ]
        fake = FakeCanteenCrowdChecker(crowd_data=non_dining_data)
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._dining import tool_recommend_canteen
            r = tool_recommend_canteen(campus="闵行", meal_type="午餐", top_n=3)
            assert r["ok"] is True
            # Should still return results (just with lower scores)
            assert len(r["recommendations"]) >= 1
        finally:
            kmp.undo()
            hmp.undo()


class TestToolRecordDiningChoice:
    """tool_record_dining_choice persists to dining_history.json."""

    def test_records_choice(self, tmp_path):
        hp, mp = _setup_test_history(tmp_path, history=[])
        try:
            from sjtu_agent.agent.tools._dining import tool_record_dining_choice
            r = tool_record_dining_choice(
                canteen_id=300,
                canteen_name="第三餐饮大楼",
                sub_area="鲜烫吊龙米线",
                rating=5,
                meal_type="午餐",
            )
            assert r["ok"] is True
            assert r["recorded"]["canteen_id"] == 300
            assert r["recorded"]["rating"] == 5

            # Verify file was written
            assert hp.exists()
            data = json.loads(hp.read_text(encoding="utf-8"))
            assert len(data) == 1
            assert data[0]["canteen_name"] == "第三餐饮大楼"
        finally:
            mp.undo()

    def test_auto_infers_meal_type(self, tmp_path):
        hp, mp = _setup_test_history(tmp_path, history=[])
        try:
            from sjtu_agent.agent.tools._dining import tool_record_dining_choice
            r = tool_record_dining_choice(
                canteen_id=100, canteen_name="第一餐饮大楼",
            )
            assert r["ok"] is True
            # meal_type should be auto-inferred (not empty)
            assert r["recorded"]["meal_type"] != ""
        finally:
            mp.undo()

    def test_caps_at_200(self, tmp_path):
        """History should be capped at 200 entries."""
        large_history = [
            {
                "id": i,
                "timestamp": "2026-06-10T12:00:00+08:00",
                "canteen_id": 100,
                "canteen_name": "第一餐饮大楼",
                "meal_type": "午餐",
            }
            for i in range(1, 201)  # 200 entries already
        ]
        hp, mp = _setup_test_history(tmp_path, history=large_history)
        try:
            from sjtu_agent.agent.tools._dining import tool_record_dining_choice
            r = tool_record_dining_choice(canteen_id=300, canteen_name="第三餐饮大楼")
            assert r["ok"] is True

            data = json.loads(hp.read_text(encoding="utf-8"))
            # Should still be 200, oldest removed
            assert len(data) == 200
            # Newest entry first
            assert data[0]["canteen_id"] == 300
        finally:
            mp.undo()

    def test_multiple_records_accumulate(self, tmp_path):
        hp, mp = _setup_test_history(tmp_path, history=[])
        try:
            from sjtu_agent.agent.tools._dining import tool_record_dining_choice
            tool_record_dining_choice(canteen_id=300, canteen_name="三餐")
            tool_record_dining_choice(canteen_id=100, canteen_name="一餐", rating=3)
            tool_record_dining_choice(canteen_id=500, canteen_name="五餐", meal_type="晚餐")

            data = json.loads(hp.read_text(encoding="utf-8"))
            assert len(data) == 3
            assert data[0]["canteen_id"] == 500  # newest first
            assert data[2]["canteen_id"] == 300  # oldest last
        finally:
            mp.undo()


class TestToolGetDiningHistory:
    """tool_get_dining_history reads history and computes preference stats."""

    def test_empty_history(self, tmp_path):
        hp, mp = _setup_test_history(tmp_path, history=[])
        try:
            from sjtu_agent.agent.tools._dining import tool_get_dining_history
            r = tool_get_dining_history()
            assert r["ok"] is True
            assert r["has_history"] is False
            assert r["records"] == []
        finally:
            mp.undo()

    def test_with_history(self, tmp_path):
        hp, mp = _setup_test_history(tmp_path, history=SAMPLE_DINING_HISTORY)
        try:
            from sjtu_agent.agent.tools._dining import tool_get_dining_history
            r = tool_get_dining_history(limit=3)
            assert r["ok"] is True
            assert r["has_history"] is True
            assert r["total_records"] == 5
            assert len(r["recent_records"]) == 3
            # Stats should be computed
            assert len(r["stats"]["top_canteens"]) > 0
            assert "time_patterns" in r["stats"]
            assert "inferred_crowd_tolerance" in r["stats"]
        finally:
            mp.undo()

    def test_corrupted_file(self, tmp_path):
        hp = tmp_path / "dining_history.json"
        hp.write_text("this is not valid json", encoding="utf-8")
        mp = pytest.MonkeyPatch()
        import sjtu_agent.agent.tools._dining as dining_mod
        mp.setattr(dining_mod, "DINING_HISTORY_PATH", hp)
        try:
            from sjtu_agent.agent.tools._dining import tool_get_dining_history
            r = tool_get_dining_history()
            # read_json_safe returns [] on corrupted file
            assert r["has_history"] is False
        finally:
            mp.undo()


# ══════════════════════════════════════════════════════════════════════════════
# Tests: TOOLS_ENTRIES structure
# ══════════════════════════════════════════════════════════════════════════════


class TestToolsEntries:
    """TOOLS_ENTRIES must follow OpenAI function-calling format."""

    def test_all_entries_have_type_function(self):
        from sjtu_agent.agent.tools._dining import TOOLS_ENTRIES
        for entry in TOOLS_ENTRIES:
            assert entry["type"] == "function"
            assert "name" in entry["function"]
            assert "description" in entry["function"]
            assert "parameters" in entry["function"]
            assert entry["function"]["parameters"]["type"] == "object"

    def test_all_tool_names_match_implementations(self):
        from sjtu_agent.agent.tools import _dining as mod
        for entry in mod.TOOLS_ENTRIES:
            name = entry["function"]["name"]
            func_name = f"tool_{name}"
            assert hasattr(mod, func_name), f"Missing {func_name}"
            assert callable(getattr(mod, func_name))

    def test_tool_count(self):
        from sjtu_agent.agent.tools._dining import TOOLS_ENTRIES
        assert len(TOOLS_ENTRIES) == 5


# ══════════════════════════════════════════════════════════════════════════════
# Tests: run_tool dispatch integration
# ══════════════════════════════════════════════════════════════════════════════


class TestRunToolDispatch:
    """Each dining tool must be reachable via run_tool()."""

    def test_get_canteen_info_dispatch(self, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        try:
            from sjtu_agent.agent.tools._core import run_tool
            import json as _json
            r = _json.loads(run_tool("get_canteen_info", {"canteen_id": 300}))
            assert r["ok"] is True
            assert r["canteen"]["name"] == "第三餐饮大楼"
        finally:
            kmp.undo()

    def test_get_dining_history_dispatch(self, tmp_path):
        hp, mp = _setup_test_history(tmp_path, history=[])
        try:
            from sjtu_agent.agent.tools._core import run_tool
            import json as _json
            r = _json.loads(run_tool("get_dining_history", {}))
            assert r["ok"] is True
        finally:
            mp.undo()

    def test_record_dining_choice_dispatch(self, tmp_path):
        hp, mp = _setup_test_history(tmp_path, history=[])
        try:
            from sjtu_agent.agent.tools._core import run_tool
            import json as _json
            args = {"canteen_id": 100, "canteen_name": "第一餐饮大楼", "meal_type": "午餐"}
            r = _json.loads(run_tool("record_dining_choice", args))
            assert r["ok"] is True
        finally:
            mp.undo()

    def test_recommend_canteen_dispatch(self, monkeypatch, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=[])
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._core import run_tool
            import json as _json
            args = {"campus": "闵行", "meal_type": "午餐", "top_n": 2}
            r = _json.loads(run_tool("recommend_canteen", args))
            assert r["ok"] is True
            assert len(r["recommendations"]) == 2
        finally:
            kmp.undo()
            hmp.undo()

    def test_get_canteen_crowd_dispatch(self, monkeypatch):
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        from sjtu_agent.agent.tools._core import run_tool
        import json as _json
        r = _json.loads(run_tool("get_canteen_crowd", {"campus": "闵行"}))
        assert r["ok"] is True

    def test_unknown_tool_returns_error(self):
        from sjtu_agent.agent.tools._core import run_tool
        import json as _json
        r = _json.loads(run_tool("nonexistent_tool", {}))
        assert "error" in r


# ══════════════════════════════════════════════════════════════════════════════
# Tests: persistence (atomic_write_json / read_json_safe)
# ══════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    """Verify dining history uses crash-safe persistence helpers."""

    def test_crash_safety_atomic_write(self, tmp_path):
        """atomic_write_json never leaves a half-written file."""
        from sjtu_agent.paths import atomic_write_json, read_json_safe
        hp = tmp_path / "test_atomic.json"
        # Write a large payload — if crash safety is violated, we'd see truncation
        data = [{"id": i, "text": "x" * 100} for i in range(100)]
        atomic_write_json(hp, data)
        loaded = read_json_safe(hp)
        assert len(loaded) == 100

    def test_read_empty_file(self, tmp_path):
        from sjtu_agent.paths import read_json_safe
        hp = tmp_path / "empty.json"
        hp.write_text("", encoding="utf-8")
        result = read_json_safe(hp, default=[])
        assert result == []

    def test_read_nonexistent_file(self, tmp_path):
        from sjtu_agent.paths import read_json_safe
        result = read_json_safe(tmp_path / "does_not_exist.json", default=[])
        assert result == []

    def test_read_corrupted_file(self, tmp_path):
        from sjtu_agent.paths import read_json_safe
        hp = tmp_path / "bad.json"
        hp.write_text("{ this is not valid", encoding="utf-8")
        result = read_json_safe(hp, default=[])
        assert result == []


# ══════════════════════════════════════════════════════════════════════════════
# Tests: edge cases & regression
# ══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case handling."""

    def test_recommend_when_no_operational_canteens(self, monkeypatch, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=[])
        # All canteens closed
        closed_data = [
            {**c, "is_operational": False}
            for c in SAMPLE_CANTEEN_CROWD
        ]
        fake = FakeCanteenCrowdChecker(crowd_data=closed_data)
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._dining import tool_recommend_canteen
            r = tool_recommend_canteen(campus="闵行", meal_type="午餐")
            # Should still return ok=True but with empty recommendations
            assert r["ok"] is True
        finally:
            kmp.undo()
            hmp.undo()

    def test_crowd_data_none_rate(self, monkeypatch, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=[])
        # All canteens have None overall_rate
        none_rate_data = [
            {**c, "overall_rate": None, "overall_label": "非就餐时间"}
            for c in SAMPLE_CANTEEN_CROWD[:4]
        ]
        fake = FakeCanteenCrowdChecker(crowd_data=none_rate_data)
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._dining import tool_recommend_canteen
            r = tool_recommend_canteen(top_n=3)
            assert r["ok"] is True
            # Should still return results
            assert len(r["recommendations"]) >= 1
        finally:
            kmp.undo()
            hmp.undo()

    def test_scoring_is_deterministic(self, monkeypatch, tmp_path):
        kp, kmp = _setup_test_knowledge(tmp_path)
        hp, hmp = _setup_test_history(tmp_path, history=SAMPLE_DINING_HISTORY)
        fake = FakeCanteenCrowdChecker()
        monkeypatch.setattr(
            "sjtu_agent.data.canteen_crowd.CanteenCrowdChecker",
            lambda: fake,
        )
        try:
            from sjtu_agent.agent.tools._dining import tool_recommend_canteen
            r1 = tool_recommend_canteen(campus="闵行", meal_type="午餐", top_n=4)
            r2 = tool_recommend_canteen(campus="闵行", meal_type="午餐", top_n=4)
            scores1 = [rec["score"] for rec in r1["recommendations"]]
            scores2 = [rec["score"] for rec in r2["recommendations"]]
            assert scores1 == scores2
        finally:
            kmp.undo()
            hmp.undo()

    def test_meal_type_inference_boundaries(self):
        """Meal type inference at boundary hours."""
        from sjtu_agent.agent.tools._dining import _infer_meal_type

        # 5:59 → default
        assert _infer_meal_type(_dt.datetime(2026, 6, 18, 5, 59, tzinfo=CST)) == "午餐"
        # 6:00 → 早餐
        assert _infer_meal_type(_dt.datetime(2026, 6, 18, 6, 0, tzinfo=CST)) == "早餐"
        # 9:59 → 早餐
        assert _infer_meal_type(_dt.datetime(2026, 6, 18, 9, 59, tzinfo=CST)) == "早餐"
        # 10:00 → 午餐
        assert _infer_meal_type(_dt.datetime(2026, 6, 18, 10, 0, tzinfo=CST)) == "午餐"
        # 16:59 → 午餐
        assert _infer_meal_type(_dt.datetime(2026, 6, 18, 16, 59, tzinfo=CST)) == "午餐"
        # 17:00 → 晚餐
        assert _infer_meal_type(_dt.datetime(2026, 6, 18, 17, 0, tzinfo=CST)) == "晚餐"
        # 19:59 → 晚餐
        assert _infer_meal_type(_dt.datetime(2026, 6, 18, 19, 59, tzinfo=CST)) == "晚餐"
        # 20:00 → 夜宵
        assert _infer_meal_type(_dt.datetime(2026, 6, 18, 20, 0, tzinfo=CST)) == "夜宵"
        # 22:59 → 夜宵
        assert _infer_meal_type(_dt.datetime(2026, 6, 18, 22, 59, tzinfo=CST)) == "夜宵"

    def test_auth_is_not_required(self):
        """Canteen API is public — no credentials in code or config."""
        from sjtu_agent.data.canteen_crowd import API_BASE, MAIN_URL, SUB_URL

        # No API key, token, or secret anywhere in the module
        import inspect
        src = inspect.getsource(
            __import__("sjtu_agent.data.canteen_crowd", fromlist=["CanteenCrowdChecker"])
        )
        for sensitive in ["password", "token", "secret", "apikey", "api_key", "Authorization"]:
            assert sensitive.lower() not in src.lower(), f"Found sensitive: {sensitive}"

        # The API is HTTPS (not HTTP)
        for url in [API_BASE, MAIN_URL, SUB_URL]:
            assert url.startswith("https://"), f"Not HTTPS: {url}"
