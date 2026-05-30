#!/usr/bin/env python3
"""
telegram_bot.py — 将 agent.py 接入 Telegram Bot

用法:
  python3 telegram_bot.py          # 正常运行（长轮询）
  python3 telegram_bot.py --test   # 只测试 token 连通性，不启动

配置（config.json）:
  telegram_token        : BotFather 给的 token
  telegram_allowed_ids  : 允许使用的 Telegram user_id 列表（整数）
                          留空列表时，bot 会对任何人回复其 chat_id，方便首次配置

命令:
  /start /help  — 帮助
  /id           — 显示自己的 chat_id（添加到白名单用）
  /reset        — 清空本次对话历史
  /reminders    — 查看提醒事项列表
"""

import base64
import importlib
import io
import json
import re
import sys
import tempfile
import threading
import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sjtu_agent.paths import CONFIG_PATH

import telebot
import agent
import ddl_checker as dc

# ── 配置加载 ──────────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

cfg         = _load_cfg()
BOT_TOKEN   = cfg.get("telegram_token", "")
ALLOWED_IDS = set(int(x) for x in cfg.get("telegram_allowed_ids", []))

if not BOT_TOKEN:
    print("❌ config.json 中未设置 telegram_token，请先运行 setup_config.py")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# ── 会话状态（每个 chat_id 独立） ─────────────────────────────────────────────

_sessions: dict[int, dict] = {}
_locks:    dict[int, threading.Lock] = {}


def _get_session(chat_id: int) -> dict:
    if chat_id not in _sessions:
        agent_cfg = agent.load_agent_config()
        _sessions[chat_id] = {
            "messages":   [],
            "model_box":  [agent_cfg["model"]],
            "client_box": [agent._make_client(agent_cfg)],
        }
        _locks[chat_id] = threading.Lock()
    return _sessions[chat_id]


_TG_CTX = (
    "\n\n## 当前运行环境：Telegram Bot\n"
    "你正在通过 Telegram Bot 与用户交互，以下规则适用：\n"
    "- 当你调用 download_assignments 下载文件后，文件会**自动通过 Telegram 发送给用户**，无需告知用户本地路径或让用户手动打开目录。\n"
    "- 不要在回复中出现本地文件路径、`open` 命令或任何要求用户在终端操作的指令。\n"
    "- 下载完成后，直接告知用户「文件已发送」即可，不要描述本地路径。\n"
    "- 不要说「我无法在对话中传输二进制文件」——在 Telegram 环境下你完全可以发送文件。\n"
)


def _build_date_ctx() -> str:
    """生成包含当前精确时间的日期上下文（每次调用都是最新时间）。"""
    now   = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    year  = now.year
    month = now.month
    if month >= 9:
        cur_xnm, cur_xqm   = year,     "1"
        prev_xnm, prev_xqm = year - 1, "2"
    elif month <= 6:
        cur_xnm, cur_xqm   = year - 1, "2"
        prev_xnm, prev_xqm = year - 1, "1"
    else:
        cur_xnm, cur_xqm   = year - 1, "3"
        prev_xnm, prev_xqm = year - 1, "2"
    return (
        f"\n\n## 当前时间（每轮自动刷新）\n"
        f"现在：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[now.weekday()]}。\n"
        f"当前学期：{cur_xnm}-{cur_xnm+1}学年第{cur_xqm}学期。\n"
        f"「上学期」={prev_xnm}-{prev_xnm+1}学年第{prev_xqm}学期"
        f"（query_grades: year='{prev_xnm}', semester='{prev_xqm}'）。\n"
        f"「本学期」={cur_xnm}-{cur_xnm+1}学年第{cur_xqm}学期"
        f"（query_grades: year='{cur_xnm}', semester='{cur_xqm}'）。"
    )


def _init_messages(sess: dict) -> None:
    """首次对话时注入 system prompt；后续每轮由 _capture_turn 刷新时间。"""
    if sess["messages"]:
        return
    sess["messages"].append({"role": "system", "content": agent.SYSTEM_PROMPT + _build_date_ctx() + _TG_CTX})


# ── 输出捕获 ──────────────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mKABCDEFGHJKST]')


def _capture_turn(sess: dict, user_text: str, on_tool_result=None) -> str:
    """
    运行一轮对话，捕获 stdout，提取并返回 Agent 回复文本。
    Spinner 的 \r 控制序列和 ANSI 颜色代码会被过滤掉。
    on_tool_result: 可选回调 (tool_name, fn_args, result_str) -> None
    """
    _init_messages(sess)
    # 每轮刷新 system prompt 中的时间，避免长会话里时间过期
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        sess["messages"][0]["content"] = agent.SYSTEM_PROMPT + _build_date_ctx() + _TG_CTX
    sess["messages"].append({"role": "user", "content": user_text})

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        agent._run_one_turn(
            sess["client_box"][0],
            sess["model_box"][0],
            sess["messages"],
        )
    finally:
        sys.stdout = old_stdout

    raw = buf.getvalue()
    # 去除 ANSI 转义码
    clean = _ANSI_RE.sub("", raw)
    # 找到最后一个 "Agent: " 标记（spinner 输出在它之前）
    marker = "Agent: "
    idx = clean.rfind(marker)
    if idx == -1:
        # 没有找到，可能全是工具调用后无文本回复
        # 尝试从消息历史里取最后一条 assistant 消息
        for m in reversed(sess["messages"]):
            if m.get("role") == "assistant":
                content = m.get("content", "")
                if isinstance(content, str):
                    return content.strip() or "(已完成)"
                elif isinstance(content, list):
                    texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                    return "\n".join(texts).strip() or "(已完成)"
        return "(已完成)"

    return clean[idx + len(marker):].strip()


# ── 流式推进（带工具进度回调） ──────────────────────────────────────────────

def _streamed_turn(sess: dict, user_text: str, on_progress, on_tool_result=None) -> str:
    """
    运行一轮对话，期间通过 on_progress(event_type, payload) 实时上报：
      - on_progress("tool_start", {"name": str})
      - on_progress("tool_end",   {"name": str, "elapsed_ms": int})
      - on_progress("first_token", {})           # 收到第一个文本 token
      - on_progress("text_chunk",  {"text": str}) # 累积 buffer 已超阈值
    on_tool_result: 可选回调 (tool_name, fn_args, result_str) -> None
    返回完整文本回复。tool_use 循环最多 8 轮。
    """
    import time as _time
    import json as _json

    _init_messages(sess)
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        sess["messages"][0]["content"] = agent.SYSTEM_PROMPT + _build_date_ctx() + _TG_CTX
    sess["messages"].append({"role": "user", "content": user_text})

    client = sess["client_box"][0]
    model = sess["model_box"][0]
    is_anthropic = agent._is_anthropic_model(model)

    full_text = ""
    MAX_ROUNDS = 20
    saw_first_token_global = [False]

    def _emit_first_token():
        if saw_first_token_global[0]:
            return
        saw_first_token_global[0] = True
        try: on_progress("first_token", {})
        except Exception: pass

    if is_anthropic:
        system_msg = sess["messages"][0]["content"] if sess["messages"][0]["role"] == "system" else ""
        api_msgs = [m for m in sess["messages"] if m["role"] != "system"]
        tools = agent._anthropic_tools()

        for _round in range(MAX_ROUNDS):
            content_blocks: list[dict] = []
            tool_inputs: dict[int, str] = {}
            text_so_far = ""

            with client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system_msg,
                messages=api_msgs,
                tools=tools,
            ) as stream:
                for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "content_block_start":
                        block = event.content_block
                        btype = getattr(block, "type", "")
                        if btype == "text":
                            content_blocks.append({"type": "text", "text": ""})
                        elif btype == "tool_use":
                            content_blocks.append({
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": {},
                            })
                            tool_inputs[len(content_blocks) - 1] = ""
                    elif etype == "content_block_delta":
                        delta = event.delta
                        dtype = getattr(delta, "type", "")
                        if dtype == "text_delta":
                            chunk = delta.text
                            text_so_far += chunk
                            full_text += chunk
                            if content_blocks and content_blocks[-1].get("type") == "text":
                                content_blocks[-1]["text"] += chunk
                            _emit_first_token()
                        elif dtype == "input_json_delta":
                            idx = event.index
                            tool_inputs[idx] = tool_inputs.get(idx, "") + delta.partial_json

            for idx, raw_json in tool_inputs.items():
                if idx < len(content_blocks) and content_blocks[idx].get("type") == "tool_use":
                    try:
                        content_blocks[idx]["input"] = _json.loads(raw_json or "{}")
                    except Exception:
                        content_blocks[idx]["input"] = {}

            has_tool_use = any(b.get("type") == "tool_use" for b in content_blocks)
            api_msgs.append({"role": "assistant", "content": content_blocks})
            sess["messages"].append({"role": "assistant", "content": content_blocks})

            if not has_tool_use:
                return full_text

            tool_results = []
            for b in content_blocks:
                if b.get("type") != "tool_use":
                    continue
                fn_name = b["name"]
                fn_args = b["input"] if isinstance(b["input"], dict) else {}
                try: on_progress("tool_start", {"name": fn_name})
                except Exception: pass
                t0 = _time.monotonic()
                result = agent.run_tool(fn_name, fn_args)
                elapsed_ms = int((_time.monotonic() - t0) * 1000)
                try: on_progress("tool_end", {"name": fn_name, "elapsed_ms": elapsed_ms})
                except Exception: pass
                if on_tool_result:
                    try: on_tool_result(fn_name, fn_args, result)
                    except Exception: pass
                tool_results.append({"type": "tool_result", "tool_use_id": b["id"], "content": result})
            api_msgs.append({"role": "user", "content": tool_results})
            sess["messages"].append({"role": "user", "content": tool_results})

        # 超出 MAX_ROUNDS 仍在调工具：强制一次无工具调用以合成最终回复
        try:
            fallback_text = ""
            with client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system_msg,
                messages=api_msgs,
            ) as stream:
                for event in stream:
                    if getattr(event, "type", "") == "content_block_delta":
                        delta = event.delta
                        if getattr(delta, "type", "") == "text_delta":
                            fallback_text += delta.text
                            full_text += delta.text
                            _emit_first_token()
            if fallback_text:
                sess["messages"].append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": fallback_text}],
                })
        except Exception:
            pass
        return full_text

    # ── OpenAI 兼容路径 ──────────────────────────────────────────────────────
    messages = list(sess["messages"])

    for _round in range(MAX_ROUNDS):
        text_so_far = ""
        tool_calls_map: dict[int, dict] = {}

        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=agent.TOOLS,
            tool_choice="auto",
            stream=True,
            timeout=180,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                text_so_far += delta.content
                full_text += delta.content
                _emit_first_token()
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                    if tc.id:
                        tool_calls_map[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_map[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_map[idx]["arguments"] += tc.function.arguments

        if not tool_calls_map:
            sess["messages"].append({"role": "assistant", "content": text_so_far})
            return full_text

        from openai.types.chat import ChatCompletionMessageToolCall, ChatCompletionMessage
        from openai.types.chat.chat_completion_message_tool_call import Function

        tc_objs = []
        for idx in sorted(tool_calls_map):
            e = tool_calls_map[idx]
            tc_objs.append(ChatCompletionMessageToolCall(
                id=e["id"], type="function",
                function=Function(name=e["name"], arguments=e["arguments"]),
            ))
        assistant_msg = ChatCompletionMessage(
            role="assistant", content=text_so_far or None, tool_calls=tc_objs,
        )
        messages.append(assistant_msg)
        sess["messages"].append(assistant_msg)

        for tc in tc_objs:
            fn_name = tc.function.name
            try: fn_args = _json.loads(tc.function.arguments or "{}")
            except Exception: fn_args = {}
            try: on_progress("tool_start", {"name": fn_name})
            except Exception: pass
            t0 = _time.monotonic()
            result = agent.run_tool(fn_name, fn_args)
            elapsed_ms = int((_time.monotonic() - t0) * 1000)
            try: on_progress("tool_end", {"name": fn_name, "elapsed_ms": elapsed_ms})
            except Exception: pass
            if on_tool_result:
                try: on_tool_result(fn_name, fn_args, result)
                except Exception: pass
            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result}
            messages.append(tool_msg)
            sess["messages"].append(tool_msg)

    # 超出 MAX_ROUNDS 仍在调工具：强制一次无工具调用以合成最终回复
    try:
        fb_text = ""
        fb_stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            timeout=180,
        )
        for chunk in fb_stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                fb_text += delta.content
                full_text += delta.content
                _emit_first_token()
        if fb_text:
            sess["messages"].append({"role": "assistant", "content": fb_text})
    except Exception:
        pass

    # 记录对话到 conversation_log（供用户画像更新使用）
    try:
        from sjtu_agent.news_aggregator.profile import log_conversation
        log_conversation(user_text, full_text)
    except Exception:
        pass

    return full_text


# ── LLM 预拦截：高频问题直接调工具，跳过整轮 LLM ─────────────────────────────

_QUICK_INTENTS: list[tuple["re.Pattern", str]] = [
    # DDL 相关
    (re.compile(r"^(我?(还|目前|现在|最近)?有(哪些|什么|啥)?\s*(ddl|DDL|作业|截止|要交))\W*$"), "ddl"),
    (re.compile(r"^\s*(查|看|要|给我|帮我看?)?\s*(ddl|DDL)\s*[?？!！。.]*$"), "ddl"),
    (re.compile(r"^\s*(临近|最近|近期)\s*(的)?(ddl|DDL|作业)\W*$"), "ddl"),
    # 课表
    (re.compile(r"^(今天|今日)(我)?(有(什么|啥|哪些)?)?\s*课\W*$"), "schedule_today"),
    (re.compile(r"^(明天|明日)(我)?(有(什么|啥|哪些)?)?\s*课\W*$"), "schedule_tomorrow"),
    # 物理实验
    (re.compile(r"^(下次|下一次|下一节)\s*(物理)?实验\W*$"), "next_lab"),
    # 提醒列表
    (re.compile(r"^\s*(我的)?提醒(事项)?\s*(列表)?\W*$"), "list_reminders"),
]


def _try_quick_intent(user_text: str) -> tuple[str, str] | None:
    """如果命中预拦截规则，直接跑工具并返回 (label, html_reply)；否则返回 None。
    跳过 LLM 推理可省 5-10s/次。"""
    text = (user_text or "").strip()
    if len(text) > 50:
        return None  # 长消息留给 LLM
    for pattern, intent in _QUICK_INTENTS:
        if not pattern.match(text):
            continue
        try:
            if intent == "ddl":
                raw = agent.run_tool("get_ddls", {})
                return ("DDL", _format_ddls_for_telegram(raw))
            if intent == "schedule_today":
                raw = agent.run_tool("get_schedule", {"query_type": "day", "date": "今天"})
                return ("今日课表", _format_schedule_for_telegram(raw, "今天"))
            if intent == "schedule_tomorrow":
                raw = agent.run_tool("get_schedule", {"query_type": "day", "date": "明天"})
                return ("明日课表", _format_schedule_for_telegram(raw, "明天"))
            if intent == "next_lab":
                raw = agent.run_tool("get_next_lab", {})
                return ("下次物理实验", _format_next_lab_for_telegram(raw))
            if intent == "list_reminders":
                raw = agent.run_tool("list_reminders", {})
                return ("提醒事项", _format_reminders_for_telegram(raw))
        except Exception:
            return None  # 工具失败时退回到 LLM
    return None


_PLATFORM_CN = {
    "canvas":     "Canvas",
    "Canvas":     "Canvas",
    "aihaoke":    "AI 好课",
    "icourse163": "中国大学 MOOC",
    "icourse":    "中国大学 MOOC",
    "phycai":     "物理实验",
}


def _format_ddls_for_telegram(raw_json: str) -> str:
    """把 tool_get_ddls 的 JSON 输出渲染成 Telegram HTML。"""
    try:
        data = json.loads(raw_json)
    except Exception:
        return "❌ 获取 DDL 失败，请重试。"
    ddls = data.get("ddls") or []
    if not ddls:
        return "🎉 <b>没有未完成的 DDL</b>，可以放心休息了。"
    lines = ["📋 <b>未完成 DDL</b>\n"]
    for d in ddls[:30]:
        platform = _PLATFORM_CN.get(d.get("platform", ""), d.get("platform", ""))
        course = (d.get("course") or "").strip()
        name = (d.get("name") or "未命名作业").strip()
        due = d.get("due") or ""
        hours_left = d.get("hours_left")
        if isinstance(hours_left, (int, float)):
            if hours_left < 24:
                left = f"还剩 <b>{int(hours_left)} 小时</b>"
            else:
                left = f"还剩 {int(hours_left/24)} 天"
        else:
            left = ""
        head = f"• <b>{name}</b>"
        meta = f"  {platform} · {course}".rstrip(" ·")
        tail = f"  📅 {due} {('· ' + left) if left else ''}".strip()
        lines.append(head)
        if course or platform:
            lines.append(meta)
        lines.append(tail)
    if len(ddls) > 30:
        lines.append(f"\n…共 {len(ddls)} 项，仅显示前 30 项。")
    return "\n".join(lines)


def _format_schedule_for_telegram(raw_json: str, label: str) -> str:
    try:
        data = json.loads(raw_json)
    except Exception:
        return "❌ 获取课表失败。"
    if data.get("error"):
        return f"❌ {data['error']}"
    classes = data.get("classes") or data.get("items") or []
    if not classes:
        return f"📭 <b>{label}没有课</b>。"
    lines = [f"📅 <b>{label}的课</b>\n"]
    for c in classes:
        name = c.get("name") or c.get("course_name") or "未知课程"
        time_range = c.get("time") or c.get("time_range") or ""
        room = c.get("room") or c.get("classroom") or ""
        teacher = c.get("teacher") or ""
        lines.append(f"• <b>{name}</b>")
        meta_parts = [time_range, room, teacher]
        meta = "  " + " · ".join(p for p in meta_parts if p)
        if meta.strip():
            lines.append(meta)
    return "\n".join(lines)


def _format_next_lab_for_telegram(raw_json: str) -> str:
    try:
        data = json.loads(raw_json)
    except Exception:
        return "❌ 获取实验安排失败。"
    lab = data.get("lab") or data
    if not lab or not (lab.get("name") or lab.get("title")):
        return "📭 没有查到下次物理实验安排。"
    name = lab.get("name") or lab.get("title") or ""
    when = lab.get("time") or lab.get("start_time") or ""
    where = lab.get("location") or lab.get("room") or ""
    lines = [f"🧪 <b>下次物理实验</b>\n"]
    lines.append(f"• <b>{name}</b>")
    if when:
        lines.append(f"  🕐 {when}")
    if where:
        lines.append(f"  📍 {where}")
    return "\n".join(lines)


def _format_reminders_for_telegram(raw_json: str) -> str:
    try:
        data = json.loads(raw_json)
    except Exception:
        return "❌ 获取提醒事项失败。"
    reminders = data.get("reminders") or []
    if not reminders:
        return "📭 没有提醒事项。"
    lines = ["📌 <b>提醒事项</b>\n"]
    for r in reminders[:30]:
        title = (r.get("title") or "").strip()
        start = r.get("start") or ""
        end = r.get("end") or ""
        when = start or end or ""
        line = f"• <b>{title}</b>"
        if when:
            line += f"\n  📅 {when}"
        lines.append(line)
    return "\n".join(lines)


# ── Markdown → Telegram HTML 转换 ────────────────────────────────────────────

def _latex_to_unicode(text: str) -> str:
    """
    将 LaTeX 数学公式转换为可读的 Unicode 文本。
    处理行内公式 \(...\) 和块级公式 \[...\] 及 $$...$$。
    """
    import re as _re

    _SYMBOLS = [
        # 分数优先（嵌套时需多次迭代）
        (r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)"),
        # 希腊字母
        (r"\\omega", "ω"), (r"\\alpha", "α"), (r"\\beta", "β"), (r"\\gamma", "γ"),
        (r"\\delta", "δ"), (r"\\epsilon", "ε"), (r"\\theta", "θ"), (r"\\lambda", "λ"),
        (r"\\mu", "μ"), (r"\\pi", "π"), (r"\\sigma", "σ"), (r"\\tau", "τ"),
        (r"\\phi", "φ"), (r"\\psi", "ψ"), (r"\\rho", "ρ"), (r"\\eta", "η"),
        (r"\\Omega", "Ω"), (r"\\Delta", "Δ"), (r"\\Sigma", "Σ"), (r"\\Pi", "Π"),
        (r"\\Lambda", "Λ"), (r"\\Phi", "Φ"), (r"\\Psi", "Ψ"), (r"\\Gamma", "Γ"),
        # 运算符和关系
        (r"\\times", "×"), (r"\\cdot", "·"), (r"\\div", "÷"),
        (r"\\leq", "≤"), (r"\\geq", "≥"), (r"\\neq", "≠"), (r"\\approx", "≈"),
        (r"\\pm", "±"), (r"\\mp", "∓"), (r"\\infty", "∞"),
        (r"\\Rightarrow", "⇒"), (r"\\rightarrow", "→"), (r"\\leftarrow", "←"),
        (r"\\Leftrightarrow", "⟺"), (r"\\leftrightarrow", "↔"),
        (r"\\to", "→"), (r"\\gets", "←"),
        (r"\\partial", "∂"), (r"\\nabla", "∇"), (r"\\int", "∫"), (r"\\sum", "Σ"),
        (r"\\prod", "∏"),
        (r"\\sqrt\{([^{}]+)\}", r"√(\1)"),
        (r"\\sqrt", "√"),
        # 上下标
        (r"\^2", "²"), (r"\^3", "³"), (r"\^n", "ⁿ"), (r"\^T", "ᵀ"),
        (r"_\{([^{}]+)\}", r"_\1"),
        (r"_0", "₀"), (r"_1", "₁"), (r"_2", "₂"), (r"_3", "₃"),
        (r"_n", "ₙ"), (r"_i", "ᵢ"), (r"_m", "ₘ"),
        # 其他
        (r"\\boxed\{([^{}]+)\}", r"【\1】"),
        (r"\\left", ""), (r"\\right", ""),
        (r"\\quad", "  "), (r"\\,", " "), (r"\\;", " "),
        (r"\\!", ""), (r"\\ ", " "),
        (r"\\text\{([^{}]+)\}", r"\1"),
        (r"\\mathrm\{([^{}]+)\}", r"\1"),
        (r"\\mathbf\{([^{}]+)\}", r"\1"),
        (r"\\[a-zA-Z]+", ""),
        (r"[{}]", ""),
    ]

    def _convert(expr: str) -> str:
        s = expr
        # 多次迭代处理嵌套（如 \frac{\sqrt{k}}{M}）
        for _ in range(5):
            prev = s
            for pattern, repl in _SYMBOLS:
                s = _re.sub(pattern, repl, s)
            if s == prev:
                break
        return s.strip()

    # 块级公式 \[...\] → 独立行
    text = _re.sub(
        r"\\\[(.+?)\\\]",
        lambda m: "\n" + _convert(m.group(1)) + "\n",
        text, flags=_re.DOTALL,
    )
    # 块级公式 $$...$$
    text = _re.sub(
        r"\$\$(.+?)\$\$",
        lambda m: "\n" + _convert(m.group(1)) + "\n",
        text, flags=_re.DOTALL,
    )
    # 行内公式 \(...\)
    text = _re.sub(
        r"\\\((.+?)\\\)",
        lambda m: _convert(m.group(1)),
        text, flags=_re.DOTALL,
    )
    # 行内公式 $...$（单个 $，排除 $$）
    text = _re.sub(
        r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)",
        lambda m: _convert(m.group(1)),
        text,
    )
    return text


def _md_to_tg_html(text: str) -> str:
    """
    将 agent 输出的 Markdown 文本转换为 Telegram 支持的 HTML 格式。
    Telegram HTML 只支持：<b> <i> <u> <s> <code> <pre> <a>
    LaTeX 公式先转换为 Unicode 可读形式。
    """
    import html as _html
    import re as _re

    # 先把 LaTeX 公式转成 Unicode
    text = _latex_to_unicode(text)

    # 1. 先转义 HTML 特殊字符（避免内容中的 < > & 被解析为标签）
    #    但要跳过已有的 HTML 标签（如日报里的 <b>）
    #    检测是否已经是 HTML：包含明确的 <b> <i> 等标签
    if _re.search(r'<(b|i|u|code|pre|a)\b[^>]*>', text):
        # 已经是 HTML 格式，直接返回
        return text

    # 转义纯文本中的 HTML 字符
    def _escape_non_tag(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = text.split("\n")
    result = []

    for line in lines:
        # 水平分隔线
        if _re.match(r"^\s*[-*_]{3,}\s*$", line):
            result.append("")
            continue

        # ### 标题 → <b>
        m = _re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            content = _escape_non_tag(m.group(1).strip())
            # 移除标题内的 ** 标记
            content = _re.sub(r"\*\*(.*?)\*\*", r"\1", content)
            result.append(f"<b>{content}</b>")
            continue

        # 普通行：转义后处理内联样式
        line = _escape_non_tag(line)

        # **bold** → <b>bold</b>
        line = _re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", line)

        # *italic* 或 _italic_ → <i>italic</i>（避免误伤正常下划线）
        line = _re.sub(r"\*([^*\n]+?)\*", r"<i>\1</i>", line)

        # `code` → <code>code</code>
        line = _re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", line)

        # [text](url) → <a href="url">text</a>
        line = _re.sub(r"\[([^\]]+?)\]\((https?://[^\)]+?)\)", r'<a href="\2">\1</a>', line)

        result.append(line)

    return "\n".join(result)


# ── 消息发送工具 ──────────────────────────────────────────────────────────────

def _send_chunks(chat_id: int, text: str, max_len: int = 4000, parse_mode: str = "") -> None:
    """自动分割超长消息（Telegram 限制 4096 字节）。"""
    while text:
        bot.send_message(chat_id, text[:max_len], parse_mode=parse_mode or None)
        text = text[max_len:]


def _keep_typing(chat_id: int, stop_event: threading.Event) -> None:
    """在处理期间持续发送 typing 动作（每 4 秒一次）。"""
    while not stop_event.wait(4):
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass


# ── 权限检查 ──────────────────────────────────────────────────────────────────

def _is_authorized(chat_id: int, message) -> bool:
    if not ALLOWED_IDS:
        bot.reply_to(
            message,
            f"⚠️ 白名单未配置。\n\n"
            f"你的 chat_id 是：<code>{chat_id}</code>\n\n"
            f"请将此 ID 添加到 config.json 的 <code>telegram_allowed_ids</code> 列表中，"
            f"然后重启 bot。",
            parse_mode="HTML",
        )
        return False
    if chat_id not in ALLOWED_IDS:
        bot.reply_to(message, f"⛔ 未授权访问（chat_id: {chat_id}）")
        return False
    return True


# ── 命令处理 ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start", "help"])
def cmd_start(msg):
    chat_id = msg.chat.id
    if not _is_authorized(chat_id, msg):
        return
    bot.reply_to(msg, (
        "🤖 <b>SJTU Agent</b>\n\n"
        "直接发消息开始对话。\n\n"
        "<b>命令：</b>\n"
        "/report — 立即生成今日学习日报\n"
        "/reset — 重置对话历史\n"
        "/reminders — 查看提醒事项\n"
        "/id — 显示你的 chat_id"
    ), parse_mode="HTML")


@bot.message_handler(commands=["report"])
def cmd_report(msg):
    chat_id = msg.chat.id
    if not _is_authorized(chat_id, msg):
        return
    bot.send_chat_action(chat_id, "typing")
    stop_typing = threading.Event()
    typing_thread = threading.Thread(
        target=_keep_typing, args=(chat_id, stop_typing), daemon=True
    )
    typing_thread.start()
    def run_report():
        try:
            import daily_report
            report = daily_report.build_report()
            _send_chunks(chat_id, report, parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"❌ 生成日报出错：{e}")
        finally:
            stop_typing.set()
    threading.Thread(target=run_report, daemon=True).start()


@bot.message_handler(commands=["id"])
def cmd_id(msg):
    bot.reply_to(msg, f"你的 chat_id：<code>{msg.chat.id}</code>", parse_mode="HTML")


@bot.message_handler(commands=["reset"])
def cmd_reset(msg):
    if not _is_authorized(msg.chat.id, msg):
        return
    _sessions.pop(msg.chat.id, None)
    bot.reply_to(msg, "✅ 对话历史已清空。")


@bot.message_handler(commands=["reminders"])
def cmd_reminders(msg):
    if not _is_authorized(msg.chat.id, msg):
        return
    result = agent.tool_list_reminders()
    active  = result.get("active", [])
    expired = result.get("expired", [])
    lines   = [f"🕐 {result['current_time']}\n"]
    if active:
        lines.append(f"📌 <b>待提醒（{len(active)}条）</b>")
        for r in active:
            end  = f" → {r['end'][:16]}" if r.get("end") else ""
            note = f"\n   <i>{r['note']}</i>" if r.get("note") else ""
            lines.append(f"• [{r['id']}] {r['title']}  {r['start'][:16]}{end}{note}")
    else:
        lines.append("📌 暂无待提醒事项")
    if expired:
        lines.append(f"\n✅ <b>已过期（{len(expired)}条）</b>")
        for r in expired:
            lines.append(f"• [{r['id']}] {r['title']}")
    bot.reply_to(msg, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["news"])
def cmd_news(msg):
    """立即触发一次新闻日报采集与推送。"""
    if not _is_authorized(msg.chat.id, msg):
        return
    bot.reply_to(msg, "⏳ 正在采集新闻，请稍候（约 30 秒）…")
    def run():
        try:
            from sjtu_agent.news_aggregator import NewsAggregator
            from sjtu_agent.agent.chat_loop import load_agent_config
            from sjtu_agent.agent.runner import _make_client
            cfg = load_agent_config()
            llm_client = _make_client(cfg) if cfg.get("api_key") else None
            agg = NewsAggregator(llm_client=llm_client, model=cfg.get("model", ""))
            _, html = agg.run()
            _send_chunks(msg.chat.id, html, parse_mode="HTML")
        except Exception as e:
            bot.send_message(msg.chat.id, f"❌ 新闻采集失败：{e}")
    threading.Thread(target=run, daemon=True).start()


@bot.message_handler(commands=["news_block"])
def cmd_news_block(msg):
    """屏蔽某个新闻分类：/news_block 二手交易"""
    if not _is_authorized(msg.chat.id, msg):
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "用法：/news_block <分类名>\n例如：/news_block 二手交易")
        return
    category = parts[1].strip()
    try:
        from sjtu_agent.news_aggregator.profile import UserProfile
        UserProfile().block_category(category)
        bot.reply_to(msg, f"✅ 已屏蔽「{category}」，下次日报不再推送此类内容。")
    except Exception as e:
        bot.reply_to(msg, f"❌ 操作失败：{e}")


@bot.message_handler(commands=["news_unblock"])
def cmd_news_unblock(msg):
    """取消屏蔽：/news_unblock 二手交易"""
    if not _is_authorized(msg.chat.id, msg):
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "用法：/news_unblock <分类名>")
        return
    category = parts[1].strip()
    try:
        from sjtu_agent.news_aggregator.profile import UserProfile
        UserProfile().unblock_category(category)
        bot.reply_to(msg, f"✅ 已取消屏蔽「{category}」。")
    except Exception as e:
        bot.reply_to(msg, f"❌ 操作失败：{e}")


@bot.message_handler(commands=["news_reset"])
def cmd_news_reset(msg):
    """重置用户画像（保留屏蔽列表）。"""
    if not _is_authorized(msg.chat.id, msg):
        return
    try:
        from sjtu_agent.news_aggregator.profile import UserProfile
        UserProfile().reset()
        bot.reply_to(msg, "✅ 用户画像已重置，下次日报将从零开始学习你的偏好。")
    except Exception as e:
        bot.reply_to(msg, f"❌ 操作失败：{e}")


# ── 文件 / 图片处理辅助 ───────────────────────────────────────────────────────

# 临时文件目录（每次启动复用，进程退出后由 OS 自动清理）
_TMP_DIR = Path(tempfile.mkdtemp(prefix="sjtu_tg_"))


def _download_tg_file(file_id: str, filename: str) -> Path:
    """从 Telegram 服务器下载文件，保存到临时目录，返回本地路径。"""
    file_info = bot.get_file(file_id)
    file_bytes = bot.download_file(file_info.file_path)
    save_path = _TMP_DIR / filename
    save_path.write_bytes(file_bytes)
    return save_path


def _model_supports_vision(model: str) -> bool:
    """简单判断当前模型是否支持图片输入。"""
    m = model.lower()
    return any(kw in m for kw in [
        "vision", "gpt-4o", "gpt-4-turbo", "claude-3", "claude-4",
        "gemini", "qwen-vl", "qwen3vl", "glm-4v", "internvl",
        "sonnet-4", "opus-4", "haiku-4",  # Claude 4 系列（支持下划线和连字符）
    ])


def _capture_turn_multimodal(sess: dict, content: list) -> str:
    """
    与 _capture_turn 类似，但 user 消息使用多模态 content list。
    content 格式：OpenAI 多模态消息 content 数组，如
      [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:..."}}]
    """
    _init_messages(sess)
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        sess["messages"][0]["content"] = agent.SYSTEM_PROMPT + _build_date_ctx() + _TG_CTX
    sess["messages"].append({"role": "user", "content": content})

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        agent._run_one_turn(
            sess["client_box"][0],
            sess["model_box"][0],
            sess["messages"],
        )
    finally:
        sys.stdout = old_stdout

    raw   = buf.getvalue()
    clean = _ANSI_RE.sub("", raw)
    marker = "Agent: "
    idx = clean.rfind(marker)
    if idx == -1:
        for m in reversed(sess["messages"]):
            if m.get("role") == "assistant":
                c = m.get("content", "")
                if isinstance(c, str):
                    return c.strip() or "(已完成)"
                elif isinstance(c, list):
                    return "\n".join(b.get("text", "") for b in c if b.get("type") == "text").strip() or "(已完成)"
        return "(已完成)"
    return clean[idx + len(marker):].strip()


def _run_with_typing(chat_id: int, lock, fn):
    """在 typing 动作下运行 fn()，统一处理异常并发送结果。"""
    sess = _get_session(chat_id)
    if lock.locked():
        bot.send_message(chat_id, "⏳ 上一条消息还在处理中，请稍候…")
        return

    stop_typing = threading.Event()
    threading.Thread(target=_keep_typing, args=(chat_id, stop_typing), daemon=True).start()
    try:
        with lock:
            reply = fn(sess)
        html_reply = _md_to_tg_html(reply)
        _send_chunks(chat_id, html_reply, parse_mode="HTML")
    except Exception as e:
        bot.send_message(chat_id, f"❌ 出错了：{e}")
    finally:
        stop_typing.set()


# ── 文档消息处理（PDF / 任意文件）────────────────────────────────────────────

@bot.message_handler(content_types=["document"])
def handle_document(msg):
    chat_id = msg.chat.id
    if not _is_authorized(chat_id, msg):
        return

    doc      = msg.document
    caption  = (msg.caption or "").strip()
    filename = doc.file_name or f"file_{doc.file_id[:8]}"

    lock = _locks.get(chat_id) or _get_session(chat_id) and _locks[chat_id]
    if lock.locked():
        bot.reply_to(msg, "⏳ 上一条消息还在处理中，请稍候…")
        return

    bot.send_chat_action(chat_id, "upload_document")

    def run():
        sess = _get_session(chat_id)
        stop_typing = threading.Event()
        threading.Thread(target=_keep_typing, args=(chat_id, stop_typing), daemon=True).start()
        try:
            # 1. 下载文件到临时目录
            local_path = _download_tg_file(doc.file_id, filename)

            # 2. 构造传给 agent 的用户消息：告知文件已保存到本地路径
            suffix = local_path.suffix.lower()
            extra_context = ""
            extract_error = ""
            # 优先走 parse router（支持多文件格式）；失败后回退到 legacy reader
            try:
                parse_result = agent.tool_parse_local_file(
                    str(local_path),
                    max_chars=4000,
                    start_page=1,
                    strategy="auto",
                )
                extracted = (parse_result or {}).get("content", "")
                if extracted:
                    parser_name = parse_result.get("parser", "unknown")
                    extra_context = (
                        f"\n\n以下是文件提取的文字内容供参考（parser={parser_name}）：\n"
                        f"```\n{extracted[:3000]}\n```"
                    )
                else:
                    extract_error = (parse_result or {}).get("error", "")
            except Exception as ex:
                extract_error = str(ex)

            # 3. 构建系统消息，明确告知 agent 文件已就绪、无需再让用户重发
            file_size_kb = local_path.stat().st_size // 1024
            user_text = (
                f"[用户通过 Telegram 上传了文件，文件已自动下载到本机]\n"
                f"  文件名：{filename}\n"
                f"  本地路径：{local_path}  （此路径真实有效，可直接传给工具使用）\n"
                f"  文件大小：{file_size_kb} KB"
            )
            if extra_context:
                user_text += extra_context
            elif extract_error:
                user_text += f"\n\n  （文件文本提取失败：{extract_error}；可用 parse_local_file 或 read_assignment_file 工具重试）"
            elif suffix == ".pdf":
                user_text += f"\n\n  （可用 parse_local_file 或 read_assignment_file 工具读取 PDF 内容）"

            user_text += "\n\n⚠️ 注意：文件已在本机就绪，不要让用户重新发路径或重新上传，直接处理即可。"
            user_text += f"\n\n用户说：{caption}" if caption else "\n\n（用户未附加说明，请询问需要对这个文件做什么）"

            with lock:
                reply = _capture_turn(sess, user_text)
            _send_chunks(chat_id, _md_to_tg_html(reply), parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"❌ 文件处理出错：{e}")
        finally:
            stop_typing.set()

    threading.Thread(target=run, daemon=True).start()


# ── 图片消息处理 ──────────────────────────────────────────────────────────────

@bot.message_handler(content_types=["photo"])
def handle_photo(msg):
    chat_id = msg.chat.id
    if not _is_authorized(chat_id, msg):
        return

    caption = (msg.caption or "").strip()
    # 取最高分辨率（photos[-1]）
    photo   = msg.photo[-1]
    lock = _locks.get(chat_id) or _get_session(chat_id) and _locks[chat_id]
    if lock.locked():
        bot.reply_to(msg, "⏳ 上一条消息还在处理中，请稍候…")
        return

    bot.send_chat_action(chat_id, "typing")

    def run():
        sess = _get_session(chat_id)
        stop_typing = threading.Event()
        threading.Thread(target=_keep_typing, args=(chat_id, stop_typing), daemon=True).start()
        try:
            local_path = _download_tg_file(photo.file_id, f"photo_{photo.file_id[:8]}.jpg")
            model = sess["model_box"][0]

            if _model_supports_vision(model):
                # 读取图片并 base64 编码，构造多模态消息
                img_bytes = local_path.read_bytes()
                b64 = base64.b64encode(img_bytes).decode()
                content: list = []
                if caption:
                    content.append({"type": "text", "text": caption})
                else:
                    content.append({"type": "text", "text": "（用户发送了一张图片，请描述图片内容或询问用户需要做什么）"})
                # Anthropic 与 OpenAI 的图片块格式不同，按当前模型选择
                if agent._is_anthropic_model(model):
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    })
                else:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    })
                with lock:
                    reply = _capture_turn_multimodal(sess, content)
            else:
                # 不支持视觉的模型：以文本说明代替
                user_text = (
                    f"[用户通过 Telegram 发送了一张图片]\n"
                    f"图片已保存到本地：{local_path}\n"
                    f"（当前模型 {model} 不支持图片输入，无法直接查看图片内容）"
                    + (f"\n\n用户说：{caption}" if caption else "\n\n（用户未附加说明）")
                )
                with lock:
                    reply = _capture_turn(sess, user_text)

            _send_chunks(chat_id, _md_to_tg_html(reply), parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"❌ 图片处理出错：{e}")
        finally:
            stop_typing.set()

    threading.Thread(target=run, daemon=True).start()


# ── 普通消息处理 ──────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    chat_id = msg.chat.id
    cfg = _load_cfg()
    if not cfg.get("telegram_enabled", True):
        return
    if not _is_authorized(chat_id, msg):
        return
    if not msg.text:
        bot.reply_to(msg, "暂不支持该类型消息，请发送文字、图片或文件（PDF）。")
        return

    sess = _get_session(chat_id)
    lock = _locks[chat_id]

    if lock.locked():
        bot.reply_to(msg, "⏳ 上一条消息还在处理中，请稍候…")
        return

    user_text = msg.text.strip()

    # ── 预拦截：高频问题直接调工具，跳过 LLM ─────────────────────────────
    quick = _try_quick_intent(user_text)
    if quick is not None:
        _, html_reply = quick
        try:
            bot.send_chat_action(chat_id, "typing")
            _send_chunks(chat_id, html_reply, parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"❌ 出错了：{e}")
        return

    bot.send_chat_action(chat_id, "typing")

    def run():
        # 进度状态消息：用 edit_message_text 反复更新
        progress_msg = None
        try:
            progress_msg = bot.send_message(chat_id, "⏳ 正在思考…")
        except Exception:
            progress_msg = None

        progress_lines: list[str] = []
        last_edit_at = [0.0]
        EDIT_THROTTLE_SEC = 0.6

        import time as _t

        def _edit_progress(force: bool = False):
            if progress_msg is None:
                return
            now = _t.monotonic()
            if not force and (now - last_edit_at[0] < EDIT_THROTTLE_SEC):
                return
            last_edit_at[0] = now
            text = "\n".join(progress_lines) or "⏳ 正在思考…"
            try:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=text,
                    parse_mode="HTML",
                )
            except Exception:
                pass  # 编辑失败（被删/限流）忽略，最终回复仍会单独发

        def on_progress(event_type: str, payload: dict):
            if event_type == "tool_start":
                label = agent._TOOL_LABELS.get(payload["name"], payload["name"])
                progress_lines.append(f"⚙️ {label}…")
                _edit_progress()
            elif event_type == "tool_end":
                if progress_lines:
                    label = agent._TOOL_LABELS.get(payload["name"], payload["name"])
                    elapsed = payload.get("elapsed_ms", 0)
                    sec = elapsed / 1000
                    progress_lines[-1] = f"✅ {label}（{sec:.1f}s）"
                    _edit_progress(force=True)
            elif event_type == "first_token":
                progress_lines.append("💬 开始生成回复…")
                _edit_progress(force=True)

        def on_tool_result(tool_name: str, fn_args: dict, result_str: str):
            """工具执行完后，如果是文件下载类工具，把文件发给用户。"""
            if tool_name not in ("download_assignments",):
                return
            import json as _j
            try:
                data = _j.loads(result_str)
            except Exception:
                return
            # files 是字符串路径列表（来自 ddl_checker.download_assignments）
            for item in data.get("items", []):
                for f in item.get("files", []):
                    path_str = f if isinstance(f, str) else f.get("path", "")
                    if not path_str:
                        continue
                    p = Path(path_str)
                    if not p.exists():
                        continue
                    try:
                        with p.open("rb") as fh:
                            bot.send_document(chat_id, fh, visible_file_name=p.name)
                    except Exception as e:
                        bot.send_message(chat_id, f"⚠️ 发送文件 {p.name} 失败：{e}")

        try:
            with lock:
                reply = _streamed_turn(sess, user_text, on_progress, on_tool_result)
            html_reply = _md_to_tg_html(reply) if reply else "(已完成)"
            # 删除进度消息，发送最终回复（避免长留闪烁信息）
            if progress_msg is not None:
                try:
                    bot.delete_message(chat_id, progress_msg.message_id)
                except Exception:
                    pass
            _send_chunks(chat_id, html_reply, parse_mode="HTML")
        except Exception as e:
            try:
                if progress_msg is not None:
                    bot.delete_message(chat_id, progress_msg.message_id)
            except Exception:
                pass
            bot.send_message(chat_id, f"❌ 出错了：{e}")

    threading.Thread(target=run, daemon=True).start()


# ── 公共函数（供 remind_check.py 调用） ──────────────────────────────────────

def send_reminder_via_telegram(title: str, subtitle: str, body: str) -> None:
    """
    向 telegram_allowed_ids 中所有用户推送提醒通知。
    由 remind_check.py 在找到匹配提醒时调用。
    """
    allowed = set(int(x) for x in _load_cfg().get("telegram_allowed_ids", []))
    if not allowed:
        return
    text = f"🔔 <b>{title}</b>\n<i>{subtitle}</i>"
    if body:
        text += f"\n{body}"
    _bot = telebot.TeleBot(BOT_TOKEN)
    for uid in allowed:
        try:
            _bot.send_message(uid, text, parse_mode="HTML")
        except Exception as e:
            print(f"[WARN] Telegram 推送失败 uid={uid}: {e}")


# ── 入口 ──────────────────────────────────────────────────────────────────────

import time as _time


def _wait_for_network(max_wait: int = 120, interval: int = 5) -> "telebot.types.User":
    """等待网络就绪后获取 bot 信息，开机时 DNS 可能尚未就绪，故加重试。"""
    deadline = _time.monotonic() + max_wait
    attempt = 0
    while True:
        attempt += 1
        try:
            return bot.get_me()
        except Exception as e:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                print(f"❌ 网络等待超时（{max_wait}s），最后一次错误：{e}")
                raise
            wait = min(interval, remaining)
            print(f"[WARN] 第 {attempt} 次连接失败（网络未就绪？），{wait:.0f}s 后重试：{e}")
            _time.sleep(wait)


if __name__ == "__main__":
    if "--test" in sys.argv:
        me = bot.get_me()
        print(f"✅ Bot 连通正常：@{me.username}（{me.first_name}）")
        print(f"   白名单：{ALLOWED_IDS or '(未设置，任何人发消息都会看到提示)'}")
        sys.exit(0)

    me = _wait_for_network()
    print(f"✅ @{me.username} 已启动")
    print(f"   白名单：{ALLOWED_IDS or '(未设置)'}")

    # ── 启动上线通知 ──────────────────────────────────────────────────────────
    if ALLOWED_IDS:
        startup_time = _dt.datetime.now().strftime("%H:%M")
        startup_text = (
            f"✅ <b>SJTU Agent 已上线</b>  {startup_time}\n"
            f"直接发消息开始对话，或输入 /help 查看命令。"
        )
        for _uid in ALLOWED_IDS:
            try:
                bot.send_message(_uid, startup_text, parse_mode="HTML")
            except Exception as _e:
                print(f"[WARN] 上线通知发送失败 uid={_uid}: {_e}")

    # 向 Telegram 服务器注册命令列表，用户输入 / 时会弹出自动补全菜单
    from telebot.types import BotCommand
    bot.set_my_commands([
        BotCommand("report",       "📊 立即生成今日学习日报"),
        BotCommand("reminders",    "📌 查看提醒事项列表"),
        BotCommand("news",         "📰 立即获取今日新闻日报"),
        BotCommand("news_block",   "🚫 屏蔽某类新闻（如：/news_block 二手交易）"),
        BotCommand("news_reset",   "🔄 重置新闻推荐画像"),
        BotCommand("reset",        "🔄 重置对话历史"),
        BotCommand("id",           "🔑 显示我的 chat_id"),
        BotCommand("help",         "❓ 帮助与命令列表"),
    ])
    print("   已注册 8 条命令")
    print(f"   等待消息… （Ctrl+C 停止）")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
