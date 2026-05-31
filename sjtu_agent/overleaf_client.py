"""SJTU Overleaf (latex.sjtu.edu.cn) 客户端 — Git Bridge + 模板管理。

通过 Overleaf Git Bridge 克隆模板项目，本地套用后编译。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from sjtu_agent.paths import DATA_DIR

_OVERLEAF_BASE = "https://latex.sjtu.edu.cn"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "sjtu_templates"
_USER_TEMPLATES_DIR = DATA_DIR / "sjtu_templates"


def list_local_templates() -> list[dict]:
    """列出本地可用的模板。内置模板 + 用户下载的模板。"""
    templates = []
    for base in (_TEMPLATES_DIR, _USER_TEMPLATES_DIR):
        if not base.exists():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                readme = d / "README.md"
                desc = ""
                if readme.exists():
                    desc = readme.read_text(encoding="utf-8").strip().split("\n")[0]
                templates.append({
                    "name": d.name,
                    "path": str(d),
                    "description": desc or "(无描述)",
                    "source": "builtin" if str(base) == str(_TEMPLATES_DIR) else "user",
                })
    return templates


def _ensure_templates_dir() -> Path:
    _USER_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    return _USER_TEMPLATES_DIR


def clone_template_from_overleaf(project_id: str, template_name: str = "") -> str | None:
    """通过 Git Bridge 克隆 Overleaf 项目到本地模板目录。返回模板路径。"""
    git = shutil.which("git")
    if not git:
        return None

    name = template_name or f"overleaf-{project_id}"
    target = _USER_TEMPLATES_DIR / name
    if target.exists():
        return str(target)

    url = f"{_OVERLEAF_BASE}/git/{project_id}"
    try:
        subprocess.run(
            [git, "clone", "--depth", "1", url, str(target)],
            capture_output=True, text=True, timeout=60,
            check=True,
        )
        return str(target)
    except subprocess.CalledProcessError:
        return None


def _find_xelatex() -> str | None:
    """查找本机 xelatex。"""
    candidates = [shutil.which("xelatex"), shutil.which("xelatex.exe")]
    if os.name == "nt":
        for d in [r"C:\Program Files\MiKTeX\miktex\bin\x64\xelatex.exe",
                  r"C:\Program Files (x86)\MiKTeX\miktex\bin\x64\xelatex.exe"]:
            if Path(d).exists():
                candidates.insert(0, d)
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def compile_latex(tex_file: Path, work_dir: Path | None = None) -> tuple[bool, str]:
    """运行 xelatex 编译 .tex 文件。返回 (success, output)。"""
    xelatex = _find_xelatex()
    if not xelatex:
        return False, "[xelatex] 未找到 xelatex，请安装 MiKTeX"

    cwd = work_dir or tex_file.parent
    try:
        result = subprocess.run(
            [xelatex, "-interaction=nonstopmode", tex_file.name],
            cwd=str(cwd), capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace",
        )
        # xelatex 需要跑两次以生成目录和交叉引用
        subprocess.run(
            [xelatex, "-interaction=nonstopmode", tex_file.name],
            cwd=str(cwd), capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        # 提取错误行
        errors = [l for l in result.stdout.split("\n") if l.startswith("!")]
        return False, "\n".join(errors[:5]) or result.stdout[-500:]
    except subprocess.TimeoutExpired:
        return False, "[xelatex] 编译超时"
    except Exception as e:
        return False, f"[xelatex] 异常: {e}"
