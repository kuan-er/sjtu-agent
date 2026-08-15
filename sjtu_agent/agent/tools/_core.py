"""sjtu_agent/agent/tools.py — 工具定义（TOOLS）与所有 tool_xxx 实现。

包含：
- TOOLS 列表（OpenAI function calling 格式）
- 所有 tool_xxx 函数（配置/DDL/作业/校园服务/成绩/提醒/邮件等）
- run_tool() 分发函数
- DDL 缓存辅助（_ddl_cache_*、_fetch_ddls_parallel）
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from sjtu_agent.paths import (
    AGENT_CONFIG_PATH,
    CARE_STATE_PATH,
    CONFIG_PATH,
    DDL_CACHE_PATH,
    ENV_PATH,
    MYSJTU_CATALOG_PATH,
    PACKAGE_ROOT,
    PROJECT_ROOT,
    REMINDERS_PATH,
    SHUIYUAN_PROFILE_DIR,
    USER_PROFILE_PATH,
    atomic_write_json,
    read_json_safe,
)
from sjtu_agent.parsing import parse_file as parse_router_file
from sjtu_agent.config import cfg as _cfg
from sjtu_agent.logging import get_logger

ROOT = PROJECT_ROOT
_logger = get_logger("tools")
_INTERACTIVE_CHAT_ENV = "SJTU_AGENT_INTERACTIVE_CHAT"
_PARSE_BACKEND_INSTALL = {
    "paddleocr": {"label": "OCR", "modules": ["paddleocr"], "packages": ["paddleocr==3.6.0"]},
    "whisper": {"label": "ASR", "modules": ["whisper"], "packages": ["openai-whisper==20250625"]},
    "pdf_ocr": {"label": "PDF OCR", "modules": ["paddleocr", "pypdfium2"], "packages": ["paddleocr==3.6.0", "pypdfium2>=4.30,<5"]},
}

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

import ddl_checker as dc
from sjtu_agent.canvas_client import CanvasError, make_client_from_config
from sjtu_agent.agent.tools._canvas_utils import (
    _make_canvas_client,
    _canvas_error_payload,
    _resolve_canvas_course_or_error,
    _canvas_settings_url,
)
from sjtu_agent.canvas_monitor import update_canvas_monitor_config

from sjtu_agent.agent.tools._reminders import (
    TOOLS_ENTRIES as _REMINDER_TOOLS,
    _load_reminders, _save_reminders,
    tool_add_reminder, tool_list_reminders, tool_remove_reminder,
)
from sjtu_agent.agent.tools._user_profile import (
    TOOLS_ENTRIES as _USER_PROFILE_TOOLS,
    tool_get_user_profile, tool_update_user_profile,
)
from sjtu_agent.agent.tools._report_prefs import (
    TOOLS_ENTRIES as _REPORT_PREFS_TOOLS,
    tool_get_report_preferences, tool_update_report_preferences,
)
from sjtu_agent.agent.tools._python_exec import (
    TOOLS_ENTRIES as _PYTHON_EXEC_TOOLS,
    tool_execute_python,
)


from sjtu_agent.agent.tools._email import (
    TOOLS_ENTRIES as _EMAIL_TOOLS,
    tool_read_emails, tool_search_emails, tool_send_email,
)
from sjtu_agent.agent.tools._platforms import (
    TOOLS_ENTRIES as _PLATFORM_TOOLS,
    tool_setup_telegram, tool_setup_wechat, tool_setup_feishu, tool_setup_qq,
    tool_qq_add_user, tool_qq_list_users, tool_qq_remove_user,
)

from sjtu_agent.agent.tools._mcp_skills import (
    TOOLS_ENTRIES as _MCP_SKILLS_TOOLS,
    tool_add_mcp_server, tool_add_skill, tool_create_skill, tool_list_skills, tool_manage_skill,
)

from sjtu_agent.agent.tools._canvas_files import (
    TOOLS_ENTRIES as _CANVAS_FILES_TOOLS,
    tool_list_canvas_folders, tool_list_canvas_files, tool_canvas_file_tree,
    tool_download_canvas_file,
    tool_canvas_track_mark, tool_canvas_track_unmark, tool_canvas_track_list,
    tool_canvas_track_status, tool_canvas_track_diff, tool_canvas_track_mark_course,
)
from sjtu_agent.agent.tools._dining import (
    TOOLS_ENTRIES as _DINING_TOOLS,
    tool_get_canteen_crowd, tool_get_canteen_info,
    tool_recommend_canteen, tool_record_dining_choice,
    tool_get_dining_history,
)
from sjtu_agent.agent.tools._changelog import (
    TOOLS_ENTRIES as _CHANGELOG_TOOLS,
    tool_get_recent_updates,
)
from sjtu_agent.agent.tools._bot_setup import (
    TOOLS_ENTRIES as _BOT_SETUP_TOOLS,
    tool_get_bot_setup_guide,
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ddls",
            "description": "获取所有平台（Canvas / AI 好课（aihaoke） / 中国大学MOOC）未完成 DDL，按截止时间升序。默认自动过滤 Canvas 课程通知（评分/问卷等非作业条目）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skip_canvas":  {"type": "boolean"},
                    "skip_aihaoke": {"type": "boolean"},
                    "skip_icourse": {"type": "boolean"},
                    "classify": {
                        "type": "boolean",
                        "description": "是否对 Canvas 作业进行智能分类（区分真实作业与课程通知）。日报和用户主动查 DDL 时传 true。",
                    },
                    "include_notifications": {
                        "type": "boolean",
                        "description": "是否包含课程通知类条目。用户说「全部」「包括通知」时传 true。默认 false（过滤通知）。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_next_lab",
            "description": "获取下一次物理实验课（phycai 实验室预约）安排，包括名称、时间、地点。注意：这是实验课预约，不是作业。用户说'实验安排'、'物理实验课'、'下次实验'时调用，不要因为'物理作业'触发此工具。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_all",
            "description": "一次性获取所有平台 DDL 和下一次物理实验安排。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skip_canvas":  {"type": "boolean"},
                    "skip_aihaoke": {"type": "boolean"},
                    "skip_icourse": {"type": "boolean"},
                    "skip_phycai":  {"type": "boolean"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_setup",
            "description": "检查当前环境配置状态：各平台凭证是否存在、Cookie 是否存在。启动时必须调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_shuiyuan",
            "description": "配置或刷新水源社区登录态。当前版本保存 session cookie，无需 User API Key；已有有效 cookie 时不会重新登录。用户说'配置水源'/'授权水源'/'设置水源'时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_shuiyuan_cookie",
            "description": "保存用户从浏览器开发者工具复制的 shuiyuan.sjtu.edu.cn Cookie（完整 Cookie 头或单个 session token），并自动校验。自动登录水源失败时，引导用户复制 Cookie 后调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cookie_text": {"type": "string", "description": "用户粘贴的 Cookie 文本，例如 _forum_session=xxx; _t=yyy"}
                },
                "required": ["cookie_text"],
            },
        },
    },
    *_MCP_SKILLS_TOOLS,
    {
        "type": "function",
        "function": {
            "name": "setup_course_community",
            "description": (
                "登录选课社区 course.sjtu.plus 并保存 session cookie（首选邮箱密码登录端点）。"
                "默认会用 jAccount 用户名拼出 <user>@sjtu.edu.cn 作为账号，密码默认复用 jAccount 密码"
                "（很多用户两者一致）。若不一致，用 password 参数显式传入站内密码。"
                "首次调用建议不传参数直接尝试；若返回 401/403 说明密码不一致，再向用户索取站内密码。"
                "用户说『配置选课社区』『授权选课社区』『登录 course.sjtu.plus』时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "course.sjtu.plus 用户名（一般是 jAccount 用户名）"},
                    "password": {"type": "string", "description": "course.sjtu.plus 站内密码（**不是** jAccount 密码）"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_courses",
            "description": (
                "在选课社区 course.sjtu.plus 搜索课程，返回候选课程列表（id/课名/老师/评分/评价数）。"
                "用户问『XX 课怎么样』『XX 老师的 XX 课口碑如何』『推荐选什么课』『XX 课难不难』等选课/课评相关问题时优先调用此工具，"
                "再用 get_course_detail 读取详情和评价。比 search_campus 更专门、信息更结构化。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，可以是课程名、老师名、课程代码"},
                    "page_size": {"type": "integer", "description": "返回结果数，默认 8，最大 20"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_course_detail",
            "description": (
                "查看 course.sjtu.plus 上某门课的详情和最新若干条学生评价。"
                "通常在 search_courses 拿到 course_id 后调用，用来回答『这门课具体咋样』『有什么真实评价』。"
                "**禁止编造评价内容**：用户想了解课程口碑必须用此工具读取真实评价。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {"type": "integer", "description": "课程 id（来自 search_courses 结果）"},
                    "max_reviews": {"type": "integer", "description": "最多返回多少条评价，默认 10，最大 20"},
                },
                "required": ["course_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_canvas",
            "description": "配置 Canvas API Token。优先在具备 jAccount 凭据和 Playwright 时尝试自动创建并保存 token；如果自动流程失败，再回退到手动引导。用户说'配置Canvas'/'设置Canvas'/'Canvas token 不会弄'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "open_browser": {
                        "type": "boolean",
                        "description": "是否尝试打开 Canvas 设置页，默认 true"
                    },
                    "auto_create": {
                        "type": "boolean",
                        "description": "是否尝试通过 Playwright 自动创建并保存 Canvas token，默认 false"
                    },
                    "token_purpose": {
                        "type": "string",
                        "description": "自动创建 token 时填写的用途，默认 SJTU Agent"
                    }
                },
                "required": []
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_credentials",
            "description": "将用户提供的账号凭证保存到本地 .env 和 config.json，仅传入已提供的字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "jaccount_username": {"type": "string", "description": "交大 jAccount 用户名（用于 AI 好课（aihaoke）和物理实验）"},
                    "jaccount_password": {"type": "string", "description": "交大 jAccount 密码"},
                    "canvas_token":      {"type": "string", "description": "Canvas API Token"},
                    "mooc_username":     {"type": "string", "description": "中国大学MOOC 手机号"},
                    "mooc_password":     {"type": "string", "description": "中国大学MOOC 密码"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "login_platform",
            "description": "为指定平台执行 Playwright 自动登录，刷新 Cookie。保存凭证后调用此工具验证。",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["aihaoke", "phycai", "icourse"],
                    },
                },
                "required": ["platform"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_assignment_files",
            "description": (
                "列出本地 assignments/ 目录下已下载的作业文件。"
                "用户问「有哪些作业」「下载了什么」「列出作业文件」时调用。"
                "返回课程-作业-文件的树状结构。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "course_filter": {
                        "type": "string",
                        "description": "只列出名称包含此字符串的课程，留空则列出全部",
                    },
                    "assignments_dir": {
                        "type": "string",
                        "description": "作业目录，默认 ./assignments",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_assignment_file",
            "description": (
                "读取本地作业文件的文字内容（支持 PDF 和 HTML）。"
                "用户问「第一题是什么」「这道题怎么做」「帮我看看作业内容」时，"
                "先用 list_assignment_files 找到文件路径，再调用此工具读取内容，然后回答。"
                "注意：PDF 中的数学公式可能无法完整提取，需结合上下文理解。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "文件的完整路径（从 list_assignment_files 结果获取）",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "最多返回的字符数，默认 8000，超长文档可分段读取",
                    },
                    "start_page": {
                        "type": "integer",
                        "description": "PDF 从第几页开始读（1-indexed），默认 1",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_local_file",
            "description": (
                "统一解析本地文件内容（支持多种文本/文档/图片/音频格式，按后端能力自动路由）。"
                "优先用于 read_assignment_file 不支持的类型。"
                "当 strategy=auto 时会自动选择可用解析器。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "本地文件路径"},
                    "max_chars": {"type": "integer", "description": "最多返回字符数，默认 8000"},
                    "start_page": {"type": "integer", "description": "PDF 起始页（1-indexed），默认 1"},
                    "strategy": {
                        "type": "string",
                        "description": "auto/legacy/markitdown/docling/mineru/paddleocr/whisper/pdf_ocr",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_local_files",
            "description": (
                "批量解析多个本地文件并合并结果。"
                "适合用户一次上传多个文件（题面+附录+图片）时统一抽取内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "本地文件路径列表",
                    },
                    "per_file_max_chars": {"type": "integer", "description": "每个文件最大字符数，默认 4000"},
                    "total_max_chars": {"type": "integer", "description": "合并内容总字符上限，默认 12000"},
                    "start_page": {"type": "integer", "description": "PDF 起始页（1-indexed），默认 1"},
                    "strategy": {
                        "type": "string",
                        "description": "auto/legacy/markitdown/docling/mineru/paddleocr/whisper/pdf_ocr",
                    },
                },
                "required": ["file_paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_parse_backend",
            "description": (
                "Install parsing backends for OCR/ASR when missing. "
                "Call only after user confirms installation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "backend": {
                        "type": "string",
                        "description": "paddleocr/whisper/pdf_ocr",
                    },
                },
                "required": ["backend"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_assignments",
            "description": (
                "下载近期作业材料（Canvas 题目说明/附件 + AI 好课（aihaoke）作业页面截图/附件），"
                "保存到本地 assignments/ 目录。返回每个作业的保存路径。"
                "用户说「下载作业」「帮我把题目下载下来」时调用。"
                "默认只下载近期作业；如果上下文里已经明确提到某门课或某个作业，必须传过滤条件，避免扫全平台。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skip_canvas":  {"type": "boolean", "description": "跳过 Canvas，默认 false"},
                    "skip_aihaoke": {"type": "boolean", "description": "跳过 AI 好课（aihaoke），默认 false"},
                    "course_filter": {
                        "type": "string",
                        "description": "只下载名称包含此字符串的课程。上下文已明确课程时必须填写",
                    },
                    "assignment_filter": {
                        "type": "string",
                        "description": "只下载名称包含此字符串的作业。上下文已明确作业名时必须填写",
                    },
                    "due_within_days": {
                        "type": "integer",
                        "description": "只下载未来多少天内截止的作业，默认 7。若用户明确要求全部长期作业，可设更大值",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "保存目录，默认 ./assignments",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_canvas_courses",
            "description": "列出当前 Canvas active 课程，可选包含 tabs 和教师信息。用户问 Canvas 有哪些课程、课程 ID、课程代码时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_tabs": {"type": "boolean", "description": "是否包含课程 tabs，默认 false"},
                    "include_teachers": {"type": "boolean", "description": "是否包含教师列表，默认 false"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_course_announcements",
            "description": "按 Canvas 课程名、课程代码或 course_id 查看某门课公告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course": {"type": "string", "description": "课程名、课程代码或 Canvas course_id"},
                    "limit": {"type": "integer", "description": "最多返回公告数，默认 20"},
                    "since_days": {"type": "integer", "description": "只看最近多少天，可不传"},
                },
                "required": ["course"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_course_quizzes",
            "description": "按 Canvas 课程名、课程代码或 course_id 查看某门课 quiz/测验。优先 Classic Quizzes，并补充 quiz-backed assignments。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course": {"type": "string", "description": "课程名、课程代码或 Canvas course_id"},
                    "include_past": {"type": "boolean", "description": "是否包含已过期 quiz，默认 false"},
                    "include_assignment_backed": {"type": "boolean", "description": "是否从 assignments 补充识别 quiz，默认 true"},
                },
                "required": ["course"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_course_updates",
            "description": "聚合查看某门 Canvas 课程的公告、quiz、作业和 activity stream。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course": {"type": "string", "description": "课程名、课程代码或 Canvas course_id"},
                    "include": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要包含的 sections，默认 announcements/quizzes/assignments/activity",
                    },
                    "limit": {"type": "integer", "description": "每类最多返回数量，默认 10"},
                    "include_past": {"type": "boolean", "description": "是否包含已过期 quiz/作业，默认 false"},
                },
                "required": ["course"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_todo",
            "description": "查看 Canvas 全局 todo 和 planner items，用于回答近期待办。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "最多返回数量，默认 20"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_canvas_monitor",
            "description": (
                "配置 Canvas watcher 的定时监控参数。用户要求调整 Canvas 监控间隔、监控课程、"
                "公告/quiz/作业监控开关、通知渠道，或暂停/启用 Canvas 监控时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "description": "是否启用 Canvas 监控"},
                    "interval_seconds": {"type": "integer", "description": "检查间隔秒数，最小 30 秒"},
                    "interval_minutes": {"type": "number", "description": "检查间隔分钟数，会换算成秒；优先于 interval_seconds"},
                    "course_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "要监控的 Canvas course_id 列表；优先于 course_filters",
                    },
                    "course_filters": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按课程名或课程代码关键词筛选监控课程",
                    },
                    "include_announcements": {"type": "boolean", "description": "是否监控公告"},
                    "include_quizzes": {"type": "boolean", "description": "是否监控 quiz"},
                    "include_assignments": {"type": "boolean", "description": "是否监控作业"},
                    "include_activity": {"type": "boolean", "description": "是否监控 course activity stream"},
                    "notify_channels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "通知渠道，可用 system/telegram/feishu/wechat",
                    },
                    "baseline_on_first_run": {"type": "boolean", "description": "首次运行是否只建立基线，不推送历史内容"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_canvas_assignments",
            "description": (
                "列出 Canvas 上允许文件提交（online_upload）的作业，含课程ID、作业ID。"
                "用户想提交作业但没有提供 course_id/assignment_id 时，先调此工具让用户确认目标作业。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "course_filter": {
                        "type": "string",
                        "description": "只列出名称包含此字符串的课程，留空则列全部",
                    },
                    "include_past": {
                        "type": "boolean",
                        "description": "是否包含已过期作业，默认 false",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_canvas_assignment",
            "description": (
                "将本地文件上传并提交到 Canvas 指定作业。"
                "必须先知道 course_id 和 assignment_id（可先调 list_canvas_assignments 获取）。"
                "用户把 PDF/文件拖入终端后得到路径，说'帮我提交这个文件'时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "本地文件的绝对路径，如 /Users/xxx/hw1.pdf",
                    },
                    "course_id": {
                        "type": "integer",
                        "description": "Canvas 课程 ID（从 list_canvas_assignments 获取）",
                    },
                    "assignment_id": {
                        "type": "integer",
                        "description": "Canvas 作业 ID（从 list_canvas_assignments 获取）",
                    },
                    "comment": {
                        "type": "string",
                        "description": "可选：提交时附加的文字备注",
                    },
                },
                "required": ["file_path", "course_id", "assignment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_campus",
            "description": (
                "搜索交大校园相关网站的内容。"
                "支持：jwc（教务处通知公告）、shuiyuan（水源社区论坛帖子）、dyweb（传承·交大课程资料）。"
                "重要：若用户明确指定了某个网站（如'水源'、'教务处'、'传承'），"
                "必须在 sites 中只填该网站，不得多填其他网站。"
                "只有用户未指定网站时才搜全部。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如「期末考试」「选课」「转专业」",
                    },
                    "sites": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["jwc", "shuiyuan", "dyweb"]},
                        "description": (
                            "要搜索的网站。"
                            "用户说'水源/水源社区/bbs'→必须只填[\"shuiyuan\"]；"
                            "用户说'教务处/jwc'→必须只填[\"jwc\"]；"
                            "用户说'传承/dyweb'→必须只填[\"dyweb\"]；"
                            "用户未指定平台→不传此参数，搜全部。"
                            "绝对不能在用户只要水源时多加jwc或dyweb。"
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "每个网站最多返回几条结果，默认 6",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_shuiyuan_topic",
            "description": (
                "读取水源社区某个具体帖子的完整内容（含原帖正文和所有回复）。"
                "当用户在 search_campus 搜索到水源帖子后想看具体内容，"
                "或用户直接给出水源帖子 URL / topic id 说「看看这个帖子都讨论了什么」时调用。"
                "**禁止编造帖子内容**：想了解某帖子讨论就必须用此工具读取，不得凭标题/摘要臆测。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "水源帖子 URL（如 https://shuiyuan.sjtu.edu.cn/t/topic/471260）或 topic id（如 471260）",
                    },
                    "max_posts": {
                        "type": "integer",
                        "description": "最多返回前多少楼（含主楼），默认 30",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_schedule",
            "description": (
                "查询课表。"
                "用户问「今天有什么课」「明天几点上课」「本周课表」「下周有没有课」等时调用。"
                "query_type='day' 查某天课程，query_type='week' 查某周课表。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["day", "week"],
                        "description": "day=查某天，week=查某周",
                    },
                    "date": {
                        "type": "string",
                        "description": (
                            "query_type=day 时使用。"
                            "'今天'/'明天'/'后天'/'昨天' 或 'YYYY-MM-DD'，留空=今天"
                        ),
                    },
                    "week_offset": {
                        "type": "integer",
                        "description": (
                            "query_type=week 时使用。"
                            "0=本周（默认），1=下周，-1=上周"
                        ),
                    },
                    "set_semester_start": {
                        "type": "string",
                        "description": (
                            "如果用户告知学期起始日期，传入 YYYY-MM-DD（必须是周一）。"
                            "仅在用户明确说出起始日期时才传。"
                        ),
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "true=强制忽略缓存重新拉取课表。仅在用户明确说「刷新课表」「更新课表」时才传 true。",
                    },
                },
                "required": ["query_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse_mysjtu",
            "description": (
                "在浏览器中自动操作 my.sjtu.edu.cn 完成查询或业务办理。"
                "适用于：查成绩、查绩点、查奖学金、查培养方案、办理注册手续、预约校车班车、办理各类申请等。"
                "不适用于：课表（用 get_schedule）、DDL（用 get_ddls）、搜索（用 search_campus）。"
                "遇到需要点击、填表、导航的情况也可以用，通过 action 参数传入操作指令。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "要完成的任务，用自然语言描述，例如「查看本学期所有课程成绩」「预约明天去徐汇的班车」",
                    },
                    "start_url": {
                        "type": "string",
                        "description": "起始 URL，默认 https://my.sjtu.edu.cn，可指定具体子页面加快速度",
                    },
                    "action": {
                        "type": "string",
                        "description": (
                            "可选的具体操作指令（在上一次 browse_mysjtu 返回页面内容后用）。"
                            "格式：'click:文本' 点击包含该文本的链接/按钮；"
                            "'goto:URL' 直接跳转；"
                            "'search:关键词' 在搜索框输入并搜索。"
                            "留空则只读取当前/起始页面内容。"
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_mysjtu_catalog",
            "description": (
                "爬取 my.sjtu.edu.cn 所有分类和服务，建立本地缓存供后续快速查找。"
                "首次使用 browse_mysjtu 前可先调用一次，以后每隔数周刷新一次即可。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_grades",
            "description": (
                "直接从教学信息服务网 (i.sjtu.edu.cn) 查询学生成绩和绩点，自动完成 jAccount SSO。"
                "用户说「查成绩」「上学期成绩」「查绩点」「GPA」「看看我的成绩」「本学年成绩」等时调用。"
                "比 browse_mysjtu 更快更准，直接返回结构化的成绩列表和加权绩点。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "string",
                        "description": (
                            "学年起始年份，如 '2025' 表示 2025-2026 学年。"
                            "不传或空字符串=查全部学年。"
                            "'上学年'/'去年'→当前年份减1；"
                            "'本学年'/'今年'→当前年份（如 2025）。"
                        ),
                    },
                    "semester": {
                        "type": "string",
                        "enum": ["", "1", "2", "3"],
                        "description": (
                            "'1'=第1学期(秋季/上学期)，'2'=第2学期(春季/下学期)，"
                            "'3'=第3学期(夏季)，''=全部学期。"
                            "用户说'上学期'→通常是'1'（秋季学期）；'下学期'→'2'；不指定→''。"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    *_REMINDER_TOOLS,
    *_USER_PROFILE_TOOLS,
    *_REPORT_PREFS_TOOLS,
    *_PLATFORM_TOOLS,
    *_CHANGELOG_TOOLS,
    *_BOT_SETUP_TOOLS,
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "抓取网页内容并提取纯文本。"
                "用户发送网址链接（微信公众号、新闻、讲座通知等）时调用此工具获取页面内容。"
                "返回网页标题和正文文本，自动去除 HTML 标签和脚本。"
                "适用于：微信公众号文章、校园新闻、讲座通知、活动页面等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的网址（支持 http/https）",
                    },
                },
                "required": ["url"],
            },
        },
    },
    *_EMAIL_TOOLS,
    *_CANVAS_FILES_TOOLS,
    *_DINING_TOOLS,
]

# ══════════════════════════════════════════════════════════════════════════════
# my.sjtu.edu.cn 服务目录缓存
# ══════════════════════════════════════════════════════════════════════════════

_CANVAS_DEFAULT_BASE_URL = "https://oc.sjtu.edu.cn"
_CANVAS_SETUP_REASON = (
    "Canvas Access Token 只会在用户点击“新增访问令牌”后显示一次，"
    "当前 Agent 只会在具备 jAccount 凭据和 Playwright 时尝试自动创建；"
    "如果自动流程不可靠或失败，就会回退到手动引导。"
)
_CANVAS_SETUP_STEPS = [
    "打开 Canvas 并完成 jAccount 登录。",
    "进入「账户 / Account」->「设置 / Settings」。",
    "在页面下方找到「已批准的集成 / 访问许可证」。",
    "点击「+ 新增访问令牌 / New Access Token」。",
    "用途建议填写 SJTU Agent；过期时间可按需设置。",
    "复制弹出的 token（只显示一次），原样发给我。",
    "我会调用 save_credentials 把 token 保存到本地 config.json。",
]

# 常见别名映射（中文俗称 → 服务名关键词）
_MYSJTU_ALIASES: dict[str, list[str]] = {
    "班车": ["学生预约乘车", "Shuttle Bus"],
    "校车": ["学生预约乘车", "Shuttle Bus"],
    "乘车": ["学生预约乘车"],
    "预约乘车": ["学生预约乘车"],
    "洗澡": ["学生洗浴"],
    "洗浴": ["学生洗浴"],
    "电费": ["宿舍电费"],
    "报修": ["自助报修"],
    "宿舍报修": ["自助报修"],
    "网络报修": ["学生宿舍网络报修"],
    "开网": ["学生宿舍开网申请"],
    "心理": ["心理咨询"],
    "就业": ["就业服务"],
    "实习": ["就业服务"],
    "发票": ["我的发票"],
    "报销": ["智能报销"],
    "缴费": ["在线缴费"],
    "学费": ["学费情况", "在线缴费"],
    "宿舍": ["住在交大"],
    "失物": ["失物招领"],
    "地图": ["电子地图"],
    "热线": ["54741234热线平台"],
    "投诉": ["54741234热线平台"],
    "天文台": ["光启天文台预约"],
    "进校": ["学生亲友进校备案"],
    "亲友": ["学生亲友进校备案"],
    "电动车": ["两轮电动自行车实名登记"],
    "体育场": ["Sports Venue Booking"],
    "场馆": ["Sports Venue Booking"],
    "会议室": ["会议室预约平台"],
    "助学贷款": ["助学贷款信息登记"],
    "绿色通道": ["绿色通道"],
    "减免": ["学费减免申请"],
    "档案": ["人事档案状态查询"],
    "成绩": ["本科生电子成绩单"],
    "成绩单": ["本科生电子成绩单", "第二课堂成绩单"],
    "接种": ["预防接种"],
    "疫苗": ["预防接种"],
    "宾馆": ["交大宾馆预订"],
    "酒店": ["交大宾馆预订"],
    "等级考试": ["等级考试"],
    "四六级": ["等级考试"],
    "IP申请": ["IP申请"],
    "预约羽毛球场": ["场馆预约"],
    "场馆": ["场馆预约"],
    "体育馆": ["场馆预约"],
    "预约场地": ["场馆预约"],
    "电子成绩单": ["本科生电子成绩单"],
    "学业成绩": ["本科生电子成绩单"],
    "课表": ["学在交大"],
    "课程表": ["学在交大"],
    "我的课表": ["学在交大"],
    "培养方案": ["学在交大"],
    "选课": ["学在交大", "学生选课特殊申请"],
    "图书馆座位": ["交圕座位预约"],
    "图书馆空间": ["交圕空间预约"],
    "图书馆会议室": ["交圕会议室预约"],
    "借书": ["当前借阅", "历史借阅", "图书馆权限（门禁/借书）开通申请"],
    "借阅": ["当前借阅", "历史借阅"],
    "开门时间": ["开放时间"],
    "开放时间": ["开放时间"],
    "教务": ["学在交大"],
    "教务服务": ["学在交大"],
    "在线缴费": ["在线缴费"],
    "交学费": ["在线缴费"],
}

_MYSJTU_STOPWORDS = [
    "帮我", "一下", "看看", "查看", "看一下", "看", "查一下", "查", "去", "我要", "我想",
    "想", "服务", "业务", "办理", "申请", "入口", "页面", "系统", "功能", "使用", "打开",
]

_MYSJTU_CATEGORY_ALIASES: dict[str, list[str]] = {
    "图书馆": ["图书馆"],
    "教务": ["教务处", "学在交大", "教学服务"],
    "教务服务": ["教务处", "学在交大", "教学服务"],
    "学习": ["学在交大", "教学服务"],
    "缴费": ["财务", "后勤"],
    "报修": ["信息服务", "后勤", "图书馆"],
    "宿舍": ["生活服务", "信息服务"],
    "体育": ["智慧体育"],
    "场馆": ["智慧体育"],
    "校园卡": ["生活服务", "信息服务", "财务"],
}

_MYSJTU_SEARCH_ONLY_HINTS = {
    "图书馆", "教务", "教务服务", "校园卡", "信息服务", "生活服务", "财务", "后勤",
}


def _canvas_openid_connect_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/login/openid_connect"


def _canvas_auto_setup_state() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as exc:
        return False, f"Playwright 不可用：{exc}"

    username = os.environ.get("JACCOUNT_USERNAME", "").strip()
    password = os.environ.get("JACCOUNT_PASSWORD", "").strip()
    if not username or not password:
        return False, "缺少 jAccount 用户名或密码"

    return True, "ready"


def _canvas_click_first(page, selectors: list[str], timeout: int = 5000) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
            for idx in range(count):
                candidate = locator.nth(idx)
                try:
                    candidate.wait_for(state="visible", timeout=timeout)
                    candidate.click()
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _canvas_fill_first(page, selectors: list[str], value: str, timeout: int = 5000) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
            for idx in range(count):
                candidate = locator.nth(idx)
                try:
                    candidate.wait_for(state="visible", timeout=timeout)
                    candidate.fill(value)
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _extract_canvas_token(page) -> str:
    try:
        inputs = page.locator("input, textarea")
        for idx in range(inputs.count()):
            try:
                node = inputs.nth(idx)
                if not node.is_visible():
                    continue
                value = node.input_value().strip()
            except Exception:
                continue
            if re.fullmatch(r"[A-Za-z0-9_\-]{20,}", value):
                return value
    except Exception:
        pass

    for selector in ["code", "pre", ".ic-Form-control", ".ui-dialog-content", ".ReactModal__Content", "body"]:
        try:
            nodes = page.locator(selector)
        except Exception:
            continue
        for idx in range(nodes.count()):
            try:
                node = nodes.nth(idx)
                if selector != "body" and not node.is_visible():
                    continue
                text = node.inner_text(timeout=1500).strip()
            except Exception:
                continue
            match = re.search(r"([A-Za-z0-9_\-]{20,})", text)
            if match:
                return match.group(1)
    return ""


def _auto_create_canvas_token(base_url: str, token_purpose: str = "SJTU Agent") -> dict:
    ready, reason = _canvas_auto_setup_state()
    if not ready:
        return {"success": False, "error": reason}

    username = os.environ.get("JACCOUNT_USERNAME", "").strip()
    password = os.environ.get("JACCOUNT_PASSWORD", "").strip()
    settings_url = _canvas_settings_url(base_url)
    openid_connect_url = _canvas_openid_connect_url(base_url)

    try:
        from playwright.sync_api import sync_playwright
        import login as login_module
    except Exception as exc:
        return {"success": False, "error": f"自动创建前置依赖不可用：{exc}"}

    try:
        print("[Canvas] 正在启动浏览器并尝试自动创建 token…", flush=True)
        with sync_playwright() as playwright:
            # 始终使用无头模式：有界面模式在 Windows 终端/CI 环境中容易卡死
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            print("[Canvas] 正在打开 Canvas 设置页…", flush=True)
            page.goto(settings_url, wait_until="domcontentloaded", timeout=30_000)

            if "/login/canvas" in page.url or page.url.rstrip("/").endswith("/login"):
                print("[Canvas] 检测到 Canvas 登录页，正在跳转到 jAccount 单点登录…", flush=True)
                page.goto(openid_connect_url, wait_until="domcontentloaded", timeout=30_000)

            if "jaccount.sjtu.edu.cn" in page.url:
                print("[Canvas] 检测到 jAccount 登录页，正在尝试登录…", flush=True)
                if not login_module._fill_jaccount(page, username, password):
                    browser.close()
                    return {"success": False, "error": "jAccount 登录失败，无法自动创建 Canvas token"}

            print("[Canvas] 已进入 Canvas，正在定位 token 设置…", flush=True)
            # 用 load 代替 networkidle，避免在复杂页面无限等待
            try:
                page.goto(settings_url, wait_until="load", timeout=30_000)
            except Exception:
                page.goto(settings_url, wait_until="domcontentloaded", timeout=30_000)
            # 额外等待 JS 渲染完成
            page.wait_for_timeout(2000)

            if not _canvas_click_first(
                page,
                [
                    "text=New Access Token",
                    "text=创建新访问许可证",
                    "text=新增访问令牌",
                    "text=+ New Access Token",
                    "text=+ 创建新访问许可证",
                    "text=+ 新增访问令牌",
                    "button:has-text('New Access Token')",
                    "button:has-text('创建新访问许可证')",
                    "button:has-text('新增访问令牌')",
                    "a:has-text('New Access Token')",
                    "a:has-text('创建新访问许可证')",
                    "a:has-text('新增访问令牌')",
                ],
            ):
                browser.close()
                return {"success": False, "error": "没有在 Canvas 设置页找到创建访问令牌的入口"}

            print("[Canvas] 已打开新建 token 对话框，正在填写用途…", flush=True)
            # 等待对话框出现
            page.wait_for_timeout(800)
            _canvas_fill_first(
                page,
                [
                    "input[name='purpose']",
                    "input[id*='purpose']",
                    "input[placeholder*='Purpose']",
                    "input[placeholder*='用途']",
                    ".ui-dialog input[type='text']",
                    ".ReactModal__Content input[type='text']",
                    "dialog input[type='text']",
                ],
                token_purpose,
            )

            if not _canvas_click_first(
                page,
                [
                    "button:has-text('Generate Token')",
                    "button:has-text('生成令牌')",
                    "button:has-text('生成')",
                    "button:has-text('Submit')",
                    "button:has-text('确定')",
                    "a:has-text('生成令牌')",
                    ".ReactModal__Content button.btn-primary",
                    ".ui-dialog button.btn-primary",
                    ".ui-dialog button[type='submit']",
                    ".ReactModal__Content button[type='submit']",
                ],
            ):
                browser.close()
                return {"success": False, "error": "没有找到生成 token 的确认按钮"}

            print("[Canvas] 正在等待 token 出现…", flush=True)
            # 等待 token 显示区域出现（最多 8 秒）
            _token_appeared = False
            for _sel in [
                "input[value]",
                ".ic-Form-control",
                ".ui-dialog-content",
                ".ReactModal__Content",
                "code",
                "pre",
            ]:
                try:
                    page.wait_for_selector(_sel, timeout=8_000)
                    _token_appeared = True
                    break
                except Exception:
                    continue
            if not _token_appeared:
                page.wait_for_timeout(3000)
            token = _extract_canvas_token(page)
            browser.close()
    except Exception as exc:
        return {"success": False, "error": f"自动创建 Canvas token 失败：{exc}"}

    if not token:
        return {"success": False, "error": "Canvas 已触发生成流程，但没有成功读取到 token"}

    print("[Canvas] 已读取到 token，正在保存到本地配置…", flush=True)
    tool_save_credentials(canvas_token=token)
    return {
        "success": True,
        "auto_created": True,
        "settings_url": settings_url,
        "token_saved": True,
        "token_purpose": token_purpose,
    }


def _normalize_mysjtu_task(task: str) -> str:
    text = (task or "").lower().strip()
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[，。！？、,.!?:：;；/\\\-_+=()（）\[\]【】<>《》\"'`~@#$%^&*]+", "", text)
    for word in sorted(_MYSJTU_STOPWORDS, key=len, reverse=True):
        text = text.replace(word, "")
    return text


def _mysjtu_grams(text: str) -> set[str]:
    if not text:
        return set()
    if len(text) == 1:
        return {text}
    return {text[i:i+2] for i in range(len(text) - 1)}


def _mysjtu_category_matches(task_norm: str, category: str) -> bool:
    category_norm = _normalize_mysjtu_task(category)
    if category_norm and category_norm in task_norm:
        return True
    for hint, categories in _MYSJTU_CATEGORY_ALIASES.items():
        if hint in task_norm and category in categories:
            return True
    return False


def _mysjtu_search_keyword(task: str) -> str:
    task_norm = _normalize_mysjtu_task(task)
    if not task_norm:
        return (task or "").strip()[:10]
    for hint in sorted(_MYSJTU_CATEGORY_ALIASES, key=len, reverse=True):
        if hint in task_norm:
            return hint
    return task_norm[:10]


def _extract_libseat_context(current_url: str, text: str) -> dict | None:
    """为图书馆座位预约系统补充解释，避免把首页统计误判为当前可预约状态。"""
    if "libseat.sjtu.edu.cn" not in current_url and "图书馆座位预约系统" not in text:
        return None

    is_homepage = "#/ic/home" in current_url or current_url.rstrip("/") == "https://libseat.sjtu.edu.cn"
    library_counts = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(.*?图书馆.*?|.*?阅览室.*?)\((\d+)/(\d+)\)$", line)
        if m:
            library_counts.append({
                "name": m.group(1).strip(),
                "display_count": f"{m.group(2)}/{m.group(3)}",
            })

    warning = None
    if is_homepage:
        warning = (
            "当前页面是图书馆座位系统首页统计页。首页里的“空闲/总数”和馆区汇总数字不等于“此刻一定可以预约”，"
            "闭馆、未到开放时段或需要进入具体日期/时段时，首页仍可能显示这些统计。"
            "只有进入具体日期/时段的选座页面并看到可选座位后，才能确认当前可预约。"
        )

    return {
        "site": "libseat",
        "is_homepage": is_homepage,
        "booking_status": "unverified" if is_homepage else "unknown",
        "warning": warning,
        "library_counts": library_counts[:8],
    }


def _load_mysjtu_catalog() -> list[dict]:
    """加载本地服务目录缓存，不存在则返回空列表。"""
    if not MYSJTU_CATALOG_PATH.exists():
        return []
    try:
        data = json.loads(MYSJTU_CATALOG_PATH.read_text(encoding="utf-8"))
        return data.get("services", [])
    except Exception:
        return []


def _find_mysjtu_service(task: str, catalog: list[dict]) -> dict | None:
    """
    在服务目录中根据任务描述找最匹配的服务。
    匹配策略：别名优先 → 服务名子串 → 分类子串。
    返回 {'name', 'url', 'category'} 或 None。
    """
    if not catalog:
        return None

    task_raw = (task or "").strip()
    task_norm = _normalize_mysjtu_task(task_raw)
    generic_search_only = task_norm in _MYSJTU_SEARCH_ONLY_HINTS

    # 1. 别名匹配
    for alias, names in sorted(_MYSJTU_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if alias in task_raw or alias in task_norm:
            for target in names:
                for svc in catalog:
                    if target in svc["name"]:
                        return svc

    if generic_search_only:
        return None

    # 2. 服务名子串匹配（任务包含服务名的关键字）
    best: dict | None = None
    best_score = 0.0
    task_grams = _mysjtu_grams(task_norm)
    for svc in catalog:
        name = svc.get("name", "")
        category = svc.get("category", "")
        name_norm = _normalize_mysjtu_task(name)
        if not name_norm:
            continue

        score = 0.0
        if task_norm == name_norm:
            score += 2.0
        elif task_norm and task_norm in name_norm:
            score += 1.1

        name_grams = _mysjtu_grams(name_norm)
        if task_grams and name_grams:
            overlap = len(task_grams & name_grams)
            if overlap >= 2:
                score += overlap / len(name_grams)
            elif overlap == 1:
                score += 0.1

        if _mysjtu_category_matches(task_norm, category):
            score += 0.35

        if score > best_score:
            best_score = score
            best = svc

    if best_score >= 0.55:
        return best

    return None


def tool_refresh_mysjtu_catalog() -> dict:
    """爬取 my.sjtu.edu.cn 所有分类的服务，建立本地缓存。直接从 Vue 组件数据提取 URL，无需点击。"""
    try:
        from playwright.sync_api import sync_playwright as _spw
    except ImportError:
        return {"error": "未安装 playwright"}

    cfg = dc.load_config()
    jaccount_cookies = cfg.get("jaccount_cookies", {})
    if not jaccount_cookies:
        return {"error": "未配置 jAccount cookie，请先配置 jAccount"}

    catalog: list[dict] = []
    seen: set[str] = set()

    _JS_EXTRACT = """() => {
        const appEls = document.querySelectorAll('.app.cursor-pointer');
        const results = [];
        for (const el of appEls) {
            const vk = Object.keys(el).find(k => k.startsWith('__vue'));
            if (!vk) continue;
            const comp = el[vk];
            const app = comp && comp._props && comp._props.app;
            if (app && app.name && app.uri) {
                results.push({name: app.name, url: app.uri, id: app.id || ''});
            }
        }
        return results;
    }"""

    with _spw() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        ctx.add_cookies([
            {"name": k, "value": v, "domain": ".sjtu.edu.cn", "path": "/"}
            for k, v in jaccount_cookies.items()
        ])

        page = ctx.new_page()
        page.goto("https://my.sjtu.edu.cn", wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_timeout(2000)

        all_cats = page.locator(".type-item-text").all_text_contents()

        for cat in all_cats:
            cat = cat.strip()
            if not cat:
                continue
            try:
                page.locator(".type-item-text", has_text=cat).first.click()
                page.wait_for_timeout(500)

                apps = page.evaluate(_JS_EXTRACT)
                for app in apps:
                    name = app.get("name", "").strip()
                    if name and name not in seen:
                        seen.add(name)
                        catalog.append({
                            "name": name,
                            "url": app["url"],
                            "id": app.get("id", ""),
                            "category": cat,
                        })
            except Exception:
                continue

        browser.close()

    import datetime
    MYSJTU_CATALOG_PATH.write_text(
        json.dumps({
            "updated": datetime.date.today().isoformat(),
            "services": catalog,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "count": len(catalog),
        "message": f"已缓存 {len(catalog)} 个服务，保存于 {MYSJTU_CATALOG_PATH.name}",
    }

# ══════════════════════════════════════════════════════════════════════════════
# 工具实现
# ══════════════════════════════════════════════════════════════════════════════

def _serialize_ddl(item: dict, now=None) -> dict:
    import datetime as _dt
    if now is None:
        now = _dt.datetime.now(dc.CST)
    total_seconds = (item["due"] - now).total_seconds()
    hours_left    = int(total_seconds / 3600)
    result = {
        "platform":   item["platform"],
        "course":     item["course"],
        "name":       item["name"],
        "due":        item["due"].strftime("%Y-%m-%d %H:%M"),   # 已转为 CST，无需带 tz
        "hours_left": hours_left,                               # 负数=已过期
        "expired":    total_seconds < 0,
        "submitted":  item.get("submitted", False),
    }
    # 如果有分类信息则带上
    if item.get("type"):
        result["type"] = item["type"]
        result["type_confidence"] = item.get("type_confidence", 0.0)
    return result


def _serialize_lab(lab: dict | None) -> dict | None:
    if not lab:
        return None
    dt = lab["dt"]
    return {
        "name":     lab["name"],
        "datetime": dt.isoformat(),
        "weekday":  dc.WEEKDAY_ZH[dt.weekday()],
        "time_str": lab["time_str"],
        "room":     lab["room"],
    }


def tool_check_setup() -> dict:
    env_user  = os.environ.get("JACCOUNT_USERNAME", "")
    env_pass  = os.environ.get("JACCOUNT_PASSWORD", "")
    mooc_user = os.environ.get("MOOC_USERNAME", "")
    mooc_pass = os.environ.get("MOOC_PASSWORD", "")
    from sjtu_agent.agent.chat_loop import load_agent_config
    agent_cfg = load_agent_config()
    canvas_auto_ready, canvas_auto_reason = _canvas_auto_setup_state()

    _cfg.reload_if_changed()
    cfg = _cfg.raw()

    def has_cookies(key: str) -> bool:
        return bool(cfg.get(key))

    return {
        "agent": {
            "configured": bool(agent_cfg.get("api_key") and agent_cfg.get("model")),
            "base_url": agent_cfg.get("base_url") or None,
            "model": agent_cfg.get("model") or None,
        },
        "jaccount": {
            "has_credentials": bool(env_user and env_pass),
            "username": env_user or None,
        },
        "canvas": {
            "has_token": bool(cfg.get("canvas_token") and not cfg.get("canvas_token", "").startswith("YOUR_")),
            "settings_url": _canvas_settings_url(cfg.get("canvas_base_url", _CANVAS_DEFAULT_BASE_URL)),
            "setup_tool": "setup_canvas",
            "can_auto_fetch": canvas_auto_ready,
            "auto_fetch_reason": canvas_auto_reason,
        },
        "aihaoke": {
            "has_credentials": bool(env_user and env_pass),
            "has_cookies": has_cookies("aihaoke_cookies"),
        },
        "phycai": {
            "has_credentials": bool(env_user and env_pass),
            "has_cookies": has_cookies("phycai_cookies"),
        },
        "icourse": {
            "has_credentials": bool(mooc_user and mooc_pass),
            "mooc_username": mooc_user or None,
            "has_cookies": has_cookies("icourse_cookies"),
        },
        "shuiyuan": {
            "has_api_key": bool(cfg.get("shuiyuan_user_api_key")),
            "has_cookies": bool(cfg.get("shuiyuan_cookies")),
            "access_ok": bool(cfg.get("shuiyuan_user_api_key") or cfg.get("shuiyuan_cookies")),
            "needs_attention": not bool(cfg.get("shuiyuan_user_api_key") or cfg.get("shuiyuan_cookies")),
            "api_key_required": False,
            "note": (
                "session cookie 已可用于水源搜索和读取帖子，无需 User API Key。"
                if cfg.get("shuiyuan_cookies")
                else "未配置水源登录态；如需要可调用 setup_shuiyuan。"
            ),
        },
        "course_community": {
            "has_cookies": bool(cfg.get("course_sjtu_cookies")),
        },
        "config_file_exists": CONFIG_PATH.exists(),
    }


def tool_setup_canvas(open_browser: bool = True, auto_create: bool = False, token_purpose: str = "SJTU Agent") -> dict:
    """提供 Canvas Token 生成引导，并在条件允许时尝试自动创建 token。"""
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass

    base_url = cfg.get("canvas_base_url", _CANVAS_DEFAULT_BASE_URL).rstrip("/")
    settings_url = _canvas_settings_url(base_url)
    token = cfg.get("canvas_token", "").strip()
    has_existing_token = bool(token and not token.startswith("YOUR_"))
    token_valid = None
    can_auto_fetch, auto_fetch_reason = _canvas_auto_setup_state()

    if has_existing_token:
        try:
            import requests as _req
            resp = _req.get(
                f"{base_url}/api/v1/users/self/profile",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            token_valid = resp.status_code == 200
        except Exception:
            token_valid = None

    if auto_create:
        auto_result = _auto_create_canvas_token(base_url, token_purpose=token_purpose)
        auto_result.setdefault("settings_url", settings_url)
        auto_result.setdefault("can_auto_fetch", can_auto_fetch)
        auto_result.setdefault("auto_fetch_reason", auto_fetch_reason)
        auto_result.setdefault("has_existing_token", has_existing_token)
        auto_result.setdefault("existing_token_valid", token_valid)
        if auto_result.get("success"):
            auto_result.setdefault("next_action", "Canvas token 已经自动保存到本地 config.json。")
            return auto_result
        auto_result.setdefault("reason", _CANVAS_SETUP_REASON)
        auto_result.setdefault("steps", _CANVAS_SETUP_STEPS)
        auto_result.setdefault("next_action", "自动流程失败后，你仍然可以手动生成 token 并粘贴给我保存。")
        return auto_result

    opened_browser = False
    if open_browser:
        try:
            import webbrowser
            opened_browser = bool(webbrowser.open(settings_url))
        except Exception:
            opened_browser = False

    return {
        "success": True,
        "can_auto_fetch": can_auto_fetch,
        "auto_fetch_reason": auto_fetch_reason,
        "reason": _CANVAS_SETUP_REASON,
        "base_url": base_url,
        "settings_url": settings_url,
        "opened_browser": opened_browser,
        "has_existing_token": has_existing_token,
        "existing_token_valid": token_valid,
        "steps": _CANVAS_SETUP_STEPS,
        "next_action": "生成后把 token 原样发给我，我会调用 save_credentials 保存。",
    }


def _cookies_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())


def _shuiyuan_session_is_valid(cookies: dict) -> bool:
    """用 Discourse 当前用户接口验证 shuiyuan session cookie 是否仍登录。"""
    if not cookies:
        return False
    try:
        r = requests.get(
            "https://shuiyuan.sjtu.edu.cn/session/current.json",
            headers={
                "Cookie": _cookies_header(cookies),
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return False
        data = r.json()
        return bool(data.get("current_user"))
    except Exception:
        return False


def tool_setup_shuiyuan() -> dict:
    """授权水源社区：优先复用已保存且仍有效的 session cookie。

    User API Key 方案已废弃，当前方案为 Playwright 登录后保存 session cookie。
    每次调用前先验证旧 cookie，避免无谓登录触发 jAccount 异地登录风控。
    """
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass

    existing = cfg.get("shuiyuan_cookies") or {}
    if _shuiyuan_session_is_valid(existing):
        return {
            "success": True,
            "message": "水源社区 session 仍然有效，已跳过重新登录。",
        }

    username = os.environ.get("JACCOUNT_USERNAME", "").strip()
    password = os.environ.get("JACCOUNT_PASSWORD", "").strip()

    if not username and not cfg.get("jaccount_cookies"):
        return {
            "error": "需要先配置 jAccount 凭据（save_credentials）",
            "next_action": "请先用 save_credentials 保存 jAccount 用户名和密码，再重试「配置水源」。",
        }

    return _setup_shuiyuan_session(cfg, username, password)


def _parse_shuiyuan_cookie_text(cookie_text: str) -> list[dict]:
    """解析用户粘贴的 Cookie。

    支持两种形式：
      - 完整 Cookie 头：_forum_session=abc; _t=def; ...
      - 单个 token：直接尝试常见的水源 session cookie 名称
    """
    text = (cookie_text or "").strip().strip('"').strip("'")
    if not text:
        return []

    pairs: list[dict] = []
    if "=" in text:
        for part in text.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, _, value = part.partition("=")
            name = name.strip()
            value = value.strip()
            if name and value:
                pairs.append({"name": name, "value": value})
        return pairs

    return [
        {"name": name, "value": text}
        for name in ("_forum_session", "_t", "_discourse_session")
    ]


def tool_save_shuiyuan_cookie(cookie_text: str) -> dict:
    """保存用户从浏览器复制的 shuiyuan.sjtu.edu.cn session cookie。"""
    candidates = _parse_shuiyuan_cookie_text(cookie_text)
    if not candidates:
        return {"error": "没有收到有效的 Cookie 文本"}

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception as exc:
            return {"error": f"config.json 读取失败：{exc}"}

    if len(candidates) > 1 or any(c["name"] not in {"_forum_session", "_t", "_discourse_session"} for c in candidates):
        trial = {c["name"]: c["value"] for c in candidates}
        if _shuiyuan_session_is_valid(trial):
            cfg["shuiyuan_cookies"] = trial
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
            return {"success": True, "message": "水源 Cookie 已保存并通过校验。"}

    for candidate in candidates:
        trial = {candidate["name"]: candidate["value"]}
        if _shuiyuan_session_is_valid(trial):
            cfg["shuiyuan_cookies"] = trial
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
            return {"success": True, "message": f"水源 Cookie 已保存（{candidate['name']}）。"}

    return {
        "error": "Cookie 校验未通过：/session/current.json 未返回当前用户。",
        "next_action": "请确认复制的是 shuiyuan.sjtu.edu.cn 登录后的完整 Cookie。",
    }


def _setup_shuiyuan_session(cfg: dict, username: str, password: str) -> dict:
    """Playwright 登录水源并保存 session cookie。

    优先复用持久化浏览器 profile（shuiyuan_browser_profile/），profile 失效
    或不可用时回退到新的浏览器上下文，并把 config 中的 jAccount cookie 合并进去。
    """
    manual_note = (
        "水源社区没有固定的 API 设置页面；不要去偏好设置里找 API。"
        "如果 User API Key 授权不可用，session cookie 就是当前的降级方案。"
    )

    def _shuiyuan_session_error(message: str) -> dict:
        return {
            "error": message,
            "manual_note": manual_note,
            "next_action": (
                "如果自动登录失败，可以稍后重新说“配置水源”再试一次。"
                "当前项目对水源的可用凭据不一定是 API Key，也可能是 session cookie。"
            ),
        }

    try:
        from playwright.sync_api import sync_playwright as _sync_pw
    except ImportError:
        return _shuiyuan_session_error("未安装 playwright")

    try:
        import login as login_module
    except Exception as e:
        return _shuiyuan_session_error(f"加载登录模块失败：{e}")

    jaccount_cookies = cfg.get("jaccount_cookies", {})
    try:
        SHUIYUAN_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _shuiyuan_session_error(f"无法创建水源浏览器 profile 目录：{e}")
    _ManualLoginRequired = getattr(login_module, "ManualLoginRequired", None)

    new_session: dict = {}
    with _sync_pw() as pw:
        # 优先复用持久化浏览器 profile：保留 localStorage / cookie / 浏览器指纹，
        # 比每次新建无痕上下文更不容易触发 jAccount 异地登录风控。
        browser = None
        profile_reused = False
        try:
            try:
                ctx = pw.chromium.launch_persistent_context(
                    user_data_dir=str(SHUIYUAN_PROFILE_DIR),
                    headless=True,
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    viewport={"width": 1440, "height": 900},
                )
                profile_reused = True
            except Exception:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    viewport={"width": 1440, "height": 900},
                )

            # 无论 profile 是否存在，都把 config 里最新的 jAccount cookie 合并进去。
            if jaccount_cookies:
                try:
                    ctx.add_cookies([
                        {"name": k, "value": v, "domain": "jaccount.sjtu.edu.cn", "path": "/"}
                        for k, v in jaccount_cookies.items()
                    ])
                except Exception:
                    pass
        except Exception as e:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            return _shuiyuan_session_error(f"启动浏览器失败：{e}")

        def _close() -> None:
            try:
                ctx.close()
            except Exception:
                pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

        page = ctx.new_page()
        try:
            # jAccount SSO 页资源较多，networkidle 容易超时；先等 DOM 再按需等待。
            page.goto("https://shuiyuan.sjtu.edu.cn/", wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            pass

        if "jaccount" in page.url:
            # 正常情况应停在 /jaccount/jalogin?... 登录页。若落在 /（只显示
            # Welcome to SJTU jAccount）或登录框未渲染，说明重定向被旧 cookie
            # 干扰。清除 cookie 后重新从水源发起 SSO。
            for _attempt in range(2):
                if "/jaccount/jalogin" in page.url and page.locator("#input-login-user").count():
                    break
                try:
                    ctx.clear_cookies()
                except Exception:
                    pass
                try:
                    page.goto(
                        "https://shuiyuan.sjtu.edu.cn/",
                        wait_until="domcontentloaded",
                        timeout=20_000,
                    )
                    page.wait_for_url("**/jaccount/jalogin**", timeout=15_000)
                except Exception:
                    pass

            if not username or not password:
                _close()
                return {"error": "需要 jAccount 凭据，请先用 save_credentials 配置"}
            try:
                if not login_module._fill_jaccount(page, username, password):
                    _close()
                    return _shuiyuan_session_error("jAccount 登录失败，请检查账号密码")
                try:
                    page.wait_for_url("**/shuiyuan.sjtu.edu.cn/**", timeout=15_000)
                except Exception:
                    pass
                new_ja = {c["name"]: c["value"] for c in ctx.cookies()
                          if "jaccount" in c.get("domain", "")}
                if new_ja:
                    cfg["jaccount_cookies"] = new_ja
            except Exception as e:
                _close()
                if _ManualLoginRequired is not None and isinstance(e, _ManualLoginRequired):
                    return _shuiyuan_session_error(
                        f"{e}。建议先在常用电脑的浏览器里登录水源一次，再重试；"
                        "也可按排错手册手动导出 shuiyuan cookie。"
                    )
                return _shuiyuan_session_error(f"jAccount 登录失败：{e}")

        new_session = {c["name"]: c["value"] for c in ctx.cookies()
                       if "shuiyuan.sjtu.edu.cn" in c.get("domain", "")}
        profile_ja = {c["name"]: c["value"] for c in ctx.cookies()
                      if "jaccount" in c.get("domain", "")}
        if profile_ja:
            cfg["jaccount_cookies"] = profile_ja
        _close()

    if not profile_reused and not new_session:
        return _shuiyuan_session_error("未能获取水源社区 session，请检查账号")

    if not new_session:
        return _shuiyuan_session_error(
            "未能从浏览器 profile 中获取水源社区 session，请检查是否已登录。"
        )

    # 不能只看域名 cookie：即使没有登录也可能拿到游客 cookie。
    # 必须通过 Discourse 当前用户接口确认 session 真的已登录。
    if not _shuiyuan_session_is_valid(new_session):
        return _shuiyuan_session_error(
            "已拿到水源社区 cookie，但当前用户接口校验未通过（可能仍未登录）。"
        )

    cfg["shuiyuan_cookies"] = new_session
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    return {"success": True, "message": "水源社区 session 登录成功（需定期更新）"}


# ── 选课社区 course.sjtu.plus ────────────────────────────────────────────────
# 该站是纯 SPA + 私有 API，所有 /api/ 接口需要 jAccount OAuth cookie 才能访问。
# 这里复用 jAccount cookie + Playwright 跑一次 OAuth 拿 course.sjtu.plus 域 cookie，
# 之后所有 API 调用直接 requests.get + cookie 即可，不再开浏览器。

_COURSE_PLUS_BASE = "https://course.sjtu.plus"


def tool_setup_course_community(username: str = "", password: str = "") -> dict:
    """Login to course.sjtu.plus using Playwright browser automation.
    Fills in the login form (email + password), submits, and captures cookies."""
    import json as _json
    import os as _os
    import time as _time
    import requests as _rq
    from sjtu_agent.paths import CONFIG_PATH as _CFG

    user = (username or "").strip()
    pwd = (password or "").strip()
    if not user:
        user = _os.environ.get("JACCOUNT_USERNAME", "").strip()
    if not pwd:
        pwd = _os.environ.get("JACCOUNT_PASSWORD", "").strip()
    if not user or not pwd:
        return {
            "error": "未找到 jAccount 凭据。请先运行 sjtu-agent setup 或在对话中说「配置 jAccount」。"
        }
    # 登录页第一个 input placeholder 是 "jAccount"（用户名，非邮箱），
    # 第二个是 "选课社区密码，非 jAccount 密码"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "Playwright 未安装，请运行 playwright install chromium"}

    course_cookies = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()

            # Navigate to login page
            page.goto("https://course.sjtu.plus/login", wait_until="networkidle", timeout=30_000)
            _time.sleep(2)

            # Fill in login form: username + password
            inputs = page.locator("input")
            if inputs.count() >= 2:
                inputs.nth(0).fill(user)     # jAccount username
                inputs.nth(1).fill(pwd)      # site password
                # Click login button
                btn = page.locator("button:has-text('登录')")
                if btn.count() > 0:
                    btn.first.click()
                    _time.sleep(5)
                    try:
                        page.wait_for_load_state("networkidle", timeout=20_000)
                    except Exception:
                        pass

            # Check if login succeeded by hitting /api/auth/me
            page.goto("https://course.sjtu.plus/api/auth/me", wait_until="domcontentloaded", timeout=15_000)
            _time.sleep(2)

            # Collect cookies
            for c in ctx.cookies():
                domain = c.get("domain", "")
                if "course.sjtu.plus" in domain:
                    course_cookies[c["name"]] = c["value"]

            browser.close()
    except Exception as e:
        return {
            "error": f"选课社区登录（Playwright）失败: {e}",
            "next_action": "请手动访问 https://course.sjtu.plus 登录后再试。"
        }

    if not course_cookies:
        return {
            "error": "未能获取选课社区 session cookie",
            "next_action": "请手动打开浏览器访问 https://course.sjtu.plus 登录后，回来告诉我「已登录」。"
        }

    # Verify via direct API call
    try:
        r = _rq.get("https://course.sjtu.plus/api/auth/me",
                    cookies=course_cookies,
                    headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
                    timeout=10)
        if r.status_code != 200 or not r.json().get("username"):
            return {
                "error": "cookie 验证失败，请手动登录选课社区",
                "next_action": "访问 https://course.sjtu.plus 登录后告诉我「已登录」。"
            }
    except Exception as e:
        return {"error": f"cookie 验证请求失败: {e}", "next_action": "请重试或手动登录"}

    cfg = {}
    if _CFG.exists():
        try:
            cfg = _json.loads(_CFG.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": f"config.json 读取失败，已中止保存以保护现有配置: {e}"}
    cfg["course_sjtu_cookies"] = course_cookies
    _CFG.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "message": f"选课社区登录成功（已验证 /api/auth/me）",
    }


_COURSE_PLUS_BASE = "https://course.sjtu.plus"


def _course_plus_request(path: str, params: dict | None = None, max_retry: int = 2):
    """Call course.sjtu.plus API (v2). Uses stored cookies."""
    import time as _time
    import requests as _rq

    _cfg.reload_if_changed()
    cookies = _cfg.get("course_sjtu_cookies") or {}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": _COURSE_PLUS_BASE + "/",
    }

    url = _COURSE_PLUS_BASE + path
    last_err = ""
    for attempt in range(max_retry):
        try:
            r = _rq.get(url, params=params or {}, headers=headers, cookies=cookies,
                        timeout=15, allow_redirects=True)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("error"):
                    if "unauthorized" in str(data["error"]).lower():
                        return None, "选课社区需要登录，请说「配置选课社区」重新登录"
                    return None, data.get("error", "未知错误")
                return data, None
            if r.status_code == 404:
                return None, "未找到（404）"
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
            _time.sleep(1 + attempt)
    return None, f"选课社区请求失败：{last_err}"


def tool_search_courses(query: str, page_size: int = 8) -> dict:
    """Search courses on course.sjtu.plus."""
    if not query.strip():
        return {"error": "请提供搜索关键词"}
    data, err = _course_plus_request("/api/course/", {
        "q": query.strip(), "page_size": min(max(1, page_size), 20), "page": 1,
    })
    if err:
        if "需要登录" in err:
            login = tool_setup_course_community()
            if login.get("ok"):
                data, err = _course_plus_request("/api/course/", {
                    "q": query.strip(), "page_size": min(max(1, page_size), 20), "page": 1,
                }, max_retry=1)
        if err:
            return {"error": err}

    if not data or not isinstance(data, dict):
        return {"error": "选课社区返回了意外的数据格式"}
    items = data.get("items", [])
    if not items:
        return {"message": f"选课社区没有找到与「{query}」相关的课程"}
    results = []
    for item in items[:page_size]:
        teacher = (item.get("main_teacher") or {})
        rating = item.get("rating") or {}
        results.append({
            "id": item.get("id"), "code": item.get("code", ""), "name": item.get("name", ""),
            "credit": item.get("credit", 0), "department": item.get("department", ""),
            "teacher": teacher.get("name", ""), "avg_rating": rating.get("avg", 0),
            "review_count": rating.get("count", 0),
            "url": f"{_COURSE_PLUS_BASE}/course/{item.get('id')}",
        })
    return {"total": data.get("total"), "returned": len(results), "courses": results}


def tool_get_course_detail(course_id: int, max_reviews: int = 10) -> dict:
    """Get course detail and reviews from course.sjtu.plus."""
    detail, err = _course_plus_request(f"/api/course/{course_id}")
    if err:
        return {"error": err}
    if not detail or not isinstance(detail, dict):
        return {"error": "选课社区返回了意外的数据格式"}
    teacher = (detail.get("main_teacher") or {})
    rating = detail.get("rating") or {}
    result = {
        "id": detail.get("id"), "code": detail.get("code", ""), "name": detail.get("name", ""),
        "credit": detail.get("credit", 0), "department": detail.get("department", ""),
        "teacher": teacher.get("name", ""), "teacher_title": teacher.get("title", ""),
        "avg_rating": rating.get("avg", 0), "review_count": rating.get("count", 0),
        "url": f"{_COURSE_PLUS_BASE}/course/{course_id}",
    }
    review_data, _ = _course_plus_request(f"/api/course/{course_id}/review", {
        "order_by": "updated_at", "page_size": min(max(1, max_reviews), 20), "page": 1,
    })
    if review_data and isinstance(review_data, dict):
        reviews = []
        for r in (review_data.get("items") or [])[:max_reviews]:
            reviews.append({
                "rating": r.get("rating", 0), "content": (r.get("content") or "")[:500],
                "semester": r.get("semester", ""), "created_at": r.get("created_at", ""),
            })
        result["reviews"] = reviews
        result["review_total"] = review_data.get("total", len(reviews))
    return result


def tool_save_credentials(
    jaccount_username: str = "",
    jaccount_password: str = "",
    canvas_token: str = "",
    mooc_username: str = "",
    mooc_password: str = "",
) -> dict:
    updated = []

    env_lines: list = []
    if ENV_PATH.exists():
        env_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    def set_env(key, value):
        nonlocal env_lines
        line = f"{key}={value}"
        for i, l in enumerate(env_lines):
            if l.startswith(f"{key}="):
                env_lines[i] = line
                return
        env_lines.append(line)

    if jaccount_username:
        set_env("JACCOUNT_USERNAME", jaccount_username)
        os.environ["JACCOUNT_USERNAME"] = jaccount_username
        updated.append("jAccount 用户名")
    if jaccount_password:
        set_env("JACCOUNT_PASSWORD", jaccount_password)
        # 不写入 os.environ — 防止被子进程继承泄露
        updated.append("jAccount 密码")
    if mooc_username:
        set_env("MOOC_USERNAME", mooc_username)
        os.environ["MOOC_USERNAME"] = mooc_username
        updated.append("MOOC 用户名")
    if mooc_password:
        set_env("MOOC_PASSWORD", mooc_password)
        updated.append("MOOC 密码")

    if any([jaccount_username, jaccount_password, mooc_username, mooc_password]):
        ENV_PATH.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception as e:
            return {"error": f"config.json 读取失败，已中止保存以保护现有配置: {e}"}

    cfg.setdefault("canvas_base_url", "https://oc.sjtu.edu.cn")
    cfg.setdefault("aihaoke_cookies", {})
    cfg.setdefault("phycai_cookies", {})
    cfg.setdefault("icourse_cookies", {})

    if canvas_token:
        cfg["canvas_token"] = canvas_token
        updated.append("Canvas Token")

    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return {"saved": updated, "success": True}


def tool_login_platform(platform: str) -> dict:
    if not CONFIG_PATH.exists():
        return {"success": False, "error": "config.json 不存在，请先保存凭证"}
    cfg = json.loads(CONFIG_PATH.read_text())
    try:
        import login as _login_module
        _ManualLoginRequired = getattr(_login_module, "ManualLoginRequired", None)
    except Exception:
        _ManualLoginRequired = None
    try:
        if platform == "aihaoke":
            print("  [Playwright 自动登录 aihaoke，浏览器窗口会短暂出现…]", flush=True)
            ok, error = dc.refresh_aihaoke_cookies(cfg)
            if not ok:
                return {"success": False, "error": error}
            result = dc.fetch_aihaoke(cfg)
            return {"success": True, "platform": "aihaoke", "ddl_count": len(result)}
        elif platform == "phycai":
            print("  [Playwright 自动登录物理实验平台…]", flush=True)
            result = dc.fetch_phycai(cfg)
            return {"success": True, "platform": "phycai", "lab": _serialize_lab(result)}
        elif platform == "icourse":
            print("  [Playwright 自动登录中国大学MOOC…]", flush=True)
            result = dc.fetch_icourse(cfg)
            return {"success": True, "platform": "icourse", "ddl_count": len(result)}
        else:
            return {"success": False, "error": f"未知平台: {platform}"}
    except Exception as e:
        if _ManualLoginRequired is not None and isinstance(e, _ManualLoginRequired):
            return {
                "success": False,
                "manual_login_required": True,
                "stop_retrying": True,
                "platform": platform,
                "error": str(e),
                "hint": "请用户自己在浏览器里手动登录一次该平台（账号密码 + 选择验证方式 + OTP），完成后再让我重试。不要再次调用 login_platform。",
            }
        return {"success": False, "error": str(e)}



# ── DDL 持久磁盘缓存 ─────────────────────────────────────────────────────────
# 缓存文件存放在 DATA_DIR/.ddl_cache.json，进程重启后依然有效。
# TTL = 15 分钟（900 秒）；若缓存命中则直接返回，避免每次都发起网络请求。

from sjtu_agent.paths import DDL_CACHE_PATH as _DDL_CACHE_PATH

_DDL_CACHE_TTL = 900  # 秒（15 分钟）


def _ddl_cache_load() -> dict:
    """从磁盘读取缓存，返回 {cache_key: {"ts": float, "data": list}} 字典。"""
    try:
        if _DDL_CACHE_PATH.exists():
            import json as _json
            return _json.loads(_DDL_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _ddl_cache_save(store: dict) -> None:
    """将缓存字典写入磁盘。"""
    try:
        import json as _json
        _DDL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DDL_CACHE_PATH.write_text(
            _json.dumps(store, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def _ddl_cache_get(cache_key: str) -> list | None:
    """从磁盘缓存读取指定 key，若 TTL 未超期则返回数据，否则返回 None。"""
    import time as _t
    import datetime as _dt
    store = _ddl_cache_load()
    entry = store.get(cache_key)
    if not entry:
        return None
    if _t.time() - entry.get("ts", 0) > _DDL_CACHE_TTL:
        return None
    # 反序列化 due 字段（JSON 存为字符串）
    raw_list = entry.get("data", [])
    result = []
    for item in raw_list:
        item = dict(item)
        if isinstance(item.get("due"), str):
            try:
                item["due"] = _dt.datetime.fromisoformat(item["due"])
            except Exception:
                pass
        if isinstance(item.get("dt"), str):
            try:
                item["dt"] = _dt.datetime.fromisoformat(item["dt"])
            except Exception:
                pass
        result.append(item)
    return result


def _ddl_cache_set(cache_key: str, data: list) -> None:
    """将 data 写入磁盘缓存（datetime 自动序列化为 ISO 格式字符串）。"""
    import time as _t
    import datetime as _dt

    def _serialize(obj):
        if isinstance(obj, _dt.datetime):
            return obj.isoformat()
        return str(obj)

    store = _ddl_cache_load()
    import json as _json
    serializable = _json.loads(_json.dumps(data, default=_serialize))
    store[cache_key] = {"ts": _t.time(), "data": serializable}
    _ddl_cache_save(store)


def _fetch_ddls_parallel(cfg: dict, skip_canvas=False, skip_aihaoke=False,
                          skip_icourse=False, classify: bool = False) -> list:
    """并行拉取各平台 DDL，返回合并列表（未排序）。
    优先使用 15 分钟内的磁盘缓存。
    classify=True 时使用独立缓存键（含 description/type），避免缓存污染。
    """
    import concurrent.futures as _cf

    cache_key = f"{skip_canvas},{skip_aihaoke},{skip_icourse}"
    if classify:
        cache_key += ",classify"
    cached = _ddl_cache_get(cache_key)
    if cached is not None:
        return cached

    tasks = []
    if not skip_canvas:   tasks.append(("canvas",  lambda: dc.fetch_canvas(cfg, classify=classify)))
    if not skip_aihaoke:  tasks.append(("aihaoke", lambda: dc.fetch_aihaoke(cfg)))
    if not skip_icourse:  tasks.append(("icourse", lambda: dc.fetch_icourse(cfg)))

    all_ddl: list = []
    if not tasks:
        return all_ddl

    with _cf.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks}
        for fut in _cf.as_completed(futures):
            try:
                all_ddl.extend(fut.result())
            except Exception as e:
                _logger.warning("[DDL] %s 拉取失败：%s", futures[fut], e)

    _ddl_cache_set(cache_key, all_ddl)
    return all_ddl


def _classify_canvas_ddls(ddls: list) -> list:
    """对 Canvas DDL 列表进行智能分类（作业 / 通知 / 其他）。

    规则预筛 + LLM 批量分类。结果写回每个 dict 的 type 和 type_confidence 字段。
    仅处理 platform == "Canvas" 且有 description 字段的项。
    """
    import json as _json
    from sjtu_agent.agent.chat_loop import load_agent_config as _load_cfg
    from sjtu_agent.agent.runner import _make_client as _make_llm_client

    # 只处理 Canvas 且有 description 的项
    canvas_items = [
        (i, d) for i, d in enumerate(ddls)
        if d.get("platform") == "Canvas" and d.get("description") is not None
    ]
    if not canvas_items:
        return ddls

    # ── 规则预筛 ──
    notification_keywords = ["通知", "公告", "提醒", "评分", "评价", "问卷", "反馈",
                              "评分标准", "课程介绍", "Syllabus", "教学大纲"]
    # 强通知关键词 — 无论 submission_types 是什么，名称含这些词就是通知
    strong_notification_keywords = ["评分", "评价", "问卷", "反馈", "评分标准"]
    need_llm = []
    for idx, d in canvas_items:
        desc = (d.get("description") or "").strip()
        sub_types = d.get("submission_types") or []
        name = d.get("name", "")

        # 规则0: 名称含强通知关键词 → 直接判 notification（不管 submission_types）
        if any(kw in name for kw in strong_notification_keywords):
            d["type"] = "notification"
            d["type_confidence"] = 1.0
            continue

        # 规则1: submission_types 为 ["none"] 或空，且名称/描述含关键词 → notification
        is_none_submission = (not sub_types or sub_types == ["none"])
        if is_none_submission:
            text = f"{name} {desc}"
            if any(kw in text for kw in notification_keywords):
                d["type"] = "notification"
                d["type_confidence"] = 1.0
                continue

        # 规则2: submission_types 包含实际提交类型 (online_upload/online_text_entry/online_url)
        #         且名称不含通知关键词 → 疑似作业，交给 LLM
        has_real_submission = any(
            t in sub_types for t in ["online_upload", "online_text_entry", "online_url",
                                      "online_quiz", "external_tool", "media_recording"]
        )
        if has_real_submission:
            need_llm.append((idx, d))
            continue

        # 规则3: 没有 description 且 submission_types 为 none → notification
        if not desc and is_none_submission:
            d["type"] = "notification"
            d["type_confidence"] = 1.0
            continue

        # 剩余 → LLM 判断
        need_llm.append((idx, d))

    if not need_llm:
        return ddls

    # ── LLM 批量分类 ──
    # 构建简洁的分类请求
    items_for_llm = []
    for idx, d in need_llm:
        desc = (d.get("description") or "")[:500]  # 截断长描述
        items_for_llm.append({
            "index": len(items_for_llm),
            "course": d.get("course", ""),
            "name": d.get("name", ""),
            "description": desc,
            "submission_types": d.get("submission_types", []),
        })

    prompt = f"""判断以下 Canvas 条目是「作业/任务」还是「课程通知」。

条目列表：
{_json.dumps(items_for_llm, ensure_ascii=False, indent=2)}

判断标准：
- 要求学生提交/完成具体内容（做题、写报告、上传文件）→ "assignment"
- 仅信息告知、无需提交（课程安排通知、评分提醒、问卷、反馈征集、Syllabus）→ "notification"
- 评价/评分/问卷/反馈/课程介绍 → "notification"
- 不确定 → "unknown"

返回 JSON 数组（只返回 JSON，不要其他文字）：
[{{"index": 0, "type": "assignment", "confidence": 0.95}}, ...]"""

    try:
        agent_cfg = _load_cfg()
        client = _make_llm_client(agent_cfg)
        model = agent_cfg.get("model", "deepseek-chat")

        # 使用轻量模型调用（非流式，快速返回）
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.1,  # 低温度，追求一致分类
        )
        text = resp.choices[0].message.content or ""

        # 解析 JSON（防御：可能有 markdown 包裹）
        text = text.strip()
        if text.startswith("```"):
            # 去掉 ```json ... ``` 包裹
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        classifications = _json.loads(text)

        # 写回结果
        class_map = {c["index"]: c for c in classifications}
        for local_idx, (orig_idx, d) in enumerate(need_llm):
            c = class_map.get(local_idx, {})
            d["type"] = c.get("type", "unknown")
            d["type_confidence"] = c.get("confidence", 0.0)

    except Exception as e:
        _logger.warning("[DDL classify] LLM 分类失败，全部标记为 unknown: %s", e)
        for idx, d in need_llm:
            d.setdefault("type", "unknown")
            d.setdefault("type_confidence", 0.0)

    # 确保所有 Canvas 项都有 type（防御）
    for d in ddls:
        if d.get("platform") == "Canvas":
            d.setdefault("type", "unknown")
            d.setdefault("type_confidence", 0.0)

    return ddls


def tool_get_ddls(skip_canvas=False, skip_aihaoke=False, skip_icourse=False,
                  classify: bool = False, include_notifications: bool = False):
    import datetime as _dt
    cfg = dc.load_config()
    now = _dt.datetime.now(dc.CST)

    # 当 classify=True 时，需要原始数据（含 description）做分类
    fetch_classify = classify
    all_ddl = _fetch_ddls_parallel(cfg, skip_canvas, skip_aihaoke, skip_icourse,
                                    classify=fetch_classify)

    if classify:
        _classify_canvas_ddls(all_ddl)

    all_ddl.sort(key=lambda x: x["due"])
    warnings = []
    if not skip_canvas and not (cfg.get("canvas_token") and not cfg.get("canvas_token", "").startswith("YOUR_")):
        warnings.append("Canvas 未配置 token；请先调用 setup_canvas 获取引导，生成后再用 save_credentials 保存。")

    serialized = [_serialize_ddl(x, now) for x in all_ddl if not x.get("submitted")]

    # 默认过滤通知类（除非用户明确要求）
    notification_count = 0
    if classify and not include_notifications:
        filtered = []
        for d in serialized:
            if d.get("type") == "notification":
                notification_count += 1
            else:
                filtered.append(d)
        serialized = filtered

    result = {
        "current_time": now.strftime("%Y-%m-%d %H:%M"),
        "ddls": serialized,
        "warnings": warnings,
    }
    if classify and notification_count > 0:
        result["filtered_notifications"] = notification_count
        result["hint"] = f"已过滤 {notification_count} 条课程通知（评分/问卷/公告等）。回复「全部DDL」可查看所有条目。"
    return result


def tool_get_next_lab():
    return _serialize_lab(dc.fetch_phycai(dc.load_config()))


def tool_get_all(skip_canvas=False, skip_aihaoke=False, skip_icourse=False, skip_phycai=False):
    import concurrent.futures as _cf
    cfg = dc.load_config()

    # DDL 和物理实验同时拉取
    with _cf.ThreadPoolExecutor(max_workers=2) as pool:
        ddl_fut = pool.submit(_fetch_ddls_parallel, cfg, skip_canvas, skip_aihaoke, skip_icourse)
        lab_fut = pool.submit(dc.fetch_phycai, cfg) if not skip_phycai else None
        all_ddl = ddl_fut.result()
        lab = lab_fut.result() if lab_fut else None

    import datetime as _dt
    now = _dt.datetime.now(dc.CST)
    all_ddl.sort(key=lambda x: x["due"])
    warnings = []
    if not skip_canvas and not (cfg.get("canvas_token") and not cfg.get("canvas_token", "").startswith("YOUR_")):
        warnings.append("Canvas 未配置 token；请先调用 setup_canvas 获取引导，生成后再用 save_credentials 保存。")
    return {
        "current_time": now.strftime("%Y-%m-%d %H:%M"),
        "ddls": [_serialize_ddl(x, now) for x in all_ddl if not x.get("submitted")],
        "lab":  _serialize_lab(lab),
        "warnings": warnings,
    }



def tool_search_campus(
    query: str,
    sites: list | None = None,
    max_results: int = 6,
) -> dict:
    cfg = dc.load_config()
    return dc.search_campus(cfg, query, sites=sites, max_results=max_results)


def _shuiyuan_request(url: str, params: dict, headers: dict, cookies, max_retry: int = 3):
    """带 429 退避重试的 GET。借鉴 openclaw-sjtu 的限流处理。"""
    import time as _time
    import requests as _rq
    last_exc = None
    for attempt in range(max_retry):
        try:
            r = _rq.get(url, params=params, headers=headers, cookies=cookies, timeout=20)
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                _time.sleep(wait)
                continue
            return r
        except Exception as e:
            last_exc = e
            _time.sleep(1 + attempt)
    if last_exc:
        raise last_exc
    return None


def tool_read_shuiyuan_topic(topic: str, max_posts: int = 30) -> dict:
    """读取水源社区某个帖子的主楼 + 若干楼回复。

    topic 可以是 URL、URL 片段、topic id 字符串或整数。
    max_posts > 20 时会通过 /t/{id}/posts.json 分页补抓（避免只拿到 post_stream 前 20 楼）。
    返回：{title, url, category_id, posts_count, posts:[{post_number, username, created_at, content}]}
    """
    import re as _re
    import html as _html

    cfg = dc.load_config()
    api_key   = (cfg.get("shuiyuan_user_api_key") or "").strip()
    client_id = (cfg.get("shuiyuan_user_api_client_id") or "").strip()
    session   = cfg.get("shuiyuan_cookies") or {}
    if not api_key and not session:
        return {"error": "水源社区未配置，请对 Agent 说「配置水源」完成登录"}

    s = str(topic).strip()
    m = _re.search(r"/t(?:/[^/]+)?/(\d+)", s)
    if m:
        tid = m.group(1)
    elif s.isdigit():
        tid = s
    else:
        return {"error": f"无法从 '{topic}' 提取 topic id；请传入帖子 URL 或数字 id"}

    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    cookies = None
    if api_key:
        headers["User-Api-Key"] = api_key
        headers["User-Api-Client-Id"] = client_id
    else:
        cookies = session

    base = "https://shuiyuan.sjtu.edu.cn"
    try:
        r = _shuiyuan_request(f"{base}/t/{tid}.json", {"include_raw": "false"}, headers, cookies)
        if r.status_code in (401, 403) or "login" in r.url:
            return {"error": "水源社区凭证已过期，请对 Agent 说「配置水源」重新授权"}
        if r.status_code == 404:
            return {"error": f"水源帖子 {tid} 不存在或无权限查看"}
        if r.status_code == 429:
            return {"error": "水源社区限流（429），稍后重试"}
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        # 尽量结构化错误（借鉴 openclaw HttpRequestError 思路）
        msg = str(e)
        if "ConnectionError" in msg or "Timeout" in msg:
            return {"error": f"水源社区网络异常：{msg}"}
        return {"error": f"读取水源帖子失败：{msg}"}

    title = data.get("fancy_title") or data.get("title") or ""
    slug  = data.get("slug") or "topic"
    url   = f"{base}/t/{slug}/{tid}"
    posts_count = data.get("posts_count") or 0
    post_stream_info = data.get("post_stream") or {}
    initial_posts = post_stream_info.get("posts") or []
    stream_ids = post_stream_info.get("stream") or []

    def _html_to_text(h: str) -> str:
        if not h:
            return ""
        txt = _re.sub(r"(?is)<script[^>]*>.*?</script>", "", h)
        txt = _re.sub(r"(?is)<style[^>]*>.*?</style>", "", txt)
        txt = _re.sub(r"(?is)<br\s*/?>", "\n", txt)
        txt = _re.sub(r"(?is)</p\s*>", "\n", txt)
        txt = _re.sub(r"(?is)<[^>]+>", "", txt)
        txt = _html.unescape(txt)
        txt = _re.sub(r"\n{3,}", "\n\n", txt).strip()
        return txt

    def _serialize(p: dict) -> dict:
        return {
            "post_number": p.get("post_number"),
            "username":    p.get("username"),
            "created_at":  p.get("created_at"),
            "like_count":  p.get("actions_summary", [{}])[0].get("count") if p.get("actions_summary") else None,
            "content":     _html_to_text(p.get("cooked") or ""),
        }

    target = max(1, max_posts)
    by_id: dict = {p.get("id"): p for p in initial_posts if p.get("id") is not None}

    # 若需要的楼层数超过初始返回（通常 20 楼），按 stream id 分批补抓
    if target > len(initial_posts) and stream_ids:
        need_ids = [pid for pid in stream_ids if pid not in by_id]
        need_ids = need_ids[: max(0, target - len(initial_posts))]
        BATCH = 20
        for i in range(0, len(need_ids), BATCH):
            chunk = need_ids[i:i + BATCH]
            try:
                # Discourse 接受重复 query 参数 post_ids[]
                params = [("post_ids[]", str(x)) for x in chunk]
                rr = _shuiyuan_request(f"{base}/t/{tid}/posts.json", params, headers, cookies)
                if rr.status_code != 200:
                    break
                more = (rr.json().get("post_stream") or {}).get("posts") or []
                for p in more:
                    if p.get("id") is not None:
                        by_id[p["id"]] = p
            except Exception:
                break

    # 按 stream 顺序输出（保证楼层顺序正确）
    ordered = []
    for pid in stream_ids:
        p = by_id.get(pid)
        if p:
            ordered.append(p)
        if len(ordered) >= target:
            break
    if not ordered:
        ordered = initial_posts[:target]

    posts = [_serialize(p) for p in ordered]

    return {
        "topic_id":    int(tid),
        "title":       title,
        "url":         url,
        "category_id": data.get("category_id"),
        "posts_count": posts_count,
        "views":       data.get("views"),
        "returned":    len(posts),
        "posts":       posts,
    }


def tool_get_schedule(
    query_type: str = "day",
    date: str = "",
    week_offset: int = 0,
    set_semester_start: str = "",
    refresh: bool = False,
) -> dict:
    cfg = dc.load_config()
    if set_semester_start:
        result = dc.set_semester_start(cfg, set_semester_start)
        if "error" in result:
            return result
        cfg = dc.load_config()
    if query_type == "week":
        return dc.get_schedule_for_week(cfg, week_offset=week_offset, refresh=refresh)
    else:
        return dc.get_schedule_for_date(cfg, date_str=date, refresh=refresh)


def _validate_mysjtu_url(url: str) -> str | None:
    """Validate URL belongs to *.sjtu.edu.cn. Returns error message or None."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"不支持的协议: {parsed.scheme}"
    host = (parsed.hostname or "").lower()
    if not host.endswith(".sjtu.edu.cn") and host != "sjtu.edu.cn":
        return f"不允许访问外部域名: {host}"
    return None


def tool_browse_mysjtu(task: str, start_url: str = "https://my.sjtu.edu.cn", action: str = "") -> dict:
    """
    用 Playwright 打开 my.sjtu.edu.cn，执行可选操作，返回页面文字内容。
    先查本地服务目录缓存，命中则直接跳转目标 URL，无需多级导航。
    """
    try:
        from playwright.sync_api import sync_playwright as _spw
    except ImportError:
        return {"error": "未安装 playwright"}

    # 防 SSRF — 只允许 *.sjtu.edu.cn
    err = _validate_mysjtu_url(start_url)
    if err:
        return {"error": err}

    cfg = dc.load_config()
    jaccount_cookies = cfg.get("jaccount_cookies", {})

    # ── 缓存命中：根据任务描述直接跳转对应服务 URL ────────────────────────
    catalog = _load_mysjtu_catalog()
    _auto_search_keyword = None
    if catalog and not action and start_url == "https://my.sjtu.edu.cn":
        matched = _find_mysjtu_service(task, catalog)
        if matched:
            start_url = matched["url"]
            # 在返回值里告知命中了哪个服务
            _matched_service = f"{matched['name']}（{matched['category']}）"
        else:
            _matched_service = None
            _auto_search_keyword = _mysjtu_search_keyword(task)
    else:
        _matched_service = None

    with _spw() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        # 注入 jAccount cookie，直接跳过登录
        if jaccount_cookies:
            ctx.add_cookies([
                {"name": k, "value": v, "domain": ".sjtu.edu.cn", "path": "/"}
                for k, v in jaccount_cookies.items()
            ] + [
                {"name": k, "value": v, "domain": "jaccount.sjtu.edu.cn", "path": "/"}
                for k, v in jaccount_cookies.items()
            ])

        page = ctx.new_page()

        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(1500)
        except Exception as e:
            browser.close()
            return {"error": f"页面加载失败：{e}"}

        effective_action = action
        if _auto_search_keyword and not effective_action and start_url == "https://my.sjtu.edu.cn":
            effective_action = f"search:{_auto_search_keyword}"

        # 执行操作指令
        if effective_action:
            try:
                if effective_action.startswith("click:"):
                    text = effective_action[6:].strip()
                    # 优先精确匹配链接/按钮，再模糊匹配
                    for sel in [f"a:has-text('{text}')", f"button:has-text('{text}')",
                                f"[class*='menu']:has-text('{text}')", f"*:has-text('{text}')"]:
                        loc = page.locator(sel).first
                        if loc.count() and loc.is_visible(timeout=1000):
                            loc.click()
                            page.wait_for_load_state("domcontentloaded", timeout=10_000)
                            page.wait_for_timeout(1000)
                            break
                elif effective_action.startswith("goto:"):
                    url = effective_action[5:].strip()
                    err = _validate_mysjtu_url(url)
                    if err:
                        browser.close()
                        return {"error": err}
                    page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    page.wait_for_timeout(1000)
                elif effective_action.startswith("search:"):
                    kw = effective_action[7:].strip()
                    for sel in ["input[type='search']", "input[placeholder*='搜索']",
                                "input[placeholder*='search']", ".search-input input", "input.el-input__inner"]:
                        loc = page.locator(sel).first
                        if loc.count() and loc.is_visible(timeout=500):
                            loc.fill(kw)
                            loc.press("Enter")
                            page.wait_for_load_state("domcontentloaded", timeout=10_000)
                            page.wait_for_timeout(1000)
                            break
            except Exception as e:
                pass  # 操作失败，继续返回当前页内容

        current_url = page.url

        # 提取页面文字内容
        text = page.evaluate("""
        () => {
            // 移除 script/style
            document.querySelectorAll('script,style,noscript').forEach(e => e.remove());
            // 提取主要内容区
            const main = document.querySelector('main, #main, .main, [class*="content"], [class*="container"]');
            const src = main || document.body;
            return (src.innerText || src.textContent || '').replace(/\\n{3,}/g, '\\n\\n').trim();
        }
        """)

        # 提取页面中的链接（帮助 agent 决定下一步点哪里）
        links = page.evaluate("""
        () => {
            return Array.from(document.querySelectorAll('a[href]'))
                .filter(a => a.innerText.trim() && !a.href.startsWith('javascript'))
                .slice(0, 30)
                .map(a => ({text: a.innerText.trim().slice(0, 40), href: a.href}));
        }
        """)

        browser.close()

    libseat_context = _extract_libseat_context(current_url, text)
    if libseat_context and libseat_context.get("warning"):
        text = f"[系统提示] {libseat_context['warning']}\n\n{text}"

    # 检测是否被重定向到登录页
    is_login_page = "jaccount.sjtu.edu.cn" in current_url or (
        "login" in current_url.lower() and "sjtu.edu.cn" in current_url
    )

    return {
        "url": current_url,
        "logged_in": not is_login_page,
        "matched_service": _matched_service,
        "auto_search_keyword": _auto_search_keyword,
        "libseat_context": libseat_context,
        "content": text[:6000],
        "truncated": len(text) > 6000,
        "links": links,
        "task": task,
    }


def tool_query_grades(year: str = "", semester: str = "") -> dict:
    """
    直接从教学信息服务网 (i.sjtu.edu.cn) 查询成绩，自动完成 jAccount SSO。
    year: 学年起始年，如 "2025" 表示 2025-2026 学年，空=全部
    semester: "1"=第1学期(秋), "2"=第2学期(春), "3"=第3学期(夏), ""=全部
    """
    try:
        from playwright.sync_api import sync_playwright as _spw
    except ImportError:
        return {"error": "未安装 playwright，请运行 pip install playwright"}

    cfg = dc.load_config()
    jaccount_cookies = cfg.get("jaccount_cookies", {})
    if not jaccount_cookies:
        return {"error": "未配置 jAccount Cookie，请先配置 jAccount 登录"}

    _XQM_MAP = {"1": "3", "2": "12", "3": "16", "": ""}
    xqm = _XQM_MAP.get(str(semester), "")

    try:
        import time as _time
        with _spw() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
            ctx.add_cookies([
                {"name": k, "value": v, "domain": ".sjtu.edu.cn", "path": "/"}
                for k, v in jaccount_cookies.items()
            ])
            page = ctx.new_page()

            # 1. SSO 登录（自动跳转）
            page.goto("https://i.sjtu.edu.cn/jaccountlogin", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
            if "jaccount" in page.url:
                browser.close()
                return {"error": "jAccount Cookie 已过期，请重新配置 jAccount 登录"}

            # 2. 访问成绩查询页面，获取隐藏字段（含用户身份信息）
            page.goto(
                "https://i.sjtu.edu.cn/cjcx/cjcx_cxDgXscj.html?gnmkdm=N305005",
                wait_until="networkidle", timeout=15000
            )
            page.wait_for_timeout(500)

            form_data = page.evaluate("""() => {
                const inputs = document.querySelectorAll('input[type=hidden]');
                const data = {};
                for (const i of inputs) { data[i.name] = i.value; }
                return data;
            }""")

            # 3. 直接调用 jqGrid 数据接口
            resp = ctx.request.post(
                "https://i.sjtu.edu.cn/cjcx/cjcx_cxXsgrcj.html?doType=query&gnmkdm=N305005",
                form={
                    **form_data,
                    "xnm": year,
                    "xqm": xqm,
                    "kcbjdm": "",
                    "page": "1",
                    "rows": "500",
                    "sidx": "xnm",
                    "sord": "desc",
                    "_search": "false",
                    "nd": str(int(_time.time() * 1000)),
                    "zd_fzdm": "N305005-xs",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://i.sjtu.edu.cn/cjcx/cjcx_cxDgXscj.html?gnmkdm=N305005",
                }
            )
            data = resp.json()
            items = data.get("items", [])
            browser.close()
    except Exception as e:
        return {"error": str(e)}

    if not items:
        return {
            "count": 0,
            "year_filter": year or "全部",
            "semester_filter": semester or "全部",
            "grades": [],
            "message": "未找到成绩数据，该学期可能还未录入",
        }

    grades = []
    total_credits = 0.0
    weighted_sum = 0.0

    for item in items:
        xf_str = item.get("xf", "")
        jd_str = item.get("jd", "")
        try:
            xf = float(xf_str) if xf_str else 0.0
            jd = float(jd_str) if jd_str else None
        except ValueError:
            xf = 0.0
            jd = None

        grades.append({
            "year":        f"{item.get('xnm', '')}学年",
            "semester":    f"第{item.get('xqmmc', '')}学期",
            "course_id":   item.get("kch", ""),
            "course_name": item.get("kcmc", ""),
            "score":       item.get("cj", ""),
            "gpa":         jd_str,
            "credits":     xf_str,
            "type":        item.get("kcbj", "").strip(),
            "exam_type":   item.get("khfsmc", ""),
        })

        if jd is not None and xf > 0:
            total_credits += xf
            weighted_sum += jd * xf

    avg_gpa = weighted_sum / total_credits if total_credits > 0 else None

    return {
        "count": len(grades),
        "year_filter": year or "全部",
        "semester_filter": semester or "全部",
        "weighted_gpa": round(avg_gpa, 4) if avg_gpa is not None else None,
        "total_credits": total_credits,
        "grades": grades,
    }



def _validate_fetch_url(url: str) -> dict | None:
    """Validate URL for tool_fetch_url — block private IPs and non-HTTP schemes.

    Returns an error dict if invalid, None if OK.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": f"不支持的协议: {parsed.scheme}，仅允许 http/https"}
    host = parsed.hostname
    if not host:
        return {"ok": False, "error": "无法解析 URL 主机名"}
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            return {"ok": False, "error": "不允许访问内网地址"}
    except ValueError:
        pass  # hostname, not IP — allow
    return None


def tool_fetch_url(url: str) -> dict:
    """
    抓取网页内容并提取纯文本。
    支持微信公众号、普通网页等，自动提取标题和正文。
    微信公众号优先用 Playwright 绕过反爬，失败时降级到 requests。
    """
    import re
    from bs4 import BeautifulSoup

    # URL 安全校验 — 防 SSRF
    err = _validate_fetch_url(url)
    if err:
        return err

    # 微信公众号优先用 Playwright（绕过反爬）
    if "mp.weixin.qq.com" in url and HAS_PLAYWRIGHT:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # 等待内容加载
                page.wait_for_selector("#js_content, .rich_media_content", timeout=10000)
                html = page.content()
                browser.close()

                soup = BeautifulSoup(html, "html.parser")
                # 移除无关标签
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()

                # 提取标题
                title_tag = soup.find("h1", class_="rich_media_title") or soup.find("h2", class_="rich_media_title")
                title = title_tag.get_text(strip=True) if title_tag else (soup.title.string.strip() if soup.title else "")

                # 提取正文
                content_tag = soup.find("div", id="js_content") or soup.find("div", class_="rich_media_content")
                if content_tag:
                    text = content_tag.get_text(separator="\n", strip=True)
                else:
                    text = soup.get_text(separator="\n", strip=True)

                # 清理多余空行
                text = re.sub(r'\n\s*\n+', '\n\n', text)
                text = text.strip()

                # 截断过长内容
                if len(text) > 8000:
                    text = text[:8000] + "\n\n[内容过长，已截断...]"

                return {
                    "ok": True,
                    "url": url,
                    "title": title,
                    "content": text,
                    "length": len(text),
                    "method": "playwright",
                }
        except Exception as e:
            # Playwright 失败，降级到 requests
            pass

    # 普通网页或 Playwright 失败时用 requests
    try:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.43(0x18002b2d) NetType/WIFI Language/zh_CN",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://mp.weixin.qq.com/",
        }
        resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        # 移除无关标签
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # 提取标题
        title = ""
        if "mp.weixin.qq.com" in url:
            title_tag = soup.find("h1", class_="rich_media_title") or soup.find("h2", class_="rich_media_title")
            if title_tag:
                title = title_tag.get_text(strip=True)
        if not title:
            title = soup.title.string.strip() if soup.title else ""
            if not title and soup.find("h1"):
                title = soup.find("h1").get_text(strip=True)

        # 提取正文
        if "mp.weixin.qq.com" in url:
            content_tag = soup.find("div", id="js_content") or soup.find("div", class_="rich_media_content")
            if content_tag:
                text = content_tag.get_text(separator="\n", strip=True)
            else:
                text = soup.get_text(separator="\n", strip=True)
        else:
            content_tag = soup.find("article") or soup.find("main") or soup.find("body")
            if content_tag:
                text = content_tag.get_text(separator="\n", strip=True)
            else:
                text = soup.get_text(separator="\n", strip=True)

        # 清理多余空行
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        text = text.strip()

        # 截断过长内容
        if len(text) > 8000:
            text = text[:8000] + "\n\n[内容过长，已截断...]"

        return {
            "ok": True,
            "url": url,
            "title": title,
            "content": text,
            "length": len(text),
            "method": "requests",
        }
    except Exception as e:
        return {"ok": False, "error": f"抓取失败: {e}"}


def tool_list_assignment_files(
    course_filter: str = "",
    assignments_dir: str = "./assignments",
) -> dict:
    base = Path(assignments_dir)
    if not base.exists():
        return {"error": f"目录不存在: {base.resolve()}，请先执行 download_assignments"}
    tree = []
    for course_dir in sorted(base.iterdir()):
        if not course_dir.is_dir():
            continue
        if course_filter and course_filter not in course_dir.name:
            continue
        assignments = []
        for asgn_dir in sorted(course_dir.iterdir()):
            if not asgn_dir.is_dir():
                continue
            files = [
                {"name": f.name, "path": str(f.resolve()), "size_kb": round(f.stat().st_size / 1024, 1)}
                for f in sorted(asgn_dir.iterdir())
                if f.is_file() and f.suffix.lower() in {
                    ".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg", ".webp",
                    ".docx", ".doc", ".txt", ".md", ".csv", ".tsv", ".json",
                    ".xlsx", ".xls", ".pptx", ".ppt", ".zip", ".mp3", ".wav", ".m4a", ".mp4",
                }
            ]
            if files:
                assignments.append({"assignment": asgn_dir.name, "files": files})
        if assignments:
            tree.append({"course": course_dir.name, "assignments": assignments})
    return {"tree": tree, "base_dir": str(base.resolve())}


def tool_read_assignment_file(
    file_path: str,
    max_chars: int = 8000,
    start_page: int = 1,
) -> dict:
    path = Path(file_path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        path = (ROOT / file_path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        return {"error": f"路径越权: {file_path}"}
    if not path.exists():
        return {"error": f"文件不存在: {file_path}，请用 list_assignment_files 确认正确路径"}
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            import pypdf
            reader = pypdf.PdfReader(str(path))
            total_pages = len(reader.pages)
            parts = []
            chars = 0
            for i, page in enumerate(reader.pages[start_page - 1:], start=start_page):
                text = page.extract_text() or ""
                if chars + len(text) > max_chars:
                    text = text[: max_chars - chars]
                    parts.append(text)
                    chars = max_chars
                    break
                parts.append(text)
                chars += len(text)
            content = "\n\n--- 第 {} 页 ---\n".join([""] * len(parts)).strip()
            # 保留页码标记
            labeled = []
            for idx, (pg_num, txt) in enumerate(
                zip(range(start_page, start_page + len(parts)), parts)
            ):
                labeled.append(f"【第 {pg_num} 页】\n{txt.strip()}")
            content = "\n\n".join(labeled)
            return {
                "file": path.name,
                "total_pages": total_pages,
                "pages_read": f"{start_page}-{start_page + len(parts) - 1}",
                "truncated": chars >= max_chars,
                "content": content,
            }
        elif suffix in {".html", ".htm"}:
            from html.parser import HTMLParser
            class _Strip(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.parts = []
                def handle_data(self, data):
                    self.parts.append(data)
            p = _Strip()
            p.feed(path.read_text(encoding="utf-8", errors="replace"))
            text = " ".join(p.parts)
            # 压缩空白
            import re
            text = re.sub(r"\s{3,}", "\n\n", text).strip()
            truncated = len(text) > max_chars
            return {
                "file": path.name,
                "truncated": truncated,
                "content": text[:max_chars],
            }
        else:
            return {"error": f"暂不支持 {suffix} 格式，目前支持 PDF 和 HTML"}
    except Exception as e:
        return {"error": str(e)}


def _detect_missing_parse_backend(parsed: dict) -> str | None:
    parser = str(parsed.get("parser", "")).strip().lower()
    text = " ".join(
        [
            str(parsed.get("error", "") or ""),
            str(parsed.get("content", "") or ""),
            " ".join(str(x) for x in (parsed.get("warnings") or [])),
        ]
    ).lower()
    if "pdf ocr backend missing" in text or "requires paddleocr + pypdfium2" in text:
        return "pdf_ocr"
    if parser == "image_stub" or "paddleocr backend is not installed" in text or "ocr backend missing" in text:
        return "paddleocr"
    if parser == "audio_stub" or "whisper backend is not installed" in text or "asr backend missing" in text:
        return "whisper"
    if "ppt ocr backend missing" in text:
        return "paddleocr"
    return None


def _is_interactive_chat_for_install_prompt() -> bool:
    if os.environ.get(_INTERACTIVE_CHAT_ENV, "").strip() != "1":
        return False
    stdin = getattr(sys, "stdin", None)
    stdout = getattr(sys, "stdout", None)
    if stdin is None or stdout is None:
        return False
    return bool(getattr(stdin, "isatty", lambda: False)() and getattr(stdout, "isatty", lambda: False)())


def _ask_install_missing_backend(backend: str) -> bool:
    meta = _PARSE_BACKEND_INSTALL.get(backend)
    if not meta:
        return False
    packages = ", ".join(meta.get("packages", []))
    prompt = (
        f"\n[parse] Missing {meta['label']} backend '{backend}' "
        f"(pip package: {packages}). Install now? [y/N]: "
    )
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in {"y", "yes"}


def _install_missing_backend_package(backend: str) -> tuple[bool, str]:
    meta = _PARSE_BACKEND_INSTALL.get(backend)
    if not meta:
        return False, f"unknown backend: {backend}"
    packages = [str(p).strip() for p in (meta.get("packages") or []) if str(p).strip()]
    if not packages:
        return False, f"no package configured for backend: {backend}"
    cmd = [sys.executable, "-m", "pip", "install", *packages]
    _logger.info("[parse] Installing %s ...", " ".join(packages))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        _logger.info("[parse] Installed %s.", " ".join(packages))
        return True, ""
    err = (proc.stderr or proc.stdout or "").strip()
    if len(err) > 800:
        err = err[-800:]
    _logger.error("[parse] Install failed (%s): %s", " ".join(packages), err or "unknown error")
    return False, err


def _append_parse_warning(parsed: dict, message: str) -> dict:
    warnings = parsed.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    if message not in warnings:
        warnings.append(message)
    parsed["warnings"] = warnings
    return parsed


def _maybe_install_missing_parse_backend_and_retry(
    parsed: dict,
    path: Path,
    max_chars: int,
    start_page: int,
    strategy: str,
) -> dict:
    backend = _detect_missing_parse_backend(parsed)
    if not backend:
        return parsed
    if not _is_interactive_chat_for_install_prompt():
        return parsed
    if not _ask_install_missing_backend(backend):
        return _append_parse_warning(parsed, f"install_skipped:{backend}")

    ok, err = _install_missing_backend_package(backend)
    if not ok:
        return _append_parse_warning(parsed, f"install_failed:{backend}:{err[:120]}")

    retried = parse_router_file(
        str(path),
        max_chars=max_chars,
        start_page=start_page,
        strategy=strategy or "auto",
    )
    if retried.get("ok"):
        return _append_parse_warning(retried, f"auto_installed:{backend}")
    return _append_parse_warning(retried, f"auto_installed_but_retry_failed:{backend}")


def tool_install_parse_backend(backend: str) -> dict:
    b = str(backend or "").strip().lower()
    meta = _PARSE_BACKEND_INSTALL.get(b)
    if not meta:
        return {"ok": False, "error": f"unsupported backend: {backend}", "supported": sorted(_PARSE_BACKEND_INSTALL.keys())}
    ok, err = _install_missing_backend_package(b)
    if not ok:
        return {"ok": False, "backend": b, "packages": meta.get("packages", []), "error": err or "install failed"}
    return {"ok": True, "backend": b, "packages": meta.get("packages", [])}


def tool_parse_local_file(
    file_path: str,
    max_chars: int = 8000,
    start_page: int = 1,
    strategy: str = "auto",
) -> dict:
    """
    New parse router entrypoint.
    Keeps read_assignment_file unchanged as fallback when strategy asks for legacy
    or when auto parse fails on PDF/HTML.
    """
    path = Path(file_path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        path = (ROOT / file_path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        return {"error": f"路径越权: {file_path}"}
    if not path.exists():
        return {"error": f"文件不存在: {file_path}，请确认路径"}

    if (strategy or "").strip().lower() == "legacy":
        legacy = tool_read_assignment_file(str(path), max_chars=max_chars, start_page=start_page)
        return {
            "ok": "error" not in legacy,
            "parser": "legacy_read_assignment_file",
            "fallback_used": True,
            **legacy,
        }

    parsed = parse_router_file(
        str(path),
        max_chars=max_chars,
        start_page=start_page,
        strategy=strategy or "auto",
    )

    parsed = _maybe_install_missing_parse_backend_and_retry(
        parsed=parsed,
        path=path,
        max_chars=max_chars,
        start_page=start_page,
        strategy=strategy or "auto",
    )

    if parsed.get("ok"):
        return parsed

    # Keep previous stable behavior as hard fallback for legacy-supported formats.
    if path.suffix.lower() in {".pdf", ".html", ".htm"}:
        legacy = tool_read_assignment_file(str(path), max_chars=max_chars, start_page=start_page)
        if "error" not in legacy:
            return {
                "ok": True,
                "parser": "legacy_read_assignment_file",
                "fallback_used": True,
                "warnings": [f"router_failed: {parsed.get('error', 'unknown error')}"],
                **legacy,
            }
    return parsed


def tool_parse_local_files(
    file_paths: list[str],
    per_file_max_chars: int = 4000,
    total_max_chars: int = 12000,
    start_page: int = 1,
    strategy: str = "auto",
) -> dict:
    # Keep fallback behavior inside each file parse by delegating to tool_parse_local_file.
    if not isinstance(file_paths, list) or not file_paths:
        return {"error": "file_paths 不能为空"}

    merged: list[str] = []
    items: list[dict] = []
    failures: list[dict] = []
    total_chars = 0

    for p in file_paths:
        item = tool_parse_local_file(
            file_path=str(p),
            max_chars=per_file_max_chars,
            start_page=start_page,
            strategy=strategy,
        )
        ok = bool(item.get("ok", "error" not in item))
        items.append(item)
        if not ok:
            failures.append({"file_path": str(p), "error": item.get("error", "parse failed")})
            continue

        content = str(item.get("content", "") or "")
        if not content:
            continue
        header = f"===== {item.get('file', Path(str(p)).name)} =====\n"
        block = header + content + "\n"
        if total_chars + len(block) > total_max_chars:
            remain = max(0, total_max_chars - total_chars)
            if remain > 0:
                merged.append(block[:remain])
                total_chars += remain
            break
        merged.append(block)
        total_chars += len(block)

    return {
        "ok": True,
        "count": len(file_paths),
        "success_count": len(file_paths) - len(failures),
        "failure_count": len(failures),
        "failures": failures,
        "truncated": total_chars >= total_max_chars,
        "content": "\n".join(merged).strip(),
        "items": items,
    }


def tool_download_assignments(
    skip_canvas: bool = False,
    skip_aihaoke: bool = False,
    course_filter: str = "",
    assignment_filter: str = "",
    due_within_days: int = 7,
    output_dir: str = "./assignments",
) -> dict:
    cfg = dc.load_config()

    # 自动跳过 aihaoke：仅有 locale cookie 说明未登录（不值得尝试，避免无效的 Playwright 登录）
    _aihaoke_cookies = cfg.get("aihaoke_cookies", {})
    _meaningful_aihaoke = {k: v for k, v in _aihaoke_cookies.items() if k != "locale"}
    if not _meaningful_aihaoke and not skip_aihaoke:
        skip_aihaoke = True

    results = dc.download_assignments(
        cfg,
        output_dir=output_dir,
        skip_canvas=skip_canvas,
        skip_aihaoke=skip_aihaoke,
        course_filter=course_filter,
        assignment_filter=assignment_filter,
        due_within_days=due_within_days,
    )
    # 统计摘要
    total_files = sum(len(r.get("files", [])) for r in results)
    return {
        "downloaded": len(results),
        "total_files": total_files,
        "output_dir": str(Path(output_dir).resolve()),
        "filters": {
            "course_filter": course_filter,
            "assignment_filter": assignment_filter,
            "due_within_days": due_within_days,
            "skip_canvas": skip_canvas,
            "skip_aihaoke": skip_aihaoke,
        },
        "items": [
            {
                "platform": r["platform"],
                "course":   r["course"],
                "name":     r["name"],
                "due":      r.get("due"),
                "files":    r.get("files", []),
                "output_dir": r.get("output_dir", ""),
            }
            for r in results
            if "error" not in r
        ],
        "errors": [r["error"] for r in results if "error" in r],
    }


def tool_list_canvas_courses(include_tabs: bool = False, include_teachers: bool = False) -> dict:
    try:
        client = _make_canvas_client()
        return client.list_courses(include_tabs=include_tabs, include_teachers=include_teachers)
    except CanvasError as exc:
        return _canvas_error_payload(exc)


def tool_get_canvas_course_announcements(course, limit: int = 20, since_days: int | None = None) -> dict:
    try:
        client = _make_canvas_client()
        resolved = _resolve_canvas_course_or_error(client, course)
        if not resolved.get("ok"):
            return resolved
        course_info = resolved["course"]
        result = client.list_announcements(course_info["course_id"], limit=limit, since_days=since_days)
        result["course"] = course_info
        return result
    except CanvasError as exc:
        return _canvas_error_payload(exc)


def tool_get_canvas_course_quizzes(
    course,
    include_past: bool = False,
    include_assignment_backed: bool = True,
) -> dict:
    try:
        client = _make_canvas_client()
        resolved = _resolve_canvas_course_or_error(client, course)
        if not resolved.get("ok"):
            return resolved
        course_info = resolved["course"]
        result = client.list_quizzes(
            course_info["course_id"],
            include_past=include_past,
            include_assignment_backed=include_assignment_backed,
        )
        result["course"] = course_info
        return result
    except CanvasError as exc:
        return _canvas_error_payload(exc)


def tool_get_canvas_course_updates(
    course,
    include: list[str] | None = None,
    limit: int = 10,
    include_past: bool = False,
) -> dict:
    try:
        client = _make_canvas_client()
        resolved = _resolve_canvas_course_or_error(client, course)
        if not resolved.get("ok"):
            return resolved
        course_info = resolved["course"]
        result = client.get_course_updates(
            course_info["course_id"],
            include=include,
            limit=limit,
            include_past=include_past,
        )
        result["course"] = course_info
        return result
    except CanvasError as exc:
        return _canvas_error_payload(exc)


def tool_get_canvas_todo(limit: int = 20) -> dict:
    try:
        client = _make_canvas_client()
        return client.list_todo(limit=limit)
    except CanvasError as exc:
        return _canvas_error_payload(exc)


def tool_configure_canvas_monitor(
    enabled: bool | None = None,
    interval_seconds: int | None = None,
    interval_minutes: float | None = None,
    course_ids: list[int] | None = None,
    course_filters: list[str] | None = None,
    include_announcements: bool | None = None,
    include_quizzes: bool | None = None,
    include_assignments: bool | None = None,
    include_activity: bool | None = None,
    notify_channels: list[str] | None = None,
    baseline_on_first_run: bool | None = None,
) -> dict:
    cfg = read_json_safe(CONFIG_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}
    monitor, updated_fields, notes = update_canvas_monitor_config(
        cfg,
        enabled=enabled,
        interval_seconds=interval_seconds,
        interval_minutes=interval_minutes,
        course_ids=course_ids,
        course_filters=course_filters,
        include_announcements=include_announcements,
        include_quizzes=include_quizzes,
        include_assignments=include_assignments,
        include_activity=include_activity,
        notify_channels=notify_channels,
        baseline_on_first_run=baseline_on_first_run,
    )
    atomic_write_json(CONFIG_PATH, cfg)
    return {
        "ok": True,
        "config": monitor,
        "updated_fields": sorted(set(updated_fields)),
        "config_path": str(CONFIG_PATH),
        "notes": notes,
    }


def tool_list_canvas_assignments(course_filter: str = "", include_past: bool = False) -> dict:
    """列出 Canvas 上允许文件提交（online_upload）的作业，返回含 course_id / assignment_id。"""
    try:
        client = _make_canvas_client()
        courses = client.list_courses().get("courses", [])
    except CanvasError as exc:
        return _canvas_error_payload(exc)

    if course_filter:
        lowered = course_filter.lower()
        courses = [
            course for course in courses
            if lowered in str(course.get("name", "")).lower()
            or lowered in str(course.get("course_code", "")).lower()
        ]

    result = []
    for course in courses[:15]:
        course_id = course["course_id"]
        course_name = course.get("name", "未知课程")
        try:
            assignments = client.list_assignments(course_id, include_past=include_past).get("assignments", [])
        except CanvasError:
            continue
        for assignment in assignments:
            if "online_upload" not in assignment.get("submission_types", []):
                continue
            result.append({
                "course_id": course_id,
                "course_name": course_name,
                "assignment_id": assignment["assignment_id"],
                "assignment_name": assignment.get("name", ""),
                "due_at": assignment.get("due_at", ""),
                "points_possible": assignment.get("points_possible"),
            })

    return {"count": len(result), "assignments": result}


def tool_submit_canvas_assignment(
    file_path: str,
    course_id: int,
    assignment_id: int,
    comment: str = "",
) -> dict:
    """
    将本地文件上传并提交到 Canvas 指定作业（three-step Canvas file upload）。
    file_path: 文件的绝对路径（用户拖入终端后得到的路径）。
    """
    import mimetypes
    import requests as _req
    from pathlib import Path as _P

    fp = _P(file_path.strip().strip("'\""))
    if not fp.exists():
        return {"error": f"文件不存在: {fp}"}
    if not fp.is_file():
        return {"error": f"路径不是文件: {fp}"}

    cfg   = dc.load_config()
    base  = cfg.get("canvas_base_url", _CANVAS_DEFAULT_BASE_URL).rstrip("/")
    token = cfg.get("canvas_token", "").strip()
    if not token:
        return {
            "error": "未配置 Canvas Token。",
            "settings_url": _canvas_settings_url(base),
            "next_action": "请先调用 setup_canvas 获取一步步引导，生成 token 后再用 save_credentials 保存。",
        }
    headers = {"Authorization": f"Bearer {token}"}

    mime      = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
    file_size = fp.stat().st_size

    # ── Step 1: 申请上传许可 ─────────────────────────────────────────────
    r1 = _req.post(
        f"{base}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self/files",
        headers=headers,
        json={"name": fp.name, "size": file_size, "content_type": mime},
        timeout=30,
    )
    if r1.status_code not in (200, 201):
        return {"error": f"申请上传许可失败 ({r1.status_code}): {r1.text[:300]}"}
    upload_info   = r1.json()
    upload_url    = upload_info["upload_url"]
    upload_params = upload_info.get("upload_params", {})

    # ── Step 2: 上传文件 ──────────────────────────────────────────────────
    with open(fp, "rb") as fobj:
        r2 = _req.post(
            upload_url,
            data=upload_params,
            files={"file": (fp.name, fobj, mime)},
            timeout=180,
            allow_redirects=True,
        )

    if r2.status_code in (200, 201):
        file_data = r2.json()
    elif r2.status_code in (301, 302, 303):
        confirm_url = r2.headers.get("Location", "")
        r3 = _req.get(confirm_url, headers=headers, timeout=30)
        file_data = r3.json()
    else:
        return {"error": f"文件上传失败 ({r2.status_code}): {r2.text[:300]}"}

    file_id = file_data.get("id")
    if not file_id:
        return {"error": f"上传完成但未获取到文件 ID，响应: {str(file_data)[:200]}"}

    # ── Step 3: 提交作业 ──────────────────────────────────────────────────
    payload: dict = {
        "submission": {
            "submission_type": "online_upload",
            "file_ids": [file_id],
        }
    }
    if comment:
        payload["comment"] = {"text_comment": comment}

    r_sub = _req.post(
        f"{base}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
        headers=headers,
        json=payload,
        timeout=30,
    )
    if r_sub.status_code not in (200, 201):
        return {"error": f"提交失败 ({r_sub.status_code}): {r_sub.text[:300]}"}

    sub = r_sub.json()
    return {
        "ok":             True,
        "file_name":      fp.name,
        "file_id":        file_id,
        "submission_id":  sub.get("id"),
        "submitted_at":   sub.get("submitted_at"),
        "workflow_state": sub.get("workflow_state"),
    }


def _no_args(fn):
    """包装不接受参数的 tool_xxx：忽略传入的 kwargs，调用 fn()。

    保持 run_tool 原行为（无参工具在 if/elif 里被直接调用，忽略多余 args）。
    """
    return lambda **kw: fn()


# 工具名 → callable(args_dict)。无参工具用 _no_args 包裹；有特殊参数
# 处理的特例用 lambda。普通工具直接存函数引用（run_tool 用 fn(**args) 调用）。
_TOOL_REGISTRY = {
    "check_setup": _no_args(tool_check_setup),
    "save_credentials": tool_save_credentials,
    "setup_canvas": tool_setup_canvas,
    "login_platform": lambda **kw: tool_login_platform(kw["platform"]),
    "get_ddls": tool_get_ddls,
    "get_recent_updates": _no_args(tool_get_recent_updates),
    "get_bot_setup_guide": tool_get_bot_setup_guide,
    "get_next_lab": _no_args(tool_get_next_lab),
    "get_all": tool_get_all,
    "download_assignments": tool_download_assignments,
    "list_assignment_files": tool_list_assignment_files,
    "read_assignment_file": tool_read_assignment_file,
    "parse_local_file": tool_parse_local_file,
    "parse_local_files": tool_parse_local_files,
    "install_parse_backend": tool_install_parse_backend,
    "search_campus": tool_search_campus,
    "read_shuiyuan_topic": tool_read_shuiyuan_topic,
    "get_schedule": tool_get_schedule,
    "setup_shuiyuan": _no_args(tool_setup_shuiyuan),
    "save_shuiyuan_cookie": tool_save_shuiyuan_cookie,
    "add_mcp_server": tool_add_mcp_server,
    "add_skill": tool_add_skill,
    "create_skill": tool_create_skill,
    "list_skills": tool_list_skills,
    "manage_skill": tool_manage_skill,
    "setup_course_community": tool_setup_course_community,
    "search_courses": tool_search_courses,
    "get_course_detail": tool_get_course_detail,
    "browse_mysjtu": tool_browse_mysjtu,
    "refresh_mysjtu_catalog": _no_args(tool_refresh_mysjtu_catalog),
    "query_grades": tool_query_grades,
    "add_reminder": tool_add_reminder,
    "list_reminders": _no_args(tool_list_reminders),
    "remove_reminder": tool_remove_reminder,
    "list_canvas_courses": tool_list_canvas_courses,
    "get_canvas_course_announcements": tool_get_canvas_course_announcements,
    "get_canvas_course_quizzes": tool_get_canvas_course_quizzes,
    "get_canvas_course_updates": tool_get_canvas_course_updates,
    "get_canvas_todo": tool_get_canvas_todo,
    "configure_canvas_monitor": tool_configure_canvas_monitor,
    "list_canvas_assignments": tool_list_canvas_assignments,
    "submit_canvas_assignment": tool_submit_canvas_assignment,
    "list_canvas_folders": tool_list_canvas_folders,
    "list_canvas_files": tool_list_canvas_files,
    "canvas_file_tree": tool_canvas_file_tree,
    "download_canvas_file": tool_download_canvas_file,
    "canvas_track_mark": tool_canvas_track_mark,
    "canvas_track_unmark": tool_canvas_track_unmark,
    "canvas_track_list": _no_args(tool_canvas_track_list),
    "canvas_track_status": tool_canvas_track_status,
    "canvas_track_diff": tool_canvas_track_diff,
    "canvas_track_mark_course": tool_canvas_track_mark_course,
    "read_emails": tool_read_emails,
    "search_emails": tool_search_emails,
    "send_email": tool_send_email,
    "fetch_url": tool_fetch_url,
    "execute_python": tool_execute_python,
    "update_user_profile": tool_update_user_profile,
    "get_user_profile": _no_args(tool_get_user_profile),
    "update_report_preferences": tool_update_report_preferences,
    "get_report_preferences": _no_args(tool_get_report_preferences),
    "setup_telegram": tool_setup_telegram,
    "setup_wechat": _no_args(tool_setup_wechat),
    "setup_feishu": tool_setup_feishu,
    "setup_qq": tool_setup_qq,
    "qq_add_user": tool_qq_add_user,
    "qq_list_users": _no_args(tool_qq_list_users),
    "qq_remove_user": tool_qq_remove_user,
    "get_canteen_crowd": tool_get_canteen_crowd,
    "get_canteen_info": tool_get_canteen_info,
    "recommend_canteen": tool_recommend_canteen,
    "record_dining_choice": tool_record_dining_choice,
    "get_dining_history": tool_get_dining_history,
}


# ── 工具 schema 校验（Harness）─────────────────────────────────────────────

# 从 TOOLS 构建 name → parameters schema 的查找表（含 MCP 外的内置工具）
_TOOL_SCHEMAS: dict[str, dict] = {}
for _t in TOOLS:
    _fn = _t.get("function", {})
    if _fn.get("name"):
        _TOOL_SCHEMAS[_fn["name"]] = _fn.get("parameters") or {}


def _coerce_args(name: str, args: dict | None) -> tuple[dict | None, str | None]:
    """按工具 schema 校验必填 + 规约参数类型。返回 (coerced_args, error)。

    - 无 schema（如 mcp__* 外部工具）→ 原样放行
    - 缺必填 → 明确报错（替代晦涩的 TypeError 崩溃）
    - 已知参数按类型规约（"true"→True, "3"→3），规约失败 → 报错
    - 未知参数保留透传（schema 可能不全，不拦截）
    """
    schema = _TOOL_SCHEMAS.get(name)
    if not schema:
        return args or {}, None
    properties = schema.get("properties") or {}
    required = schema.get("required") or []

    coerced = dict(args or {})
    for key, spec in properties.items():
        if key not in coerced:
            continue
        val = coerced[key]
        t = spec.get("type")
        try:
            if t == "integer":
                coerced[key] = int(val)
            elif t == "number":
                coerced[key] = float(val)
            elif t == "boolean":
                coerced[key] = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "yes")
            elif t == "array" and not isinstance(val, list):
                coerced[key] = [val]
            elif t == "object" and not isinstance(val, dict):
                coerced[key] = {}
        except (TypeError, ValueError):
            return None, f"参数 {key} 类型错误：期望 {t}，实际 {val!r}"

    for req in required:
        if req not in coerced:
            return None, f"缺少必填参数: {req}"
    return coerced, None


_SENSITIVE_ARG_KEYS = ("token", "secret", "password", "api_key", "app_secret")


def _log_tool_call(name: str, args: dict | None, elapsed: float, result) -> None:
    """记录一次工具调用（可观测）：名称 / 参数（脱敏）/ 耗时 / 结果长度。"""
    try:
        safe_args = {}
        for k, v in (args or {}).items():
            kl = k.lower()
            if any(s in kl for s in _SENSITIVE_ARG_KEYS):
                safe_args[k] = "***"
            elif isinstance(v, (str, int, float, bool)):
                safe_args[k] = v
            else:
                safe_args[k] = type(v).__name__
        r_len = len(str(result)) if result else 0
        _logger.info("[tool] %s args=%s %.3fs result_len=%d", name, safe_args, elapsed, r_len)
    except Exception:
        pass


def run_tool(name: str, args: dict) -> str:
    try:
        if name.startswith("mcp__"):
            from sjtu_agent.extensions.mcp_client import call_tool
            return call_tool(name, args or {})
        fn = _TOOL_REGISTRY.get(name)
        if not fn:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        coerced, err = _coerce_args(name, args)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        import time as _time
        _t0 = _time.monotonic()
        r = fn(**coerced)
        _elapsed = _time.monotonic() - _t0
        # 调用日志（可观测）：工具名 / 参数 / 耗时 / 结果长度
        _log_tool_call(name, args, _elapsed, r)
    except Exception as e:
        r = {"error": str(e)}
    return json.dumps(r, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════════
# Agent LLM 配置
# ══════════════════════════════════════════════════════════════════════════════

# 致远一号 API（交大官方 OpenAI 兼容接口）的环境变量名
_ZHIYUAN_BASE_URL_ENV = "ZHIYUAN_BASE_URL"
_ZHIYUAN_API_KEY_ENV  = "ZHIYUAN_API_KEY"
_ZHIYUAN_DEFAULT_BASE = "https://models.sjtu.edu.cn/api/v1"
_ZHIYUAN_DEFAULT_MODEL = "deepseek-chat"
