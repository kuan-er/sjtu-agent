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
            fn_args = json.loads(tc["function"]["arguments"] or "{}")
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
                      "tools": tools, "max_tokens": 4096, "stream": True},
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
                                    try:
                                        blk["input"] = _json.loads(tool_inputs[bidx] or "{}")
                                    except Exception:
                                        blk["input"] = {}

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
            model=model, system=system, messages=api_msgs, max_tokens=4096,
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
    from sjtu_agent.agent.context import trim_session
    trim_session(messages)
    if _is_anthropic_model(model):
        _run_one_turn_anthropic(client, model, messages)
    else:
        _run_one_turn_openai(client, model, messages)

