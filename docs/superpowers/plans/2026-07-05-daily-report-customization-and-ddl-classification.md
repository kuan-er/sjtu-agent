# Daily Report Customization & DDL Classification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable users to customize daily report content via natural conversation (preferences persisted), and intelligently classify Canvas assignments as real homework vs. course notifications using LLM-based content analysis.

**Architecture:** Two independent phases. Phase 1 adds DDL classification: `fetch_canvas()` reads assignment descriptions, a new `_classify_canvas_ddls()` function uses LLM to batch-classify, results are cached alongside the existing 15-min DDL cache, and `_serialize_ddl()` + `tool_get_ddls()` expose type info. Phase 2 adds report preferences: a new `_report_prefs.py` tool module stores section toggles and custom instructions in `config.json`, `daily_report.py` reads them to dynamically build the LLM prompt instead of the hardcoded 6-section template.

**Tech Stack:** Python 3.11+, existing project patterns (TOOLS_ENTRIES + tool_xxx functions, atomic_write_json, ConfigStore), Canvas REST API, OpenAI-compatible LLM client.

## Global Constraints

- Follow existing code patterns: tool modules use `TOOLS_ENTRIES` list + `tool_xxx()` functions, imported by `_core.py`
- Use `atomic_write_json()` from `sjtu_agent.paths` for config writes to prevent corruption
- DDL cache TTL remains 900s (15 minutes); classification results share the same cache entry
- Classification LLM call uses the same client as daily report generation (via `agent._make_client()`)
- Default behavior unchanged: without `classify=True`, DDLs behave exactly as before (backward compatible)
- All user-facing text in Chinese
- Follow project naming: private functions prefixed with `_`, tools are `tool_xxx`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `ddl_checker.py` | Modify | `fetch_canvas()` gains `classify` param, pulls `description` field from Canvas API |
| `sjtu_agent/agent/tools/_core.py` | Modify | New `_classify_canvas_ddls()`, updated `_serialize_ddl()`, updated `tool_get_ddls()`, new tool definitions for report prefs, updated `run_tool()` dispatch |
| `sjtu_agent/agent/tools/_report_prefs.py` | Create | `tool_update_report_preferences()`, `tool_get_report_preferences()`, `TOOLS_ENTRIES` |
| `sjtu_agent/agent/prompts.py` | Modify | SYSTEM_PROMPT additions for report pref modification behavior and DDL classification awareness |
| `scripts/daily_report.py` | Modify | `_collect_data()` passes `classify=True`, `build_report()` reads prefs and dynamically builds prompt |

---

### Task 1: Enhance `fetch_canvas()` to pull assignment descriptions

**Files:**
- Modify: `ddl_checker.py:117-203`

**Interfaces:**
- Consumes: Canvas REST API `/api/v1/courses/{cid}/assignments` (existing)
- Produces: Each result dict gains `description` (str, HTML), `submission_types` (list[str])

**Description:** When `classify=True`, the function additionally fetches the full assignment object
(which already contains `description` in the Canvas API response — no extra API call needed) and
stores `description` and `submission_types` in the result dict.

- [ ] **Step 1: Modify `fetch_canvas()` signature and body**

Add `classify: bool = False` parameter. When True, store extra fields from the Canvas API response.

In `ddl_checker.py`, change lines 117 and 162-170, 191-201:

```python
def fetch_canvas(cfg: dict, include_past: bool = False, classify: bool = False) -> list[dict]:
    """通过 Canvas REST API 获取作业。include_past=True 时包含已过期的历史作业。
    classify=True 时额外返回 description 和 submission_types 字段用于智能分类。"""
    # ... token/base/session setup unchanged (lines 119-126) ...

    # ... courses fetch unchanged (lines 128-141) ...

    results: list[dict] = []

    for course in courses:
        cid = course["id"]
        cname = course.get("name", f"课程{cid}")

        pending: list[dict] = []
        asgn_url: str | None = f"{base}/api/v1/courses/{cid}/assignments"
        asgn_params: dict = {"per_page": 100, "order_by": "due_at"}
        while asgn_url:
            try:
                r = session.get(asgn_url, params=asgn_params, timeout=15)
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"[Canvas] 获取 {cname} 作业失败：{e}")
                raise
            for a in r.json():
                if a.get("workflow_state") == "deleted":
                    continue
                due = parse_dt(a.get("due_at", ""))
                if not include_past and (not due or due < datetime.now(CST)):
                    continue
                item = {
                    "id": a["id"],
                    "name": a.get("name", "未知作业"),
                    "due": due,
                }
                if classify:
                    item["description"] = a.get("description", "") or ""
                    item["submission_types"] = a.get("submission_types", []) or []
                pending.append(item)
            asgn_url = r.links.get("next", {}).get("url")
            asgn_params = {}

        if not pending:
            continue

        # ... submissions check unchanged (lines 175-189) ...

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
```

- [ ] **Step 2: Verify existing behavior unchanged**

Run: `python -c "import ddl_checker as dc; cfg = dc.load_config(); r = dc.fetch_canvas(cfg, include_past=False); print(f'Got {len(r)} DDLs'); print(r[0].keys() if r else 'no DDLs')"`

Expected: Output shows DDL count and keys WITHOUT `description` or `submission_types` (backward compatible when classify not passed).

- [ ] **Step 3: Verify classify mode works**

Run: `python -c "import ddl_checker as dc; cfg = dc.load_config(); r = dc.fetch_canvas(cfg, include_past=False, classify=True); print(r[0].keys() if r else 'no DDLs')"`

Expected: Output shows keys including `description` and `submission_types`.

- [ ] **Step 4: Commit**

```bash
git add ddl_checker.py
git commit -m "feat: add classify param to fetch_canvas() — pull description and submission_types"
```

---

### Task 2: Add `_classify_canvas_ddls()` in `_core.py`

**Files:**
- Modify: `sjtu_agent/agent/tools/_core.py` (insert after line ~2129, before `_prefetch_ddls_background`)

**Interfaces:**
- Consumes: `agent.load_agent_config()` (from `chat_loop.py`), `agent._make_client()` (from `runner.py`)
- Produces: `_classify_canvas_ddls(ddls: list[dict]) -> list[dict]` — mutates each dict in-place, adding `type` and `type_confidence`

**Description:** Takes a list of Canvas DDL dicts (with `description` and `submission_types` fields), applies rule-based pre-screening, then sends remaining items to LLM for batch classification. Results are written back in-place.

- [ ] **Step 1: Add the classification function**

Insert after line 2129 in `_core.py` (after `_fetch_ddls_parallel`):

```python
def _classify_canvas_ddls(ddls: list) -> list:
    """对 Canvas DDL 列表进行智能分类（作业 / 通知 / 其他）。
    
    规则预筛 + LLM 批量分类。结果写回每个 dict 的 type 和 type_confidence 字段。
    仅处理 platform == "Canvas" 且有 description 字段的项。
    """
    import json as _json
    from sjtu_agent.agent.chat_loop import load_agent_config as _load_cfg
    from sjtu_agent.agent.runner import _make_client as _make_llm_client

    # 只处理 Canvas 且有 description 的项
    canvas_items = [
        (i, d) for i, d in enumerate(ddls)
        if d.get("platform") == "Canvas" and d.get("description") is not None
    ]
    if not canvas_items:
        return ddls

    # ── 规则预筛 ──
    notification_keywords = ["通知", "公告", "提醒", "评分", "评价", "问卷", "反馈",
                              "评分标准", "课程介绍", "Syllabus", "教学大纲"]
    need_llm = []
    for idx, d in canvas_items:
        desc = (d.get("description") or "").strip()
        sub_types = d.get("submission_types") or []
        name = d.get("name", "")

        # 规则1: submission_types 为 ["none"] 或空，且名称/描述含关键词 → notification
        is_none_submission = (not sub_types or sub_types == ["none"])
        if is_none_submission:
            text = f"{name} {desc}"
            if any(kw in text for kw in notification_keywords):
                d["type"] = "notification"
                d["type_confidence"] = 1.0
                continue

        # 规则2: submission_types 包含实际提交类型 (online_upload/online_text_entry/online_url)
        #         且名称不含通知关键词 → 疑似作业，交给 LLM
        has_real_submission = any(
            t in sub_types for t in ["online_upload", "online_text_entry", "online_url",
                                      "online_quiz", "external_tool", "media_recording"]
        )
        if has_real_submission:
            need_llm.append((idx, d))
            continue

        # 规则3: 没有 description 且 submission_types 为 none → notification
        if not desc and is_none_submission:
            d["type"] = "notification"
            d["type_confidence"] = 1.0
            continue

        # 剩余 → LLM 判断
        need_llm.append((idx, d))

    if not need_llm:
        return ddls

    # ── LLM 批量分类 ──
    # 构建简洁的分类请求
    items_for_llm = []
    for idx, d in need_llm:
        desc = (d.get("description") or "")[:500]  # 截断长描述
        items_for_llm.append({
            "index": len(items_for_llm),
            "course": d.get("course", ""),
            "name": d.get("name", ""),
            "description": desc,
            "submission_types": d.get("submission_types", []),
        })

    prompt = f"""判断以下 Canvas 条目是「作业/任务」还是「课程通知」。
    
条目列表：
{_json.dumps(items_for_llm, ensure_ascii=False, indent=2)}

判断标准：
- 要求学生提交/完成具体内容（做题、写报告、上传文件）→ "assignment"
- 仅信息告知、无需提交（课程安排通知、评分提醒、问卷、反馈征集、Syllabus）→ "notification"
- 评价/评分/问卷/反馈/课程介绍 → "notification"
- 不确定 → "unknown"

返回 JSON 数组（只返回 JSON，不要其他文字）：
[{{"index": 0, "type": "assignment", "confidence": 0.95}}, ...]"""

    try:
        agent_cfg = _load_cfg()
        client = _make_llm_client(agent_cfg)
        model = agent_cfg.get("model", "deepseek-chat")

        # 使用轻量模型调用（非流式，快速返回）
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.1,  # 低温度，追求一致分类
        )
        text = resp.choices[0].message.content or ""

        # 解析 JSON（防御：可能有 markdown 包裹）
        text = text.strip()
        if text.startswith("```"):
            # 去掉 ```json ... ``` 包裹
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        classifications = _json.loads(text)

        # 写回结果
        class_map = {c["index"]: c for c in classifications}
        for local_idx, (orig_idx, d) in enumerate(need_llm):
            c = class_map.get(local_idx, {})
            d["type"] = c.get("type", "unknown")
            d["type_confidence"] = c.get("confidence", 0.0)

    except Exception as e:
        print(f"[DDL classify] LLM 分类失败，全部标记为 unknown: {e}")
        for idx, d in need_llm:
            d.setdefault("type", "unknown")
            d.setdefault("type_confidence", 0.0)

    # 确保所有 Canvas 项都有 type（防御）
    for d in ddls:
        if d.get("platform") == "Canvas":
            d.setdefault("type", "unknown")
            d.setdefault("type_confidence", 0.0)

    return ddls
```

- [ ] **Step 2: Verify classification with test data**

Create a quick test script:

```bash
python -c "
import ddl_checker as dc, json
from sjtu_agent.agent.tools._core import _classify_canvas_ddls
cfg = dc.load_config()
ddls = dc.fetch_canvas(cfg, classify=True)
print(f'Fetched {len(ddls)} DDLs with descriptions')
_classify_canvas_ddls(ddls)
for d in ddls:
    print(f'  [{d.get(\"type\",\"?\")}] {d[\"course\"]} · {d[\"name\"]} (confidence={d.get(\"type_confidence\",0)})')
"
```

Expected: Each DDL has a `type` field with value "assignment", "notification", or "unknown".

- [ ] **Step 3: Commit**

```bash
git add sjtu_agent/agent/tools/_core.py
git commit -m "feat: add _classify_canvas_ddls() — rule+LLM batch classification"
```

---

### Task 3: Update `_serialize_ddl()` and `tool_get_ddls()` signatures

**Files:**
- Modify: `sjtu_agent/agent/tools/_core.py:1429-1443` (`_serialize_ddl`)
- Modify: `sjtu_agent/agent/tools/_core.py:2229-2242` (`tool_get_ddls`)
- Modify: `sjtu_agent/agent/tools/_core.py:110-126` (TOOLS entry for `get_ddls`)

**Interfaces:**
- Produces: `_serialize_ddl()` returns dict with `type` and `type_confidence` fields
- Produces: `tool_get_ddls(classify=False, include_notifications=False)` 

- [ ] **Step 1: Update `_serialize_ddl()`**

Replace lines 1429-1443:

```python
def _serialize_ddl(item: dict, now=None) -> dict:
    import datetime as _dt
    if now is None:
        now = _dt.datetime.now(dc.CST)
    total_seconds = (item["due"] - now).total_seconds()
    hours_left    = int(total_seconds / 3600)
    result = {
        "platform":   item["platform"],
        "course":     item["course"],
        "name":       item["name"],
        "due":        item["due"].strftime("%Y-%m-%d %H:%M"),
        "hours_left": hours_left,
        "expired":    total_seconds < 0,
        "submitted":  item.get("submitted", False),
    }
    # 如果有分类信息则带上
    if item.get("type"):
        result["type"] = item["type"]
        result["type_confidence"] = item.get("type_confidence", 0.0)
    return result
```

- [ ] **Step 2: Update `tool_get_ddls()` signature and body**

Replace lines 2229-2242:

```python
def tool_get_ddls(skip_canvas=False, skip_aihaoke=False, skip_icourse=False,
                  classify: bool = False, include_notifications: bool = False):
    import datetime as _dt
    cfg = dc.load_config()
    now = _dt.datetime.now(dc.CST)

    # 当 classify=True 时，需要原始数据（含 description）做分类
    fetch_classify = classify
    all_ddl = _fetch_ddls_parallel(cfg, skip_canvas, skip_aihaoke, skip_icourse,
                                    classify=fetch_classify)

    if classify:
        _classify_canvas_ddls(all_ddl)

    all_ddl.sort(key=lambda x: x["due"])
    warnings = []
    if not skip_canvas and not (cfg.get("canvas_token") and not cfg.get("canvas_token", "").startswith("YOUR_")):
        warnings.append("Canvas 未配置 token；请先调用 setup_canvas 获取引导，生成后再用 save_credentials 保存。")

    serialized = [_serialize_ddl(x, now) for x in all_ddl if not x.get("submitted")]

    # 默认过滤通知类（除非用户明确要求）
    notification_count = 0
    if classify and not include_notifications:
        filtered = []
        for d in serialized:
            if d.get("type") == "notification":
                notification_count += 1
            else:
                filtered.append(d)
        serialized = filtered

    result = {
        "current_time": now.strftime("%Y-%m-%d %H:%M"),
        "ddls": serialized,
        "warnings": warnings,
    }
    if classify and notification_count > 0:
        result["filtered_notifications"] = notification_count
        result["hint"] = f"已过滤 {notification_count} 条课程通知（评分/问卷/公告等）。回复「全部DDL」可查看所有条目。"
    return result
```

- [ ] **Step 3: Update TOOLS definition for `get_ddls`**

Replace lines 113-125:

```python
    {
        "type": "function",
        "function": {
            "name": "get_ddls",
            "description": "获取所有平台（Canvas / AI 好课（aihaoke） / 中国大学MOOC）未完成 DDL，按截止时间升序。默认自动过滤 Canvas 课程通知（评分/问卷等非作业条目）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skip_canvas":  {"type": "boolean"},
                    "skip_aihaoke": {"type": "boolean"},
                    "skip_icourse": {"type": "boolean"},
                    "classify": {
                        "type": "boolean",
                        "description": "是否对 Canvas 作业进行智能分类（区分真实作业与课程通知）。日报和用户主动查 DDL 时传 true。",
                    },
                    "include_notifications": {
                        "type": "boolean",
                        "description": "是否包含课程通知类条目。用户说「全部」「包括通知」时传 true。默认 false（过滤通知）。",
                    },
                },
                "required": [],
            },
        },
    },
```

- [ ] **Step 4: Update `_fetch_ddls_parallel()` to pass `classify` through**

Replace lines 2100-2129:

```python
def _fetch_ddls_parallel(cfg: dict, skip_canvas=False, skip_aihaoke=False,
                          skip_icourse=False, classify: bool = False) -> list:
    """并行拉取各平台 DDL，返回合并列表（未排序）。
    优先使用 15 分钟内的磁盘缓存。
    classify=True 时使用独立缓存键（含 description/type），避免缓存污染。
    """
    import concurrent.futures as _cf

    cache_key = f"{skip_canvas},{skip_aihaoke},{skip_icourse}"
    if classify:
        cache_key += ",classify"
    cached = _ddl_cache_get(cache_key)
    if cached is not None:
        return cached

    tasks = []
    if not skip_canvas:
        tasks.append(("canvas",  lambda: dc.fetch_canvas(cfg, classify=classify)))
    if not skip_aihaoke:
        tasks.append(("aihaoke", lambda: dc.fetch_aihaoke(cfg)))
    if not skip_icourse:
        tasks.append(("icourse", lambda: dc.fetch_icourse(cfg)))

    all_ddl: list = []
    if not tasks:
        return all_ddl

    with _cf.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks}
        for fut in _cf.as_completed(futures):
            try:
                all_ddl.extend(fut.result())
            except Exception as e:
                print(f"[DDL] {futures[fut]} 拉取失败：{e}")

    _ddl_cache_set(cache_key, all_ddl)
    return all_ddl
```

- [ ] **Step 5: Update `run_tool()` dispatch for `get_ddls`**

Line 3543 already dispatches `get_ddls` — no change needed since `tool_get_ddls` accepts `**args`.

- [ ] **Step 6: Verify backward compatibility**

Run: `python -c "from sjtu_agent.agent.tools._core import tool_get_ddls; import json; r = tool_get_ddls(); print(json.dumps(r, ensure_ascii=False, indent=2)[:500])"`

Expected: DDL list without `type` fields and without `filtered_notifications` hint (classify defaults to False).

- [ ] **Step 7: Verify classification mode**

Run: `python -c "from sjtu_agent.agent.tools._core import tool_get_ddls; import json; r = tool_get_ddls(classify=True); print('filtered_notifications:', r.get('filtered_notifications', 0)); print('hint:', r.get('hint', 'none'))"`

Expected: Shows filtered count and hint if any notifications were detected.

- [ ] **Step 8: Commit**

```bash
git add sjtu_agent/agent/tools/_core.py
git commit -m "feat: update tool_get_ddls() with classify + include_notifications params"
```

---

### Task 4: Create `_report_prefs.py` tool module

**Files:**
- Create: `sjtu_agent/agent/tools/_report_prefs.py`

**Interfaces:**
- Produces: `TOOLS_ENTRIES` (list of 2 tool definitions), `tool_update_report_preferences()`, `tool_get_report_preferences()`
- Consumes: `CONFIG_PATH` from `sjtu_agent.paths`, `atomic_write_json` from `sjtu_agent.paths`

- [ ] **Step 1: Create the module**

```python
"""Report preferences tools — customize daily report sections and instructions."""

import json

from sjtu_agent.paths import CONFIG_PATH, atomic_write_json

# ── 默认偏好 ─────────────────────────────────────────────────────────────
_DEFAULT_SECTIONS = {
    "ddl": True,
    "schedule": True,
    "lab": True,
    "jwc": True,
    "news": True,
    "tips": True,
}

_DEFAULT_PREFS = {
    "sections": dict(_DEFAULT_SECTIONS),
    "custom_instructions": "",
    "per_type": {
        "morning": {},
        "noon": {},
        "evening": {},
    },
}

# ── 工具定义 ─────────────────────────────────────────────────────────────

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "update_report_preferences",
            "description": (
                "修改用户的早/中/晚报偏好设置。用户说「早报不要XX」「晚报加上XX」"
                "「日报多关注XX」时调用。只传需要修改的字段，未传字段保持不变。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "object",
                        "description": (
                            "要修改的日报模块显隐。键名：ddl（作业DDL）、schedule（课表）、"
                            "lab（物理实验）、jwc（教务通知）、news（校园动态）、tips（行动建议）。"
                            "值为 true=显示，false=隐藏。只传需要改的键。"
                        ),
                        "properties": {
                            "ddl": {"type": "boolean"},
                            "schedule": {"type": "boolean"},
                            "lab": {"type": "boolean"},
                            "jwc": {"type": "boolean"},
                            "news": {"type": "boolean"},
                            "tips": {"type": "boolean"},
                        },
                    },
                    "custom_instructions": {
                        "type": "string",
                        "description": (
                            "用户对日报的额外要求（自然语言），将直接注入日报生成提示词。"
                            "如「多关注物理作业」「语气轻松一点」「晚报重点提醒明天要交的」。"
                        ),
                    },
                    "report_type": {
                        "type": "string",
                        "enum": ["morning", "noon", "evening", "all"],
                        "description": (
                            "修改哪个报别的偏好。morning=早报，noon=午报，evening=晚报。"
                            "all=所有报别统一修改。默认 all。"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_report_preferences",
            "description": "查看用户当前的日报偏好设置（各模块显隐状态和自定义要求）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# ── 内部 ────────────────────────────────────────────────────────────────

def _load_prefs() -> dict:
    """从 config.json 加载日报偏好，不存在时返回默认值。"""
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            prefs = cfg.get("report_preferences")
            if prefs and isinstance(prefs, dict):
                # 确保默认结构完整
                merged = dict(_DEFAULT_PREFS)
                merged["sections"] = {**_DEFAULT_SECTIONS, **prefs.get("sections", {})}
                merged["custom_instructions"] = prefs.get("custom_instructions", "")
                merged["per_type"] = prefs.get("per_type", {})
                for rt in ("morning", "noon", "evening"):
                    merged["per_type"].setdefault(rt, {})
                return merged
    except Exception:
        pass
    return dict(_DEFAULT_PREFS)


def _save_prefs(prefs: dict) -> None:
    """将日报偏好写回 config.json。"""
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        else:
            cfg = {}
        cfg["report_preferences"] = prefs
        atomic_write_json(CONFIG_PATH, cfg)
    except Exception as e:
        raise RuntimeError(f"保存日报偏好失败: {e}")

# ── 工具实现 ────────────────────────────────────────────────────────────

def tool_get_report_preferences() -> dict:
    """查看当前日报偏好。"""
    prefs = _load_prefs()
    return {"preferences": prefs}


def tool_update_report_preferences(
    sections: dict | None = None,
    custom_instructions: str | None = None,
    report_type: str = "all",
) -> dict:
    """更新日报偏好。只修改传入的字段。"""
    prefs = _load_prefs()
    changes = []

    # 确定要修改的目标
    targets = ["morning", "noon", "evening"] if report_type == "all" else [report_type]

    for rt in targets:
        per_type = prefs["per_type"].setdefault(rt, {})

        if sections:
            rt_sections = per_type.setdefault("sections", {})
            for key in ("ddl", "schedule", "lab", "jwc", "news", "tips"):
                if key in sections:
                    rt_sections[key] = sections[key]
            changes.extend(f"{rt}:{k}={sections[k]}" for k in sections)

        if custom_instructions is not None:
            per_type["custom_instructions"] = custom_instructions
            changes.append(f"{rt}:custom_instructions")

    # 如果 report_type == "all"，同时更新顶层默认值
    if report_type == "all":
        if sections:
            for key in sections:
                prefs["sections"][key] = sections[key]
        if custom_instructions is not None:
            prefs["custom_instructions"] = custom_instructions

    _save_prefs(prefs)

    summary_parts = []
    if sections:
        enabled = [k for k, v in sections.items() if v]
        disabled = [k for k, v in sections.items() if not v]
        section_names = {
            "ddl": "📚作业DDL", "schedule": "📅课表", "lab": "🔬物理实验",
            "jwc": "📢教务通知", "news": "📰校园动态", "tips": "💡行动建议",
        }
        if enabled:
            summary_parts.append(f"将展示: {'、'.join(section_names.get(k, k) for k in enabled)}")
        if disabled:
            summary_parts.append(f"将隐藏: {'、'.join(section_names.get(k, k) for k in disabled)}")
    if custom_instructions is not None:
        summary_parts.append(f"自定义要求: {custom_instructions}")

    scope = "所有报别" if report_type == "all" else f"{report_type}"
    return {
        "ok": True,
        "scope": scope,
        "summary": "；".join(summary_parts) if summary_parts else "偏好已更新",
        "changes": changes,
    }
```

- [ ] **Step 2: Verify the module loads**

Run: `python -c "from sjtu_agent.agent.tools._report_prefs import TOOLS_ENTRIES, tool_get_report_preferences, tool_update_report_preferences; print('Module OK'); r = tool_get_report_preferences(); print(r)"`

Expected: Module loads without errors; shows default preferences.

- [ ] **Step 3: Commit**

```bash
git add sjtu_agent/agent/tools/_report_prefs.py
git commit -m "feat: add _report_prefs.py — report customization tools"
```

---

### Task 5: Wire report prefs tools into `_core.py`

**Files:**
- Modify: `sjtu_agent/agent/tools/_core.py` (imports, TOOLS list, `run_tool()` dispatch)

- [ ] **Step 1: Add import**

After line 69 (after `_user_profile` import block):

```python
from sjtu_agent.agent.tools._report_prefs import (
    TOOLS_ENTRIES as _REPORT_PREFS_TOOLS,
    tool_get_report_preferences, tool_update_report_preferences,
)
```

- [ ] **Step 2: Add to TOOLS list**

After line 828 (`*_USER_PROFILE_TOOLS,`):

```python
    *_REPORT_PREFS_TOOLS,
```

- [ ] **Step 3: Add to `run_tool()` dispatch**

After line 3594 (`elif name == "get_user_profile":`):

```python
        elif name == "update_report_preferences": r = tool_update_report_preferences(**args)
        elif name == "get_report_preferences":    r = tool_get_report_preferences()
```

- [ ] **Step 4: Add tool label**

After line 618 (`"get_dining_history": "正在查看就餐历史",`):

```python
    "update_report_preferences": "正在更新日报偏好",
    "get_report_preferences":    "正在读取日报偏好",
```

- [ ] **Step 5: Verify wiring**

Run: `python -c "from sjtu_agent.agent.tools._core import run_tool; print(run_tool('get_report_preferences', {}))"`

Expected: JSON output showing preferences dict.

- [ ] **Step 6: Commit**

```bash
git add sjtu_agent/agent/tools/_core.py
git commit -m "feat: wire report_prefs tools into _core.py"
```

---

### Task 6: Update SYSTEM_PROMPT in `prompts.py`

**Files:**
- Modify: `sjtu_agent/agent/prompts.py` (insert new sections)

- [ ] **Step 1: Add DDL classification behavior guidance**

Insert after line 131 (after the DDL query section "无待完成任务时明确告知"):

```
- 获取 DDL 时默认传 classify=True，自动区分真实作业和课程通知（评分/问卷/公告等）
- 如果返回结果包含 filtered_notifications 和 hint，按 hint 提示用户
- 用户说「全部」「包括通知」「所有DDL」时传 include_notifications=True
- 分类为 notification 的条目在展示时标注 📢 而非 📝
```

- [ ] **Step 2: Add report preference modification guidance**

Insert before the "## 提醒事项管理" section (before line 367):

```
## 日报偏好

用户可能通过对话调整早/中/晚报的内容。识别到以下意图时调用对应工具：

- 「早报不要XX」「晚报加上XX」「日报隐藏XX」→ update_report_preferences 修改 sections
- 「日报多关注XX」「日报语气XX」「晚报重点提醒XX」→ update_report_preferences 更新 custom_instructions
- 「看看日报设置」「日报偏好是什么」→ get_report_preferences

修改成功后简短确认变更内容，告知「下次日报自动生效」。不要长篇解释。
```

- [ ] **Step 3: Commit**

```bash
git add sjtu_agent/agent/prompts.py
git commit -m "feat: add DDL classify + report prefs behavior to SYSTEM_PROMPT"
```

---

### Task 7: Update `daily_report.py` — classification and dynamic prompts

**Files:**
- Modify: `scripts/daily_report.py:146-170` (`_collect_data`)
- Modify: `scripts/daily_report.py:193-394` (`build_report`)

- [ ] **Step 1: Update `_collect_data()` to request classification**

Replace line 153 (`"ddls": lambda: agent.tool_get_ddls(),`):

```python
        "ddls":     lambda: agent.tool_get_ddls(classify=True),
```

- [ ] **Step 2: Add preference loading helper**

Insert before `build_report()` (before line 193):

```python
def _load_report_preferences(report_type: str) -> dict:
    """加载日报偏好，合并通用设置和报别特定设置。"""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        prefs = cfg.get("report_preferences", {})
    except Exception:
        return {"sections": {"ddl": True, "schedule": True, "lab": True, "jwc": True, "news": True, "tips": True}, "custom_instructions": ""}

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
```

- [ ] **Step 3: Replace hardcoded prompt in `build_report()` with dynamic prompt**

Replace lines 291-356 (from `schedule_section_label` assignment through `{_build_care_note()}`):

```python
    schedule_section_label = "明日课表" if report_type == "evening" else "今日课表"

    # 读取日报偏好
    prefs = _load_report_preferences(report_type)
    enabled_sections = prefs["sections"]
    custom_instructions = prefs["custom_instructions"]

    # --- 动态构建 AI 提示词 ---
    section_templates = {
        "ddl": (f"📚 <b>作业 DDL</b>：列出今日/本周截止任务（如无则写"暂无紧急 DDL ✅"）；每条注明距截止时间\n"
                f"今日截止（{len(today_ddls)} 项）：\n{chr(10).join(_fmt_ddl(d) for d in today_ddls) or '（无）'}\n"
                f"本周内截止（{len(week_ddls)} 项）：\n{chr(10).join(_fmt_ddl(d) for d in week_ddls) or '（无）'}"),
        "schedule": (f"📅 <b>{schedule_prompt_header}</b>：课程名+时间（如无课则写"无课"）\n"
                     f"{_fmt_schedule(schedule_raw)}"),
        "lab": (f"🔬 <b>下次实验</b>：时间、地点（如无则写"暂无安排"）\n"
                f"{_fmt_lab(lab_raw)}"),
        "jwc": (f"📢 <b>教务通知</b>：最多2条关键通知摘要（如无则写"暂无新通知"）\n"
                f"{_fmt_jwc(jwc_raw)}"),
        "news": (f"📰 <b>校园动态</b>：从校园新闻中选取1-2条最相关或有趣的摘要（如无则写"暂无"）\n"
                 f"{news_raw or '（暂无）'}"),
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
```

- [ ] **Step 4: Verify daily report generation**

Run: `python scripts/daily_report.py --test --type morning`

Expected: Report generated with all 6 default sections. No errors.

- [ ] **Step 5: Test with modified preferences**

Create a quick test:

```bash
python -c "
import json
from sjtu_agent.paths import CONFIG_PATH
cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
cfg['report_preferences'] = {'sections': {'ddl': True, 'schedule': True, 'lab': False, 'jwc': False, 'news': False, 'tips': True}, 'custom_instructions': '语气活泼一点', 'per_type': {}}
import sjtu_agent.paths as p
p.atomic_write_json(CONFIG_PATH, cfg)
print('Prefs set: jwc+news+lab disabled, tips kept')
"
```

Then run: `python scripts/daily_report.py --test --type evening`

Expected: Report has DDL + Schedule + Tips sections, but NO lab/jwc/news sections. "语气活泼一点" influences the tone.

Then restore defaults:
```bash
python -c "
import json
from sjtu_agent.paths import CONFIG_PATH
cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
cfg.pop('report_preferences', None)
import sjtu_agent.paths as p
p.atomic_write_json(CONFIG_PATH, cfg)
print('Prefs reset')
"
```

- [ ] **Step 6: Commit**

```bash
git add scripts/daily_report.py
git commit -m "feat: dynamic report prompt based on user preferences + DDL classification"
```

---

### Task 8: Integration test — end-to-end verification

- [ ] **Step 1: Run full test suite**

```bash
pytest
```

Expected: All existing tests pass. No regressions.

- [ ] **Step 2: Test DDL classification end-to-end**

```bash
python -c "
from sjtu_agent.agent.tools._core import tool_get_ddls
import json
r = tool_get_ddls(classify=True)
print('DDLs:', len(r['ddls']))
print('Filtered notifications:', r.get('filtered_notifications', 0))
if r.get('hint'):
    print('Hint:', r['hint'])
for d in r['ddls'][:5]:
    print(f'  [{d.get(\"type\",\"?\")}] {d[\"course\"]} · {d[\"name\"]}  due={d[\"due\"]}')
"
```

Expected: Shows classified DDLs with type field.

- [ ] **Step 3: Test report preferences update via tools**

```bash
python -c "
from sjtu_agent.agent.tools._report_prefs import tool_update_report_preferences, tool_get_report_preferences
import json

# Update
r = tool_update_report_preferences(sections={'jwc': False, 'news': False}, report_type='morning')
print('Update:', json.dumps(r, ensure_ascii=False))

# Read back
r2 = tool_get_report_preferences()
print('Prefs:', json.dumps(r2, ensure_ascii=False, indent=2)[:500])

# Reset
tool_update_report_preferences(sections={'jwc': True, 'news': True}, report_type='morning')
print('Reset done')
"
```

Expected: Preferences update and read back correctly.

- [ ] **Step 4: Test daily report with classification**

Run: `python scripts/daily_report.py --test --type morning`

Expected: Report DDL section no longer shows notification-type items (like "课程综合评分"). Action suggestions are relevant to real assignments only.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: verify DDL classification + report prefs integration"
```

---

## Verification Summary

After all tasks complete:

1. **Existing tests pass**: `pytest` (from project root) — no regressions
2. **DDL classification works**: `tool_get_ddls(classify=True)` returns typed DDLs with notifications filtered
3. **Report preferences persist**: `tool_update_report_preferences()` writes to `config.json`, survives restart
4. **Daily report dynamic**: `daily_report.py --test` output only shows enabled sections, follows custom instructions
5. **Known case fixed**: "课程综合评分" type items no longer appear in DDL section or trigger awkward action suggestions
