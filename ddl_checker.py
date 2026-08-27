#!/usr/bin/env python3
"""
DDL Checker — 多平台课程截止时间聚合工具

支持平台：
  1. Canvas LMS      (oc.sjtu.edu.cn)     — Bearer Token
  2. aihaoke.net     (sjtu.aihaoke.net)   — Cookie
  3. 物理实验        (phycai.sjtu.edu.cn) — Cookie
  4. 中国大学MOOC    (icourse163.org)     — Cookie

用法：
  python ddl_checker.py
  python ddl_checker.py --canvas-only
  python ddl_checker.py --skip icourse phycai
"""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from sjtu_agent.paths import CONFIG_PATH, ENV_PATH, SCHEDULE_CACHE_PATH as _SCHEDULE_CACHE_PATH
from sjtu_agent.logging import get_logger

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

_logger = get_logger("ddl_checker")

# ── 全局常量 ──────────────────────────────────────────────────────────────────

CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST)
WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"[错误] 未找到配置文件：{CONFIG_PATH}")
        print("       请将 config.example.json 复制为 config.json 并填入凭据。")
        sys.exit(1)
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def parse_dt(s: str) -> datetime | None:
    """尝试多种格式解析时间字符串，统一返回 CST datetime。"""
    if not s:
        return None
    s = s.strip()
    # (fmt, default_tz)：strptime 不会从 Z/+08:00 字面量推断时区，必须显式指定。
    fmts: list[tuple[str, timezone | None]] = [
        ("%Y-%m-%dT%H:%M:%SZ", timezone.utc),
        ("%Y-%m-%dT%H:%M:%S.%fZ", timezone.utc),
        ("%Y-%m-%dT%H:%M:%S+08:00", CST),
        ("%Y-%m-%dT%H:%M:%S", None),
        ("%Y-%m-%d %H:%M:%S", None),
        ("%Y/%m/%d %H:%M:%S", None),
        ("%Y-%m-%d %H:%M", None),
        ("%Y/%m/%d %H:%M", None),
    ]
    for fmt, default_tz in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                # 格式自带时区标记的（Z / +08:00）用对应时区；纯本地时间默认 CST
                dt = dt.replace(tzinfo=default_tz or CST)
            return dt.astimezone(CST)
        except ValueError:
            continue
    return None


def deadline_label(dt: datetime) -> str:
    delta = dt - NOW
    days = delta.days
    hours = int(delta.total_seconds() // 3600)
    if delta.total_seconds() < 0:
        return "已过期"
    if hours < 24:
        return f"今天 {hours}h后"
    if days == 1:
        return "明天"
    return f"{days}天后"


def fmt_due(dt: datetime) -> str:
    return dt.strftime("%Y/%m/%d %H:%M")


def make_session(cookies: dict, referer: str = "") -> requests.Session:
    s = requests.Session()
    s.cookies.update(cookies)
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        **({"Referer": referer} if referer else {}),
    })
    return s


# ── Platform 1: Canvas LMS ────────────────────────────────────────────────────

def fetch_canvas(cfg: dict, include_past: bool = False, classify: bool = False) -> list[dict]:
    """通过 Canvas REST API 获取作业。include_past=True 时包含已过期的历史作业。
    classify=True 时额外返回 description 和 submission_types 字段用于智能分类。"""
    token = cfg.get("canvas_token", "").strip()
    base = cfg.get("canvas_base_url", "https://oc.sjtu.edu.cn").rstrip("/")
    if not token or token.startswith("YOUR_"):
        _logger.warning("[Canvas] ⚠ 未配置 canvas_token，跳过")
        return []

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    # 1. 分页获取所有在修课程
    courses: list[dict] = []
    url: str | None = f"{base}/api/v1/courses"
    params: dict = {"enrollment_state": "active", "per_page": 100}
    while url:
        try:
            r = session.get(url, params=params, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            _logger.error(f"[Canvas] 获取课程列表失败：{e}")
            raise  # 让上层调用者感知错误，而非静默返回空列表
        courses.extend(r.json())
        url = r.links.get("next", {}).get("url")
        params = {}

    results: list[dict] = []

    # 2. 逐课程拉取即将到期作业，并用 /students/submissions 批量核查个人提交状态
    # （include[]=submission 在 SJTU Canvas 有 bug，数据不准确）
    for course in courses:
        cid = course["id"]
        cname = course.get("name", f"课程{cid}")

        # 2a. 拉取作业列表（不用 bucket=upcoming，那个只返回 7 天内的，10 天外的会被漏掉；改本地按 due_at 过滤）
        pending: list[dict] = []
        asgn_url: str | None = f"{base}/api/v1/courses/{cid}/assignments"
        asgn_params: dict = {"per_page": 100, "order_by": "due_at"}
        while asgn_url:
            try:
                r = session.get(asgn_url, params=asgn_params, timeout=15)
                r.raise_for_status()
            except requests.RequestException as e:
                _logger.error(f"[Canvas] 获取 {cname} 作业失败：{e}")
                raise
            for a in r.json():
                if a.get("workflow_state") == "deleted":
                    continue
                due = parse_dt(a.get("due_at", ""))
                if not include_past and (not due or due < datetime.now(CST)):
                    continue
                item = {"id": a["id"], "name": a.get("name", "未知作业"), "due": due}
                if classify:
                    item["description"] = a.get("description", "") or ""
                    item["submission_types"] = a.get("submission_types", []) or []
                pending.append(item)
            asgn_url = r.links.get("next", {}).get("url")
            asgn_params = {}

        if not pending:
            continue

        # 2b. 批量查当前用户的提交状态
        aid_list = [str(a["id"]) for a in pending]
        submitted_ids: set[int] = set()
        sub_url: str | None = f"{base}/api/v1/courses/{cid}/students/submissions"
        sub_params: dict = {"student_ids[]": "self", "per_page": 100}
        for aid in aid_list:
            sub_params.setdefault("assignment_ids[]", []).append(aid)  # type: ignore[union-attr]
        try:
            sr = session.get(sub_url, params=sub_params, timeout=15)
            sr.raise_for_status()
            for sub in sr.json():
                if sub.get("workflow_state") in ("submitted", "graded") and sub.get("submitted_at"):
                    submitted_ids.add(sub["assignment_id"])
        except requests.RequestException as e:
            _logger.warning(f"[Canvas] 查询 {cname} 提交状态失败：{e}")

        for a in pending:
            entry = {
                "platform": "Canvas",
                "course": cname,
                "name": a["name"],
                "due": a["due"],
                "submitted": a["id"] in submitted_ids,
                "course_id": cid,
                "assignment_id": a["id"],
                "url": f"{base}/courses/{cid}/assignments/{a['id']}",
            }
            if classify:
                entry["description"] = a.get("description", "")
                entry["submission_types"] = a.get("submission_types", [])
            results.append(entry)

    return results


# ── Platform 2: sjtu.aihaoke.net ─────────────────────────────────────────────
# Bearer token 就存在 aihaoke_cookies["haoke-token"] 里，直接用 requests 调 API。

_AIHAOKE_COURSES_TTL_SECONDS = 7 * 24 * 3600  # 课程列表缓存 7 天


def _aihaoke_courses_cache_fresh(cfg: dict) -> bool:
    """判断 config.json 中的 aihaoke_courses 是否仍在 TTL 内。"""
    ts = cfg.get("aihaoke_courses_fetched_at")
    if not isinstance(ts, (int, float)):
        return False
    return (datetime.now().timestamp() - float(ts)) < _AIHAOKE_COURSES_TTL_SECONDS


def _fetch_aihaoke_enrolled_courses(cfg: dict) -> list[dict] | None:
    """
    通过 aihaoke 官方"我的班级"API 获取用户当前选修课程。
    直接命中 /api/teach/instance/listMyClass（浏览器端进入 /student/course 时调用的接口）。
    用 Playwright 上下文发起请求以携带完整的 WAF/Referer，稳定性最高。
    """
    if not HAS_PLAYWRIGHT:
        return None

    ok, error = refresh_aihaoke_cookies(cfg)
    if not ok:
        _logger.warning(f"[aihaoke] cookies 刷新失败：{error}")
        return None

    raw_cookies = cfg.get("aihaoke_cookies", {})
    token = raw_cookies.get("haoke-token", "").strip()
    if not token:
        return None

    import uuid as _uuid
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            ctx.add_cookies([
                {"name": k, "value": v, "domain": "sjtu.aihaoke.net", "path": "/"}
                for k, v in raw_cookies.items()
            ])
            # 先访问学生页，让浏览器建立 referer / WAF 上下文
            page = ctx.new_page()
            try:
                page.goto(
                    "https://sjtu.aihaoke.net/student/course",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
            except Exception:
                pass
            finally:
                page.close()

            resp = ctx.request.post(
                "https://sjtu.aihaoke.net/api/teach/instance/listMyClass",
                data=json.dumps({
                    "instanceName": "",
                    "classId": 0,
                    "requestId": str(_uuid.uuid4()),
                }),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Referer": "https://sjtu.aihaoke.net/student/course",
                },
                timeout=20000,
            )
            try:
                data = resp.json()
            finally:
                browser.close()
    except Exception as e:
        _logger.warning(f"[aihaoke] 调用 listMyClass 失败：{e}")
        return None

    if data.get("code") == 401:
        _logger.warning("[aihaoke] listMyClass 返回 401，token 可能失效")
        return None

    payload = data.get("data")
    if isinstance(payload, dict):
        rows = (
            payload.get("teachClassResponseList")
            or payload.get("rowList")
            or payload.get("list")
            or []
        )
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    courses: list[dict] = []
    for c in rows:
        cid = c.get("classId") or c.get("courseId") or c.get("id")
        iid = c.get("instanceId") or c.get("id") or cid
        name = (
            c.get("instanceName")
            or c.get("courseName")
            or c.get("className")
            or c.get("name")
            or (f"课程{cid}" if cid else "")
        )
        if not cid or not name:
            continue
        courses.append({
            "name": str(name).strip(),
            "courseId": int(cid),
            "instanceId": int(iid or cid),
        })

    if not courses:
        _logger.warning(f"[aihaoke] listMyClass 返回空列表，响应：{json.dumps(data, ensure_ascii=False)[:300]}")
        return None

    cfg["aihaoke_courses"] = courses
    cfg["aihaoke_courses_fetched_at"] = datetime.now().timestamp()
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _logger.info(f"[aihaoke] ✓ 自动识别到 {len(courses)} 门课程：{[c['name'] for c in courses]}")
    return courses


def _aihaoke_token_works(token: str) -> bool:
    """快速检测 aihaoke token 是否能正常调用任务列表 API（不启动浏览器）。"""
    import uuid as _uuid
    if not token:
        return False
    try:
        resp = requests.post(
            "https://sjtu.aihaoke.net/api/learn/task/listTask",
            json={
                "classId": 0,
                "orderType": 0,
                "page": {"pageNo": 1, "pageSize": 1},
                "searchText": "",
                "status": 0,
                "taskTypes": [],
                "requireFlag": 1,
                "requestId": str(_uuid.uuid4()),
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        data = resp.json()
        # 401 = token 过期；404 = 接口正常但 classId=0 不存在（说明 token 有效）
        return data.get("code") != 401
    except Exception:
        return False


def fetch_aihaoke(cfg: dict, *, force_refresh_courses: bool = False) -> list[dict]:
    """获取 aihaoke 必做任务。优先纯 API，token 失效时才启动 Playwright 刷新。"""
    import uuid as _uuid

    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
        load_dotenv()
    except ImportError:
        pass

    raw_cookies = cfg.get("aihaoke_cookies", {})
    token = raw_cookies.get("haoke-token", "").strip()
    has_creds = bool(
        os.environ.get("JACCOUNT_USERNAME", "").strip()
        and os.environ.get("JACCOUNT_PASSWORD", "").strip()
    )
    if not token and not has_creds:
        _logger.warning("[aihaoke] ⚠ 未配置 aihaoke_cookies[haoke-token] 且缺少 jAccount 凭据，跳过")
        return []

    # 快速验证 token（纯 requests，< 1s）；token 为空或失效都触发刷新
    if not token or not _aihaoke_token_works(token):
        _logger.info("[aihaoke] Token 缺失或已过期，正在登录…")
        ok, error = refresh_aihaoke_cookies(cfg)
        if not ok:
            _logger.warning(f"[aihaoke] ⚠ 登录失败：{error}")
            return []
        raw_cookies = cfg.get("aihaoke_cookies", {})
        token = raw_cookies.get("haoke-token", "").strip()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # 课程列表：优先用未过期的缓存；否则调用 listMyClass API 自动发现
    courses = cfg.get("aihaoke_courses") or []
    cache_fresh = _aihaoke_courses_cache_fresh(cfg)
    if force_refresh_courses or not courses or not cache_fresh:
        if force_refresh_courses:
            _logger.info("[aihaoke] 强制刷新选修课程列表…")
        elif not courses:
            _logger.info("[aihaoke] 正在自动识别选修课程列表…")
        else:
            _logger.info("[aihaoke] 课程缓存已过期，重新识别…")
        discovered = _fetch_aihaoke_enrolled_courses(cfg)
        if discovered:
            courses = discovered
        elif courses:
            _logger.warning("[aihaoke] ⚠ 自动识别失败，沿用历史缓存继续运行")
        else:
            _logger.warning("[aihaoke] ⚠ 未能识别到任何已选修课程，跳过")
            return []

    if not courses:
        _logger.warning("[aihaoke] ⚠ 课程列表为空，跳过")
        return []

    def _fetch_one(course: dict) -> tuple[list[dict], bool]:
        """拉取单课程全部页面，返回 (tasks, token_expired)。"""
        cid = course["courseId"]
        cname = course["name"]
        tasks: list[dict] = []
        page_no = 1
        while True:
            body = {
                "classId": cid,
                "orderType": 0,
                "page": {"pageNo": page_no, "pageSize": 50},
                "searchText": "",
                "status": 0,
                "taskTypes": [],
                "requireFlag": 1,
                "requestId": str(_uuid.uuid4()),
            }
            try:
                resp = requests.post(
                    "https://sjtu.aihaoke.net/api/learn/task/listTask",
                    json=body, headers=headers, timeout=15,
                )
                data = resp.json()
            except Exception as e:
                _logger.warning(f"  [aihaoke] {cname} 第{page_no}页请求失败：{e}")
                return tasks, False

            if data.get("code") == 401:
                return tasks, True

            rows = data.get("data", {}).get("rowList", [])
            total_pages = data.get("data", {}).get("pageCount", 1)

            for t in rows:
                if t.get("myStatus") != 10:
                    continue
                due = parse_dt(t.get("endTime", ""))
                if not due or due < datetime.now(CST):
                    continue
                tasks.append({
                    "platform": "aihaoke",
                    "course": cname,
                    "name": t.get("taskName", "未知任务").strip(),
                    "due": due,
                    "submitted": False,
                })

            if page_no >= total_pages:
                break
            page_no += 1
        return tasks, False

    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: list[dict] = []
    token_expired = False
    with ThreadPoolExecutor(max_workers=min(len(courses), 4)) as pool:
        futures = {pool.submit(_fetch_one, c): c for c in courses}
        for fut in as_completed(futures):
            tasks, expired = fut.result()
            results.extend(tasks)
            if expired:
                token_expired = True
    if token_expired:
        _logger.warning("[aihaoke] ⚠ Token 已过期，请运行 python login.py --aihaoke 刷新")

    return results


def refresh_aihaoke_cookies(cfg: dict) -> tuple[bool, str]:
    """校验并在需要时刷新 aihaoke cookies，成功后写回 config.json。"""
    import uuid as _uuid

    if not HAS_PLAYWRIGHT:
        return False, "未安装 playwright，请运行 pip install playwright && playwright install chromium"

    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
        load_dotenv()
    except ImportError:
        pass

    try:
        from login import login_aihaoke as _login_aihaoke
    except Exception as e:
        return False, f"加载 aihaoke 登录器失败：{e}"

    username = os.environ.get("JACCOUNT_USERNAME", "").strip()
    password = os.environ.get("JACCOUNT_PASSWORD", "").strip()
    has_creds = bool(username and password)
    raw_cookies = cfg.get("aihaoke_cookies", {})

    if not raw_cookies and not has_creds:
        return False, "未配置 jAccount 凭据或 aihaoke_cookies"

    def _token_is_valid(token: str) -> bool:
        """通过调用"我的课程"接口检测 token 是否有效，无需依赖硬编码课程 ID。"""
        if not token:
            return False
        try:
            resp = requests.post(
                "https://sjtu.aihaoke.net/api/learn/course/listMyCourse",
                json={"page": {"pageNo": 1, "pageSize": 1}, "requestId": str(_uuid.uuid4())},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            data = resp.json()
        except Exception:
            return False
        return data.get("code") != 401

    def _collect_cookies(ctx) -> dict[str, str]:
        return {
            c["name"]: c["value"]
            for c in ctx.cookies()
            if "aihaoke.net" in c.get("domain", "")
        }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        try:
            if raw_cookies:
                ctx.add_cookies([
                    {"name": k, "value": v, "domain": "sjtu.aihaoke.net", "path": "/"}
                    for k, v in raw_cookies.items()
                ])
                page = ctx.new_page()
                try:
                    page.goto("https://sjtu.aihaoke.net/student", wait_until="domcontentloaded", timeout=12_000)
                except Exception:
                    pass
                finally:
                    page.close()

                current_cookies = _collect_cookies(ctx)
                if _token_is_valid(current_cookies.get("haoke-token", "")):
                    cfg["aihaoke_cookies"] = current_cookies
                    CONFIG_PATH.write_text(
                        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    return True, ""

            if not has_creds:
                return False, "需要 jAccount 凭据，请先用 save_credentials 配置"

            ok = _login_aihaoke(ctx, username, password)
            if not ok:
                return False, "aihaoke 登录失败"

            new_cookies = _collect_cookies(ctx)
            if not _token_is_valid(new_cookies.get("haoke-token", "")):
                return False, "aihaoke 登录后未获取到有效 token"

            cfg["aihaoke_cookies"] = new_cookies
            CONFIG_PATH.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True, ""
        finally:
            browser.close()


# ── Platform 3: phycai.sjtu.edu.cn ───────────────────────────────────────────

_GEEK_CAPTCHA_API = "https://geek.sjtu.edu.cn/captcha-solver/"


def _solve_captcha_phycai(img_bytes: bytes) -> str:
    """验证码识别：极客协会 API → Claude → 手动输入。"""
    import base64
    import io

    # 1. 极客协会 API
    try:
        try:
            from PIL import Image  # type: ignore
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((110, 40))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            jpeg_bytes = buf.getvalue()
        except ImportError:
            jpeg_bytes = img_bytes
        r = requests.post(
            _GEEK_CAPTCHA_API,
            files={"image": ("cap.jpg", jpeg_bytes, "image/jpeg")},
            timeout=8,
        )
        if r.ok:
            code = r.json().get("result", "").strip()
            if code:
                _logger.info(f"  [CAPTCHA] 极客协会识别：{code}")
                return code
    except Exception:
        pass

    # 2. Claude Haiku
    try:
        import anthropic  # type: ignore
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-haiku-20240307",
                max_tokens=16,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png",
                        "data": base64.b64encode(img_bytes).decode(),
                    }},
                    {"type": "text", "text": "这是一个验证码图片，请只输出验证码文字，不要其他内容。"},
                ]}],
            )
            code = msg.content[0].text.strip()
            if code:
                _logger.info(f"  [CAPTCHA] Claude 识别：{code}")
                return code
    except Exception:
        pass

    # 3. 手动输入
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(img_bytes)
        tmp = f.name
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", tmp])
    except Exception:
        pass
    code = input(f"  [CAPTCHA] 请手动输入验证码（图片：{tmp}）：").strip()
    try:
        os.unlink(tmp)
    except Exception:
        import atexit as _atexit
        _atexit.register(lambda: os.unlink(tmp) if os.path.exists(tmp) else None)
    return code


def _phycai_fetch_with_login(cfg: dict) -> str | None:
    """通过 phycai jAccount SSO 按钮登录，成功则返回页面 HTML 并刷新 cookies。"""
    if not HAS_PLAYWRIGHT:
        return None

    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except ImportError:
        pass

    username = os.environ.get("JACCOUNT_USERNAME", "").strip()
    password = os.environ.get("JACCOUNT_PASSWORD", "").strip()
    if not username or not password:
        return None

    _logger.info("[phycai] 正在通过 jAccount 登录…")
    target_url = "http://www.phycai.sjtu.edu.cn/pe/student/select.aspx"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()

            # Jlogin.aspx 会直接跳转到 jAccount OAuth
            page.goto(
                "http://www.phycai.sjtu.edu.cn/pe/Jlogin.aspx",
                wait_until="networkidle", timeout=25_000,
            )

            if "jaccount.sjtu.edu.cn" in page.url:
                page.evaluate(
                    "if (typeof switchLoginType === 'function') switchLoginType('password')"
                )
                page.wait_for_timeout(400)
                page.fill("#input-login-user", username)
                page.fill("#input-login-pass", password)

                logged_in = False
                for attempt in range(3):
                    cap = page.locator("#captcha-img")
                    if cap.count() and cap.is_visible():
                        code = _solve_captcha_phycai(cap.screenshot())
                        page.fill("#input-login-captcha", code)
                    page.click("#submit-password-button")
                    try:
                        page.wait_for_function(
                            "() => !location.href.includes('jaccount.sjtu.edu.cn') || "
                            "!!document.querySelector('.alert-danger, [class*=errorMsg]')",
                            timeout=12_000,
                        )
                    except Exception:
                        pass
                    if "jaccount.sjtu.edu.cn" not in page.url:
                        logged_in = True
                        break
                    print(f"  [jAccount] 第 {attempt + 1} 次验证码错误，刷新重试…")
                    page.evaluate(
                        "if (typeof refreshCaptcha === 'function') refreshCaptcha()"
                    )
                    page.wait_for_timeout(700)

                if not logged_in:
                    _logger.error("[phycai] jAccount 登录失败")
                    browser.close()
                    return None

                # 等待跳回 phycai（任意 phycai 路径即可）
                try:
                    page.wait_for_url("**/phycai.sjtu.edu.cn/**", timeout=15_000)
                except Exception:
                    pass

            # 无论跳到哪里，直接导航到目标页
            page.goto(target_url, wait_until="networkidle", timeout=20_000)
            html = page.content()

            # 把新 cookies 写回 config.json
            new_cookies = {
                c["name"]: c["value"]
                for c in ctx.cookies()
                if "phycai.sjtu.edu.cn" in c.get("domain", "")
            }
            if new_cookies:
                cfg["phycai_cookies"] = new_cookies
                CONFIG_PATH.write_text(
                    json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                _logger.info("[phycai] ✓ 已登录并更新 cookies")

            browser.close()
            return html
    except Exception as e:
        _logger.error(f"[phycai] jAccount 登录出错：{e}")
        return None


def fetch_phycai(cfg: dict) -> dict | None:
    """获取最近一次未到来的物理实验安排。"""
    url = "http://www.phycai.sjtu.edu.cn/pe/student/select.aspx"

    # 优先：用已有 cookies（避免每次都要过验证码）
    cookies = cfg.get("phycai_cookies", {})
    if cookies and not all(v.startswith("YOUR_") for v in cookies.values()):
        session = make_session(cookies)
        try:
            r = session.get(url, timeout=15)
            r.encoding = r.apparent_encoding
            r.raise_for_status()
            # 简单判断是否跳转到登录页（cookies 过期时会跳转）
            if "login" not in r.url.lower() and "Jlogin" not in r.url:
                result = _parse_phycai_table(r.text)
                if result is not None:
                    return result
        except requests.RequestException:
            pass

    # 回退：用 .env 账号密码通过 jAccount 登录
    html = _phycai_fetch_with_login(cfg)
    if html is None:
        _logger.warning("[phycai] ⚠ 登录失败，跳过")
        return None

    return _parse_phycai_table(html)


def _parse_phycai_table(html: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")

    # 找到包含实验数据的主表（通常是内容最多的那个）
    tables = soup.find_all("table")
    if not tables:
        _logger.warning("[phycai] ⚠ 未找到任何表格，可能 cookie 已过期")
        return None
    table = max(tables, key=lambda t: len(t.find_all("tr")))

    rows = table.find_all("tr")
    if len(rows) < 2:
        return None

    # 解析表头，建立列索引
    headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
    col: dict[str, int] = {}
    mappings = {
        "name": ["实验项目", "实验名称", "项目名称", "项目"],
        "date": ["实验日期", "日期", "上课日期"],
        "time": ["实验时间", "时间", "上课时间"],
        "room": ["上课教室", "教室", "地点", "实验室"],
    }
    for key, words in mappings.items():
        for i, h in enumerate(headers):
            if any(w in h for w in words):
                col[key] = i
                break

    experiments: list[dict] = []
    for row in rows[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if not cells:
            continue

        def cell(key):
            i = col.get(key)
            return cells[i] if i is not None and i < len(cells) else ""

        date_str = cell("date")
        time_str = cell("time")
        # 提取第一个 HH:MM，兼容"星期五18:00"/"18:00~21:00"等格式
        m = re.search(r"\d{1,2}:\d{2}", time_str)
        time_start = m.group() if m else ""
        dt = _parse_phycai_dt(date_str, time_start)
        if dt and dt > datetime.now(CST):
            experiments.append({
                "name":     cell("name"),
                "dt":       dt,
                "room":     cell("room"),
                "time_str": time_str,
            })

    if not experiments:
        return None
    experiments.sort(key=lambda x: x["dt"])
    return experiments[0]


def _parse_phycai_dt(date_str: str, time_str: str) -> datetime | None:
    date_str = date_str.strip()
    time_str = time_str.strip()
    date_fmts = ["%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"]
    time_fmts = ["%H:%M", "%H时%M分", "%H:%M:%S"]
    for df in date_fmts:
        for tf in time_fmts:
            try:
                dt = datetime.strptime(f"{date_str} {time_str}", f"{df} {tf}")
                return dt.replace(tzinfo=CST)
            except ValueError:
                continue
    return None


# ── Platform 4: icourse163.org ────────────────────────────────────────────────

def _icourse_login_with_creds(cfg: dict) -> dict | None:
    """
    用 .env 中的 MOOC_USERNAME / MOOC_PASSWORD 登录 icourse163。
    点击首页登录按钮 → 在弹出的 reg.icourse163.org iframe 中填写手机号+密码。
    成功则返回新 cookies dict 并写回 config.json，失败返回 None。
    """
    if not HAS_PLAYWRIGHT:
        return None
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except ImportError:
        pass
    username = os.environ.get("MOOC_USERNAME", "").strip()
    password = os.environ.get("MOOC_PASSWORD", "").strip()
    if not username or not password:
        return None

    _logger.info("[icourse163] 正在用账号密码登录…")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()

            page.goto("https://www.icourse163.org/", wait_until="networkidle", timeout=40_000)
            page.wait_for_timeout(2000)

            # 关掉 AI 助手弹窗（如有）
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            except Exception:
                pass

            # 点击"登录 | 注册"
            page.get_by_text("登录", exact=False).first.click()

            # 等待 reg.icourse163.org 的登录 iframe 出现
            page.wait_for_selector(
                "iframe[src*='reg.icourse163.org'][src*='index_dl2']",
                timeout=15_000,
            )
            page.wait_for_timeout(1000)

            # 找到登录 iframe（优先手机号登录，即 index_dl2）
            login_frame = None
            for frame in page.frames:
                if "reg.icourse163.org" in frame.url and "index_dl2" in frame.url:
                    if frame.locator("input[type='password']").count() > 0:
                        login_frame = frame
                        break

            if login_frame is None:
                _logger.warning("[icourse163] 未找到登录 iframe")
                browser.close()
                return None

            # 填写手机号和密码（取第一个可见的输入框）
            txt_inputs = login_frame.locator(
                "input[type='text'], input[type='tel']"
            ).all()
            txt_field = next((i for i in txt_inputs if i.is_visible()), None)
            if txt_field is None:
                _logger.warning("[icourse163] 未找到手机号输入框")
                browser.close()
                return None
            txt_field.fill(username)

            pwd_inputs = login_frame.locator("input[type='password']").all()
            pwd_field = next((i for i in pwd_inputs if i.is_visible()), None)
            if pwd_field is None:
                _logger.warning("[icourse163] 未找到密码输入框")
                browser.close()
                return None
            pwd_field.fill(password)

            # 点击登录按钮（绿色「登 录」按钮）
            login_frame.get_by_text("登 录", exact=True).click()

            # 等待 modal 消失或页面刷新为已登录状态
            try:
                page.wait_for_function(
                    "() => !document.querySelector('iframe[src*=\"reg.icourse163.org\"]')",
                    timeout=20_000,
                )
            except Exception:
                pass

            page.wait_for_timeout(2000)

            new_cookies = {
                c["name"]: c["value"]
                for c in ctx.cookies()
                if "icourse163.org" in c.get("domain", "")
            }
            browser.close()

            if not new_cookies.get("NTESSTUDYSI"):
                _logger.error("[icourse163] 登录失败，请确认 MOOC_USERNAME / MOOC_PASSWORD 正确")
                return None

            cfg["icourse_cookies"] = new_cookies
            CONFIG_PATH.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _logger.info("[icourse163] ✓ 登录成功，已更新 cookies")
            return new_cookies

    except Exception as e:
        _logger.error(f"[icourse163] 登录出错：{e}")
        return None


def _icourse_warm_up(session: requests.Session) -> bool:
    """访问 icourse163 首页让服务器把 STUDY_INFO/STUDY_SESS/STUDY_PERSIST 设到
    .icourse163.org 域名上。
    必须在调任何受鉴权保护的 RPC 之前调用——否则 requests cookie jar 不会发送
    config.json 里手动加载的 STUDY_* cookies（值含引号/特殊字符），导致服务端
    认为未登录、RPC 返回 result=null。
    返回是否登录态有效。"""
    try:
        session.get("https://www.icourse163.org/", timeout=20)
    except requests.RequestException:
        return False
    # warm-up 后服务端会下发 STUDY_INFO 到 .icourse163.org；只有真登录时才会下发
    for c in session.cookies:
        if c.name == "STUDY_INFO" and ".icourse163.org" in (c.domain or ""):
            return True
    return False


def _icourse_get_my_courses(session: requests.Session) -> list[dict] | None:
    """通过 getMyLearnedCoursePanelList.rpc 获取用户当前所有已注册课程及当前学期。
    返回 [{name, course_id, term_id, school_short_name}, ...]；
    鉴权/网络失败返回 None；用户未注册任何课程返回 []。"""
    url = "https://www.icourse163.org/web/j/learnerCourseRpcBean.getMyLearnedCoursePanelList.rpc"
    csrf_key = session.cookies.get("NTESSTUDYSI", "")
    out: list[dict] = []

    # courseType: 1=MOOC, 2=SPOC。type=30 是"全部"——和官网"我的课程"行为一致。
    for course_type in ("1", "2"):
        page_index = 1
        while page_index < 20:  # 防御性上限
            try:
                r = session.post(
                    url,
                    params={"csrfKey": csrf_key},
                    data={"type": "30", "p": str(page_index), "psize": "20", "courseType": course_type},
                    timeout=15,
                )
            except requests.RequestException as e:
                _logger.warning(f"[icourse163] 拉取课程列表网络错误：{e}")
                return None
            if r.status_code != 200:
                return None
            try:
                data = r.json()
            except json.JSONDecodeError:
                return None
            if data.get("code") != 0:
                return None
            result = data.get("result") or {}
            items = result.get("result") or []
            for c in items:
                term = c.get("termPanel") or {}
                term_id = term.get("id")
                course_id = c.get("id")
                if not term_id or not course_id:
                    continue
                school = c.get("schoolPanel") or {}
                out.append({
                    "name": c.get("name") or "未命名课程",
                    "course_id": int(course_id),
                    "term_id": int(term_id),
                    "school_short_name": (school.get("shortName") or "").strip(),
                })
            pagination = result.get("pagination") or {}
            total_pages = pagination.get("totlePageCount") or 1
            if page_index >= total_pages or not items:
                break
            page_index += 1
    return out


def _icourse_rpc(session: requests.Session, term_id: int,
                 course_id: int = 0, school: str = "") -> dict | None:
    """调用 courseBean.getLastLearnedMocTermDto.rpc 拿到完整课程结构（章节/quiz/exam）。
    调用前必须先 _icourse_warm_up；Referer 必须指向具体 learn 页，否则部分账号会得到
    result=null（疑似服务端 anti-bot 检查）。"""
    url = "https://www.icourse163.org/web/j/courseBean.getLastLearnedMocTermDto.rpc"
    csrf_key = session.cookies.get("NTESSTUDYSI", "")
    referer = (
        f"https://www.icourse163.org/learn/{school}-{course_id}?tid={term_id}"
        if school and course_id
        else "https://www.icourse163.org/"
    )
    try:
        r = session.post(
            url,
            params={"csrfKey": csrf_key},
            data={"termId": str(term_id)},
            headers={"Referer": referer},
            timeout=15,
        )
        if r.status_code == 200 and "application/json" in r.headers.get("Content-Type", ""):
            data = r.json()
            if data.get("result"):
                return data["result"]
    except (requests.RequestException, json.JSONDecodeError):
        pass
    return None


def fetch_icourse(cfg: dict) -> list[dict]:
    """获取中国大学MOOC各课程未完成的测试/考试。

    流程：
      1. 用 config.icourse_cookies 建 session 并 warm-up 首页
      2. warm-up 成功（说明 cookies 有效）→ 调 panel list 拿用户所有课程 + 当前学期
      3. warm-up 失败（cookies 过期）→ 用 MOOC_USERNAME/MOOC_PASSWORD 重登并重试
      4. 对每门课用 termId 拉章节，解析 quiz/exam 的 deadline + 分数
    """
    cookies = cfg.get("icourse_cookies") or {}

    session: requests.Session | None = None
    if cookies.get("NTESSTUDYSI"):
        session = make_session(cookies)
        if not _icourse_warm_up(session):
            session = None  # cookies 已失效

    if session is None:
        _logger.info("[icourse163] cookies 失效或未配置，正在登录…")
        new_cookies = _icourse_login_with_creds(cfg)
        if not new_cookies:
            _logger.warning("[icourse163] ⚠ 登录失败，跳过")
            return []
        cookies = new_cookies
        session = make_session(cookies)
        if not _icourse_warm_up(session):
            _logger.warning("[icourse163] ⚠ 登录成功但 warm-up 仍失败，跳过")
            return []

    courses = _icourse_get_my_courses(session)
    if courses is None:
        _logger.warning("[icourse163] ⚠ 无法拉取课程列表，跳过")
        return []
    if not courses:
        _logger.info("[icourse163] 用户未注册任何 MOOC/SPOC 课程")
        return []

    _logger.info(f"[icourse163] 已发现 {len(courses)} 门课程：{', '.join(c['name'] for c in courses)}")
    results: list[dict] = []
    for course in courses:
        rpc_result = _icourse_rpc(
            session, course["term_id"],
            course_id=course["course_id"],
            school=course["school_short_name"],
        )
        if rpc_result is None:
            _logger.warning(f"[icourse163] ⚠ {course['name']} 拉取章节失败 (term={course['term_id']})")
            continue
        results.extend(_parse_icourse_rpc(rpc_result, course["name"]))
    return results


def _parse_icourse_rpc(result: dict, cname: str) -> list[dict]:
    results = []
    # result 可能是 {"mocTermDto": {...}, ...} 或直接就是 mocTermDto
    moc = result.get("mocTermDto") or result
    now = datetime.now(CST)  # 使用实时时间，避免模块导入时 NOW 冻结的问题

    # 从章节的 quizs 列表中查找测试（实际数据结构）
    for chapter in moc.get("chapters", []):
        for quiz in chapter.get("quizs") or []:
            test = quiz.get("test") or {}
            score = test.get("userScore") or test.get("testScore")
            if score is not None and float(score) > 0:
                continue
            deadline_ms = test.get("deadline")
            if not deadline_ms:
                continue
            due = datetime.fromtimestamp(int(deadline_ms) / 1000, tz=CST)
            if due < now:
                continue
            used = test.get("usedTryCount") or 0
            results.append({
                "platform": "icourse163",
                "course": cname,
                "name": quiz.get("name") or "未知测试",
                "due": due,
                "submitted": int(used) > 0,
            })

    # 从章节结构中查找测验单元 (contentType=5)（兼容旧格式）
    for chapter in moc.get("chapters", []):
        for lesson in chapter.get("lessons", []):
            for unit in lesson.get("units", []):
                if unit.get("contentType") != 5:
                    continue
                score = unit.get("testScore")
                if score is not None and float(score) > 0:
                    continue
                deadline_ms = unit.get("deadline") or unit.get("testEndTime")
                if not deadline_ms:
                    continue
                due = datetime.fromtimestamp(int(deadline_ms) / 1000, tz=CST)
                if due < now:
                    continue
                results.append({
                    "platform": "icourse163",
                    "course": cname,
                    "name": unit.get("name") or "未知测试",
                    "due": due,
                    "submitted": False,
                })

    # 从 exams 列表中查找考试
    for exam in moc.get("exams") or []:
        score = exam.get("userScore") or exam.get("testScore")
        if score is not None and float(score) > 0:
            continue
        deadline_ms = exam.get("endTime") or exam.get("deadline")
        if not deadline_ms:
            continue
        due = datetime.fromtimestamp(int(deadline_ms) / 1000, tz=CST)
        if due < now:
            continue
        results.append({
            "platform": "icourse163",
            "course": cname,
            "name": exam.get("name") or exam.get("title") or "未知考试",
            "due": due,
            "submitted": False,
        })

    return results


# ── 输出格式化 ────────────────────────────────────────────────────────────────

def print_report(ddl_items: list[dict], lab: dict | None) -> None:
    print("\n===== 近期必做 DDL（按截止时间排序）=====")
    # 过滤掉已提交的
    pending = [x for x in ddl_items if not x.get("submitted")]
    if not pending:
        print("  （暂无即将到来的必做任务）")
    else:
        for item in pending:
            label    = deadline_label(item["due"])
            platform = f"[{item['platform']}]"
            print(f"⚠️  [{label}] {platform} {item['course']} · {item['name']}"
                  f"  截止：{fmt_due(item['due'])}")

    print("\n===== 下一次物理实验 =====")
    if not lab:
        print("  （未获取到实验安排，请检查 phycai_cookies 或页面结构）")
    else:
        dt      = lab["dt"]
        weekday = WEEKDAY_ZH[dt.weekday()]
        print(f"  {lab['name']}")
        print(f"  时间：{dt.strftime('%Y/%m/%d')} ({weekday}) {lab['time_str']}")
        print(f"  地点：{lab['room']}")
    print()


# ── 主入口 ────────────────────────────────────────────────────────────────────

# ── Assignment Download ───────────────────────────────────────────────────────

def _safe_fname(s: str) -> str:
    """将字符串转换为合法的文件/目录名。"""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', s).strip("._") or "unnamed"


def _matches_assignment_download_filters(
    course_name: str,
    assignment_name: str,
    due: datetime | None,
    *,
    course_filter: str = "",
    assignment_filter: str = "",
    due_within_days: int | None = 7,
) -> bool:
    if course_filter and course_filter.lower() not in course_name.lower():
        return False
    if assignment_filter and assignment_filter.lower() not in assignment_name.lower():
        return False
    if due is not None and due_within_days is not None and due_within_days >= 0:
        now = datetime.now(CST)
        if due > now + timedelta(days=due_within_days):
            return False
    return True


def _download_canvas_assignment(
    session: requests.Session,
    base: str,
    course_id: int,
    assignment_id: int,
    course_name: str,
    assignment_name: str,
    out: "Path",
) -> list[str]:
    """下载单个 Canvas 作业的题目说明和附件，返回已保存的文件路径列表。"""
    dest = out / _safe_fname(course_name) / _safe_fname(assignment_name)
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []

    # 获取作业详情
    try:
        r = session.get(
            f"{base}/api/v1/courses/{course_id}/assignments/{assignment_id}",
            params={"include[]": "attachments"},
            timeout=15,
        )
        r.raise_for_status()
        detail = r.json()
    except Exception as e:
        print(f"    ✗ 获取详情失败：{e}")
        return []

    desc = detail.get("description") or ""

    # 1. 保存题目说明 HTML（保留原始内容用于备查）
    if desc:
        html_path = dest / "description.html"
        html_path.write_text(
            f"<meta charset='utf-8'><h1>{assignment_name}</h1>\n{desc}",
            encoding="utf-8",
        )
        saved.append(str(html_path))

    # 2. 从 description 提取 Canvas 文件引用
    #    SJTU Canvas 使用两种格式：
    #      a) data-api-endpoint="https://.../api/v1/courses/.../files/<id>"  ← 优先
    #      b) href="https://.../courses/.../files/<id>?verifier=...&wrap=1"
    #      c) href="https://.../files/<id>/download?..."                     ← 旧格式
    file_api_urls: set[str] = set()

    # 格式 a: data-api-endpoint 属性（最可靠）
    for m in re.finditer(r'data-api-endpoint="([^"]+/api/v1/[^"/]+/files/\d+)"', desc):
        file_api_urls.add(m.group(1))

    # 格式 b: courses/.../files/<id>?...
    for m in re.finditer(r'href="([^"]*?/courses/\d+/files/(\d+)\?[^"]*)"', desc):
        file_api_urls.add(f"{base}/api/v1/files/{m.group(2)}")

    # 格式 c: /files/<id>/download
    for m in re.finditer(r'href="([^"]*?/files/(\d+)/download[^"]*)"', desc):
        file_api_urls.add(f"{base}/api/v1/files/{m.group(2)}")

    # 3. 作业级别 attachments（部分 Canvas 实例支持）
    for att in detail.get("attachments") or []:
        aid = att.get("id")
        if aid:
            file_api_urls.add(f"{base}/api/v1/files/{aid}")

    # 4. 对每个文件调用 Files API 拿真实下载 URL，然后下载
    for api_url in file_api_urls:
        try:
            meta_r = session.get(api_url, timeout=10)
            meta_r.raise_for_status()
            meta = meta_r.json()
        except Exception as e:
            print(f"    ✗ 获取文件元信息失败 {api_url}：{e}")
            continue

        download_url = meta.get("url")
        fname = meta.get("filename") or meta.get("display_name") or "attachment"
        if not download_url:
            continue

        fpath = dest / _safe_fname(fname)
        try:
            fr = session.get(download_url, timeout=60, stream=True, allow_redirects=True)
            fr.raise_for_status()
            # 如果响应头里有更准确的文件名，用它
            cd = fr.headers.get("Content-Disposition", "")
            m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";\r\n]+)\"?", cd, re.I)
            if m:
                fname = urllib.parse.unquote(m.group(1).strip())
                fpath = dest / _safe_fname(fname)
            with open(fpath, "wb") as f:
                for chunk in fr.iter_content(8192):
                    f.write(chunk)
            saved.append(str(fpath))
            print(f"    ✓ {fname} ({meta.get('size', 0) // 1024} KB)")
        except Exception as e:
            print(f"    ✗ 下载 {fname} 失败：{e}")

    return saved


def download_canvas_assignments(
    cfg: dict,
    output_dir: str = "./assignments",
    course_filter: str = "",
    assignment_filter: str = "",
    due_within_days: int | None = 7,
    include_past: bool = False,
) -> list[dict]:
    """下载符合过滤条件的 Canvas 作业题目说明和附件，返回下载结果列表。"""
    token = cfg.get("canvas_token", "").strip()
    base  = cfg.get("canvas_base_url", "https://oc.sjtu.edu.cn").rstrip("/")
    if not token or token.startswith("YOUR_"):
        return [{"error": "未配置 canvas_token"}]

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    out = Path(output_dir)

    # 获取在修课程
    courses: list[dict] = []
    url: str | None = f"{base}/api/v1/courses"
    params: dict = {"enrollment_state": "active", "per_page": 100}
    while url:
        r = session.get(url, params=params, timeout=15)
        r.raise_for_status()
        courses.extend(r.json())
        url = r.links.get("next", {}).get("url")
        params = {}

    results: list[dict] = []
    for course in courses:
        cid   = course["id"]
        cname = course.get("name", f"课程{cid}")

        asgn_url: str | None = f"{base}/api/v1/courses/{cid}/assignments"
        asgn_params: dict = {"per_page": 100, "order_by": "due_at"}
        while asgn_url:
            try:
                r = session.get(asgn_url, params=asgn_params, timeout=15)
                r.raise_for_status()
            except Exception as e:
                _logger.warning(f"[Canvas] {cname} 获取作业列表失败：{e}")
                break
            for a in r.json():
                if a.get("workflow_state") == "deleted":
                    continue
                due = parse_dt(a.get("due_at", ""))
                if not include_past and (not due or due < datetime.now(CST)):
                    continue
                assignment_name = a.get("name", "未知作业")
                if not _matches_assignment_download_filters(
                    cname,
                    assignment_name,
                    due,
                    course_filter=course_filter,
                    assignment_filter=assignment_filter,
                    due_within_days=due_within_days,
                ):
                    continue
                print(f"[Canvas] ↓ {cname} / {assignment_name}")
                files = _download_canvas_assignment(
                    session, base, cid, a["id"], cname, assignment_name, out
                )
                results.append({
                    "platform": "Canvas",
                    "course": cname,
                    "name": assignment_name,
                    "due": due.isoformat(),
                    "files": files,
                    "output_dir": str(out / _safe_fname(cname) / _safe_fname(assignment_name)),
                })
            asgn_url = r.links.get("next", {}).get("url")
            asgn_params = {}

    return results


# ── aihaoke JS（含 id 字段，供下载使用）──────────────────────────────────────

_AIHAOKE_TASK_FULL_JS = """
() => {
  const pinia = document.querySelector('#app')?.__vue_app__
                 ?.config?.globalProperties?.$pinia;
  if (!pinia) return null;
  const state = pinia.state.value?.studentTaskStore?.studentTaskState;
  if (!state) return null;
  return (state.menuData || []).map(t => ({
    id:          t.id ?? t.taskId ?? t.homeworkId ?? null,
    taskName:    t.taskName,
    taskType:    t.taskType,
    requireFlag: t.requireFlag,
    myStatus:    t.myStatus,
    endTime:     t.endTime,
  }));
}
"""

_AIHAOKE_PAGE_FILES_JS = r"""
() => {
  const links = Array.from(document.querySelectorAll('a[href]'));
  return links
    .map(a => ({ href: a.href, text: a.textContent.trim() }))
    .filter(l => l.href && (
      l.href.includes('/download') ||
      l.href.includes('attachment') ||
      /\.(pdf|doc|docx|ppt|pptx|xls|xlsx|zip|rar|7z|png|jpg|mp4)(\?|$)/i.test(l.href)
    ));
}
"""


def download_aihaoke_assignments(
    cfg: dict,
    output_dir: str = "./assignments",
    course_filter: str = "",
    assignment_filter: str = "",
    due_within_days: int | None = 7,
) -> list[dict]:
    """用 Playwright 登录 aihaoke，下载符合过滤条件的作业说明页面和附件。"""
    if not HAS_PLAYWRIGHT:
        return [{"error": "未安装 playwright，请运行 pip install playwright && playwright install chromium"}]

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ok, error = refresh_aihaoke_cookies(cfg)
    if not ok:
        return [{"error": error}]

    raw_cookies = cfg.get("aihaoke_cookies", {})

    out = Path(output_dir)
    results: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            accept_downloads=True,
        )
        pw_cookies = [
            {"name": k, "value": v, "domain": "sjtu.aihaoke.net", "path": "/"}
            for k, v in raw_cookies.items()
        ]
        if pw_cookies:
            ctx.add_cookies(pw_cookies)

        page = ctx.new_page()

        # 优先使用用户实际选修课程；API 不可用时跳过，不使用硬编码列表
        token = raw_cookies.get("haoke-token", "")
        enrolled = cfg.get("aihaoke_courses") or (
            _fetch_aihaoke_enrolled_courses({
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }) if token else None
        )
        if not enrolled:
            browser.close()
            return [{"error": "无法获取 aihaoke 选修课程列表，跳过下载"}]

        for course in enrolled:
            cid   = course["courseId"]
            iid   = course.get("instanceId", cid)
            cname = course["name"]

            list_url = (
                f"https://sjtu.aihaoke.net/student/course/{cid}/task"
                f"?instanceId={iid}&taskType=all-tasks"
            )
            try:
                page.goto(list_url, wait_until="networkidle", timeout=30_000)
                page.wait_for_function(
                    "() => document.querySelector('#app')?.__vue_app__"
                    "?.config?.globalProperties?.$pinia"
                    "?.state?.value?.studentTaskStore?.studentTaskState?.menuData?.length > 0",
                    timeout=10_000,
                )
                tasks = page.evaluate(_AIHAOKE_TASK_FULL_JS) or []
            except Exception as e:
                _logger.warning(f"  [aihaoke] {cname} 加载失败：{e}")
                continue

            for t in tasks:
                if t.get("requireFlag") != 1:
                    continue
                if t.get("myStatus") != 10:
                    continue
                if t.get("taskType") not in (30, 50, 51, 70):
                    continue
                due = parse_dt(t.get("endTime", ""))
                if not due or due < datetime.now(CST):
                    continue

                task_id   = t.get("id")
                task_name = (t.get("taskName") or "未知任务").strip()
                if not _matches_assignment_download_filters(
                    cname,
                    task_name,
                    due,
                    course_filter=course_filter,
                    assignment_filter=assignment_filter,
                    due_within_days=due_within_days,
                ):
                    continue
                dest = out / _safe_fname(cname) / _safe_fname(task_name)
                dest.mkdir(parents=True, exist_ok=True)
                saved: list[str] = []

                # 尝试导航到任务详情页
                detail_url = None
                if task_id:
                    detail_url = (
                        f"https://sjtu.aihaoke.net/student/course/{cid}"
                        f"/task/{task_id}?instanceId={iid}"
                    )

                print(f"[aihaoke] ↓ {cname} / {task_name}")
                try:
                    nav_url = detail_url or list_url
                    page.goto(nav_url, wait_until="networkidle", timeout=20_000)

                    # 保存页面截图作为"说明"备用
                    ss_path = dest / "screenshot.png"
                    page.screenshot(path=str(ss_path), full_page=True)
                    saved.append(str(ss_path))
                    print(f"    ✓ screenshot.png")

                    # 提取页面中的下载链接并下载
                    file_links = page.evaluate(_AIHAOKE_PAGE_FILES_JS) or []
                    for lnk in file_links:
                        href = lnk.get("href", "")
                        if not href or href.startswith("blob:"):
                            continue
                        try:
                            with page.expect_download(timeout=30_000) as dl_info:
                                page.evaluate(f"window.open('{href}', '_blank')")
                            download = dl_info.value
                            fname = download.suggested_filename or href.split("/")[-1].split("?")[0]
                            fpath = dest / _safe_fname(fname)
                            download.save_as(str(fpath))
                            saved.append(str(fpath))
                            print(f"    ✓ {fname}")
                        except Exception:
                            pass
                except Exception as e:
                    print(f"    ✗ 无法获取详情：{e}")

                results.append({
                    "platform": "aihaoke",
                    "course": cname,
                    "name": task_name,
                    "due": due.isoformat(),
                    "files": saved,
                    "output_dir": str(dest),
                })

        browser.close()
    return results


def download_assignments(
    cfg: dict,
    output_dir: str = "./assignments",
    skip_canvas: bool = False,
    skip_aihaoke: bool = False,
    course_filter: str = "",
    assignment_filter: str = "",
    due_within_days: int | None = 7,
    include_past: bool = False,
) -> list[dict]:
    """下载所有平台内符合过滤条件的近期作业材料。"""
    results: list[dict] = []
    if not skip_canvas:
        print("[*] 下载 Canvas 作业材料…")
        results.extend(
            download_canvas_assignments(
                cfg,
                output_dir,
                course_filter,
                assignment_filter,
                due_within_days,
                include_past=include_past,
            )
        )
    if not skip_aihaoke:
        print("[*] 下载 aihaoke 作业材料…")
        results.extend(
            download_aihaoke_assignments(
                cfg,
                output_dir,
                course_filter,
                assignment_filter,
                due_within_days,
            )
        )
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 校园内容搜索
# ══════════════════════════════════════════════════════════════════════════════

# jwc RSS feeds（教务处通知公告）
_JWC_RSS_FEEDS = [
    # 通知公告
    "https://jwc.sjtu.edu.cn/system/resource/code/rss/rssfeed.jsp"
    "?type=list&treeid=1292&viewid=1011878&mode=10&dbname=vsb"
    "&owner=1707467176&ownername=jwc2021&contentid=1015253&number=50&httproot=",
]


def _search_jwc(query: str, max_results: int = 8) -> list[dict]:
    """从教务处 RSS 按关键词搜索通知公告。"""
    import xml.etree.ElementTree as ET

    items: list[dict] = []
    for url in _JWC_RSS_FEEDS:
        try:
            r = requests.get(url, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                desc  = (item.findtext("description") or "").strip()
                link  = (item.findtext("link") or "").strip()
                date  = (item.findtext("pubDate") or "").strip()
                items.append({"title": title, "summary": desc[:300], "url": link, "date": date})
        except Exception as e:
            _logger.warning(f"[jwc] RSS 获取失败：{e}")

    if not items:
        return [{"error": "无法获取教务处 RSS，网络不通或地址变更"}]

    q = query.lower()
    matched = [i for i in items
               if q in i["title"].lower() or q in i["summary"].lower()]
    # 没有关键词匹配时返回最新几条
    if not matched:
        return items[:max_results]
    return matched[:max_results]


def _search_shuiyuan(cfg: dict, query: str, max_results: int = 5) -> list[dict]:
    """搜索水源社区：优先用 User API Key，回退到 session cookie。"""
    api_key   = cfg.get("shuiyuan_user_api_key", "").strip()
    client_id = cfg.get("shuiyuan_user_api_client_id", "").strip()
    session   = cfg.get("shuiyuan_cookies", {})

    if not api_key and not session:
        return [{"error": "水源社区未配置，请对 Agent 说「配置水源」完成登录"}]

    if api_key:
        req_kwargs = {
            "headers": {
                "User-Api-Key":       api_key,
                "User-Api-Client-Id": client_id,
                "Accept":             "application/json",
            }
        }
    else:
        req_kwargs = {
            "cookies": session,
            "headers": {"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        }

    try:
        r = requests.get(
            "https://shuiyuan.sjtu.edu.cn/search.json",
            params={"q": query, "page": 1},
            timeout=10,
            **req_kwargs,
        )
        if r.status_code in (401, 403) or "login" in r.url:
            return [{"error": "水源社区凭证已过期，请对 Agent 说「配置水源」重新授权"}]
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [{"error": f"水源社区搜索失败：{e}"}]

    topics   = data.get("topics") or []
    posts    = data.get("posts") or []
    post_map = {p.get("topic_id"): p for p in posts}
    results: list[dict] = []
    for t in topics[:max_results]:
        tid  = t.get("id")
        slug = t.get("slug", "")
        post = post_map.get(tid, {})
        results.append({
            "title":    t.get("fancy_title") or t.get("title", ""),
            "url":      f"https://shuiyuan.sjtu.edu.cn/t/{slug}/{tid}",
            "excerpt":  post.get("blurb", ""),
            "replies":  t.get("posts_count", 0),
            "views":    t.get("views", 0),
            "category": t.get("category_id"),
        })
    if not results:
        return [{"message": f"水源社区没有找到关于「{query}」的帖子"}]
    return results


_DYWEB_API = "https://api-v2.share.dyweb.sjtu.cn/api/v1"
_DYWEB_MATERIAL_TYPES = {1: "课件", 2: "答案", 3: "实验报告", 4: "参考书", 5: "试卷", 6: "其他"}


def _dyweb_refresh_token(cfg: dict) -> str:
    """通过 Playwright + jAccount OAuth 刷新 sjtu_token。新站 (2026-06) 使用
    OAuth2 授权码流程：获取 config → 跳转 jAccount → 填密码登录 → 回调 dyweb。"""
    if not HAS_PLAYWRIGHT:
        return ""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    jc_user = os.environ.get("JACCOUNT_USERNAME", "").strip()
    jc_pwd = os.environ.get("JACCOUNT_PASSWORD", "").strip()
    if not jc_user or not jc_pwd:
        return ""

    import time, urllib.parse
    # Step 1: Get OAuth config
    try:
        r = requests.get(
            "https://api-v2.share.dyweb.sjtu.cn/auth/jaccount/config",
            params={"redirect_uri": "https://share.dyweb.sjtu.cn/auth/jaccount/callback"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        oauth_cfg = r.json().get("data", {})
        if not oauth_cfg.get("client_id"):
            return ""
    except Exception:
        return ""

    params = {
        "response_type": "code",
        "client_id": oauth_cfg["client_id"],
        "redirect_uri": oauth_cfg["redirect_uri"],
        "state": oauth_cfg["state"],
        "scope": "profile",
    }
    auth_url = ("https://jaccount.sjtu.edu.cn/oauth2/authorize?"
                + urllib.parse.urlencode(params))

    # Step 2: Playwright — fill jAccount form, submit, follow to dyweb
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        token = ""
        try:
            page.goto(auth_url, wait_until="networkidle", timeout=30_000)
            time.sleep(2)

            # Fill jAccount password login form
            user_el = page.locator("#input-login-user")
            pass_el = page.locator("#input-login-pass")
            if user_el.count() > 0 and pass_el.count() > 0:
                user_el.fill(jc_user)
                pass_el.fill(jc_pwd)
                submit = page.locator("#submit-password-button")
                if submit.count() > 0 and submit.is_visible():
                    submit.click()
                    time.sleep(3)

                # Handle CAPTCHA if triggered
                captcha = page.locator("#input-login-captcha")
                if captcha.count() > 0 and captcha.is_visible():
                    captcha_img = page.locator("#captcha-img, img[src*=captcha]")
                    if captcha_img.count() > 0:
                        try:
                            img_bytes = captcha_img.first.screenshot()
                            from login import _solve_captcha
                            code = _solve_captcha(img_bytes)
                            if code:
                                captcha.fill(code)
                                if submit.count() > 0 and submit.is_visible():
                                    submit.click()
                                    time.sleep(3)
                        except Exception:
                            pass

            # Follow redirects to dyweb callback (page body contains JWT)
            for _ in range(12):
                if "share.dyweb.sjtu.cn" in page.url:
                    break
                time.sleep(2)

            # New site uses JWT Bearer token, not cookie
            if "share.dyweb.sjtu.cn" in page.url:
                try:
                    body = page.locator("body").text_content()
                    data = json.loads(body) if body else {}
                    token = data.get("data", "")
                except Exception:
                    token = ""
            # Fallback: check for cookie (old site)
            if not token:
                for c in ctx.cookies():
                    if c["name"] == "sjtu_token":
                        token = c["value"]
                        break
        except Exception as e:
            _logger.warning(f"[dyweb] OAuth 失败：{e}")
        finally:
            browser.close()

    if token:
        cfg["dyweb_token"] = token
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        _logger.info("[dyweb] ✓ token 已更新")
    return token


def _dyweb_request(cfg: dict, method: str, path: str, **kwargs) -> dict | None:
    """带 token 自动刷新的 API 请求，返回 JSON data 或 None。

    新版 API (2026-06): api-v2.share.dyweb.sjtu.cn/api/v1
    """
    token = cfg.get("dyweb_token", "")
    if not token:
        token = _dyweb_refresh_token(cfg)
        if not token:
            return None

    def _call(tok: str):
        cookies = {"sjtu_token": tok}
        headers = {"User-Agent": "Mozilla/5.0",
                   "Origin": "https://share.dyweb.sjtu.cn",
                   "Referer": "https://share.dyweb.sjtu.cn/"}
        if method == "GET":
            return requests.get(f"{_DYWEB_API}{path}", cookies=cookies, headers=headers, timeout=10, **kwargs)
        return requests.post(f"{_DYWEB_API}{path}", cookies=cookies, headers=headers, timeout=10, **kwargs)

    r = _call(token)
    if r.status_code == 401:
        token = _dyweb_refresh_token(cfg)
        if not token:
            return None
        r = _call(token)

    if r.status_code != 200:
        return None
    data = r.json()
    if isinstance(data, dict) and not data.get("success", True):
        _logger.warning(f"[dyweb] API error: {data.get('message', '')}")
        return None
    return data.get("data")


def _search_dyweb(cfg: dict, query: str, max_results: int = 6,
                  material_type: str = "") -> list[dict]:
    """在传承·交大搜索资料，返回课程+资料列表。"""
    # 搜索课程
    courses_data = _dyweb_request(cfg, "POST", "/course/search",
                                  json={"keyword": query, "page": 1, "page_size": max_results})
    if courses_data is None:
        return [{"error": "传承·交大 API 不可用，请检查 jAccount 配置"}]

    courses = courses_data if isinstance(courses_data, list) else []
    if not courses:
        return [{"message": f"传承·交大没有找到与「{query}」相关的课程"}]

    # 反查材料类型 id
    type_id = None
    for tid, name in _DYWEB_MATERIAL_TYPES.items():
        if material_type and material_type in name:
            type_id = tid
            break

    results: list[dict] = []
    for course in courses[:max_results]:
        cid   = course.get("id")
        cname = course.get("name", "")
        ccode = course.get("code", "")
        org   = (course.get("organization") or {}).get("name", "")

        # 获取该课程的资料
        params: dict = {"course_id": cid}
        if type_id:
            params["material_type_id"] = type_id
        mats_data = _dyweb_request(cfg, "GET", "/material", params=params)
        if mats_data is None:
            continue

        # 新版 API (2026-06): materials nested in classes[].materials[]
        all_mats = []
        classes = mats_data.get("classes") or [] if isinstance(mats_data, dict) else []
        for cls in classes:
            for m in (cls.get("materials") or []):
                all_mats.append(m)
        # Fallback: old format with unarchived/archived
        if not all_mats:
            unarchived = mats_data.get("unarchived") or [] if isinstance(mats_data, dict) else []
            archived   = mats_data.get("archived") or [] if isinstance(mats_data, dict) else []
            all_mats   = unarchived + archived

        # 过滤并排序（按下载量）
        if query:
            q = query.lower()
            filtered = [m for m in all_mats if q in (m.get("name") or "").lower()
                        or q in (m.get("description") or "").lower()]
            if not filtered:
                filtered = all_mats
        else:
            filtered = all_mats

        filtered.sort(key=lambda m: m.get("purchase_count") or m.get("download_count") or 0, reverse=True)

        materials = []
        for m in filtered[:8]:
            mt = m.get("material_type") or {}
            mtype = mt.get("name", "其他") if isinstance(mt, dict) else _DYWEB_MATERIAL_TYPES.get(m.get("material_type_id", 0), "其他")
            materials.append({
                "name":      m.get("name", "") or m.get("file_name", ""),
                "type":      mtype,
                "ext":       m.get("ext", ""),
                "downloads": m.get("purchase_count") or m.get("download_count", 0),
                "points":    m.get("points") or m.get("point", 0),
                "url":       f"https://share.dyweb.sjtu.cn/course/{cid}",
            })

        results.append({
            "course":    cname,
            "code":      ccode,
            "org":       org,
            "course_url": f"https://share.dyweb.sjtu.cn/course/{cid}",
            "materials": materials,
        })

    if not results:
        return [{"message": f"传承·交大没有找到与「{query}」相关的资料"}]
    return results


def search_campus(
    cfg: dict,
    query: str,
    sites: list[str] | None = None,
    max_results: int = 6,
) -> dict:
    """在交大校园相关网站搜索内容。

    sites 可选值：'jwc'（教务处通知）、'shuiyuan'（水源社区）、'dyweb'（传承·交大资料）
    默认三者都搜。
    """
    if sites is None:
        sites = ["jwc", "shuiyuan", "dyweb"]
    out: dict = {}
    if "jwc" in sites:
        _logger.info(f"[搜索] 教务处通知：{query}…")
        out["jwc"] = _search_jwc(query, max_results)
    if "shuiyuan" in sites:
        _logger.info(f"[搜索] 水源社区：{query}…")
        out["shuiyuan"] = _search_shuiyuan(cfg, query, max_results)
    if "dyweb" in sites:
        _logger.info(f"[搜索] 传承·交大：{query}…")
        out["dyweb"] = _search_dyweb(cfg, query, max_results)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 课表（JWXT 教学信息服务网）
# ══════════════════════════════════════════════════════════════════════════════

# 每节课的开始/结束时间（参考 CourseBlock / SJTU 作息）
_SLOT_TIMES: list[tuple[str, str]] = [
    ("8:00",  "8:45"),   # 第 1 节
    ("8:55",  "9:40"),   # 第 2 节
    ("10:00", "10:45"),  # 第 3 节
    ("10:55", "11:40"),  # 第 4 节
    ("12:00", "12:45"),  # 第 5 节
    ("12:55", "13:40"),  # 第 6 节
    ("14:00", "14:45"),  # 第 7 节
    ("14:55", "15:40"),  # 第 8 节
    ("16:00", "16:45"),  # 第 9 节
    ("16:55", "17:40"),  # 第 10 节
    ("18:00", "18:45"),  # 第 11 节
    ("18:55", "19:40"),  # 第 12 节
    ("20:00", "20:45"),  # 第 13 节
    ("20:55", "21:40"),  # 第 14 节
]

_WEEKDAY_CN = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _parse_week_set(zcd: str) -> set[int]:
    """解析周次字符串（如 '1-16周' '1,3,5-10周(单)'）为周次集合。"""
    weeks: set[int] = set()
    for part in zcd.split(","):
        part = part.strip()
        step = 1
        if "(单)" in part:
            step = 2
            part = part.replace("(单)", "")
        if "(双)" in part:
            step = 2
            part = part.replace("(双)", "")
        part = part.replace("周", "").strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            for w in range(int(a), int(b) + 1, step):
                weeks.add(w)
        elif part.isdigit():
            weeks.add(int(part))
    return weeks


def _parse_jcs(jcs: str) -> tuple[int, int]:
    """'3-4' → (3, 4)；单节 '5' → (5, 5)"""
    parts = jcs.split("-")
    start = int(parts[0])
    end = int(parts[-1])
    return start, end


def _get_jwxt_cookies(cfg: dict) -> dict | None:
    """获取 JWXT session cookies，优先复用已保存的，失效时走 Playwright 刷新。"""
    saved = cfg.get("jwxt_cookies", {})
    if saved:
        try:
            r = requests.post(
                "https://i.sjtu.edu.cn/kbcx/xskbcx_cxXsKb.html",
                data={"xnm": "2025", "xqm": "12"},
                cookies=saved,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if r.status_code == 200 and "kbList" in r.text:
                return saved
        except Exception:
            pass
        _logger.info("[jwxt] session 已过期，重新登录…")

    if not HAS_PLAYWRIGHT:
        return None
    jaccount_cookies = cfg.get("jaccount_cookies", {})
    if not jaccount_cookies:
        return None

    import time
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([
            {"name": k, "value": v, "domain": "jaccount.sjtu.edu.cn", "path": "/"}
            for k, v in jaccount_cookies.items()
        ])
        page = ctx.new_page()
        try:
            page.goto("https://i.sjtu.edu.cn/jaccountlogin",
                      wait_until="networkidle", timeout=20_000)
            time.sleep(2)
        except Exception as e:
            _logger.error(f"[jwxt] 登录失败: {e}")
            browser.close()
            return None
        cookies = {c["name"]: c["value"] for c in ctx.cookies()
                   if "i.sjtu.edu.cn" in c.get("domain", "")}
        browser.close()

    if cookies:
        cfg["jwxt_cookies"] = cookies
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        _logger.info("[jwxt] ✓ cookies 已更新")
        return cookies
    return None


def _auto_year_term() -> tuple[str, str]:
    """根据当前月份自动判断学年和 xqm 参数。"""
    m = NOW.month
    y = NOW.year
    if m >= 9:                    # 秋季（当年9月 - 次年1月）
        return str(y), "3"
    elif m == 1:                  # 仍属上一学年秋季
        return str(y - 1), "3"
    else:                         # 春季（2-8月）
        return str(y - 1), "12"


def fetch_schedule(cfg: dict, year: str = "", term: str = "", refresh: bool = False) -> dict:
    """
    从 SJTU 教学信息服务网获取完整课表。优先读取本地缓存，同一学期内不重复请求。

    year: 学年（如 '2025' 表示 2025-2026），留空自动判断
    term: '1'=秋季 / '2'=春季，留空自动判断
    refresh: True 时强制忽略缓存重新拉取
    返回 {courses, year, term, total, cached}
    """
    auto_year, auto_xqm = _auto_year_term()
    if not year:
        year = auto_year
    if not term:
        xqm  = auto_xqm
        term = "1" if xqm == "3" else "2"
    else:
        xqm = "3" if term == "1" else "12"

    cache_key = f"{year}-{term}"

    # ── 读缓存 ──────────────────────────────────────────────────────────────
    if not refresh and _SCHEDULE_CACHE_PATH.exists():
        try:
            cached = json.loads(_SCHEDULE_CACHE_PATH.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key:
                cached["cached"] = True
                return cached
        except Exception:
            pass

    # ── 网络请求 ─────────────────────────────────────────────────────────────
    cookies = _get_jwxt_cookies(cfg)
    if not cookies:
        return {"error": "无法获取教务系统 session，请检查 jAccount 配置"}

    try:
        r = requests.post(
            "https://i.sjtu.edu.cn/kbcx/xskbcx_cxXsKb.html",
            data={"xnm": year, "xqm": xqm},
            cookies=cookies,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://i.sjtu.edu.cn/"},
            timeout=15,
        )
    except Exception as e:
        return {"error": f"教务系统请求异常: {e}"}

    if r.status_code != 200:
        return {"error": f"教务系统请求失败: {r.status_code}"}
    if "jAccount" in r.text:
        cfg.pop("jwxt_cookies", None)
        return {"error": "教务系统 session 已过期，请稍后重试（重新运行即可自动刷新）"}

    data = r.json()
    courses = []
    for item in data.get("kbList", []):
        jcs = item.get("jcs", "1-1")
        slot_s, slot_e = _parse_jcs(jcs)
        week_set = _parse_week_set(item.get("zcd", ""))
        courses.append({
            "name":       item.get("kcmc", ""),
            "code":       item.get("kch", ""),
            "teacher":    item.get("xm", ""),
            "location":   item.get("cdmc", ""),
            "campus":     item.get("xqmc", ""),
            "day":        int(item.get("xqj", 0)),     # 1=周一 … 7=周日
            "slot_start": slot_s,
            "slot_end":   slot_e,
            "time_start": _SLOT_TIMES[slot_s - 1][0] if 1 <= slot_s <= 14 else "",
            "time_end":   _SLOT_TIMES[slot_e - 1][1]  if 1 <= slot_e <= 14 else "",
            "weeks":      sorted(week_set),
            "week_str":   item.get("zcd", ""),
        })

    courses.sort(key=lambda c: (c["day"], c["slot_start"]))
    result = {"courses": courses, "year": year, "term": term, "total": len(courses),
              "cache_key": cache_key, "cached": False}

    # 写缓存
    try:
        _SCHEDULE_CACHE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        _logger.info(f"[jwxt] ✓ 课表已缓存（{len(courses)} 门）")
    except Exception as e:
        _logger.warning(f"[jwxt] 缓存写入失败: {e}")

    return result


def _effective_semester_start(cfg: dict, now=None) -> str:
    """生效的学期第一周周一：官方校历优先，config['semester_start'] 兜底。

    校历（academic_calendar.json）覆盖当天所在学期时直接采用其 start_date——
    教学周以校历为准，避免新学期沿用上学期的旧配置导致周次过滤失效；
    校历未覆盖（寒暑假/未收录学期）时回退 config 中手动设置的值。
    """
    from datetime import date as _date
    target = now or NOW.date()
    try:
        from sjtu_agent.calendar import AcademicCalendar
        from sjtu_agent.paths import DATA_DIR
        cal_start = AcademicCalendar(DATA_DIR).get_semester_start(target)
        if cal_start:
            return cal_start.isoformat()
    except Exception:
        pass  # 校历文件缺失/损坏时静默走旧配置，不阻断课表查询
    return str(cfg.get("semester_start", "") or "")


def _current_week_num(cfg: dict) -> int | None:
    """计算当前是第几教学周；周次锚点由 _effective_semester_start 决定。"""
    start_str = _effective_semester_start(cfg)
    if not start_str:
        return None
    try:
        from datetime import date
        sem_start = date.fromisoformat(start_str)
        delta = (NOW.date() - sem_start).days
        if delta < 0:
            return None
        return delta // 7 + 1
    except ValueError:
        return None


def get_schedule_for_date(cfg: dict, date_str: str = "", refresh: bool = False) -> dict:
    """
    获取某一天的课程安排。
    date_str: 'YYYY-MM-DD' / '今天' / '明天' / '后天' / '昨天'，留空=今天
    """
    from datetime import date, timedelta
    today = NOW.date()
    aliases = {
        "今天": 0, "today": 0,
        "明天": 1, "tomorrow": 1,
        "后天": 2,
        "昨天": -1, "yesterday": -1,
    }
    if not date_str or date_str in aliases:
        target = today + timedelta(days=aliases.get(date_str, 0))
    else:
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            return {"error": f"日期格式错误: {date_str}，请使用 YYYY-MM-DD"}

    # 计算第几周（校历优先，config 兜底）
    start_str = _effective_semester_start(cfg, now=target)
    week_num: int | None = None
    if start_str:
        try:
            sem_start = date.fromisoformat(start_str)
            delta = (target - sem_start).days
            week_num = delta // 7 + 1 if delta >= 0 else None
        except ValueError:
            pass

    result = fetch_schedule(cfg, refresh=refresh)
    if "error" in result:
        return result

    weekday = target.isoweekday()   # 1=周一 … 7=周日
    day_courses = [
        c for c in result["courses"]
        if c["day"] == weekday and (week_num is None or week_num in c["weeks"])
    ]

    week_info = f"第 {week_num} 周" if week_num else "（未配置 semester_start，不过滤周次）"
    return {
        "date":      target.isoformat(),
        "weekday":   _WEEKDAY_CN[weekday],
        "week_info": week_info,
        "courses":   day_courses,
        "total":     len(day_courses),
    }


def get_schedule_for_week(cfg: dict, week_offset: int = 0, refresh: bool = False) -> dict:
    """
    获取某一周的完整课表。
    week_offset: 0=本周，1=下周，-1=上周
    """
    week_num: int | None = _current_week_num(cfg)
    target_week = (week_num + week_offset) if week_num is not None else None

    result = fetch_schedule(cfg, refresh=refresh)
    if "error" in result:
        return result

    schedule: dict[int, list] = {}
    for c in result["courses"]:
        if target_week is None or target_week in c["weeks"]:
            schedule.setdefault(c["day"], []).append(c)

    days = [
        {"day": _WEEKDAY_CN[d], "day_num": d, "courses": schedule[d]}
        for d in range(1, 8) if d in schedule
    ]

    week_label = {0: "本周", 1: "下周", -1: "上周"}.get(week_offset, f"第{target_week}周")
    return {
        "week_label":    week_label,
        "week_num":      target_week,
        "days":          days,
        "total_courses": sum(len(d["courses"]) for d in days),
    }


def set_semester_start(cfg: dict, date_str: str) -> dict:
    """设置/更新学期起始日期（学期第一周周一），保存到 config。"""
    from datetime import date
    try:
        d = date.fromisoformat(date_str)
        if d.isoweekday() != 1:
            return {"error": f"{date_str} 不是周一，semester_start 应为学期第一周的周一"}
    except ValueError:
        return {"error": f"日期格式错误: {date_str}"}
    cfg["semester_start"] = date_str
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "semester_start": date_str, "message": f"学期起始日期已设为 {date_str}"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="多平台课程 DDL 聚合工具")
    p.add_argument("--skip", nargs="+", choices=["canvas", "aihaoke", "phycai", "icourse"],
                   default=[], help="跳过指定平台")
    p.add_argument("--canvas-only", action="store_true", help="仅抓取 Canvas")
    p.add_argument("--refresh-aihaoke-courses", action="store_true",
                   help="强制重新从 aihaoke 拉取选修课程列表（学期换课后使用）")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg  = load_config()

    skip = set(args.skip)
    if args.canvas_only:
        skip = {"aihaoke", "phycai", "icourse"}

    all_ddl: list[dict] = []

    if "canvas" not in skip:
        print("[*] 正在获取 Canvas 作业…")
        all_ddl.extend(fetch_canvas(cfg))

    if "aihaoke" not in skip:
        print("[*] 正在获取 aihaoke 任务…")
        all_ddl.extend(fetch_aihaoke(cfg, force_refresh_courses=args.refresh_aihaoke_courses))

    if "icourse" not in skip:
        print("[*] 正在获取 MOOC 测试…")
        all_ddl.extend(fetch_icourse(cfg))

    all_ddl.sort(key=lambda x: x["due"])

    lab: dict | None = None
    if "phycai" not in skip:
        print("[*] 正在获取物理实验安排…")
        lab = fetch_phycai(cfg)

    print_report(all_ddl, lab)


if __name__ == "__main__":
    main()
