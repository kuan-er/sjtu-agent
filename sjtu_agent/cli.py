from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path

from sjtu_agent import __version__
from sjtu_agent.paths import describe_runtime_paths
from sjtu_agent.scheduler import available_service_names, current_platform_name, install_daemons
from sjtu_agent.setup_wizard import register_setup_parser
from sjtu_agent.terminal_ui import print_json


def _run_module(module_name: str, script_args: list[str] | None = None) -> int:
    old_argv = sys.argv[:]
    sys.argv = [module_name, *(script_args or [])]
    try:
        runpy.run_module(module_name, run_name="__main__")
        return 0
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 0
    finally:
        sys.argv = old_argv


def _cmd_doctor(_: argparse.Namespace) -> int:
    import agent

    payload = {
        "version": __version__,
        "paths": describe_runtime_paths(),
        "setup": agent.tool_check_setup(),
    }
    print_json(payload)
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    """拉取最新代码并重装包，可选更新 Playwright Chromium。"""
    import subprocess
    import shutil
    from sjtu_agent.paths import PROJECT_ROOT

    pip = Path(sys.executable).parent / "pip"
    if not pip.exists():
        pip = Path(sys.executable).parent / "pip3"

    print(f"sjtu-agent 更新工具")
    print(f"  当前版本：{__version__}")
    print(f"  项目目录：{PROJECT_ROOT}")

    # ── 0. 前置检查 ────────────────────────────────────────────────────────
    git = shutil.which("git")
    if not git:
        print("[!] 未找到 git，无法更新代码。请安装 Git 后重试。")
        return 1

    is_git_repo = False
    try:
        r = subprocess.run(
            [git, "rev-parse", "--is-inside-work-tree"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=5,
        )
        is_git_repo = r.returncode == 0
    except Exception:
        pass

    if not is_git_repo:
        print(f"[!] {PROJECT_ROOT} 不是 Git 仓库，无法自动更新。")
        print("   请确认项目是通过 git clone 安装的。")
        return 1

    # ── 1. 显示待更新内容 ──────────────────────────────────────────────────
    if not args.skip_git:
        # 获取当前 HEAD 和远端 HEAD 的差异
        local_hash = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=5,
        ).stdout.decode().strip()

        # 尝试获取远端最新
        print("\n正在检查远端更新…")
        fetch_result = subprocess.run(
            [git, "fetch", "--quiet", "--no-tags", "origin"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=30,
        )
        if fetch_result.returncode != 0:
            print("[!] 无法连接到远端仓库，请检查网络。")
            if not args.upgrade_deps and not args.update_playwright:
                return 1
        else:
            # 确定远端分支
            remote_ref = ""
            for ref in ["@{u}", "origin/main", "origin/master"]:
                r = subprocess.run(
                    [git, "rev-parse", ref],
                    cwd=str(PROJECT_ROOT), capture_output=True, timeout=5,
                )
                if r.returncode == 0:
                    remote_ref = ref
                    break

            if remote_ref:
                remote_hash = subprocess.run(
                    [git, "rev-parse", remote_ref],
                    cwd=str(PROJECT_ROOT), capture_output=True, timeout=5,
                ).stdout.decode().strip()

                if local_hash == remote_hash:
                    print("[OK] 已是最新版本，无需更新。")
                    if not args.upgrade_deps and not args.update_playwright:
                        return 0
                else:
                    # 显示最近几个 commit
                    behind = subprocess.run(
                        [git, "rev-list", "--count", f"{local_hash}..{remote_hash}"],
                        cwd=str(PROJECT_ROOT), capture_output=True, timeout=5,
                    ).stdout.decode().strip()
                    print(f"\n发现 {behind} 个新提交：")
                    log_result = subprocess.run(
                        [git, "log", "--oneline", f"{local_hash}..{remote_hash}", "-10"],
                        cwd=str(PROJECT_ROOT), capture_output=True, timeout=5,
                    )
                    if log_result.returncode == 0:
                        for line in log_result.stdout.decode().strip().split("\n"):
                            if line:
                                print(f"  • {line}")

    # ── 2. git pull ────────────────────────────────────────────────────────
    if not args.skip_git:
        print("\n正在拉取最新代码…")
        # 先尝试 fast-forward（最简单，无冲突）
        result = subprocess.run(
            [git, "pull", "--ff-only"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("[OK] 代码已更新。")
        else:
            # fast-forward 失败 → 可能是本地有提交导致分叉，尝试 rebase
            print("  (fast-forward 不可用，尝试 rebase…)")
            rebase_result = subprocess.run(
                [git, "pull", "--rebase"],
                cwd=str(PROJECT_ROOT),
                capture_output=False,
            )
            if rebase_result.returncode == 0:
                print("[OK] 代码已更新（rebase 方式）。")
            else:
                print("[!] 自动更新失败，请手动处理：")
                print(f"    cd {PROJECT_ROOT}")
                print("    git status    # 查看当前状态")
                print("    git log --oneline -5   # 查看本地提交")
                print("    如有未推送的本地提交，可尝试: git pull --rebase")
                print("    或放弃本地修改: git reset --hard origin/main")
                return 1

    # ── 3. pip install -e . ────────────────────────────────────────────────
    print("\n正在重新安装…")
    pip_cmd = [sys.executable, "-m", "pip", "install", "-e", str(PROJECT_ROOT), "--quiet"]
    if args.upgrade_deps:
        pip_cmd.append("--upgrade")
    result = subprocess.run(pip_cmd)
    if result.returncode != 0:
        print("[!] 依赖安装失败，请手动运行: pip install -e " + str(PROJECT_ROOT))
        return 1

    # ── 4. 可选：更新 Playwright Chromium ─────────────────────────────────
    if args.update_playwright:
        print("正在更新 Playwright Chromium…")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])

    # ── 5. Windows 上写兜底 .pth 确保 editable install 路径不丢失 ────
    if sys.platform == "win32":
        try:
            import site as _site
            sp_dirs = _site.getsitepackages()
            if sp_dirs:
                pth_path = Path(sp_dirs[0]) / "sjtu_agent_editable_path.pth"
                pth_path.write_text(str(PROJECT_ROOT) + "\n", encoding="utf-8")
                print(f"已刷新 .pth 文件：{pth_path}")
        except Exception as _e:
            print(f"（写 .pth 失败，非致命：{_e}）")

    # ── 6. 打印新版本 ────────────────────────────────────────────────────
    try:
        import importlib
        import sjtu_agent as _pkg
        importlib.reload(_pkg)
        new_version = _pkg.__version__
    except Exception:
        new_version = "（重新打开终端后生效）"
    print(f"\n[OK] 更新完成！当前版本：{new_version}")
    print("  如果 feishu-bot 等功能未生效，请重新打开终端。")
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    return _run_module("agent", args.script_args)


def _cmd_setup_config(args: argparse.Namespace) -> int:
    return _run_module("setup_config", args.script_args)


def _cmd_login(args: argparse.Namespace) -> int:
    return _run_module("login", args.script_args)


def _cmd_ddl(args: argparse.Namespace) -> int:
    return _run_module("ddl_checker", args.script_args)


def _cmd_daily_report(args: argparse.Namespace) -> int:
    return _run_module("daily_report", args.script_args)


def _cmd_telegram_bot(args: argparse.Namespace) -> int:
    return _run_module("telegram_bot", args.script_args)


def _cmd_feishu_bot(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parent.parent
    script = root / "feishu_bot.py"
    old_argv = sys.argv[:]
    sys.argv = [str(script), *(args.script_args or [])]
    try:
        runpy.run_path(str(script), run_name="__main__")
        return 0
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 0
    finally:
        sys.argv = old_argv


def _cmd_qq_bot(args: argparse.Namespace) -> int:
    return _run_script("qq_bot", args.script_args)


def _cmd_remind_check(args: argparse.Namespace) -> int:
    return _run_module("remind_check", args.script_args)


def _cmd_news_digest(args: argparse.Namespace) -> int:
    """运行智能新闻日报（采集 + 排序 + 推送）。"""
    root = Path(__file__).resolve().parent.parent
    script = root / "news_digest.py"
    old_argv = sys.argv[:]
    sys.argv = [str(script), *(args.script_args or [])]
    try:
        import runpy
        runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = old_argv
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    return _run_module("mcp_server", args.script_args)


def _parse_kv_items(items: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"Expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Empty key in: {item}")
        result[key] = value
    return result


def _cmd_add_mcp_server(args: argparse.Namespace) -> int:
    from sjtu_agent.agent.tools import tool_add_mcp_server

    try:
        env = _parse_kv_items(args.env)
        headers = _parse_kv_items(args.header)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = tool_add_mcp_server(
        server_id=args.server_id,
        transport=args.transport,
        command=args.command or "",
        args=args.arg or [],
        url=args.url or "",
        cwd=args.cwd or "",
        env=env,
        headers=headers,
        enabled=not args.disabled,
        call_timeout=args.call_timeout,
        acknowledge_external_mcp=True,
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


def _cmd_add_skill(args: argparse.Namespace) -> int:
    from sjtu_agent.agent.tools import tool_add_skill

    content = args.content or ""
    if args.content_file:
        try:
            content = Path(args.content_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"failed to read --content-file: {exc}", file=sys.stderr)
            return 1
    payload = tool_add_skill(
        name=args.name,
        content=content,
        source_file=args.source_file or "",
        enabled=not args.disabled,
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


def _cmd_list_skills(args: argparse.Namespace) -> int:
    from sjtu_agent.agent.tools import tool_list_skills

    payload = tool_list_skills(include_content=args.include_content)
    print_json(payload)
    return 0 if payload.get("ok") else 1


def _cmd_manage_skill(args: argparse.Namespace) -> int:
    from sjtu_agent.agent.tools import tool_manage_skill

    payload = tool_manage_skill(action=args.action, name=args.name)
    print_json(payload)
    return 0 if payload.get("ok") else 1


def _cmd_web(args: argparse.Namespace) -> int:
    from sjtu_agent.web.server import start
    start(port=args.port, open_browser=not args.no_browser)
    return 0


def _cmd_wechat_bot(args: argparse.Namespace) -> int:
    # wechat_bot.py 位于项目根目录，用 run_path 直接执行脚本文件
    root = Path(__file__).resolve().parent.parent
    script = root / "wechat_bot.py"
    old_argv = sys.argv[:]
    sys.argv = [str(script), *(args.script_args or [])]
    try:
        runpy.run_path(str(script), run_name="__main__")
        return 0
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 0
    finally:
        sys.argv = old_argv


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("time must be in HH:MM format") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise argparse.ArgumentTypeError("time must be in HH:MM format")
    return hour, minute


def _cmd_install_daemons(args: argparse.Namespace) -> int:
    try:
        # 构建平台专属参数（macOS 支持自定义 output_dir 和 telegram_throttle）
        platform_kwargs: dict = {}
        if hasattr(args, "output_dir") and args.output_dir:
            platform_kwargs["output_dir"] = Path(args.output_dir)
        if hasattr(args, "telegram_throttle"):
            platform_kwargs["telegram_throttle"] = args.telegram_throttle

        payload = install_daemons(
            service_names=tuple(args.services) if args.services else None,
            python_executable=Path(args.python_executable),
            daily_report_time=args.daily_report_time,
            remind_interval=args.remind_interval,
            load=not args.write_only,
            backend=getattr(args, "backend", "taskschd"),
            **platform_kwargs,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print_json(payload)
    if not args.write_only:
        import time
        import webbrowser
        import urllib.request
        url = "http://127.0.0.1:7860"
        # Poll until web service is up (max 15s) instead of a fixed sleep
        for _ in range(15):
            try:
                urllib.request.urlopen(url + "/api/status", timeout=1)
                break
            except Exception:
                time.sleep(1)
        webbrowser.open(url)
    return 0


def _add_passthrough_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    handler,
) -> None:
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument("script_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    parser.set_defaults(func=handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sjtu-agent", description="Deployable CLI for SJTU Agent.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    register_setup_parser(subparsers, _parse_hhmm)

    _add_passthrough_parser(subparsers, "chat", "start interactive chat mode", _cmd_chat)
    _add_passthrough_parser(subparsers, "setup-config", "build config.json from browser cookies", _cmd_setup_config)
    _add_passthrough_parser(subparsers, "login", "refresh platform cookies with Playwright", _cmd_login)
    _add_passthrough_parser(subparsers, "ddl", "run the DDL checker report", _cmd_ddl)
    _add_passthrough_parser(subparsers, "daily-report", "generate or send the daily report", _cmd_daily_report)
    _add_passthrough_parser(subparsers, "telegram-bot", "start the Telegram bot", _cmd_telegram_bot)
    _add_passthrough_parser(subparsers, "feishu-bot", "start the Feishu (Lark) bot (long connection)", _cmd_feishu_bot)
    _add_passthrough_parser(subparsers, "qq-bot", "start the QQ official bot (botpy)", _cmd_qq_bot)
    _add_passthrough_parser(subparsers, "remind-check", "run the reminder daemon once", _cmd_remind_check)
    _add_passthrough_parser(subparsers, "news-digest", "run the smart news digest (collect + rank + push)", _cmd_news_digest)
    _add_passthrough_parser(subparsers, "mcp", "start the MCP server", _cmd_mcp)
    _add_passthrough_parser(subparsers, "wechat-bot", "start the WeChat ilink bot (long-polling)", _cmd_wechat_bot)

    add_mcp_parser = subparsers.add_parser(
        "add-mcp-server",
        help="register a custom external MCP server",
    )
    add_mcp_parser.add_argument("server_id", help="short MCP server id")
    add_mcp_parser.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable_http", "http"])
    add_mcp_parser.add_argument("--command", default="", help="command for stdio transport")
    add_mcp_parser.add_argument("--arg", action="append", default=[], help="stdio command argument; repeat for multiple args")
    add_mcp_parser.add_argument("--url", default="", help="MCP endpoint URL for sse/http transports")
    add_mcp_parser.add_argument("--cwd", default="", help="working directory for stdio transport")
    add_mcp_parser.add_argument("--env", action="append", default=[], help="environment variable KEY=VALUE; repeatable")
    add_mcp_parser.add_argument("--header", action="append", default=[], help="HTTP header KEY=VALUE; repeatable")
    add_mcp_parser.add_argument("--call-timeout", type=int, default=120, help="tool call timeout in seconds")
    add_mcp_parser.add_argument("--disabled", action="store_true", help="write config but do not enable")
    add_mcp_parser.set_defaults(func=_cmd_add_mcp_server)

    add_skill_parser = subparsers.add_parser(
        "add-skill",
        help="create or enable a custom prompt-only skill",
    )
    add_skill_parser.add_argument("name", help="skill name / directory id")
    add_skill_parser.add_argument("--content", default="", help="SKILL.md content")
    add_skill_parser.add_argument("--content-file", default="", help="read SKILL.md content from this file")
    add_skill_parser.add_argument("--source-file", default="", help="copy skill content from an existing local SKILL.md file")
    add_skill_parser.add_argument("--disabled", action="store_true", help="write the skill but do not enable it")
    add_skill_parser.set_defaults(func=_cmd_add_skill)

    list_skills_parser = subparsers.add_parser(
        "list-skills",
        help="list prompt-only skills and enabled state",
    )
    list_skills_parser.add_argument("--include-content", action="store_true", help="include full SKILL.md content")
    list_skills_parser.set_defaults(func=_cmd_list_skills)

    manage_skill_parser = subparsers.add_parser(
        "manage-skill",
        help="enable, disable, or delete a prompt-only skill",
    )
    manage_skill_parser.add_argument("action", choices=["enable", "disable", "delete"])
    manage_skill_parser.add_argument("name", help="skill name / directory id")
    manage_skill_parser.set_defaults(func=_cmd_manage_skill)

    web_parser = subparsers.add_parser("web", help="open the local web configuration UI in your browser")
    web_parser.add_argument(
        "--port",
        type=int,
        default=7860,
        help="port to listen on (default: 7860)",
    )
    web_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="start the server without opening the browser automatically",
    )
    web_parser.set_defaults(func=_cmd_web)

    _platform_name = current_platform_name()
    install_daemons_parser = subparsers.add_parser(
        "install-daemons",
        help=f"install background services for the current platform ({_platform_name})",
    )
    install_daemons_parser.add_argument(
        "--output-dir",
        default=None,
        help="(macOS/Linux) directory where service files will be written (default: platform standard path)",
    )
    install_daemons_parser.add_argument(
        "--write-only",
        action="store_true",
        help="only write service files; do not load/register them",
    )
    install_daemons_parser.add_argument(
        "--python-executable",
        default=sys.executable,
        help="python executable that background services should use",
    )
    install_daemons_parser.add_argument(
        "--services",
        nargs="+",
        choices=available_service_names(),
        help="subset of services to install",
    )
    install_daemons_parser.add_argument(
        "--daily-report-time",
        type=_parse_hhmm,
        default=(22, 0),
        help="daily report schedule in HH:MM, default 22:00",
    )
    install_daemons_parser.add_argument(
        "--remind-interval",
        type=int,
        default=60,
        help="reminder daemon interval in seconds, default 60",
    )
    install_daemons_parser.add_argument(
        "--telegram-throttle",
        type=int,
        default=10,
        help="(macOS) launchd throttle interval for telegram bot restarts, default 10",
    )
    install_daemons_parser.add_argument(
        "--backend",
        choices=["taskschd", "psmux"],
        default="taskschd",
        help="(Windows) 后端选择：taskschd（任务计划程序，默认）或 psmux（分离会话）",
    )
    install_daemons_parser.set_defaults(func=_cmd_install_daemons)

    doctor = subparsers.add_parser("doctor", help="print runtime paths and setup status")
    doctor.set_defaults(func=_cmd_doctor)

    update_parser = subparsers.add_parser(
        "update",
        help="从远端仓库拉取最新代码并重装",
    )
    update_parser.add_argument(
        "--skip-git",
        action="store_true",
        help="跳过 git pull，仅重装依赖",
    )
    update_parser.add_argument(
        "--upgrade-deps",
        action="store_true",
        help="同时升级所有 Python 依赖至最新版",
    )
    update_parser.add_argument(
        "--update-playwright",
        action="store_true",
        help="同时更新 Playwright Chromium 浏览器",
    )
    update_parser.set_defaults(func=_cmd_update)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    if not getattr(args, "command", None):
        return _cmd_chat(argparse.Namespace(script_args=[]))
    if unknown:
        if hasattr(args, "script_args"):
            args.script_args = list(getattr(args, "script_args", [])) + unknown
        else:
            parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
