#!/usr/bin/env python3
"""
feishu_bot.py — 将 agent.py 接入飞书（Lark）自建应用，长连接接收消息。

用法:
  python3 feishu_bot.py           # 正常运行（WebSocket 长连接）
  python3 feishu_bot.py --test    # 仅校验凭据
  python3 feishu_bot.py --whoami  # 启动 bot 并把每个发送者的 open_id 打到控制台

配置（config.json）:
  feishu_app_id              : 自建应用 App ID（cli_xxx）
  feishu_app_secret          : App Secret
  feishu_allowed_open_ids    : 允许使用的 open_id 列表；留空 [] 时所有人可用
                               （建议先留空，让 bot 把每条来访的 open_id 回显出来再加白名单）

事件订阅: im.message.receive_v1（接收消息 v2.0）
事件接收: 使用长连接（在飞书开放平台「事件与回调」中切换）
"""

import argparse
import concurrent.futures
import io
import json
import re
import sys
import threading
import time
import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sjtu_agent.paths import CONFIG_PATH

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
)

import agent


def _load_cfg() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


cfg = _load_cfg()
APP_ID = cfg.get("feishu_app_id", "").strip()
APP_SECRET = cfg.get("feishu_app_secret", "").strip()
_raw_allowed = cfg.get("feishu_allowed_open_ids", []) or []
if isinstance(_raw_allowed, str):
    try:
        _raw_allowed = json.loads(_raw_allowed)
    except Exception:
        _raw_allowed = []
ALLOWED_OPEN_IDS: set[str] = set(_raw_allowed)

if not APP_ID or not APP_SECRET:
    print("[X] config.json 中未设置 feishu_app_id / feishu_app_secret，请先在 WebUI 或 setup 中配置")
    sys.exit(1)


# ── 全局 API client（用来回复消息） ────────────────────────────────────────────
_api_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

# ── 后台线程池（LLM 推理在后台线程执行，避免阻塞 WS event loop） ─────────
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu")

# ── 多对话会话（每个 open_id 可拥有多个独立对话） ────────────────────────
_sessions: dict[str, dict] = {}
_locks: dict[str, threading.Lock] = {}
_sess_meta_lock = threading.Lock()

# ── 作业解答上下文（记住最近一次 /hw do，供"给我答案"使用）────────────────
_hw_context: dict[str, dict] = {}
_hw_ctx_lock = threading.Lock()

# ── 近期更新冷却期（防 Feishu 重发导致重复回复）──────────────────────────
_recent_updates_cooldown: dict[str, float] = {}
_cooldown_lock = threading.Lock()
_COOLDOWN_SEC = 10

# ── 会话持久化 ──────────────────────────────────────────────────────────────
from sjtu_agent.paths import DATA_DIR
_SESSIONS_FILE = DATA_DIR / "feishu_sessions.json"
_SAVE_LOCK = threading.Lock()
_MAX_SESSION_AGE_DAYS = 30


def _load_sessions() -> None:
    """从磁盘恢复会话状态。"""
    if not _SESSIONS_FILE.exists():
        return
    try:
        with _SAVE_LOCK:
            data = json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))
        cutoff = _dt.datetime.now().timestamp() - _MAX_SESSION_AGE_DAYS * 86400
        with _sess_meta_lock:
            for open_id, meta in data.items():
                convs = []
                for c in meta.get("conversations", []):
                    if c.get("saved_at", 0) < cutoff:
                        continue
                    agent_cfg = agent.load_agent_config()
                    c["model_box"] = [agent_cfg.get("model", "deepseek-chat")]
                    c["client_box"] = [agent._make_client(agent_cfg) if agent_cfg else None]
                    convs.append(c)
                if convs:
                    _sessions[open_id] = {
                        "conversations": convs,
                        "current_idx": min(meta.get("current_idx", 0), len(convs) - 1),
                        "next_name_id": meta.get("next_name_id", len(convs) + 1),
                    }
                    _locks[open_id] = threading.Lock()
        if _sessions:
            total = sum(len(m["conversations"]) for m in _sessions.values())
            print(f"[feishu] 已恢复 {len(_sessions)} 个用户的 {total} 个对话")
    except Exception as e:
        print(f"[feishu] 会话恢复失败: {e}")


def _save_sessions() -> None:
    """将当前会话状态保存到磁盘（只保存可序列化字段）。"""
    try:
        _SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        now_ts = _dt.datetime.now().timestamp()
        with _sess_meta_lock:
            data = {}
            for open_id, meta in _sessions.items():
                data[open_id] = {
                    "current_idx": meta["current_idx"],
                    "next_name_id": meta["next_name_id"],
                    "conversations": [{
                        "name": c["name"],
                        "messages": c["messages"][-200:],  # 只保留最近 200 条
                        "created_at": c["created_at"],
                        "saved_at": now_ts,
                    } for c in meta["conversations"]],
                }
        with _SAVE_LOCK:
            _SESSIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[feishu] 会话保存失败: {e}")


# 启动时恢复会话
_load_sessions()



def _new_conv_dict(name: str) -> dict:
    agent_cfg = agent.load_agent_config()
    return {
        "name": name,
        "messages": [],
        "model_box": [agent_cfg.get("model", "deepseek-chat")],
        "client_box": [agent._make_client(agent_cfg) if agent_cfg.get("api_key") else None],
        "created_at": _dt.datetime.now().strftime("%m-%d %H:%M"),
    }


def _ensure_user(open_id: str) -> None:
    with _sess_meta_lock:
        if open_id not in _sessions:
            _sessions[open_id] = {
                "conversations": [_new_conv_dict("默认")],
                "current_idx": 0,
                "next_name_id": 1,
            }
            _locks[open_id] = threading.Lock()


def _get_active_conv(open_id: str) -> tuple[dict, dict, threading.Lock]:
    _ensure_user(open_id)
    with _sess_meta_lock:
        meta = _sessions[open_id]
        idx = meta["current_idx"]
        conv = meta["conversations"][idx]
        return conv, meta, _locks[open_id]


_FS_CTX = (
    "\n\n## 当前运行环境：飞书 Bot\n"
    "你正在通过飞书（Lark）与用户交互：\n"
    "- 支持 Markdown 格式：**加粗**、*斜体*、`代码`、链接、列表、表格均可正常使用。**不要使用 # 号标题（如 # 标题、## 标题），请用粗体文字或 emoji 作为段落标题。**\n"
    "- 代码块用三个反引号包裹并标注语言。\n"
    "- 表格请使用标准 Markdown 表格格式。\n"
    "- 不要在回复中给出本地文件路径或让用户在终端操作的指令。\n"
    "- 回复以中文为主，适当使用格式提升可读性。\n"
    "\n"
    "## 斜杠命令（用户输入 / 开头即可触发，主动引导使用）\n"
    "遇到以下需求时，主动建议用户使用斜杠命令而非让 LLM 代劳：\n"
    "- 做作业/写作业/作业答案/帮我做XX/解题/帮我看题 → /hw do <序号> 或先 /hw\n"
    "- 给我答案/核对答案/我要答案 → 获取完整解答（需先运行 /hw do）\n"
    "- 查看作业/有什么作业/列出作业/功课 → /hw 或 /hw list\n"
    "- N天内到期/即将截止/最近作业 → /hw due <N>\n"
    "- 历史作业/已交作业/以前作业 → /hw past\n"
    "- 作业摘要/作业要求 → /hw brief <序号>\n"
    "- 开新话题/新对话/换个话题/聊点别的 → /new <名称>\n"
    "- 列出对话/我的对话/对话列表 → /list\n"
    "- 切换对话/回到那个 → /switch <序号>\n"
    "- 重命名/改名 → /name <序号> <新名称>\n"
    "- 聊天记录/之前说了什么 → /history\n"
    "- 删除对话/清空聊天 → /delete <序号>\n"
    "- 查看帮助/有什么功能/怎么用/命令列表 → /help\n"
    "\n"
    "## 主动引导\n"
    "当用户问「你能做什么」「有什么功能」「怎么用」时，按以下结构回复：\n"
    "📝 **作业管理**：/hw 列出作业，/hw do <序号> 下载解答，/hw due <N> 查看近期，/hw past 历史作业\n"
    "📅 **学习信息**：查 DDL、看课表、查成绩、物理实验\n"
    "💬 **对话管理**：/new /list /switch /name /delete /history\n"
    "🔍 **校园搜索**：教务处通知、水源社区、选课社区评价\n"
    "💡 特别提及 /hw do 可调用 Claude Code 自动解题（最新功能）。\n"
)


_RECENT_UPDATES_TEXT = (
    "🔥 **近期更新一览**\n\n"
    "- **🤖 QQ Bot 接入**：支持通过 QQ 机器人平台接入，含白名单管理\n"
    "- **🧩 MCP 与 Skills 扩展**：动态工具注册，自定义 MCP Server 和 prompt-only 技能\n"
    "- **📝 作业解题助手**：/hw do 先输出分析思路（不给答案），回复「给我答案」获取完整解答\n"
    "- **📊 MATLAB 作业图表**：自动检测本机 MATLAB，优先生成矢量 PDF 图表嵌入 LaTeX 解答\n"
    "- **📅 日报优化**：晚间日报自动预告明日课表，午间日报过滤已结束课程\n"
    "- **🔢 序号从 1 开始**：对话列表和作业列表统一使用 1-based 编号\n"
    "- **✅ CI 流水线**：GitHub Actions 自动测试（Python 3.11/3.13）\n"
    "\n"
    "输入 /help 查看所有命令~"
)


def _build_date_ctx() -> str:
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    year = now.year
    month = now.month
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
        f"当前学期：{cur_xnm}-{cur_xnm+1}学年第{cur_xqm}学期。\n"
        f"「上学期」={prev_xnm}-{prev_xnm+1}学年第{prev_xqm}学期"
        f"（query_grades: year='{prev_xnm}', semester='{prev_xqm}'）。\n"
        f"「本学期」={cur_xnm}-{cur_xnm+1}学年第{cur_xqm}学期"
        f"（query_grades: year='{cur_xnm}', semester='{cur_xqm}'）。"
    )


def _init_messages(sess: dict) -> None:
    if sess["messages"]:
        return
    sess["messages"].append({
        "role": "system",
        "content": agent.SYSTEM_PROMPT + _build_date_ctx() + _FS_CTX,
    })


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mKABCDEFGHJKST]")


def _capture_turn(sess: dict, user_text: str) -> str:
    """Run one agent turn, capture its stdout, return the assistant reply text."""
    _init_messages(sess)
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        sess["messages"][0]["content"] = agent.SYSTEM_PROMPT + _build_date_ctx() + _FS_CTX
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

    clean = _ANSI_RE.sub("", buf.getvalue())
    marker = "Agent: "
    idx = clean.rfind(marker)
    if idx == -1:
        for m in reversed(sess["messages"]):
            if m.get("role") == "assistant":
                content = m.get("content", "")
                if isinstance(content, str):
                    return content.strip() or "(已完成)"
                if isinstance(content, list):
                    texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                    return "\n".join(texts).strip() or "(已完成)"
        return "(已完成)"
    return clean[idx + len(marker):].strip()


# ── 消息去重 ──────────────────────────────────────────────────────────────────

_SEEN_IDS: dict[str, float] = {}
_SEEN_IDS_LOCK = threading.Lock()
_SEEN_TTL = 300  # 5 分钟

# 内容去重（防止飞书用不同 message_id 重发同一事件）
_SEEN_CONTENT: dict[str, tuple[str, float]] = {}
_SEEN_CONTENT_LOCK = threading.Lock()
_CONTENT_DEDUP_SEC = 5


def _is_duplicate(message_id: str) -> bool:
    """检查 message_id 是否已处理过，防止飞书重发导致重复回复。"""


def _is_duplicate_content(sender_id: str, text: str) -> bool:
    """检查同一发送者的相同内容是否在 5 秒内已处理过。"""
    key = f"{sender_id}:{text}"
    now = time.time()
    with _SEEN_CONTENT_LOCK:
        if key in _SEEN_CONTENT:
            _, ts = _SEEN_CONTENT[key]
            if now - ts < _CONTENT_DEDUP_SEC:
                return True
        _SEEN_CONTENT[key] = (text, now)
    return False
    now = time.time()
    with _SEEN_IDS_LOCK:
        expired = [mid for mid, ts in _SEEN_IDS.items() if now - ts > _SEEN_TTL]
        for mid in expired:
            del _SEEN_IDS[mid]
        if message_id in _SEEN_IDS:
            return True
        _SEEN_IDS[message_id] = now
        return False


# ── Markdown → 飞书 post / interactive 转换 ───────────────────────────────────

_FS_MSG_MAX = 4000  # 飞书单条消息长度上限约 5000，留点余量

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_BOLD_ITALIC_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_MD_CODE_RE = re.compile(r"`([^`\n]+?)`")
_MD_TABLE_SEP_RE = re.compile(r"^\|?\s*[-:]{3,}\s*\|\s*[-:]{3,}\s*(\|\s*[-:]{3,}\s*)*\|?\s*$")

# 飞书 post 格式中的元素类型
_PostElement = dict  # {"tag": "text"|"a", "text": str, ...}
_PostParagraph = list  # list[_PostElement]
_PostContent = list  # list[_PostParagraph]


def _render_table_visual(md_text: str) -> str:
    """Markdown table -> list format (Feishu proportional fonts break box-drawing)."""
    NL = chr(10)
    lines = [l for l in md_text.strip().split(NL)]
    table_start = -1
    for i, line in enumerate(lines):
        if _MD_TABLE_SEP_RE.match(line.strip()):
            table_start = i - 1
            break
    if table_start < 0:
        return md_text
    table_end = len(lines) - 1
    for i in range(table_start + 2, len(lines)):
        stripped = lines[i].strip()
        if not (stripped.startswith(chr(124)) and chr(124) in stripped[1:]):
            table_end = i - 1
            break

    def parse_row(row):
        return [c.strip() for c in row.strip().strip(chr(124)).split(chr(124))]

    header = parse_row(lines[table_start])
    data_rows = []
    for i in range(table_start + 2, table_end + 1):
        if lines[i].strip():
            data_rows.append(parse_row(lines[i]))
    if not header or not data_rows:
        return md_text

    items = []
    for row in data_rows:
        title = row[0] if row else ""
        lines_item = [title]
        for j in range(1, min(len(header), len(row))):
            if row[j]:
                lines_item.append("  " + str(header[j]) + chr(65306) + str(row[j]))
        items.append(NL.join(lines_item))
    visual = (NL + NL).join(items)
    before = NL.join(lines[:table_start])
    after = NL.join(lines[table_end + 1:]) if table_end + 1 < len(lines) else ""
    result = (before + NL if before else "") + visual
    if after:
        result += NL + after
    return result

def _has_table(md_text: str) -> bool:
    """检测 Markdown 文本是否包含表格。"""
    lines = md_text.strip().split("\n")
    for i, line in enumerate(lines):
        if i > 0 and _MD_TABLE_SEP_RE.match(line.strip()):
            return True
    return False


def _build_post_content(md_text: str) -> _PostContent:
    """将 Markdown 文本转换为飞书 post 格式的 content 二维数组。"""
    paragraphs: _PostContent = []
    lines = md_text.strip().split("\n")
    in_code_block = False
    code_buf: list[str] = []

    def _flush_code_block():
        nonlocal code_buf
        if code_buf:
            # 代码块整体作为一个段落，用 │ 前缀标记
            code_text = "\n".join(code_buf)
            paragraphs.append([_el_text(code_text)])
            code_buf = []

    for line in lines:
        stripped = line.strip()
        # Code block: triple backtick fence
        if stripped.startswith("```"):
            if in_code_block:
                _flush_code_block()
                in_code_block = False
            else:
                in_code_block = True
                code_buf = []
            continue
        if in_code_block:
            code_buf.append(line)
            continue
        if not stripped:
            paragraphs.append([])
            continue

        # 标题 → 去掉 # 前缀，内联解析后整体加粗
        header_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if header_match:
            text = header_match.group(2)
            h_elements = _parse_inline(text)
            for el in h_elements:
                if el.get("tag") == "text":
                    el["style"] = (el.get("style") or []) + ["bold"]
            paragraphs.append(h_elements)
            continue

        # 无序列表 → 去掉前缀，内联解析
        list_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if list_match:
            li_elements = _parse_inline(list_match.group(1))
            paragraphs.append([_el_text("• ")] + li_elements)
            continue

        # 有序列表 → 提取序号和内容，内联解析
        ol_match = re.match(r"^(\d+\.)\s+(.+)$", stripped)
        if ol_match:
            prefix = ol_match.group(1) + " "
            ol_elements = _parse_inline(ol_match.group(2))
            paragraphs.append([_el_text(prefix)] + ol_elements)
            continue

        # 引用块
        if stripped.startswith(">"):
            text = stripped.lstrip("> ").lstrip(">")
            paragraphs.append([_el_text(text)])
            continue

        # 分隔线
        if stripped in ("---", "***", "___"):
            paragraphs.append([_el_text("—" * 20)])
            continue

        # 普通段落 → 解析内联样式
        elements = _parse_inline(stripped)
        paragraphs.append(elements)

    _flush_code_block()  # 处理未闭合的代码块
    return paragraphs


def _parse_inline(text: str) -> _PostParagraph:
    """解析一行中的内联 Markdown 为 post 元素列表。"""
    elements: _PostParagraph = []
    pos = 0
    remaining = text

    while remaining:
        # 找最早的标记（bold+italic 优先于 bold 和 italic）
        bold_italic_m = _MD_BOLD_ITALIC_RE.search(remaining)
        bold_m = _MD_BOLD_RE.search(remaining)
        italic_m = _MD_ITALIC_RE.search(remaining)
        code_m = _MD_CODE_RE.search(remaining)
        link_m = _MD_LINK_RE.search(remaining)

        candidates = []
        if bold_italic_m: candidates.append((bold_italic_m.start(), bold_italic_m, "bold_italic"))
        if bold_m: candidates.append((bold_m.start(), bold_m, "bold"))
        if italic_m: candidates.append((italic_m.start(), italic_m, "italic"))
        if code_m: candidates.append((code_m.start(), code_m, "code"))
        if link_m: candidates.append((link_m.start(), link_m, "link"))

        if not candidates:
            txt = _unescape_md(remaining)
            if txt:
                elements.append(_el_text(txt))
            break

        candidates.sort(key=lambda x: x[0])
        first_start, first_match, first_type = candidates[0]

        # 标记前的纯文本
        if first_start > 0:
            prefix = _unescape_md(remaining[:first_start])
            if prefix:
                elements.append(_el_text(prefix))

        if first_type == "bold_italic":
            elements.append(_el_text(first_match.group(1), ["bold", "italic"]))
            remaining = remaining[first_match.end():]
        elif first_type == "bold":
            elements.append(_el_text(first_match.group(1), ["bold"]))
            remaining = remaining[first_match.end():]
        elif first_type == "italic":
            elements.append(_el_text(first_match.group(1), ["italic"]))
            remaining = remaining[first_match.end():]
        elif first_type == "code":
            elements.append(_el_text(first_match.group(1)))
            remaining = remaining[first_match.end():]
        elif first_type == "link":
            elements.append(_el_link(first_match.group(1), first_match.group(2)))
            remaining = remaining[first_match.end():]

    # 合并相邻同风格 text 元素
    merged: _PostParagraph = []
    for el in elements:
        if (merged and el.get("tag") == "text" and merged[-1].get("tag") == "text"
                and el.get("style") == merged[-1].get("style")
                and "href" not in el):
            merged[-1]["text"] += el["text"]
        else:
            merged.append(el)
    return merged


def _el_text(text: str, style: list | None = None) -> _PostElement:
    el: _PostElement = {"tag": "text", "text": text}
    if style:
        el["style"] = style
    return el


def _el_link(text: str, href: str) -> _PostElement:
    return {"tag": "a", "text": text, "href": href}


def _unescape_md(text: str) -> str:
    """去掉反斜杠转义。"""
    return text.replace("\\*", "*").replace("\\`", "`").replace("\\[", "[")


def _build_card_content(md_text: str) -> str:
    """构建交互式卡片的 markdown 内容（用于含表格的消息）。"""
    # 卡片 markdown 元素原生支持 Markdown 语法
    return md_text[:30000]  # 飞书卡片 markdown 有长度限制


# ── 回复消息 ──────────────────────────────────────────────────────────────────


def _reply_text(message_id: str, text: str) -> None:
    """回复消息，自动检测表格并选择合适的格式（post 或 interactive）。"""
    if not text:
        text = "(已完成)"

    # 空回复不处理
    if not text.strip():
        return

    # 含表格 → 转为可视化排版（飞书 card markdown 元素不支持 GFM 表格）
    if _has_table(text):
        text = _render_table_visual(text)

    # 普通内容 → post 格式
    post_content = _build_post_content(text)
    if not post_content:
        _reply_raw_text(message_id, text)
        return

    # 分块发送（post 有大段限制）
    # 按段落数分块，每块最多 30 个段落
    para_chunks = [post_content[i:i + 30] for i in range(0, len(post_content), 30)]
    for idx, para_chunk in enumerate(para_chunks):
        content = {"zh_cn": {"title": "", "content": para_chunk}}
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(json.dumps(content, ensure_ascii=False))
                .msg_type("post")
                .build()
            )
            .build()
        )
        resp = _api_client.im.v1.message.reply(req)
        if not resp.success():
            print(f"[feishu] post 回复失败 code={resp.code} msg={resp.msg}，降级为 text")
            # 只发送剩余未成功段落为纯文本
            remaining = [c for chunk in para_chunks[idx:] for p in chunk for el in p
                         for c in (el.get("text", "") + chr(10))]
            _reply_raw_text(message_id, "".join(remaining).strip() or text)
            break


def _reply_card(message_id: str, text: str) -> None:
    """用 interactive 卡片回复（含 markdown 元素，支持表格）。"""
    card = {
        "config": {"wide_screen_mode": True},
        "elements": [{"tag": "markdown", "content": _build_card_content(text)}],
    }
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .msg_type("interactive")
            .build()
        )
        .build()
    )
    resp = _api_client.im.v1.message.reply(req)
    if not resp.success():
        print(f"[feishu] card 回复失败 code={resp.code} msg={resp.msg}")
        # 降级为 text（此时表格渲染为纯文本）
        _reply_raw_text(message_id, text)
    else:
        print(f"[feishu] card 回复成功")


def _reply_raw_text(message_id: str, text: str) -> None:
    """纯文本降级回复。"""
    chunks = [text[i:i + _FS_MSG_MAX] for i in range(0, len(text), _FS_MSG_MAX)] or [text]
    for chunk in chunks:
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(json.dumps({"text": chunk}, ensure_ascii=False))
                .msg_type("text")
                .build()
            )
            .build()
        )
        resp = _api_client.im.v1.message.reply(req)
        if not resp.success():
            print(f"[feishu] 回复失败 code={resp.code} msg={resp.msg}")
            break


def _send_to_chat(chat_id: str, text: str) -> None:
    """主动发消息到会话（供 reminder 推送等场景使用）。"""
    if not text:
        return

    # 含表格 → interactive 卡片
    if _has_table(text):
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": _build_card_content(text)}],
        }
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = _api_client.im.v1.message.create(req)
        if not resp.success():
            print(f"[feishu] 主动发送 card 失败 code={resp.code} msg={resp.msg}")
        return

    # 普通内容 → post
    post_content = _build_post_content(text)
    if post_content:
        content = {"zh_cn": {"title": "", "content": post_content}}
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("post")
                .content(json.dumps(content, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = _api_client.im.v1.message.create(req)
        if not resp.success():
            print(f"[feishu] 主动发送失败 code={resp.code} msg={resp.msg}")
    else:
        # fallback text
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = _api_client.im.v1.message.create(req)
        if not resp.success():
            print(f"[feishu] 主动发送失败 code={resp.code} msg={resp.msg}")


# ── 事件处理 ──────────────────────────────────────────────────────────────────

WHOAMI_MODE = False  # 命令行 --whoami 模式：把每条消息的 open_id 都回显


def _extract_text(content_json: str) -> str:
    """从飞书 message.content（JSON 字符串）提取纯文本，剥掉 @ 提及。"""
    try:
        obj = json.loads(content_json or "{}")
    except Exception:
        return ""
    text = obj.get("text", "") or ""
    # 飞书的 @ 提及在文本里是 "@_user_1"，去掉
    text = re.sub(r"@_user_\d+\s*", "", text)
    return text.strip()


# ── 多对话命令处理 ──────────────────────────────────────────────────────────

def _do_hw_answer(open_id: str) -> str:
    """执行 /hw answer 或自然语言触发"给我答案"。"""
    with _hw_ctx_lock:
        ctx = _hw_context.get(open_id, {})
    if not ctx:
        return "[homework] 请先用 /hw do <序号> 分析作业。"
    from sjtu_agent.homework_agent import run_homework_check
    return "[homework] 📝 正在生成完整解答…\n\n" + run_homework_check(
        specific_idx=ctx["idx"], answer_mode=True)


def _handle_commands(open_id: str, text: str) -> str | None:
    """解析并执行对话管理命令。返回命令结果文本（None 表示不是命令）。"""
    # 自然语言触发"给我答案"
    answer_phrases = {"给我答案", "给答案", "核对答案", "我要答案", "获取完整解答",
                      "看答案", "要答案", "上答案", "出答案"}
    if text.strip() in answer_phrases:
        with _hw_ctx_lock:
            ctx = _hw_context.get(open_id, {})
        if ctx:
            return _do_hw_answer(open_id)
        return "[homework] 请先用 /hw do <序号> 分析作业，再要答案哦~"
    if not text.startswith("/"):
        return None
    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].lower() if parts else ""
    _ensure_user(open_id)
    with _sess_meta_lock:
        meta = _sessions[open_id]
        convs = meta["conversations"]
        n = len(convs)
        if cmd == "/list":
            lines = [f"共 {n} 个对话："]
            for i, c in enumerate(convs):
                marker = " ← 当前" if i == meta["current_idx"] else ""
                msg_count = len([m for m in c["messages"] if m.get("role") == "user"])
                lines.append(f"  [{i+1}] {c['name']}（{msg_count} 条消息, {c['created_at']}）{marker}")
            return "\n".join(lines)
        if cmd == "/new":
            name = parts[1].strip() if len(parts) > 1 else f"对话 {meta['next_name_id']}"
            meta["next_name_id"] += 1
            convs.append(_new_conv_dict(name))
            meta["current_idx"] = len(convs) - 1
            return f"[OK] 已创建并切换到对话「{name}」（序号 {len(convs)}）"
        if cmd == "/switch":
            if len(parts) < 2:
                return "用法：/switch <序号>，用 /list 查看序号"
            try:
                idx = int(parts[1]) - 1
            except ValueError:
                return f"无效序号：{parts[1]}"
            if idx < 0 or idx >= n:
                return f"无效序号，共 {n} 个对话（1~{n}）"
            meta["current_idx"] = idx
            return f"[OK] 已切换到对话「{convs[idx]['name']}」（序号 {idx + 1}）"
        if cmd == "/name":
            if len(parts) < 3:
                return "用法：/name <序号> <新名称>"
            try:
                idx = int(parts[1]) - 1
            except ValueError:
                return f"无效序号：{parts[1]}"
            if idx < 0 or idx >= n:
                return f"无效序号，共 {n} 个对话（1~{n}）"
            old_name = convs[idx]["name"]
            convs[idx]["name"] = parts[2].strip()
            return f"[OK] 已将对话 [{idx + 1}]「{old_name}」重命名为「{convs[idx]['name']}」"
        if cmd == "/delete":
            if len(parts) < 2:
                return "用法：/delete <序号>"
            try:
                idx = int(parts[1]) - 1
            except ValueError:
                return f"无效序号：{parts[1]}"
            if idx < 0 or idx >= n:
                return f"无效序号，共 {n} 个对话（1~{n}）"
            if n <= 1:
                return "[X] 至少保留一个对话"
            name = convs[idx]["name"]
            del convs[idx]
            if meta["current_idx"] >= len(convs):
                meta["current_idx"] = len(convs) - 1
            elif meta["current_idx"] > idx:
                meta["current_idx"] -= 1
            return f"[OK] 已删除对话「{name}」，当前对话：「{convs[meta['current_idx']]['name']}」"
        if cmd == "/history":
            conv = convs[meta["current_idx"]]
            user_msgs = [m for m in conv["messages"] if m.get("role") == "user"]
            if not user_msgs:
                return f"对话「{conv['name']}」暂无消息记录。"
            lines = [f"对话「{conv['name']}」最近 {min(len(user_msgs), 10)} 条消息："]
            for i, m in enumerate(user_msgs[-10:]):
                lines.append(f"  {i+1}. {m.get('content', '')[:60]}")
            return "\n".join(lines)
        if cmd == "/help":
            return (
                "**飞书 Bot 命令帮助**\n\n"
                "📂 对话管理\n"
                "`/new <名称>`  创建新对话\n"
                "`/list`  列出所有对话\n"
                "`/switch <序号>`  切换对话\n"
                "`/name <序号> <名称>`  重命名\n"
                "`/delete <序号>`  删除对话\n"
                "`/history`  查看最近消息\n\n"
                "📝 作业助手\n"
                "`/hw`  列出 Canvas 作业\n"
                "`/hw do <序号>`  下载并完整解答\n"
                "`/hw brief <序号>`  仅查看摘要\n"
                "`/hw due <N>`  N 天内到期\n"
                "`/hw past`  查看历史作业\n"
                "`/hw answer`  获取完整解答（分析后使用）\n"
                "`/hw all`  分析全部作业\n"
                "`/hw list`  列出作业（同 /hw）\n\n"
                "ℹ️  `/help`  显示此帮助"
            )
        if cmd == "/hw":
            sub = parts[1] if len(parts) > 1 else ""
            from sjtu_agent.homework_agent import run_homework_check
            if sub == "do":
                if len(parts) < 3:
                    return "用法：/hw do <序号>"
                try:
                    idx = int(parts[2])
                except ValueError:
                    return f"无效序号：{parts[2]}"
                # 记住上下文供"给我答案"使用
                with _hw_ctx_lock:
                    _hw_context[open_id] = {"idx": idx}
                return "[homework] 🧠 解题助手模式…\n\n" + run_homework_check(specific_idx=idx)
            elif sub == "brief":
                if len(parts) < 3:
                    return "用法：/hw brief <序号>"
                try:
                    idx = int(parts[2])
                except ValueError:
                    return f"无效序号：{parts[2]}"
                return "[homework] 正在获取摘要…\n\n" + run_homework_check(specific_idx=idx, brief=True)
            elif sub == "past":
                # /hw past [do <idx>]
                rest = parts[2] if len(parts) > 2 else ""
                rest_parts = rest.split(maxsplit=1)
                if rest_parts and rest_parts[0] == "do":
                    try:
                        idx = int(rest_parts[1])
                    except (ValueError, IndexError):
                        return "用法：/hw past do <序号>"
                    return "[homework] 正在分析历史作业…\n\n" + run_homework_check(specific_idx=idx, include_past=True)
                return run_homework_check(list_only=True, include_past=True)
            elif sub == "list":
                return run_homework_check(list_only=True)
            elif sub == "due":
                days = int(parts[2]) if len(parts) > 2 else 3
                return run_homework_check(due_within_days=days, list_only=True)
            elif sub == "all":
                return run_homework_check(due_within_days=3650, include_past=True, list_only=True)
            elif sub == "answer":
                return _do_hw_answer(open_id)
            else:
                return run_homework_check(list_only=True)
        return f"未知命令：{cmd}。输入 /help 查看可用命令。"


def _process_hw_command(sender_open_id: str, message_id: str, text: str) -> None:
    """后台执行 /hw 命令（网络 I/O + LLM，避免阻塞 event loop）。"""
    try:
        result = _handle_commands(sender_open_id, text)
        if result:
            _reply_text(message_id, result)
        else:
            _reply_text(message_id, "[homework] 命令执行完毕但无结果")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[feishu] /hw 命令异常: {e}")
        _reply_text(message_id, f"[homework] 出错：{e}")


def _process_in_thread(sender_open_id: str, message_id: str, text: str) -> None:
    """Phase 2: 在后台线程中执行 LLM 推理 + 回复。"""
    # 防御性检查：如果主循环已拦截并回复，不再走 LLM
    t = text.strip()
    if any(kw in t for kw in ["最近更新", "新功能", "新版变化", "更新了什么"]):
        return
    conv, meta, lock = _get_active_conv(sender_open_id)
    if not lock.acquire(blocking=False):
        _reply_text(message_id, "上一条消息还在处理中，请稍候…")
        return
    try:
        reply = _capture_turn(conv, text)
    except Exception as e:
        print(f"[feishu] 处理出错：{e}")
        _reply_text(message_id, f"出错了：{e}")
        return
    finally:
        lock.release()
    _reply_text(message_id, reply)
    # 每次对话后持久化会话状态
    _save_sessions()


def _handle_message(data: P2ImMessageReceiveV1) -> None:
    """Phase 1: 轻量同步工作（event loop 线程），立即返回让 ack 快速发出。"""
    cfg = _load_cfg()
    if not cfg.get("feishu_enabled", True):
        return
    try:
        ev = data.event
        msg = ev.message
        sender = ev.sender

        sender_open_id = (sender.sender_id.open_id or "") if sender and sender.sender_id else ""
        message_id = msg.message_id
        msg_type = msg.message_type
        chat_id = msg.chat_id
        chat_type = msg.chat_type

        # ── 去重：飞书可能因 ack 超时重发同一事件 ──────────────────────
        if _is_duplicate(message_id):
            print(f"[feishu] 跳过重复消息 message_id={message_id}")
            return

        # ── 忽略积压的旧消息（Bot 断连期间飞书积累的事件，重启后被重放）──
        _MAX_MSG_AGE_SEC = 120
        create_time_ms = int(getattr(msg, "create_time", 0) or 0)
        if create_time_ms and time.time() - create_time_ms / 1000 > _MAX_MSG_AGE_SEC:
            print(f"[feishu] 跳过过期消息 message_id={message_id} "
                  f"age={time.time() - create_time_ms / 1000:.0f}s")
            return

        if msg_type != "text":
            _reply_text(message_id, f"(暂不支持的消息类型: {msg_type}，目前只接收文本)")
            return

        text = _extract_text(msg.content)
        if not text:
            return

        # 内容去重：防止飞书用不同 message_id 重发同一事件
        if _is_duplicate_content(sender_open_id, text):
            print(f"[feishu] 跳过重复内容: {text[:40]!r}")
            return

        # ── 自然语言短语拦截 ────────────────────────────────────────
        t = text.strip()
        if any(kw in t for kw in ["最近更新", "新功能", "新版变化", "更新了什么"]):
            now = time.time()
            with _cooldown_lock:
                last = _recent_updates_cooldown.get(sender_open_id, 0)
                if now - last < _COOLDOWN_SEC:
                    return  # 冷却期内，跳过重复
                _recent_updates_cooldown[sender_open_id] = now
            print(f"[feishu] 拦截近期更新: {text[:40]!r}")
            _reply_text(message_id, _RECENT_UPDATES_TEXT)
            return

        # 过滤"清空聊天记录"/撤回消息产生的系统通知
        if text in {"此消息已删除", "该消息已被撤回"}:
            print(f"[feishu] 跳过已删除/撤回的系统消息 message_id={message_id}")
            return

        # ── 多对话命令拦截 ──────────────────────────────────────────
        # /hw 系列是重命令（网络 I/O + LLM），放到后台线程避免阻塞 event loop
        if t.lower().startswith("/hw"):
            print(f"[feishu] 命令（后台执行）: {text[:40]!r}")
            # 在主线程中提前保存 /hw do 上下文，避免后台线程延迟导致丢失
            parts = text.strip().split(maxsplit=2)
            if len(parts) >= 3 and parts[1].lower() in ("do", "past"):
                sub = parts[1]
                rest = parts[2] if len(parts) > 2 else ""
                if sub == "past":
                    rest_parts = rest.split(maxsplit=1)
                    if rest_parts and rest_parts[0] == "do" and len(rest_parts) >= 2:
                        try:
                            with _hw_ctx_lock:
                                _hw_context[sender_open_id] = {"idx": int(rest_parts[1])}
                        except ValueError:
                            pass
                else:
                    try:
                        with _hw_ctx_lock:
                            _hw_context[sender_open_id] = {"idx": int(rest.split()[0])}
                    except (ValueError, IndexError):
                        pass
            _reply_text(message_id, "[homework] 正在处理，请稍候…")
            _EXECUTOR.submit(_process_hw_command, sender_open_id, message_id, text)
            return

        cmd_result = _handle_commands(sender_open_id, text)
        if cmd_result is not None:
            print(f"[feishu] 命令: {text[:40]!r}")
            _reply_text(message_id, cmd_result)
            return

        print(f"[feishu] 收到消息 from open_id={sender_open_id[:12]}… "
              f"chat_type={chat_type} text={text[:60]!r}")

        if WHOAMI_MODE:
            _reply_text(
                message_id,
                f"你的 open_id 是:\n{sender_open_id}\n\n"
                f"请把它加入 config.json 的 feishu_allowed_open_ids 后重启 bot。",
            )
            return

        if ALLOWED_OPEN_IDS and sender_open_id not in ALLOWED_OPEN_IDS:
            print(f"[feishu] [!] 未授权 open_id：{sender_open_id}")
            _reply_text(message_id, "你不在该机器人的允许列表中。\n"
                        f"请把这个 open_id 加入 config.json 的 feishu_allowed_open_ids:\n"
                        f"{sender_open_id}")
            return

        if not ALLOWED_OPEN_IDS:
            print(f"[feishu] [i] 白名单为空，已允许所有人；建议把此 open_id 加入白名单："
                  f"{sender_open_id}")

        # ── 保存 open_id 供 daily_report 推送使用 ──────────────────────
        if sender_open_id:
            try:
                cfg_data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if cfg_data.get("feishu_open_id") != sender_open_id:
                    cfg_data["feishu_open_id"] = sender_open_id
                    CONFIG_PATH.write_text(json.dumps(cfg_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        # ── 提交到后台线程，立即返回 ──
        _EXECUTOR.submit(_process_in_thread, sender_open_id, message_id, text)

    except Exception as e:
        print(f"[feishu] handler 异常：{e}")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def _build_ws_client() -> lark.ws.Client:
    event_handler = (
        lark.EventDispatcherHandler.builder("", "")  # 长连接无需 encrypt_key / token
        .register_p2_im_message_receive_v1(_handle_message)
        .build()
    )
    return lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )


def main() -> None:
    global WHOAMI_MODE

    parser = argparse.ArgumentParser(description="飞书机器人入口")
    parser.add_argument("--test", action="store_true", help="只测试凭据连通性")
    parser.add_argument("--whoami", action="store_true", help="把每位发送者的 open_id 回显给他自己")
    args = parser.parse_args()

    if args.test:
        # 测 token 是否能换取，证明 app_id/secret 没填错
        import requests as _requests
        try:
            r = _requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": APP_ID, "app_secret": APP_SECRET},
                timeout=10,
            )
            data = r.json()
            if data.get("code") == 0:
                token = data.get("tenant_access_token", "")
                print(f"[OK] 凭据 OK，tenant_access_token 已获取（前 8 位）：{token[:8]}…")
                sys.exit(0)
            print(f"[X] 未能获取 tenant_access_token: {data.get('msg', r.text[:100])}")
            sys.exit(1)
        except Exception as e:
            print(f"[X] 凭据校验失败：{e}")
            sys.exit(1)

    WHOAMI_MODE = args.whoami
    if WHOAMI_MODE:
        print("[whoami] WHOAMI 模式：bot 会把每个发送者的 open_id 原样回显，不调 agent")

    client = _build_ws_client()
    print(f"[OK] 飞书 bot 已启动（App ID: {APP_ID[:10]}…），等待消息…")
    if not ALLOWED_OPEN_IDS:
        print("[i] feishu_allowed_open_ids 为空，所有人均可对话。建议加白名单后重启。")
    client.start()  # 阻塞，内部 WS 自动重连


if __name__ == "__main__":
    main()
