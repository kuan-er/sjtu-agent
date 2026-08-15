from __future__ import annotations

import argparse
import json
import runpy
import sys
from datetime import datetime
from pathlib import Path

from sjtu_agent import __version__
from sjtu_agent.paths import describe_runtime_paths
from sjtu_agent.scheduler import (
    available_service_names,
    current_platform_name,
    daemon_status,
    install_daemons,
    list_deployments,
    resync_daemons,
    uninstall_daemons,
)
from sjtu_agent.setup_wizard import register_setup_parser
from sjtu_agent.terminal_ui import print_json


def _resolve_script_path(script_name: str) -> Path:
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "scripts" / f"{script_name}.py",
        root / f"{script_name}.py",  # backward compatibility
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"script not found for '{script_name}'. tried: " + ", ".join(str(p) for p in candidates)
    )


def _run_script(script_name: str, script_args: list[str] | None = None) -> int:
    try:
        script = _resolve_script_path(script_name)
    except FileNotFoundError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    old_argv = sys.argv[:]
    sys.argv = [str(script), *(script_args or [])]
    try:
        runpy.run_path(str(script), run_name="__main__")
        return 0
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 0
    finally:
        sys.argv = old_argv


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

    # ── 1.5 检测已安装的后台服务 ───────────────────────────────────────────
    # 后台 daemon 在 git pull + reinstall 后仍引用旧模块路径，会导致
    # ModuleNotFoundError（issue #113）。从安装清单读取所有平台的部署记录，
    # 更新前停止、更新后按原参数恢复。
    _installed_daemons = list_deployments()

    def _restore_installed_daemons() -> None:
        """按更新前的清单重启后台服务。"""
        for _deployment in _installed_daemons:
            _backend = _deployment["backend"]
            _names = _deployment["services"]
            print(f"[i] 重启后台服务（{_backend}）: {', '.join(_names)}")
            try:
                kwargs: dict = {
                    "daily_report_time": tuple(_deployment.get("daily_report_time") or (22, 0)),
                    "remind_interval": int(_deployment.get("remind_interval") or 60),
                    "telegram_throttle": int(_deployment.get("telegram_throttle") or 10),
                }
                if _deployment.get("output_dir"):
                    kwargs["output_dir"] = Path(_deployment["output_dir"])
                install_daemons(
                    service_names=tuple(_names),
                    python_executable=Path(sys.executable),
                    backend=_backend,
                    **kwargs,
                )
            except Exception as e:
                print(f"[!] 重启后台服务失败: {e}")

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
    # 确认要更新后，先停后台服务（避免更新后旧模块引用报错）
    for _deployment in _installed_daemons:
        _backend = _deployment["backend"]
        _names = _deployment["services"]
        print(f"[i] 更新前停止后台服务（{_backend}）: {', '.join(_names)}")
        try:
            kwargs: dict = {}
            if _deployment.get("output_dir"):
                kwargs["output_dir"] = Path(_deployment["output_dir"])
            uninstall_daemons(service_names=tuple(_names), backend=_backend, **kwargs)
        except Exception as e:
            print(f"[!] 停止后台服务失败（可忽略）: {e}")
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
                _restore_installed_daemons()
                return 1

    # ── 3. pip install -e . ────────────────────────────────────────────────
    print("\n正在重新安装…")
    pip_cmd = [sys.executable, "-m", "pip", "install", "-e", str(PROJECT_ROOT), "--quiet"]
    if args.upgrade_deps:
        pip_cmd.append("--upgrade")
    result = subprocess.run(pip_cmd)
    if result.returncode != 0:
        print("[!] 依赖安装失败，请手动运行: pip install -e " + str(PROJECT_ROOT))
        _restore_installed_daemons()
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

    # ── 5.5 重启后台服务 ──────────────────────────────────────────────────
    if _installed_daemons:
        _restore_installed_daemons()

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
    return _run_script("setup_config", args.script_args)


def _cmd_login(args: argparse.Namespace) -> int:
    return _run_module("login", args.script_args)


def _cmd_ddl(args: argparse.Namespace) -> int:
    return _run_module("ddl_checker", args.script_args)


def _cmd_daily_report(args: argparse.Namespace) -> int:
    passthru = list(args.script_args) if args.script_args else []
    if getattr(args, "type", "evening") != "evening":
        passthru = ["--type", args.type] + passthru
    if getattr(args, "test", False):
        passthru = ["--test"] + passthru
    return _run_script("daily_report", passthru)


def _cmd_telegram_bot(args: argparse.Namespace) -> int:
    return _run_script("telegram_bot", args.script_args)


def _cmd_feishu_bot(args: argparse.Namespace) -> int:
    return _run_script("feishu_bot", args.script_args)


def _cmd_qq_bot(args: argparse.Namespace) -> int:
    return _run_script("qq_bot", args.script_args)


def _cmd_email_watcher(args: argparse.Namespace) -> int:
    return _run_script("email_watcher", args.script_args)


def _cmd_canvas_watcher(args: argparse.Namespace) -> int:
    return _run_script("canvas_watcher", args.script_args)


def _cmd_remind_check(args: argparse.Namespace) -> int:
    return _run_script("remind_check", args.script_args)


def _cmd_news_digest(args: argparse.Namespace) -> int:
    """运行智能新闻日报（采集 + 排序 + 推送）。"""
    return _run_script("news_digest", args.script_args)


def _cmd_aihot_push(args: argparse.Namespace) -> int:
    """获取 AI HOT 精选资讯并推送飞书。"""
    return _run_script("aihot_push", args.script_args)


def _cmd_mcp(args: argparse.Namespace) -> int:
    return _run_script("mcp_server", args.script_args)


def _cmd_install_parse_backends(args: argparse.Namespace) -> int:
    script_args: list[str] = []
    backend = getattr(args, "backend", "")
    if backend:
        script_args += ["--backend", backend]
    if getattr(args, "upgrade", False):
        script_args.append("--upgrade")
    return _run_script("install_parse_backends", script_args)


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
    start(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def _cmd_tui(args: argparse.Namespace) -> int:
    from sjtu_agent.tui import run
    return run()


def _cmd_web_proxy(args: argparse.Namespace) -> int:
    from sjtu_agent.web_proxy import generate_proxy_config, write_proxy_config

    try:
        if args.output:
            destination = write_proxy_config(
                Path(args.output),
                kind=args.type,
                domain=args.domain,
                backend_port=args.port,
                force=args.force,
            )
            print(f"已生成 HTTPS 反代配置：{destination}")
        else:
            sys.stdout.write(generate_proxy_config(args.type, args.domain, args.port))
    except (OSError, ValueError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_wechat_bot(args: argparse.Namespace) -> int:
    return _run_script("wechat_bot", args.script_args)


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


def _should_open_web_after_install(args: argparse.Namespace) -> bool:
    """安装后台服务后是否轮询并打开 Web UI。

    仅在明确安装（或默认安装包含）web 服务、且用户没有传 --no-browser 时返回 True，
    避免无头服务器上 `install-daemons --services feishu-bot` 卡 15 秒再尝试打开浏览器。
    """
    if getattr(args, "write_only", False) or getattr(args, "no_browser", False):
        return False
    selected = getattr(args, "services", None) or None
    return selected is None or "web" in selected


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
    if _should_open_web_after_install(args):
        import time
        import webbrowser
        import urllib.request
        url = "http://127.0.0.1:7860"
        # Poll until web service is up (max 15s) instead of a fixed sleep.
        # 注意：/api/status 受 cookie 保护，无 cookie 会 403 导致 poll 永远等满 15s，
        # 所以轮询根路径 /（无需鉴权）来判断服务是否就绪。
        for _ in range(15):
            try:
                urllib.request.urlopen(url + "/", timeout=1)
                break
            except Exception:
                time.sleep(1)
        webbrowser.open(url)
    elif not args.write_only:
        print("[i] 未选择 web 服务，跳过浏览器启动。")
    return 0


def _cmd_daemons_status(args: argparse.Namespace) -> int:
    payload = daemon_status(
        service_names=tuple(args.services) if args.services else None,
        backend=args.backend,
    )
    print_json(payload)
    return 0 if payload.get("all_installed", payload.get("all_running", True)) else 1


def _cmd_daemons_uninstall(args: argparse.Namespace) -> int:
    try:
        payload = uninstall_daemons(
            service_names=tuple(args.services) if args.services else None,
            backend=args.backend,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print_json(payload)
    return 0


def _cmd_daemons_resync(args: argparse.Namespace) -> int:
    payload = resync_daemons(
        python_executable=Path(args.python_executable) if args.python_executable else None,
    )
    print_json(payload)
    return 0


def _prompt_config_password(confirm: bool) -> str:
    """交互式读取配置归档密码；无 TTY 时从 SJTU_AGENT_CONFIG_PASSWORD 读取。"""
    from sjtu_agent.config_transfer import password_from_env

    env_password = password_from_env()
    if env_password:
        return env_password

    try:
        import getpass
        if sys.stdin.isatty():
            password = getpass.getpass("Config archive password: ")
            if not confirm:
                return password
            again = getpass.getpass("Confirm password: ")
            if password != again:
                raise ValueError("两次输入的密码不一致")
            return password
    except EOFError as exc:
        raise ValueError("无法在无 TTY 环境交互输入密码") from exc
    raise ValueError(
        "无法交互输入密码。请设置 SJTU_AGENT_CONFIG_PASSWORD，或使用未加密归档 + SSH 管道传输。"
    )


def _cmd_export_config(args: argparse.Namespace) -> int:
    from sjtu_agent.config_transfer import export_bytes, export_to_path

    encrypt_password: str | None = None
    if args.encrypt:
        try:
            encrypt_password = _prompt_config_password(confirm=True)
        except ValueError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            return 1
        if not encrypt_password:
            print("[!] 密码不能为空。", file=sys.stderr)
            return 1

    expires_hours = None if getattr(args, "no_expiry", False) else args.expires_hours

    if getattr(args, "output", None) == "-":
        # --output -：二进制写入 stdout，报告写入 stderr
        try:
            data = export_bytes(
                include_state=args.with_state,
                state_files=args.state_file,
                encrypt_password=encrypt_password,
                expires_hours=expires_hours,
            )
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[!] 导出失败：{exc}", file=sys.stderr)
            return 1
        print(f"[i] 已向 stdout 写出 {len(data)} 字节。", file=sys.stderr)
        return 0

    destination = (
        Path(args.output)
        if args.output
        else Path.cwd() / f"sjtu-agent-config-{datetime.now().strftime('%Y%m%d-%H%M%S')}.tar.gz"
    )
    try:
        payload = export_to_path(
            destination,
            include_state=args.with_state,
            state_files=args.state_file,
            encrypt_password=encrypt_password,
            expires_hours=expires_hours,
            force=args.force,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[!] 导出失败：{exc}", file=sys.stderr)
        return 1
    print_json(payload)
    if not encrypt_password:
        print(
            "[i] 这是未加密归档，内含 API Key / 密码 / Token。"
            "请通过 SSH/scp 传输，用后删除，勿上传公网。",
            file=sys.stderr,
        )
    return 0


def _read_import_source(source: str) -> bytes:
    if source == "-":
        data = sys.stdin.buffer.read()
    else:
        data = Path(source).read_bytes()
    if not data:
        raise ValueError("输入为空")
    return data


def _cmd_import_config(args: argparse.Namespace) -> int:
    from sjtu_agent.config_transfer import is_encrypted, import_bytes, password_from_env

    try:
        data = _read_import_source(args.archive)
    except (OSError, ValueError) as exc:
        print(f"[!] 读取输入失败：{exc}", file=sys.stderr)
        return 1

    decrypt_password: str | None = None
    if is_encrypted(data):
        decrypt_password = password_from_env()
        if not decrypt_password:
            try:
                decrypt_password = _prompt_config_password(confirm=False)
            except ValueError as exc:
                print(f"[!] {exc}", file=sys.stderr)
                return 1

    if not args.dry_run and not args.yes:
        if args.archive == "-":
            print(
                "[!] stdin 模式会覆盖运行时凭据文件，必须显式传 --yes。",
                file=sys.stderr,
            )
            return 1
        if not sys.stdin.isatty():
            print(
                "[!] 非交互环境导入会覆盖运行时凭据文件，必须显式传 --yes（或 --dry-run 预览）。",
                file=sys.stderr,
            )
            return 1
        answer = input("导入会覆盖同名运行时文件（自动备份），确认继续？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消。", file=sys.stderr)
            return 1

    try:
        payload = import_bytes(
            data,
            target_dir=Path(args.target_dir) if args.target_dir else None,
            decrypt_password=decrypt_password,
            skip_state=args.skip_state,
            state_files=args.state_file,
            allow_expired=args.allow_expired,
            dry_run=args.dry_run,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[!] 导入失败：{exc}", file=sys.stderr)
        return 1
    print_json(payload)
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
    daily_rpt = subparsers.add_parser("daily-report", help="generate or send the daily report")
    daily_rpt.add_argument("--type", choices=["morning", "noon", "evening"],
                           default="evening", help="report type")
    daily_rpt.add_argument("--test", action="store_true", help="print only, do not send")
    daily_rpt.add_argument("script_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    daily_rpt.set_defaults(func=_cmd_daily_report)
    _add_passthrough_parser(subparsers, "telegram-bot", "start the Telegram bot", _cmd_telegram_bot)
    _add_passthrough_parser(subparsers, "feishu-bot", "start the Feishu (Lark) bot (long connection)", _cmd_feishu_bot)
    _add_passthrough_parser(subparsers, "qq-bot", "start the QQ official bot (botpy)", _cmd_qq_bot)
    _add_passthrough_parser(subparsers, "email-watcher", "monitor SJTU email and push new mail via Feishu", _cmd_email_watcher)
    _add_passthrough_parser(subparsers, "canvas-watcher", "monitor Canvas course announcements and quizzes", _cmd_canvas_watcher)
    _add_passthrough_parser(subparsers, "remind-check", "run the reminder daemon once", _cmd_remind_check)
    _add_passthrough_parser(subparsers, "news-digest", "run the smart news digest (collect + rank + push)", _cmd_news_digest)
    _add_passthrough_parser(subparsers, "aihot", "fetch AI HOT news and push to Feishu", _cmd_aihot_push)
    _add_passthrough_parser(subparsers, "mcp", "start the MCP server", _cmd_mcp)
    _add_passthrough_parser(subparsers, "wechat-bot", "start the WeChat ilink bot (long-polling)", _cmd_wechat_bot)

    install_parse_backends_parser = subparsers.add_parser(
        "install-parse-backends",
        help="install pinned OCR/ASR parser backends",
    )
    install_parse_backends_parser.add_argument(
        "--backend",
        choices=["all", "paddleocr", "pdf_ocr", "whisper"],
        default="all",
        help="backend group to install (default: all)",
    )
    install_parse_backends_parser.add_argument(
        "--upgrade",
        action="store_true",
        help="pass --upgrade to pip install",
    )
    install_parse_backends_parser.set_defaults(func=_cmd_install_parse_backends)

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
        "--host",
        default="127.0.0.1",
        help="host/interface to bind (default: 127.0.0.1; use 0.0.0.0 for remote access)",
    )
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

    tui_parser = subparsers.add_parser(
        "tui",
        help="start the full-screen Textual terminal UI (requires: pip install -e \".[tui]\")",
    )
    tui_parser.set_defaults(func=_cmd_tui)

    web_proxy_parser = subparsers.add_parser(
        "web-proxy",
        help="generate an Nginx or Caddy HTTPS reverse-proxy config for the Web UI",
    )
    web_proxy_parser.add_argument(
        "--type",
        choices=["nginx", "caddy"],
        default="nginx",
        help="reverse proxy type (default: nginx)",
    )
    web_proxy_parser.add_argument(
        "--domain",
        required=True,
        help="public domain name, e.g. sjtu-agent.example.com",
    )
    web_proxy_parser.add_argument(
        "--port",
        type=int,
        default=7860,
        help="local Web UI port that the proxy should forward to (default: 7860)",
    )
    web_proxy_parser.add_argument(
        "--output",
        default=None,
        help="write config to a file instead of stdout",
    )
    web_proxy_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing output file",
    )
    web_proxy_parser.set_defaults(func=_cmd_web_proxy)

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
        "--no-browser",
        action="store_true",
        help="do not poll/open the local Web UI after installation (useful on servers)",
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

    daemons_parser = subparsers.add_parser(
        "daemons",
        help="inspect, uninstall, or resync previously installed background services",
    )
    daemons_sub = daemons_parser.add_subparsers(dest="daemons_action", required=True)

    daemons_status_parser = daemons_sub.add_parser("status", help="show background service status")
    daemons_status_parser.add_argument(
        "--services",
        nargs="+",
        choices=available_service_names(),
        help="subset of services to query",
    )
    daemons_status_parser.add_argument(
        "--backend",
        choices=["taskschd", "psmux"],
        default="taskschd",
        help="(Windows) 后端选择：taskschd 或 psmux",
    )
    daemons_status_parser.set_defaults(func=_cmd_daemons_status)

    daemons_uninstall_parser = daemons_sub.add_parser(
        "uninstall", help="stop and uninstall background services"
    )
    daemons_uninstall_parser.add_argument(
        "--services",
        nargs="+",
        choices=available_service_names(),
        help="subset of services to uninstall (default: all)",
    )
    daemons_uninstall_parser.add_argument(
        "--backend",
        choices=["taskschd", "psmux"],
        default="taskschd",
        help="(Windows) 后端选择：taskschd 或 psmux",
    )
    daemons_uninstall_parser.set_defaults(func=_cmd_daemons_uninstall)

    daemons_resync_parser = daemons_sub.add_parser(
        "resync",
        help="restore background services recorded in the install manifest after a reinstall",
    )
    daemons_resync_parser.add_argument(
        "--python-executable",
        default=None,
        help="python executable to use (default: current interpreter)",
    )
    daemons_resync_parser.set_defaults(func=_cmd_daemons_resync)

    export_config_parser = subparsers.add_parser(
        "export-config",
        help="export runtime credentials/config as a portable archive (use - for stdout)",
    )
    export_config_parser.add_argument(
        "--output",
        default=None,
        help="output file path (default: sjtu-agent-config-<timestamp>.tar.gz; use - for stdout)",
    )
    export_config_parser.add_argument(
        "--with-state",
        action="store_true",
        help="also export all state files (reminders/user profile/dining history)",
    )
    export_config_parser.add_argument(
        "--state-file",
        action="append",
        choices=["reminders.json", "user_profile.json", "dining_history.json"],
        default=[],
        help="select a specific state file to export; repeatable",
    )
    export_config_parser.add_argument(
        "--encrypt",
        action="store_true",
        help="encrypt the archive with a passphrase (prompts, or SJTU_AGENT_CONFIG_PASSWORD)",
    )
    export_config_parser.add_argument(
        "--expires-hours",
        type=int,
        default=24,
        help="archive validity in hours (default: 24; 1-720)",
    )
    export_config_parser.add_argument(
        "--no-expiry",
        action="store_true",
        help="do not set an expiry time (not recommended for plaintext archives)",
    )
    export_config_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing output file",
    )
    export_config_parser.set_defaults(func=_cmd_export_config)

    import_config_parser = subparsers.add_parser(
        "import-config",
        help="import a config archive exported by export-config (use - for stdin)",
    )
    import_config_parser.add_argument("archive", help="archive path, or - for stdin")
    import_config_parser.add_argument(
        "--target-dir",
        default=None,
        help="runtime data directory to import into (default: current SJTU_AGENT_HOME)",
    )
    import_config_parser.add_argument(
        "--skip-state",
        action="store_true",
        help="do not import optional state files if present in the archive",
    )
    import_config_parser.add_argument(
        "--state-file",
        action="append",
        choices=["reminders.json", "user_profile.json", "dining_history.json"],
        default=[],
        help="select a specific state file to import; repeatable",
    )
    import_config_parser.add_argument(
        "--allow-expired",
        action="store_true",
        help="import an archive even after its expires_at timestamp",
    )
    import_config_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print what would be written without writing",
    )
    import_config_parser.add_argument(
        "--yes",
        action="store_true",
        help="overwrite same-name runtime files without prompting (backs them up first)",
    )
    import_config_parser.set_defaults(func=_cmd_import_config)

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
