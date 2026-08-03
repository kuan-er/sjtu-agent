#!/usr/bin/env python3
"""
login.py — 通过 jAccount 账号密码登录各平台，自动刷新 config.json 中的 cookies

用法：
  python login.py              # 登录所有平台
  python login.py --aihaoke   # 只刷新 aihaoke
  python login.py --phycai    # 只刷新 phycai

依赖（除 requirements.txt 外）：
  pip install python-dotenv   # 读取 .env 文件
  pip install anthropic       # 可选，用 Claude 自动识别验证码（否则手动输入）

.env 示例：
  JACCOUNT_USERNAME=your_jaccount_username  # 不是学号！是登录 my.sjtu.edu.cn 的英文用户名（如 zhangsan）
  JACCOUNT_PASSWORD=your_password
  ANTHROPIC_API_KEY=sk-ant-...  # 可选
"""

import argparse
import base64
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from sjtu_agent.paths import CONFIG_PATH

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 未安装 python-dotenv 也可以直接用环境变量

from playwright.sync_api import BrowserContext, Page, sync_playwright

# 各平台域名 → config.json 中的 key
_DOMAIN_TO_KEY = {
    "sjtu.aihaoke.net": "aihaoke_cookies",
    "aihaoke.net":      "aihaoke_cookies",
    "jaccount.sjtu.edu.cn": "jaccount_cookies",
    "www.phycai.sjtu.edu.cn": "phycai_cookies",
    "phycai.sjtu.edu.cn":    "phycai_cookies",
}


# ── 凭据 ──────────────────────────────────────────────────────────────────────

def _creds() -> tuple[str, str]:
    u = os.environ.get("JACCOUNT_USERNAME", "").strip()
    p = os.environ.get("JACCOUNT_PASSWORD", "").strip()
    if not u or not p:
        print("[错误] 请在 .env 中设置 JACCOUNT_USERNAME 和 JACCOUNT_PASSWORD")
        sys.exit(1)
    return u, p


# ── 验证码识别 ─────────────────────────────────────────────────────────────────
# 方案优先级：
#   1. 思源极客协会 ResNet 在线 API（专门针对 jAccount 验证码训练，速度快、准确率高）
#   2. Claude Haiku 视觉 API（备用，需要 ANTHROPIC_API_KEY）
#   3. 手动输入（最终兜底）

_GEEK_API = "https://geek.sjtu.edu.cn/captcha-solver/"


def _png_to_jpeg(png_bytes: bytes) -> bytes:
    """把 PNG 字节转为 JPEG 并缩放到 110×40（API 要求）。"""
    try:
        from PIL import Image  # type: ignore
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB").resize((110, 40))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except ImportError:
        return png_bytes  # Pillow 未安装时直接发 PNG


def _solve_captcha_geek(img_bytes: bytes) -> str | None:
    """用思源极客协会的 ResNet 在线 API 识别 jAccount 验证码。"""
    try:
        jpeg = _png_to_jpeg(img_bytes)
        r = requests.post(
            _GEEK_API,
            files={"image": ("captcha.jpg", jpeg, "image/jpeg")},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json().get("result", "").strip()
        return result if result else None
    except Exception as e:
        print(f"  [CAPTCHA] 极客协会 API 失败：{e}")
        return None


def _solve_captcha_claude(img_bytes: bytes) -> str | None:
    """备用：用 Claude Haiku 视觉 API 识别，需要 ANTHROPIC_API_KEY。"""
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        client = anthropic.Anthropic()
        b64 = base64.standard_b64encode(img_bytes).decode()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=16,
            messages=[{"role": "user", "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text",
                 "text": "这是一个登录验证码图片。请只输出图中的字符（通常是4个字母或数字），不要有任何其他文字。"},
            ]}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"  [CAPTCHA] Claude 识别失败：{e}")
        return None


def _solve_captcha(img_bytes: bytes) -> str:
    """验证码识别入口：极客协会 API → Claude → 手动输入。"""
    code = _solve_captcha_geek(img_bytes)
    if code:
        print(f"  [CAPTCHA] 极客协会识别：{code}")
        return code

    code = _solve_captcha_claude(img_bytes)
    if code:
        print(f"  [CAPTCHA] Claude 识别：{code}")
        return code

    # 最终兜底：打开图片让用户手动输入
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(img_bytes)
        tmp = f.name
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", tmp])
        elif platform.system() == "Windows":
            os.startfile(tmp)
        else:
            subprocess.Popen(["xdg-open", tmp])
    except Exception:
        pass
    code = input(f"  [CAPTCHA] 自动识别失败，请手动输入验证码（图片：{tmp}）：").strip()
    try:
        os.unlink(tmp)
    except Exception:
        pass
    else:
        return code
    # fallback: register for atexit cleanup
    import atexit as _atexit
    _atexit.register(lambda: os.unlink(tmp) if os.path.exists(tmp) else None)
    return code


# ── jAccount 通用登录 ──────────────────────────────────────────────────────────


class ManualLoginRequired(Exception):
    """jAccount 走到了脚本无法自动完成的二次验证分支（如境外登录的「交我办/邮箱/手机」三选一），
    需要用户在浏览器里手动登录一次。"""
    pass


def _detect_otp_method_picker(page: Page) -> bool:
    """检测当前 jAccount 页面是否是「二次验证方式三选一」选择页（境外/异地登录会触发）。
    特征：页面同时出现「交我办」 + (「邮箱」/「邮件」) + (「手机」/「短信」) 等多个 OTP 入口。"""
    try:
        text = (page.inner_text("body", timeout=800) or "")
    except Exception:
        return False
    if "交我办" not in text:
        return False
    has_mail = ("邮箱" in text) or ("邮件" in text)
    has_phone = ("手机" in text) or ("短信" in text)
    return has_mail or has_phone


def _fill_jaccount(page: Page, username: str, password: str) -> bool:
    """
    在已跳转到 jAccount jalogin 页的 page 上完成登录。
    支持：图形验证码（自动识别）+ 短信/二步验证码（终端提示手动输入）。
    成功（跳出 jaccount.sjtu.edu.cn）返回 True，否则 False。
    """
    # 切换到密码登录模式（默认可能是短信）
    page.evaluate("if (typeof switchLoginType === 'function') switchLoginType('password')")
    page.wait_for_timeout(400)

    page.fill("#input-login-user", username)
    page.fill("#input-login-pass", password)

    for attempt in range(3):
        cap = page.locator("#captcha-img")
        if cap.count() and cap.is_visible():
            code = _solve_captcha(cap.screenshot())
            page.fill("#input-login-captcha", code)

        page.click("#submit-password-button")

        # 等待：成功（URL 离开 jaccount）、短信验证步骤、或失败（出现错误提示）
        try:
            page.wait_for_function(
                "() => !location.href.includes('jaccount.sjtu.edu.cn') || "
                "!!document.querySelector('.alert-danger, [class*=errorMsg], "
                "#input-login-sms-code, #input-bind-sms-code, "
                "[id*=sms-code], [name*=sms], [id*=twoFactor], "
                "#mfa-input, [id*=mfa], [id*=otp], [placeholder*=验证码][id*=sms]')",
                timeout=12_000,
            )
        except Exception:
            pass

        if "jaccount.sjtu.edu.cn" not in page.url:
            return True

        # ── 检测「二次验证方式三选一」（境外/异地登录）─────────────────
        # 这是 jAccount 出现「交我办 / 邮箱 / 手机」三选一的页面，脚本
        # 没法替用户在交我办里点确认，也没法收用户手机/邮箱里的 OTP，
        # 必须停下来让用户手动完成。
        if _detect_otp_method_picker(page):
            raise ManualLoginRequired(
                "jAccount 触发了境外/异地登录的二次验证（交我办 / 邮箱 / 手机 三选一），"
                "脚本无法自动完成。请在浏览器里手动登录一次该平台后再试。"
            )

        # ── 检测短信/二步验证码输入框 ────────────────────────────────────
        _sms_selectors = [
            "#input-login-sms-code",
            "#input-bind-sms-code",
            "[id*=sms-code]",
            "[name=smsCode]",
            "[name*=sms][type=text]",
            "[id*=twoFactor]",
            "#mfa-input",
            "[id*=mfa][type=text]",
            "[id*=otp][type=text]",
        ]
        sms_input = None
        for sel in _sms_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.is_visible(timeout=500):
                    sms_input = loc
                    break
            except Exception:
                continue

        if sms_input is not None:
            # 尝试自动点击"发送短信"按钮（如页面有）
            for send_sel in [
                "button:has-text('发送短信')",
                "button:has-text('获取验证码')",
                "button:has-text('发送验证码')",
                "a:has-text('发送短信')",
                "[id*=send-sms]",
                "[id*=sendSms]",
            ]:
                try:
                    btn = page.locator(send_sel)
                    if btn.count() and btn.is_visible(timeout=500):
                        btn.click()
                        page.wait_for_timeout(500)
                        break
                except Exception:
                    continue

            print("\n  [jAccount] 检测到短信验证码步骤。")
            print("  请查看手机短信，将验证码输入到下方（直接回车跳过将导致登录失败）：")
            try:
                sms_code = input("  短信验证码：").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  [jAccount] 已跳过短信验证码，登录失败。")
                return False

            if not sms_code:
                print("  [jAccount] 未输入短信验证码，登录失败。")
                return False

            sms_input.fill(sms_code)

            # 寻找提交按钮
            for submit_sel in [
                "#submit-sms-button",
                "button[type=submit]",
                "button:has-text('登录')",
                "button:has-text('确认')",
                "button:has-text('验证')",
                "button:has-text('Submit')",
                "input[type=submit]",
            ]:
                try:
                    btn = page.locator(submit_sel)
                    if btn.count() and btn.is_visible(timeout=500):
                        btn.click()
                        break
                except Exception:
                    continue

            # 等待登录结果
            try:
                page.wait_for_function(
                    "() => !location.href.includes('jaccount.sjtu.edu.cn') || "
                    "!!document.querySelector('.alert-danger, [class*=errorMsg]')",
                    timeout=15_000,
                )
            except Exception:
                pass

            if "jaccount.sjtu.edu.cn" not in page.url:
                print("  [jAccount] 短信验证码验证成功！")
                return True
            print("  [jAccount] 短信验证码验证失败，请重试。")
            return False

        # 普通登录失败（验证码错误等）
        print(f"  [jAccount] 第 {attempt + 1} 次登录失败，刷新验证码重试…")
        page.evaluate("if (typeof refreshCaptcha === 'function') refreshCaptcha()")
        page.wait_for_timeout(700)

    print("  [jAccount] 多次尝试失败，请检查账号密码或稍后重试")
    return False


def _jaccount_sso(page: Page, entry_url: str, success_url_pattern: str,
                  username: str, password: str) -> bool:
    """
    通用 jAccount SSO 流程：
      1. 打开 entry_url 触发重定向
      2. 等待落到 jAccount 登录页
      3. 填写账密 + 验证码
      4. 等待重定向回 success_url_pattern
    """
    page.goto(entry_url, wait_until="networkidle", timeout=25_000)

    # 如果已经在目标页，说明 SSO 自动通过（旧 session 有效）
    if "jaccount.sjtu.edu.cn" not in page.url:
        print("  已有有效 session，无需重新登录")
        return True

    if not _fill_jaccount(page, username, password):
        return False

    page.wait_for_url(success_url_pattern, timeout=20_000)
    page.wait_for_load_state("networkidle", timeout=10_000)
    return True


# ── 各平台登录 ────────────────────────────────────────────────────────────────

def login_aihaoke(ctx: BrowserContext, username: str, password: str) -> bool:
    print("\n[*] 登录 aihaoke (sjtu.aihaoke.net)…")
    page = ctx.new_page()
    try:
        # 先到登录页
        page.goto("https://sjtu.aihaoke.net/login", wait_until="networkidle", timeout=20_000)

        # 点击"统一身份认证" tab（列表最后一项，中英文均兼容）
        page.locator(".login-type-tabs li").last.click()

        # 等待跳转到 jAccount
        page.wait_for_url("**/jaccount**", timeout=15_000)

        if not _fill_jaccount(page, username, password):
            return False

        # 等待回到 aihaoke 学生主页
        page.wait_for_url("**/sjtu.aihaoke.net/student/**", timeout=20_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        print(f"  ✓ 登录成功：{page.url}")
        return True
    except ManualLoginRequired:
        raise
    except Exception as e:
        print(f"  ✗ 失败：{e}")
        return False
    finally:
        page.close()


def login_phycai(ctx: BrowserContext, username: str, password: str) -> bool:
    print("\n[*] 登录 phycai (www.phycai.sjtu.edu.cn)…")
    page = ctx.new_page()
    try:
        # phycai 提供了专用的 jAccount 入口
        ok = _jaccount_sso(
            page,
            entry_url="http://www.phycai.sjtu.edu.cn/pe/Jlogin.aspx",
            success_url_pattern="**/phycai.sjtu.edu.cn/pe/student/**",
            username=username,
            password=password,
        )
        if ok:
            print(f"  ✓ 登录成功：{page.url}")
        return ok
    except ManualLoginRequired:
        raise
    except Exception as e:
        print(f"  ✗ 失败：{e}")
        return False
    finally:
        page.close()


# ── 收集 cookies & 写入 config.json ──────────────────────────────────────────

def _collect_and_save(ctx: BrowserContext) -> None:
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[login] config.json 解析失败，已中止保存 cookies 以保护现有配置: {e}")
            return

    # 按域名归集 cookies
    platform_cookies: dict[str, dict[str, str]] = {}
    for c in ctx.cookies():
        domain = c["domain"].lstrip(".")
        for d, key in _DOMAIN_TO_KEY.items():
            if domain == d or domain.endswith("." + d):
                platform_cookies.setdefault(key, {})[c["name"]] = c["value"]
                break

    updated = []
    for cfg_key, cookies in platform_cookies.items():
        if cookies:
            cfg[cfg_key] = cookies
            preview = {k: v[:6] + "…" for k, v in cookies.items()}
            print(f"  ✓ {cfg_key}: {preview}")
            updated.append(cfg_key)

    if not updated:
        print("  （未收集到任何 cookies，config.json 未更改）")
        return

    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 已更新 {CONFIG_PATH}")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="通过 jAccount 自动登录各平台并刷新 cookies")
    p.add_argument("--aihaoke", action="store_true", help="只登录 aihaoke")
    p.add_argument("--phycai",  action="store_true", help="只登录 phycai")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    do_all = not args.aihaoke and not args.phycai
    username, password = _creds()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # device_scale_factor=1 避免 Retina/HiDPI 截图放大（API 要求 110×40 原始尺寸）
        ctx = browser.new_context(device_scale_factor=1)

        if do_all or args.aihaoke:
            login_aihaoke(ctx, username, password)

        if do_all or args.phycai:
            login_phycai(ctx, username, password)

        print("\n[*] 收集并保存 cookies…")
        _collect_and_save(ctx)

        browser.close()


if __name__ == "__main__":
    main()
