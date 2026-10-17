"""识图能力判定（issue #203）。

原来 `model_supports_vision()` 用关键词白名单枚举「支持视觉的模型」，新模型
（如 GPT-6.1-Sol）不在名单里就被判成不能识图 → 跳过主模型、直接落到视觉模型 /
OCR。白名单永远追不上新模型，所以改成三级判定：

1. **显式配置**（用户最权威）：`agent_config.json` 里 `vision_capable` 布尔值，或
   `vision_unsupported_models` 名单；
2. **已知清单**：沿用原有关键词（`deepseek-flash` / `claude-*` / `gpt-4o` …）；
3. **行为探测**：发一张 1×1 像素图 + `max_tokens=1`，按返回错误判断是否接受图片。
   结论缓存到运行时目录，同一个模型只探测一次。

探测只做「能力探测」，不读用户图片：1×1 PNG 的成本可以忽略。
"""

from __future__ import annotations

import base64
import json
import os
import re

from sjtu_agent.logging import get_logger

_logger = get_logger("vision_capability")

# 已知支持图片输入的模型关键词（快路径，不认识就交给探测）
# 故意保持保守：只放**确认过**支持图片的型号，别猜厂商前缀——猜错的代价正是
# issue #203（新模型被当成不能识图，直接掉到 OCR）。
_KNOWN_VISION_KEYWORDS = (
    "vision", "gpt-4o", "gpt-4-turbo", "claude-3", "claude-4",
    "gemini", "qwen-vl", "qwen3vl", "glm-4v", "internvl",
    "sonnet-4", "opus-4", "haiku-4",
    "deepseek-flash",  # V4.1 Flash（官方 API）原生多模态
)

# 已知不支持图片输入（文本模型），省掉一次探测
_KNOWN_TEXT_ONLY_KEYWORDS = (
    "deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro",
    "gpt-3.5", "text-davinci",
)

# 服务端拒收图片时的典型措辞（OpenAI / Anthropic / 网关通用）
_REJECTION_PATTERNS = (
    r"not\s+support\s+(?:image|vision|multi[- ]?modal)",
    r"image(?:s)?\s+(?:input\s+)?(?:is\s+)?not\s+support",
    r"does\s+not\s+support\s+(?:image|vision)",
    r"unsupported\s+(?:content|image|media)",
    r"invalid\s+(?:content|image)",
    r"multi[- ]?modal\s+not",
    r"不支持(?:图片|图像|多模态)",
    r"无法识别图片",
    r"only\s+support\s+text",
)

# 1×1 透明 PNG：探测用的最小合法图片（68 字节）
_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

_CACHE_PATH_ENV = "SJTU_VISION_CAPABILITY_PATH"


def _cache_path():
    from pathlib import Path

    override = os.environ.get(_CACHE_PATH_ENV, "").strip()
    if override:
        return Path(override)
    from sjtu_agent.paths import DATA_DIR

    return DATA_DIR / "vision_capability.json"


def _read_cache() -> dict:
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — 缓存坏了就当没有
        return {}


def _write_cache(model: str, supported: bool) -> None:
    data = _read_cache()
    data[model] = bool(supported)
    try:
        from sjtu_agent.paths import atomic_write_json

        atomic_write_json(_cache_path(), data)
    except Exception as exc:  # noqa: BLE001 — 写不了缓存不影响本次结论
        _logger.debug(f"[vision] 缓存写入失败：{exc}")


def _configured(model: str) -> str | None:
    """读用户显式配置：True/False → "yes"/"no"；没配 → None。"""
    try:
        import sjtu_agent.agent as agent_pkg

        cfg = agent_pkg.load_agent_config() or {}
    except Exception:  # noqa: BLE001 — 配置读不到就退回探测
        return None
    flag = cfg.get("vision_capable")
    if isinstance(flag, bool):
        return "yes" if flag else "no"
    unsupported = cfg.get("vision_unsupported_models")
    if isinstance(unsupported, list) and model in [str(x) for x in unsupported]:
        return "no"
    return None


def known_vision_model(model: str) -> bool:
    """关键词白名单判定（快路径，不做探测）。"""
    lowered = (model or "").lower()
    if not lowered:
        return False
    if any(kw in lowered for kw in _KNOWN_TEXT_ONLY_KEYWORDS):
        return False
    return any(kw in lowered for kw in _KNOWN_VISION_KEYWORDS)


def vision_capability(model: str) -> str:
    """返回 "yes" / "no" / "unknown"：能确定就不探测。"""
    configured = _configured(model)
    if configured:
        return configured
    if known_vision_model(model):
        return "yes"
    if any(kw in (model or "").lower() for kw in _KNOWN_TEXT_ONLY_KEYWORDS):
        return "no"
    cached = _read_cache().get(model)
    if isinstance(cached, bool):
        return "yes" if cached else "no"
    return "unknown"


def is_image_rejection(exc: BaseException) -> bool:
    """异常是不是「这个模型不接受图片输入」。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(re.search(pattern, text) for pattern in _REJECTION_PATTERNS)


def probe_vision_capability(client, model: str, *, save: bool = True) -> str | None:
    """用一张 1×1 像素图探测模型是否接受图片输入。

    返回 "yes" / "no"；无法判断（网络、限流、超时）返回 None——此时调用方应按
    「不能识图」处理并保留原降级链（视觉模型 → OCR）。
    """
    if client is None or not model:
        return None
    b64 = base64.b64encode(_PROBE_PNG).decode()
    try:
        from sjtu_agent.agent.runner import _is_anthropic_model

        if _is_anthropic_model(model):
            client.messages.create(
                model=model,
                max_tokens=1,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "ok"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                    ],
                }],
            )
        else:
            client.chat.completions.create(
                model=model,
                max_tokens=1,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "ok"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }],
            )
    except Exception as exc:  # noqa: BLE001 — 探测失败按「不支持」处理，不改主流程
        if is_image_rejection(exc):
            _logger.info(f"[vision] {model} 不接受图片输入，已缓存该结论")
            if save:
                _write_cache(model, False)
            return "no"
        _logger.debug(f"[vision] {model} 探测无法判定：{type(exc).__name__}: {exc}")
        return None
    _logger.info(f"[vision] {model} 接受图片输入，已缓存该结论")
    if save:
        _write_cache(model, True)
    return "yes"


def record_vision_unsupported(model: str) -> None:
    """记下「这个模型不吃图片」。

    探测返回拒绝时自动调用；实际调用被拒（端点声称能识图、真发图却报错）时也
    由调用方补一次，避免每次都拿同一张图去撞墙。
    """
    if not model:
        return
    _write_cache(model, False)


def resolve_vision_capability(model: str, client=None) -> bool:
    """给 bot 用的最终判定：能确定就用确定的，不确定就探测一次。"""
    capability = vision_capability(model)
    if capability == "yes":
        return True
    if capability == "no":
        return False
    probed = probe_vision_capability(client, model)
    return probed == "yes"


def vision_capability_hint(model: str) -> str:
    """给报错文案用的一句话说明。"""
    capability = vision_capability(model)
    if capability == "yes":
        return "当前模型支持识图"
    if capability == "no":
        return "当前模型不支持识图（可在 agent_config.json 设 vision_capable=true 覆盖）"
    return "当前模型的识图能力未知（配置 vision_capable 可跳过探测）"
