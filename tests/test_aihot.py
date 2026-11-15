"""AIHOT v1 contract and both consumers, without sending real Feishu messages."""

import io
import json
import sys
import urllib.error
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest

from scripts import aihot_push


@pytest.fixture
def items():
    return [{
        "title": "模型发布", "category": "ai-models",
        "publishedAt": "2026-01-02T03:04:05Z",
        "links": {"original": "https://example.com/article", "aihot": "https://aihot.news/items/1"},
        "summary": "摘要",
    }, {
        "title": "观点文章", "category": "opinion",
        "publishedAt": "2026-01-02T03:04:05Z",
        "links": {"aihot": "https://aihot.news/items/2"},
    }, {
        "title": "新分类", "category": "future-category",
        "publishedAt": "2026-01-02T03:04:05Z", "links": None,
    }]


@pytest.fixture
def api(monkeypatch, items):
    def response(request, timeout):
        url = urlparse(request.full_url)
        assert (url.scheme, url.netloc, url.path) == ("https", "aihot.news", "/api/v1/items")
        assert parse_qs(url.query) == {"mode": ["selected"], "window": ["24h"], "limit": ["30"]}
        assert timeout == 30
        return io.BytesIO(json.dumps({"schemaVersion": 1, "items": items}).encode())
    mocked = Mock(side_effect=response)
    monkeypatch.setattr("urllib.request.urlopen", mocked)
    return mocked


def test_fetch_v1(api, items):
    assert aihot_push._fetch_items() == items
    api.assert_called_once()


@pytest.mark.parametrize("payload", [{}, []])
def test_fetch_empty(monkeypatch, payload):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    assert aihot_push._fetch_items() == []


@pytest.mark.parametrize("error", [urllib.error.HTTPError("https://aihot.news", 503, "Unavailable", {}, None), urllib.error.URLError("offline")])
def test_fetch_failure(monkeypatch, error):
    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=error))
    assert aihot_push._fetch_items() == []


def test_render_v1_fields_and_all_categories(monkeypatch, items):
    fmt = Mock(return_value="时间")
    monkeypatch.setattr(aihot_push, "_fmt_time", fmt)
    md = aihot_push._build_markdown(items)
    assert "1. [模型发布](https://example.com/article) · 时间 — 摘要" in md
    assert "2. [观点文章](https://aihot.news/items/2)" in md
    assert "[观点]" in md
    assert "[其他]" in md
    assert "3. 新分类" in md
    assert "共 3 条" in md
    assert "[aihot.news](https://aihot.news)" in md
    assert "()" not in md
    assert all(call.args == ("2026-01-02T03:04:05Z",) for call in fmt.call_args_list)


@pytest.mark.parametrize("category", [None, "", "unrecognized"])
def test_missing_or_unknown_category_kept(category):
    md = aihot_push._build_markdown([{"title": "保留", "category": category}])
    assert "[其他]" in md and "1. 保留" in md and "共 1 条" in md


def test_empty_render():
    assert aihot_push._build_markdown([]) == "（暂无 AI 资讯）"


def test_cli_dry_run(api, monkeypatch, capsys):
    send = Mock()
    monkeypatch.setattr("sjtu_agent.feishu_client.send_post_message", send)
    monkeypatch.setattr(sys, "argv", ["aihot_push.py", "--test"])
    aihot_push.main()
    assert "共 3 条" in capsys.readouterr().out
    send.assert_not_called()


def test_scheduled_push(api, monkeypatch):
    send = Mock(return_value=True)
    monkeypatch.setattr("sjtu_agent.feishu_client.send_post_message", send)
    monkeypatch.setattr("sjtu_agent.config.cfg.reload_if_changed", lambda: None)
    monkeypatch.setattr("sjtu_agent.config.cfg.get", lambda key, default: "test-open-id")
    monkeypatch.setattr(sys, "argv", ["aihot_push.py"])
    aihot_push.main()
    send.assert_called_once()
    recipient, paragraphs = send.call_args.args
    assert recipient == "test-open-id"
    elements = [element for paragraph in paragraphs for element in paragraph]
    assert {e["href"] for e in elements if e["tag"] == "a"} >= {
        "https://example.com/article", "https://aihot.news/items/2",
    }
    assert any("3. 新分类" in e.get("text", "") for e in elements)


def test_slash_command(api):
    from scripts.feishu_bot import _fetch_aihot_news
    md = _fetch_aihot_news()
    assert "共 3 条" in md
    assert "https://example.com/article" in md
    assert "https://aihot.news/items/2" in md
    assert "3. 新分类" in md
    api.assert_called_once()


def test_slash_command_empty(monkeypatch):
    from scripts.feishu_bot import _fetch_aihot_news
    monkeypatch.setattr(aihot_push, "_fetch_items", lambda **kwargs: [])
    assert "暂无 AI 资讯" in _fetch_aihot_news()
