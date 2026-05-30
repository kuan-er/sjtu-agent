"""sjtu_agent/homework_agent.py — Canvas 作业自动获取、分析与回传。

核心流程：
  1. 拉取 DDL → 过滤 Canvas + N 天内到期
  2. 下载作业文件到 ASSIGNMENTS_DIR / 课程 / 作业 /
  3. 读取各类文件（PDF/DOCX/HTML/MD/TXT）
  4. 调 LLM 生成：摘要 + 题目分析 + 参考答案
  5. 结果通过飞书推送回用户
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from sjtu_agent.paths import ASSIGNMENTS_DIR

import agent


def _get_feishu_config() -> dict | None:
    """读取飞书配置用于推送。"""
    from sjtu_agent.paths import CONFIG_PATH
    if not CONFIG_PATH.exists():
        return None
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if cfg.get("feishu_app_id") and cfg.get("feishu_open_id"):
            return cfg
    except Exception:
        pass
    return None


def _read_file(file_path: Path) -> str:
    """读取单个文件，返回文本内容。根据扩展名选择解析方式。"""
    ext = file_path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(file_path))
            text = ""
            for page in reader.pages[:10]:  # 最多读 10 页
                t = page.extract_text()
                if t:
                    text += t + "\n"
            return text.strip() or "[PDF 内容为空]"

        elif ext in (".docx", ".doc"):
            from docx import Document
            doc = Document(str(file_path))
            paras = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n".join(paras) or "[DOCX 内容为空]"

        elif ext in (".html", ".htm"):
            from html.parser import HTMLParser
            class _Stripper(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                def handle_data(self, d):
                    self.text.append(d)
            s = _Stripper()
            s.feed(file_path.read_text(encoding="utf-8", errors="replace"))
            return "".join(s.text).strip() or "[HTML 内容为空]"

        elif ext in (".md", ".txt", ".tex", ".py", ".json", ".yaml", ".yml"):
            return file_path.read_text(encoding="utf-8", errors="replace").strip()

        else:
            return f"[不支持的文件格式: {ext}]"
    except Exception as e:
        return f"[读取失败: {e}]"


def _latex_to_unicode(text: str) -> str:
    """简单 LaTeX → Unicode 转换。"""
    replacements = {
        r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
        r"\epsilon": "ε", r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ",
        r"\pi": "π", r"\sigma": "σ", r"\phi": "φ", r"\omega": "ω",
        r"\times": "×", r"\div": "÷", r"\pm": "±", r"\cdot": "·",
        r"\sum": "∑", r"\prod": "∏", r"\int": "∫", r"\infty": "∞",
        r"\leq": "≤", r"\geq": "≥", r"\neq": "≠", r"\approx": "≈",
        r"\sqrt": "√", r"\frac": "/", r"\partial": "∂", r"\nabla": "∇",
        r"\rightarrow": "→", r"\Rightarrow": "⇒", r"\leftarrow": "←",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def _extract_code_blocks(md_text: str) -> list[tuple[str, str]]:
    """从 Markdown 提取代码块，返回 [(language, code), ...]."""
    blocks = []
    pattern = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)
    for m in pattern.finditer(md_text):
        lang = m.group(1) or ""
        code = m.group(2).strip()
        blocks.append((lang, code))
    return blocks


def generate_solution_files(title: str, solution: str, output_dir: Path,
                            answer_mode: bool = False) -> list[str]:
    """生成 .md + 提取代码文件。PDF/HTML 由 Claude Code 自行生成。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = [str(output_dir / "_解答.md")]

    ai_notice = "> ⚠️ 本文由 AI 辅助生成，仅供学习参考\n\n"
    md_content = (ai_notice + solution) if answer_mode else solution
    (output_dir / "_解答.md").write_text(md_content, encoding="utf-8")

    code_notice = {
        "py": "# AI 辅助生成代码，供学习参考\n\n",
        "java": "// AI 辅助生成代码，供学习参考\n\n",
        "cpp": "// AI 辅助生成代码，供学习参考\n\n",
        "c": "// AI 辅助生成代码，供学习参考\n\n",
        "js": "// AI 辅助生成代码，供学习参考\n\n",
        "ts": "// AI 辅助生成代码，供学习参考\n\n",
        "go": "// AI 辅助生成代码，供学习参考\n\n",
    }

    for i, (lang, code) in enumerate(_extract_code_blocks(solution)):
        ext = lang if lang in ("py", "java", "cpp", "c", "js", "ts", "go") else "txt"
        p = output_dir / f"_code_{i+1}.{ext}"
        notice = code_notice.get(ext, "") if answer_mode else ""
        p.write_text(notice + code, encoding="utf-8")
        saved.append(str(p))
    return saved


def read_assignment_content(assignment_dir: Path) -> str:
    """读取一个作业目录下所有文件，返回合并文本。"""
    if not assignment_dir.exists():
        return f"[目录不存在: {assignment_dir}]"
    parts = []
    for f in sorted(assignment_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            content = _read_file(f)
            if content:
                parts.append(f"[{f.name}]\n{content}")
    return "\n\n".join(parts) if parts else "[无可读文件]"


def _call_llm(prompt: str, llm_client=None, model: str = "") -> str:
    """调用 LLM 并返回结果。"""
    if llm_client is None:
        agent_cfg = agent.load_agent_config()
        if not agent_cfg.get("api_key"):
            return "[LLM 未配置]"
        llm_client = agent._make_client(agent_cfg)
        model = agent_cfg.get("model", "deepseek-chat")

    try:
        if agent._is_anthropic_model(model):
            resp = llm_client.messages.create(
                model=model, max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text or "[空响应]"
        else:
            resp = llm_client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
            text = resp.choices[0].message.content or ""
            think_re = re.compile(r"<think>.*?</think>", re.DOTALL)
            return think_re.sub("", text).strip() or "[空响应]"
    except Exception as e:
        return f"[分析失败: {e}]"


def solve_homework(course: str, assignment_name: str, content: str,
                   brief: bool = False) -> str:
    """让 LLM 实际解题并返回完整解答。brief=True 仅返回摘要。"""
    content = _latex_to_unicode(content)

    if brief:
        prompt = f"""课程：{course}，作业：{assignment_name}
{content[:6000]}

请用 1-2 句话概括这份作业的要求。"""
    else:
        prompt = f"""你是一位学霸，请完整解答以下作业。要能被直接提交。

课程：{course}
作业名称：{assignment_name}

{content[:8000]}

要求：
- 逐题解答，标清题号
- 编程题给出完整可运行代码（含必要的 import 和注释）
- 数学题给出分步推导和最终答案
- **所有数学公式必须用 $$...$$ 包裹**（包括行内公式如 $$\alpha=1$$）
- 论述题给出结构化论点
- 如题目信息不完整请标注推断依据
- 用中文回答"""
    return _call_llm(prompt)


# 向后兼容
analyze_homework = solve_homework


def _fetch_pending(include_past: bool = False) -> list[dict]:
    """获取 Canvas 作业。include_past=True 时包含已过期的历史作业。"""
    import ddl_checker as dc
    cfg = dc.load_config()
    ddls = dc.fetch_canvas(cfg, include_past=include_past)
    if include_past:
        # 只返回已过期但已提交的作业（历史作业）
        from datetime import datetime, timezone, timedelta
        CST = timezone(timedelta(hours=8))
        now = datetime.now(CST)
        past = [d for d in ddls if d.get("due") and hasattr(d["due"], "timestamp") and d["due"] < now]
        print(f"[homework] Canvas 共 {len(ddls)} 个作业，{len(past)} 个历史")
        return past
    pending = [d for d in ddls if not d.get("submitted")]
    print(f"[homework] Canvas 共 {len(ddls)} 个作业，{len(pending)} 个未提交")
    return pending


def _filter_by_due(pending: list[dict], due_within_days: int) -> list[dict]:
    """按截止天数过滤。due_within_days=0 表示不限制。"""
    if due_within_days <= 0:
        return pending
    import ddl_checker as dc
    from datetime import timedelta
    now_time = dc.NOW
    window = timedelta(days=due_within_days)
    filtered = []
    for d in pending:
        due = d.get("due")
        if due and hasattr(due, 'timestamp'):
            remaining = due - now_time
            if remaining <= window and remaining.total_seconds() > 0:
                filtered.append(d)
    return filtered


import shutil

# Claude Code CLI 路径（常驻进程 PATH 可能不包含 npm global 目录）
_CLAUDE_CANDIDATES = [
    shutil.which("claude"),
    shutil.which("claude.cmd"),
    r"D:\develop\node_global\claude.cmd",
    r"D:\develop\node_global\claude",
]
_CLAUDE_BIN = next((p for p in _CLAUDE_CANDIDATES if p and Path(p).exists()), "")


def _claude_code_solve(hw_dir: Path, course: str, aname: str, content: str,
                        brief: bool = False, answer_mode: bool = False) -> str:
    """使用本地 Claude Code CLI 解题。answer_mode=True 输出完整答案。"""
    if not _CLAUDE_BIN or not Path(_CLAUDE_BIN).exists():
        print("[homework] Claude Code 不可用，回退到 API 调用")
        return solve_homework(course, aname, content, brief=brief)

    import subprocess

    # 读取用户信息作为上下文
    user_ctx = ""
    try:
        from sjtu_agent.paths import CONFIG_PATH, ENV_PATH
        cfg_data = {}
        if CONFIG_PATH.exists():
            cfg_data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        jaccount = cfg_data.get("jaccount_username", "") or ""
        if jaccount:
            user_ctx = f"\n用户信息：jAccount 用户名 {jaccount}"
    except Exception:
        pass

    prompt = f"""在当前目录完成{aname}（{course}）。{user_ctx}

铁律（违反即失败）：
1. 禁止任何形式的提问/确认/等待——必须直接完成
2. 先读完 description.html 和所有附件，再动笔
3. 按以下规则选择输出格式（照做，不推断）：
   - 含"代码/Python/C/Java/编程" → 必须生成 .py/.c/.java 文件 + README.md
   - 含"PPT/展示/汇报" → _解答.md 内输出每页标题+要点大纲
   - 物理/数学/信号/电路 → **所有公式必须用 $$...$$ 包裹**（行内公式也 $$）
   - 含"论文/报告" → 结构：摘要/引言/方法/结果/结论
   - 其他 → _解答.md + _解答.html
4. 逐题解答写入 _解答.md
5. 编写 _解答.tex 并运行 xelatex 编译成 _解答.pdf：使用 \\usepackage[hmargin=2.5cm,vmargin=2.5cm]{{geometry}} 和 \\pagestyle{{plain}} 确保页号统一在底部居中
6. **所有数学表达式（2^n、x_i、α+β、log n 等）必须用 $...$（行内）或 $$...$$（独立行）包裹，严禁在正文中裸写数学符号。**
7. 输出 "SUMMARY:" 开头的 200 字摘要"""

    # MATLAB 图表生成（若本机安装了 MATLAB）
    try:
        from sjtu_agent.matlab_util import matlab_available, MATLAB_BIN
        if matlab_available():
            prompt += f"""
8. 如需图表/数据可视化，**优先用 MATLAB 而非 Matplotlib**：
   a) 编写 _figures.m，每张图用 figure(); plot/fplot/surf(...);
      exportgraphics(gcf,'figN.pdf','ContentType','vector'); 保存
   b) 运行 `{MATLAB_BIN}` -batch "run('_figures.m'); exit"
   c) 在 _解答.tex 中用 \\includegraphics{{figN.pdf}} 嵌入，
      xelatex 编译后矢量图无损整合到 PDF 中
   **MATLAB 矢量 PDF 可无损嵌入 LaTeX，远优于 PNG 截图**"""
    except Exception:
        pass

    if brief:
        prompt += "\n注意：只要摘要，不要完整解答。"
    elif answer_mode:
        prompt += (
            "\n**完整解答模式**：逐题给出详细推导/代码/结果。"
            "\n在 _解答.md 第一行写入：> ⚠️ 本文由 AI 辅助生成，仅供学习参考"
            "\n在 _解答.tex 中加入：\\fancyfoot[C]{AI 辅助生成 · 学习参考}"
            "\n代码文件中头部注释：# AI 辅助生成代码，供学习参考"
        )
    else:
        prompt += (
            "\n**解题助手模式（重要）**："
            "\n- 只输出：题目考点分析 + 解题思路框架 + 关键公式/方法提示"
            "\n- **不要给完整答案、不写最终结果、不写完整代码**"
            "\n- 每题末尾留白，暗示用户自己尝试"
            "\n- 最后一行写：「📝 思考后再核对？回复『给我答案』获取完整解答」"
        )

    try:
        # Windows subprocess 会截断多行参数，改用 stdin 传 prompt
        result = subprocess.run(
            [_CLAUDE_BIN, "-p", "--add-dir", str(hw_dir), "--permission-mode", "bypassPermissions"],
            cwd=str(hw_dir), input=prompt,
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
        )
        output = result.stdout.strip()
        if result.returncode != 0 and not output:
            print(f"[homework] Claude Code 失败 ({result.returncode}), 回退 API")
            print(f"  stderr: {result.stderr[:200]}")
            return solve_homework(course, aname, content, brief=brief)

        # 提取 SUMMARY 作为飞书回复
        summary_marker = "SUMMARY:"
        if summary_marker in output:
            idx = output.index(summary_marker)
            summary = output[idx + len(summary_marker):].strip()[:2000]
            return summary + f"\n\n完整解答已保存到 {hw_dir}"
        return output[-2000:] + f"\n\n完整解答已保存到 {hw_dir}"
    except subprocess.TimeoutExpired:
        print("[homework] Claude Code 超时，回退 API")
        return solve_homework(course, aname, content, brief=brief)
    except Exception as e:
        print(f"[homework] Claude Code 异常: {e}, 回退 API")
        return solve_homework(course, aname, content, brief=brief)


def _download_and_analyze_one(d: dict, idx: int, brief: bool = False,
                              answer_mode: bool = False) -> str:
    """下载并解答单个作业。brief=True 仅返回摘要。"""
    course = d.get("course", "未知课程")
    aname = d.get("name", "未知作业")
    due = d.get("due")
    due_str = due.strftime("%m月%d日 %H:%M") if due and hasattr(due, 'strftime') else str(due or "?")
    from datetime import datetime, timezone, timedelta
    CST = timezone(timedelta(hours=8))
    days_left = (due - datetime.now(CST)).days if due else "?"
    remaining = f"{days_left} 天" if isinstance(days_left, int) else "?"

    safe_course = re.sub(r'[\\/*?:"<>|]', '_', course)
    safe_name = re.sub(r'[\\/*?:"<>|]', '_', aname)
    hw_dir = ASSIGNMENTS_DIR / safe_course / safe_name

    # 下载
    try:
        import ddl_checker as dc
        cfg = dc.load_config()
        dc.download_assignments(
            cfg,  # 必需的第一个参数
            course_filter=course, assignment_filter=aname,
            output_dir=str(ASSIGNMENTS_DIR), due_within_days=3650,
            include_past=True,  # 允许下载历史作业
        )
    except Exception as e:
        print(f"[homework] 下载失败 {course}/{aname}: {e}")

    content = read_assignment_content(hw_dir)
    if "[无可读文件]" in content:
        return (
            f"[{idx}] {course} — {aname}\n"
            f"截止：{due_str}（{remaining}）\n"
            f"{content}"
        )

    # 清理前次运行产生的旧输出文件，防止 Claude Code 误读
    for old in hw_dir.glob("_解答.*"):
        try: old.unlink()
        except Exception: pass
    for old in hw_dir.glob("_code_*"):
        try: old.unlink()
        except Exception: pass
    # 清理 MATLAB / LaTeX 前次运行残留
    for pattern in ["fig*.pdf", "fig*.png", "fig*.eps", "_figures.m",
                     "*.aux", "*.log", "*.out", "*.toc"]:
        for old in hw_dir.glob(pattern):
            try: old.unlink()
            except Exception: pass

    print(f"[homework] 解题: {course} - {aname}")
    feishu_reply = _claude_code_solve(hw_dir, course, aname, content, brief=brief, answer_mode=answer_mode)

    # 生成解答文件（从 Claude Code 写入的 _解答.md 读取完整内容）
    solution_path = hw_dir / "_解答.md"
    full_solution = solution_path.read_text(encoding="utf-8") if solution_path.exists() else feishu_reply
    title = f"{course} — {aname}"
    try:
        files = generate_solution_files(title, full_solution, hw_dir, answer_mode=answer_mode)
        print(f"[homework] 已生成 {len(files)} 个文件: {files}")
    except Exception as e:
        print(f"[homework] 文件生成失败: {e}")

    # 收集下载文件信息
    file_info = ""
    try:
        files_found = [f for f in hw_dir.iterdir() if f.is_file() and not f.name.startswith(".")
                       and not f.name.startswith("_解答") and not f.name.startswith("_code_")]
        if files_found:
            exts = set(f.suffix for f in files_found if f.suffix)
            file_info = f"\n📎 题目文件：{len(files_found)} 个（{', '.join(sorted(exts))}）"
    except Exception:
        pass

    # 飞书回复
    notice = "\n\n📝 AI 辅助生成，供学习参考" if answer_mode else ""
    return (
        f"[{idx}] {course} — {aname}\n"
        f"截止：{due_str}（{remaining}）\n\n"
        f"{feishu_reply}{file_info}{notice}"
    )


def _format_list(pending: list[dict], past: bool = False) -> str:
    """格式化作业列表。past=True 时提示用户使用 /hw past do 而非 /hw do。"""
    if not pending:
        return "[homework] 暂无 Canvas 作业"
    past_label = "（已提交）" if past else ""
    lines = [f"共 {len(pending)} 个作业{past_label}："]
    from datetime import datetime, timezone, timedelta
    CST = timezone(timedelta(hours=8))
    for i, d in enumerate(pending):
        course = d.get("course", "未知课程")
        aname = d.get("name", "未知作业")
        due = d.get("due")
        due_str = due.strftime("%m/%d") if due and hasattr(due, 'strftime') else str(due or "?")
        days = (due - datetime.now(CST)).days if due else "?"
        lines.append(f"  [{i+1}] {course} — {aname}（{due_str}，{days} 天）")
    if past:
        lines.append("\n/hw past do <序号> 下载分析")
    else:
        lines.append("\n/hw do <序号> 下载分析")
    return "\n".join(lines)


def run_homework_check(due_within_days: int = 0, specific_idx: int | None = None,
                       list_only: bool = False, brief: bool = False,
                       include_past: bool = False, answer_mode: bool = False) -> str:
    """主入口：列出或分析 Canvas 作业。answer_mode=True 输出完整答案。"""
    pending = _fetch_pending(include_past=include_past)
    if due_within_days > 0:
        pending = _filter_by_due(pending, due_within_days)
        print(f"[homework] 过滤后 {len(pending)} 个 {due_within_days} 天内到期")

    if not pending:
        label = f"{due_within_days} 天内" if due_within_days > 0 else ""
        return f"[homework] 暂无{label}未提交的 Canvas 作业"

    # 仅列出
    if list_only:
        return _format_list(pending, past=include_past)

    # 分析指定作业（用户输入 1-based 序号，转为 0-based）
    if specific_idx is not None:
        idx = specific_idx - 1
        if 0 <= idx < len(pending):
            return _download_and_analyze_one(pending[idx], idx, brief=brief, answer_mode=answer_mode)
        return f"[homework] 无效序号：{specific_idx}，共 {len(pending)} 个（1~{len(pending)}）"

    # 默认：列出
    return _format_list(pending)


def run_homework_check_and_push(due_within_days: int = 3,
                                 specific_idx: int | None = None) -> None:
    """运行作业检查并通过飞书推送结果。"""
    result = run_homework_check(due_within_days, specific_idx)
    cfg = _get_feishu_config()
    if not cfg:
        print("[homework] 飞书未配置，仅打印：\n" + result)
        return

    import requests
    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": cfg["feishu_app_id"], "app_secret": cfg["feishu_app_secret"]},
            timeout=10,
        )
        if r.status_code != 200 or r.json().get("code") != 0:
            print(f"[homework] 飞书 token 获取失败")
            return
        token = r.json()["tenant_access_token"]

        chunks = [result[i:i + 3800] for i in range(0, len(result), 3800)]
        for chunk in chunks:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": cfg["feishu_open_id"],
                    "msg_type": "text",
                    "content": json.dumps({"text": chunk}, ensure_ascii=False),
                },
                timeout=15,
            )
            if resp.status_code != 200 or resp.json().get("code") != 0:
                print(f"[homework] 推送失败: {resp.text[:100]}")
                return
        print("[homework] 飞书推送完成")
    except Exception as e:
        print(f"[homework] 推送异常: {e}")
