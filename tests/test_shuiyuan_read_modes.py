"""read_shuiyuan_topic 的分模式读取（长帖检索 / 抽样 / 追更 / 区间）。

用例基于真实 Discourse 响应结构，并且**按实盘验证后的机制**构造：
- 楼层顺序以主题 JSON 的 `post_stream.stream`（全量 id 表）为准；
- 取楼用 `posts.json?post_ids[]=`（实测 `?post_number=` 的分块语义与 posts_count 不符，
  有删楼时 post_number 会超过 posts_count，按总数取末尾会取到中段）。
"""

from __future__ import annotations

import pytest

from sjtu_agent.agent.tools import _core as core


class FakeResponse:
    def __init__(self, payload, status_code=200, url=""):
        self._payload = payload
        self.status_code = status_code
        self.url = url or "https://shuiyuan.sjtu.edu.cn/"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _post(number, *, cooked=None, wiki=False, accepted=False, replies=0, reads=0, likes=0):
    p = {
        "id": 1000 + number,
        "post_number": number,
        "username": f"user{number}",
        "created_at": "2026-09-01T00:00:00.000Z",
        "cooked": cooked if cooked is not None else f"<p>第 {number} 楼内容</p>",
        "reply_count": replies,
        "reads": reads,
        "wiki": wiki,
        "accepted_answer": accepted,
    }
    if likes:
        p["actions_summary"] = [{"id": 2, "count": likes}]
    return p


def _topic(total=3000, first_posts=None, has_summary=False):
    first = first_posts if first_posts is not None else [_post(1), _post(2), _post(3)]
    head_ids = [p["id"] for p in first]
    rest = [1000 + n for n in range(len(first) + 1, total + 1)]
    return {
        "fancy_title": "关于 Anthropic 的讨论",
        "title": "关于 Anthropic 的讨论",
        "slug": "anthropic",
        "posts_count": total,
        "category_id": 4,
        "views": 12345,
        "has_summary": has_summary,
        "post_stream": {"posts": first, "stream": head_ids + rest},
    }


def _post_ids_of(params) -> list[int]:
    if isinstance(params, list):
        return [int(v) for k, v in params if k == "post_ids[]"]
    return []


def _dispatch(topic, *, search=None, posts_by_id=None, calls=None, search_status=200):
    """按 URL 分发：主题 JSON / search.json / posts.json（post_ids[] 批量）。"""
    store = posts_by_id or {}

    def fake(url, params=None, headers=None, cookies=None, max_retry=3):
        if calls is not None:
            calls.append((url, params))
        if "/search.json" in url:
            if search_status != 200:
                return FakeResponse({}, status_code=search_status, url=url)
            return FakeResponse(search if search is not None else {"posts": [], "topics": []}, url=url)
        if url.endswith("/posts.json"):
            got = [store[i] for i in _post_ids_of(params) if i in store]
            return FakeResponse({"post_stream": {"posts": got}}, url=url)
        return FakeResponse(topic, url=url)

    return fake


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setattr(
        core.dc, "load_config",
        lambda: {"shuiyuan_user_api_key": "k", "shuiyuan_user_api_client_id": "c"},
    )


# ── search：长帖首选 ────────────────────────────────────────────────────────


def test_search_mode_returns_matched_posts(monkeypatch):
    topic = _topic(total=3000)
    hits = [
        {"id": 2800, "topic_id": 471260, "post_number": 1800, "username": "bob",
         "created_at": "2026-08-01T00:00:00.000Z", "blurb": "…提到 Anthropic 封号…"},
        {"id": 2900, "topic_id": 471260, "post_number": 2500, "username": "carol",
         "created_at": "2026-09-01T00:00:00.000Z", "blurb": "…Anthropic 风评…"},
        {"id": 9999, "topic_id": 999999, "post_number": 5, "username": "other",
         "blurb": "别的帖子"},  # 不该混进来
    ]
    store = {
        2800: _post(1800, cooked="<p>Anthropic <b>封号</b>潮</p>", replies=12, reads=9000, likes=44),
        2900: _post(2500, cooked="<p>Anthropic 风评</p>"),
    }
    store[2800]["id"], store[2900]["id"] = 2800, 2900
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(topic, search={"posts": hits}, posts_by_id=store))

    result = core.tool_read_shuiyuan_topic(
        "https://shuiyuan.sjtu.edu.cn/t/anthropic/471260", mode="search", query="Anthropic"
    )

    assert result["mode"] == "search"
    assert result["matched"] == 2                       # 别的帖子被过滤
    assert result["sampled"] is True
    assert [p["post_number"] for p in result["posts"]] == [1800, 2500]
    assert "封号" in result["posts"][0]["content"]       # HTML 转纯文本
    assert result["posts"][0]["like_count"] == 44        # 价值信号保留
    assert result["posts"][0]["reply_count"] == 12
    assert "不要声称读过全文" in result["note"]


def test_search_mode_requires_query(monkeypatch):
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(_topic(total=10)))
    result = core.tool_read_shuiyuan_topic("1", mode="search", query="  ")
    assert "query" in result["error"]


def test_search_mode_no_hits_is_not_an_error(monkeypatch):
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(_topic(total=500), search={"posts": []}))
    result = core.tool_read_shuiyuan_topic("1", mode="search", query="不存在的词")
    assert result["matched"] == 0 and result["posts"] == []
    assert "没有命中" in result["note"]


def test_search_mode_unauthorized_reports_expired(monkeypatch):
    monkeypatch.setattr(
        core, "_shuiyuan_request",
        _dispatch(_topic(total=500), search_status=403),
    )
    result = core.tool_read_shuiyuan_topic("1", mode="search", query="Anthropic")
    assert "未授权" in result["error"] and "配置水源" in result["error"]


# ── auto / summary ─────────────────────────────────────────────────────────


def test_auto_mode_picks_search_when_query_present(monkeypatch):
    calls = []
    store = {1005: _post(5)}
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(
        _topic(total=100),
        search={"posts": [{"id": 1005, "topic_id": 2, "post_number": 5, "username": "u", "blurb": "命中"}]},
        posts_by_id=store, calls=calls,
    ))
    result = core.tool_read_shuiyuan_topic("2", query="Anthropic")
    assert result["mode"] == "search"
    assert any("search.json" in url for url, _ in calls)


def test_summary_mode_composes_main_accepted_and_tail(monkeypatch):
    first = [_post(1, cooked="<p>主楼：汇总索引</p>", wiki=True),
             _post(2, accepted=True, cooked="<p>最佳答案在这里</p>"),
             _post(3)]
    topic = _topic(total=3000, first_posts=first)
    store = {p["id"]: p for p in first}
    for n in range(2996, 3001):                 # 末尾 5 楼
        store[1000 + n] = _post(n)
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(topic, posts_by_id=store))

    result = core.tool_read_shuiyuan_topic("7", mode="summary")
    numbers = [p["post_number"] for p in result["posts"]]

    assert result["mode"] == "summary"
    assert 1 in numbers and 2 in numbers         # 主楼 + 最佳答案
    assert max(numbers) == 3000                  # 真的取到末尾（按 stream 切片，而不是 posts_count）
    assert result["posts"][0]["wiki"] is True
    assert any(p["accepted_answer"] for p in result["posts"])
    assert "抽样" in result["note"]


# ── tail / range ───────────────────────────────────────────────────────────


def test_tail_mode_keeps_latest_posts(monkeypatch):
    topic = _topic(total=3000)
    store = {1000 + n: _post(n) for n in range(2996, 3001)}
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(topic, posts_by_id=store))

    result = core.tool_read_shuiyuan_topic("9", mode="tail", max_posts=5)
    assert [p["post_number"] for p in result["posts"]] == [2996, 2997, 2998, 2999, 3000]


def test_tail_mode_handles_deleted_post_number_gaps(monkeypatch):
    """删楼会让 post_number 超过 posts_count：末尾必须按 stream 定位，而不是按总数。"""
    topic = _topic(total=33)
    topic["post_stream"]["stream"] = [1000 + n for n in (1, 2, 47, 48, 54)]   # 中间大量删楼
    store = {1000 + n: _post(n) for n in (1, 2, 47, 48, 54)}
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(topic, posts_by_id=store))

    result = core.tool_read_shuiyuan_topic("9", mode="tail", max_posts=2)
    assert [p["post_number"] for p in result["posts"]] == [48, 54]


def test_range_mode_walks_the_stream_and_filters(monkeypatch):
    topic = _topic(total=500)
    store = {1000 + n: _post(n) for n in range(1, 41)}
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(topic, posts_by_id=store))

    result = core.tool_read_shuiyuan_topic("11", mode="range", from_post=10, to_post=13)
    assert [p["post_number"] for p in result["posts"]] == [10, 11, 12, 13]


def test_range_mode_locates_by_post_number_when_deletions_shift_the_stream(monkeypatch):
    """删楼错位：楼层号 47/48 在 stream 里位于下标 2/3，二分要能定位。"""
    topic = _topic(total=5)
    topic["post_stream"]["stream"] = [1001, 1002, 1047, 1048, 1054]
    store = {1000 + n: _post(n) for n in (1, 2, 47, 48, 54)}
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(topic, posts_by_id=store))

    result = core.tool_read_shuiyuan_topic("11", mode="range", from_post=47, to_post=48)
    assert [p["post_number"] for p in result["posts"]] == [47, 48]


# ── 预算与结构 ─────────────────────────────────────────────────────────────


def test_budget_truncates_long_posts(monkeypatch):
    long_html = "<p>" + ("很长的楼层正文。" * 400) + "</p>"
    topic = _topic(total=3000)
    store = {1000 + n: _post(n, cooked=long_html) for n in range(2991, 3001)}
    monkeypatch.setattr(core, "_shuiyuan_request", _dispatch(topic, posts_by_id=store))

    result = core.tool_read_shuiyuan_topic("13", mode="tail", max_posts=10)
    assert result["truncated"] is True
    assert len(result["posts"]) < 10                     # 整轮预算拦住后面的楼层
    assert all(len(p["content"]) <= core._READ_POST_CHARS + 20 for p in result["posts"])
    assert result["posts"][0]["content"].endswith("（本楼已截断）")


def test_unconfigured_shuiyuan_returns_guidance(monkeypatch):
    monkeypatch.setattr(core.dc, "load_config", lambda: {})
    result = core.tool_read_shuiyuan_topic("1", mode="summary")
    assert "未配置" in result["error"]


def test_tool_schema_exposes_new_modes():
    entry = next(t for t in core.TOOLS if t["function"]["name"] == "read_shuiyuan_topic")
    props = entry["function"]["parameters"]["properties"]
    assert {"mode", "query", "from_post", "to_post", "max_posts"} <= set(props)
    assert "search" in props["mode"]["enum"]
    assert "抽样" in entry["function"]["description"]
