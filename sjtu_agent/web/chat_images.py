"""Web/TUI 附件：图片走原生多模态，其它文件走文本预解析。

Web GUI 与 TUI 共用同一套 SSE 引擎（`sjtu_agent.web.server._stream_chat`），
所以这里是两条通道唯一的开关点：

- 图片 → 主模型能识图时**直接作为多模态内容发过去**（原生识图），
  不再先经视觉模型/OCR 转成文字；
- 图片 → 主模型不能识图（或事后被端点拒绝）时，退回 `attachment_content()`
  的文本路径（视觉模型 → OCR），行为与以前一致；
- 其它文件（PDF/Word/音频…）→ 一直是文本预解析，与识图无关。
"""

from __future__ import annotations

import base64
from pathlib import Path

from sjtu_agent.vision import image_mime_type

# 历史轮次里不再重发图片字节，但让模型知道「当时有图」并给出本地路径，
# 需要再看得清时可以走 parse_local_file。
_MAX_NOTE_IMAGES = 3


def is_image_attachment(item: dict) -> bool:
    """附件是不是图片（按 mime，不看扩展名——上传接口的 mime 更可信）。"""
    return str(item.get("mime_type") or "").lower().startswith("image/")


def split_attachments(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """拆成（图片, 其它）。"""
    images: list[dict] = []
    others: list[dict] = []
    for item in items or []:
        (images if is_image_attachment(item) else others).append(item)
    return images, others


def build_image_blocks(images: list[dict], proto: str) -> list[dict]:
    """按协议构造图片块；读不出文件的条目跳过（不让一张坏图毁掉整轮）。

    proto: "anthropic" → {"type": "image", "source": {...}}
           "openai"    → {"type": "image_url", "image_url": {"url": data-uri}}
    """
    blocks: list[dict] = []
    for item in images or []:
        path = item.get("path")
        if not path:
            continue
        try:
            data = Path(str(path)).read_bytes()
        except OSError:
            continue
        if not data:
            continue
        mime = image_mime_type(data, default=str(item.get("mime_type") or "image/jpeg"))
        b64 = base64.b64encode(data).decode()
        if proto == "anthropic":
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            })
        else:
            blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
    return blocks


def _image_list(images: list[dict]) -> str:
    names = [
        str(item.get("filename") or "图片") for item in images[:_MAX_NOTE_IMAGES]
    ]
    label = "、".join(names)
    if len(images) > _MAX_NOTE_IMAGES:
        label += f" 等 {len(images)} 张"
    return label


def native_image_note(images: list[dict]) -> str:
    """原生识图那一轮的补充说明（告诉模型图片已附上，不用再找）。"""
    if not images:
        return ""
    paths = [str(item.get("path")) for item in images if item.get("path")][:_MAX_NOTE_IMAGES]
    hint = f"；本地路径：{'、'.join(paths)}" if paths else ""
    return (
        f"\n\n[用户上传了图片（{_image_list(images)}），已随本条消息附上，可直接查看。"
        f"后续轮次不会重复发送图片字节{hint}]"
    )


def text_fallback_note(images: list[dict]) -> str:
    """不能原生识图时的说明：图片内容由视觉模型/OCR 预解析后拼进去。"""
    if not images:
        return ""
    return (
        f"\n\n[该模型不能直接读图，以下 {len(images)} 张图片已由识图/OCR 转成文字："
        f"{_image_list(images)}]"
    )
