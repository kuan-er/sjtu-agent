"""sjtu_agent/agent/context.py — 会话上下文质量管理（Phase 2）。

前提（2026 共识）：1M 窗口下容量不是约束，但 **Context Rot 在长上下文仍发生**
（"1M 只是悬崖来得更晚"）。所以这里管的是**质量**（抗腐烂），不是容量：

- clear_stale_tool_results()  无损清理：旧轮次 tool 结果换占位符（效果已持久化，
  原文是腐烂主因之一）。确定性占位符 → 折叠后缓存可重新命中。
- trim_session()              质量预算：历史超预算时按轮次折叠最旧对话，
  保留最近几轮原文 + 插入要点摘要（保留用户意图/关键信息，防"健忘"）。
  预算按**模型窗口**计算（见 context_budget），不再写死。

缓存感知：折叠/清理都是确定性操作（同一输入 → 同一输出），执行一次后历史稳定，
缓存从折叠点重新命中。
"""

from __future__ import annotations

import os
import urllib.parse

from sjtu_agent.logging import get_logger

_logger = get_logger("context")

# ── 折叠预算：2026 口径下按「模型窗口 × 比例」算，不再写死 ────────────────────
#
# 本机实测（2026-09，3,076 次真实模型调用）：单次调用上下文中位 **28.9 万 tokens**、
# p90 58.1 万、峰值 71.4 万；其中 **99.6% 是缓存命中的前缀**，新增输入中位仅 282
# tokens。据此：
#   - 旧的 256K 常量（1M 窗口的 25%）折得太早。外部同类实现都在窗口的 **80%~95%**
#     才压（Claude Code 1M 模型 ~967K、Codex 实测 94.7% 可用窗口、dsh 0.8×窗口）；
#     过早折叠是另一种"截断"——把模型刚看过的原文换成摘要，还多付一次摘要调用。
#   - 但也不能贴着窗口上限：要留出工具结果、单轮输出与安全余量。
# 折中取窗口的 50%（1M 窗口 → 500K），并允许 SJTU_CONTEXT_BUDGET 显式覆盖。
# 参考：docs/research/agent-token-budget-2026H2.md
_BUDGET_RATIO = 0.5
_BUDGET_FLOOR = 32_000     # 再小也不低于此值，否则几轮就折
_BUDGET_CAP = 512_000      # 再大也不超过此值，留足输出与工具结果空间

# 未知模型的保守窗口（宁可早折一点，也不要撑爆对方窗口）
_DEFAULT_CONTEXT_WINDOW = 128_000

# ── 窗口按「后端 + 模型」两面解析 ────────────────────────────────────────────
# 同一个调用名在不同后端上窗口并不一样，所以不能只看模型名：
#   * 官方端点 → 官方口径；
#   * 致远一号（校园网关）→ 按官方指南的上下文长度（见下表）；
#   * 其它第三方网关 → 未公开，保守处理，除非用 SJTU_CONTEXT_WINDOW 显式声明。
_OFFICIAL_WINDOWS: tuple[tuple[str, int], ...] = (
    ("api.deepseek.com", 1_000_000),    # deepseek-flash / V4.1 系列：1M
    ("api.anthropic.com", 1_000_000),
    ("api.openai.com", 1_050_000),
)

# 致远一号：https://claw.sjtu.edu.cn/guide/sjtu-api/（2026-09 抓取）
#   deepseek-chat / deepseek-reasoner（DeepSeek V4 Flash）= 512k
#   qwen / qwen3.8-27b（Qwen3.8-27B，多模态）= 256k
# 注意：这与官方 DeepSeek 的 1M **不同**，按模型名一刀切会把预算算到窗口外面去。
_SJTU_GATEWAY_HOSTS = ("models.sjtu.edu.cn",)
_SJTU_GATEWAY_WINDOWS: dict[str, int] = {
    "deepseek-chat": 512_000,
    "deepseek-reasoner": 512_000,
    "qwen3.8-27b": 256_000,
    "qwen": 256_000,
}
_SJTU_GATEWAY_FALLBACK_WINDOW = 256_000   # 网关上认不出的调用名：按已公开的最小窗口
_CUSTOM_GATEWAY_WINDOW = 128_000          # 其它第三方网关：不猜

# 只看模型名时的口径（没有 base_url 时的向后兼容路径，按官方规格）
_MODEL_WINDOW_RULES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("deepseek", "claude", "kimi", "moonshot", "glm", "qwen", "minimax"), 1_000_000),
    (("gpt-6", "gpt-5", "o3", "o4", "gemini"), 1_000_000),
    (("gpt-4o", "gpt-4.1", "gpt-4-turbo"), 128_000),
)

_CONTEXT_WINDOW_ENV = "SJTU_CONTEXT_WINDOW"


def _host_of(base_url: str) -> str:
    if not base_url:
        return ""
    try:
        return (urllib.parse.urlsplit(base_url).netloc or "").lower()
    except ValueError:
        return ""


def is_campus_gateway(base_url: str) -> bool:
    """是否指向致远一号（校园网关）。

    网关与官方口径不同（窗口 512k/256k、单轮输出上限未公开），
    调用方据此选择更保守的取值。
    """
    host = _host_of(base_url)
    return any(gateway in host for gateway in _SJTU_GATEWAY_HOSTS)


def model_context_window(model: str = "", base_url: str = "") -> int:
    """推断上下文窗口：显式声明 > 后端口径 > 模型名猜测 > 保守默认。

    评估顺序（每一步都可能是"不要按模型名一刀切"的理由）：
    1. `SJTU_CONTEXT_WINDOW` 环境变量（用户最清楚自己的部署）；
    2. base_url 指向致远一号 → 网关官方公布的上下文长度（DeepSeek 512k / Qwen 256k）；
    3. base_url 指向官方端点 → 官方规格（DeepSeek / Anthropic 1M、OpenAI 1.05M）；
    4. base_url 是其它网关 → 128K（未公开，不猜）；
    5. 没给 base_url（老调用路径）→ 按模型名对应的官方规格。
    """
    raw = os.environ.get(_CONTEXT_WINDOW_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)

    name = (model or "").lower().strip()
    host = _host_of(base_url)

    if host:
        if any(gateway in host for gateway in _SJTU_GATEWAY_HOSTS):
            for call_name, window in _SJTU_GATEWAY_WINDOWS.items():
                if call_name in name:
                    return window
            return _SJTU_GATEWAY_FALLBACK_WINDOW
        for official_host, window in _OFFICIAL_WINDOWS:
            if official_host in host:
                return window
        return _CUSTOM_GATEWAY_WINDOW

    for keywords, window in _MODEL_WINDOW_RULES:
        if any(keyword in name for keyword in keywords):
            return window
    return _DEFAULT_CONTEXT_WINDOW


def context_budget(model: str = "", *, base_url: str = "", override: int | None = None) -> int:
    """历史（不含 system）的折叠预算，单位 tokens。

    优先级：显式 override > 环境变量 SJTU_CONTEXT_BUDGET > 窗口 × _BUDGET_RATIO。
    环境变量与推导值都会被夹在 [_BUDGET_FLOOR, _BUDGET_CAP] 之间（防止手滑写成
    天文数字导致永不折叠）；显式 override 参数视为调用方权威，不再夹取。

    窗口按「后端 + 模型」解析（见 model_context_window）：官方 DeepSeek 1M → 500K，
    致远一号 deepseek-chat 512k → 256K，其它网关 128K → 64K。
    """
    if override is not None and override > 0:
        return max(1_000, int(override))
    raw = os.environ.get("SJTU_CONTEXT_BUDGET", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(min(_BUDGET_CAP, max(_BUDGET_FLOOR, int(raw))))
    window = model_context_window(model, base_url)
    return int(min(_BUDGET_CAP, max(_BUDGET_FLOOR, window * _BUDGET_RATIO)))


# 兼容旧引用：未指定模型时的预算（未知模型 → 128K 窗口的一半）
SESSION_QUALITY_BUDGET = context_budget()
# 折叠时保留最近几轮原文（模型最可能需要引用近况）
KEEP_RECENT_TURNS = 3
# 清理 tool 结果时保留最近几轮的（模型可能还在引用）
KEEP_RECENT_TOOL_RESULTS = 2
# 多模态图片块的固定估算成本：按视觉 token 实际量级计，绝不按 base64
# 长度计——一张图的 data URL 有数 MB 字符，按长度计会瞬间击穿预算，
# 把图片消息本身折叠掉（实测导致"模型说看不到对话"）。
_IMAGE_BLOCK_TOKEN_COST = 1_500

_TOOL_RESULT_PLACEHOLDER = "[工具结果已清理（效果已持久化），如需详情可重新查询]"


def _estimate_tokens(text: object) -> int:
    """粗略 token 估算（无 tiktoken 依赖，够预算控制用）。中英混排 ≈ len//3。"""
    if not text:
        return 0
    return max(1, len(str(text)) // 3)


def _message_cost(m: dict) -> int:
    """单条消息的估算 token 成本。多模态 content(list) 只计文本块，
    图片块按固定视觉成本计（与 base64 长度解耦）。"""
    content = m.get("content")
    if content is None:
        return 0
    if isinstance(content, str):
        return _estimate_tokens(content)
    if isinstance(content, list):
        cost = 0
        for blk in content:
            if isinstance(blk, dict):
                if blk.get("type") == "image_url":
                    cost += _IMAGE_BLOCK_TOKEN_COST
                else:
                    cost += _estimate_tokens(blk.get("text") or "")
            else:
                cost += _estimate_tokens(str(blk))
        return cost
    return _estimate_tokens(str(content))


def _session_history_cost(messages: list) -> int:
    """非 system 消息的估算 token 总量。"""
    return sum(
        _message_cost(m) for m in messages if m.get("role") != "system"
    )


def _user_indices(messages: list) -> list[int]:
    return [i for i, m in enumerate(messages) if m.get("role") == "user"]


def clear_stale_tool_results(
    messages: list, keep_recent: int = KEEP_RECENT_TOOL_RESULTS
) -> int:
    """把最近 keep_recent 轮之外的 tool_result 内容替换为占位符。

    保留 tool_call_id（API 需要 tool_calls 与结果对应）；占位符确定性，
    之后缓存可从折叠点重新命中。返回清理条数。
    """
    users = _user_indices(messages)
    if not users:
        return 0
    cutoff = users[-keep_recent] if len(users) >= keep_recent else users[0]
    cleared = 0
    for i, m in enumerate(messages):
        if m.get("role") == "tool" and i < cutoff:
            content = m.get("content", "")
            if content and isinstance(content, str) and not content.startswith("["):
                m["content"] = _TOOL_RESULT_PLACEHOLDER
                cleared += 1
    return cleared


def _build_fold_digest(folded_messages: list) -> str:
    """从被折叠的消息提取要点摘要（不调 LLM，保真）。

    每轮取用户消息核心（去掉注入的时间前缀）前 ~40 字，去重。让模型知道
    之前聊过什么主题，细节才让用户重述。保留决定/标识符类信息优先。
    """
    lines: list[str] = []
    seen: set[str] = set()
    for m in folded_messages:
        if m.get("role") != "user":
            continue
        text = str(m.get("content") or "").strip()
        # 剥掉注入的时间前缀（"## 当前时间…\n\n" 之后才是用户原话）
        if "\n\n" in text:
            text = text.split("\n\n", 1)[1].strip()
        if not text or text in seen:
            continue
        seen.add(text)
        brief = text if len(text) <= 40 else text[:37] + "…"
        lines.append(brief)
    if not lines:
        return ""
    return "折叠前曾讨论：" + "；".join(lines[:6]) + "。"


def trim_session(
    messages: list, budget: int | None = None
) -> int:
    """质量预算控制：清理旧 tool 结果 + 超预算时按轮次折叠最旧对话。

    budget 缺省用 SESSION_QUALITY_BUDGET；调用方（runner）会按当前模型传
    context_budget(model)。就地修改 messages（各入口持有同一 list 引用）。
    返回处理的条数（清理的 tool 结果数 + 折叠的轮次数）。折叠时保留最近几轮
    原文，并插入一条 system 摘要提示（防健忘）。
    """
    if budget is None:
        budget = SESSION_QUALITY_BUDGET
    if not messages:
        return 0

    cleared = clear_stale_tool_results(messages)
    total = _session_history_cost(messages)
    if total <= budget:
        _logger.debug("上下文未超预算：估算 %d / 预算 %d tokens", total, budget)
        return cleared

    users = _user_indices(messages)
    if not users:
        return cleared
    # 保护最近 KEEP_RECENT_TURNS 轮（含其 tool 结果），只折叠更早的。
    # 轮数不足时保护到第一条用户消息——绝不允许把唯一的/最新的用户消息
    # 折叠掉（否则模型看到空历史，只会说"看不到你的问题"）。
    protected_start = (
        users[-KEEP_RECENT_TURNS] if len(users) > KEEP_RECENT_TURNS else users[0]
    )

    folded: list = []
    removed = 0
    while total > budget:
        first_user = next((i for i, m in enumerate(messages) if m.get("role") == "user"), None)
        if first_user is None:
            break
        if protected_start is not None and first_user >= protected_start:
            break  # 只剩保护轮次，不再折叠
        end = next(
            (i for i in range(first_user + 1, len(messages)) if messages[i].get("role") == "user"),
            len(messages),
        )
        if protected_start is not None and end > protected_start:
            end = protected_start
        chunk = messages[first_user:end]
        chunk_cost = sum(_estimate_tokens(m.get("content", "")) for m in chunk)
        folded.extend(chunk)
        del messages[first_user:end]
        total -= chunk_cost
        removed += 1
        if removed > 100:  # 保险
            break

    if folded:
        digest = _build_fold_digest(folded)
        note = "（上下文预算：更早的对话已折叠。"
        if digest:
            note += digest + " "
        note += "如需恢复细节可让用户重述或查询日志。）"
        insert_at = 1 if messages and messages[0].get("role") == "system" else 0
        messages.insert(insert_at, {"role": "system", "content": note})
        # 折叠事件是校准预算的关键数据：记下预算、折叠轮数、折叠后估算
        _logger.info(
            "折叠历史：预算 %d tokens，折叠 %d 轮，估算 %d → %d tokens（清理 tool 结果 %d 条）",
            budget, removed, total + sum(_estimate_tokens(m.get("content", "")) for m in folded),
            _session_history_cost(messages), cleared,
        )

    return cleared + removed
