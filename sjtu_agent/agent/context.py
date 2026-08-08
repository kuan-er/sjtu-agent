"""sjtu_agent/agent/context.py — 会话上下文质量管理（Phase 2）。

前提（2026 共识）：1M 窗口下容量不是约束，但 **Context Rot 在长上下文仍发生**
（"1M 只是悬崖来得更晚"）。所以这里管的是**质量**（抗腐烂），不是容量：

- clear_stale_tool_results()  无损清理：旧轮次 tool 结果换占位符（效果已持久化，
  原文是腐烂主因之一）。确定性占位符 → 折叠后缓存可重新命中。
- trim_session()              质量预算：历史超 64K 时按轮次折叠最旧对话，
  保留最近几轮原文 + 插入要点摘要（保留用户意图/关键信息，防"健忘"）。
  Rarely 触发（为质量非容量），打断缓存前缀是偶尔可接受的代价。

缓存感知：折叠/清理都是确定性操作（同一输入 → 同一输出），执行一次后历史稳定，
缓存从折叠点重新命中。
"""

from __future__ import annotations

# 历史（不含 system）的质量预算。1M 下远低于容量上限，触发=抗腐烂。
SESSION_QUALITY_BUDGET = 64_000
# 折叠时保留最近几轮原文（模型最可能需要引用近况）
KEEP_RECENT_TURNS = 3
# 清理 tool 结果时保留最近几轮的（模型可能还在引用）
KEEP_RECENT_TOOL_RESULTS = 2

_TOOL_RESULT_PLACEHOLDER = "[工具结果已清理（效果已持久化），如需详情可重新查询]"


def _estimate_tokens(text: object) -> int:
    """粗略 token 估算（无 tiktoken 依赖，够预算控制用）。中英混排 ≈ len//3。"""
    if not text:
        return 0
    return max(1, len(str(text)) // 3)


def _session_history_cost(messages: list) -> int:
    """非 system 消息的估算 token 总量。"""
    return sum(
        _estimate_tokens(m.get("content", ""))
        for m in messages
        if m.get("role") != "system"
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
    messages: list, budget: int = SESSION_QUALITY_BUDGET
) -> int:
    """质量预算控制：清理旧 tool 结果 + 超预算时按轮次折叠最旧对话。

    就地修改 messages（各入口持有同一 list 引用）。返回处理的条数
    （清理的 tool 结果数 + 折叠的轮次数）。折叠时保留最近几轮原文，
    并插入一条 system 摘要提示（防健忘）。
    """
    if not messages:
        return 0

    cleared = clear_stale_tool_results(messages)
    total = _session_history_cost(messages)
    if total <= budget:
        return cleared

    users = _user_indices(messages)
    if not users:
        return cleared
    # 保护最近 KEEP_RECENT_TURNS 轮（含其 tool 结果），只折叠更早的
    protected_start = users[-KEEP_RECENT_TURNS] if len(users) > KEEP_RECENT_TURNS else None

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

    return cleared + removed
