"""sjtu_agent/agent/runner.py — LLM 客户端、流式处理、tool_use 循环。

包含：
- Spinner 终端进度指示器
- _make_client / _is_anthropic_model
- _stream_with_think_tags（OpenAI 思考标签处理）
- _run_one_turn_openai / _run_one_turn_anthropic / _run_one_turn
"""
from __future__ import annotations

import itertools
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from openai import OpenAI
from anthropic import Anthropic

from sjtu_agent.paths import AGENT_CONFIG_PATH, ENV_PATH
from sjtu_agent.terminal_ui import print_markdown_message, print_rule
from sjtu_agent.agent.prompts import _TOOL_LABELS

# ── Loop 边界（Phase 5）：迭代预算 + 网络重试上限 ────────────────────────────
# 防止模型无限调工具 / 网络持续失败导致死循环。
_MAX_TOOL_ITERATIONS = 8   # 单轮最多工具调用迭代次数，超出后收敛
_MAX_NETWORK_RETRIES = 2   # 网络/超时重试上限

# ── 单轮输出上限（2026 口径）─────────────────────────────────────────────────
# 旧值 4096 是 GPT-3.5 时代的产物：2026 主流模型输出上限 128K~384K（DeepSeek
# V4.1-Flash 为 384K），而推理模型的思考 token 与正文**共用**这一预算——本机
# 实测思考占输出 40%（外部对 Opus 单答案任务的实测可达 98%）。4096 会把思考
# 挤爆：v0.24.0 的日报空回复事故（#201）根因即在此。
# 默认 16K 对校园问答足够，又不会让单轮生成无界变贵；可用环境变量覆盖。
# ── 单轮输出上限（2026 口径，自适应）────────────────────────────────────────
# 旧值 4096 是 GPT-3.5 时代的产物：2026 主流模型输出上限 128K~384K，而推理模型的
# 思考 token 与正文**共用**这一配额——本机实测思考占输出 40%（外部对 Opus 单答案
# 任务的实测可达 98%）。4096 会把思考挤爆：v0.24.0 日报空回复事故（#201）根因即此。
#
# 但也不能固定按厂商上限发：max_tokens 与 prompt **共享同一个窗口**，
# DeepSeek V4.1-Flash 的上限是 393,216（384K，思考与正文共享），若在一轮大上下文
# 里照样发满，prompt + max_tokens 超过窗口会被后端以 400 拒绝。所以取
# min(厂商上限, 窗口 − 已用 prompt − 安全余量)，并保底不低于下限。
_PROVIDER_OUTPUT_CAPS: tuple[tuple[str, int], ...] = (
    ("deepseek", 393_216),   # V4.1-Flash：384K = 393,216（可通过 max_tokens 调 1~393216）
    ("claude", 128_000),     # Opus/Sonnet 5：128K（Batch 300K 走 beta 头）
    ("gpt-6", 128_000),
    ("gpt-5", 128_000),
    ("gemini", 65_536),
    ("qwen", 131_072),
    ("kimi", 131_072),
    ("glm", 128_000),
)
_OUTPUT_CAP_UNKNOWN = 8_192    # 认不出的后端：保守（该后端上限未公开）
_OUTPUT_CAP_FLOOR = 1_024      # 任何情况下都不低于此值
_OUTPUT_SAFETY_MARGIN = 2_000  # 给 prompt 估算误差留的余量
_MAX_OUTPUT_TOKENS_ENV = "SJTU_MAX_OUTPUT_TOKENS"


def _provider_output_cap(model: str) -> int:
    """厂商/模型公布的单轮输出上限；认不出的给保守值。"""
    name = (model or "").lower()
    for keyword, cap in _PROVIDER_OUTPUT_CAPS:
        if keyword in name:
            return cap
    return _OUTPUT_CAP_UNKNOWN


def _estimate_prompt_tokens(messages: list, system: str = "", tools=None) -> int:
    """粗估本轮 prompt 的 token 量，用于给输出留余量。

    复用 context 的估算（图片按固定视觉成本计，**不**按 base64 长度算，
    否则一张图就会把输出上限压到下限）。
    """
    from sjtu_agent.agent.context import _estimate_tokens, _session_history_cost

    total = _session_history_cost(messages)
    total += _estimate_tokens(system or "")
    if tools:
        total += _estimate_tokens(json.dumps(tools, ensure_ascii=False))
    return total


def _max_output_tokens(
    base_url: str = "", model: str = "", *, prompt_tokens: int = 0
) -> int:
    """自适应单轮输出上限 = min(厂商上限, 窗口 − prompt − 余量)，夹在 [下限, 上限]。

    致远一号这类网关也按所承载模型的上限算（`deepseek-chat` → 393,216），
    不再因为"网关上限没公开"就只给 8K；窗口本身仍按后端解析（网关 512k）。
    """
    raw = os.environ.get(_MAX_OUTPUT_TOKENS_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)

    from sjtu_agent.agent.context import model_context_window

    cap = _provider_output_cap(model)
    window = model_context_window(model, base_url)
    remaining = window - max(0, int(prompt_tokens)) - _OUTPUT_SAFETY_MARGIN
    return max(_OUTPUT_CAP_FLOOR, min(cap, remaining))


def _is_max_tokens_rejection(message: str) -> bool:
    """错误信息是否指向"max_tokens 不被接受/超出允许范围"。"""
    text = (message or "").lower()
    return any(
        marker in text
        for marker in ("max_tokens", "max output tokens", "max_completion_tokens")
    )


def _client_base_url(client) -> str:
    """取客户端实际使用的 base_url（OpenAI / Anthropic SDK 都暴露 .base_url）。

    预算与输出上限都要按**后端**而不是模型名来定：同一个调用名在官方端点与
    校园网关上的窗口不同（官方 DeepSeek 1M vs 致远一号 512k）。
    """
    return str(getattr(client, "base_url", "") or "")


def _get_tools():
    """Lazy import：内置工具 + MCP 服务器动态工具（registry 聚合）。

    修复（issue #149-2）：之前只返回静态 TOOLS，add_mcp_server 写入的
    MCP 服务器工具永远不会进入发给模型的工具列表（配置写了、bot 也重启了，
    但工具列表里始终没有 mcp__*）。registry.get_available_tools 自带 60s
    TTL 缓存 + 坏 server 状态工具，不会每轮重复连接。
    """
    from sjtu_agent.extensions.registry import get_available_tools
    return get_available_tools()


def _get_run_tool():
    """Lazy import run_tool，避免循环依赖。"""
    from sjtu_agent.agent.tools import run_tool
    return run_tool


def _ansi_supported() -> bool:
    """
    检测当前终端是否值得开启 \r 覆盖式 Spinner 动画。

    Windows 上即使 ANSI 转义序列可用（Windows Terminal / VS Code 终端），
    Spinner 线程的 \\r 写入仍会与 login.py / Playwright 的 print() 产生
    竞争，导致输出闪烁和乱码。因此 Windows 一律禁用动画，降级为单行静态文字。
    """
    if sys.platform == "win32":
        return False
    return True

_ANSI_OK: bool | None = None  # lazy-init

class Spinner:
    """在终端同一行显示动态转圈动画，stop() 后清除该行。
    在不支持 ANSI 的终端（Windows cmd）自动退化为静态文本行。
    每次 start 前先打印一个空行，避免 \\r 覆盖上一行用户输入。
    """
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, msg: str = ""):
        self._msg   = msg
        self._stop  = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

    def _ansi(self) -> bool:
        global _ANSI_OK
        if _ANSI_OK is None:
            _ANSI_OK = _ansi_supported()
        return _ANSI_OK

    def start(self, msg: str = "") -> "Spinner":
        if msg:
            self._msg = msg
        if self._started:
            # 已在运行，只更新消息
            return self
        self._stop.clear()
        self._started = True
        if self._ansi():
            # 先换行，确保 \r 不回到用户输入行
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            # Windows 无 ANSI：只打印一行文字
            print(f"… {self._msg}")
        return self

    def update(self, msg: str) -> None:
        self._msg = msg

    def stop(self, final: str = "") -> None:
        self._stop.set()
        self._started = False
        if self._thread:
            self._thread.join()
            self._thread = None
        if self._ansi():
            # 清除整行（包括开头的换行占位）
            sys.stdout.write("\r\033[K")
        if final:
            sys.stdout.write(final + "\n")
        sys.stdout.flush()

    def _spin(self) -> None:
        for frame in itertools.cycle(self._FRAMES):
            if self._stop.is_set():
                break
            sys.stdout.write(f"\r{frame} {self._msg}")
            sys.stdout.flush()
            time.sleep(0.08)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()



def _is_anthropic_model(model: str) -> bool:
    return model.startswith("claude")


def _make_client(cfg: dict):
    """根据模型名自动选择 OpenAI 或 Anthropic SDK。"""
    if _is_anthropic_model(cfg.get("model", "")):
        # openclaudecode.cn 等代理服务会拦截 Anthropic SDK 默认 UA，需覆盖为 Claude CLI 风格
        ua = cfg.get("user_agent", "claude-cli/1.0.57")
        return Anthropic(
            api_key=cfg["api_key"],
            base_url=cfg.get("base_url") or None,
            default_headers={"user-agent": ua},
        )
    return OpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url") or None)


def _anthropic_tools() -> list:
    """将 OpenAI 工具格式转换为 Anthropic 格式。"""
    result = []
    for t in _get_tools():
        fn = t["function"]
        result.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn["parameters"],
        })
    return result


# deepseek 模型的原生工具调用标记（服务端流式偶发未解析，混入正文）
_DSML_BLOCK_RE = re.compile(
    r"<[｜|]*DSML[｜|]* calls>.*?<[｜|]*/[｜|]*DSML[｜|]* calls>"
    r"|<[｜|]*DSML[｜|]*[^>]*>.*?<[｜|]*/[｜|]*DSML[｜|]*[^>]*>"
    r"|<[｜|]*DSML[｜|]*[^>]*>.*$",
    re.S,
)


def _strip_dsml_blocks(text: str) -> str:
    """剥离正文里混入的 DSML 工具调用标记，保留 surrounding 正文。"""
    if not text or "DSML" not in text:
        return text
    return _DSML_BLOCK_RE.sub("", text).strip()


def _parse_tool_args(raw: object) -> tuple[dict | None, str]:
    """解析工具调用参数；损坏时尝试修复尾随垃圾，仍失败返回 (None, 原因)。

    DeepSeek 流式偶发把 arguments 截断或混入垃圾字符，json.loads 直接抛
    JSONDecodeError 会杀死整轮对话并把异常文本漏给用户（飞书端表现为
    「出错了：Expecting ',' delimiter …」且本轮无回复）。
    """
    text = (raw or "{}").strip() if isinstance(raw, str) else "{}"
    try:
        args = json.loads(text)
        return (args if isinstance(args, dict) else {}, "")
    except json.JSONDecodeError as e:
        # 修复尾部垃圾：若存在合法 JSON 前缀则采用（如 '{"q":"x"} …杂音'）
        try:
            args, _ = json.JSONDecoder().raw_decode(text)
            if isinstance(args, dict):
                return args, ""
        except ValueError:
            pass
        return None, f"line {e.lineno} column {e.colno}（{e.msg}）"


def _repair_dangling_tool_calls(messages: list) -> int:
    """补齐因异常中断而悬空的工具调用（否则后续每轮都被 API 400 拒绝）。

    OpenAI 格式：assistant.tool_calls 的每个 id 必须有对应 tool 消息；
    Anthropic 格式：assistant 的 tool_use 块必须有含对应 tool_result 的
    user 消息。这里给缺失的补一条"异常中断"错误结果，让会话自愈。
    """
    repaired = 0
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            have = {
                later.get("tool_call_id")
                for later in messages[i + 1:]
                if later.get("role") == "tool"
            }
            missing = [tc["id"] for tc in m["tool_calls"] if tc["id"] not in have]
            for idx, tc_id in enumerate(missing):
                messages.insert(i + 1 + idx, {
                    "role": "tool", "tool_call_id": tc_id,
                    "content": "（上一轮该工具调用因异常中断，没有得到结果。）",
                })
                repaired += 1
            i += 1 + len(missing)
        elif m.get("role") == "assistant" and isinstance(m.get("content"), list):
            tool_use_ids = [
                b.get("id") for b in m["content"]
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            if tool_use_ids:
                have = set()
                for later in messages[i + 1:]:
                    if later.get("role") == "user" and isinstance(later.get("content"), list):
                        have |= {
                            b.get("tool_use_id") for b in later["content"]
                            if isinstance(b, dict) and b.get("type") == "tool_result"
                        }
                missing = [tid for tid in tool_use_ids if tid not in have]
                if missing:
                    results = [
                        {"type": "tool_result", "tool_use_id": tid,
                         "content": "（上一轮该工具调用因异常中断，没有得到结果。）"}
                        for tid in missing
                    ]
                    messages.insert(i + 1, {"role": "user", "content": results})
                    repaired += len(missing)
                    i += 1
        i += 1
    return repaired


def _stream_with_think_tags(stream, spinner: "Spinner") -> tuple[str, str, dict]:
    """
    消费 OpenAI 兼容的流式响应，处理两种思考格式：
      1. delta.reasoning_content 字段（DeepSeek-R1 原生）
      2. <think>...</think> XML 标签混在 content 中（minimax / 部分模型）

    思考内容实时以暗体灰字流式输出（先停 Spinner，避免并发写屏乱码）。
    正文内容全部缓冲，流结束后由调用方统一用 print_markdown_message 渲染。

    关键：在开始写思考文字前先停 Spinner，思考结束后重启 Spinner 等待正文。
    这样消除了 Spinner 的 \\r 和 write() 并发竞争导致的闪烁。

    返回：(full_content_no_think, full_reasoning, tool_calls_map)
    """
    full_content   = ""   # 包含 <think> 的原始正文（用于存入 messages）
    full_reasoning = ""   # 思考内容（展示并收集）
    tool_calls_map: dict[int, dict] = {}

    TAG_OPEN  = "<think>"
    TAG_CLOSE = "</think>"
    in_think = False   # 当前是否在 <think> 块内
    thinking_started = False  # 是否已打印过思考前缀

    def _start_thinking():
        nonlocal thinking_started
        if thinking_started:
            return
        spinner.stop()  # ← 关键：先停 Spinner，再输出文字，避免 \r 覆盖
        if spinner._ansi():
            sys.stdout.write("\033[2m💭 ")  # 暗体灰字前缀（ANSI 支持时）
        else:
            sys.stdout.write("💭 思考中：")   # Windows 纯文本前缀
        sys.stdout.flush()
        thinking_started = True

    def _end_thinking():
        nonlocal thinking_started, in_think
        if thinking_started:
            if spinner._ansi():
                sys.stdout.write("\033[0m\n")  # 重置颜色，换行
            else:
                sys.stdout.write("\n")         # Windows：直接换行
            sys.stdout.flush()
            thinking_started = False
        in_think = False
        # 注意：不在这里重启 Spinner；由调用方在流结束后统一 stop/render


    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            continue

        # ── reasoning_content 字段（DeepSeek-R1 / Qwen 原生）────────────
        rc = getattr(delta, "reasoning_content", None) or ""
        if rc:
            _start_thinking()
            sys.stdout.write(rc)
            sys.stdout.flush()
            full_reasoning += rc

        # ── content 字段 ────────────────────────────────────────────────
        text_chunk = delta.content or ""
        if text_chunk:
            full_content += text_chunk

            # 处理 <think> 标签
            if TAG_OPEN in text_chunk and not in_think:
                in_think = True
                # 取 <think> 之后的内容
                after = text_chunk[text_chunk.index(TAG_OPEN) + len(TAG_OPEN):]
                if after:
                    _start_thinking()
                    sys.stdout.write(after)
                    sys.stdout.flush()
                    full_reasoning += after
            elif TAG_CLOSE in text_chunk and in_think:
                # 取 </think> 之前的内容
                before = text_chunk[:text_chunk.index(TAG_CLOSE)]
                if before:
                    _start_thinking()
                    sys.stdout.write(before)
                    sys.stdout.flush()
                    full_reasoning += before
                _end_thinking()
            elif in_think:
                # 在思考块内部
                _start_thinking()
                sys.stdout.write(text_chunk)
                sys.stdout.flush()
                full_reasoning += text_chunk
            else:
                # 普通正文：若之前有 reasoning_content 思考，先结束思考显示
                if thinking_started:
                    _end_thinking()

        # ── 工具调用 ─────────────────────────────────────────────────────
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                entry = tool_calls_map[idx]
                if tc_delta.id:
                    entry["id"] += tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        entry["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        entry["arguments"] += tc_delta.function.arguments

    # 流结束时若还在思考状态，收尾
    if thinking_started:
        _end_thinking()

    # 从 full_content 中剥离 <think>...</think> 块，得到纯正文
    clean_content = re.sub(r"<think>.*?</think>", "", full_content, flags=re.DOTALL).strip()
    return clean_content, full_reasoning, tool_calls_map


def _run_one_turn_openai(client: OpenAI, model: str, messages: list) -> None:
    """流式输出版本：
    - 思考过程（reasoning_content 或 <think> 标签）实时灰色显示
    - 正文内容流式缓冲，结束后用 print_markdown_message 统一渲染 markdown
    """
    spinner = Spinner()
    iteration = 0
    retries = 0

    while True:
        iteration += 1
        if iteration > _MAX_TOOL_ITERATIONS:
            break  # 迭代预算耗尽 → 收敛（下方 _converge_openai）

        # ── 流式请求 ────────────────────────────────────────────────────────
        spinner.start("等待响应…")
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, tools=_get_tools(), tool_choice="auto",
                timeout=180, stream=True,
            )
        except Exception as e:
            spinner.stop()
            err = str(e).lower()
            if ("timeout" in err or "timed out" in err or "read" in err) and retries < _MAX_NETWORK_RETRIES:
                retries += 1
                import time as _time
                print(f"\r[提示] 网络超时，5 秒后重试…（{e}）")
                _time.sleep(5)
                continue
            raise
        try:
            clean_content, _reasoning, tool_calls_map = _stream_with_think_tags(stream, spinner)
        except Exception as e:
            spinner.stop()
            raise
        spinner.stop()  # 无思考内容时 _stream_with_think_tags 不会停 spinner，在此兜底

        # ── DSML 防御：deepseek 流式偶发把工具调用以原生 DSML 标记混入正文
        # （服务端未解析为结构化 tool_calls），原样返回会直接漏给用户。
        # 处理：剥离标记；若本轮没有任何结构化工具调用，则要求模型重发。
        if clean_content and "DSML" in clean_content:
            clean_content = _strip_dsml_blocks(clean_content)
            if not tool_calls_map:
                if clean_content:
                    print_markdown_message("Agent", clean_content)
                messages.append({"role": "assistant", "content": clean_content or None})
                messages.append({
                    "role": "user",
                    "content": (
                        "（系统提示：你上一条回复里的工具调用以 DSML 标记原文混入了正文，"
                        "没有被服务端解析为工具调用，用户不会看到它执行。"
                        "请把同样的工具调用重新发起一次——必须走结构化 tool_calls 通道，"
                        "不要把 DSML 标记写进回复文本。）"
                    ),
                })
                continue

        # ── 渲染正文（markdown）──────────────────────────────────────────
        if clean_content:
            print_markdown_message("Agent", clean_content)

        # ── 纯文本回复（无工具调用）─────────────────────────────────────
        if not tool_calls_map:
            _msg = {"role": "assistant", "content": clean_content}
            # DeepSeek 思考模型（deepseek-v4-flash 等）要求 reasoning_content 必须回传
            if _reasoning:
                _msg["reasoning_content"] = _reasoning
            messages.append(_msg)
            return

        # ── 有工具调用：构建 assistant 消息并执行 ───────────────────────
        # 用 dict 而非 ChatCompletionMessage 对象，方便附带 reasoning_content
        tool_calls_payload = []
        for idx in sorted(tool_calls_map):
            e = tool_calls_map[idx]
            tool_calls_payload.append({
                "id": e["id"],
                "type": "function",
                "function": {"name": e["name"], "arguments": e["arguments"]},
            })

        assistant_msg = {
            "role": "assistant",
            "content": clean_content or None,
            "tool_calls": tool_calls_payload,
        }
        if _reasoning:
            assistant_msg["reasoning_content"] = _reasoning
        messages.append(assistant_msg)

        for tc in tool_calls_payload:
            fn_name = tc["function"]["name"]
            fn_args, parse_err = _parse_tool_args(tc["function"]["arguments"])
            if fn_args is None:
                # DeepSeek 流式偶发参数截断/损坏（JSONDecodeError）：不再让整轮
                # 死掉并把异常文本漏给用户——回填错误结果，模型在迭代预算内重试
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": (
                        f"（工具 {fn_name} 的参数解析失败：{parse_err}。"
                        f"请重新调用 {fn_name}，arguments 必须是完整、合法的 JSON 对象。）"
                    ),
                })
                continue
            if fn_name not in ("check_setup",):
                spinner.start(_TOOL_LABELS.get(fn_name, fn_name) + "…")
            result = _get_run_tool()(fn_name, fn_args)
            if fn_name not in ("check_setup",):
                spinner.stop()
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    # 迭代预算耗尽：无工具调用，强制模型合成最终回复
    _converge_openai(client, model, messages)


def _converge_openai(client: OpenAI, model: str, messages: list) -> None:
    """迭代预算耗尽时收敛：无工具调用，强制生成最终回复（流式，保持 UX）。"""
    spinner = Spinner()
    spinner.start("已达到工具调用上限，正在汇总…")
    full = ""
    try:
        stream = client.chat.completions.create(
            model=model, messages=messages, timeout=180, stream=True,
        )
        full, _reasoning, _tcm = _stream_with_think_tags(stream, spinner)
    except Exception:
        full = ""
    finally:
        spinner.stop()
    if full:
        print_markdown_message("Agent", full)
    messages.append({
        "role": "assistant",
        "content": full or "(已达工具调用上限，未能完成任务。请尝试把任务拆小，或分步询问。)",
    })


def _run_one_turn_anthropic(client: Anthropic, model: str, messages: list) -> None:
    """流式调用 Anthropic Messages API（SSE），实时显示 thinking block 和正文。"""
    import httpx as _httpx
    import json as _json
    spinner = Spinner()
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    tools  = _anthropic_tools()

    api_key  = client.api_key
    base_url = str(client.base_url).rstrip("/")
    ua       = (client.default_headers or {}).get("user-agent", "claude-cli/1.0.57")
    endpoint = f"{base_url}/v1/messages"
    req_headers = {
        "x-api-key":          api_key,
        "anthropic-version":  "2023-06-01",
        "content-type":       "application/json",
        "user-agent":         ua,
    }

    iteration = 0
    retries = 0
    max_tokens_downgrades = 0      # 后端拒绝 max_tokens 时的降档次数（最多 2 次）
    output_cap_ceiling = 0         # 降档后记住的上限（0 = 尚未降档）

    while True:
        iteration += 1
        if iteration > _MAX_TOOL_ITERATIONS:
            break  # 迭代预算耗尽 → 收敛（下方 _converge_anthropic）

        api_msgs = []
        for m in messages:
            if m["role"] == "system":
                continue
            c = m.get("content")
            # 过滤掉历史里非法的空消息（防止之前留下的污染消息导致 400）
            if c is None:
                continue
            if isinstance(c, str) and not c.strip():
                continue
            if isinstance(c, list):
                non_empty = []
                for blk in c:
                    if not isinstance(blk, dict):
                        continue
                    btype = blk.get("type")
                    if btype == "text" and not (blk.get("text") or "").strip():
                        continue
                    non_empty.append(blk)
                if not non_empty:
                    continue
                api_msgs.append({"role": m["role"], "content": non_empty})
            else:
                api_msgs.append(m)
        spinner.start("等待响应…")

        # 自适应输出上限：随本轮 prompt 增长而收缩，避免 prompt + max_tokens 超窗口。
        # 若后端拒绝过某个值，用 output_cap_ceiling 记住降档结果（否则重试时又被算回去）。
        adaptive_cap = _max_output_tokens(
            _client_base_url(client), model,
            prompt_tokens=_estimate_prompt_tokens(messages, system, tools),
        )
        request_max_tokens = (
            min(adaptive_cap, output_cap_ceiling) if output_cap_ceiling else adaptive_cap
        )

        # ── SSE 流式请求 ────────────────────────────────────────────────────
        content_blocks: list[dict] = []     # 最终 assistant 消息内容
        tool_inputs: dict[int, str] = {}    # block_index -> accumulated JSON str
        in_thinking = False
        in_text     = False
        full_text   = ""
        error_payload: dict | None = None

        try:
            with _httpx.stream(
                "POST", endpoint,
                headers=req_headers,
                json={"model": model, "system": system, "messages": api_msgs,
                      "tools": tools, "max_tokens": request_max_tokens,
                      "stream": True},
                timeout=180,
            ) as resp:
                spinner.stop()

                if resp.status_code not in (200,):
                    body = resp.read().decode()
                    try:
                        error_payload = _json.loads(body)
                    except Exception:
                        error_payload = {"raw": body}
                else:
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            ev = _json.loads(data_str)
                        except Exception:
                            continue

                        ev_type = ev.get("type", "")

                        # 新 block 开始
                        if ev_type == "content_block_start":
                            block = ev.get("content_block", {})
                            btype = block.get("type", "")
                            bidx  = ev.get("index", len(content_blocks))
                            if btype == "thinking":
                                in_thinking = True
                                spinner.start("思考中…")  # Spinner 替代，隐藏思维链内容
                                content_blocks.append({"type": "thinking", "thinking": ""})
                            elif btype == "text":
                                # 文字 block 开始：停止思考 Spinner，换一个等待 Spinner
                                if in_thinking:
                                    spinner.stop()
                                    in_thinking = False
                                in_text = True
                                spinner.start("处理中…")
                                content_blocks.append({"type": "text", "text": ""})
                            elif btype == "tool_use":
                                content_blocks.append({
                                    "type": "tool_use",
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "input": {},
                                })
                                tool_inputs[bidx] = ""

                        # delta
                        elif ev_type == "content_block_delta":
                            delta = ev.get("delta", {})
                            dtype = delta.get("type", "")
                            bidx  = ev.get("index", 0)

                            if dtype == "thinking_delta":
                                chunk = delta.get("thinking", "")
                                # 只累积，不输出到终端（用 Spinner 代替，避免 ANSI 光标计算闪烁）
                                if content_blocks and content_blocks[-1].get("type") == "thinking":
                                    content_blocks[-1]["thinking"] += chunk

                            elif dtype == "text_delta":
                                chunk = delta.get("text", "")
                                # 只缓冲，不实时输出（等 block 结束后统一 markdown 渲染）
                                full_text += chunk
                                if content_blocks and content_blocks[-1].get("type") == "text":
                                    content_blocks[-1]["text"] += chunk

                            elif dtype == "input_json_delta":
                                tool_inputs[bidx] = tool_inputs.get(bidx, "") + delta.get("partial_json", "")

                        # block 结束
                        elif ev_type == "content_block_stop":
                            bidx = ev.get("index", 0)
                            # 把累积的 input JSON 解析回 dict
                            if bidx in tool_inputs and bidx < len(content_blocks):
                                blk = content_blocks[bidx]
                                if blk.get("type") == "tool_use":
                                    args, parse_err = _parse_tool_args(tool_inputs[bidx])
                                    # 解析失败用哨兵标记：执行阶段回填错误
                                    # tool_result 让模型重试，绝不带空参数执行
                                    blk["input"] = args if args is not None else {
                                        "__args_parse_error__": parse_err
                                    }

                        elif ev_type == "message_stop":
                            break

                        elif ev_type == "error":
                            error_payload = ev.get("error", ev)
                            break

        except (
            _httpx.ReadTimeout, _httpx.ConnectTimeout,
            _httpx.TimeoutException, _httpx.ConnectError,
            _httpx.RemoteProtocolError, _httpx.NetworkError,
        ) as e:
            spinner.stop()
            if in_thinking or in_text:
                sys.stdout.write("\033[0m\n")
                sys.stdout.flush()
            if retries < _MAX_NETWORK_RETRIES:
                retries += 1
                import time as _time
                print(f"\r[提示] 网络连接失败，5 秒后重试…（{type(e).__name__}: {e}）")
                _time.sleep(5)
                continue
            raise
        except Exception as e:
            spinner.stop()
            if in_thinking or in_text:
                sys.stdout.write("\033[0m\n")
                sys.stdout.flush()
            # 对于非预期异常，打印错误但不退出聊天循环
            print(f"\r[错误] 请求失败：{type(e).__name__}: {e}")
            return  # 返回到 chat_loop，让用户重新输入
        finally:
            spinner.stop()

        # ── 收尾渲染 ──────────────────────────────────────────────────────
        if in_thinking:
            spinner.stop()
            in_thinking = False
        if in_text and full_text:
            spinner.stop()  # 停止"处理中…" spinner，再渲染正文
            print_markdown_message("Agent", full_text)
        elif in_text:
            spinner.stop()

        # ── 错误处理 ──────────────────────────────────────────────────────
        if error_payload:
            import time as _time
            msg = (error_payload.get("message") or str(error_payload))[:200]
            if ("overload" in msg.lower() or "过载" in msg) and retries < _MAX_NETWORK_RETRIES:
                retries += 1
                print(f"\r[提示] 模型过载，10 秒后重试…")
                _time.sleep(10)
                continue
            if (error_payload.get("type") == "invalid_request_error" and "500" in str(error_payload)
                    and retries < _MAX_NETWORK_RETRIES):
                retries += 1
                _time.sleep(5)
                continue
            # 后端不认这么大的 max_tokens（网关硬上限 / 上下文余量比估计更紧）→ 降档重试
            if _is_max_tokens_rejection(msg) and max_tokens_downgrades < 2:
                max_tokens_downgrades += 1
                previous = request_max_tokens
                output_cap_ceiling = max(_OUTPUT_CAP_FLOOR, previous // 4)
                print(
                    f"\r[提示] 后端拒绝了 max_tokens={previous:,}，"
                    f"降到不超过 {output_cap_ceiling:,} 重试…"
                )
                continue
            raise RuntimeError(f"Anthropic API 错误: {msg}")

        # ── 判断是否有工具调用 ────────────────────────────────────────────
        has_tool_use = any(b.get("type") == "tool_use" for b in content_blocks)
        # 过滤空文本块，防止将非法空消息写入历史
        clean_blocks = []
        for b in content_blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and not (b.get("text") or "").strip():
                continue
            clean_blocks.append(b)
        if clean_blocks:
            messages.append({"role": "assistant", "content": clean_blocks})

        if not has_tool_use:
            return

        # ── 执行工具 ──────────────────────────────────────────────────────
        tool_results = []
        for b in content_blocks:
            if b.get("type") != "tool_use":
                continue
            fn_name = b["name"]
            fn_args = b["input"] if isinstance(b["input"], dict) else {}
            if not isinstance(fn_args, dict) or "__args_parse_error__" in fn_args:
                parse_err = (
                    fn_args.get("__args_parse_error__", "参数不是合法 JSON")
                    if isinstance(fn_args, dict) else "参数不是合法 JSON"
                )
                tool_results.append({
                    "type": "tool_result", "tool_use_id": b["id"],
                    "content": (
                        f"（工具 {fn_name} 的参数解析失败：{parse_err}。"
                        f"请重新调用 {fn_name}，参数必须是完整、合法的 JSON 对象。）"
                    ),
                })
                continue
            if fn_name not in ("check_setup",):
                spinner.start(_TOOL_LABELS.get(fn_name, fn_name) + "…")
            result = _get_run_tool()(fn_name, fn_args)
            if fn_name not in ("check_setup",):
                spinner.stop()
            tool_results.append({"type": "tool_result", "tool_use_id": b["id"], "content": result})
        messages.append({"role": "user", "content": tool_results})

    # 迭代预算耗尽：无工具调用强制合成最终回复
    _converge_anthropic(client, model, messages)


def _converge_anthropic(client: Anthropic, model: str, messages: list) -> None:
    """迭代预算耗尽时收敛：无工具调用，强制生成最终回复（非流式）。"""
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    api_msgs = [m for m in messages if m["role"] != "system" and m.get("content")]
    try:
        resp = client.messages.create(
            model=model, system=system, messages=api_msgs,
            max_tokens=_max_output_tokens(
                _client_base_url(client), model,
                prompt_tokens=_estimate_prompt_tokens(messages, system),
            ),
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception:
        text = ""
    if text:
        print_markdown_message("Agent", text)
    messages.append({
        "role": "assistant",
        "content": text or "(已达工具调用上限，未能完成任务。请尝试把任务拆小，或分步询问。)",
    })


def _run_one_turn(client, model: str, messages: list) -> None:
    # 上下文质量管理（Phase 2）：清理旧 tool 结果 + 超质量预算折叠最旧轮次。
    # 所有入口（bots / feishu / CLI）都经过这里，单点覆盖。
    # 预算按当前**后端 + 模型**计算（2026 口径：官方 DeepSeek 1M → 500K；
    # 致远一号 deepseek-chat 512k → 256K），见 context.context_budget。
    from sjtu_agent.agent.context import context_budget, trim_session
    trim_session(messages, budget=context_budget(model, base_url=_client_base_url(client)))
    _repair_dangling_tool_calls(messages)
    if _is_anthropic_model(model):
        _run_one_turn_anthropic(client, model, messages)
    else:
        _run_one_turn_openai(client, model, messages)

