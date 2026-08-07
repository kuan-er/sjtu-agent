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
import base64
import concurrent.futures
import json
import re
import sys
import threading
import time
import tempfile
import datetime as _dt
from pathlib import Path

import requests
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sjtu_agent.paths import CONFIG_PATH
from sjtu_agent.config import cfg as _cfg
from sjtu_agent.bots._core import (
    build_date_ctx,
    model_supports_vision as _model_supports_vision,
)

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
)

import agent

from sjtu_agent.feishu.rendering import (
    FS_MSG_MAX, build_post_content, has_table, render_table_visual,
    build_card_content,
)
from sjtu_agent.feishu.conversations import FeishuConversationManager
from sjtu_agent.logging import get_logger

_logger = get_logger("feishu_bot")


def _load_cfg() -> dict:
    _cfg.reload_if_changed()
    return _cfg.raw()


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

# ── 作业解答上下文（记住最近一次 /hw do，供"给我答案"使用）────────────────
_hw_context: dict[str, dict] = {}
_hw_ctx_lock = threading.Lock()
_hw_in_progress: set[str] = set()  # 防止同一用户并发 /hw

# ── 近期更新冷却期（防 Feishu 重发导致重复回复）──────────────────────────
_recent_updates_cooldown: dict[str, float] = {}
_cooldown_lock = threading.Lock()
_COOLDOWN_SEC = 10

# ── 会话持久化 ──────────────────────────────────────────────────────────────
from sjtu_agent.paths import DATA_DIR

_conv_mgr = FeishuConversationManager(DATA_DIR)
_conv_mgr.load()


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
    "- 套用 SJTU 模板/毕业论文格式/课程报告模板 → /template <名称>\n"
    "- 编译论文 PDF/帮我编译/生成 PDF → /template compile\n"
    "- 从 Overleaf 克隆模板/推送到 Overleaf → /template clone <id>, /template push\n"
    "- AI 资讯/今天 AI 圈/大模型新闻 → /aihot\n"
    "- 校园新闻/今天有什么新闻/每日简报 → /news\n"
    "- 食堂推荐/去哪吃/今天吃什么/食堂人多吗 → /eat [闵行|徐汇|张江]\n"
    "- 查看帮助/有什么功能/怎么用/命令列表 → /help\n"
    "\n"
    "## 主动引导\n"
    "当用户问「你能做什么」「有什么功能」「怎么用」时，按以下结构回复：\n"
    "📝 **作业管理**：/hw 列出作业，/hw do <序号> 下载解答，/hw due <N> 查看近期，/hw past 历史作业\n"
    "📄 **LaTeX 模板**：/template 列出模板，/template bachelor-thesis 套用毕业论文格式\n"
    "🤖 **AI 资讯**：/aihot 获取今日 AI 圈精选新闻（支持追问最新进展、大模型发布等）\n"
    "📰 **校园新闻**：/news 生成校园新闻摘要（教务处+水源+交大新闻网+Canvas）\n"
    "🍽️ **食堂推荐**：/eat 获取实时拥挤度 + 个性化食堂推荐，/eat 徐汇 切换校区\n"
    "📅 **学习信息**：查 DDL、看课表、查成绩、物理实验\n"
    "💬 **对话管理**：/new /list /switch /name /delete /history\n"
    "🔍 **校园搜索**：教务处通知、水源社区、选课社区评价\n"
)


_RECENT_UPDATES_TEXT = (
    "[近期更新] **v0.3.2**\n\n"
    "- [记忆] 我会记住你聊过的课程、考试、学习偏好，下次对话自动关联\n"
    "- [LaTeX] /template compile 一键编译，/template clone 从 Overleaf 克隆，/template push 推送\n"
    "- [AI] /aihot 每日 AI 圈精选新闻，按模型/产品/行业/论文/技巧分类\n"
    "- [Canvas] 课程公告和 quiz 定时监控，自动推送到飞书\n"
    "- [作业] /hw do 下载+分析思路，「给我答案」获取完整解答\n"
    "- [日报] 早/午/晚报自动推送，显示课程时间段和 DDL\n"
    "- [模板] /template 套用 SJTU 毕业论文格式\n"
    "- [邮件] 交大邮箱新邮件自动推送（只读，不发送不删除）\n"
    "- [安全] 代码通过 16 项安全审计修复，Web UI 支持 Token 认证\n"
    "\n"
    "输入 /help 查看所有命令~"
)


def _build_date_ctx() -> str:
    """日期上下文（共享 base + 校历假日/调休）。"""
    import datetime as _dt
    base = build_date_ctx()
    # 校历假日/调休上下文
    try:
        from sjtu_agent.calendar import AcademicCalendar
        from sjtu_agent.paths import DATA_DIR
        now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
        cal_ctx = AcademicCalendar(DATA_DIR).get_context(now.date())
        if cal_ctx:
            base += f"\n{cal_ctx}"
    except Exception:
        pass
    return base


def _inject_memory_ctx(open_id: str, user_msg: str) -> str:
    """Try to fetch relevant memories from ChromaDB. Returns '' on any failure."""
    try:
        from sjtu_agent.memory import build_memory_context
        return build_memory_context(open_id, user_msg, n=3)
    except Exception:
        return ""


def _init_messages(sess: dict) -> None:
    if sess["messages"]:
        return
    sess["messages"].append({
        "role": "system",
        "content": agent.SYSTEM_PROMPT + _build_date_ctx() + _FS_CTX,
    })


def _extract_assistant_reply(sess: dict) -> str:
    """Extract the last assistant reply from session messages."""
    for m in reversed(sess["messages"]):
        if m.get("role") == "assistant":
            content = m.get("content", "")
            if isinstance(content, str):
                return content.strip() or "(已完成)"
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                return "\n".join(texts).strip() or "(已完成)"
    return "(已完成)"


def _capture_turn(sess: dict, user_text: str, open_id: str = "") -> str:
    """Run one agent turn, return the assistant reply text."""
    _init_messages(sess)
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        base = agent.SYSTEM_PROMPT + _build_date_ctx() + _FS_CTX
        if open_id:
            base += _inject_memory_ctx(open_id, user_text)
        sess["messages"][0]["content"] = base
    sess["messages"].append({"role": "user", "content": user_text})

    agent._run_one_turn(
        sess["client_box"][0],
        sess["model_box"][0],
        sess["messages"],
    )

    return _extract_assistant_reply(sess)


# ── 消息去重 ──────────────────────────────────────────────────────────────────

_SEEN_IDS: dict[str, float] = {}
_SEEN_IDS_LOCK = threading.Lock()
_SEEN_TTL = 300  # 5 分钟

# 内容去重（防止飞书用不同 message_id 重发同一事件）
_SEEN_CONTENT: dict[str, tuple[str, float]] = {}
_SEEN_CONTENT_LOCK = threading.Lock()
_CONTENT_DEDUP_SEC = 5
import atexit, shutil
_TMP_DIR = DATA_DIR / "feishu_media"
_TMP_DIR.mkdir(parents=True, exist_ok=True)
atexit.register(lambda: shutil.rmtree(str(_TMP_DIR), ignore_errors=True))


def _is_duplicate(message_id: str) -> bool:
    """检查 message_id 是否已处理过，防止飞书重发导致重复回复。"""
    now = time.time()
    with _SEEN_IDS_LOCK:
        expired = [mid for mid, ts in _SEEN_IDS.items() if now - ts > _SEEN_TTL]
        for mid in expired:
            del _SEEN_IDS[mid]
        if message_id in _SEEN_IDS:
            return True
        _SEEN_IDS[message_id] = now
        return False


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




def _capture_turn_multimodal(sess: dict, content: list, open_id: str = "") -> str:
    _init_messages(sess)
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        base = agent.SYSTEM_PROMPT + _build_date_ctx() + _FS_CTX
        if open_id:
            base += _inject_memory_ctx(open_id, "")
        sess["messages"][0]["content"] = base
    sess["messages"].append({"role": "user", "content": content})

    agent._run_one_turn(
        sess["client_box"][0],
        sess["model_box"][0],
        sess["messages"],
    )

    return _extract_assistant_reply(sess)


def _get_tenant_access_token() -> str:
    from sjtu_agent.feishu_client import get_tenant_access_token
    return get_tenant_access_token(APP_ID, APP_SECRET)


def _guess_suffix(filename: str, content_type: str, default: str = ".bin") -> str:
    name = (filename or "").strip()
    if "." in name:
        return Path(name).suffix.lower() or default
    ct = (content_type or "").lower()
    if "jpeg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    if "pdf" in ct:
        return ".pdf"
    if "json" in ct:
        return ".json"
    if "plain" in ct or "text/" in ct:
        return ".txt"
    if "msword" in ct:
        return ".doc"
    if "officedocument.wordprocessingml" in ct:
        return ".docx"
    if "officedocument.presentationml" in ct:
        return ".pptx"
    if "officedocument.spreadsheetml" in ct:
        return ".xlsx"
    return default


def _resource_query_type(msg_type: str) -> str:
    # Feishu message resource API accepts only image|file:
    # image -> image; file/audio/media -> file.
    return "image" if msg_type == "image" else "file"


def _download_feishu_resource(message_id: str, file_key: str, msg_type: str, filename: str = "") -> Path:
    token = _get_tenant_access_token()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}"
    query_type = _resource_query_type(msg_type)
    resp = requests.get(url, params={"type": query_type}, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if resp.status_code != 200 and msg_type == "audio":
        resp = requests.get(url, params={"type": "file"}, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"下载飞书资源失败: HTTP {resp.status_code} {resp.text[:200]}")

    suffix = _guess_suffix(filename, resp.headers.get("content-type", ""), default=".bin")
    name = (filename or "").strip()
    if not name:
        name = f"{msg_type}_{file_key[:10]}{suffix}"
    name = Path(name).name  # strip directory components to prevent path traversal
    save_path = _TMP_DIR / name
    save_path.write_bytes(resp.content)
    return save_path


def _extract_media_ref(msg_type: str, content_json: str) -> dict | None:
    try:
        obj = json.loads(content_json or "{}")
    except Exception:
        return None
    if msg_type == "image":
        image_key = str(obj.get("image_key", "") or "").strip()
        if not image_key:
            return None
        return {"type": "image", "key": image_key, "filename": f"image_{image_key[:10]}.jpg"}
    if msg_type == "file":
        file_key = str(obj.get("file_key", "") or "").strip()
        if not file_key:
            return None
        file_name = str(obj.get("file_name", "") or "").strip()
        return {"type": "file", "key": file_key, "filename": file_name}
    if msg_type == "audio":
        audio_key = str(obj.get("file_key", "") or obj.get("audio_key", "") or "").strip()
        if not audio_key:
            return None
        file_name = str(obj.get("file_name", "") or f"audio_{audio_key[:10]}.m4a").strip()
        return {"type": "audio", "key": audio_key, "filename": file_name}
    if msg_type == "media":
        media_key = str(obj.get("file_key", "") or "").strip()
        if not media_key:
            return None
        file_name = str(obj.get("file_name", "") or f"media_{media_key[:10]}.mp4").strip()
        return {"type": "media", "key": media_key, "filename": file_name}
    return None


# ── 回复消息 ──────────────────────────────────────────────────────────────────


def _call_reply(req) -> object | None:
    """调用飞书 reply API。异常记录日志并返回 None，绝不逃逸导致线程静默死亡。

    背景（issue #93）：重启后第一条消息偶发无回复。首次回复需重新拉
    tenant_access_token（缓存重启后清空），若该请求抛 Python 异常且未捕获，
    后台线程会静默退出——用户看到"没回复"，日志里也看不到任何错误。
    """
    try:
        return _api_client.im.v1.message.reply(req)
    except Exception as e:
        _logger.warning(f"[feishu] reply API 异常: {e}")
        return None


def _reply_text(message_id: str, text: str) -> None:
    """回复消息，自动检测表格并选择合适的格式（post 或 interactive）。"""
    if not text:
        text = "(已完成)"

    # 空回复不处理
    if not text.strip():
        return

    # 含表格 → 转为可视化排版（飞书 card markdown 元素不支持 GFM 表格）
    if has_table(text):
        text = render_table_visual(text)

    # 普通内容 → post 格式
    post_content = build_post_content(text)
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
        resp = _call_reply(req)
        if resp is None:
            break
        if not resp.success():
            _logger.warning(f"[feishu] post 回复失败 code={resp.code} msg={resp.msg}，降级为 text")
            # 只发送剩余未成功段落为纯文本
            remaining = [c for chunk in para_chunks[idx:] for p in chunk for el in p
                         for c in (el.get("text", "") + chr(10))]
            _reply_raw_text(message_id, "".join(remaining).strip() or text)
            break


def _reply_card(message_id: str, text: str) -> None:
    """用 interactive 卡片回复（含 markdown 元素，支持表格）。"""
    card = {
        "config": {"wide_screen_mode": True},
        "elements": [{"tag": "markdown", "content": build_card_content(text)}],
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
    resp = _call_reply(req)
    if resp is None:
        return
    if not resp.success():
        _logger.warning(f"[feishu] card 回复失败 code={resp.code} msg={resp.msg}")
        # 降级为 text（此时表格渲染为纯文本）
        _reply_raw_text(message_id, text)
    else:
        _logger.info(f"[feishu] card 回复成功")


def _reply_raw_text(message_id: str, text: str) -> None:
    """纯文本降级回复。"""
    chunks = [text[i:i + FS_MSG_MAX] for i in range(0, len(text), FS_MSG_MAX)] or [text]
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
        resp = _call_reply(req)
        if resp is None:
            break
        if not resp.success():
            _logger.warning(f"[feishu] 回复失败 code={resp.code} msg={resp.msg}")
            break


def _send_to_chat(chat_id: str, text: str) -> None:
    """主动发消息到会话（供 reminder 推送等场景使用）。"""
    if not text:
        return

    # 含表格 → interactive 卡片
    if has_table(text):
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": build_card_content(text)}],
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
            _logger.warning(f"[feishu] 主动发送 card 失败 code={resp.code} msg={resp.msg}")
        return

    # 普通内容 → post
    post_content = build_post_content(text)
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
            _logger.warning(f"[feishu] 主动发送失败 code={resp.code} msg={resp.msg}")
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
            _logger.warning(f"[feishu] 主动发送失败 code={resp.code} msg={resp.msg}")


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


# ── AI 资讯 ──────────────────────────────────────────────────────────────────

def _fetch_aihot_news() -> str:
    """获取 AI HOT 精选资讯，返回 Markdown。"""
    try:
        from scripts.aihot_push import _fetch_items, _build_markdown
        items = _fetch_items(mode="selected", hours=24)
        if not items:
            return "暂无 AI 资讯（API 可能暂时不可用，稍后重试）"
        return _build_markdown(items)
    except Exception as e:
        return f"获取 AI 资讯失败：{e}"


def _fetch_news_digest(top_k: int = 8) -> str:
    """获取校园新闻摘要，返回 Markdown。"""
    from sjtu_agent.news_aggregator import NewsAggregator
    from sjtu_agent.agent.chat_loop import load_agent_config
    from sjtu_agent.agent.runner import _make_client

    llm_client = None
    model = ""
    try:
        cfg = load_agent_config()
        if cfg.get("api_key") and cfg.get("model"):
            llm_client = _make_client(cfg)
            model = cfg["model"]
    except Exception:
        pass

    aggregator = NewsAggregator(llm_client=llm_client, model=model)
    md_digest, _, _ = aggregator.run(hours=24, top_k=top_k)
    return md_digest


def _fetch_eat_recommendation(campus: str = "闵行") -> str:
    """获取食堂推荐，返回 Markdown。"""
    from sjtu_agent.agent.tools._dining import tool_recommend_canteen, tool_get_canteen_crowd

    result = tool_recommend_canteen(campus=campus)
    if not result.get("ok"):
        crowd = tool_get_canteen_crowd(campus=campus)
        if crowd.get("ok"):
            lines = ["## 🍽️ 食堂实时拥挤度", ""]
            for c in crowd.get("canteens", []):
                label = c["overall_label"]
                status = ("🟢" if label in ("空闲",) else
                          "🟡" if label in ("适中",) else
                          "🟠" if label in ("较挤",) else "🔴")
                lines.append(f"- {status} **{c['name']}** — {label}（{c['overall_rate']}%）")
            return "\n".join(lines)
        return f"食堂数据暂时不可用：{result.get('error', '')}"

    lines = [
        f"## 🍽️ {result['meal_type']}推荐 · {result['campus']}校区",
        "",
        result.get("summary", ""),
        "",
    ]
    for r in result.get("recommendations", []):
        label = r["overall_label"]
        status = ("🟢" if label in ("空闲",) else
                  "🟡" if label in ("适中",) else
                  "🟠" if label in ("较挤",) else "🔴")
        lines.append(f"### {r['canteen_name']} {status} {label}（{r['overall_rate']}%）")
        for reason in r.get("reasons", []):
            lines.append(f"- {reason}")
        if r.get("recommended_sub_areas"):
            areas = "、".join(r["recommended_sub_areas"][:3])
            lines.append(f"- 推荐窗口：{areas}")
        lines.append("")

    if result.get("has_history"):
        lines.append(f"_基于 {result['history_count']} 条历史记录，推荐会越来越准_")
    lines.append("_用 `/eat 徐汇` 切换校区，选好后告诉我「我去XX吃了」帮你记录偏好_")

    return "\n".join(lines)


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
    # 自然语言触发"给我答案"（子串匹配，兼容标点符号）
    _at = text.strip()
    if any(kw in _at for kw in ["给我答案", "给答案", "核对答案", "我要答案",
                                  "获取完整解答", "看答案", "要答案", "上答案", "出答案"]):
        with _hw_ctx_lock:
            ctx = _hw_context.get(open_id, {})
        if ctx:
            return _do_hw_answer(open_id)
        return "[homework] 请先用 /hw do <序号> 分析作业，再要答案哦~"
    if not text.startswith("/"):
        return None
    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].lower() if parts else ""
    # 多对话命令 → 委托 ConversationManager
    conv_result = _conv_mgr.handle_command(open_id, cmd, parts)
    if conv_result is not None:
        return conv_result

    if cmd == "/help":
            return (
                "**飞书 Bot 命令帮助**\n\n"
                "[对话]  `/new <名称>`  `/list`  `/switch <N>`  `/name <N> <名>`  `/delete <N>`  `/history`\n\n"
                "[作业]  `/hw`  `/hw do <N>`  `/hw brief <N>`  `/hw due <N>`  `/hw past`  `/hw all`\n\n"
                "[新闻]  `/news`\n"
                "[食堂]  `/eat [闵行|徐汇|张江]`\n"
                "[AI]    `/aihot`  今日 AI 圈精选新闻\n\n"
                "[LaTeX] `/template`  `/template <名称>`  `/template compile`  `/template clone <id>`  `/template push`\n\n"
                "[信息]  查 DDL、看课表、查成绩、Canvas 课程公告和 quiz\n"
                "[记忆]  我会记住你聊过的课程、考试、学习偏好，下次对话自动关联\n\n"
                "[系统]  `/help`"
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
    if cmd == "/aihot":
        return "[aihot] 正在获取 AI 资讯…\n\n" + _fetch_aihot_news()
    if cmd == "/news_block":
        from sjtu_agent.news_aggregator.profile import UserProfile
        category = parts[1].strip() if len(parts) > 1 else ""
        if not category:
            return "[news] 请指定要屏蔽的分类，如 `/news_block 教务处`。可用分类：教务处、水源社区、交大新闻网、Canvas"
        UserProfile().block_category(category)
        return f"[news] 已屏蔽「{category}」类新闻，后续摘要将不再推送此类内容。用 `/news_reset` 可重置。"
    if cmd == "/news_reset":
        from sjtu_agent.news_aggregator.profile import UserProfile
        UserProfile().reset()
        return "[news] 已重置新闻画像，下次摘要将恢复默认推荐。"
    if cmd == "/eat":
        try:
            campus = parts[1].strip() if len(parts) > 1 else "闵行"
            valid = {"闵行", "徐汇", "张江"}
            if campus not in valid:
                return f"[eat] 未知校区「{campus}」，可选：{' / '.join(valid)}"
            return "[eat] 正在查询食堂拥挤度…\n\n" + _fetch_eat_recommendation(campus)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            _logger.error(tb)
            return f"[eat] 查询失败：{e}\n```\n{tb[-500:]}\n```"
    if cmd == "/template":
        sub = parts[1].strip() if len(parts) > 1 else ""
        action = sub.split()[0] if sub else ""
        rest = " ".join(sub.split()[1:]) if sub and " " in sub else ""

        from sjtu_agent.overleaf_client import (
            list_local_templates, apply_template, clone_template_from_overleaf,
            compile_latex, find_tex_file, push_to_overleaf,
        )

        if action == "compile":
            from sjtu_agent.paths import PAPERS_DIR
            tex = find_tex_file()
            if not tex:
                return f"[xelatex] 在 {PAPERS_DIR} 下未找到 .tex 文件。请先用 /template <name> 套用模板，放入文档后编译。"
            ok, output = compile_latex(tex)
            if ok:
                pdf = tex.with_suffix(".pdf")
                return f"[xelatex] 编译成功 ✅\nPDF: {pdf.name} ({pdf.stat().st_size // 1024} KB)"
            return f"[xelatex] 编译失败 ❌\n```\n{output}\n```"

        if action == "clone":
            args = rest.split() if rest else []
            if not args:
                return "用法: /template clone <project-id> [name]"
            pid = args[0]
            name = args[1] if len(args) > 1 else ""
            path = clone_template_from_overleaf(pid, name)
            if not path:
                return f"克隆失败: 请检查 project-id 是否正确，以及 Git 是否已配置。Overleaf Git Bridge URL: https://latex.sjtu.edu.cn/git/{pid}"
            return f"模板已克隆到 `{path}`\n\n/template {Path(path).name} 即可套用。"

        if action == "push":
            from sjtu_agent.paths import PAPERS_DIR
            target = PAPERS_DIR
            msg = push_to_overleaf(target)
            return f"[git] {msg[1]}"

        templates = list_local_templates()
        if not templates:
            return "暂无可用模板。用 /template clone <project-id> 从 Overleaf 克隆。"
        if not sub:
            lines = ["📄 **可用模板**："]
            for t in templates:
                src = "📦 内置" if t["source"] == "builtin" else "📥 下载"
                lines.append(f"  [{t['name']}] {t['description']} {src}")
            lines.append("\n子命令: /template <名称> | compile | clone <id> | push")
            return "\n".join(lines)

        match = next((t for t in templates if t["name"] == sub), None)
        if not match:
            return f"模板不存在: {sub}。用 /template 查看可用模板。"
        msg = apply_template(sub)
        return f"{msg}\n\n把你的文档文件放进去，然后 /template compile 编译。"
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
        _logger.error(f"[feishu] /hw 命令异常: {e}")
        _reply_text(message_id, f"[homework] 出错：{e}")
    finally:
        _hw_in_progress.discard(sender_open_id)


def _process_news_command(sender_open_id: str, message_id: str) -> None:
    """后台执行 /news 命令（网络 I/O + LLM 排序）。"""
    try:
        digest = _fetch_news_digest()
        _reply_text(message_id, f"📰 校园新闻摘要\n\n{digest}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        _logger.error(f"[feishu] /news 命令异常: {e}")
        _reply_text(message_id, f"[news] 出错：{e}")


def _build_parser_context(local_path: Path, media_type: str = "file", max_chars: int = 3000) -> tuple[str, str]:
    def _infer_backend_missing(parse_result: dict) -> str:
        text = " ".join(
            [
                str((parse_result or {}).get("error", "") or ""),
                str((parse_result or {}).get("content", "") or ""),
                " ".join(str(x) for x in ((parse_result or {}).get("warnings") or [])),
            ]
        ).lower()
        if "pdf ocr backend missing" in text or "pypdfium2" in text:
            return "pdf_ocr"
        if "whisper backend is not installed" in text or "asr backend missing" in text:
            return "whisper"
        if "paddleocr backend is not installed" in text or "ocr backend missing" in text or "ppt ocr backend missing" in text:
            return "paddleocr"
        return ""

    try:
        strategy = "auto"
        if media_type == "image":
            strategy = "paddleocr"
        elif media_type == "audio":
            strategy = "whisper"
        parse_result = agent.tool_parse_local_file(
            str(local_path),
            max_chars=4000,
            start_page=1,
            strategy=strategy,
        )
        if not (parse_result or {}).get("ok") and strategy in {"paddleocr", "whisper"}:
            parse_result = agent.tool_parse_local_file(
                str(local_path),
                max_chars=4000,
                start_page=1,
                strategy="auto",
            )
        extracted = (parse_result or {}).get("content", "")
        if extracted:
            parser_name = parse_result.get("parser", "unknown")
            return (
                f"\n\n以下是附件提取的文字内容（parser={parser_name}）：\n```\n{extracted[:max_chars]}\n```",
                "",
            )
        err = (parse_result or {}).get("error", "")
        warnings = (parse_result or {}).get("warnings") or []
        backend = _infer_backend_missing(parse_result or {})
        if backend:
            warn_text = "；".join(str(x) for x in warnings if x)
            return (
                (
                    "\n\n[附件解析状态]\n"
                    f"{warn_text or err or '检测到 OCR/ASR 解析模块缺失'}\n"
                    f"建议：先询问用户是否安装解析模块；若用户同意，再调用 install_parse_backend(backend='{backend}') 安装后重试解析。"
                ),
                "",
            )
        return "", (err or ("；".join(str(x) for x in warnings if x) or "解析结果为空"))
    except Exception as ex:
        return "", str(ex)


_CAPTURE_TIMEOUT = 120  # 单轮 LLM 调用最大等待秒数
_MEMORY_EXTRACT_EVERY = 10  # 每 N 轮对话尝试提取一次记忆
_msg_counters: dict[str, int] = {}  # per-user message counter for memory throttle


def _try_extract_memory(open_id: str, conv: dict) -> None:
    """Throttled memory extraction: every N messages, summarize and store."""
    cnt = _msg_counters.get(open_id, 0) + 1
    _msg_counters[open_id] = cnt
    if cnt % _MEMORY_EXTRACT_EVERY != 0:
        return
    try:
        from sjtu_agent.memory import summarize_session, store_memory
        summary = summarize_session(conv.get("messages", []))
        if summary:
            store_memory(open_id, summary, {
                "session_name": conv.get("name", ""),
                "msg_count": len(conv.get("messages", [])),
                "type": "session_summary",
            })
            _logger.info(f"[feishu] 记忆已提取: {summary[:60]}...")
    except Exception as e:
        _logger.warning(f"[feishu] 记忆提取失败: {e}")


def _run_fn_with_timeout(fn, timeout: float, *args):
    """在临时线程中运行 fn(*args)，超时抛出 TimeoutError。"""
    result = []
    exc = []
    done = threading.Event()

    def _wrapper():
        try:
            result.append(fn(*args))
        except Exception as e:
            exc.append(e)
        finally:
            done.set()

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
    if not done.wait(timeout):
        raise TimeoutError(f"操作超时（{timeout}秒）")
    if exc:
        raise exc[0]
    return result[0] if result else None


def _process_in_thread(sender_open_id: str, message_id: str, text: str) -> None:
    """Phase 2: 在后台线程中执行 LLM 推理 + 回复。"""
    # 防御性检查：如果主循环已拦截并回复，不再走 LLM
    t = text.strip() if text else ""
    if any(kw in t for kw in ["最近更新", "新功能", "新版变化", "更新了什么"]):
        return
    try:
        conv, meta, lock = _conv_mgr.get_active(sender_open_id)
    except Exception as e:
        _logger.warning(f"[feishu] get_active 异常: {e}")
        return
    if not lock.acquire(blocking=False):
        _reply_text(message_id, "上一条消息还在处理中，请稍候…")
        return
    try:
        reply = _run_fn_with_timeout(_capture_turn, _CAPTURE_TIMEOUT, conv, text, sender_open_id)
    except TimeoutError:
        _logger.warning(f"[feishu] LLM 调用超时（{_CAPTURE_TIMEOUT}s），释放锁")
        _reply_text(message_id, "处理超时，请稍后重试")
        return
    except Exception as e:
        _logger.warning(f"[feishu] 处理出错：{e}")
        _reply_text(message_id, f"出错了：{e}")
        return
    finally:
        _conv_mgr.save()
        lock.release()
    _reply_text(message_id, reply)
    # 记忆提取不阻塞锁 — 在发送回复后异步进行
    try:
        _try_extract_memory(sender_open_id, conv)
    except Exception:
        pass


def _process_media_in_thread(sender_open_id: str, message_id: str, msg_type: str, content_json: str) -> None:
    try:
        conv, meta, lock = _conv_mgr.get_active(sender_open_id)
    except Exception as e:
        _logger.warning(f"[feishu] get_active 异常: {e}")
        return
    if not lock.acquire(blocking=False):
        _reply_text(message_id, "上一条消息还在处理中，请稍候…")
        return

    def _do_media_process():
        media = _extract_media_ref(msg_type, content_json)
        if not media:
            _reply_text(message_id, f"暂不支持解析该消息内容（类型={msg_type}）。")
            return

        local_path = _download_feishu_resource(
            message_id=message_id,
            file_key=media["key"],
            msg_type=media["type"],
            filename=media.get("filename", ""),
        )
        model = conv["model_box"][0]

        if msg_type == "image":
            if _model_supports_vision(model):
                img_bytes = local_path.read_bytes()
                b64 = base64.b64encode(img_bytes).decode()
                content: list = [{"type": "text", "text": "用户发送了一张图片，请先描述图片内容，再回答用户问题。"}]
                if agent._is_anthropic_model(model):
                    content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                    })
                else:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    })
                reply = _capture_turn_multimodal(conv, content, sender_open_id)
                _reply_text(message_id, reply)
                return
            # 模型不支持视觉 → 不 return，落到下方 OCR 解析路径。
            # 之前这里直接 return 提示"不支持识图"，导致即使安装了 OCR
            # 后端（paddleocr/whisper）也从不尝试解析图片（issue #113 #2）。
            # 现在非视觉模型走 _build_parser_context 的 OCR 提取。

        parser_media_type = "audio" if msg_type == "audio" else ("image" if msg_type == "image" else "file")
        parsed_ctx, parse_err = _build_parser_context(local_path, media_type=parser_media_type)
        user_text = (
            f"[用户通过飞书发送了附件]\n"
            f"  类型：{msg_type}\n"
            f"  文件名：{local_path.name}\n"
            f"  本地路径：{local_path}\n"
            f"  文件大小：{local_path.stat().st_size // 1024} KB"
        )
        if parsed_ctx:
            user_text += parsed_ctx
        else:
            user_text += f"\n\n（附件解析失败：{parse_err}）"
        user_text += "\n\n请根据已提取内容回答；若信息不足，再向用户追问。"
        reply = _capture_turn(conv, user_text, sender_open_id)
        _reply_text(message_id, reply)

    try:
        _run_fn_with_timeout(_do_media_process, _CAPTURE_TIMEOUT)
    except TimeoutError:
        _logger.warning(f"[feishu] 媒体处理超时（{_CAPTURE_TIMEOUT}s），释放锁")
        _reply_text(message_id, "处理超时，请稍后重试")
    except Exception as e:
        _logger.warning(f"[feishu] 媒体处理出错：{e}")
        _reply_text(message_id, f"附件处理失败：{e}")
    finally:
        _conv_mgr.save()
        lock.release()


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
            _logger.info(f"[feishu] 跳过重复消息 message_id={message_id}")
            return

        # ── 忽略积压的旧消息（Bot 断连期间飞书积累的事件，重启后被重放）──
        _MAX_MSG_AGE_SEC = 300  # 5 min — covers bot restart warmup (health check ~30s)
        create_time_ms = int(getattr(msg, "create_time", 0) or 0)
        if create_time_ms and time.time() - create_time_ms / 1000 > _MAX_MSG_AGE_SEC:
            _logger.info(f"[feishu] 跳过过期消息 message_id={message_id} "
                         f"age={time.time() - create_time_ms / 1000:.0f}s")
            return

        media_supported = msg_type in {"image", "file", "audio", "media"}
        text = ""
        if msg_type == "text":
            text = _extract_text(msg.content)
            if not text:
                return
            # 内容去重：防止飞书用不同 message_id 重发同一事件
            if _is_duplicate_content(sender_open_id, text):
                _logger.info(f"[feishu] 跳过重复内容: {text[:40]!r}")
                return
        elif not media_supported:
            _reply_text(message_id, f"(暂不支持的消息类型: {msg_type}，目前支持文本、图片、文件、音频、视频)")
            return

        # ── 自然语言短语拦截 ────────────────────────────────────────
        t = text.strip() if text else ""
        if any(kw in t for kw in ["最近更新", "新功能", "新版变化", "更新了什么"]):
            now = time.time()
            with _cooldown_lock:
                last = _recent_updates_cooldown.get(sender_open_id, 0)
                if now - last < _COOLDOWN_SEC:
                    return  # 冷却期内，跳过重复
                _recent_updates_cooldown[sender_open_id] = now
            _logger.info(f"[feishu] 拦截近期更新: {text[:40]!r}")
            _reply_text(message_id, _RECENT_UPDATES_TEXT)
            return

        # 过滤"清空聊天记录"/撤回消息产生的系统通知
        if text in {"此消息已删除", "该消息已被撤回"}:
            _logger.info(f"[feishu] 跳过已删除/撤回的系统消息 message_id={message_id}")
            return

        # 白名单检查（所有命令和对话均需校验）
        if ALLOWED_OPEN_IDS and sender_open_id not in ALLOWED_OPEN_IDS:
            _logger.info(f"[feishu] [!] 未授权 open_id：{sender_open_id}")
            _reply_text(message_id, "你不在该机器人的允许列表中。\n"
                        f"请把这个 open_id 加入 config.json 的 feishu_allowed_open_ids:\n"
                        f"{sender_open_id}")
            return

        # ── 多对话命令拦截 ──────────────────────────────────────────
        # /hw 系列是重命令（网络 I/O + LLM），放到后台线程避免阻塞 event loop
        if t.lower().startswith("/hw"):
            _logger.info(f"[feishu] 命令（后台执行）: {text[:40]!r}")
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
            if sender_open_id in _hw_in_progress:
                _reply_text(message_id, "[homework] 上一个作业命令仍在处理，请稍候…")
                return
            _hw_in_progress.add(sender_open_id)
            _reply_text(message_id, "[homework] 正在处理，请稍候…")
            _EXECUTOR.submit(_process_hw_command, sender_open_id, message_id, text)
            return

        # /news 也是重命令（网络 I/O + LLM 排序），后台执行
        if t.lower().startswith("/news"):
            _logger.info(f"[feishu] 命令（后台执行）: {text[:40]!r}")
            _reply_text(message_id, "[news] 正在生成校园新闻摘要，请稍候…")
            _EXECUTOR.submit(_process_news_command, sender_open_id, message_id)
            return

        cmd_result = _handle_commands(sender_open_id, text)
        if cmd_result is not None:
            _logger.info(f"[feishu] 命令: {text[:40]!r}")
            _reply_text(message_id, cmd_result)
            return

        _logger.info(f"[feishu] 收到消息 from open_id={sender_open_id[:12]}… "
                     f"chat_type={chat_type} text={text[:60]!r}")

        if WHOAMI_MODE:
            _reply_text(
                message_id,
                f"你的 open_id 是:\n{sender_open_id}\n\n"
                f"请把它加入 config.json 的 feishu_allowed_open_ids 后重启 bot。",
            )
            return

        if ALLOWED_OPEN_IDS and sender_open_id not in ALLOWED_OPEN_IDS:
            _logger.info(f"[feishu] [!] 未授权 open_id：{sender_open_id}")
            _reply_text(message_id, "你不在该机器人的允许列表中。\n"
                        f"请把这个 open_id 加入 config.json 的 feishu_allowed_open_ids:\n"
                        f"{sender_open_id}")
            return

        if not ALLOWED_OPEN_IDS:
            _logger.info(f"[feishu] [i] 白名单为空，已允许所有人；建议把此 open_id 加入白名单："
                         f"{sender_open_id}")

        # ── 保存 open_id 供 daily_report 推送使用 ──────────────────────
        if sender_open_id:
            try:
                cfg_data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                _logger.info(f"[feishu] 跳过 open_id 持久化：config.json 读取失败: {e}")
                cfg_data = None
            if cfg_data is not None and cfg_data.get("feishu_open_id") != sender_open_id:
                try:
                    cfg_data["feishu_open_id"] = sender_open_id
                    CONFIG_PATH.write_text(json.dumps(cfg_data, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as e:
                    _logger.info(f"[feishu] open_id 写入失败: {e}")

        # ── 提交到后台线程，立即返回 ──
        if msg_type == "text":
            _EXECUTOR.submit(_process_in_thread, sender_open_id, message_id, text)
        else:
            _EXECUTOR.submit(_process_media_in_thread, sender_open_id, message_id, msg_type, msg.content)

    except Exception as e:
        _logger.warning(f"[feishu] handler 异常：{e}")


# ── 心跳与健壮性 ────────────────────────────────────────────────────────────

def _heartbeat_worker(interval: float = 30) -> None:
    """后台线程：每 interval 秒写一次心跳时间戳。"""
    import atexit as _atexit
    from sjtu_agent.paths import DATA_DIR
    hb_file = DATA_DIR / "feishu_heartbeat.json"
    _atexit.register(lambda: hb_file.write_text(
        json.dumps({"status": "stopped", "last_heartbeat": ""}, ensure_ascii=False),
        encoding="utf-8",
    ) if hb_file.parent.exists() else None)
    while True:
        try:
            hb_file.parent.mkdir(parents=True, exist_ok=True)
            hb_file.write_text(json.dumps({
                "status": "running",
                "last_heartbeat": _dt.datetime.now().isoformat(),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        time.sleep(interval)


def _startup_health_check() -> None:
    """启动自检：验证凭据、ChromaDB、Agent API。失败则打印原因并退出。"""
    all_ok = True

    # 1. 凭据（已在模块加载时检查过，这里只确认）
    if not APP_ID or not APP_SECRET:
        print("[X] 缺 feishu_app_id / feishu_app_secret")
        sys.exit(1)
    print("[✓] 飞书凭据已配置")

    # 2. ChromaDB 可用性
    try:
        from sjtu_agent.memory import _get_client, build_memory_context
        _client = _get_client(str(DATA_DIR / "chroma_memory"))
        print("[✓] ChromaDB 就绪")
    except Exception as e:
        print(f"[!] ChromaDB 不可用（记忆功能将停用）: {e}")
        # 不阻塞启动

    # 3. Agent API 连通性
    try:
        cfg = agent.load_agent_config()
        if cfg.get("api_key"):
            print(f"[✓] Agent API 已配置 (model={cfg.get('model', '?')})")
        else:
            print("[!] Agent API Key 未配置（对话功能不可用）")
            all_ok = False
    except Exception as e:
        print(f"[!] Agent API 检查失败: {e}")
        all_ok = False

    if not all_ok:
        print("[i] 部分服务不可用，Bot 将继续启动但功能受限")


def _shutdown_cleanup() -> None:
    """atexit: 关闭线程池、清理临时文件。"""
    try:
        _EXECUTOR.shutdown(wait=False)
    except Exception:
        pass
    try:
        import shutil
        if _TMP_DIR.exists():
            shutil.rmtree(str(_TMP_DIR), ignore_errors=True)
    except Exception:
        pass


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

    # 启动自检
    _startup_health_check()

    # atexit 清理
    import atexit as _atexit
    _atexit.register(_shutdown_cleanup)

    # 心跳线程
    threading.Thread(target=_heartbeat_worker, daemon=True, name="feishu-hb").start()

    client = _build_ws_client()
    print(f"[OK] 飞书 bot 已启动（App ID: {APP_ID[:10]}…），等待消息…")
    if not ALLOWED_OPEN_IDS:
        print("[i] feishu_allowed_open_ids 为空，所有人均可对话。建议加白名单后重启。")
    client.start()  # 阻塞，内部 WS 自动重连


if __name__ == "__main__":
    main()
