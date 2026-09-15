"""Tests for sjtu_agent/feishu/rendering.py — Markdown to Feishu post conversion."""
import pytest
from sjtu_agent.feishu.rendering import (
    FS_MSG_MAX, has_table, render_table_visual, parse_inline,
    build_post_content, build_card_content,
)


class TestHasTable:
    def test_simple_table(self):
        assert has_table("| a | b |\n|---|---|") is True

    def test_no_table(self):
        assert has_table("just text") is False

    def test_separator_in_middle(self):
        assert has_table("text\n|---|---|\n| a | b |") is True

    def test_separator_first_line(self):
        assert has_table("|---|---|\n| a | b |") is False

    def test_empty_string(self):
        assert has_table("") is False


class TestRenderTableVisual:
    def test_basic_table(self):
        result = render_table_visual("| Name | Value |\n|------|-------|\n| a    | 1     |")
        assert "a" in result
        assert "1" in result

    def test_no_table(self):
        text = "just plain text"
        assert render_table_visual(text) == text

    def test_multiple_rows(self):
        result = render_table_visual("| Canteen | Crowd |\n|---------|-------|\n| 一餐 | 10 |\n| 二餐 | 35 |")
        assert "一餐" in result
        assert "二餐" in result

    def test_multiline_before_table(self):
        result = render_table_visual("Intro\nMore text\n| A | B |\n|---|---|\n| x | y |")
        assert "Intro" in result
        assert "x" in result


class TestParseInline:
    def test_plain_text(self):
        elements = parse_inline("hello world")
        assert len(elements) == 1
        assert elements[0] == {"tag": "text", "text": "hello world"}

    def test_bold(self):
        elements = parse_inline("**hello**")
        assert elements[0] == {"tag": "text", "text": "hello", "style": ["bold"]}

    def test_italic_asterisk(self):
        elements = parse_inline("*hello*")
        assert elements[0] == {"tag": "text", "text": "hello", "style": ["italic"]}

    def test_italic_underscore(self):
        elements = parse_inline("_hello_")
        assert elements[0] == {"tag": "text", "text": "hello", "style": ["italic"]}

    def test_bold_italic(self):
        elements = parse_inline("***hello***")
        assert elements[0] == {"tag": "text", "text": "hello", "style": ["bold", "italic"]}

    def test_code(self):
        elements = parse_inline("`code`")
        assert elements[0] == {"tag": "text", "text": "code"}

    def test_link(self):
        elements = parse_inline("[text](https://example.com)")
        assert elements[0] == {"tag": "a", "text": "text", "href": "https://example.com"}

    def test_mixed_bold_and_text(self):
        elements = parse_inline("hello **world**")
        assert len(elements) == 2
        assert elements[0] == {"tag": "text", "text": "hello "}

    def test_multiple_bold(self):
        elements = parse_inline("**a** and **b**")
        assert len(elements) >= 2

    def test_escape_asterisk(self):
        elements = parse_inline(r"\*not italic\*")
        # \ gets unescaped, *not italic* matches as italic
        texts = "".join(el["text"] for el in elements if el.get("tag") == "text")
        assert "not italic" in texts

    def test_empty_string(self):
        elements = parse_inline("")
        assert elements == []

    def test_text_then_link(self):
        elements = parse_inline("see [link](https://x.com)")
        assert len(elements) == 2


class TestBuildPostContent:
    def test_plain_paragraph(self):
        content = build_post_content("hello world")
        assert len(content) == 1
        assert content[0][0] == {"tag": "text", "text": "hello world"}

    def test_heading(self):
        content = build_post_content("## Title")
        assert content[0][0]["style"] == ["bold"]

    def test_unordered_list(self):
        content = build_post_content("- item")
        assert content[0][0]["text"] == "• "

    def test_ordered_list(self):
        content = build_post_content("1. first")
        assert content[0][0]["text"] == "1. "

    def test_blockquote(self):
        content = build_post_content("> quoted")
        assert content[0][0]["text"] == "quoted"

    def test_separator(self):
        content = build_post_content("---")
        assert content[0][0]["text"] == "—" * 20

    def test_code_block(self):
        content = build_post_content("```\ncode\n```")
        assert len(content) == 1

    def test_multiple_paragraphs(self):
        content = build_post_content("line1\n\nline2")
        assert len(content) == 3  # para 1, empty, para 2

    def test_empty(self):
        content = build_post_content("")
        assert content == [[]]

    def test_unclosed_code_block(self):
        content = build_post_content("```\ncode")
        assert len(content) == 1


class TestBuildCardContent:
    def test_normal(self):
        assert build_card_content("hello") == "hello"

    def test_truncation(self):
        long_text = "x" * 40000
        result = build_card_content(long_text)
        assert len(result) == 30000


class TestFSMsgMax:
    def test_value(self):
        assert FS_MSG_MAX == 4000


# ── 渲染稳定性回归（用户实测：黑体/表格时好时坏） ─────────────────────────────

def _texts_with_style(paragraph):
    return [(el["text"], el.get("style") or []) for el in paragraph if el.get("tag") == "text"]


def test_bold_underscore_style_not_swallowed_by_italic():
    """模型混用 __bold__ 风格时必须渲染为黑体（此前被斜体规则误吞为 _bold_）。"""
    els = parse_inline("__重点__：明天有课")
    texts = _texts_with_style(els)
    assert ("重点", ["bold"]) in texts
    assert not any(t == "重点" and "italic" in s for t, s in texts)


def test_italic_star_and_underscore_still_work():
    assert ("斜", ["italic"]) in _texts_with_style(parse_inline("*斜*体"))
    assert ("斜体", ["italic"]) in _texts_with_style(parse_inline("_斜体_"))


def test_multiplication_not_italicized():
    """乘号星号（空格包裹）不误判为斜体。"""
    els = parse_inline("计算 3 * 4 = 12 与 5 * 6 = 30")
    assert not any("italic" in s for _, s in _texts_with_style(els))


def test_table_two_dash_separator_detected():
    """|--|--| 短分隔线（GFM 允许）此前漏识别导致表格原样漏出。"""
    md = "| A | B |\n|--|--|\n| 1 | 2 |"
    assert has_table(md) is True
    assert "|" not in render_table_visual(md)


def test_table_visual_keeps_content():
    md = "| 名称 | 状态 |\n|---|---|\n| 作业1 | 未交 |"
    visual = render_table_visual(md)
    assert "|" not in visual
    # 设计如此：首列值作为条目名，其余列渲染为 "表头：值"
    assert "作业1" in visual and "状态：未交" in visual
