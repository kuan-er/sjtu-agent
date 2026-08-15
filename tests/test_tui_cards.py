from __future__ import annotations

from sjtu_agent.tui.cards import render_command_result


def test_dining_card_renders_recommendations():
    result = {
        "view": "dining",
        "text": "fallback",
        "data": {
            "ok": True,
            "mode": "recommendation",
            "campus": "闵行",
            "meal_type": "午餐",
            "summary": "今天适合清淡",
            "has_history": True,
            "history_count": 3,
            "recommendations": [
                {
                    "canteen_name": "第一餐饮大楼",
                    "overall_label": "空闲",
                    "overall_rate": 20,
                    "reasons": ["人少"],
                    "recommended_sub_areas": ["面馆", "自选"],
                }
            ],
        },
    }
    markdown = render_command_result(result)
    assert "第一餐饮大楼" in markdown
    assert "面馆" in markdown
    assert "历史记录" in markdown
    assert "fallback" not in markdown


def test_news_card_renders_links_and_meta():
    result = {
        "view": "news",
        "text": "fallback",
        "data": {
            "items": [
                {
                    "title": "教务通知",
                    "url": "https://example.com",
                    "source": "jwc",
                    "category": "选课",
                    "reason": "与你相关",
                    "summary": "摘要内容",
                }
            ]
        },
    }
    markdown = render_command_result(result)
    assert "[教务通知](https://example.com)" in markdown
    assert "教务处" in markdown
    assert "选课" in markdown
    assert "与你相关" in markdown


def test_homework_card_uses_past_command_for_past_list():
    result = {
        "view": "homework",
        "text": "fallback",
        "data": {
            "ok": True,
            "kind": "past",
            "assignments": [
                {"index": 1, "course": "数学分析", "name": "作业一", "due": "2026-08-01", "submitted": True}
            ],
        },
    }
    markdown = render_command_result(result)
    assert "数学分析" in markdown
    assert "/hw past do N" in markdown


def test_template_list_card():
    result = {
        "view": "template_list",
        "text": "fallback",
        "data": {"templates": [{"name": "bachelor-thesis", "description": "毕业论文", "source": "builtin"}]},
    }
    markdown = render_command_result(result)
    assert "bachelor-thesis" in markdown
    assert "📦" in markdown


def test_unknown_view_falls_back_to_text():
    assert render_command_result({"view": "markdown", "text": "**hello**"}) == "**hello**"
