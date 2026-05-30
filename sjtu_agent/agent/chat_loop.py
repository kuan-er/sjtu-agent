"""sjtu_agent/agent/chat_loop.py — 配置加载、聊天主循环、程序入口。"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import datetime as _dt
from pathlib import Path
from dotenv import load_dotenv

from sjtu_agent.paths import AGENT_CONFIG_PATH, ENV_PATH, DDL_CACHE_PATH
from sjtu_agent.terminal_ui import print_markdown_message, print_rule
from sjtu_agent.agent.prompts import SYSTEM_PROMPT
from sjtu_agent.agent.runner import _make_client, _run_one_turn, _is_anthropic_model, Spinner
from sjtu_agent.agent.tools import TOOLS, run_tool, _fetch_ddls_parallel, _ddl_cache_get, tool_check_setup, _load_reminders

load_dotenv(ENV_PATH)

_ZHIYUAN_BASE_URL_ENV  = "ZHIYUAN_BASE_URL"
_ZHIYUAN_API_KEY_ENV   = "ZHIYUAN_API_KEY"
_ZHIYUAN_DEFAULT_BASE  = "https://models.sjtu.edu.cn/api/v1"
_ZHIYUAN_DEFAULT_MODEL = "deepseek-chat"


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



def load_agent_config() -> dict:
    """加载 Agent LLM 配置，优先级：agent_config.json > 致远一号环境变量 > 空配置。

    agent_config.json 是用户在 web UI / setup 中显式配置的，最高优先级。
    ZHIYUAN_API_KEY 仅作为没有显式配置时的 fallback，避免环境变量永久劫持配置。
    """
    # 1. 优先：用户显式配置（web UI / setup_wizard 写入）
    if AGENT_CONFIG_PATH.exists():
        try:
            cfg = json.loads(AGENT_CONFIG_PATH.read_text())
            if str(cfg.get("provider", "")).lower() == "custom":
                return cfg if cfg.get("api_key") and cfg.get("model") else {}
            if cfg.get("api_key") and cfg.get("model"):
                return cfg
        except (json.JSONDecodeError, OSError):
            pass
    # 2. fallback：致远一号 / DeepSeek 环境变量
    zhiyuan_base = os.environ.get(_ZHIYUAN_BASE_URL_ENV, "").strip()
    zhiyuan_key  = os.environ.get(_ZHIYUAN_API_KEY_ENV, "").strip()
    if zhiyuan_key:
        return {
            "base_url": zhiyuan_base or _ZHIYUAN_DEFAULT_BASE,
            "api_key":  zhiyuan_key,
            "model":    _ZHIYUAN_DEFAULT_MODEL,
            "_source":  "zhiyuan_env",
        }
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        return {
            "base_url": "https://api.deepseek.com",
            "api_key":  deepseek_key,
            "model":    "deepseek-chat",
            "_source":  "deepseek_env",
        }
    return {}


def _test_llm_connection_simple(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """测试 LLM API 连接是否正常。返回 (ok, error_msg)。"""
    _url = base_url.strip().rstrip("/")
    if _url and not _url.startswith(("http://", "https://")):
        return False, f"Base URL 格式不正确（缺少 http:// 或 https://）：{_url!r}"
    if not api_key.strip():
        return False, "API Key 为空"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key.strip(), base_url=_url or None)
        client.chat.completions.create(
            model=model.strip(),
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            timeout=15,
        )
        return True, ""
    except Exception as e:
        err = str(e)
        if "Connection error" in err or "UnsupportedProtocol" in err or "missing an 'http" in err:
            return False, f"无法连接到 API（{_url or 'openai 官方'}），请检查 Base URL"
        if "401" in err or "Unauthorized" in err or "Invalid API key" in err.lower():
            return False, "API Key 无效或已失效"
        if "timeout" in err.lower() or "timed out" in err.lower():
            return False, "连接超时（15s），请检查网络或 Base URL"
        return False, f"连接失败：{err[:120]}"


def setup_agent_config() -> dict:
    print("\n=== SJTU DDL Agent 首次配置 ===")
    print("请填写用于驱动 Agent 的大模型 API 信息")
    print("推荐选项：")
    print("  1) 交大致远一号：https://models.sjtu.edu.cn/api/v1（免费）")
    print("  2) DeepSeek 官方：https://api.deepseek.com（更快更稳定）")
    print("  3) 其他 OpenAI 兼容接口（OpenAI、学校超算集群等）")
    print("\n直接粘贴 API Key 即可自动识别（Base URL 自动匹配）。")
    print("输入 quit / skip 可跳过配置直接进入 Agent（功能受限）\n")

    def _prompt(msg: str) -> str:
        """带退出检测的 input，Ctrl+C / EOF / quit / skip 均触发跳出。"""
        try:
            val = input(msg).strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(0)
        if val.lower() in ("quit", "exit", "skip", "q"):
            raise SystemExit(0)
        return val

    def _mask_secret(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"

    def _ask_keep(label: str, value: str, *, secret: bool = False) -> bool:
        shown = _mask_secret(value) if secret else value
        while True:
            ans = _prompt(f"检测到已配置 {label}: {shown}，是否保留？[Y/n]: ").lower()
            if ans in {"", "y", "yes"}:
                return True
            if ans in {"n", "no"}:
                return False
            print("请输入 y 或 n。")

    current = load_agent_config()
    base_url = str(current.get("base_url", "")).strip()
    api_key = str(current.get("api_key", "")).strip()
    model = str(current.get("model", "")).strip() or "deepseek-chat"

    if base_url or api_key or model:
        print("检测到已有模型配置，下面逐项询问是否保留。")
        try:
            if base_url and not _ask_keep("API Base URL", base_url):
                base_url = ""
            if api_key and not _ask_keep("API Key", api_key, secret=True):
                api_key = ""
            if model and not _ask_keep("模型名称", model):
                model = "deepseek-chat"
        except SystemExit:
            print("\n已跳过 API 配置。部分依赖 LLM 的功能将不可用。")
            print("你可以后续运行 sjtu-agent setup 补充配置，或使用 /model 命令修改。\n")
            return {"base_url": "", "api_key": "", "model": "deepseek-chat"}

    while True:
        try:
            base_input = _prompt(
                f"API Base URL（如 https://api.openai.com/v1，回车保留当前: {base_url or 'https://api.openai.com/v1'}）: "
            )
            if base_input:
                base_url = base_input

            key_hint = _mask_secret(api_key) if api_key else "未设置"
            key_input = _prompt(f"API Key（回车保留当前: {key_hint}）: ")
            if key_input:
                api_key = key_input

            model_input = _prompt(
                f"模型名称（如 deepseek-chat / deepseek-v4-pro，回车保留当前: {model or 'deepseek-chat'}）: "
            )
            if model_input:
                model = model_input
            if not model:
                model = "deepseek-chat"
        except SystemExit:
            print("\n已跳过 API 配置。部分依赖 LLM 的功能将不可用。")
            print("你可以后续运行 sjtu-agent setup 补充配置，或使用 /model 命令修改。\n")
            # 返回一个"空"配置，让 chat_loop 仍可启动（工具调用不受影响）
            return {"base_url": "", "api_key": "", "model": "deepseek-chat"}

        if not api_key:
            print("\n⚠️  API Key 不能为空。")
            print("请重新输入（直接回车可重用上次输入的值；输入 quit 可跳过配置）\n")
            continue

        resolved_url = base_url or "https://api.openai.com/v1"
        print("正在测试 API 连接，请稍候…", end="", flush=True)
        ok, err_msg = _test_llm_connection_simple(resolved_url, api_key, model)
        if ok:
            print(" ✅ 连接成功")
            break
        print(f"\n⚠️  连接测试失败：{err_msg}")
        print("请重新输入（直接回车可重用上次输入的值；输入 quit 可跳过配置）\n")

    cfg = {
        "base_url": resolved_url,
        "api_key":  api_key,
        "model":    model,
    }
    AGENT_CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    print("\nAgent 配置已保存。\n")
    return cfg


# ══════════════════════════════════════════════════════════════════════════════
# 主聊天循环
# ══════════════════════════════════════════════════════════════════════════════


def chat_loop(client, model: str):
    import datetime as _dt
    _now = _dt.datetime.now()
    _year = _now.year
    _month = _now.month
    # 判断当前学期：9-1月=第1学期(秋), 2-6月=第2学期(春), 7-8月=第3学期(夏)
    if _month >= 9:
        _cur_xnm = _year       # 如 2025（即2025-2026学年）
        _cur_xqm = "1"
        _prev_xnm = _year - 1  # 上学期 = 上一学年第2学期
        _prev_xqm = "2"
    elif _month <= 6:
        _cur_xnm = _year - 1   # 如 2025（即2025-2026学年）
        _cur_xqm = "2"
        _prev_xnm = _year - 1  # 上学期 = 同一学年第1学期
        _prev_xqm = "1"
    else:  # 7-8月
        _cur_xnm = _year - 1
        _cur_xqm = "3"
        _prev_xnm = _year - 1
        _prev_xqm = "2"

    _date_ctx = (
        f"\n\n## 当前时间（自动注入，每次对话刷新）\n"
        f"现在：{_now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[_now.weekday()]}。\n"
        f"当前学期：{_cur_xnm}-{_cur_xnm+1}学年第{_cur_xqm}学期。\n"
        f"「上学期」= {_prev_xnm}-{_prev_xnm+1}学年第{_prev_xqm}学期"
        f"（query_grades: year='{_prev_xnm}', semester='{_prev_xqm}'）。\n"
        f"「本学期」= {_cur_xnm}-{_cur_xnm+1}学年第{_cur_xqm}学期"
        f"（query_grades: year='{_cur_xnm}', semester='{_cur_xqm}'）。\n"
        f"「本学年」= {_cur_xnm}学年"
        f"（query_grades: year='{_cur_xnm}', semester=''）。"
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT + _date_ctx}]
    model_box  = [model]   # 用列表包裹使内部可修改
    client_box = [client]  # 同理，切换模型时可替换 client

    # ── 启动时后台预热 DDL 缓存 + 检查更新（完全不阻塞主线程）──────────────────
    _prefetch_ddls_background()
    _update_thread = threading.Thread(target=_check_for_updates, daemon=True)
    _update_thread.start()
    # 不在这里 join()，避免 git fetch 慢网络时卡住启动

    # ── 启动检查：直接调本地函数，无需 LLM roundtrip ─────────────────────────

    print("正在检查配置状态…", flush=True)
    setup = tool_check_setup()
    all_ok = (
        setup["jaccount"]["has_credentials"]
        and setup["canvas"]["has_token"]
        and setup["aihaoke"]["has_cookies"]
        and setup["phycai"]["has_cookies"]
        and setup["icourse"]["has_cookies"]
    )
    if all_ok:
        uname = setup["jaccount"].get("username") or ""
        print(f"✅ 所有平台已就绪（{uname}）\n")
        print("输入问题继续对话，输入 quit 退出。\n")
    else:
        # 有未完成配置，让 LLM 引导
        setup_json = json.dumps(setup, ensure_ascii=False)
        messages.append({
            "role": "user",
            "content": f"配置检查结果：{setup_json}\n请根据结果告知我缺少哪些配置，并引导我完成设置。",
        })
        _run_one_turn(client_box[0], model_box[0], messages)
        print("输入问题继续对话，输入 quit 退出。\n")

    # ── 启动时检查即将到期的提醒事项（30分钟内）────────────────────────────
    import datetime as _dt2
    _CST = _dt2.timezone(_dt2.timedelta(hours=8))
    _now2 = _dt2.datetime.now(_CST)
    _soon = _now2 + _dt2.timedelta(minutes=30)
    _due_reminders = []
    for _r in _load_reminders():
        for _key in ("start", "end"):
            _ts = _r.get(_key, "")
            if not _ts:
                continue
            try:
                _rdt = _dt2.datetime.fromisoformat(_ts)
                if _rdt.tzinfo is None:
                    _rdt = _rdt.replace(tzinfo=_CST)
                if _now2 <= _rdt <= _soon:
                    _due_reminders.append(
                        f"  ⏰ 【{_r['title']}】{'开始' if _key=='start' else '结束'}"
                        f" 于 {_rdt.strftime('%H:%M')}"
                        + (f"（{_r['note']}）" if _r.get("note") else "")
                    )
            except Exception:
                pass
    if _due_reminders:
        print_rule("即将到期的提醒事项（30分钟内）")
        for _line in _due_reminders:
            print(_line)
        print()

    # ── 在第一次等待用户输入前，最多等 2 秒看更新检查结果 ─────────────────
    _update_thread.join(timeout=2)
    if _UPDATE_AVAILABLE.get("behind"):
        behind = _UPDATE_AVAILABLE["behind"]
        print(f"💡 有 {behind} 个新提交可用，运行 sjtu-agent update 即可一键更新。\n")

    while True:
        try:
            user_input = input(f"你[{model_box[0]}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "退出", "q"):
            print("再见！")
            break

        # 断言命令
        if user_input.startswith("/model"):
            print_rule("切换模型配置")
            cur = load_agent_config()
            new_base  = input(f"API Base URL（当前: {cur.get('base_url','')}，回车不变）: ").strip()
            new_key   = input(f"API Key（当前: {'*'*8 if cur.get('api_key') else '未设置'}，回车不变）: ").strip()
            new_model = input(f"模型名称（当前: {cur.get('model','')}，回车不变）: ").strip()
            updated = {
                "base_url": new_base  or cur.get("base_url", "https://api.openai.com/v1"),
                "api_key":  new_key   or cur.get("api_key", ""),
                "model":    new_model or cur.get("model", "deepseek-chat"),
            }
            AGENT_CONFIG_PATH.write_text(json.dumps(updated, indent=2, ensure_ascii=False))
            client_box[0] = _make_client(updated)
            model_box[0] = updated["model"]
            # 切换协议时重置对话，避免消息格式冲突
            messages.clear()
            messages.append({"role": "system", "content": SYSTEM_PROMPT})
            proto = "Anthropic" if _is_anthropic_model(updated["model"]) else "OpenAI"
            print(f"  已切换到: {updated['model']}  [协议: {proto}]（已保存，对话已重置）\n")
            continue

        messages.append({"role": "user", "content": user_input})
        try:
            _run_one_turn(client_box[0], model_box[0], messages)
        except KeyboardInterrupt:
            print("\n[已中断当前请求，可继续输入]")
            # 移除未完成的 user 消息，保持历史干净
            if messages and messages[-1].get("role") == "user":
                messages.pop()
        except Exception as e:
            print(f"\r[错误] 本轮请求失败（{type(e).__name__}: {e}），请重新输入。")
            # 移除未完成的 user 消息
            if messages and messages[-1].get("role") == "user":
                messages.pop()


def main():
    cfg = load_agent_config()
    if not cfg or not cfg.get("api_key"):
        cfg = setup_agent_config()
    client = _make_client(cfg)
    chat_loop(client, cfg["model"])


if __name__ == "__main__":
    main()
