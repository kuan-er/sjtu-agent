"""Shared conversation-core for the SJTU platform bots.

Everything here is stateless (takes the session dict explicitly), so each
platform script keeps only its transport/media/command plumbing.

The session dict shape is: {messages, model_box: [str], client_box: [client]}
— compatible with both the plain in-memory dicts of telegram/wechat/qq and
FeishuConversationManager's conversations.
"""
from __future__ import annotations

import datetime as _dt
import re

from sjtu_agent.agent import SYSTEM_PROMPT, _run_one_turn

# Each bot previously defined this regex locally; kept here once. feishu never
# needed it because it doesn't capture stdout — the shared path also doesn't.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mKABCDEFGHJKST]")


def build_date_ctx() -> str:
    """当前时间 + SJTU 学期上下文（每次调用刷新，避免长会话里时间过期）。"""
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    year, month = now.year, now.month
    if month >= 9:
        cur_xnm, cur_xqm = year, "1"
        prev_xnm, prev_xqm = year - 1, "2"
    elif month <= 6:
        cur_xnm, cur_xqm = year - 1, "2"
        prev_xnm, prev_xqm = year - 1, "1"
    else:
        cur_xnm, cur_xqm = year - 1, "3"
        prev_xnm, prev_xqm = year - 1, "2"
    return (
        f"\n\n## 当前时间（每轮自动刷新）\n"
        f"现在：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[now.weekday()]}。\n"
        f"当前学期：{cur_xnm}-{cur_xnm + 1}学年第{cur_xqm}学期。\n"
        f"「上学期」={prev_xnm}-{prev_xnm + 1}学年第{prev_xqm}学期"
        f"（query_grades: year='{prev_xnm}', semester='{prev_xqm}'）。\n"
        f"「本学期」={cur_xnm}-{cur_xnm + 1}学年第{cur_xqm}学期"
        f"（query_grades: year='{cur_xnm}', semester='{cur_xqm}'）。"
    )


def model_supports_vision(model: str) -> bool:
    """关键词判断模型是否支持图片输入。对 None 安全（统一各 bot 的防御写法）。"""
    m = (model or "").lower()
    return any(kw in m for kw in [
        "vision", "gpt-4o", "gpt-4-turbo", "claude-3", "claude-4",
        "gemini", "qwen-vl", "qwen3vl", "glm-4v", "internvl",
        "sonnet-4", "opus-4", "haiku-4",
    ])


def make_session(agent_cfg: dict | None = None) -> dict:
    """标准 session dict: {messages, model_box, client_box}。"""
    from sjtu_agent.agent import _make_client, load_agent_config
    cfg = agent_cfg or load_agent_config()
    return {
        "messages": [],
        "model_box": [cfg.get("model", "deepseek-chat")],
        "client_box": [_make_client(cfg) if cfg.get("api_key") else None],
    }


def init_messages(sess: dict, platform_ctx: str = "") -> None:
    """首次对话注入 system prompt；后续由 refresh_system_prompt 刷新时间。"""
    if sess["messages"]:
        return
    sess["messages"].append({
        "role": "system",
        "content": SYSTEM_PROMPT + build_date_ctx() + platform_ctx,
    })


def refresh_system_prompt(sess: dict, platform_ctx: str = "",
                          extra_suffix_fn=None, user_text: str = "") -> None:
    """每轮刷新 [0] system 内容（时间过期）+ 可选额外上下文。

    extra_suffix_fn(user_text) -> str 是飞书记忆注入的钩子。
    """
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        base = SYSTEM_PROMPT + build_date_ctx() + platform_ctx
        if extra_suffix_fn:
            base += extra_suffix_fn(user_text)
        sess["messages"][0]["content"] = base


def extract_assistant_reply(sess: dict) -> str:
    """从消息历史取最后一条 assistant 文本（飞书已验证的可靠方式）。

    _run_one_turn 会把 assistant 消息追加到 sess['messages']，所以无需
    捕获 stdout + 解析 "Agent: " marker。
    """
    for m in reversed(sess["messages"]):
        if m.get("role") == "assistant":
            content = m.get("content", "")
            if isinstance(content, str):
                return content.strip() or "(已完成)"
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                return "\n".join(texts).strip() or "(已完成)"
    return "(已完成)"


def run_one_turn(sess: dict, user_text: str, platform_ctx: str = "",
                 extra_suffix_fn=None) -> str:
    """追加文本用户轮次 → 跑 LLM → 返回 assistant 回复。"""
    init_messages(sess, platform_ctx)
    refresh_system_prompt(sess, platform_ctx, extra_suffix_fn, user_text)
    sess["messages"].append({"role": "user", "content": user_text})
    _run_one_turn(sess["client_box"][0], sess["model_box"][0], sess["messages"])
    return extract_assistant_reply(sess)


def run_one_turn_multimodal(sess: dict, content: list, platform_ctx: str = "",
                            extra_suffix_fn=None) -> str:
    """同 run_one_turn，但用户消息是 OpenAI multimodal content list。"""
    init_messages(sess, platform_ctx)
    refresh_system_prompt(sess, platform_ctx, extra_suffix_fn, "")
    sess["messages"].append({"role": "user", "content": content})
    _run_one_turn(sess["client_box"][0], sess["model_box"][0], sess["messages"])
    return extract_assistant_reply(sess)
