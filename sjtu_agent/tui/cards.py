"""
sjtu_agent/tui/cards.py — 把共享命令结果渲染成终端 Markdown 卡片。

Web GUI 用 HTML 卡片渲染 {view, text, data}；TUI 用同一份数据生成
适合 Textual Markdown widget 的 Markdown 布局。
"""

from __future__ import annotations

from typing import Any


def _dining(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    text = result.get("text") or ""
    if not data.get("ok"):
        return text

    mode = data.get("mode", "recommendation")
    campus = data.get("campus", "")
    if mode == "crowd":
        lines = [f"## 🍽️ 食堂拥挤度 · {campus} 校区", ""]
        for canteen in data.get("canteens", []):
            lines.append(
                f"- **{canteen.get('name', '')}** — "
                f"{canteen.get('overall_label', '')}（{canteen.get('overall_rate', '')}%）"
            )
        return "\n".join(lines)

    lines = [
        f"## 🍽️ {data.get('meal_type', '')}推荐 · {campus} 校区",
        "",
        str(data.get("summary", "")).strip(),
        "",
    ]
    for rank, rec in enumerate(data.get("recommendations", []), start=1):
        lines.append(
            f"### {rank}. {rec.get('canteen_name', '')} "
            f"{rec.get('overall_label', '')}（{rec.get('overall_rate', '')}%）"
        )
        for reason in rec.get("reasons", []):
            lines.append(f"- {reason}")
        areas = rec.get("recommended_sub_areas") or []
        if areas:
            lines.append(f"- 推荐窗口：{'、'.join(areas[:3])}")
        lines.append("")

    if data.get("has_history"):
        lines.append(f"_基于 {data.get('history_count', 0)} 条历史记录，推荐会越来越准_")
    return "\n".join(lines).strip()


def _news(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    items = data.get("items") or []
    if not items:
        return result.get("text") or ""

    source_labels = {"jwc": "教务处", "shuiyuan": "水源社区", "official": "交大新闻网", "canvas": "Canvas"}
    lines = [f"## 📰 校园新闻 · {len(items)} 条", ""]
    for item in items:
        title = item.get("title", "未命名")
        url = item.get("url", "")
        heading = f"[{title}]({url})" if url else title
        lines.append(f"### {heading}")
        source = source_labels.get(item.get("source", ""), item.get("source", ""))
        meta = source
        if item.get("category"):
            meta += f" · {item['category']}"
        if item.get("reason"):
            meta += f" · {item['reason']}"
        if meta:
            lines.append(f"**{meta}**")
        if item.get("summary"):
            lines.append(str(item["summary"]))
        lines.append("")
    return "\n".join(lines).strip()


def _homework(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    assignments = data.get("assignments") or []
    kind = data.get("kind", "list")
    if not assignments:
        return result.get("text") or ""

    lines = [f"## 📝 作业列表 · {len(assignments)} 项", ""]
    for item in assignments:
        due = item.get("due") or "未知"
        status = "已提交" if item.get("submitted") else "未提交"
        days = item.get("days_left")
        if days is not None:
            if days > 0:
                due += f"（{days} 天后）"
            elif days == 0:
                due += "（今天）"
            else:
                due += "（已截止）"
        lines.append(
            f"### [{item.get('index')}] {item.get('course', '')} — {item.get('name', '')}"
        )
        lines.append(f"- 截止：{due} · {status}")
        lines.append("")

    command = "/hw past do N" if kind in {"past", "all"} else "/hw do N"
    lines.append(f"_在输入框输入 `{command}`（把 N 换成序号）即可分析对应作业_")
    return "\n".join(lines).strip()


def _template_list(data: dict[str, Any], text: str) -> str:
    templates = data.get("templates") or []
    if not templates:
        return text
    lines = ["## 📚 可用 LaTeX 模板", ""]
    for template in templates:
        source = "📦 内置" if template.get("source") == "builtin" else "📥 用户"
        lines.append(f"- **{template.get('name', '')}** {source}")
        lines.append(f"  {template.get('description', '')}")
    lines.extend(["", "_输入 `/template <名称>` 套用模板_"])
    return "\n".join(lines)


def render_command_result(result: dict[str, Any]) -> str:
    """把 {view,text,data} 渲染为终端 Markdown。"""
    view = result.get("view") or "markdown"
    data = result.get("data") or {}
    text = result.get("text") or ""

    if view == "dining":
        return _dining(result)
    if view == "news":
        return _news(result)
    if view == "homework":
        return _homework(result)
    if view == "template_list":
        return _template_list(data, text)
    if view == "template_compile":
        if data.get("ok"):
            return f"## ✅ LaTeX 编译成功\n\nPDF：`{data.get('pdf', '')}`（{data.get('size_kb', 0)} KB）"
        return text
    if view == "template_clone":
        if data.get("ok"):
            return f"## ✅ 模板已克隆\n\n名称：`{data.get('name', '')}`\n\n`/template {data.get('name', '')}` 即可套用。"
        return text
    if view == "template_apply":
        if data.get("ok"):
            return f"## ✅ 已套用模板\n\n`{data.get('name', '')}`\n\n把你的文档放进去，然后 `/template compile` 编译。"
        return text
    if view == "template_push":
        if data.get("ok"):
            return f"## ✅ 已推送到 Overleaf\n\n{data.get('message', '')}"
        return text
    if view == "news_preference":
        if data.get("ok"):
            if data.get("category"):
                return f"## ✅ 已屏蔽新闻分类\n\n`{data.get('category')}`"
            return f"## ✅ 新闻偏好已更新\n\n{text}"
        return text
    return text
