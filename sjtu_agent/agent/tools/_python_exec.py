"""Dynamic Python execution tool."""

import re as _re
import subprocess as _sp
import sys as _sys

from sjtu_agent.paths import PROJECT_ROOT, ENV_PATH

# ── 危险操作静态守卫（确定性约束 > 概率性遵守，Harness）──────────────────────
# execute_python 在受信任本地环境跑任意代码；这里在子进程启动前做一层静态拦截，
# 挡住常见的破坏性操作（删关键文件 / 覆盖配置 / 破坏 git / 任意 shell）。
# 注意：这是护栏不是沙箱——纵深防御的一层，配合敏感 env 剥离使用。

_DANGEROUS_PATTERNS: list[tuple[_re.Pattern, str]] = [
    (_re.compile(r"git.{0,30}(reset.{0,20}--hard|clean\s+-[fd]|push.{0,20}(--force|\s-f\b)|checkout.{0,10}--|rebase\b)", _re.IGNORECASE),
     "破坏性 git 操作（reset --hard / clean / push --force / checkout -- / rebase）"),
    (_re.compile(r"os\.system\s*\(", _re.IGNORECASE), "os.system 任意 shell 执行"),
    (_re.compile(r"shell\s*=\s*True", _re.IGNORECASE), "shell=True 执行任意命令"),
    (_re.compile(r"shutil\.rmtree\s*\(", _re.IGNORECASE), "递归删除目录（shutil.rmtree）"),
    (_re.compile(r"os\.(remove|unlink)\s*\([^)]*(config\.json|agent_config|\.env|user_profile|\.git)"),
     "删除关键文件（config.json / .env / agent_config / user_profile / .git）"),
    (_re.compile(r"\.unlink\s*\([^)]*(config\.json|agent_config|\.env|user_profile|\.git)"),
     "删除关键文件（config.json / .env / agent_config / user_profile / .git）"),
]

_CONFIG_MARKERS = ("CONFIG_PATH", "config.json", "agent_config", ".env", "user_profile")
_WRITE_OPS = ("write_text", "write_bytes", "os.remove", "os.unlink", ".unlink(", ".rmdir(", "shutil.rmtree")


def _guard_code(code: str) -> str | None:
    """静态扫描代码，命中危险模式返回错误信息，否则返回 None。"""
    for pattern, reason in _DANGEROUS_PATTERNS:
        if pattern.search(code):
            return f"危险操作被拦截：{reason}。如需执行请改用更安全的方式，或告诉用户手动处理。"
    if any(m in code for m in _CONFIG_MARKERS) and any(w in code for w in _WRITE_OPS):
        return "危险操作被拦截：检测到对关键配置文件（config.json / .env / agent_config / user_profile）的写或删除操作。凭据/配置不应被脚本覆盖。"
    return None


TOOLS_ENTRIES = [
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
]


def tool_execute_python(code: str, timeout: int = 60) -> dict:
    # 危险操作静态拦截（Harness）：先于子进程执行
    blocked = _guard_code(code)
    if blocked:
        return {"ok": False, "error": blocked, "blocked": True}

    preamble = (
        "import sys, os\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from pathlib import Path\n"
        "from dotenv import load_dotenv\n"
        f"load_dotenv({str(ENV_PATH)!r})\n"
        "import ddl_checker as dc\n"
    )
    full_code = preamble + "\n" + code

    # 剥离敏感环境变量，防止代码执行泄露凭据
    import os as _os
    _sensitive_keys = {
        "JACCOUNT_PASSWORD", "MOOC_PASSWORD", "ZHIYUAN_API_KEY",
        "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
        "EMAIL_PASSWORD",
    }
    env = {k: v for k, v in _os.environ.items() if k not in _sensitive_keys}

    try:
        result = _sp.run(
            [_sys.executable, "-c", full_code],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
            env=env,
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
