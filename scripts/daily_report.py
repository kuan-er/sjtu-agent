#!/usr/bin/env python3
"""
daily_report.py — 每日学习汇报，通过 Telegram 推送

内容：
  1. 📚 DDL 提醒（7天内截止的作业，重点标注今日截止）
  2. 📅 今日课表
  3. 🔬 下次物理实验
  4. 📢 教务处/水源最新通知
  5. 💡 AI 综合学习建议

用法：
  python3 daily_report.py          # 生成并发送
  python3 daily_report.py --test   # 只打印，不发送

launchd 每天 22:00 自动运行。
"""

import json
import sys
import datetime as dt
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sjtu_agent.paths import CONFIG_PATH, DAILY_REPORT_LOG_PATH
from sjtu_agent.config import cfg as _cfg
from sjtu_agent.logging import get_logger

import agent
import ddl_checker as dc

# ── Telegram 推送 ─────────────────────────────────────────────────────────────

def _send_telegram(text: str) -> None:
    """向所有 allowed_ids 分块推送 Telegram 消息。"""
    _cfg.reload_if_changed()
    cfg = _cfg.raw()
    if not cfg.get("telegram_enabled", True):
        print("[daily_report] Telegram 推送已关闭，跳过")
        return
    token = cfg.get("telegram_token", "")
    allowed_ids = cfg.get("telegram_allowed_ids", [])
    if not token or not allowed_ids:
        print("[daily_report] Telegram 未配置，跳过推送")
        return

    import urllib.request
    # Telegram 单条消息最大 4096 字符
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for uid in allowed_ids:
        for chunk in chunks:
            payload = json.dumps({
                "chat_id": uid,
                "text": chunk,
                "parse_mode": "HTML",
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                urllib.request.urlopen(req, timeout=15)
            except Exception as e:
                print(f"[daily_report] Telegram 推送失败 uid={uid}: {e}")


def _html_to_post(text: str) -> list:
    """将日报 HTML（<b>/<i>/<br>）转为飞书 post 格式的段落列表。支持嵌套标签。"""
    import re
    paragraphs = []
    raw_paras = re.split(r"<br\s*/?>|\n", text)
    for para in raw_paras:
        para = para.strip()
        if not para:
            continue
        elements = []
        pos = 0
        # 栈追踪当前活跃的样式
        open_styles: list[str] = []
        for m in re.finditer(r"<(/?)([bi])>", para):
            tag_close, tag = m.groups()
            # 标签前的纯文本（使用当前栈的样式）
            prefix = para[pos:m.start()]
            if prefix:
                el = {"tag": "text", "text": prefix}
                if open_styles:
                    el["style"] = list(open_styles)
                elements.append(el)
            if not tag_close:
                open_styles.append("bold" if tag == "b" else "italic")
            else:
                style = "bold" if tag == "b" else "italic"
                if style in open_styles:
                    open_styles.remove(style)
                # 不在此处创建元素——合并到下一个 prefix 或 remaining
            pos = m.end()
        remaining = para[pos:]
        if remaining:
            el = {"tag": "text", "text": remaining}
            if open_styles:
                el["style"] = list(open_styles)
            elements.append(el)
        if elements:
            paragraphs.append(elements)
    return paragraphs


def _send_feishu(text: str) -> None:
    """通过飞书 API 向用户私聊推送日报（post 格式，支持 Markdown 渲染）。"""
    _cfg.reload_if_changed()
    cfg = _cfg.raw()
    if not cfg.get("feishu_enabled", True):
        print("[daily_report] 飞书推送已关闭，跳过")
        return
    open_id = cfg.get("feishu_open_id", "")
    if not open_id:
        print("[daily_report] 飞书未配置（feishu_open_id 缺失），跳过推送")
        return

    # 把 HTML 转为飞书 post 段落格式
    post_paras = _html_to_post(text)

    from sjtu_agent.feishu_client import send_post_message
    if send_post_message(open_id, post_paras):
        print("[daily_report] 飞书推送完成")
    else:
        print("[daily_report] 飞书推送失败")


# ── 数据收集 ──────────────────────────────────────────────────────────────────

def _get_news() -> str:
    """从 NewsAggregator 获取最近新闻摘要。失败时返回空字符串。"""
    try:
        from sjtu_agent.news_aggregator import NewsAggregator
        agg = NewsAggregator()
        md_digest, _ = agg.run(hours=24, top_k=4)
        return md_digest or ""
    except Exception as e:
        print(f"[daily_report] 新闻获取失败: {e}")
        return ""


def _collect_data(report_type: str = "evening") -> dict:
    """并行收集各平台数据，任何单项失败不影响其他项。"""
    import concurrent.futures as cf

    date_arg = "明天" if report_type == "evening" else "今天"
    results = {}
    tasks = {
        "ddls":     lambda: agent.tool_get_ddls(classify=True),
        "schedule": lambda: agent.tool_get_schedule(query_type="day", date=date_arg),
        "lab":      lambda: agent.tool_get_next_lab(),
        "jwc":      lambda: agent.tool_search_campus("通知 公告", sites=["jwc"], max_results=4),
        "news":     lambda: _get_news(),
    }

    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): key for key, fn in tasks.items()}
        for fut in cf.as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception as e:
                print(f"[daily_report] {key} 获取失败: {e}")
                results[key] = None

    return results


# ── AI 生成汇报 ───────────────────────────────────────────────────────────────

_WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]


def _build_care_note() -> str:
    """Build a care note from recent memories for personalisation."""
    _cfg.reload_if_changed()
    cfg = _cfg.raw()
    open_id = cfg.get("feishu_open_id", "")
    if not open_id:
        return ""
    try:
        from sjtu_agent.memory import get_care_suggestions
        suggestion = get_care_suggestions(open_id)
        if suggestion:
            return f"\n用户近期的关注和动态：{suggestion}\n在行动建议中适当提及这些内容，让用户感到被关注。"
    except Exception:
        pass
    return ""

def _load_report_preferences(report_type: str) -> dict:
    """加载日报偏好，合并通用设置和报别特定设置。"""
    _cfg.reload_if_changed()
    prefs = _cfg.get("report_preferences", {})

    # 默认值
    default_sections = {"ddl": True, "schedule": True, "lab": True, "jwc": True, "news": True, "tips": True}
    sections = {**default_sections, **prefs.get("sections", {})}
    custom = prefs.get("custom_instructions", "")

    # 报别特定覆盖
    per_type = prefs.get("per_type", {}).get(report_type, {})
    if per_type.get("sections"):
        sections.update(per_type["sections"])
    if per_type.get("custom_instructions"):
        custom = per_type["custom_instructions"]

    return {"sections": sections, "custom_instructions": custom}


def build_report(report_type: str = "evening") -> str:
    """收集数据 → 调用 AI 生成中文汇报 → 返回 HTML 格式字符串。"""
    now = dt.datetime.now(dc.CST)
    date_str = f"{now.strftime('%Y年%m月%d日')}（星期{_WEEKDAY_ZH[now.weekday()]}）"
    hour = now.hour
    if report_type == "morning":
        label = "晨间学习早报"
    elif report_type == "noon":
        label = "午间学习速报"
    else:
        label = "晚间学习日报"

    print("[daily_report] 正在收集数据…")
    data = _collect_data(report_type)

    # --- 拆解 DDL ---
    all_ddls = (data.get("ddls") or {}).get("ddls", [])
    today_ddls = [d for d in all_ddls if not d["expired"] and d["hours_left"] <= 24]
    week_ddls  = [d for d in all_ddls if not d["expired"] and 24 < d["hours_left"] <= 7 * 24]
    far_ddls   = [d for d in all_ddls if not d["expired"] and d["hours_left"] > 7 * 24]

    schedule_raw = data.get("schedule")
    lab_raw      = data.get("lab")
    jwc_raw      = data.get("jwc")
    news_raw     = data.get("news", "") or ""

    # 午报：过滤掉上午已结束的课程（第 1-4 节，11:40 前结束）
    noon_has_remaining = True
    if report_type == "noon" and schedule_raw and isinstance(schedule_raw, dict):
        courses = schedule_raw.get("courses", [])
        if courses:
            afternoon = [c for c in courses if c.get("slot_start", 0) >= 5]
            noon_has_remaining = len(afternoon) > 0
            schedule_raw = {**schedule_raw, "courses": afternoon}

    # 确定课表标签
    schedule_day_label = "明日" if report_type == "evening" else "今日"

    # --- 构建给 AI 的数据上下文 ---
    def _fmt_ddl(d):
        h = d["hours_left"]
        if h <= 24:
            urgency = f"⚠️ 今日截止（{h}小时后）"
        elif h <= 72:
            urgency = f"明后天截止（约{h//24}天后）"
        else:
            urgency = f"{h//24}天后截止"
        submitted = "✅ 已提交" if d.get("submitted") else ""
        return f"[{d['platform']}] {d['course']} · {d['name']}  截止:{d['due']}  {urgency} {submitted}".strip()

    ddl_section = "\n".join(_fmt_ddl(d) for d in all_ddls) if all_ddls else "（所有作业均已完成或无作业）"

    # 从 schedule 提取课程列表文字
    def _fmt_schedule(s):
        if not s:
            return "（获取失败）"
        if isinstance(s, dict) and s.get("error"):
            return f"（{s['error']}）"
        courses = s.get("courses") if isinstance(s, dict) else None
        if courses:
            lines = []
            for c in courses:
                t = c.get("time_str") or c.get("time") or ""
                if not t:
                    ts = c.get("time_start", "")
                    te = c.get("time_end", "")
                    if ts and te:
                        t = f"{ts}-{te}"
                room = c.get("room") or c.get("location") or ""
                lines.append(f"{t} {c.get('name','未知课程')} @ {room}".strip())
            return "\n".join(lines) if lines else f"（{schedule_day_label}无课）"
        if report_type == "noon" and isinstance(s, dict) and not noon_has_remaining:
            return "（上午课程已结束，下午及晚间无课）"
        # fallback: 直接 JSON
        return json.dumps(s, ensure_ascii=False)

    def _fmt_lab(l):
        if not l:
            return "（未获取到，或近期无安排）"
        d = l.get("datetime") or ""
        weekday = l.get("weekday") or ""
        return f"{l.get('name','')}  {d[:10]} {weekday} {l.get('time_str','')} @ {l.get('room','')}".strip()

    def _fmt_jwc(j):
        if not j:
            return "（获取失败）"
        items = j.get("results", []) if isinstance(j, dict) else []
        if not items:
            return "（暂无新通知）"
        lines = []
        for item in items[:3]:
            title = item.get("title") or item.get("snippet") or "无标题"
            url   = item.get("url") or item.get("link") or ""
            lines.append(f"• {title}")
            if url:
                lines.append(f"  {url}")
        return "\n".join(lines)

    schedule_section_label = "明日课表" if report_type == "evening" else "今日课表"

    # 读取日报偏好
    prefs = _load_report_preferences(report_type)
    enabled_sections = prefs["sections"]
    custom_instructions = prefs["custom_instructions"]

    _type_hints = {
        "morning": "今日课程安排+今日截止DDL+晨间行动建议（如：早上有什么课、今天要交什么）",
        "noon":   "下午及晚间课程安排+临近DDL提醒+午间行动建议（如：下午有什么课、明天截止的作业；上午课程已结束无需再提）",
        "evening": "今日总结+明日课程预告+晚间行动建议（如：今天完成了什么、明天有什么课、要准备什么）",
    }
    hint = _type_hints.get(report_type, _type_hints["evening"])
    schedule_prompt_header = "明日课程" if report_type == "evening" else "今日课程"

    # --- 动态构建 AI 提示词 ---
    section_templates = {
        "ddl": (f"""📚 <b>作业 DDL</b>：列出今日/本周截止任务（如无则写"暂无紧急 DDL ✅"）；每条注明距截止时间
今日截止（{len(today_ddls)} 项）：
{chr(10).join(_fmt_ddl(d) for d in today_ddls) or "（无）"}
本周内截止（{len(week_ddls)} 项）：
{chr(10).join(_fmt_ddl(d) for d in week_ddls) or "（无）"}"""),
        "schedule": (f"""📅 <b>{schedule_prompt_header}</b>：课程名+时间（如无课则写"无课"）
{_fmt_schedule(schedule_raw)}"""),
        "lab": (f"""🔬 <b>下次实验</b>：时间、地点（如无则写"暂无安排"）
{_fmt_lab(lab_raw)}"""),
        "jwc": (f"""📢 <b>教务通知</b>：最多2条关键通知摘要（如无则写"暂无新通知"）
{_fmt_jwc(jwc_raw)}"""),
        "news": (f"""📰 <b>校园动态</b>：从校园新闻中选取1-2条最相关或有趣的摘要（如无则写"暂无"）
{news_raw or "（暂无）"}"""),
        "tips": "💡 <b>行动建议</b>：根据当前 DDL 紧急程度和时段，用1-2句话给出具体建议",
    }

    # 构建 data_ctx（DDL 详情和校历始终包含，供 LLM 参考）
    data_ctx = f"""当前时间：{date_str} {now.strftime('%H:%M')}

【DDL 汇总（共 {len(all_ddls)} 项未完成）】
今日截止（{len(today_ddls)} 项）：
{chr(10).join(_fmt_ddl(d) for d in today_ddls) or "（无）"}

本周内截止（{len(week_ddls)} 项）：
{chr(10).join(_fmt_ddl(d) for d in week_ddls) or "（无）"}

更远期（{len(far_ddls)} 项）：
{chr(10).join(_fmt_ddl(d) for d in far_ddls) or "（无）"}

【{schedule_section_label}】
{_fmt_schedule(schedule_raw)}

【下次物理实验】
{_fmt_lab(lab_raw)}

【教务处最新通知】
{_fmt_jwc(jwc_raw)}

【校园新闻/水源热帖（近24h）】
{news_raw or "（暂无）"}"""

    # 校历
    try:
        from sjtu_agent.calendar import AcademicCalendar
        from sjtu_agent.paths import DATA_DIR
        cal_ctx = AcademicCalendar(DATA_DIR).get_context(now.date())
        if cal_ctx:
            data_ctx += f"\n\n【校历提醒】\n{cal_ctx}"
    except Exception:
        pass

    _THINK_RE = __import__("re").compile(r"<think>.*?</think>", __import__("re").DOTALL)

    # 构建启用的 section 列表
    section_order = ["ddl", "schedule", "lab", "jwc", "news", "tips"]
    enabled_list = [section_templates[k] for k in section_order if enabled_sections.get(k, True)]

    if not enabled_list:
        # 用户把所有模块都关了
        enabled_list = ["请告知用户当前日报所有模块均已隐藏，建议至少开启一个模块。"]

    # 构建提示词
    extra_line = f"\n额外要求：{custom_instructions}" if custom_instructions else ""
    prompt = f"""你是一个贴心的学习助手，请根据以下数据为上海交通大学学生生成一份{label}。

要求：
- 使用 Telegram HTML 格式（只用 <b> <i> 标签，不用 Markdown），不要用 * # 符号
- 语气友好简洁，像朋友发消息，不要太正式
- 全部用中文
- 时间段：现在是{report_type}（{hint}）
- 按以下结构输出（只输出启用的模块，顺序不变）：

第1行：📊 <b>{date_str} {label}</b>

然后依次输出以下启用的模块（每节空一行）：
{chr(10).join(enabled_list)}{extra_line}

以下是收集到的数据：
{data_ctx}
{_build_care_note()}"""

    print("[daily_report] 正在生成汇报…")
    try:
        agent_cfg = agent.load_agent_config()
        client = agent._make_client(agent_cfg)
        model = agent_cfg.get("model", "deepseek-chat")

        if agent._is_anthropic_model(model):
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text
        else:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
            # DeepSeek Reasoner: reasoning_content 单独字段，直接取 content
            choice = resp.choices[0].message
            text = choice.content or ""

        # 去除 DeepSeek 等模型嵌在 content 里的 <think>...</think> 推理块
        text = _THINK_RE.sub("", text).strip()
        # 防御：若仍以 <think> 开头说明截断未闭合，截取 </think> 后的部分
        if text.startswith("<think>"):
            idx = text.find("</think>")
            text = text[idx + len("</think>"):].strip() if idx != -1 else ""
        return text or "(报告生成失败，请重试)"

    except Exception as e:
        print(f"[daily_report] AI 生成失败，降级为纯文本模式: {e}")
        fallback_schedule_label = "明日课程" if report_type == "evening" else "今日课程"
        return _fallback_report(date_str, today_ddls, week_ddls, far_ddls,
                                _fmt_schedule(schedule_raw), _fmt_lab(lab_raw), _fmt_jwc(jwc_raw),
                                fallback_schedule_label)


def _fallback_report(date_str, today_ddls, week_ddls, far_ddls,
                     schedule_txt, lab_txt, jwc_txt,
                     schedule_label: str = "今日课程") -> str:
    """AI 不可用时的纯文本降级汇报。"""
    lines = [f"📊 <b>{date_str} 学习日报</b>\n"]

    lines.append("📚 <b>作业 DDL</b>")
    if today_ddls:
        for d in today_ddls:
            lines.append(f"⚠️ 今日截止 {d['hours_left']}h | [{d['platform']}] {d['course']} · {d['name']}")
    if week_ddls:
        for d in week_ddls:
            lines.append(f"• {d['hours_left']//24}天后 | [{d['platform']}] {d['course']} · {d['name']}")
    if not today_ddls and not week_ddls:
        lines.append("暂无7天内截止任务 ✅")

    lines.append(f"\n📅 <b>{schedule_label}</b>\n{schedule_txt}")
    lines.append(f"\n🔬 <b>下次实验</b>\n{lab_txt}")
    lines.append(f"\n📢 <b>教务通知</b>\n{jwc_txt}")
    return "\n".join(lines)


# ── 日志 ──────────────────────────────────────────────────────────────────────

_logger = get_logger("daily_report")


def _log(msg: str) -> None:
    _logger.info(msg)


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="打印汇报但不发送")
    parser.add_argument("--type", choices=["morning", "noon", "evening"],
                        default="evening", help="汇报类型（morning/noon/evening）")
    args = parser.parse_args()

    _log(f"=== {args.type} 汇报开始 ===")
    try:
        report = build_report(report_type=args.type)
        if args.test:
            print("\n" + "="*60)
            try:
                print(report)
            except UnicodeEncodeError:
                print(report.encode(sys.stdout.encoding or "utf-8", errors="replace")
                      .decode(sys.stdout.encoding or "utf-8", errors="replace"))
            print("="*60)
            _log("测试模式，未发送")
        else:
            _send_telegram(report)
            _send_feishu(report)
            _log("汇报已发送")
    except Exception as e:
        _log(f"[X] 发生错误: {e}")
        traceback.print_exc()
