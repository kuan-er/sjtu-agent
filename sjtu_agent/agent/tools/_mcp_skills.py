"""MCP server and prompt-only skill management tools."""

import json
import os
import re
import shutil
from pathlib import Path

from sjtu_agent.paths import (
    CONFIG_PATH,
    PACKAGE_ROOT,
    PROJECT_ROOT,
    atomic_write_json,
    read_json_safe,
)

# ── helpers (shared with _core) ──────────────────────────────────────────────

def _normalize_config_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [value]
        except Exception:
            return [value]
    return [value]


def _valid_config_id(value, field_name="id") -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    return re.sub(r"[^a-zA-Z0-9._-]", "-", s)[:64]


# ── TOOLS schema entries ─────────────────────────────────────────────────────

TOOLS_ENTRIES = [
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
]


# ── tool implementations ─────────────────────────────────────────────────────

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
        "config_path": str(CONFIG_PATH),
        "next_action": (
            "MCP tools are now registered. Tell the user: 配置已写入 "
            f"`{CONFIG_PATH}`（mcp_servers 键）。工具会在下一次工具列表刷新（约 60 秒内）"
            "自动下发，无需重启会话；但若依赖未装齐，需要把依赖安装进「运行 bot 的进程」所在 "
            "venv（不是交互式 sjtu-agent 的 venv），然后重启对应的后台服务。"
        ),
        "checklist": [
            "依赖安装进 bot 进程所在 venv，例如: cd 项目目录 && source .venv/bin/activate && pip install <包>",
            "重启后台服务: sjtu-agent daemons restart --services feishu-bot  （或其他 bot，Windows 加 --backend psmux 视部署而定）",
            "重启后若工具仍不出现，直接调用 mcp__<server_id>__status 查看失败原因",
        ],
    }


def tool_add_skill(
    name: str,
    content: str = "",
    source_file: str = "",
    enabled: bool = True,
) -> dict:
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
