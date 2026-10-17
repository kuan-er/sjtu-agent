"""教务处检索：伪命中、结构化说明与范围标注（issue #204 复现一）。"""

from __future__ import annotations

import pytest

import ddl_checker as dc


def _rss(*items) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><link>{url}</link>"
        f"<description>{desc}</description>"
        f"<pubDate>Mon, 01 Sep 2026 08:00:00 +0800</pubDate></item>"
        for title, url, desc in items
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<rss version=\"2.0\"><channel>{body}</channel></rss>"
    )
    return xml.encode("utf-8")


class _Resp:
    def __init__(self, content: bytes):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None


@pytest.fixture
def jwc(monkeypatch):
    """把教务处 RSS 换成本地固定样本，且只保留一个 feed。"""
    sample = _rss(
        (
            "关于开设2026年秋季学期游泳技能培训班的通知",
            "https://jwc.sjtu.edu.cn/info/1.htm",
            "游泳技能培训班报名通知",
        ),
        (
            "关于2026级平台能力测试的通知",
            "https://jwc.sjtu.edu.cn/info/2.htm",
            "平台能力测试安排",
        ),
        (
            "2026-2027学年第一学期选课通知",
            "https://jwc.sjtu.edu.cn/info/3.htm",
            "选课时间安排",
        ),
    )
    monkeypatch.setattr(dc, "_JWC_RSS_FEEDS", ["https://jwc.sjtu.edu.cn/rss"])
    monkeypatch.setattr(dc.requests, "get", lambda *a, **k: _Resp(sample))
    return dc


def test_keyword_hit_is_marked_matched(jwc):
    items, note = jwc._search_jwc("游泳技能培训班", max_results=3)
    assert [item["title"] for item in items] == [
        "关于开设2026年秋季学期游泳技能培训班的通知"
    ]
    assert items[0]["matched"] is True
    assert "近期通知" in note and "不是全站" in note


def test_multi_term_query_matches_by_real_terms(jwc):
    """「游泳技能培训班 2026」不该被当成整串子串去匹配。"""
    items, _ = jwc._search_jwc("游泳技能培训班 2026", max_results=3)
    assert items and items[0]["matched"] is True
    assert "2026" in items[0]["title"]


def test_year_terms_rank_but_do_not_gate(jwc):
    """只有年份命中的条目也能出现（标 matched），但排在有实词命中的之后。"""
    items, _ = jwc._search_jwc("选课 2026", max_results=5)
    assert items[0]["title"] == "2026-2027学年第一学期选课通知"
    assert items[0]["matched"] is True


def test_no_match_does_not_impersonate_hits(jwc):
    """不存在的关键词：明确说明未命中，最新通知标 matched=false。"""
    items, note = jwc._search_jwc("zxqnonexistent72941随机无此事项", max_results=3)
    assert items and all(item["matched"] is False for item in items)
    assert "未命中" in note
    assert "不是搜索结果" in note
    # 关键：不能是「假命中」——不能把最新通知说成搜到了
    assert "matched=false" in note


def test_empty_query_lists_latest(jwc):
    items, note = jwc._search_jwc("", max_results=2)
    assert len(items) == 2
    assert all(item["matched"] is False for item in items)
    assert "最新" in note


def test_search_campus_surfaces_the_note(jwc):
    result = jwc.search_campus({}, "zxqnonexistent72941", sites=["jwc"], max_results=2)
    assert "jwc_note" in result
    assert "未命中" in result["jwc_note"]
    assert all(item["matched"] is False for item in result["jwc"])


def test_rss_failure_is_reported(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(dc, "_JWC_RSS_FEEDS", ["https://jwc.sjtu.edu.cn/rss"])
    monkeypatch.setattr(dc.requests, "get", boom)
    items, note = dc._search_jwc("选课", max_results=3)
    assert items and "error" in items[0]
    assert note == ""
