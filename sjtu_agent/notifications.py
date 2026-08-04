from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request


def _send_system_notification(title: str, subtitle: str, body: str) -> None:
    message = f"{subtitle}\n{body}" if body else subtitle
    try:
        from plyer import notification as _plyer_notif  # type: ignore
        _plyer_notif.notify(
            title=title,
            message=message,
            app_name="SJTU Agent",
            timeout=10,
        )
        return
    except Exception:
        pass

    if sys.platform == "darwin":
        def esc(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        script = (
            f'display notification "{esc(body)}" '
            f'with title "{esc(title)}" '
            f'subtitle "{esc(subtitle)}"'
        )
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)
    elif sys.platform == "win32":
        # Windows 10+ 内置 PowerShell ToastNotification — 用环境变量传参，防注入
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
        subprocess.run(["notify-send", title, message], check=True, capture_output=True, timeout=5)


def _send_telegram_notification(cfg: dict, title: str, subtitle: str, body: str) -> None:
    token = cfg.get("telegram_token", "")
    allowed_ids = [int(x) for x in cfg.get("telegram_allowed_ids", [])]
    text = f"🔔 <b>{title}</b>\n<i>{subtitle}</i>"
    if body:
        text += f"\n{body}"
    # Telegram 单条消息最大 4096 字符，超长按 4000 分块
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    for uid in allowed_ids:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        for chunk in chunks:
            data = json.dumps({"chat_id": uid, "text": chunk, "parse_mode": "HTML"}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)


def _send_feishu_notification(cfg: dict, title: str, subtitle: str, body: str) -> None:
    # 复用 feishu_client 的缓存 tenant_access_token，避免每次手动获取
    from sjtu_agent.feishu_client import send_text_message
    open_id = cfg.get("feishu_open_id", "")
    text = f"🔔 {title}\n{subtitle}"
    if body:
        text += f"\n{body}"
    if not send_text_message(open_id, text):
        raise RuntimeError("飞书推送失败")


def _channel_configured(cfg: dict, channel: str) -> bool:
    if channel == "system":
        return True
    if channel == "telegram":
        return bool(
            cfg.get("telegram_enabled", True)
            and cfg.get("telegram_token")
            and cfg.get("telegram_allowed_ids")
        )
    if channel == "feishu":
        return bool(
            cfg.get("feishu_enabled", True)
            and cfg.get("feishu_app_id")
            and cfg.get("feishu_app_secret")
            and cfg.get("feishu_open_id")
        )
    if channel == "wechat":
        return bool(cfg.get("wechat_enabled", True))
    return False


def send_notification(
    cfg: dict,
    title: str,
    subtitle: str,
    body: str,
    *,
    channels: list[str] | None = None,
    test_mode: bool = False,
) -> dict:
    channels = channels or ["system", "telegram", "feishu"]
    sent: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    would_send: list[dict] = []

    for channel in channels:
        if not _channel_configured(cfg, channel):
            skipped.append({"channel": channel, "reason": "unconfigured"})
            continue
        if test_mode:
            would_send.append({
                "channel": channel,
                "title": title,
                "subtitle": subtitle,
                "body": body,
            })
            continue
        try:
            if channel == "system":
                _send_system_notification(title, subtitle, body)
            elif channel == "telegram":
                _send_telegram_notification(cfg, title, subtitle, body)
            elif channel == "feishu":
                _send_feishu_notification(cfg, title, subtitle, body)
            elif channel == "wechat":
                from scripts.wechat_bot import send_reminder_via_wechat
                send_reminder_via_wechat(title, subtitle, body)
            else:
                skipped.append({"channel": channel, "reason": "unsupported"})
                continue
            sent.append({"channel": channel})
        except Exception as exc:
            failed.append({"channel": channel, "error": str(exc)})
    return {
        "ok": not failed,
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "would_send": would_send,
    }
