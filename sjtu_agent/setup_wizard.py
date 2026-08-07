from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from sjtu_agent.scheduler import (
    available_service_names,
    install_daemons,
)
# macOS launchd 专属功能（plist 状态查询）按需导入
def _get_launchd_state_checker():
    """延迟导入 macOS launchd 状态查询，在非 macOS 平台返回 None。"""
    if sys.platform == "darwin":
        try:
            from sjtu_agent.scheduler.launchd import status as _launchd_status
            return _launchd_status
        except ImportError:
            pass
    return None
from sjtu_agent.paths import AGENT_CONFIG_PATH, CONFIG_PATH, ENV_PATH, describe_runtime_paths
from sjtu_agent.terminal_ui import print_bullets, print_json, print_key_value, print_markdown_message, print_rule, print_status


def _print_header(title: str) -> None:
    print_rule(title)


def _confirm(prompt: str, default: bool, assume_yes: bool) -> bool:
    if assume_yes:
        answer = "yes" if default else "no"
        print(f"{prompt} [{answer}]")
        return default

    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{prompt} {suffix} ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def _print_check(label: str, ok: bool, detail: str = "") -> None:
    print_status(label, ok, detail)


def _dependency_checks() -> tuple[list[dict[str, object]], list[str]]:
    checks = [
        ("playwright", "playwright.sync_api", "browser automation", True),
        ("python-dotenv", "dotenv", "load .env credentials", True),
        ("browser-cookie3", "browser_cookie3", "import cookies from Chrome", False),
    ]

    results: list[dict[str, object]] = []
    fatal_errors: list[str] = []
    for name, module_name, purpose, required in checks:
        try:
            importlib.import_module(module_name)
            results.append({"name": name, "ok": True, "detail": purpose, "required": required})
        except Exception as exc:
            detail = f"{purpose}; import failed: {exc}"
            results.append({"name": name, "ok": False, "detail": detail, "required": required})
            if required:
                fatal_errors.append(f"{name}: {detail}")

    if sys.platform == "darwin":
        launchctl_path = shutil.which("launchctl")
        ok = bool(launchctl_path)
        results.append(
            {
                "name": "launchctl",
                "ok": ok,
                "detail": launchctl_path or "macOS launchd CLI not found",
                "required": True,
            }
        )
        if not ok:
            fatal_errors.append("launchctl: macOS launchd CLI not found")

    return results, fatal_errors


def _check_playwright_chromium() -> dict[str, object]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"ok": False, "detail": f"playwright import failed: {exc}", "path": None}

    try:
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
        return {
            "ok": executable.exists(),
            "detail": str(executable),
            "path": str(executable),
        }
    except Exception as exc:
        return {"ok": False, "detail": f"playwright runtime check failed: {exc}", "path": None}


def _install_playwright_chromium() -> bool:
    command = [sys.executable, "-m", "playwright", "install", "chromium"]
    print("Running:", " ".join(command))
    result = subprocess.run(command)
    return result.returncode == 0


def _doctor_status() -> dict:
    import agent

    return agent.tool_check_setup()


def _print_runtime_summary() -> None:
    paths = describe_runtime_paths()
    print_key_value("Runtime data directory", paths["data_dir"])
    print_key_value("Runtime agent config", paths["agent_config_path"])
    print_key_value("Runtime config file", paths["config_path"])
    print_key_value("Runtime env file", paths["env_path"])


def _agent_config_status() -> dict[str, object]:
    import agent

    cfg = agent.load_agent_config()
    return {
        "configured": bool(cfg.get("api_key") and cfg.get("model")),
        "base_url": cfg.get("base_url") or None,
        "model": cfg.get("model") or None,
    }


def _cli_agent_updates(args: argparse.Namespace) -> dict[str, str]:
    return {
        "base_url": args.llm_base_url or "",
        "api_key": args.llm_api_key or "",
        "model": args.llm_model or "",
    }


def _test_llm_connection(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """
    发一条极短的请求验证 LLM API 是否可用。
    返回 (ok, error_message)；ok=True 表示连通。
    """
    import os as _os
    # 确保 base_url 有协议头
    _url = base_url.strip().rstrip("/")
    if _url and not _url.startswith(("http://", "https://")):
        return False, f"Base URL 格式不正确（缺少 http:// 或 https://）：{_url!r}"
    if not api_key.strip():
        return False, "API Key 为空"
    if not model.strip():
        return False, "模型名称为空"

    try:
        from openai import OpenAI as _OpenAI
        _client = _OpenAI(
            api_key=api_key.strip(),
            base_url=_url or None,
        )
        _client.chat.completions.create(
            model=model.strip(),
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            timeout=15,
        )
        return True, ""
    except Exception as e:
        err = str(e)
        # 简化常见错误信息
        if "Connection error" in err or "UnsupportedProtocol" in err or "missing an 'http" in err:
            return False, f"无法连接到 API（{_url or 'openai 官方'}），请检查 Base URL 是否正确"
        if "401" in err or "Unauthorized" in err or "Invalid API key" in err.lower() or "authentication" in err.lower():
            return False, "API Key 无效或已失效，请重新检查"
        if "404" in err and "model" in err.lower():
            return False, f"模型 {model!r} 不存在，请检查模型名称"
        if "timeout" in err.lower() or "timed out" in err.lower():
            return False, f"连接超时（15s），请检查网络或 Base URL"
        return False, f"API 测试失败：{err[:200]}"


def _apply_agent_config_updates(updates: dict[str, str]) -> dict[str, str] | None:
    if not any(updates.values()):
        return None

    import agent

    current = agent.load_agent_config()
    saved = {
        "base_url": updates["base_url"] or current.get("base_url") or "https://models.sjtu.edu.cn/api/v1",
        "api_key": updates["api_key"] or current.get("api_key") or "",
        "model": updates["model"] or current.get("model") or "deepseek-chat",
    }
    # 保留已保存的 vision_model（主模型更新不清掉视觉模型）
    if isinstance(current.get("vision_model"), dict):
        saved["vision_model"] = current["vision_model"]
    AGENT_CONFIG_PATH.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    return saved


def _apply_vision_config_updates(updates: dict) -> dict | None:
    """保存/更新 vision_model 块到 agent_config.json。

    updates: {"enabled": bool, "base_url": str, "api_key": str, "model": str}
    api_key 仅在本地文件，绝不打印。
    """
    import agent

    current = agent.load_agent_config()
    vm = dict(current.get("vision_model") or {})
    for key in ("enabled", "base_url", "api_key", "model"):
        if updates.get(key):
            vm[key] = updates[key]
    if not vm:
        return None
    vm.setdefault("enabled", True)
    vm.setdefault("model", "qwen-vl-max")
    saved = dict(current)
    saved["vision_model"] = vm
    AGENT_CONFIG_PATH.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    return vm


def _read_secret(prompt: str) -> str:
    try:
        import getpass

        if sys.stdin.isatty():
            try:
                return getpass.getpass(prompt)
            except EOFError:
                return ""
    except Exception:
        pass
    return input(prompt)


def _cli_credential_updates(args: argparse.Namespace) -> dict[str, str]:
    return {
        "jaccount_username": args.jaccount_username or "",
        "jaccount_password": args.jaccount_password or "",
        "mooc_username": args.mooc_username or "",
        "mooc_password": args.mooc_password or "",
        "canvas_token": args.canvas_token or "",
    }


def _collect_credential_updates(args: argparse.Namespace, status: dict) -> dict[str, str]:
    import agent

    updates = _cli_credential_updates(args)

    if args.skip_credential_prompts:
        return updates

    if (not status["jaccount"]["has_credentials"] and not args.assume_yes and
            _confirm("Save jAccount username and password now?", True, False)):
        default_user = status["jaccount"].get("username") or ""
        entered_user = input(f"jAccount username [{default_user}]: ").strip()
        updates["jaccount_username"] = entered_user or default_user
        updates["jaccount_password"] = _read_secret("jAccount password: ").strip()
    elif status["jaccount"]["has_credentials"] and not args.assume_yes:
        if _confirm("Update saved jAccount credentials?", False, False):
            default_user = status["jaccount"].get("username") or ""
            entered_user = input(f"jAccount username [{default_user}]: ").strip()
            updates["jaccount_username"] = entered_user or default_user
            new_password = _read_secret("jAccount password (leave blank to keep current): ").strip()
            updates["jaccount_password"] = new_password

    if (not status["icourse"]["has_credentials"] and not args.assume_yes and
            _confirm("Save MOOC credentials for icourse now?", False, False)):
        updates["mooc_username"] = input("MOOC username: ").strip()
        updates["mooc_password"] = _read_secret("MOOC password: ").strip()

    if not status["canvas"]["has_token"] and not args.assume_yes:
        if _confirm("Save a Canvas API token now?", True, False):
            canvas_info = agent.tool_setup_canvas(open_browser=False)
            settings_url = canvas_info.get("settings_url", "https://oc.sjtu.edu.cn/profile/settings")
            print("Canvas token settings page:", settings_url)
            if _confirm("Open the Canvas settings page in your browser now?", True, False):
                try:
                    webbrowser.open(settings_url)
                except Exception:
                    print("Could not open the browser automatically.")
            updates["canvas_token"] = input("Paste the Canvas token here (leave blank to skip): ").strip()

    return updates


def _apply_credential_updates(updates: dict[str, str]) -> dict[str, object] | None:
    import agent

    if not any(updates.values()):
        return None
    return agent.tool_save_credentials(**updates)


def _maybe_create_skeleton_config() -> None:
    import agent

    if not CONFIG_PATH.exists():
        agent.tool_save_credentials()


def _needs_cookie_import(status: dict) -> bool:
    return (
        not status.get("config_file_exists")
        or not status["aihaoke"]["has_cookies"]
        or not status["phycai"]["has_cookies"]
        or not status["icourse"]["has_cookies"]
    )


def _import_browser_cookies() -> bool:
    import importlib.util
    _script = Path(__file__).resolve().parent.parent / "scripts" / "setup_config.py"
    _spec = importlib.util.spec_from_file_location("setup_config", _script)
    if _spec is None or _spec.loader is None:
        raise RuntimeError("Cannot load setup_config.py from scripts/")
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["setup_config"] = _mod
    _spec.loader.exec_module(_mod)
    try:
        _mod.main()
        return True
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code == 0:
            return True
        raise RuntimeError("setup-config failed while importing browser cookies") from exc


def _maybe_import_browser_cookies(args: argparse.Namespace, status: dict) -> bool:
    if args.skip_cookie_import:
        return False

    default = _needs_cookie_import(status)
    if not _confirm("Import platform cookies from local Chrome now?", default, args.assume_yes):
        return False
    return _import_browser_cookies()


def _print_setup_status(status: dict) -> None:
    agent_status = status.get("agent", _agent_config_status())
    agent_detail = agent_status.get("model") or str(AGENT_CONFIG_PATH)
    if agent_status.get("configured") and agent_status.get("base_url"):
        agent_detail = f"{agent_status['model']} @ {agent_status['base_url']}"
    _print_check("LLM config", bool(agent_status.get("configured")), str(agent_detail))
    _print_check("config.json", status["config_file_exists"], str(CONFIG_PATH))
    _print_check("jAccount credentials", status["jaccount"]["has_credentials"], str(ENV_PATH))
    _print_check("Canvas token", status["canvas"]["has_token"], status["canvas"]["settings_url"])
    _print_check("AI 好课 (aihaoke) cookies", status["aihaoke"]["has_cookies"])
    _print_check("物理实验 (phycai) cookies", status["phycai"]["has_cookies"])
    _print_check("中国大学 MOOC (icourse) credentials", status["icourse"]["has_credentials"])
    _print_check("中国大学 MOOC (icourse) cookies", status["icourse"]["has_cookies"])
    _print_check(
        "shuiyuan access",
        status["shuiyuan"]["has_api_key"] or status["shuiyuan"]["has_cookies"],
        "API key or session cookie",
    )


def _build_recommendations(status: dict) -> list[str]:
    recommendations: list[str] = []
    if not status["agent"]["configured"]:
        recommendations.append(f"Add your LLM API settings in {AGENT_CONFIG_PATH} or rerun sjtu-agent setup.")
    if not status["jaccount"]["has_credentials"]:
        recommendations.append(f"Add jAccount credentials in {ENV_PATH} or rerun sjtu-agent setup.")
    if not status["canvas"]["has_token"]:
        if status["canvas"].get("can_auto_fetch"):
            recommendations.append("Canvas token can be auto-created now; rerun sjtu-agent setup and it will try automatically.")
        else:
            recommendations.append(
                f"Generate a Canvas token at {status['canvas']['settings_url']} and save it with sjtu-agent setup."
            )
    if not status["aihaoke"]["has_cookies"] or not status["phycai"]["has_cookies"] or not status["icourse"]["has_cookies"]:
        recommendations.append("Log into the teaching sites in Chrome, then rerun sjtu-agent setup or sjtu-agent setup-config.")
    if not (status["shuiyuan"]["has_api_key"] or status["shuiyuan"]["has_cookies"]):
        recommendations.append("Optional: set up Shuiyuan later from the chat agent if you need forum search.")
    return recommendations


def _ready_for_daemons(status: dict) -> bool:
    has_runtime_inputs = any(
        [
            status["jaccount"]["has_credentials"],
            status["canvas"]["has_token"],
            status["aihaoke"]["has_cookies"],
            status["phycai"]["has_cookies"],
            status["icourse"]["has_cookies"],
            status["shuiyuan"]["has_api_key"],
            status["shuiyuan"]["has_cookies"],
        ]
    )
    return status["config_file_exists"] and has_runtime_inputs


def _install_background_services(args: argparse.Namespace) -> dict[str, object]:
    """跨平台安装后台服务，调用统一调度层。"""
    platform_kwargs: dict = {}
    if hasattr(args, "output_dir") and args.output_dir:
        platform_kwargs["output_dir"] = Path(args.output_dir)
    if hasattr(args, "telegram_throttle"):
        platform_kwargs["telegram_throttle"] = args.telegram_throttle
    return install_daemons(
        service_names=tuple(args.services) if args.services else None,
        python_executable=Path(args.python_executable),
        daily_report_time=args.daily_report_time,
        remind_interval=args.remind_interval,
        load=not args.write_daemons_only,
        **platform_kwargs,
    )


def _daemon_state(args: argparse.Namespace) -> dict[str, object]:
    """查询当前平台后台服务安装状态。"""
    from sjtu_agent.scheduler import daemon_status
    platform_kwargs: dict = {}
    if hasattr(args, "output_dir") and args.output_dir:
        platform_kwargs["output_dir"] = Path(args.output_dir)
    result = daemon_status(
        service_names=tuple(args.services) if args.services else None,
        **platform_kwargs,
    )
    services = result.get("services", [])
    return {
        "services": services,
        "existing": [s for s in services if s.get("installed")],
        "missing":  [s for s in services if not s.get("installed")],
        "all_present": result.get("all_installed", False),
    }


# 向后兼容别名（供 SetupConversation 内部使用）
_install_launchd = _install_background_services
_launchd_state   = _daemon_state


def _maybe_install_background_services(args: argparse.Namespace, status: dict) -> bool:
    if args.skip_launchd:
        return False

    ready = _ready_for_daemons(status)
    if not ready:
        print("Background services are not installed yet because the configuration is still incomplete.")

    platform_label = "macOS background services" if sys.platform == "darwin" else "background services"
    if not _confirm(f"Install {platform_label} now?", ready, args.assume_yes):
        return False

    try:
        payload = _install_background_services(args)
    except RuntimeError as exc:
        print(f"Background service installation failed: {exc}")
        return False

    output_dir = payload.get("output_dir") or payload.get("unit_dir") or ""
    if output_dir:
        print(f"Installed background services into: {output_dir}")
    for service in payload.get("services", []):
        name = service.get("name") or service.get("task_name") or service.get("unit_name") or ""
        path = service.get("plist_path") or service.get("service_path") or service.get("task_name") or ""
        print(f"  - {name} -> {path}")

    # Wait for web service to come up, then open browser
    import time
    import webbrowser
    import urllib.request
    url = "http://127.0.0.1:7860"
    print(f"Waiting for web UI to start at {url} …")
    for _ in range(15):
        try:
            urllib.request.urlopen(url + "/api/status", timeout=1)
            break
        except Exception:
            time.sleep(1)
    webbrowser.open(url)
    return True


def _run_automatic_setup(args: argparse.Namespace) -> int:
    print("SJTU Agent setup wizard")
    print("This command checks dependencies, prepares browser automation, reviews configuration, and can install macOS background services.")
    _print_runtime_summary()

    _print_header("Dependency Check")
    dependency_results, fatal_errors = _dependency_checks()
    for result in dependency_results:
        _print_check(str(result["name"]), bool(result["ok"]), str(result["detail"]))
    if fatal_errors:
        print("\nSetup cannot continue until the required dependencies are available:")
        for item in fatal_errors:
            print("  -", item)
        print("Try reinstalling the package with pip install -e . or pip install sjtu-agent.")
        return 1

    _print_header("Playwright Chromium")
    chromium_status = _check_playwright_chromium()
    _print_check("Chromium browser", bool(chromium_status["ok"]), str(chromium_status["detail"]))
    if not chromium_status["ok"] and not args.skip_playwright_install:
        print("Chromium is missing. Installing Playwright Chromium automatically...")
        if not _install_playwright_chromium():
            print("Playwright Chromium installation failed.")
            return 1
        chromium_status = _check_playwright_chromium()
        _print_check("Chromium browser", bool(chromium_status["ok"]), str(chromium_status["detail"]))
        if not chromium_status["ok"]:
            print("Chromium is still unavailable after installation.")
            return 1

    _print_header("Agent Model")
    agent_save = _apply_agent_config_updates(_cli_agent_updates(args))
    if agent_save:
        print(f"Saved LLM config: {agent_save['model']} @ {agent_save['base_url']}")
    else:
        agent_status = _agent_config_status()
        _print_check("LLM config", bool(agent_status["configured"]), str(agent_status.get("model") or AGENT_CONFIG_PATH))

    _print_header("Credentials")
    status = _doctor_status()
    updates = _collect_credential_updates(args, status)
    save_result = _apply_credential_updates(updates)
    if save_result:
        print("Saved:", ", ".join(save_result.get("saved", [])) or "nothing new")
    elif args.skip_credential_prompts and any(updates.values()):
        print("Applied credentials from command-line flags.")
    else:
        print("No manual credential changes were made.")

    _print_header("Browser Cookie Import")
    status = _doctor_status()
    imported = _maybe_import_browser_cookies(args, status)
    if imported:
        print("Browser cookies were imported into config.json.")
    else:
        print("Cookie import was skipped.")

    _maybe_create_skeleton_config()

    _print_header("Configuration Doctor")
    status = _doctor_status()
    _print_setup_status(status)
    recommendations = _build_recommendations(status)
    if recommendations:
        print("\nRecommended next steps:")
        for item in recommendations:
            print("  -", item)
    else:
        print("\nCore setup looks ready.")

    _print_header("Background Services")
    installed = _maybe_install_background_services(args, status)
    if not installed:
        print("Background services were not changed.")

    print("\nSetup wizard finished.")
    if status["agent"]["configured"]:
        print("The main agent is ready. Start it with: sjtu-agent")
    else:
        print(f"The main agent is not ready yet. Add LLM settings in {AGENT_CONFIG_PATH} and then run: sjtu-agent")
    print("You can rerun this command any time: sjtu-agent setup")
    return 0


def _classify_reply(raw: str) -> str:
    text = raw.strip().lower()
    if not text:
        return "empty"
    # 长度 > 20 通常是粘贴的 token/链接/句子，直接当文本处理，避免把 token 里的 "y"/"go" 当肯定词
    if len(text) > 20:
        return "text"
    if any(token in text for token in ["退出", "结束", "取消", "quit", "exit"]):
        return "quit"
    if any(token in text for token in ["帮助", "help", "怎么用", "能做什么", "命令"]):
        return "help"
    if any(token in text for token in ["状态", "体检", "summary", "status", "doctor", "检查"]):
        return "status"
    if "canvas" in text and any(token in text for token in ["auto", "自动", "代我", "帮我"]):
        return "auto_canvas"
    if "canvas" in text and any(token in text for token in ["open", "打开", "浏览器", "page", "网页"]):
        return "open_canvas"
    if any(token in text for token in ["跳过", "先不", "稍后", "later", "skip", "不用", "不要", "不需要", "不了"]):
        return "skip"
    if any(token in text for token in ["为什么", "为何", "怎么", "什么", "?", "？", "why", "how", "what"]):
        return "question"
    # 肯定词必须是完整匹配（或 + 标点），不能用子串，否则 token 里出现 y/go/ok 也会被误判
    _stripped = text.rstrip(".!。！~～ ")
    if _stripped in {"继续", "开始", "安装", "执行", "导入", "保存", "打开", "可以",
                     "好的", "好", "行", "嗯", "对", "是",
                     "yes", "y", "ok", "okay", "sure", "continue", "go", "yep", "yeah"}:
        return "yes"
    return "text"


class SetupConversation:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.skipped_steps: set[str] = set()
        self.exit_code = 1

    def say(self, message: str) -> None:
        print_markdown_message("Assistant", message, style="magenta")

    def quit_setup(self) -> bool:
        self.exit_code = 0
        self.say("这次 setup 我先停在这里。你之后重新运行 sjtu-agent setup 就能接着配。")
        return False

    def prompt(self) -> str:
        try:
            return input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            self.quit_setup()
            raise SystemExit(0)

    def show_status(self, status: dict) -> None:
        self.say("这是当前的 setup 体检结果。")
        _print_setup_status(status)
        recommendations = _build_recommendations(status)
        if recommendations:
            print_bullets(recommendations, title="Next")
        else:
            self.say("核心配置已经基本齐了。")

    def show_help(self, step: str) -> None:
        self.say("你可以直接用自然语言回答我。常用命令有：status 查看体检，help 查看帮助，skip 跳过当前步骤，quit 退出 setup。")
        if step == "agent":
            self.say("在 LLM 这一步，我会先收集 base URL、API key 和模型名。这样后面你就可以直接进入真正的 agent 对话。")
        if step == "canvas":
            self.say("在 Canvas 这一步，如果 Playwright 和 jAccount 已就绪，我会先直接自动尝试创建；如果失败，你仍然可以 open canvas 或直接粘贴 token。")

    def answer_question(self, raw: str, step: str, status: dict) -> str:
        text = raw.strip().lower()
        paths = describe_runtime_paths()
        if "cookie" in text or "chrome" in text or "登录" in text:
            return "我会从本机 Chrome 里读取你已经登录过的 AI 好课（aihaoke）、物理实验（phycai）、中国大学 MOOC（icourse）cookie，然后写进 config.json。你不用手工拷贝 cookie 值。"
        if "llm" in text or "模型" in text or "api key" in text or "base url" in text:
            return (
                "我会先把驱动 Agent 的大模型接口写进 agent_config.json。"
                "配好之后，你就可以直接运行 sjtu-agent，用完整的 agent 对话继续引导剩余配置。"
            )
        if "canvas" in text or "token" in text:
            if status["canvas"].get("can_auto_fetch"):
                return (
                    "我现在可以先尝试自动创建并保存 Canvas token。"
                    "如果自动流程失败，再回退到手动打开页面和粘贴 token。"
                )
            return (
                f"Canvas token 还是需要在 {status['canvas']['settings_url']} 这个页面里确认生成。"
                "我会尽量缩短步骤：帮你打开页，必要时再让你粘贴 token。"
            )
        if "playwright" in text or "chromium" in text or "浏览器自动化" in text:
            return "Playwright Chromium 用于自动登录、刷新 cookie 和网页抓取。这不会替代你平时使用的 Chrome，只是给 agent 一个可控的浏览器执行环境。"
        if "launchd" in text or "后台" in text or "自动启动" in text or "telegram" in text:
            return "launchd 是 macOS 的后台任务系统。我会把日报、提醒检查和 Telegram bot 安装成用户级 LaunchAgent，方便开机后自动运行。"
        if "文件" in text or "保存" in text or "目录" in text or "在哪里" in text:
            return (
                f"运行时文件保存在 {paths['data_dir']}。其中 config 在 {paths['config_path']}，"
                f"环境变量在 {paths['env_path']}。"
            )
        if step == "jaccount":
            return "jAccount 账号密码主要用来自动刷新 AI 好课（aihaoke）、物理实验（phycai）和水源相关登录态。没有这一步，很多自动化能力会退化成手工配置。"
        if step == "mooc":
            return "MOOC 账号密码只影响 icourse/中国大学 MOOC 这一路。如果你暂时不用这个平台，可以先跳过。"
        if step == "cookies":
            return "cookie 导入会优先补齐 AI 好课（aihaoke）、物理实验（phycai）、中国大学 MOOC（icourse）的现有登录态。如果你还没在 Chrome 里登录这些站点，先登录再回来导入最省事。"
        if step == "launchd":
            return "这一步只是在 macOS 里登记后台服务，不会修改你的课程数据。以后你也可以随时重跑 setup 或 install-daemons 来更新它。"
        return "这一步的目标是把缺的配置补齐。你可以直接继续、跳过，或者输入 status 查看我当前检测到的缺口。"

    def open_canvas_page(self) -> None:
        import agent

        canvas_info = agent.tool_setup_canvas(open_browser=True)
        settings_url = canvas_info.get("settings_url", "https://oc.sjtu.edu.cn/profile/settings")
        self.say(f"Canvas 设置页已经尝试打开：{settings_url}")
        if canvas_info.get("can_auto_fetch"):
            self.say("如果你想让我再自动试一次，直接回复 auto canvas。")
            return
        self.say("你只需要在页面里点 New Access Token，复制弹出的 token 发给我即可。")

    def handle_common(self, raw: str, step: str, status: dict) -> str:
        intent = _classify_reply(raw)
        if intent == "help":
            self.show_help(step)
            return "handled"
        if intent == "status":
            self.show_status(status)
            return "handled"
        if intent == "quit":
            return "quit"
        if intent == "open_canvas":
            self.open_canvas_page()
            return "handled"
        if intent == "auto_canvas":
            return "auto_canvas"
        if intent == "question":
            self.say(self.answer_question(raw, step, status))
            return "handled"
        return intent

    def next_step(self, status: dict, chromium_status: dict[str, object]) -> str | None:
        if not status["agent"]["configured"] and "agent" not in self.skipped_steps:
            return "agent"
        if not chromium_status["ok"] and not self.args.skip_playwright_install and "playwright" not in self.skipped_steps:
            return "playwright"
        if not self.args.skip_credential_prompts and not status["jaccount"]["has_credentials"] and "jaccount" not in self.skipped_steps:
            return "jaccount"
        if not self.args.skip_credential_prompts and not status["icourse"]["has_credentials"] and "mooc" not in self.skipped_steps:
            return "mooc"
        if not self.args.skip_credential_prompts and not status["canvas"]["has_token"] and "canvas" not in self.skipped_steps:
            return "canvas"
        if not self.args.skip_cookie_import and _needs_cookie_import(status) and "cookies" not in self.skipped_steps:
            return "cookies"
        if not self.args.skip_launchd and sys.platform == "darwin" and _ready_for_daemons(status):
            launchd_info = _launchd_state(self.args)
            if not launchd_info["all_present"] and "launchd" not in self.skipped_steps:
                return "launchd"
        return None

    def handle_agent(self, status: dict) -> bool:
        _ZHIYUAN_DEFAULT_BASE = "https://models.sjtu.edu.cn/api/v1"
        _ZHIYUAN_DEFAULT_MODEL = "deepseek-chat"

        import re as _re
        _API_KEY_RE = _re.compile(r'^[A-Za-z0-9_\-]{16,}$')

        self.say("先把驱动 SJTU Agent 的大模型 API 配好。这样后面你可以直接进入真正的 agent 对话，而不是只靠固定问答。")
        self.say("推荐使用交大致远一号 API（OpenAI 兼容接口），Base URL 为 https://models.sjtu.edu.cn/api/v1，模型默认 deepseek-chat。")
        self.say("可用模型：deepseek-chat、deepseek-reasoner、glm-5、minimax、qwen3coder、qwen3vl。")
        self.say("请直接把致远一号 API Key 粘贴进来；如果你现在不想配，也可以回复 skip。")
        while True:
            raw = self.prompt()

            # API Key 优先判断：如果输入看起来像 key（长度 ≥ 16 且只含字母数字 - _），
            # 直接跳过 handle_common 防止 key 中的子串（ok/go/y 等）被误判为 intent。
            stripped = raw.strip()
            if stripped and _API_KEY_RE.match(stripped):
                # 当作 API Key 直接处理，不走 intent 分类
                pass
            else:
                intent = self.handle_common(raw, "agent", status)
                if intent == "handled":
                    continue
                if intent == "quit":
                    return self.quit_setup()
                if intent == "skip":
                    self.skipped_steps.add("agent")
                    self.say("好的，LLM API 这一步先跳过。")
                    return True
                if intent in {"yes", "empty"}:
                    self.say("请直接粘贴你的 API Key（格式：sk-...），或者回复 skip 先跳过。")
                    continue

            api_key = stripped
            if not api_key:
                self.say("没有收到 API Key，请直接粘贴，或者回复 skip。")
                continue

            base_url = _ZHIYUAN_DEFAULT_BASE
            model = _ZHIYUAN_DEFAULT_MODEL

            # ── 保存前先测试连通性 ──────────────────────────────────────────
            self.say("正在测试 API 连接，请稍候…")
            ok, err_msg = _test_llm_connection(base_url, api_key, model)
            if not ok:
                self.say(f"⚠️  连接测试失败：{err_msg}")
                self.say("请检查 API Key 是否正确，然后重新粘贴；或者回复 skip 先跳过。")
                continue

            # ── 保存到 .env（ZHIYUAN_API_KEY），而不是 agent_config.json ──
            env_lines: list = []
            if ENV_PATH.exists():
                env_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

            key_line = f"ZHIYUAN_API_KEY={api_key}"
            updated = False
            for i, line in enumerate(env_lines):
                if line.startswith("ZHIYUAN_API_KEY="):
                    env_lines[i] = key_line
                    updated = True
                    break
            if not updated:
                env_lines.append(key_line)
            ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
            ENV_PATH.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

            import os as _os
            _os.environ["ZHIYUAN_API_KEY"] = api_key

            self.say(f"✅ 连接测试通过，已将 API Key 保存到 .env（ZHIYUAN_API_KEY）。")
            self.say(f"默认模型：{model}，Base URL：{base_url}。")
            self.say("现在你已经具备完整 agent 对话能力了；这个 setup 也会继续帮你把校园平台配置补齐。")

            # 主模型配置成功后，可选配置视觉模型（识图能力）
            if not self._configure_vision_model(status):
                return self.quit_setup()
            return True

    def _configure_vision_model(self, status: dict) -> bool:
        """可选步骤：配置独立视觉模型（用于识图，如 qwen-vl-max）。

        返回 True 表示正常结束（已保存或已跳过）；返回 False 表示用户选择退出 setup。
        """
        self.say("\n接下来是可选的「视觉模型」配置。")
        self.say("如果你的主模型（如 deepseek）不支持识图，可以单独配一个视觉模型（如 qwen-vl-max），"
                 "飞书收到图片时优先用它识图。不想配可以回复 skip。")
        while True:
            raw = self.prompt()
            intent = self.handle_common(raw, "vision", status)
            if intent == "handled":
                continue
            if intent == "quit":
                return False
            if intent == "skip":
                self.say("好的，跳过视觉模型配置（识图将走 OCR 兜底）。")
                return True
            if intent in {"yes", "empty"}:
                break
            self.say("配置视觉模型请输入 y，或回复 skip 跳过。")
            continue

        self.say("视觉模型 Base URL（直接回车使用默认 https://dashscope.aliyuncs.com/compatible-mode/v1）：")
        base_url = self.prompt().strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.say("视觉模型名称（直接回车使用默认 qwen-vl-max）：")
        model = self.prompt().strip() or "qwen-vl-max"
        api_key = _read_secret("视觉模型 API Key（输入不回显）: ").strip()
        if not api_key:
            self.say("没有收到 API Key，跳过视觉模型配置。")
            return True

        self.say("正在测试视觉模型连接，请稍候…")
        ok, err = _test_llm_connection(base_url, api_key, model)
        if not ok:
            self.say(f"视觉模型连接测试失败：{err}（不会保存 key）")
            self.say("你可以稍后重新运行 setup 配置，或手动编辑 agent_config.json 的 vision_model。")
            return True

        saved = _apply_vision_config_updates({
            "enabled": True, "base_url": base_url, "api_key": api_key, "model": model,
        })
        if saved:
            self.say(f"✅ 视觉模型已保存：{saved.get('model')}（enabled=true）")
        return True

    def handle_playwright(self, status: dict) -> bool:
        self.say("我发现 Playwright Chromium 还没准备好。自动登录、cookie 刷新和水源授权都依赖它。")
        self.say("我现在直接替你安装；如果安装失败，我再告诉你手动处理方式。")
        if not _install_playwright_chromium():
            self.say("Playwright Chromium 安装失败了。你可以稍后重试，或者手工运行 python -m playwright install chromium。")
            self.skipped_steps.add("playwright")
            return True
        chromium_status = _check_playwright_chromium()
        if not chromium_status["ok"]:
            self.say("Chromium 安装后仍然不可用。你可以稍后重试，或者手工运行 python -m playwright install chromium。")
            self.skipped_steps.add("playwright")
            return True
        self.say(f"Chromium 已就绪：{chromium_status['detail']}")
        return True

    def handle_jaccount(self, status: dict) -> bool:
        self.say("我建议先补齐 jAccount 用户名和密码。这样我才能自动刷新 AI 好课（aihaoke）、物理实验（phycai）和水源相关登录态。")
        self.say("请直接输入 jAccount 用户名（不是学号！是你登录 my.sjtu.edu.cn 时使用的英文用户名，通常是拼音或姓名缩写，例如 zhangsan），或者回复 skip。")
        while True:
            raw = self.prompt()
            intent = self.handle_common(raw, "jaccount", status)
            if intent == "handled":
                continue
            if intent == "quit":
                return self.quit_setup()
            if intent == "skip":
                self.skipped_steps.add("jaccount")
                self.say("好的，jAccount 这一步先不配。")
                return True
            if intent == "empty":
                self.say("我还没有拿到用户名。请输入你登录 my.sjtu.edu.cn 时的英文用户名（通常是拼音，不是学号），或者说 skip。")
                continue

            username = raw
            self.say("接下来请输入 jAccount 密码。我会隐藏输入；如果直接回车，我就视为这一步取消。")
            password = _read_secret("Password: ").strip()
            if not password:
                self.skipped_steps.add("jaccount")
                self.say("没有收到密码，我先不保存 jAccount。")
                return True
            result = _apply_credential_updates(
                {
                    "jaccount_username": username,
                    "jaccount_password": password,
                    "mooc_username": "",
                    "mooc_password": "",
                    "canvas_token": "",
                }
            )
            saved = ", ".join(result.get("saved", [])) if result else ""
            self.say(f"已保存：{saved or 'jAccount 信息'}。")
            return True

    def handle_mooc(self, status: dict) -> bool:
        self.say("我还缺 icourse 对应的 MOOC 账号密码。如果你平时要查 icourse，这一步现在补上最省事；不用的话可以跳过。")
        self.say("请直接输入 MOOC 用户名，或者回复 skip。")
        while True:
            raw = self.prompt()
            intent = self.handle_common(raw, "mooc", status)
            if intent == "handled":
                continue
            if intent == "quit":
                return self.quit_setup()
            if intent == "skip":
                self.skipped_steps.add("mooc")
                self.say("好的，MOOC 这一步先跳过。")
                return True
            if intent == "empty":
                self.say("如果你现在不想配置，直接回复 skip 就行。")
                continue

            username = raw
            self.say("接下来请输入 MOOC 密码。我会隐藏输入；留空表示取消这一步。")
            password = _read_secret("Password: ").strip()
            if not password:
                self.skipped_steps.add("mooc")
                self.say("没有收到密码，我先不保存 MOOC。")
                return True
            result = _apply_credential_updates(
                {
                    "jaccount_username": "",
                    "jaccount_password": "",
                    "mooc_username": username,
                    "mooc_password": password,
                    "canvas_token": "",
                }
            )
            saved = ", ".join(result.get("saved", [])) if result else ""
            self.say(f"已保存：{saved or 'MOOC 信息'}。")
            return True

    def handle_canvas(self, status: dict) -> bool:
        import agent

        def attempt_auto_canvas() -> bool:
            self.say("我现在开始自动尝试创建 Canvas token。你会先看到一个浏览器窗口；如果自动流程失败，我会回退到手动方式。")
            auto_result = agent.tool_setup_canvas(open_browser=False, auto_create=True)
            if auto_result.get("success"):
                self.say("Canvas token 已经自动创建并保存好了。")
                return True
            self.say(f"自动创建没有成功：{auto_result.get('error', '未知错误')}。")
            self.say("我现在回退到手动方式。你可以直接把 token 粘贴给我，或者回复 open canvas 打开设置页。")
            return False

        canvas_info = agent.tool_setup_canvas(open_browser=False)
        settings_url = canvas_info.get("settings_url", "https://oc.sjtu.edu.cn/profile/settings")
        if canvas_info.get("can_auto_fetch"):
            self.say("Canvas 这一步我会先直接替你自动尝试创建 token；如果失败，再回退到手动。")
            if attempt_auto_canvas():
                return True
        else:
            reason = canvas_info.get("auto_fetch_reason") or "当前还不满足自动创建条件"
            self.say("Canvas 这一步暂时还不能自动完成。")
            self.say(f"原因：{reason}。你可以直接把 token 粘贴给我，或者回复 open canvas 打开 {settings_url}。")
        while True:
            raw = self.prompt()
            intent = self.handle_common(raw, "canvas", status)
            if intent == "handled":
                continue
            if intent == "quit":
                return self.quit_setup()
            if intent == "skip":
                self.skipped_steps.add("canvas")
                self.say("好，Canvas token 这一步先跳过。")
                return True
            if intent == "auto_canvas":
                if attempt_auto_canvas():
                    return True
                continue
            if intent == "yes":
                self.open_canvas_page()
                self.say("页面打开后，你可以自己复制 token 发给我；如果你想让我再自动试一次，也可以回复 auto canvas。")
                continue
            if intent == "empty":
                if canvas_info.get("can_auto_fetch"):
                    self.say("如果你想让我再自动试一次，直接回复 auto canvas；否则可以 open canvas、直接粘贴 token，或者 skip。")
                else:
                    self.say("如果你还没准备好 token，可以回复 open canvas 或者 skip。")
                continue

            result = _apply_credential_updates(
                {
                    "jaccount_username": "",
                    "jaccount_password": "",
                    "mooc_username": "",
                    "mooc_password": "",
                    "canvas_token": raw,
                }
            )
            verify = agent.tool_setup_canvas(open_browser=False)
            if verify.get("existing_token_valid") is False:
                self.say("我已经保存这个 Canvas token，但接口校验没有通过。常见原因是复制不完整、token 失效，或者不是当前学校 Canvas 的 token。")
            else:
                saved = ", ".join(result.get("saved", [])) if result else "Canvas token"
                self.say(f"已保存：{saved}。")
            return True

    def handle_cookies(self, status: dict) -> bool:
        missing = []
        if not status["aihaoke"]["has_cookies"]:
            missing.append("AI 好课")
        if not status["phycai"]["has_cookies"]:
            missing.append("物理实验")
        if not status["icourse"]["has_cookies"]:
            missing.append("中国大学 MOOC")
        missing_text = ", ".join(missing) if missing else "课程平台"
        self.say(f"我还缺这些站点的登录态：{missing_text}。如果你已经在 Chrome 里登录过，我现在可以自动导入。")
        self.say("准备好了就回复继续；如果你想稍后自己登录 Chrome 再回来，回复 skip。")
        while True:
            raw = self.prompt()
            intent = self.handle_common(raw, "cookies", status)
            if intent == "handled":
                continue
            if intent == "quit":
                return self.quit_setup()
            if intent == "skip":
                self.skipped_steps.add("cookies")
                self.say("好的，cookie 导入这一步先跳过。")
                return True
            if intent in {"yes", "empty", "text"}:
                try:
                    _import_browser_cookies()
                    self.say("cookie 已导入到 config.json。")
                except RuntimeError as exc:
                    self.say(f"cookie 导入失败：{exc}")
                # 无论导入成功与否，都标记为已处理，避免因某个平台未登录 Chrome 而死循环
                self.skipped_steps.add("cookies")
                return True
            self.say("这一步你可以回复继续或者 skip。")

    def handle_launchd(self, status: dict) -> bool:
        launchd_info = _launchd_state(self.args)
        existing = [item["label"] for item in launchd_info["existing"]]
        missing = [item["label"] for item in launchd_info["missing"]]
        if existing:
            self.say(f"我已经检测到这些 LaunchAgent 文件：{', '.join(existing)}。")
        if missing:
            self.say(f"这些后台服务还没装好：{', '.join(missing)}。")
        self.say("如果你愿意，我现在就把日报、提醒和 Telegram bot 的 macOS 后台服务写好并装载。")
        while True:
            raw = self.prompt()
            intent = self.handle_common(raw, "launchd", status)
            if intent == "handled":
                continue
            if intent == "quit":
                return self.quit_setup()
            if intent == "skip":
                self.skipped_steps.add("launchd")
                self.say("好，后台服务这一步先不动。")
                return True
            if intent in {"yes", "empty", "text"}:
                try:
                    payload = _install_launchd(self.args)
                except RuntimeError as exc:
                    self.say(f"后台服务安装失败：{exc}")
                    self.skipped_steps.add("launchd")
                    return True
                self.say(f"后台服务已经写入 {payload['output_dir']}。")
                for service in payload["services"]:
                    print(f"- {service['label']} -> {service['plist_path']}")
                return True
            self.say("这一步你可以回复继续或者 skip。")

    def finish(self) -> int:
        _maybe_create_skeleton_config()
        status = _doctor_status()
        self.say("这轮 setup 我已经带你走完了。最后给你一个收口摘要。")
        _print_setup_status(status)
        if sys.platform == "darwin" and _launchd_state(self.args)["all_present"]:
            self.say("macOS 后台服务也已经就绪。")

        recommendations = _build_recommendations(status)
        if recommendations:
            print("\nNext:")
            for item in recommendations:
                print(f"- {item}")
        else:
            self.say("核心配置已经齐了，你现在可以直接开始使用。")

        if not status["agent"]["configured"]:
            self.say(f"模型 API 还没配好，所以这次还不能直接启动智能体。配好后直接运行 sjtu-agent 就能进入主对话。")
            self.say("如果以后想重新检查环境，直接运行 sjtu-agent setup 或 sjtu-agent doctor。")
            return 0

        if recommendations:
            self.say("虽然还有一些校园平台配置可以继续补，但智能体已经可以启动了。")

        self.say("如果你愿意，我现在就直接启动 SJTU Agent 主对话。你也可以先结束 setup，之后手动运行 sjtu-agent。")
        self.say("你可以回复继续启动，或者回复 skip 先结束 setup。")
        while True:
            raw = self.prompt()
            intent = self.handle_common(raw, "finish", status)
            if intent == "handled":
                continue
            if intent in {"quit", "skip"}:
                if intent == "quit":
                    self.quit_setup()
                    return 0
                self.say("好的，这次 setup 到这里结束。之后你随时可以直接运行 sjtu-agent。")
                self.say("如果以后想重新检查环境，直接运行 sjtu-agent setup 或 sjtu-agent doctor。")
                return 0
            if intent in {"yes", "empty"}:
                return self.launch_main_agent()
            self.say("你可以回复继续启动，或者回复 skip 先结束 setup。")

    def launch_main_agent(self) -> int:
        self.say("正在启动 SJTU Agent 主对话。之后你可以直接描述需求，或者输入 /help 查看命令。")
        old_argv = sys.argv[:]
        sys.argv = ["agent"]
        try:
            from sjtu_agent.agent.chat_loop import main as agent_main
            agent_main()
            return 0
        except SystemExit as exc:
            code = exc.code
            return code if isinstance(code, int) else 0
        finally:
            sys.argv = old_argv

    def run(self) -> int:
        self.say("我是 SJTU Agent 的 setup assistant。我会先把模型 API 配好，再按缺口一步一步带你完成校园平台配置。")
        self.say("过程中你可以随时输入 status、help、skip 或 quit。")
        _print_runtime_summary()

        self.say("我先完成基础依赖检查。")
        dependency_results, fatal_errors = _dependency_checks()
        for result in dependency_results:
            _print_check(str(result["name"]), bool(result["ok"]), str(result["detail"]))
        if fatal_errors:
            self.say("当前还有硬性依赖缺失，所以这轮 setup 不能继续。")
            for item in fatal_errors:
                print(f"- {item}")
            self.say("先把这些依赖补齐，再重新运行 sjtu-agent setup。")
            return 1

        chromium_status = _check_playwright_chromium()
        _print_check("Chromium browser", bool(chromium_status["ok"]), str(chromium_status["detail"]))

        initial_agent_save = _apply_agent_config_updates(_cli_agent_updates(self.args))
        if initial_agent_save:
            self.say(f"我已经先保存了命令行里的模型配置：{initial_agent_save['model']} @ {initial_agent_save['base_url']}。")

        initial_updates = _cli_credential_updates(self.args)
        initial_save = _apply_credential_updates(initial_updates)
        if initial_save:
            self.say("我已经先保存了你通过命令行传进来的凭据。")

        status = _doctor_status()
        while True:
            step = self.next_step(status, chromium_status)
            if step is None:
                break
            handler = getattr(self, f"handle_{step}")
            if not handler(status):
                return self.exit_code
            chromium_status = _check_playwright_chromium()
            status = _doctor_status()

        return self.finish()


def run_setup_wizard(args: argparse.Namespace) -> int:
    if args.assume_yes:
        return _run_automatic_setup(args)
    return SetupConversation(args).run()


def register_setup_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], parse_hhmm) -> None:
    parser = subparsers.add_parser(
        "setup",
        help="run the conversational first-run setup assistant",
    )
    parser.add_argument("--yes", dest="assume_yes", action="store_true", help="accept default yes/no prompts")
    parser.add_argument(
        "--skip-playwright-install",
        action="store_true",
        help="do not try to install Playwright Chromium automatically",
    )
    parser.add_argument(
        "--skip-cookie-import",
        action="store_true",
        help="skip importing cookies from local Chrome",
    )
    parser.add_argument(
        "--skip-credential-prompts",
        action="store_true",
        help="do not ask for jAccount, MOOC, or Canvas inputs",
    )
    parser.add_argument(
        "--skip-launchd",
        action="store_true",
        help="skip macOS launchd installation",
    )
    parser.add_argument(
        "--write-daemons-only",
        action="store_true",
        help="write launchd plist files without loading them",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="directory where service files will be written (default: platform standard path)",
    )
    parser.add_argument(
        "--python-executable",
        default=sys.executable,
        help="python executable that launchd should use",
    )
    parser.add_argument(
        "--services",
        nargs="+",
        choices=available_service_names(),
        help="subset of background services to install",
    )
    parser.add_argument(
        "--daily-report-time",
        type=parse_hhmm,
        default=(22, 0),
        help="daily report schedule in HH:MM, default 22:00",
    )
    parser.add_argument(
        "--remind-interval",
        type=int,
        default=60,
        help="reminder daemon interval in seconds, default 60",
    )
    parser.add_argument(
        "--telegram-throttle",
        type=int,
        default=10,
        help="launchd throttle interval for telegram bot restarts, default 10",
    )
    parser.add_argument("--jaccount-username", default="", help="jAccount username to save")
    parser.add_argument("--jaccount-password", default="", help="jAccount password to save")
    parser.add_argument("--mooc-username", default="", help="MOOC username to save")
    parser.add_argument("--mooc-password", default="", help="MOOC password to save")
    parser.add_argument("--canvas-token", default="", help="Canvas API token to save")
    parser.add_argument("--llm-base-url", default="", help="LLM API base URL to save")
    parser.add_argument("--llm-api-key", default="", help="LLM API key to save")
    parser.add_argument("--llm-model", default="", help="LLM model name to save")
    parser.set_defaults(func=run_setup_wizard)
