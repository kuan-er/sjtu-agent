"""Standalone vision model for image analysis.

独立于主对话模型：主模型（纯文本逻辑）不支持视觉时，用这里配置的
视觉模型（如 qwen-vl-max）识图。仅做一次性识图，不进入对话历史。
"""

import base64
import json

from sjtu_agent.paths import AGENT_CONFIG_PATH

# 惰性导入占位：_make_client 在首次真实调用时才从 sjtu_agent.agent 导入。
# 模块级占位（None）让测试可以 monkeypatch.setattr 覆盖，从而用 fake client 验 payload。
_make_client = None


def load_vision_config() -> dict | None:
    """读 agent_config.json 的 vision_model；未启用返回 None。

    返回 {"base_url", "api_key", "model"}。api_key 绝不进入任何打印。
    """
    try:
        if AGENT_CONFIG_PATH.exists():
            cfg = json.loads(AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
            vm = cfg.get("vision_model")
            if vm and vm.get("enabled") and vm.get("api_key") and vm.get("model"):
                return {
                    "base_url": vm.get("base_url"),
                    "api_key": vm["api_key"],
                    "model": vm["model"],
                }
    except Exception:
        pass
    return None


_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def image_mime_type(data: bytes, default: str = "image/jpeg") -> str:
    """按文件头判断图片真实类型。

    之前各 bot 一律把图片标成 `image/jpeg`：OpenAI 兼容端多数不在意，但
    Anthropic 会直接 400（"media_type does not match"），于是明明是原生视觉
    模型，微信/QQ 发来的 PNG 却识图失败。
    """
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in _IMAGE_SIGNATURES:
        if data.startswith(magic):
            return mime
    return default


def analyze_image(image_bytes: bytes, prompt: str = "") -> str:
    """用视觉模型识图，返回描述文本。失败抛异常。

    使用 OpenAI 兼容格式（image_url base64）。视觉模型只用于本次识图，
    不参与主对话历史。
    """
    global _make_client
    if _make_client is None:
        from sjtu_agent.agent import _make_client

    cfg = load_vision_config()
    if not cfg:
        raise RuntimeError("视觉模型未配置（agent_config.json 的 vision_model）")

    client = _make_client(cfg)
    b64 = base64.b64encode(image_bytes).decode()
    mime = image_mime_type(image_bytes)
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt or "请描述这张图片的内容，并提取其中的文字。"},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }]
    resp = client.chat.completions.create(
        model=cfg["model"], messages=messages, max_tokens=1024,
    )
    return resp.choices[0].message.content or ""
