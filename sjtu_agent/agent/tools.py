"""sjtu_agent/agent/tools.py — 工具定义（TOOLS）与所有 tool_xxx 实现。

包含：
- TOOLS 列表（OpenAI function calling 格式）
- 所有 tool_xxx 函数（配置/DDL/作业/校园服务/成绩/提醒/邮件等）
- run_tool() 分发函数
- DDL 缓存辅助（_ddl_cache_*、_fetch_ddls_parallel）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
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
    USER_PROFILE_PATH,
    atomic_write_json,
    read_json_safe,
)
from sjtu_agent.parsing import parse_file as parse_router_file

ROOT = PROJECT_ROOT
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


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ddls",
            "description": "获取所有平台（Canvas / AI 好课（aihaoke） / 中国大学MOOC）未完成 DDL，按截止时间升序。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skip_canvas":  {"type": "boolean"},
                    "skip_aihaoke": {"type": "boolean"},
                    "skip_icourse": {"type": "boolean"},
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
            "description": "交互式授权水源社区 Discourse User API Key。会自动打开浏览器，用户在页面点击授权后自动完成，无需手动操作。用户说'配置水源'/'授权水源'/'设置水源'时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_mcp_server",
            "description": (
                "Register a custom external MCP server in config.json. "
                "Use when the user asks to add/connect/configure a custom MCP server. "
                "The first chat-triggered call must leave acknowledge_external_mcp=false "
                "so the user is warned before an external command or URL is trusted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "server_id": {"type": "string", "description": "Short MCP server id, e.g. filesystem or my_tools."},
                    "transport": {
                        "type": "string",
                        "enum": ["stdio", "sse", "streamable_http", "http"],
                        "description": "MCP transport. Defaults to stdio.",
                    },
                    "command": {"type": "string", "description": "Command for stdio transport, e.g. python or node."},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command arguments for stdio transport.",
                    },
                    "url": {"type": "string", "description": "MCP endpoint URL for sse/http transports."},
                    "cwd": {"type": "string", "description": "Optional working directory for stdio transport."},
                    "env": {"type": "object", "description": "Optional environment variables for stdio transport."},
                    "headers": {"type": "object", "description": "Optional HTTP headers for sse/http transports."},
                    "enabled": {"type": "boolean", "description": "Whether to enable immediately. Defaults to true."},
                    "call_timeout": {"type": "integer", "description": "Tool call timeout in seconds. Defaults to 120."},
                    "acknowledge_external_mcp": {
                        "type": "boolean",
                        "description": "Must be true before saving from chat after the external MCP warning.",
                    },
                },
                "required": ["server_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_skill",
            "description": (
                "Create or update a custom prompt-only skill under the sjtu-agent data directory "
                "and optionally enable it. Use when the user asks to add a custom skill."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill directory name, e.g. my-skill."},
                    "content": {"type": "string", "description": "SKILL.md content to write."},
                    "source_file": {"type": "string", "description": "Optional local file path to read SKILL.md content from."},
                    "enabled": {"type": "boolean", "description": "Whether to add the skill to skills.enabled. Defaults to true."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": (
                "Create a prompt-only skill from a user's natural-language requirements. "
                "If the requirement is underspecified, return follow-up questions instead "
                "of writing a skill. Use when the user asks for skill creator / create a skill."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short skill id, e.g. exam-planner."},
                    "purpose": {"type": "string", "description": "What user need should trigger this skill."},
                    "triggers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Example phrases or situations that should activate this skill.",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Concrete instructions the agent should follow when the skill is active.",
                    },
                    "constraints": {"type": "string", "description": "Optional boundaries, safety notes, or style constraints."},
                    "examples": {"type": "string", "description": "Optional examples of good use."},
                    "content": {"type": "string", "description": "Optional full SKILL.md content. Overrides generated content."},
                    "enabled": {"type": "boolean", "description": "Whether to enable after creation. Defaults to true."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List available prompt-only skills, their enabled state, source, and SKILL.md path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_content": {
                        "type": "boolean",
                        "description": "Whether to include SKILL.md text snippets. Defaults to false.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_skill",
            "description": "Enable, disable, or delete a prompt-only skill. Deletion is limited to user-data skills.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["enable", "disable", "delete"]},
                    "name": {"type": "string", "description": "Skill name / directory id."},
                },
                "required": ["action", "name"],
            },
        },
    },
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
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": (
                "添加一条提醒事项到本地列表。"
                "用户说「帮我记一下」「提醒我」「记得要...」「把 XXX 加到提醒」时调用。"
                "start 是提醒开始时间（或事项截止时间），end 是可选的结束时间。"
                "若用户未提供具体时间，从上下文推断或询问。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "提醒标题，简洁描述事项"},
                    "start": {"type": "string", "description": "开始时间，格式 'YYYY-MM-DD HH:MM'"},
                    "end":   {"type": "string", "description": "结束时间（可选），格式 'YYYY-MM-DD HH:MM'"},
                    "note":  {"type": "string", "description": "备注说明（可选）"},
                },
                "required": ["title", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": (
                "查看所有提醒事项（分为未过期/已过期）。"
                "用户说「我有什么提醒」「提醒事项」「记了什么」时调用。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_reminder",
            "description": "删除指定 id 的提醒事项。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {"type": "integer", "description": "要删除的提醒 id"},
                },
                "required": ["reminder_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_profile",
            "description": (
                "将本轮对话中观察到的用户信息更新到本地用户画像文件。"
                "每当你从对话中了解到用户的新信息（姓名/学号/专业/课程偏好/作息/情绪状态/"
                "近期压力/兴趣爱好/特殊事件等），就调用此工具记录。"
                "不要等用户主动说「更新画像」，而是每轮对话结束前自动判断是否有新信息需要记录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "object",
                        "description": (
                            "要更新的字段（只传有新信息的字段，不要覆盖未提及的字段）。\n"
                            "常用字段示例：\n"
                            "  name: str — 姓名或昵称\n"
                            "  major: str — 专业\n"
                            "  grade: str — 年级（如 大二）\n"
                            "  courses: list[str] — 正在上的课程\n"
                            "  stress_level: str — 近期压力（low/medium/high/overwhelmed）\n"
                            "  mood: str — 情绪（happy/normal/tired/anxious/sad）\n"
                            "  recent_events: list[str] — 近期重要事件（考试/答辩/面试/生日等）\n"
                            "  hobbies: list[str] — 兴趣爱好\n"
                            "  sleep_pattern: str — 作息（如 late_night/normal/early）\n"
                            "  last_active: str — 最后活跃时间（ISO 格式，自动填当前时间）\n"
                            "  care_notes: list[str] — 需要定期关怀提示（如 '明天考物理'）\n"
                            "  custom: dict — 其他自定义字段"
                        ),
                        "additionalProperties": True,
                    },
                    "reason": {
                        "type": "string",
                        "description": "简述为什么更新这些字段（供调试参考）",
                    },
                },
                "required": ["updates"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_profile",
            "description": (
                "读取当前用户画像，了解用户的基本信息、情绪状态、近期事件等。"
                "在准备给用户发送关怀消息或个性化回复前先调用，确保不重复关怀。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_telegram",
            "description": (
                "配置 Telegram Bot：将 telegram_token 和可选的 allowed_ids 保存到 config.json，"
                "然后可以用 sjtu-agent telegram-bot 启动 Bot。"
                "用户说「接入Telegram」「配置Telegram」「怎么用Telegram」「Telegram bot」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "telegram_token": {
                        "type": "string",
                        "description": "BotFather 给出的 Bot Token，格式如 1234567890:ABCdefGHI…",
                    },
                    "allowed_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "允许使用 Bot 的 Telegram user_id 列表（整数）。留空则 Bot 启动后会显示任意用户的 chat_id，可先留空再补填。",
                    },
                },
                "required": ["telegram_token"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_wechat",
            "description": (
                "配置微信 ilink Bot：打印登录二维码，让用户扫码完成微信接入，"
                "bot_token 自动保存到 config.json。"
                "用户说「接入微信」「配置微信」「微信 bot」「微信推送」时调用。"
                "注意：扫码登录必须在终端完成，此工具会打印二维码并等待用户扫码确认。"
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
            "name": "setup_feishu",
            "description": (
                "配置飞书 Bot 凭据（App ID 和 App Secret）。"
                "用户在 https://open.feishu.cn/app 创建企业自建应用，开启 Bot 能力、"
                "添加 im:message 权限、订阅 im.message.receive_v1 事件（WebSocket 模式）后，"
                "从「凭证与基础信息」页面获取 App ID 和 App Secret。"
                "用户说「接入飞书」「配置飞书」「飞书 bot」「飞书推送」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "feishu_app_id": {
                        "type": "string",
                        "description": "飞书应用的 App ID（cli_ 开头）",
                    },
                    "feishu_app_secret": {
                        "type": "string",
                        "description": "飞书应用的 App Secret",
                    },
                    "feishu_allowed_open_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "（可选）允许使用 Bot 的飞书用户 open_id 列表。留空则允许所有人。用户在飞书中给 Bot 发消息后，日志会显示其 open_id。",
                    },
                },
                "required": ["feishu_app_id", "feishu_app_secret"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_qq",
            "description": (
                "配置 QQ 官方机器人凭据（AppID + AppSecret）并保存到 config.json。"
                "调用后会尝试请求 QQ OpenAPI 校验凭据可用性。"
                "请先登录 https://q.qq.com/ ，进入机器人平台并创建机器人，再获取 AppID 与 AppSecret。"
                "如果某些字段已配置，可只传需要修改的字段；未传字段会保留原值。"
                "建议首次先不填 qq_allowed_user_ids，待目标用户给 Bot 发消息后再回填白名单。"
                "注意 qq_allowed_user_ids 填的是 QQ 用户标识（openid/id），不是 QQ 号。"
                "用户说“接入QQ”“配置QQ Bot”“QQ机器人”时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "qq_app_id": {
                        "type": "string",
                        "description": "QQ 机器人 AppID（在 https://q.qq.com/qqbot/openclaw/ 获取）。不传则保留当前值。",
                    },
                    "qq_app_secret": {
                        "type": "string",
                        "description": "QQ 机器人 AppSecret（在 https://q.qq.com/qqbot/openclaw/ 获取）。不传则保留当前值。",
                    },
                    "qq_allowed_user_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选白名单用户标识（openid/id，不是 QQ 号）。不传则保留当前值；传空数组 [] 表示清空白名单（允许所有用户）。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_add_user",
            "description": (
                "将一个 QQ 用户标识加入 qq_allowed_user_ids 白名单。"
                "如果没有 user_id，先提示用户：让待加入账号在 QQ 里给 Bot 发一条消息，"
                "从机器人提示/日志中拿到「QQ 用户标识」后再回填。"
                "注意这里填的是用户标识（openid/id），不是 QQ 号。"
                "用户说『增加QQ用户』『添加QQ白名单用户』时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "qq_user_id": {
                        "type": "string",
                        "description": "要加入白名单的 QQ 用户标识（openid/id，不是 QQ 号）。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_list_users",
            "description": (
                "列出当前 qq_allowed_user_ids 白名单。用户说『QQ用户列表』『查看QQ白名单』时调用。"
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
            "name": "qq_remove_user",
            "description": (
                "从 qq_allowed_user_ids 删除一个用户标识。"
                "用户说『删除QQ用户』『移除QQ白名单用户』时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "qq_user_id": {
                        "type": "string",
                        "description": "要移除的 QQ 用户标识（openid/id，不是 QQ 号）。",
                    },
                },
                "required": ["qq_user_id"],
            },
        },
    },
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
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": (
                "在当前项目环境中动态执行 Python 代码片段，用于完成没有现成工具的任务。"
                "当你想做某件事但没有对应工具时（例如：标记邮件已读、批量操作、数据处理、"
                "调用任意 API、读写文件等），先尝试写代码解决，实在做不到再报错。"
                "代码可以 import 任何已安装的包（imaplib/smtplib/requests/json/os 等）。"
                "代码中 print() 的输出会作为结果返回。"
                "注意：代码运行在受信任的本地环境，可以直接访问 os.environ、CONFIG_PATH 等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "要执行的 Python 代码。"
                            "可通过 import agent, ddl_checker as dc 引入项目模块。"
                            "结果用 print() 输出，或直接 raise 异常报错。\n"
                            "示例：将所有未读邮件设为已读：\n"
                            "  import imaplib, ssl, os\n"
                            "  ctx = ssl.create_default_context()\n"
                            "  m = imaplib.IMAP4_SSL('mail.sjtu.edu.cn', 993, ctx)\n"
                            "  user = os.environ['JACCOUNT_USERNAME'] + '@sjtu.edu.cn'\n"
                            "  m.login(user, os.environ['JACCOUNT_PASSWORD'])\n"
                            "  m.select('INBOX')\n"
                            "  m.uid('STORE', '1:*', '+FLAGS', '\\\\Seen')\n"
                            "  print('OK')\n"
                            "  m.logout()"
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 60",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_emails",
            "description": (
                "通过 IMAP 读取交大邮箱（mail.sjtu.edu.cn）邮件列表。"
                "用户说「看看邮件」「有没有新邮件」「读一下邮件」「查收件箱」时调用。"
                "默认读收件箱最新 10 封；也可以指定 uid 精确读取某一封的正文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "文件夹：INBOX（收件箱，默认）/ Sent（已发送）/ Drafts / Trash / 中文别名（收件箱/已发送/垃圾邮件）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回几封，默认 10",
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "只看未读邮件，默认 false",
                    },
                    "with_body": {
                        "type": "boolean",
                        "description": "同时返回邮件正文，默认 false（仅列表时不返回正文，节省 token）",
                    },
                    "uid": {
                        "type": "string",
                        "description": "指定读取某一封邮件的完整正文（uid 从 read_emails 列表结果中获取）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": (
                "按关键词搜索交大邮箱中的邮件（标题/发件人/全文）。"
                "用户说「找一下关于 XXX 的邮件」「搜索来自 XXX 的邮件」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "folder": {
                        "type": "string",
                        "description": "搜索的文件夹，默认 INBOX",
                    },
                    "search_in": {
                        "type": "string",
                        "enum": ["SUBJECT", "FROM", "TEXT", "TO"],
                        "description": "搜索范围：SUBJECT（主题，默认）/ FROM（发件人）/ TEXT（全文）/ TO（收件人）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回几封，默认 10",
                    },
                    "with_body": {
                        "type": "boolean",
                        "description": "同时返回搜索结果的正文，默认 false",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "用交大邮箱发送邮件（SMTP SSL）。"
                "用户说「发一封邮件给 XXX」「回复这封邮件」「帮我发邮件」时调用。"
                "发送前先向用户确认收件人、主题和正文内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "收件人邮箱地址，多个用逗号分隔",
                    },
                    "subject": {
                        "type": "string",
                        "description": "邮件主题",
                    },
                    "body": {
                        "type": "string",
                        "description": "邮件正文（纯文本）",
                    },
                    "cc": {
                        "type": "string",
                        "description": "抄送地址（可选，逗号分隔）",
                    },
                    "reply_to_uid": {
                        "type": "string",
                        "description": "回复某封邮件时传入原邮件 uid（自动补 In-Reply-To 头）",
                    },
                    "folder": {
                        "type": "string",
                        "description": "reply_to_uid 所在文件夹，默认 INBOX",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
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


def _canvas_settings_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/profile/settings"


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
    return {
        "platform":   item["platform"],
        "course":     item["course"],
        "name":       item["name"],
        "due":        item["due"].strftime("%Y-%m-%d %H:%M"),   # 已转为 CST，无需带 tz
        "hours_left": hours_left,                               # 负数=已过期
        "expired":    total_seconds < 0,
        "submitted":  item.get("submitted", False),
    }


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

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass

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


def tool_setup_shuiyuan() -> dict:
    """用 Playwright 登录水源社区，保存 session cookie（User API Key 方案已废弃）。"""
    username = os.environ.get("JACCOUNT_USERNAME", "").strip()
    password = os.environ.get("JACCOUNT_PASSWORD", "").strip()
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass

    if not username and not cfg.get("jaccount_cookies"):
        return {
            "error": "需要先配置 jAccount 凭据（save_credentials）",
            "next_action": "请先用 save_credentials 保存 jAccount 用户名和密码，再重试「配置水源」。",
        }

    return _setup_shuiyuan_session(cfg, username, password)


def _normalize_config_list(value) -> list:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return []


def _valid_config_id(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{label} is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", cleaned):
        raise ValueError(f"{label} may only contain letters, numbers, dot, underscore, and hyphen")
    return cleaned


def tool_add_mcp_server(
    server_id: str,
    transport: str = "stdio",
    command: str = "",
    args: list | None = None,
    url: str = "",
    cwd: str = "",
    env: dict | None = None,
    headers: dict | None = None,
    enabled: bool = True,
    call_timeout: int = 120,
    acknowledge_external_mcp: bool = False,
) -> dict:
    """Register a custom MCP server in config.json."""
    server_id = _valid_config_id(server_id, "server_id")
    transport = (transport or "stdio").strip().lower()
    if transport not in {"stdio", "sse", "streamable_http", "http"}:
        return {"error": f"Unsupported MCP transport: {transport}"}

    args = _normalize_config_list(args)
    env = env if isinstance(env, dict) else {}
    headers = headers if isinstance(headers, dict) else {}
    call_timeout = max(1, int(call_timeout or 120))

    if transport == "stdio":
        command = (command or "").strip()
        if not command:
            return {"error": "stdio MCP server requires `command`."}
        external_target = " ".join([command, *args]).strip()
    else:
        url = (url or "").strip()
        if not url:
            return {"error": f"{transport} MCP server requires `url`."}
        if not url.startswith(("http://", "https://")):
            return {"error": "MCP URL must start with http:// or https://"}
        external_target = url

    if not acknowledge_external_mcp:
        return {
            "requires_confirmation": True,
            "server_id": server_id,
            "transport": transport,
            "external_target": external_target,
            "message": (
                "This will register an external MCP server. The agent may execute the configured "
                "stdio command or send requests to the configured HTTP endpoint when tools are used."
            ),
            "next_action": (
                "Tell the user the exact external command or URL above. Only call add_mcp_server "
                "again with acknowledge_external_mcp=true after the user explicitly confirms."
            ),
        }

    cfg = read_json_safe(CONFIG_PATH, {})
    mcp_servers = cfg.get("mcp_servers", {})
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}

    server_cfg: dict = {
        "enabled": bool(enabled),
        "transport": transport,
        "call_timeout": call_timeout,
    }
    if transport == "stdio":
        server_cfg.update({"command": command, "args": args})
        if cwd:
            server_cfg["cwd"] = str(Path(os.path.expandvars(cwd)).expanduser())
        if env:
            server_cfg["env"] = {str(k): str(v) for k, v in env.items()}
    else:
        server_cfg["url"] = url
        if headers:
            server_cfg["headers"] = {str(k): str(v) for k, v in headers.items()}

    mcp_servers[server_id] = server_cfg
    cfg["mcp_servers"] = mcp_servers
    atomic_write_json(CONFIG_PATH, cfg)

    try:
        from sjtu_agent.extensions.mcp_client import list_openai_tools
        list_openai_tools(force_refresh=True)
    except Exception:
        pass

    return {
        "ok": True,
        "server_id": server_id,
        "config": server_cfg,
        "tool_prefix": f"mcp__{server_id}__",
        "next_action": "Restart or continue the conversation; MCP tools will be rediscovered automatically.",
    }


def tool_add_skill(
    name: str,
    content: str = "",
    source_file: str = "",
    enabled: bool = True,
) -> dict:
    """Create/update a prompt-only skill and optionally enable it."""
    name = _valid_config_id(name, "name")
    source_file = (source_file or "").strip()
    if source_file:
        path = Path(os.path.expandvars(source_file)).expanduser()
        if not path.exists() or not path.is_file():
            return {"error": f"source_file not found: {source_file}"}
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return {"error": f"failed to read source_file: {exc}"}
    if not (content or "").strip():
        return {"error": "Skill content is required. Provide content or source_file."}

    skill_dir = CONFIG_PATH.parent / "skills" / name
    skill_file = skill_dir / "SKILL.md"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(content.strip() + "\n", encoding="utf-8")

    cfg = read_json_safe(CONFIG_PATH, {})
    skills_cfg = cfg.get("skills", {})
    if not isinstance(skills_cfg, dict):
        skills_cfg = {}
    enabled_skills = _normalize_config_list(skills_cfg.get("enabled", []))
    if enabled and name not in enabled_skills:
        enabled_skills.append(name)
    if not enabled and name in enabled_skills:
        enabled_skills.remove(name)
    skills_cfg["enabled"] = enabled_skills
    cfg["skills"] = skills_cfg
    atomic_write_json(CONFIG_PATH, cfg)

    return {
        "ok": True,
        "name": name,
        "path": str(skill_file),
        "enabled": bool(enabled),
        "next_action": "The skill prompt will be included in newly built system prompts.",
    }


def _slugify_skill_name(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    return "-".join(words[:5])


def _render_skill_content(
    name: str,
    purpose: str,
    triggers: list | None,
    instructions: str,
    constraints: str = "",
    examples: str = "",
) -> str:
    lines = [
        f"# {name}",
        "",
        f"Use this skill when {purpose.strip()}",
    ]
    trigger_items = _normalize_config_list(triggers)
    if trigger_items:
        lines.extend(["", "## Triggers"])
        lines.extend(f"- {item}" for item in trigger_items)
    lines.extend(["", "## Instructions", instructions.strip()])
    if (constraints or "").strip():
        lines.extend(["", "## Constraints", constraints.strip()])
    if (examples or "").strip():
        lines.extend(["", "## Examples", examples.strip()])
    return "\n".join(lines).strip() + "\n"


def tool_create_skill(
    name: str = "",
    purpose: str = "",
    triggers: list | None = None,
    instructions: str = "",
    constraints: str = "",
    examples: str = "",
    content: str = "",
    enabled: bool = True,
) -> dict:
    """Create a prompt-only skill from structured requirements, or ask for missing details."""
    purpose = (purpose or "").strip()
    instructions = (instructions or "").strip()
    content = (content or "").strip()
    questions: list[str] = []

    if not (name or "").strip():
        derived = _slugify_skill_name(purpose or instructions)
        name = derived
    if not (name or "").strip():
        questions.append("What short skill name should be used, e.g. exam-planner?")
    if not purpose and not content:
        questions.append("What user need or situation should trigger this skill?")
    if not instructions and not content:
        questions.append("What should the agent do step by step when this skill is active?")

    if questions:
        return {
            "requires_more_info": True,
            "questions": questions,
            "next_action": "Ask the user these questions, then call create_skill again with the clarified details.",
        }

    name = _valid_config_id(name, "name")
    if not content:
        content = _render_skill_content(
            name=name,
            purpose=purpose,
            triggers=triggers,
            instructions=instructions,
            constraints=constraints,
            examples=examples,
        )
    result = tool_add_skill(name=name, content=content, enabled=enabled)
    if result.get("ok"):
        result["created_by"] = "create_skill"
    return result


def _configured_skill_dirs_for_tools() -> list[tuple[str, Path]]:
    cfg = read_json_safe(CONFIG_PATH, {})
    skills_cfg = cfg.get("skills", {})
    if not isinstance(skills_cfg, dict):
        skills_cfg = {}

    dirs: list[tuple[str, Path]] = [
        ("bundled", PACKAGE_ROOT / "skills"),
        ("repo", PROJECT_ROOT / "skills"),
        ("user", CONFIG_PATH.parent / "skills"),
    ]
    extra_dirs = skills_cfg.get("dirs", [])
    if isinstance(extra_dirs, list):
        for item in extra_dirs:
            if isinstance(item, str) and item.strip():
                dirs.append(("extra", Path(os.path.expandvars(item)).expanduser()))

    seen: set[str] = set()
    result: list[tuple[str, Path]] = []
    for source, path in dirs:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            result.append((source, path))
    return result


def _skill_summary(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:240]
    return ""


def tool_list_skills(include_content: bool = False) -> dict:
    """List all discovered prompt-only skills."""
    cfg = read_json_safe(CONFIG_PATH, {})
    skills_cfg = cfg.get("skills", {})
    if not isinstance(skills_cfg, dict):
        skills_cfg = {}
    enabled_names = _normalize_config_list(skills_cfg.get("enabled", []))
    enable_all = "*" in enabled_names
    enabled_set = set(enabled_names)

    entries: list[dict] = []
    seen: set[str] = set()
    for source, base in _configured_skill_dirs_for_tools():
        if not base.exists():
            continue
        for skill_file in sorted(base.glob("*/SKILL.md")):
            name = skill_file.parent.name
            key = str(skill_file.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                text = skill_file.read_text(encoding="utf-8")
            except OSError:
                text = ""
            item = {
                "name": name,
                "enabled": enable_all or name in enabled_set,
                "source": source,
                "path": str(skill_file),
                "summary": _skill_summary(text),
            }
            if include_content:
                item["content"] = text
            entries.append(item)

    return {
        "ok": True,
        "enabled": enabled_names,
        "skill_dirs": [{"source": source, "path": str(path)} for source, path in _configured_skill_dirs_for_tools()],
        "skills": entries,
    }


def tool_manage_skill(action: str, name: str) -> dict:
    """Enable, disable, or delete a prompt-only skill."""
    import shutil

    action = (action or "").strip().lower()
    if action not in {"enable", "disable", "delete"}:
        return {"error": "action must be one of: enable, disable, delete"}
    name = _valid_config_id(name, "name")

    cfg = read_json_safe(CONFIG_PATH, {})
    skills_cfg = cfg.get("skills", {})
    if not isinstance(skills_cfg, dict):
        skills_cfg = {}
    enabled_skills = _normalize_config_list(skills_cfg.get("enabled", []))

    if action == "enable" and name not in enabled_skills:
        enabled_skills.append(name)
    elif action in {"disable", "delete"} and name in enabled_skills:
        enabled_skills.remove(name)

    deleted = False
    if action == "delete":
        user_root = (CONFIG_PATH.parent / "skills").resolve()
        skill_dir = (user_root / name).resolve()
        if not skill_dir.is_relative_to(user_root):
            return {"error": "refusing to delete a skill outside the user skill directory"}
        if not skill_dir.exists():
            return {"error": f"user skill not found: {name}"}
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            return {"error": f"user skill has no SKILL.md: {name}"}
        shutil.rmtree(skill_dir)
        deleted = True

    skills_cfg["enabled"] = enabled_skills
    cfg["skills"] = skills_cfg
    atomic_write_json(CONFIG_PATH, cfg)

    return {
        "ok": True,
        "action": action,
        "name": name,
        "enabled": name in enabled_skills,
        "deleted": deleted,
    }


def _setup_shuiyuan_session(cfg: dict, username: str, password: str) -> dict:
    """降级方案：Playwright 登录水源，保存 session cookie。"""
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

    new_session: dict = {}
    with _sync_pw() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        if jaccount_cookies:
            ctx.add_cookies([
                {"name": k, "value": v, "domain": "jaccount.sjtu.edu.cn", "path": "/"}
                for k, v in jaccount_cookies.items()
            ])
        page = ctx.new_page()
        try:
            page.goto("https://shuiyuan.sjtu.edu.cn/", wait_until="networkidle", timeout=20_000)
        except Exception:
            pass
        if "jaccount" in page.url:
            if not username or not password:
                browser.close()
                return {"error": "需要 jAccount 凭据，请先用 save_credentials 配置"}
            try:
                if not login_module._fill_jaccount(page, username, password):
                    browser.close()
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
                browser.close()
                return _shuiyuan_session_error(f"jAccount 登录失败：{e}")
        new_session = {c["name"]: c["value"] for c in ctx.cookies()
                       if "shuiyuan.sjtu.edu.cn" in c.get("domain", "")}
        browser.close()

    if not new_session:
        return _shuiyuan_session_error("未能获取水源社区 session，请检查账号")

    cfg["shuiyuan_cookies"] = new_session
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    return {"success": True, "message": f"水源社区 session 登录成功（需定期更新）"}


# ── 选课社区 course.sjtu.plus ────────────────────────────────────────────────
# 该站是纯 SPA + 私有 API，所有 /api/ 接口需要 jAccount OAuth cookie 才能访问。
# 这里复用 jAccount cookie + Playwright 跑一次 OAuth 拿 course.sjtu.plus 域 cookie，
# 之后所有 API 调用直接 requests.get + cookie 即可，不再开浏览器。

_COURSE_PLUS_BASE = "https://course.sjtu.plus"


def tool_setup_course_community(username: str = "", password: str = "") -> dict:
    """登录 course.sjtu.plus 并保存 session cookie。

    课程社区登录页提供两个 tab：
      - 「邮箱密码登录」：POST /oauth/email/login/ {account: "<user>@sjtu.edu.cn", password}
      - 「账号登录」    ：POST /oauth/login/        {username, password}
    站内说明：用户通常用 jAccount 邮箱注册，密码自行设定（很多人会和 jAccount 一致）。

    默认行为：
      - username 缺省 → 使用 cfg['course_sjtu_username'] 或 env JACCOUNT_USERNAME
      - password 缺省 → 使用 cfg['course_sjtu_password'] 或 env JACCOUNT_PASSWORD
    先尝试 email 端点（account = "<username>@sjtu.edu.cn"），失败再回落到 username 端点。
    """
    import requests as _rq

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass

    username = (username or cfg.get("course_sjtu_username")
                or os.environ.get("JACCOUNT_USERNAME", "")).strip()
    password = (password or cfg.get("course_sjtu_password")
                or os.environ.get("JACCOUNT_PASSWORD", "")).strip()

    if not username or not password:
        return {
            "error": "未找到 course.sjtu.plus 账号密码，也未配置 jAccount 凭证",
            "next_action": (
                "请先配置 jAccount（save_credentials），或直接告诉我 course.sjtu.plus 上"
                "的用户名密码。注册地址：https://course.sjtu.plus/login 用 jAccount 邮箱验证码登录。"
            ),
        }

    account_email = username if "@" in username else f"{username}@sjtu.edu.cn"

    sess = _rq.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": _COURSE_PLUS_BASE + "/login",
        "Origin": _COURSE_PLUS_BASE,
    })

    try:
        sess.get(_COURSE_PLUS_BASE + "/", timeout=10)
    except Exception:
        pass
    # Django CSRF：cookie 里的 csrftoken 必须回传到 X-CSRFToken header
    _csrf = sess.cookies.get("csrftoken")
    if _csrf:
        sess.headers["X-CSRFToken"] = _csrf

    bare_user = username.split("@")[0]

    attempts = [
        ("email",    "/oauth/email/login/", {"account": bare_user, "password": password}),
        ("username", "/oauth/login/",       {"username": bare_user, "password": password}),
    ]

    last_err = None
    for kind, path, payload in attempts:
        try:
            r = sess.post(_COURSE_PLUS_BASE + path, json=payload, timeout=15)
        except Exception as e:
            last_err = f"[{kind}] 请求失败：{e}"
            continue

        if r.status_code == 200:
            new_session = dict(sess.cookies.get_dict())
            if not new_session.get("sessionid"):
                last_err = f"[{kind}] HTTP 200 但响应未包含 sessionid cookie"
                continue
            try:
                verify = sess.get(f"{_COURSE_PLUS_BASE}/api/me/", timeout=10)
                me_info = verify.json() if verify.status_code == 200 else None
                if verify.status_code in (401, 403):
                    last_err = f"[{kind}] cookie 校验失败（/api/me/ {verify.status_code}）"
                    continue
            except Exception:
                me_info = None

            cfg["course_sjtu_cookies"] = new_session
            cfg["course_sjtu_username"] = username
            cfg["course_sjtu_password"] = password
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
            return {
                "success": True,
                "message": f"选课社区登录成功（{kind} 端点）",
                "logged_in_as": (me_info or {}).get("username") if isinstance(me_info, dict) else None,
            }

        detail = ""
        try:
            detail = r.json().get("detail") or r.text[:200]
        except Exception:
            detail = r.text[:200]
        last_err = f"[{kind}] HTTP {r.status_code}：{detail}"
        # 400/401/403 都是凭据错（站点用 400 "用户名或密码错误。"）；密码相同不必再试另一端点
        if r.status_code in (400, 401, 403) and kind == "email":
            return {
                "error": f"登录失败：{last_err}",
                "next_action": (
                    "course.sjtu.plus 站内密码看起来和 jAccount 不一致。"
                    "请去 https://course.sjtu.plus/login 用「邮箱验证登录」登入后，"
                    "在「偏好设置」里查看/重置站内密码，然后告诉我，调用 "
                    "setup_course_community(password='<站内密码>') 完成配置。"
                ),
            }

    return {"error": f"两种登录方式都失败：{last_err}"}


def _course_plus_request(path: str, params: dict | None = None, max_retry: int = 2):
    """带 cookie 调 course.sjtu.plus 私有 API，自动重试。返回 (data_or_None, error_str_or_None)。"""
    import time as _time
    import requests as _rq

    cfg = dc.load_config()
    cookies = cfg.get("course_sjtu_cookies") or {}
    if not cookies:
        return None, "选课社区未配置，请说「配置选课社区」完成登录"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Referer": _COURSE_PLUS_BASE + "/",
    }
    url = _COURSE_PLUS_BASE + path
    last_err = None
    for attempt in range(max_retry):
        try:
            r = _rq.get(url, params=params or {}, headers=headers, cookies=cookies, timeout=15)
            if r.status_code == 429:
                _time.sleep(15 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                return None, "选课社区凭证已过期，请说「配置选课社区」重新授权"
            if r.status_code == 404:
                return None, "选课社区返回 404（资源不存在或路径已变）"
            r.raise_for_status()
            return r.json(), None
        except Exception as e:
            last_err = str(e)
            _time.sleep(1 + attempt)
    return None, f"选课社区请求失败：{last_err}"


def tool_search_courses(query: str, page_size: int = 8) -> dict:
    """在选课社区 course.sjtu.plus 搜索课程，返回简要列表。

    返回每门课的 id / name / 老师 / 学院 / 平均评分 / 评价数，用于让用户挑选后再用
    get_course_detail 查看详情和评价。
    """
    if not query or not query.strip():
        return {"error": "query 不能为空"}
    data, err = _course_plus_request("/api/search/", {"q": query.strip(), "page_size": max(1, min(20, page_size))})
    if err:
        return {"error": err}

    results = []
    raw = data.get("results") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return {"error": "选课社区返回结构异常", "raw_keys": list(data.keys()) if isinstance(data, dict) else None}

    for item in raw[:page_size]:
        _r = item.get("rating")
        if isinstance(_r, dict):
            _avg = _r.get("avg")
            _rcount = _r.get("count")
        else:
            _avg = _r or item.get("avg_rating")
            _rcount = item.get("review_count") or item.get("reviews_count")
        results.append({
            "id":         item.get("id") or item.get("course_id"),
            "code":       item.get("code") or item.get("course_code"),
            "name":       item.get("name") or item.get("title"),
            "teachers":   item.get("teachers") or item.get("teacher") or item.get("main_teacher"),
            "department": item.get("department") or item.get("dept"),
            "credit":     item.get("credit"),
            "rating":     _avg,
            "review_count": _rcount,
            "url":        f"{_COURSE_PLUS_BASE}/course/{item.get('id') or item.get('course_id')}/"
                          if (item.get("id") or item.get("course_id")) else None,
        })

    return {
        "query": query,
        "count": len(results),
        "total": data.get("count") if isinstance(data, dict) else None,
        "results": results,
    }


def tool_get_course_detail(course_id: int, max_reviews: int = 10) -> dict:
    """获取选课社区某门课的详细信息和最新若干条评价。"""
    if not course_id:
        return {"error": "course_id 不能为空"}
    detail, err = _course_plus_request(f"/api/course/{course_id}/")
    if err:
        return {"error": err}

    review_data, rerr = _course_plus_request(f"/api/course/{course_id}/review/", {"page_size": max(1, min(20, max_reviews))})
    reviews_raw = []
    if not rerr and isinstance(review_data, dict):
        reviews_raw = review_data.get("results") or []
    elif not rerr and isinstance(review_data, list):
        reviews_raw = review_data

    def _trim(t: str, n: int = 600) -> str:
        if not t:
            return ""
        t = t.strip()
        return t if len(t) <= n else t[:n] + "..."

    reviews = []
    for r in reviews_raw[:max_reviews]:
        reviews.append({
            "rating":     r.get("rating") or r.get("score"),
            "semester":   r.get("semester") or r.get("term"),
            "created_at": r.get("created_at") or r.get("created"),
            "content":    _trim(r.get("content") or r.get("comment") or r.get("text") or ""),
            "likes":      r.get("likes") or r.get("like_count"),
        })

    _mt = detail.get("main_teacher") or {}
    _tg = detail.get("teacher_group") or []
    _teachers = (
        detail.get("teachers")
        or detail.get("teacher")
        or (_mt.get("name") if isinstance(_mt, dict) else None)
        or ", ".join([t.get("name", "") for t in _tg if isinstance(t, dict)]) or None
    )
    _rating = detail.get("rating")
    if isinstance(_rating, dict):
        _avg = _rating.get("avg")
        _rcount = _rating.get("count")
    else:
        _avg = _rating or detail.get("avg_rating")
        _rcount = detail.get("review_count") or detail.get("reviews_count")

    return {
        "id":         detail.get("id") or course_id,
        "code":       detail.get("code") or detail.get("course_code"),
        "name":       detail.get("name") or detail.get("title"),
        "teachers":   _teachers,
        "department": detail.get("department") or detail.get("dept"),
        "credit":     detail.get("credit"),
        "category":   detail.get("category"),
        "rating":     _avg,
        "review_count": _rcount,
        "summary":    detail.get("summary") or detail.get("description"),
        "url":        f"{_COURSE_PLUS_BASE}/course/{detail.get('id') or course_id}/",
        "reviews":    reviews,
        "reviews_returned": len(reviews),
    }


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
        os.environ["JACCOUNT_PASSWORD"] = jaccount_password
        updated.append("jAccount 密码")
    if mooc_username:
        set_env("MOOC_USERNAME", mooc_username)
        os.environ["MOOC_USERNAME"] = mooc_username
        updated.append("MOOC 用户名")
    if mooc_password:
        set_env("MOOC_PASSWORD", mooc_password)
        os.environ["MOOC_PASSWORD"] = mooc_password
        updated.append("MOOC 密码")

    if any([jaccount_username, jaccount_password, mooc_username, mooc_password]):
        ENV_PATH.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass

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


def _fetch_ddls_parallel(cfg: dict, skip_canvas=False, skip_aihaoke=False, skip_icourse=False) -> list:
    """并行拉取各平台 DDL，返回合并列表（未排序）。
    优先使用 15 分钟内的磁盘缓存，缓存命中时无需发起任何网络请求。
    """
    import concurrent.futures as _cf

    cache_key = f"{skip_canvas},{skip_aihaoke},{skip_icourse}"
    cached = _ddl_cache_get(cache_key)
    if cached is not None:
        return cached

    tasks = []
    if not skip_canvas:   tasks.append(("canvas",  lambda: dc.fetch_canvas(cfg)))
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
                print(f"[DDL] {futures[fut]} 拉取失败：{e}")

    _ddl_cache_set(cache_key, all_ddl)
    return all_ddl


def _prefetch_ddls_background() -> None:
    """在独立子进程中静默预热 DDL 缓存，不阻塞主进程，不向终端输出任何内容。
    子进程的 stdout/stderr 统一重定向到 devnull，完全不干扰主进程终端。
    """
    import subprocess as _sp
    import sys as _sys
    import os as _os

    cached = _ddl_cache_get("False,False,False")
    if cached is not None:
        return  # 缓存仍有效，无需预热

    # 用 -c 片段在子进程里静默执行拉取
    _script = (
        "import sys, os; sys.path.insert(0, os.path.dirname(sys.argv[0]) or '.'); "
        "import agent as _a, ddl_checker as _dc; "
        "_a._fetch_ddls_parallel(_dc.load_config())"
    )
    try:
        _sp.Popen(
            [_sys.executable, "-c", _script],
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
            cwd=str(Path(__file__).resolve().parent),
        )
    except Exception:
        pass  # 预热失败不影响主进程


def _check_for_updates() -> None:
    """
    在后台线程中检查 git 远程是否有新提交。
    若检测到更新，启动完成后打印一行提示，引导用户运行 sjtu-agent update。
    非 git 仓库 / 无网络时静默失败，不影响任何功能。
    """
    import shutil as _shutil
    import subprocess as _sub

    git = _shutil.which("git")
    if not git:
        return

    project_root = str(Path(__file__).resolve().parent)
    try:
        # 检查是否在 git 仓库内
        r = _sub.run(
            [git, "rev-parse", "--is-inside-work-tree"],
            cwd=project_root, capture_output=True, timeout=5,
        )
        if r.returncode != 0:
            return

        # 静默 fetch（只更新远端引用，不改变本地分支）
        _sub.run(
            [git, "fetch", "--quiet", "--no-tags", "origin"],
            cwd=project_root, capture_output=True, timeout=15,
        )

        # 比较本地 HEAD 与 origin/HEAD（或 origin/main）
        local_hash = _sub.run(
            [git, "rev-parse", "HEAD"],
            cwd=project_root, capture_output=True, timeout=5,
        ).stdout.decode().strip()

        # 尝试 @{u}（跟踪分支），失败则 origin/main
        r2 = _sub.run(
            [git, "rev-parse", "@{u}"],
            cwd=project_root, capture_output=True, timeout=5,
        )
        if r2.returncode == 0:
            remote_hash = r2.stdout.decode().strip()
        else:
            r3 = _sub.run(
                [git, "rev-parse", "origin/main"],
                cwd=project_root, capture_output=True, timeout=5,
            )
            if r3.returncode != 0:
                return
            remote_hash = r3.stdout.decode().strip()

        if local_hash and remote_hash and local_hash != remote_hash:
            # 统计落后几个提交
            r4 = _sub.run(
                [git, "rev-list", "--count", f"{local_hash}..{remote_hash}"],
                cwd=project_root, capture_output=True, timeout=5,
            )
            behind = r4.stdout.decode().strip() if r4.returncode == 0 else "?"
            # 存入模块级变量，启动完成后打印
            _UPDATE_AVAILABLE["behind"] = behind
    except Exception:
        pass  # 网络不通或其他异常，静默忽略


# 用于在主线程启动完成后读取后台更新检查结果
_UPDATE_AVAILABLE: dict = {}


def tool_get_ddls(skip_canvas=False, skip_aihaoke=False, skip_icourse=False):
    import datetime as _dt
    cfg = dc.load_config()
    now = _dt.datetime.now(dc.CST)
    all_ddl = _fetch_ddls_parallel(cfg, skip_canvas, skip_aihaoke, skip_icourse)
    all_ddl.sort(key=lambda x: x["due"])
    warnings = []
    if not skip_canvas and not (cfg.get("canvas_token") and not cfg.get("canvas_token", "").startswith("YOUR_")):
        warnings.append("Canvas 未配置 token；请先调用 setup_canvas 获取引导，生成后再用 save_credentials 保存。")
    return {
        "current_time": now.strftime("%Y-%m-%d %H:%M"),
        "ddls": [_serialize_ddl(x, now) for x in all_ddl if not x.get("submitted")],
        "warnings": warnings,
    }


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


def tool_browse_mysjtu(task: str, start_url: str = "https://my.sjtu.edu.cn", action: str = "") -> dict:
    """
    用 Playwright 打开 my.sjtu.edu.cn，执行可选操作，返回页面文字内容。
    先查本地服务目录缓存，命中则直接跳转目标 URL，无需多级导航。
    """
    try:
        from playwright.sync_api import sync_playwright as _spw
    except ImportError:
        return {"error": "未安装 playwright"}

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


# ══════════════════════════════════════════════════════════════════════════════
# 提醒事项
# ══════════════════════════════════════════════════════════════════════════════

def _load_reminders() -> list[dict]:
    if not REMINDERS_PATH.exists():
        return []
    try:
        return json.loads(REMINDERS_PATH.read_text(encoding="utf-8")).get("reminders", [])
    except Exception:
        return []


def _save_reminders(reminders: list[dict]) -> None:
    REMINDERS_PATH.write_text(
        json.dumps({"reminders": reminders}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def tool_add_reminder(
    title: str,
    start: str,
    end: str = "",
    note: str = "",
) -> dict:
    """
    添加一条提醒事项。
    start/end: ISO 8601 或 'YYYY-MM-DD HH:MM'（默认上海时区）。
    """
    import datetime as _dt
    def _parse(s: str) -> _dt.datetime | None:
        if not s:
            return None
        s = s.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = _dt.datetime.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dc.CST)
                return dt
            except ValueError:
                continue
        return None

    start_dt = _parse(start)
    if start_dt is None:
        return {"error": f"无法解析时间：{start!r}，请使用 'YYYY-MM-DD HH:MM' 格式"}

    reminders = _load_reminders()
    new_id = max((r["id"] for r in reminders), default=0) + 1
    entry = {
        "id":    new_id,
        "title": title.strip(),
        "start": start_dt.isoformat(),
        "end":   _parse(end).isoformat() if end else "",
        "note":  note.strip(),
    }
    reminders.append(entry)
    _save_reminders(reminders)
    return {"ok": True, "id": new_id, "reminder": entry}


def tool_list_reminders() -> dict:
    """列出所有提醒事项，标注是否已过期。"""
    import datetime as _dt
    now = _dt.datetime.now(dc.CST)
    reminders = _load_reminders()
    items = []
    for r in reminders:
        end_str = r.get("end", "")
        expired = False
        if end_str:
            try:
                end_dt = _dt.datetime.fromisoformat(end_str)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=dc.CST)
                expired = end_dt < now
            except Exception:
                pass
        items.append({**r, "expired": expired})
    active   = [i for i in items if not i["expired"]]
    inactive = [i for i in items if i["expired"]]
    return {
        "current_time": now.strftime("%Y-%m-%d %H:%M"),
        "active_count": len(active),
        "active":   active,
        "expired":  inactive,
    }


def tool_remove_reminder(reminder_id: int) -> dict:
    """删除指定 id 的提醒事项。"""
    reminders = _load_reminders()
    new_list = [r for r in reminders if r["id"] != reminder_id]
    if len(new_list) == len(reminders):
        return {"error": f"未找到 id={reminder_id} 的提醒事项"}
    _save_reminders(new_list)
    return {"ok": True, "removed_id": reminder_id}


# ══════════════════════════════════════════════════════════════════════════════
# 交大邮箱（IMAP / SMTP）
# ══════════════════════════════════════════════════════════════════════════════

_SJTU_IMAP_HOST = "mail.sjtu.edu.cn"
_SJTU_IMAP_PORT = 993
_SJTU_SMTP_HOST = "mail.sjtu.edu.cn"
_SJTU_SMTP_PORT = 465


def _get_email_creds() -> tuple[str, str]:
    """从环境变量读取邮箱账号和密码。
    账号格式：学号@sjtu.edu.cn，密码同 jAccount 密码（或独立邮箱密码）。
    优先用 EMAIL_USERNAME / EMAIL_PASSWORD，回退到 JACCOUNT_USERNAME / JACCOUNT_PASSWORD。
    """
    username = os.environ.get("EMAIL_USERNAME", "").strip()
    password = os.environ.get("EMAIL_PASSWORD", "").strip()
    if not username:
        username = os.environ.get("JACCOUNT_USERNAME", "").strip()
        if username and "@" not in username:
            username = username + "@sjtu.edu.cn"
    if not password:
        password = os.environ.get("JACCOUNT_PASSWORD", "").strip()
    return username, password


def _imap_connect():
    """建立 IMAP4_SSL 连接并登录，返回 imaplib.IMAP4_SSL 对象。"""
    import imaplib, ssl
    username, password = _get_email_creds()
    if not username or not password:
        raise ValueError("未配置邮箱账号或密码（EMAIL_USERNAME / EMAIL_PASSWORD 或 JACCOUNT_USERNAME / JACCOUNT_PASSWORD）")
    ctx = ssl.create_default_context()
    m = imaplib.IMAP4_SSL(_SJTU_IMAP_HOST, _SJTU_IMAP_PORT, ssl_context=ctx)
    m.login(username, password)
    return m


def _parse_email_headers(raw_bytes: bytes) -> dict:
    """从 RFC 822 头字节解析 Subject / From / To / Date。"""
    import email as _email
    import email.header as _hdr
    msg = _email.message_from_bytes(raw_bytes)

    def _decode(value: str | None) -> str:
        if not value:
            return ""
        parts = _hdr.decode_header(value)
        result = []
        for text, charset in parts:
            if isinstance(text, bytes):
                try:
                    result.append(text.decode(charset or "utf-8", errors="replace"))
                except LookupError:
                    result.append(text.decode("utf-8", errors="replace"))
            else:
                result.append(text)
        return "".join(result)

    return {
        "subject": _decode(msg.get("Subject")),
        "from":    _decode(msg.get("From")),
        "to":      _decode(msg.get("To")),
        "date":    _decode(msg.get("Date")),
        "message_id": (msg.get("Message-ID") or "").strip(),
    }


def _parse_email_body(raw_bytes: bytes, max_chars: int = 3000) -> str:
    """提取邮件纯文本正文（text/plain 优先，其次 text/html 去标签）。"""
    import email as _email
    import re as _re
    msg = _email.message_from_bytes(raw_bytes)

    def _walk(part) -> str:
        ct = part.get_content_type()
        if ct == "text/plain":
            try:
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
            except Exception:
                return ""
        if ct == "text/html":
            try:
                charset = part.get_content_charset() or "utf-8"
                html = part.get_payload(decode=True).decode(charset, errors="replace")
                text = _re.sub(r"<[^>]+>", "", html)
                text = _re.sub(r"&nbsp;", " ", text)
                text = _re.sub(r"&lt;", "<", text)
                text = _re.sub(r"&gt;", ">", text)
                text = _re.sub(r"&amp;", "&", text)
                text = _re.sub(r"\s{3,}", "\n\n", text)
                return text.strip()
            except Exception:
                return ""
        return ""

    # 优先找 text/plain
    plain = ""
    html_fallback = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            ct = part.get_content_type()
            if ct == "text/plain" and not plain:
                plain = _walk(part)
            elif ct == "text/html" and not html_fallback:
                html_fallback = _walk(part)
    else:
        text = _walk(msg)
        if msg.get_content_type() == "text/html":
            html_fallback = text
        else:
            plain = text

    body = plain or html_fallback
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…（正文已截断）"
    return body.strip()


def tool_read_emails(
    folder: str = "INBOX",
    limit: int = 10,
    unread_only: bool = False,
    with_body: bool = False,
    uid: str = "",
) -> dict:
    """
    通过 IMAP 读取交大邮箱邮件。
    folder: 文件夹名（INBOX / Sent / Drafts / Trash，中文如 已发送 也可）
    limit: 最多返回几封，默认 10
    unread_only: 只看未读邮件
    with_body: 同时返回邮件正文
    uid: 指定读取某一封邮件（uid 从列表里取）
    """
    import imaplib

    try:
        m = _imap_connect()
    except ValueError as e:
        return {"error": str(e), "hint": "请先配置 EMAIL_USERNAME / EMAIL_PASSWORD 或 JACCOUNT_USERNAME / JACCOUNT_PASSWORD"}
    except Exception as e:
        return {"error": f"IMAP 登录失败：{e}"}

    try:
        # 处理中文文件夹别名
        _folder_map = {
            "收件箱": "INBOX",
            "已发送": "Sent",
            "发件箱": "Sent",
            "垃圾邮件": "Junk",
            "已删除": "Trash",
            "草稿": "Drafts",
            "草稿箱": "Drafts",
        }
        select_folder = _folder_map.get(folder, folder)

        # 只读取单封（by uid）
        if uid:
            typ, data = m.select(select_folder, readonly=True)
            if typ != "OK":
                # 尝试加引号（含空格或特殊字符的文件夹名需要）
                typ, data = m.select(f'"{select_folder}"', readonly=True)
            typ2, raw = m.uid("FETCH", uid, "(RFC822)")
            m.close()
            m.logout()
            if typ2 != "OK" or not raw or not raw[0]:
                return {"error": f"未找到 UID={uid} 的邮件"}
            raw_bytes = raw[0][1]
            headers = _parse_email_headers(raw_bytes)
            body    = _parse_email_body(raw_bytes)
            return {"uid": uid, **headers, "body": body}

        # 批量读取
        typ, data = m.select(select_folder, readonly=True)
        if typ != "OK":
            typ, data = m.select(f'"{select_folder}"', readonly=True)
        if typ != "OK":
            m.logout()
            return {"error": f"无法打开文件夹 '{select_folder}'，请检查文件夹名称"}

        search_criteria = "UNSEEN" if unread_only else "ALL"
        typ, uids_data = m.uid("SEARCH", search_criteria)
        if typ != "OK":
            m.close(); m.logout()
            return {"error": "搜索失败"}

        uid_list = uids_data[0].decode().split() if uids_data[0] else []
        uid_list = uid_list[-limit:]   # 取最新的 limit 封

        emails = []
        for _uid in reversed(uid_list):
            fetch_spec = "(RFC822)" if with_body else "(RFC822.HEADER)"
            typ2, raw = m.uid("FETCH", _uid, fetch_spec)
            if typ2 != "OK" or not raw or not raw[0]:
                continue
            raw_bytes = raw[0][1]
            entry = {"uid": _uid, **_parse_email_headers(raw_bytes)}
            if with_body:
                entry["body"] = _parse_email_body(raw_bytes)
            emails.append(entry)

        m.close()
        m.logout()
        return {
            "folder":       select_folder,
            "total_found":  len(uid_list),
            "returned":     len(emails),
            "emails":       emails,
        }
    except Exception as e:
        try: m.logout()
        except Exception: pass
        return {"error": str(e)}


def tool_search_emails(
    keyword: str,
    folder: str = "INBOX",
    search_in: str = "SUBJECT",
    limit: int = 10,
    with_body: bool = False,
) -> dict:
    """
    搜索交大邮箱中的邮件。
    keyword: 搜索关键词
    folder: 文件夹（默认 INBOX）
    search_in: SUBJECT（主题）/ FROM（发件人）/ TEXT（全文）/ TO（收件人）
    limit: 最多返回结果数
    with_body: 是否同时返回正文
    """
    import imaplib

    try:
        m = _imap_connect()
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"IMAP 登录失败：{e}"}

    try:
        _folder_map = {
            "收件箱": "INBOX", "已发送": "Sent", "垃圾邮件": "Junk",
            "已删除": "Trash", "草稿": "Drafts",
        }
        select_folder = _folder_map.get(folder, folder)
        typ, _ = m.select(select_folder, readonly=True)
        if typ != "OK":
            typ, _ = m.select(f'"{select_folder}"', readonly=True)
        if typ != "OK":
            m.logout()
            return {"error": f"无法打开文件夹 '{select_folder}'"}

        search_in_upper = search_in.upper()
        if search_in_upper not in ("SUBJECT", "FROM", "TEXT", "TO", "BODY"):
            search_in_upper = "SUBJECT"

        # IMAP SEARCH 字符串（UTF-8 CHARSET）
        try:
            typ, uids_data = m.uid(
                "SEARCH", "CHARSET", "UTF-8",
                search_in_upper, keyword.encode("utf-8"),
            )
        except imaplib.IMAP4.error:
            # 部分服务器不支持 UTF-8 charset，降级到 ASCII
            safe_kw = keyword.encode("ascii", errors="ignore").decode()
            typ, uids_data = m.uid("SEARCH", search_in_upper, f'"{safe_kw}"')

        uid_list = uids_data[0].decode().split() if (uids_data and uids_data[0]) else []
        uid_list = uid_list[-limit:]

        emails = []
        for _uid in reversed(uid_list):
            fetch_spec = "(RFC822)" if with_body else "(RFC822.HEADER)"
            typ2, raw = m.uid("FETCH", _uid, fetch_spec)
            if typ2 != "OK" or not raw or not raw[0]:
                continue
            raw_bytes = raw[0][1]
            entry = {"uid": _uid, **_parse_email_headers(raw_bytes)}
            if with_body:
                entry["body"] = _parse_email_body(raw_bytes)
            emails.append(entry)

        m.close()
        m.logout()
        return {
            "keyword":      keyword,
            "search_in":    search_in_upper,
            "folder":       select_folder,
            "total_found":  len(uid_list),
            "returned":     len(emails),
            "emails":       emails,
        }
    except Exception as e:
        try: m.logout()
        except Exception: pass
        return {"error": str(e)}


def tool_send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    reply_to_uid: str = "",
    folder: str = "INBOX",
) -> dict:
    """
    用交大邮箱发送邮件（SMTP SSL port 465）。
    to: 收件人（单个或逗号分隔多个）
    subject: 邮件主题
    body: 邮件正文（纯文本）
    cc: 抄送（可选，逗号分隔）
    reply_to_uid: 如果是回复某封邮件，传入原邮件的 IMAP uid（自动补 In-Reply-To）
    folder: reply_to_uid 所在文件夹（默认 INBOX）
    """
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.utils import formatdate, make_msgid

    username, password = _get_email_creds()
    if not username or not password:
        return {"error": "未配置邮箱账号，请设置 EMAIL_USERNAME / EMAIL_PASSWORD 或 JACCOUNT_USERNAME / JACCOUNT_PASSWORD"}

    # 若是回复，先取原邮件的 Message-ID
    in_reply_to = ""
    references  = ""
    if reply_to_uid:
        try:
            m = _imap_connect()
            _folder_map = {"收件箱": "INBOX", "已发送": "Sent"}
            sf = _folder_map.get(folder, folder)
            m.select(sf, readonly=True)
            typ, raw = m.uid("FETCH", reply_to_uid, "(RFC822.HEADER)")
            m.close(); m.logout()
            if typ == "OK" and raw and raw[0]:
                headers = _parse_email_headers(raw[0][1])
                in_reply_to = headers.get("message_id", "")
                references  = in_reply_to
        except Exception:
            pass

    msg = MIMEMultipart()
    msg["From"]    = username
    msg["To"]      = to
    msg["Date"]    = formatdate(localtime=True)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    if cc:
        msg["Cc"] = cc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"]  = references

    msg.attach(MIMEText(body, "plain", "utf-8"))

    recipients = [r.strip() for r in to.split(",") if r.strip()]
    if cc:
        recipients += [r.strip() for r in cc.split(",") if r.strip()]

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(_SJTU_SMTP_HOST, _SJTU_SMTP_PORT, context=ctx, timeout=30) as smtp:
            smtp.login(username, password)
            refused = smtp.sendmail(username, recipients, msg.as_bytes())
    except smtplib.SMTPAuthenticationError:
        return {"error": "SMTP 登录失败：用户名或密码错误。请尝试在网页邮箱开启「客户端授权码」并将授权码设为 EMAIL_PASSWORD。"}
    except Exception as e:
        return {"error": f"发送失败：{e}"}

    # 把副本 APPEND 到 IMAP 的 Sent 文件夹，否则网页邮箱「已发送」里看不到
    appended_to_sent = False
    sent_error = ""
    try:
        import imaplib, time
        m = _imap_connect()
        try:
            m.append("Sent", "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            appended_to_sent = True
        finally:
            try: m.logout()
            except Exception: pass
    except Exception as e:
        sent_error = str(e)

    return {
        "ok": True,
        "from": username,
        "to": to,
        "cc": cc,
        "subject": subject,
        "message_id": msg["Message-ID"],
        "refused": refused,
        "appended_to_sent": appended_to_sent,
        "sent_append_error": sent_error,
        "note": "SMTP queued accepted by mail.sjtu.edu.cn. If 对方没收到，请检查对方垃圾邮件箱。",
    }


def tool_get_user_profile() -> dict:
    """读取本地用户画像文件，返回画像数据。"""
    import datetime as _dt
    if not USER_PROFILE_PATH.exists():
        return {"exists": False, "profile": {}}
    try:
        profile = json.loads(USER_PROFILE_PATH.read_text(encoding="utf-8"))
        return {"exists": True, "profile": profile}
    except Exception as e:
        return {"exists": False, "error": str(e), "profile": {}}


def tool_update_user_profile(updates: dict, reason: str = "") -> dict:
    """将 updates 合并到本地用户画像文件（深度合并，不覆盖未提及字段）。"""
    import datetime as _dt

    profile: dict = {}
    if USER_PROFILE_PATH.exists():
        try:
            profile = json.loads(USER_PROFILE_PATH.read_text(encoding="utf-8"))
        except Exception:
            profile = {}

    def deep_merge(base: dict, patch: dict) -> dict:
        for k, v in patch.items():
            if k in base and isinstance(base[k], list) and isinstance(v, list):
                # list 字段：合并去重
                existing = base[k]
                for item in v:
                    if item not in existing:
                        existing.append(item)
            elif k in base and isinstance(base[k], dict) and isinstance(v, dict):
                deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    profile = deep_merge(profile, updates)
    profile["last_updated"] = _dt.datetime.now().isoformat()

    USER_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USER_PROFILE_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "updated_keys": list(updates.keys()), "reason": reason}


def tool_setup_wechat() -> dict:
    """
    获取微信 ilink Bot 登录二维码，返回 base64 图片供 Web UI 展示。
    Web UI 显示二维码后通过 /api/wechat/qr_status 轮询扫码结果。
    在终端模式下直接打印二维码 ASCII 并等待扫码完成。
    """
    try:
        import httpx as _httpx
        import io as _io
        import base64 as _b64
        import sys as _sys

        _ilink_base = "https://ilinkai.weixin.qq.com"
        resp = _httpx.get(f"{_ilink_base}/ilink/bot/get_bot_qrcode?bot_type=3", timeout=15)
        data = resp.json()
        qrcode_key = data["qrcode"]
        qrcode_url = data["qrcode_img_content"]

        # 生成二维码 base64 图片
        qr_b64 = ""
        try:
            import qrcode as _qrcode
            qr = _qrcode.QRCode(border=2)
            qr.add_data(qrcode_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            qr_b64 = _b64.b64encode(buf.getvalue()).decode()
        except Exception:
            pass

        # 判断是否在 Web 环境（有 qr_b64 就直接返回，让前端轮询）
        if qr_b64:
            return {
                "success": False,  # 还未完成扫码
                "pending": True,
                "qr_base64": qr_b64,
                "qr_url": qrcode_url,
                "qrcode_key": qrcode_key,
                "message": "请用微信扫描上方二维码。扫码成功后会自动更新状态。",
                "ilink_base": _ilink_base,
            }

        # 终端模式：打印 ASCII 二维码并等待
        try:
            import qrcode as _qrcode
            qr = _qrcode.QRCode(border=1)
            qr.add_data(qrcode_url)
            qr.make(fit=True)
            print("\n请用微信扫描以下二维码：\n")
            qr.print_ascii(invert=True)
        except Exception:
            print(f"\n二维码链接（可手动打开）：{qrcode_url}\n")

        # 轮询扫码状态（终端模式，最多 5 分钟）
        import time as _time
        deadline = _time.monotonic() + 300
        while _time.monotonic() < deadline:
            try:
                status_resp = _httpx.get(
                    f"{_ilink_base}/ilink/bot/get_qrcode_status?qrcode={qrcode_key}",
                    timeout=10,
                )
                status = status_resp.json()
            except Exception:
                _time.sleep(3)
                continue

            code = status.get("code", -1)
            if code == 0:
                token      = status["bot_token"]
                account_id = status.get("account_id", "")
                user_id    = status.get("user_id", "")
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
                cfg["wechat_bot_token"]   = token
                cfg["wechat_account_id"] = account_id
                cfg["wechat_user_id"]    = user_id
                CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
                _started = _try_start_wechat_daemon()
                return {
                    "success": True,
                    "saved": True,
                    "message": "微信 Bot 登录成功！" + ("守护进程已自动启动。" if _started else "请运行 sjtu-agent wechat-bot。"),
                    "daemon_started": _started,
                }
            elif code in (1, 2):
                _time.sleep(2)
            else:
                return {"success": False, "error": f"二维码已过期（code={code}）"}

        return {"success": False, "error": "扫码超时（5分钟）"}

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "hint": "请在终端运行 python3 wechat_bot.py --login 完成扫码",
        }


def _try_start_wechat_daemon() -> bool:
    """尝试通过 launchctl kickstart 启动 wechat-bot 守护进程。"""
    import subprocess as _sp
    try:
        uid = os.getuid()
        result = _sp.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/com.sjtu.wechat-bot"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def tool_setup_telegram(telegram_token: str, allowed_ids: list | None = None) -> dict:
    """
    将 Telegram Bot Token 和可选的白名单 user_id 保存到 config.json。
    保存后用户可执行 sjtu-agent telegram-bot 启动 Bot。
    """
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    cfg["telegram_token"] = telegram_token.strip()
    if allowed_ids is not None:
        cfg["telegram_allowed_ids"] = [int(i) for i in allowed_ids]

    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # 验证 token 是否有效（可选，无网络或防火墙限制时跳过）
    token_valid: bool | None = None
    bot_info: dict = {}
    try:
        import requests as _req
        resp = _req.get(
            f"https://api.telegram.org/bot{telegram_token.strip()}/getMe",
            timeout=10,
        )
        if resp.status_code == 200:
            token_valid = True
            bot_info = resp.json().get("result", {})
        else:
            token_valid = False
    except Exception:
        token_valid = None  # 网络不通，跳过验证

    result: dict = {
        "saved": True,
        "token_valid": token_valid,
        "bot_username": bot_info.get("username", ""),
        "bot_name": bot_info.get("first_name", ""),
        "allowed_ids_set": allowed_ids or [],
        "next_steps": [
            "运行 `sjtu-agent telegram-bot` 启动 Bot（长轮询模式）。",
            "在 Telegram 中发送 /id 给 Bot，可以获得自己的 user_id，然后把它添加到白名单。",
            "如果还没有 Bot Token，先在 Telegram 里找 @BotFather，发 /newbot 创建。",
        ],
    }
    if not allowed_ids:
        result["tip"] = (
            "当前白名单为空，Bot 启动后会对所有发消息的用户返回其 chat_id，"
            "方便你确认自己的 user_id 后再来用 setup_telegram 补填白名单。"
        )
    return result


def tool_setup_feishu(feishu_app_id: str = "", feishu_app_secret: str = "", allowed_open_ids: list | None = None) -> dict:
    """
    将飞书 App ID 和 App Secret 保存到 config.json 并验证凭证有效性。
    用户在 https://open.feishu.cn/app 创建企业自建应用后可获取这些凭据。
    可选传入 allowed_open_ids 设置白名单。
    """
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    if feishu_app_id:
        cfg["feishu_app_id"] = feishu_app_id.strip()
    if feishu_app_secret:
        cfg["feishu_app_secret"] = feishu_app_secret.strip()
    if allowed_open_ids is not None:
        cfg["feishu_allowed_open_ids"] = allowed_open_ids

    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # 验证凭据有效性（获取 tenant_access_token）
    valid: bool | None = None
    app_info: dict = {}
    try:
        import requests as _req
        resp = _req.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": cfg.get("feishu_app_id", ""),
                "app_secret": cfg.get("feishu_app_secret", ""),
            },
            timeout=10,
        )
        if resp.status_code == 200:
            body = resp.json()
            if body.get("code") == 0:
                valid = True
                app_info["tenant_access_token_ok"] = True
            else:
                valid = False
                app_info["error"] = body.get("msg", f"code={body.get('code')}")
        else:
            valid = False
            app_info["error"] = f"HTTP {resp.status_code}"
    except Exception:
        valid = None  # 网络不通，跳过验证

    result: dict = {
        "saved": True,
        "valid": valid,
        "allowed_open_ids_set": allowed_open_ids or [],
        "next_steps": [
            "运行 `sjtu-agent feishu-bot` 启动 Bot（WebSocket 长连接模式）。",
            "在飞书搜索你的应用名称，进入对话即可使用。",
            "需要后台常驻运行 `sjtu-agent install-daemons` 安装守护进程。",
            "如尚未创建飞书应用，前往 https://open.feishu.cn/app 创建企业自建应用。",
        ],
    }
    if not allowed_open_ids:
        result["tip"] = (
            "当前白名单为空，Bot 启动后允许所有人对话。"
            "如需限制，在飞书给 Bot 发一条消息后查看日志中的 open_id，"
            "再用 setup_feishu 补填 allowed_open_ids 白名单。"
        )
    if app_info:
        result["app_info"] = app_info
    return result


def tool_setup_qq(
    qq_app_id: str = "",
    qq_app_secret: str = "",
    qq_allowed_user_ids: list | None = None,
) -> dict:
    """
    保存 QQ 官方机器人凭据到 config.json，并尝试请求官方接口校验凭据。
    """
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    if qq_app_id:
        cfg["qq_app_id"] = str(qq_app_id).strip()
    if qq_app_secret:
        cfg["qq_app_secret"] = str(qq_app_secret).strip()
    if qq_allowed_user_ids is not None:
        cfg["qq_allowed_user_ids"] = [str(x).strip() for x in qq_allowed_user_ids if str(x).strip()]

    effective_app_id = str(cfg.get("qq_app_id", "")).strip()
    effective_app_secret = str(cfg.get("qq_app_secret", "")).strip()
    if not effective_app_id or not effective_app_secret:
        return {
            "saved": False,
            "error": "qq_app_id 和 qq_app_secret 仍不完整，请补全后重试。",
            "current_state": {
                "qq_app_id_set": bool(effective_app_id),
                "qq_app_secret_set": bool(effective_app_secret),
                "qq_allowed_user_ids_count": len(cfg.get("qq_allowed_user_ids", []) or []),
            },
            "next_action": "请补充缺失字段；已存在字段可不传以保留原值。",
        }

    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    valid: bool | None = None
    details: dict = {}
    try:
        resp = requests.post(
            "https://bots.qq.com/app/getAppAccessToken",
            json={
                "appId": effective_app_id,
                "clientSecret": effective_app_secret,
            },
            timeout=10,
        )
        body = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
        if resp.status_code == 200 and body.get("access_token"):
            valid = True
            details = {"expires_in": body.get("expires_in")}
        else:
            valid = False
            details = {"http_status": resp.status_code, "response": body or resp.text[:300]}
    except Exception as e:
        valid = None
        details = {"error": str(e)}

    result = {
        "saved": True,
        "valid": valid,
        "details": details,
        "app_id_set": bool(effective_app_id),
        "app_secret_set": bool(effective_app_secret),
        "allowed_user_ids_set": cfg.get("qq_allowed_user_ids", []),
        "next_steps": [
            "请让要加入白名单的 QQ 账号给 Bot 发送一条消息，获取「QQ 用户标识」。",
            "把该用户标识回填给我（可直接调用 qq_add_user 或 setup_qq 填 qq_allowed_user_ids）。",
        ],
    }
    allowed_ids = cfg.get("qq_allowed_user_ids", []) or []
    if not allowed_ids:
        result["tip"] = (
            "当前白名单为空，Bot 启动后允许所有人对话。"
            "如需限制：先让目标用户给 Bot 发一条消息，获取其「QQ 用户标识」，"
            "再用 setup_qq 补填 qq_allowed_user_ids。"
            "注意这里填的是用户标识（openid/id），不是 QQ 号。"
        )
    else:
        result["tip"] = (
            f"当前已设置 {len(allowed_ids)} 个白名单用户标识。"
            "仅列表内用户可用 Bot。"
            "若需调整，请再次调用 setup_qq 更新 qq_allowed_user_ids。"
        )
    return result


def _load_cfg_for_qq_users() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cfg_for_qq_users(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalized_qq_user_list(raw: list | None) -> list[str]:
    values = raw or []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        user_id = str(item).strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        out.append(user_id)
    return out


def tool_qq_add_user(qq_user_id: str = "") -> dict:
    """
    向 QQ 白名单添加一个用户标识（openid/id）。
    """
    user_id = str(qq_user_id).strip()
    if not user_id:
        return {
            "saved": False,
            "action_required": True,
            "message": "请先让要添加的 QQ 账号给 Bot 发一条消息。",
            "next_action": (
                "用户会在机器人提示或日志里看到「QQ 用户标识」。"
                "把该标识回填给我后，我再调用 qq_add_user 完成添加。"
            ),
            "note": "这里需要的是用户标识（openid/id），不是 QQ 号。",
        }

    cfg = _load_cfg_for_qq_users()
    existing = _normalized_qq_user_list(cfg.get("qq_allowed_user_ids", []))
    if user_id in existing:
        return {
            "saved": True,
            "added": False,
            "message": "该用户标识已在白名单中。",
            "qq_allowed_user_ids": existing,
            "count": len(existing),
        }

    existing.append(user_id)
    cfg["qq_allowed_user_ids"] = existing
    _save_cfg_for_qq_users(cfg)
    return {
        "saved": True,
        "added": True,
        "qq_allowed_user_ids": existing,
        "count": len(existing),
        "next_steps": [
            "已加入 QQ 白名单。",
            "请重启 `sjtu-agent qq-bot` 使白名单变更生效。",
        ],
    }


def tool_qq_list_users() -> dict:
    """
    列出 QQ 白名单用户标识。
    """
    cfg = _load_cfg_for_qq_users()
    users = _normalized_qq_user_list(cfg.get("qq_allowed_user_ids", []))
    return {
        "qq_allowed_user_ids": users,
        "count": len(users),
        "allow_all": len(users) == 0,
        "tip": (
            "白名单为空时表示允许所有用户。"
            if not users
            else "仅列表内用户可使用 QQ Bot。"
        ),
    }


def tool_qq_remove_user(qq_user_id: str) -> dict:
    """
    从 QQ 白名单删除一个用户标识（openid/id）。
    """
    user_id = str(qq_user_id).strip()
    if not user_id:
        return {"saved": False, "error": "qq_user_id 不能为空。"}

    cfg = _load_cfg_for_qq_users()
    existing = _normalized_qq_user_list(cfg.get("qq_allowed_user_ids", []))
    if user_id not in existing:
        return {
            "saved": True,
            "removed": False,
            "message": "该用户标识不在白名单中。",
            "qq_allowed_user_ids": existing,
            "count": len(existing),
        }

    kept = [item for item in existing if item != user_id]
    cfg["qq_allowed_user_ids"] = kept
    _save_cfg_for_qq_users(cfg)
    return {
        "saved": True,
        "removed": True,
        "qq_allowed_user_ids": kept,
        "count": len(kept),
        "next_steps": [
            "已从 QQ 白名单移除。",
            "请重启 `sjtu-agent qq-bot` 使白名单变更生效。",
        ],
    }


def tool_fetch_url(url: str) -> dict:
    """
    抓取网页内容并提取纯文本。
    支持微信公众号、普通网页等，自动提取标题和正文。
    微信公众号优先用 Playwright 绕过反爬，失败时降级到 requests。
    """
    import re
    from bs4 import BeautifulSoup

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


def tool_execute_python(code: str, timeout: int = 60) -> dict:
    """
    在当前进程中安全地执行动态 Python 代码片段。
    stdout/stderr 捕获后作为结果返回，不会污染终端。
    """
    import subprocess as _sp
    import sys as _sys

    # 注入基础 import 路径
    preamble = (
        "import sys, os\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "from dotenv import load_dotenv\n"
        f"load_dotenv({str(ENV_PATH)!r})\n"
        "import ddl_checker as dc\n"
    )
    full_code = preamble + "\n" + code

    try:
        result = _sp.run(
            [_sys.executable, "-c", full_code],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ROOT),
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            return {
                "ok": False,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "error": stderr or f"进程退出码 {result.returncode}",
            }
        return {
            "ok": True,
            "returncode": 0,
            "stdout": stdout,
            "stderr": stderr,
        }
    except _sp.TimeoutExpired:
        return {"ok": False, "error": f"代码执行超时（{timeout}秒）"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
    path = Path(file_path)
    if not path.exists():
        # 尝试相对于脚本目录解析（防止 LLM 传入相对路径）
        path = ROOT / file_path
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
    print(f"[parse] Installing {' '.join(packages)} ...")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        print(f"[parse] Installed {' '.join(packages)}.")
        return True, ""
    err = (proc.stderr or proc.stdout or "").strip()
    if len(err) > 800:
        err = err[-800:]
    print(f"[parse] Install failed ({' '.join(packages)}): {err or 'unknown error'}")
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
    path = Path(file_path)
    if not path.exists():
        path = ROOT / file_path
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


def tool_list_canvas_assignments(course_filter: str = "") -> dict:
    """列出 Canvas 上允许文件提交（online_upload）的作业，返回含 course_id / assignment_id。"""
    import requests as _req
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

    # 获取在读课程
    resp = _req.get(
        f"{base}/api/v1/courses",
        params={"enrollment_type": "student", "enrollment_state": "active", "per_page": 50},
        headers=headers, timeout=30,
    )
    if resp.status_code != 200:
        return {
            "error": f"获取课程列表失败 ({resp.status_code})，请检查 Canvas Token 是否有效。",
            "settings_url": _canvas_settings_url(base),
            "next_action": "如 token 已失效，请重新调用 setup_canvas 按提示生成新 token。",
        }
    courses = [c for c in resp.json() if isinstance(c, dict) and c.get("name")]
    if course_filter:
        courses = [c for c in courses if course_filter in c.get("name", "")]

    result = []
    for course in courses[:15]:
        cid   = course["id"]
        cname = course.get("name", "未知课程")
        resp2 = _req.get(
            f"{base}/api/v1/courses/{cid}/assignments",
            params={"per_page": 50, "order_by": "due_at"},
            headers=headers, timeout=30,
        )
        if resp2.status_code != 200:
            continue
        for a in resp2.json():
            if not isinstance(a, dict):
                continue
            if "online_upload" not in a.get("submission_types", []):
                continue
            result.append({
                "course_id":       cid,
                "course_name":     cname,
                "assignment_id":   a["id"],
                "assignment_name": a.get("name", ""),
                "due_at":          a.get("due_at", ""),
                "points_possible": a.get("points_possible"),
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


def run_tool(name: str, args: dict) -> str:
    try:
        if name.startswith("mcp__"):
            from sjtu_agent.extensions.mcp_client import call_tool
            return call_tool(name, args or {})
        if   name == "check_setup":         r = tool_check_setup()
        elif name == "save_credentials":    r = tool_save_credentials(**args)
        elif name == "setup_canvas":        r = tool_setup_canvas(**args)
        elif name == "login_platform":      r = tool_login_platform(args["platform"])
        elif name == "get_ddls":            r = tool_get_ddls(**args)
        elif name == "get_next_lab":        r = tool_get_next_lab()
        elif name == "get_all":             r = tool_get_all(**args)
        elif name == "download_assignments":r = tool_download_assignments(**args)
        elif name == "list_assignment_files": r = tool_list_assignment_files(**args)
        elif name == "read_assignment_file":  r = tool_read_assignment_file(**args)
        elif name == "parse_local_file":      r = tool_parse_local_file(**args)
        elif name == "parse_local_files":     r = tool_parse_local_files(**args)
        elif name == "install_parse_backend": r = tool_install_parse_backend(**args)
        elif name == "search_campus":         r = tool_search_campus(**args)
        elif name == "read_shuiyuan_topic":   r = tool_read_shuiyuan_topic(**args)
        elif name == "get_schedule":          r = tool_get_schedule(**args)
        elif name == "setup_shuiyuan":        r = tool_setup_shuiyuan()
        elif name == "add_mcp_server":        r = tool_add_mcp_server(**args)
        elif name == "add_skill":             r = tool_add_skill(**args)
        elif name == "create_skill":          r = tool_create_skill(**args)
        elif name == "list_skills":           r = tool_list_skills(**args)
        elif name == "manage_skill":          r = tool_manage_skill(**args)
        elif name == "setup_course_community": r = tool_setup_course_community(**args)
        elif name == "search_courses":        r = tool_search_courses(**args)
        elif name == "get_course_detail":     r = tool_get_course_detail(**args)
        elif name == "browse_mysjtu":         r = tool_browse_mysjtu(**args)
        elif name == "refresh_mysjtu_catalog": r = tool_refresh_mysjtu_catalog()
        elif name == "query_grades":            r = tool_query_grades(**args)
        elif name == "add_reminder":            r = tool_add_reminder(**args)
        elif name == "list_reminders":          r = tool_list_reminders()
        elif name == "remove_reminder":         r = tool_remove_reminder(**args)
        elif name == "list_canvas_assignments":  r = tool_list_canvas_assignments(**args)
        elif name == "submit_canvas_assignment": r = tool_submit_canvas_assignment(**args)
        elif name == "read_emails":              r = tool_read_emails(**args)
        elif name == "search_emails":            r = tool_search_emails(**args)
        elif name == "send_email":               r = tool_send_email(**args)
        elif name == "fetch_url":                r = tool_fetch_url(**args)
        elif name == "execute_python":           r = tool_execute_python(**args)
        elif name == "update_user_profile":      r = tool_update_user_profile(**args)
        elif name == "get_user_profile":         r = tool_get_user_profile()
        elif name == "setup_telegram":           r = tool_setup_telegram(**args)
        elif name == "setup_wechat":             r = tool_setup_wechat()
        elif name == "setup_feishu":             r = tool_setup_feishu(**args)
        elif name == "setup_qq":                 r = tool_setup_qq(**args)
        elif name == "qq_add_user":              r = tool_qq_add_user(**args)
        elif name == "qq_list_users":            r = tool_qq_list_users()
        elif name == "qq_remove_user":           r = tool_qq_remove_user(**args)
        else:                               r = {"error": f"未知工具: {name}"}
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


