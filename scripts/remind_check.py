#!/usr/bin/env python3
"""
remind_check.py — 后台提醒检查脚本，由 launchd 每分钟调用。

逻辑：
  - 读取 reminders.json
  - 若某条提醒的 start 或 end 距现在不超过 NOTIFY_WINDOW 分钟，发送 macOS 通知
    - 若发现未提交作业已进入最后 DDL_GUARD_WINDOW_MINUTES 分钟，触发紧急保底提醒
  - 用 remind_state.json 记录已发送的通知，避免重复弹出
  - 若提醒已过期超过 EXPIRE_CLEANUP_HOURS 小时，自动从列表移除

用法：
  python3 remind_check.py            # 正常后台运行（launchd 调用）
  python3 remind_check.py --test     # 打印将要触发的通知，不实际弹出
  python3 remind_check.py --list     # 打印当前所有提醒状态

可选配置（config.json）：
    ddl_deadline_guard_enabled     : 是否启用 DDL 紧急保底提醒，默认 true
    ddl_deadline_guard_minutes     : 距截止多少分钟内触发，默认 5
    ddl_deadline_guard_open_canvas : 触发时是否自动打开 Canvas 提交页，默认 true
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sjtu_agent.paths import CONFIG_PATH, REMINDERS_PATH, REMIND_CHECK_LOG_PATH, REMIND_STATE_PATH
from sjtu_agent.config import cfg as _cfg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ddl_checker as dc

# ── 配置 ────────────────────────────────────────────────────────────────────
STATE_PATH         = REMIND_STATE_PATH   # 记录已发送通知
LOG_PATH           = REMIND_CHECK_LOG_PATH

NOTIFY_WINDOW      = 30    # 提前多少分钟发通知
EXPIRE_CLEANUP_HOURS = 24  # 过期超过多少小时后自动清理
DDL_GUARD_WINDOW_MINUTES = 5

CST = timezone(timedelta(hours=8))

# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # 限制日志大小：超过 200KB 截断到最后 100KB
        if LOG_PATH.stat().st_size > 200 * 1024:
            content = LOG_PATH.read_bytes()
            LOG_PATH.write_bytes(content[-100 * 1024:])
    except Exception:
        pass


def _load_reminders() -> list[dict]:
    if not REMINDERS_PATH.exists():
        return []
    try:
        return json.loads(REMINDERS_PATH.read_text(encoding="utf-8")).get("reminders", [])
    except Exception as e:
        _log(f"读取 reminders.json 失败: {e}")
        return []


def _save_reminders(reminders: list[dict]) -> None:
    REMINDERS_PATH.write_text(
        json.dumps({"reminders": reminders}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_state() -> dict:
    from sjtu_agent.paths import read_json_safe
    return read_json_safe(STATE_PATH, default={})


def _save_state(state: dict) -> None:
    """原子写入；失败时仍抛出异常，让调用方决定是否回滚已发送标记。"""
    from sjtu_agent.paths import atomic_write_json
    atomic_write_json(STATE_PATH, state)


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt
    except Exception:
        return None


def _load_cfg() -> dict:
    _cfg.reload_if_changed()
    return _cfg.raw()


def _open_url(url: str) -> None:
    if not url:
        return
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=True, capture_output=True, timeout=5)
        elif sys.platform == "win32":
            os.startfile(url)
        else:
            subprocess.run(["xdg-open", url], check=True, capture_output=True, timeout=5)
    except Exception as e:
        _log(f"打开 URL 失败: {url} ({e})")


def _ddl_guard_key(item: dict) -> str:
    due = item.get("due")
    if isinstance(due, datetime):
        due_text = due.isoformat()
    else:
        due_text = str(due)
    return f"ddl-guard:{item.get('platform','')}:{item.get('course','')}:{item.get('name','')}:{due_text}"


def _load_pending_ddls(cfg: dict) -> list[dict]:
    items: list[dict] = []
    try:
        items.extend(dc.fetch_canvas(cfg))
    except Exception as e:
        _log(f"Canvas DDL 检查失败: {e}")
    try:
        items.extend(dc.fetch_aihaoke(cfg))
    except Exception as e:
        _log(f"aihaoke DDL 检查失败: {e}")
    try:
        items.extend(dc.fetch_icourse(cfg))
    except Exception as e:
        _log(f"icourse DDL 检查失败: {e}")
    return [item for item in items if not item.get("submitted")]


def _check_deadline_guard(state: dict, test_mode: bool = False) -> bool:
    cfg = _load_cfg()
    if cfg.get("ddl_deadline_guard_enabled", True) is False:
        return False

    window_minutes = int(cfg.get("ddl_deadline_guard_minutes", DDL_GUARD_WINDOW_MINUTES))
    auto_open_canvas = bool(cfg.get("ddl_deadline_guard_open_canvas", True))

    now = datetime.now(CST)
    window = timedelta(minutes=window_minutes)
    changed_state = False

    for item in _load_pending_ddls(cfg):
        due = item.get("due")
        if not isinstance(due, datetime):
            continue
        if not (now <= due <= now + window):
            continue

        key = _ddl_guard_key(item)
        if key in state:
            continue

        minutes_left = max(0, int((due - now).total_seconds() // 60))
        title = f"{item.get('course', '未知课程')} · {item.get('name', '未知作业')}"
        subtitle = f"还有 {minutes_left} 分钟截止，当前仍未提交"
        lines = [f"平台：{item.get('platform', '未知平台')}"]

        assignment_url = item.get("url", "") if item.get("platform") == "Canvas" else ""
        if assignment_url:
            lines.append(f"提交页：{assignment_url}")

        lines.append("说明：系统不会代写或自动提交，只会在最后时刻提醒你并尽量把入口打开。")
        body = "\n".join(lines)

        _log(f"{'[TEST] ' if test_mode else ''}触发 DDL 紧急保底提醒: {title} — {subtitle}")
        if not test_mode:
            _send_notification("⚠️ DDL 紧急保底提醒", subtitle, f"{title}\n{body}")
            if assignment_url and auto_open_canvas:
                _open_url(assignment_url)

        state[key] = now.isoformat()
        changed_state = True

    return changed_state


def _send_notification(title: str, subtitle: str, body: str) -> None:
    """同时推送系统通知 + Telegram 消息（若已配置）。支持 macOS / Windows / Linux。"""
    # ── 跨平台系统通知 ────────────────────────────────────────────────────
    message = f"{subtitle}\n{body}" if body else subtitle
    try:
        from plyer import notification as _plyer_notif  # type: ignore
        _plyer_notif.notify(
            title=title,
            message=message,
            app_name="SJTU Agent",
            timeout=10,
        )
    except Exception:
        # plyer 不可用时，降级到各平台原生方式
        try:
            if sys.platform == "darwin":
                def esc(s: str) -> str:
                    return s.replace("\\", "\\\\").replace('"', '\\"')
                script = (
                    f'display notification "{esc(body)}" '
                    f'with title "{esc(title)}" '
                    f'subtitle "{esc(subtitle)}"'
                )
                subprocess.run(["osascript", "-e", script],
                               check=True, capture_output=True, timeout=5)
            elif sys.platform == "win32":
                # Windows 10+ 内置 PowerShell 通知 — 用环境变量传参，防注入
                ps_script = (
                    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                    "ContentType = WindowsRuntime] | Out-Null; "
                    "$t = $env:SJTU_TITLE; $m = $env:SJTU_MESSAGE; "
                    "$template = [Windows.UI.Notifications.ToastNotificationManager]"
                    "::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                    '$template.GetElementsByTagName("text")[0].AppendChild($template.CreateTextNode($t)) | Out-Null; '
                    '$template.GetElementsByTagName("text")[1].AppendChild($template.CreateTextNode($m)) | Out-Null; '
                    "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
                    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('SJTU Agent').Show($toast)"
                )
                env = {**os.environ, "SJTU_TITLE": title, "SJTU_MESSAGE": message}
                subprocess.run(["powershell", "-Command", ps_script],
                               capture_output=True, timeout=10, env=env)
            else:
                subprocess.run(["notify-send", title, message],
                               check=True, capture_output=True, timeout=5)
        except Exception as e:
            _log(f"系统通知发送失败: {e}")

    # ── Telegram 推送 ─────────────────────────────────────────────────────
    try:
        cfg = _load_cfg()
        if not cfg.get("telegram_enabled", True):
            return
        token       = cfg.get("telegram_token", "")
        allowed_ids = [int(x) for x in cfg.get("telegram_allowed_ids", [])]
        if not token or not allowed_ids:
            return
        import urllib.request
        text = f"🔔 <b>{title}</b>\n<i>{subtitle}</i>"
        if body:
            text += f"\n{body}"
        for uid in allowed_ids:
            url  = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({"chat_id": uid, "text": text, "parse_mode": "HTML"}).encode()
            req  = urllib.request.Request(url, data=data,
                                          headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        _log(f"Telegram 推送成功 → {allowed_ids}")
    except Exception as e:
        _log(f"Telegram 推送失败: {e}")

    # ── 飞书推送 ──────────────────────────────────────────────────────────
    try:
        cfg = _load_cfg()
        if not cfg.get("feishu_enabled", True):
            return
        app_id = cfg.get("feishu_app_id", "")
        app_secret = cfg.get("feishu_app_secret", "")
        open_id = cfg.get("feishu_open_id", "")
        if not app_id or not app_secret or not open_id:
            return
        import requests
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret}, timeout=10,
        )
        if r.status_code != 200 or r.json().get("code") != 0:
            _log(f"飞书推送 token 获取失败")
            return
        token = r.json()["tenant_access_token"]
        text = f"🔔 {title}\n{subtitle}"
        if body:
            text += f"\n{body}"
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": open_id, "msg_type": "text",
                  "content": json.dumps({"text": text}, ensure_ascii=False)},
            timeout=15,
        )
        if resp.status_code == 200 and resp.json().get("code") == 0:
            _log("飞书推送完成")
        else:
            _log(f"飞书推送失败: {resp.text[:100]}")
    except Exception as e:
        _log(f"飞书推送异常: {e}")


# ── 核心逻辑 ─────────────────────────────────────────────────────────────────

def check_and_notify(test_mode: bool = False) -> None:
    now      = datetime.now(CST)
    window   = timedelta(minutes=NOTIFY_WINDOW)
    reminders = _load_reminders()
    state     = _load_state()
    changed_reminders = False
    changed_state     = False

    for r in list(reminders):
        rid   = r["id"]
        title = r["title"]
        note  = r.get("note", "")
        start_dt = _parse_dt(r.get("start", ""))
        end_dt   = _parse_dt(r.get("end", ""))

        # ── 自动清理过期条目 ──────────────────────────────────────────────
        expire_anchor = end_dt or start_dt
        if expire_anchor and expire_anchor < now - timedelta(hours=EXPIRE_CLEANUP_HOURS):
            _log(f"自动清理已过期提醒: [{rid}] {title}")
            reminders = [x for x in reminders if x["id"] != rid]
            changed_reminders = True
            # 清理状态
            for key in [f"{rid}:start", f"{rid}:end"]:
                state.pop(key, None)
            changed_state = True
            continue

        # ── 检查 start 通知 ───────────────────────────────────────────────
        if start_dt:
            key = f"{rid}:start"
            already_notified = key in state
            in_window = start_dt - window <= now <= start_dt + timedelta(minutes=2)
            if in_window and not already_notified:
                mins_left = int((start_dt - now).total_seconds() / 60)
                if mins_left >= 0:
                    subtitle = f"{mins_left} 分钟后开始"
                else:
                    subtitle = "刚刚开始"
                body = note if note else start_dt.strftime("%Y-%m-%d %H:%M")
                _log(f"{'[TEST] ' if test_mode else ''}触发开始通知: [{rid}] {title} — {subtitle}")
                if not test_mode:
                    _send_notification("📅 SJTU 提醒", subtitle, f"{title}\n{body}" if body else title)
                state[key] = now.isoformat()
                changed_state = True

        # ── 检查 end 通知 ─────────────────────────────────────────────────
        if end_dt:
            key = f"{rid}:end"
            already_notified = key in state
            in_window = end_dt - window <= now <= end_dt + timedelta(minutes=2)
            if in_window and not already_notified:
                mins_left = int((end_dt - now).total_seconds() / 60)
                if mins_left >= 0:
                    subtitle = f"还有 {mins_left} 分钟截止"
                else:
                    subtitle = "刚刚截止"
                body = note if note else end_dt.strftime("%Y-%m-%d %H:%M")
                _log(f"{'[TEST] ' if test_mode else ''}触发截止通知: [{rid}] {title} — {subtitle}")
                if not test_mode:
                    _send_notification("⏰ SJTU 截止提醒", subtitle, f"{title}\n{body}" if body else title)
                state[key] = now.isoformat()
                changed_state = True

    if changed_reminders:
        _save_reminders(reminders)
    changed_state = _check_deadline_guard(state, test_mode=test_mode) or changed_state
    if changed_state:
        _save_state(state)


def print_list() -> None:
    """打印当前所有提醒状态（--list 模式）。"""
    now       = datetime.now(CST)
    reminders = _load_reminders()
    state     = _load_state()
    if not reminders:
        print("(暂无提醒)")
        return
    print(f"当前时间: {now.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"{'ID':>3}  {'状态':<8}  {'标题':<20}  {'开始时间':<20}  {'截止时间':<20}")
    print("-" * 80)
    for r in reminders:
        rid      = r["id"]
        title    = r["title"][:20]
        start_dt = _parse_dt(r.get("start", ""))
        end_dt   = _parse_dt(r.get("end", ""))

        anchor = end_dt or start_dt
        if anchor and anchor < now:
            status = "已过期"
        elif start_dt and start_dt - timedelta(minutes=NOTIFY_WINDOW) <= now:
            status = "进行中"
        else:
            status = "待触发"

        s_notified = "✓" if f"{rid}:start" in state else " "
        e_notified = "✓" if f"{rid}:end" in state else " "

        start_str = start_dt.strftime("%m-%d %H:%M") + s_notified if start_dt else "-"
        end_str   = end_dt.strftime("%m-%d %H:%M") + e_notified if end_dt else "-"
        print(f"{rid:>3}  {status:<8}  {title:<20}  {start_str:<21}  {end_str}")


# ── 入口 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        print_list()
    elif "--test" in args:
        print(f"[TEST 模式] 当前时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M')}")
        check_and_notify(test_mode=True)
        print("[TEST 模式] 检查完毕，以上为将要触发的通知（未实际弹出）")
    else:
        check_and_notify(test_mode=False)
