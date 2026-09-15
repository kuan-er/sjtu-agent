"""sjtu_agent/feishu/rendering.py — Markdown to Feishu post format conversion.

Pure functions with no side effects. Used by feishu_bot.py for rendering
replies, cards, and proactive messages.
"""
from __future__ import annotations

import re

# ── constants ──────────────────────────────────────────────────────────────

FS_MSG_MAX = 4000

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_BOLD_ITALIC_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__")
# 斜体：星号分支要求内容首尾非空白（防 "3 * 4" 乘号误判）；下划线分支要求
# 开头前面不是 _（防 "__bold__" 被匹配成 "_bold_" 斜体——模型两种黑体风格
# 都会用，此前下划线黑体渲染不稳定就是这个原因）
_MD_ITALIC_RE = re.compile(
    r"(?<!\*)\*([^\s*](?:[^*\n]*[^\s*])?)\*(?!\*)"
    r"|(?<!_)_([^_\n]+?)_(?!_)"
)
_MD_CODE_RE = re.compile(r"`([^`\n]+?)`")
# 分隔行接受 1+ 个连字符（GFM 允许 |--|--|，此前 {3,} 会漏掉短分隔线）
_MD_TABLE_SEP_RE = re.compile(r"^\|?\s*[-:]+\s*\|\s*[-:]+\s*(\|\s*[-:]+\s*)*\|?\s*$")

# Feishu post element types
_PostElement = dict  # {"tag": "text"|"a", "text": str, ...}
_PostParagraph = list  # list[_PostElement]
_PostContent = list  # list[_PostParagraph]


# ── element helpers ────────────────────────────────────────────────────────

def _el_text(text: str, style: list | None = None) -> _PostElement:
    el: _PostElement = {"tag": "text", "text": text}
    if style:
        el["style"] = style
    return el


def _el_link(text: str, href: str) -> _PostElement:
    return {"tag": "a", "text": text, "href": href}


def _unescape_md(text: str) -> str:
    return text.replace("\\*", "*").replace("\\`", "`").replace("\\[", "[")


# ── table detection / rendering ───────────────────────────────────────────

def has_table(md_text: str) -> bool:
    lines = md_text.strip().split("\n")
    for i, line in enumerate(lines):
        if i > 0 and _MD_TABLE_SEP_RE.match(line.strip()):
            return True
    return False


def render_table_visual(md_text: str) -> str:
    """Convert Markdown table to list format (Feishu proportional fonts break box-drawing)."""
    NL = chr(10)
    lines = [l for l in md_text.strip().split(NL)]
    table_start = -1
    for i, line in enumerate(lines):
        if _MD_TABLE_SEP_RE.match(line.strip()):
            table_start = i - 1
            break
    if table_start < 0:
        return md_text
    table_end = len(lines) - 1
    for i in range(table_start + 2, len(lines)):
        stripped = lines[i].strip()
        if not (stripped.startswith(chr(124)) and chr(124) in stripped[1:]):
            table_end = i - 1
            break

    def parse_row(row):
        return [c.strip() for c in row.strip().strip(chr(124)).split(chr(124))]

    header = parse_row(lines[table_start])
    data_rows = []
    for i in range(table_start + 2, table_end + 1):
        if lines[i].strip():
            data_rows.append(parse_row(lines[i]))
    if not header or not data_rows:
        return md_text

    items = []
    for row in data_rows:
        title = row[0] if row else ""
        lines_item = [title]
        for j in range(1, min(len(header), len(row))):
            if row[j]:
                lines_item.append("  " + str(header[j]) + chr(65306) + str(row[j]))
        items.append(NL.join(lines_item))
    visual = (NL + NL).join(items)
    before = NL.join(lines[:table_start])
    after = NL.join(lines[table_end + 1:]) if table_end + 1 < len(lines) else ""
    result = (before + NL if before else "") + visual
    if after:
        result += NL + after
    return result


# ── inline parsing ─────────────────────────────────────────────────────────

def parse_inline(text: str) -> _PostParagraph:
    """Parse a single line of Markdown into Feishu post elements."""
    elements: _PostParagraph = []
    remaining = text

    while remaining:
        bold_italic_m = _MD_BOLD_ITALIC_RE.search(remaining)
        bold_m = _MD_BOLD_RE.search(remaining)
        bold_under_m = _MD_BOLD_UNDERSCORE_RE.search(remaining)
        italic_m = _MD_ITALIC_RE.search(remaining)
        code_m = _MD_CODE_RE.search(remaining)
        link_m = _MD_LINK_RE.search(remaining)

        candidates = []
        if bold_italic_m: candidates.append((bold_italic_m.start(), bold_italic_m, "bold_italic"))
        if bold_m: candidates.append((bold_m.start(), bold_m, "bold"))
        if bold_under_m: candidates.append((bold_under_m.start(), bold_under_m, "bold"))
        if italic_m: candidates.append((italic_m.start(), italic_m, "italic"))
        if code_m: candidates.append((code_m.start(), code_m, "code"))
        if link_m: candidates.append((link_m.start(), link_m, "link"))

        if not candidates:
            txt = _unescape_md(remaining)
            if txt:
                elements.append(_el_text(txt))
            break

        candidates.sort(key=lambda x: x[0])
        first_start, first_match, first_type = candidates[0]

        if first_start > 0:
            prefix = _unescape_md(remaining[:first_start])
            if prefix:
                elements.append(_el_text(prefix))

        if first_type == "bold_italic":
            elements.append(_el_text(first_match.group(1), ["bold", "italic"]))
            remaining = remaining[first_match.end():]
        elif first_type == "bold":
            elements.append(_el_text(first_match.group(1), ["bold"]))
            remaining = remaining[first_match.end():]
        elif first_type == "italic":
            txt = first_match.group(1) or first_match.group(2)
            elements.append(_el_text(txt, ["italic"]))
            remaining = remaining[first_match.end():]
        elif first_type == "code":
            elements.append(_el_text(first_match.group(1)))
            remaining = remaining[first_match.end():]
        elif first_type == "link":
            elements.append(_el_link(first_match.group(1), first_match.group(2)))
            remaining = remaining[first_match.end():]

    # merge adjacent same-style text elements
    merged: _PostParagraph = []
    for el in elements:
        if (merged and el.get("tag") == "text" and merged[-1].get("tag") == "text"
                and el.get("style") == merged[-1].get("style")
                and "href" not in el):
            merged[-1]["text"] += el["text"]
        else:
            merged.append(el)
    return merged


# ── block-level conversion ─────────────────────────────────────────────────

def build_post_content(md_text: str) -> _PostContent:
    """Convert Markdown text to Feishu post content (2D paragraph array)."""
    paragraphs: _PostContent = []
    lines = md_text.strip().split("\n")
    in_code_block = False
    code_buf: list[str] = []

    def _flush_code_block():
        nonlocal code_buf
        if code_buf:
            code_text = "\n".join(code_buf)
            paragraphs.append([_el_text(code_text)])
            code_buf = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                _flush_code_block()
                in_code_block = False
            else:
                in_code_block = True
                code_buf = []
            continue
        if in_code_block:
            code_buf.append(line)
            continue
        if not stripped:
            paragraphs.append([])
            continue

        header_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if header_match:
            text = header_match.group(2)
            h_elements = parse_inline(text)
            for el in h_elements:
                if el.get("tag") == "text":
                    el["style"] = (el.get("style") or []) + ["bold"]
            paragraphs.append(h_elements)
            continue

        list_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if list_match:
            li_elements = parse_inline(list_match.group(1))
            paragraphs.append([_el_text("• ")] + li_elements)
            continue

        ol_match = re.match(r"^(\d+\.)\s+(.+)$", stripped)
        if ol_match:
            prefix = ol_match.group(1) + " "
            ol_elements = parse_inline(ol_match.group(2))
            paragraphs.append([_el_text(prefix)] + ol_elements)
            continue

        if stripped.startswith(">"):
            text = stripped.lstrip("> ").lstrip(">")
            paragraphs.append([_el_text(text)])
            continue

        if stripped in ("---", "***", "___"):
            paragraphs.append([_el_text("—" * 20)])
            continue

        elements = parse_inline(stripped)
        paragraphs.append(elements)

    _flush_code_block()
    return paragraphs


# ── card content ───────────────────────────────────────────────────────────

def build_card_content(md_text: str) -> str:
    """Truncate markdown for Feishu interactive card (30k char limit)."""
    return md_text[:30000]
