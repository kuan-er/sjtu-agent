"""
sjtu_agent/web/attachment_context.py — 附件预解析与上下文注入（Web / TUI 共享）。

附件必须先复制进 web_attachments/ 目录再使用；这里只负责把已保存附件
预解析成文本并拼进用户消息，避免主 Agent 调用 parse_local_file 触发越权或
OCR 安装询问。
"""

from __future__ import annotations

from pathlib import Path


def attachment_content(item: dict) -> str:
    """复用飞书 Bot 的媒体解析能力，提前提取附件内容。

    图片优先走独立视觉模型，其次走 OCR；其他文件走 parse_file 统一解析。
    避免让主 Agent 再调用 parse_local_file 并触发 OCR 安装询问。
    """
    path = Path(item["path"])
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"（附件读取失败：{exc}）"

    mime_type = str(item.get("mime_type") or "")
    if mime_type.startswith("image/"):
        try:
            from sjtu_agent.vision import analyze_image, load_vision_config
            if load_vision_config() is not None:
                return analyze_image(data, "请描述这张图片并提取其中的文字。")
        except Exception:
            pass

    try:
        from sjtu_agent.parsing import parse_file
        parsed = parse_file(str(path), max_chars=8000, strategy="auto")
        if parsed.get("ok") and str(parsed.get("content") or "").strip():
            return str(parsed.get("content"))
        return "（未能自动提取该附件内容）"
    except Exception as exc:
        return f"（附件解析失败：{exc}）"


def attachment_note_for_chat(items: list[dict]) -> str:
    if not items:
        return ""
    lines = ["\n\n[用户通过 Web 上传了附件，内容已预解析]"]
    for item in items:
        lines.append(
            f"\n附件：{item['filename']}（type={item.get('mime_type')}, size={item.get('size')}B）"
            f"\n解析结果：{attachment_content(item)}"
        )
    return "\n".join(lines)


# 兼容旧调用点与测试的私有别名
_attachment_content = attachment_content
_attachment_note_for_chat = attachment_note_for_chat
