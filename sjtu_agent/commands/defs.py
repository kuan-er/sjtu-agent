"""
sjtu_agent/commands/defs.py — 跨入口命令定义

飞书 / WebUI / CLI 共用同一份斜杠命令元数据。WebUI 通过
/api/commands/resolve 把命令转换为自然语言提示后交给现有 Agent 工具链；
命令执行层在 dispatch.py 和各领域模块中共享。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDef:
    name: str
    label: str
    icon: str
    description: str
    prompt: str
    examples: tuple[str, ...] = ()
    chip: bool = True  # 是否展示在 WebUI 输入框上方的快捷 chips 里


COMMANDS: tuple[CommandDef, ...] = (
    CommandDef(
        name="/hw",
        label="作业",
        icon="📝",
        description="列出 / 管理作业，生成作业解答",
        prompt="列出我的作业",
        examples=(
            "/hw",
            "/hw do 3",
            "/hw brief 3",
            "/hw due 7",
            "/hw past",
            "/hw all",
        ),
    ),
    CommandDef(
        name="/news",
        label="校园新闻",
        icon="📰",
        description="生成校园新闻摘要",
        prompt="今天有什么校园新闻",
        examples=("/news",),
    ),
    CommandDef(
        name="/news_block",
        label="屏蔽新闻",
        icon="🚫",
        description="屏蔽某类校园新闻",
        prompt="我想屏蔽一类校园新闻，请先告诉我有哪些分类可选",
        examples=("/news_block 教务处", "/news_block 水源社区"),
        chip=False,
    ),
    CommandDef(
        name="/news_reset",
        label="重置新闻",
        icon="♻️",
        description="重置新闻偏好画像",
        prompt="重置我的新闻偏好",
        examples=("/news_reset",),
        chip=False,
    ),
    CommandDef(
        name="/eat",
        label="食堂推荐",
        icon="🍜",
        description="按实时拥挤度和历史偏好推荐食堂",
        prompt="推荐一下现在去哪吃",
        examples=("/eat", "/eat 徐汇", "/eat 张江"),
    ),
    CommandDef(
        name="/template",
        label="LaTeX 模板",
        icon="📚",
        description="套用 / 编译 / 克隆 SJTU LaTeX 模板",
        prompt="列出可用的 LaTeX 模板",
        examples=(
            "/template",
            "/template bachelor-thesis",
            "/template compile",
            "/template clone <id>",
            "/template push",
        ),
    ),
    CommandDef(
        name="/ddl",
        label="DDL",
        icon="🧪",
        description="查看 DDL 聚合",
        prompt="查看我的 DDL",
        examples=("/ddl",),
    ),
    CommandDef(
        name="/help",
        label="帮助",
        icon="ℹ️",
        description="查看可用功能",
        prompt="你能做什么？请介绍一下可用功能",
        examples=("/help",),
        chip=False,
    ),
)

_EAT_CAMPUSES = ("闵行", "徐汇", "张江")


def _split_command(text: str) -> tuple[str, str]:
    """Return (command_name, args); command_name is '' for plain text."""
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return "", raw
    parts = raw.split(maxsplit=1)
    name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return name, args


def _split_sub(args: str) -> tuple[str, str]:
    """Split ``args`` into sub-command and the rest of the arguments."""
    parts = args.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""
    return sub, rest


def command_prompt(text: str) -> str:
    """把斜杠命令转换为 WebUI 可直接发送的自然语言提示。

    映射语义与 scripts/feishu_bot.py 的 _COMMAND_REGISTRY 保持一致；
    无法识别的内容原样返回，交给 Agent 自行处理。
    """
    raw = (text or "").strip()
    name, args = _split_command(raw)
    if not name:
        return raw

    if name == "/hw":
        sub, rest = _split_sub(args)
        if sub == "do":
            if rest.isdigit():
                return f"帮我分析第 {int(rest)} 个作业并生成解答"
            if rest:
                return f"帮我分析作业并生成解答（作业序号是 {rest}，如果不是有效序号请提醒我）"
            return "帮我分析作业并生成解答（我还没指定作业序号）"
        if sub == "brief":
            if rest.isdigit():
                return f"帮我生成第 {int(rest)} 个作业的摘要"
            if rest:
                return f"帮我生成作业摘要（作业序号是 {rest}，如果不是有效序号请提醒我）"
            return "帮我生成作业摘要（我还没指定作业序号）"
        if sub == "due":
            days = int(rest) if rest.isdigit() else 3
            return f"列出 {days} 天内截止的作业"
        if sub == "past":
            past_sub, past_rest = _split_sub(rest)
            if past_sub == "do" and past_rest.isdigit():
                return f"帮我分析历史作业中第 {int(past_rest)} 个并生成解答"
            return "列出历史作业（包括已交的）"
        if sub == "all":
            return "列出所有作业（包括历史作业）"
        if sub == "answer":
            return "给我刚才分析的那份作业的完整解答"
        if sub in ("", "list"):
            return "列出我的作业"
        # 与 Feishu 一致：未知 /hw 子命令回退为列作业
        return "列出我的作业"

    if name == "/news":
        return "今天有什么校园新闻"

    if name == "/news_block":
        category = args.strip()
        if not category:
            return "我想屏蔽一类校园新闻，请先告诉我有哪些分类可选"
        return f"屏蔽「{category}」类校园新闻"

    if name == "/news_reset":
        return "重置我的新闻偏好"

    if name == "/eat":
        campus = args.strip()
        if not campus:
            campus = "闵行"
        if campus in _EAT_CAMPUSES:
            return f"推荐一下现在{campus}校区去哪吃"
        return (
            f"推荐一下现在去哪吃（注意：{campus} 不是有效校区，"
            "有效校区是闵行、徐汇、张江）"
        )

    if name == "/template":
        action, rest = _split_sub(args)
        if action == "compile":
            return "编译当前 LaTeX 模板生成 PDF"
        if action == "clone":
            clone_args = rest.split()
            project_id = clone_args[0].strip() if clone_args else ""
            template_name = clone_args[1].strip() if len(clone_args) > 1 else ""
            if not project_id:
                return "我想从 Overleaf 克隆一个 LaTeX 项目，请先告诉我 project-id"
            if template_name:
                return (
                    f"从 Overleaf 克隆项目 {project_id}，命名为 {template_name}，"
                    "作为 LaTeX 模板"
                )
            return f"从 Overleaf 克隆项目 {project_id} 作为 LaTeX 模板"
        if action == "push":
            return "把当前论文目录推送到 Overleaf"
        if not args:
            return "列出可用的 LaTeX 模板"
        return f"套用 LaTeX 模板：{args}"

    if name == "/ddl":
        return "查看我的 DDL"

    if name == "/help":
        return "你能做什么？请介绍一下可用功能"

    return raw


def command_defs() -> list[dict]:
    """导出给 /api/commands 的 JSON-safe 命令元数据。"""
    from .dispatch import CORE_COMMAND_REGISTRY
    return [
        {
            "name": c.name,
            "label": c.label,
            "icon": c.icon,
            "description": c.description,
            "prompt": c.prompt,
            "examples": list(c.examples),
            "chip": c.chip,
            "exec": c.name in CORE_COMMAND_REGISTRY,
        }
        for c in COMMANDS
    ]
