"""Tests for sjtu_agent/timeutils.py — 校园时间（北京时间）vs 用户本地时间。

覆盖：固定校园时区、user_timezone 配置覆盖、系统时区回退、双时区标签。
"""

import datetime as _dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from sjtu_agent import timeutils as tu


@pytest.fixture
def tz_config(monkeypatch):
    """注入 user_timezone 配置的钩子。"""
    holder = {}

    def _fake_read_config():
        return dict(holder)

    monkeypatch.setattr(tu, "_read_config", _fake_read_config)
    return holder


def test_school_tz_is_utc8():
    now = tu.school_now()
    assert now.utcoffset() == _dt.timedelta(hours=8)
    assert now.tzname() in ("CST", "Asia/Shanghai", "+08", "+0800", "China Standard Time")


def test_user_tz_respects_config_override(tz_config):
    tz_config["user_timezone"] = "America/New_York"
    assert tu.user_tz_name() == "America/New_York"
    assert tu.user_now().utcoffset() in (
        _dt.timedelta(hours=-4),  # EDT
        _dt.timedelta(hours=-5),  # EST
    )


def test_user_tz_invalid_config_falls_back(tz_config):
    tz_config["user_timezone"] = "Not/AZone"
    tu.user_tz_name()  # 不抛异常
    assert tu.user_now() is not None


def test_dual_label_same_zone_single(tz_config):
    tz_config["user_timezone"] = "Asia/Shanghai"
    now = tu.school_now()
    label = tu.dual_time_label(now)
    assert label.startswith("北京时间")
    assert "你那边" not in label


def test_dual_label_different_zone_shows_both(tz_config):
    tz_config["user_timezone"] = "America/New_York"
    # 选一个北京=上午、纽约=前一晚的时刻，保证日历不同
    now = _dt.datetime(2026, 9, 14, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    label = tu.dual_time_label(now)
    assert "9月14日" in label and "北京时间" in label
    assert "你那边" in label and "9月13日" in label


def test_differs_locally_boundary(tz_config):
    tz_config["user_timezone"] = "Asia/Shanghai"
    now = tu.school_now()
    assert tu.differs_locally(now) is False


def test_daily_report_header_abroad_beijing_primary(monkeypatch):
    """异地日报头行：北京日期为主（触发与晨/午/晚标签均已锚定北京时钟），
    用户当地时刻作参考注记。

    语义演进：触发时刻在调度器安装时换算为本地等价（北京 8/12/22 → 本地
    对应时刻），标签与内容重新同锚北京时钟，因此头行以北京日期为准。
    """
    from scripts import daily_report as dr
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(tu, "_read_config", lambda: {"user_timezone": "America/Los_Angeles"})
    # 北京 09-14（周一）13:00 = PDT 09-13（周日）22:00
    now = _dt.datetime(2026, 9, 14, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    header, abroad = dr._build_date_header(now)

    assert abroad is True
    assert header.startswith("2026年09月14日（星期一）")
    assert "你当地" in header and "9月13日" in header


def test_daily_report_header_home_single_timezone(monkeypatch):
    from scripts import daily_report as dr

    monkeypatch.setattr(tu, "_read_config", lambda: {"user_timezone": "Asia/Shanghai"})
    now = _dt.datetime(2026, 9, 14, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    header, abroad = dr._build_date_header(now)

    assert abroad is False
    assert header == "2026年09月14日（星期一）"
    assert "你当地" not in header


def test_campus_schedule_local_converts_for_abroad(monkeypatch):
    """日报触发时刻按北京时钟：换算到机器本地时区（测试注入 PDT）。"""
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(tu, "_read_config", lambda: {"user_timezone": "America/Los_Angeles"})
    # 机器系统时区注入为 PDT（CI 机器是 UTC，必须显式控制）
    monkeypatch.setattr(tu, "local_tz", lambda: ZoneInfo("America/Los_Angeles"))
    assert tu.campus_schedule_local(8, 0) == (17, 0)
    assert tu.campus_schedule_local(22, 30) == (7, 30)
    assert tu.campus_schedule_local(12, 0) == (21, 0)


def test_campus_schedule_local_uses_system_zone(monkeypatch):
    """UTC 机器（如 CI）：北京 08:00 → UTC 00:00，跟随系统时区而非写死。"""
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(tu, "_read_config", lambda: {"user_timezone": "America/Los_Angeles"})
    monkeypatch.setattr(tu, "local_tz", lambda: ZoneInfo("UTC"))
    assert tu.campus_schedule_local(8, 0) == (0, 0)


def test_campus_schedule_local_identity_in_china(monkeypatch):
    monkeypatch.setattr(tu, "_read_config", lambda: {"user_timezone": "Asia/Shanghai"})
    assert tu.campus_schedule_local(8, 0) == (8, 0)
    assert tu.campus_schedule_local(22, 0) == (22, 0)


# ── 日报"翻旧账"治理：记忆新鲜度 + 过期 DDL 过滤 ────────────────────────────

def test_care_suggestions_drop_stale_memories(monkeypatch):
    """几周前的旧记忆不再进日报行动建议（用户实测'塞奇奇怪怪的东西一直留着'）。"""
    from sjtu_agent import memory

    old_ts = (memory.datetime.now(memory.CST) - memory.timedelta(days=20)).isoformat()
    new_ts = (memory.datetime.now(memory.CST) - memory.timedelta(days=2)).isoformat()
    monkeypatch.setattr(
        memory, "search_memory",
        lambda uid, q, n=10: [
            {"text": "嵌入式小资源合集，以后轻松看看", "metadata": {"timestamp": old_ts}},
            {"text": "下周要准备操作系统期中考试", "metadata": {"timestamp": new_ts}},
        ],
    )
    out = memory.get_care_suggestions("u1")
    assert out is not None
    assert "操作系统" in out
    assert "嵌入式" not in out


def test_care_suggestions_none_when_all_stale(monkeypatch):
    from sjtu_agent import memory

    old_ts = (memory.datetime.now(memory.CST) - memory.timedelta(days=30)).isoformat()
    monkeypatch.setattr(
        memory, "search_memory",
        lambda uid, q, n=10: [{"text": "备考线性代数", "metadata": {"timestamp": old_ts}}],
    )
    assert memory.get_care_suggestions("u1") is None


def test_daily_report_filters_expired_ddls_from_context():
    """过期 DDL 不再进 AI 上下文（旧作业被行动建议反复翻出来讲）。"""
    from scripts import daily_report as dr

    ddls = [
        {"name": "被腰斩的大作业", "expired": True, "hours_left": -100},
        {"name": "有效作业", "expired": False, "hours_left": 30},
    ]
    active = dr._active_ddls(ddls)
    assert [d["name"] for d in active] == ["有效作业"]


def test_care_note_instructs_no_forced_mentions(monkeypatch):
    from scripts import daily_report as dr

    monkeypatch.setattr(dr._cfg, "raw", lambda: {"feishu_open_id": "ou_x"})
    monkeypatch.setattr(
        "sjtu_agent.memory.get_care_suggestions",
        lambda uid: "基于你近期的关注：准备操作系统期中考试",
    )
    note = dr._build_care_note()
    assert "非必用" in note
    assert "不要" in note and ("编造" in note or "没出现在上下文" in note)


# ── 日报生成韧性：推理模型烧穿 max_tokens 导致空回复（09-15 晚报实测） ────────

class _FakeOpenAIClient:
    """content 按预设序列返回，记录每次调用的 kwargs。"""

    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = []

        def create(**kwargs):
            self.calls.append(kwargs)
            content = self._contents.pop(0) if self._contents else ""
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="length",
            )])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_llm_generate_no_max_tokens_and_recovers(monkeypatch):
    """OpenAI 路径不设 max_tokens（推理模型思考会烧穿预算）；首轮空回复后重试成功。"""
    from scripts import daily_report as dr

    client = _FakeOpenAIClient(["", "📊 日报内容"])
    text, reason = dr._llm_generate(client, "deepseek-flash", "prompt")
    assert text == "📊 日报内容"
    assert reason == "length"
    assert len(client.calls) == 2
    assert all("max_tokens" not in kw for kw in client.calls)  # 关键回归点


def test_llm_generate_all_empty_returns_empty_for_fallback():
    from scripts import daily_report as dr

    client = _FakeOpenAIClient(["", ""])
    text, reason = dr._llm_generate(client, "deepseek-flash", "prompt")
    assert text == "" and reason == "length"
    assert len(client.calls) == 2  # 恰好重试一次，不死循环


def test_llm_generate_strips_think_blocks():
    from scripts import daily_report as dr

    client = _FakeOpenAIClient(["<think>推理过程</think>正文"])
    text, _ = dr._llm_generate(client, "deepseek-chat", "prompt")
    assert text == "正文"
