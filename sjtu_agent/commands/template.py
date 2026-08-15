"""
sjtu_agent/commands/template.py — /template 命令执行（飞书 / WebUI 共享）。
"""

from __future__ import annotations

from pathlib import Path


def cmd_template(user_id: str, parts: list[str]) -> str:
    del user_id
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
