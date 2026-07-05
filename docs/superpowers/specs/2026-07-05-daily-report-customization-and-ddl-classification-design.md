# Design: 日报定制化 & DDL 智能分类

- **Date**: 2026-07-05
- **Status**: Draft
- **Author**: Azzygoatcoder

## Context

SJTU Agent 的日报（早/中/晚报）和 DDL 查看功能目前存在两个核心问题：

1. **日报内容固化为 6 段固定模板**（作业DDL → 课表 → 实验 → 教务通知 → 校园动态 → 行动建议），用户无法按需调整展示哪些模块。
2. **DDL 不做分类** — Canvas 上老师发布的不一定都是作业（如课程综合评分、期末安排通知等），导致日报给出不合适的内容和行动建议。

**目标**：让用户通过自然对话调整日报内容（Agent 记住偏好并持久化），让 DDL 系统能智能区分"需要完成的作业"和"仅用于通知的条目"。

## Design

### Part A: 日报偏好系统

#### 数据模型

`config.json` 新增 `report_preferences` 字段：

```json
{
  "report_preferences": {
    "sections": {
      "ddl": true,
      "schedule": true,
      "lab": true,
      "jwc": true,
      "news": true,
      "tips": true
    },
    "custom_instructions": "",
    "per_type": {
      "morning": {},
      "noon": {},
      "evening": {}
    }
  }
}
```

- `sections`: 控制各模块显隐，默认全开
- `custom_instructions`: 用户自然语言额外要求（如"多关注物理作业"，直接注入 LLM prompt）
- `per_type`: 不同报别的差异化覆盖

#### 交互流程

```
用户在飞书说："早报不用展示教务通知了，太啰嗦"
  → LLM 识别为日报偏好修改
  → 调用 update_report_preferences(sections={"jwc": false}, report_type="morning")
  → 工具更新 config.json
  → Agent 回复："好的，已调整：早报将不再展示📢教务通知。"
```

#### 新增工具

在 `sjtu_agent/agent/tools/_core.py` 新增：

- `tool_update_report_preferences(sections, custom_instructions, report_type)` — 修改偏好
- `tool_get_report_preferences()` — 查看当前偏好

#### 日报生成改造

`scripts/daily_report.py` 的 `build_report()`:

- 读取 `report_preferences` 
- 按 `sections` 动态构建 LLM 提示词（替换当前硬编码 6 段结构）
- 将 `custom_instructions` 注入提示词
- `per_type[report_type]` 覆盖通用偏好

### Part B: DDL 智能分类

#### 数据模型

`fetch_canvas()` 返回的每个 DDL 项增加：

```python
{
    # ... 现有字段 ...
    "type": "assignment" | "notification" | "unknown",
    "type_confidence": 0.95,  # LLM 分类置信度
    "description": "...",      # Canvas assignment description (HTML)
}
```

`_serialize_ddl()` 也增加 `type` 和 `type_confidence`。

#### 分类流程

```
fetch_canvas(classify=True)
  → 对每个 assignment 拉取 description 字段
  → 规则预筛:
      - submission_types=["none"] 且 description 含「通知/公告/提醒/评分/评价/问卷/反馈」
        → type=notification, confidence=1.0
      - submission_types 包含 online_upload / online_text_entry / online_url
        → 进入 LLM 分类
  → 剩余项 → 批量 LLM 分类（单次调用，一次处理所有 assignments）
  → 分类结果写入 DDL 缓存（随 15 分钟 TTL）
```

#### LLM 分类提示词

```
以下是 Canvas 上的若干条目，请判断每个是「作业/任务」还是「课程通知」：

[列表: 课程名 + 作业名 + description]

返回 JSON: [{"index": 0, "type": "assignment", "confidence": 0.95}, ...]

判断标准：
- 要求学生提交/完成具体内容 → assignment
- 仅信息告知、无需提交 → notification
- 评价/评分/问卷/反馈类 → notification
- description 为空且 submission_types 为 none → notification
```

#### 触发时机

- `daily_report.py` 收集数据时 → `tool_get_ddls(classify=True)`
- 用户主动说「查 DDL」→ LLM 调 `get_ddls` 工具时传 `classify=True`
- 不传 `classify=True` 时复用上一次的分类缓存

#### 展示效果

- 默认只展示 `type=assignment` 的项
- 末尾提示「已过滤 X 条课程通知，回复"全部"可查看」
- `get_ddls` 增加 `include_notifications` 参数供用户主动查看全部

### Part C: SYSTEM_PROMPT 更新

`prompts.py` 增加：

```
## 日报偏好

用户可能要求调整早/中/晚报的内容。识别到意图后调用 update_report_preferences：
- "早报不要XX" / "晚报加上XX" → 修改对应 sections
- "日报多关注XX" / "日报语气轻松点" → 更新 custom_instructions
- 修改成功后简短确认，下次日报自动生效
```

## Files to Modify

| File | Changes |
|------|---------|
| `scripts/daily_report.py` | `build_report()` 读偏好，动态构建提示词；`_collect_data()` 传 `classify=True` |
| `sjtu_agent/agent/tools/_core.py` | 新增 `tool_update_report_preferences`、`tool_get_report_preferences`、`_classify_canvas_ddls()`；`_serialize_ddl()` 增加 `type`/`type_confidence`；`tool_get_ddls()` 增加 `classify` 和 `include_notifications` 参数 |
| `ddl_checker.py` | `fetch_canvas()` 增加 `classify` 参数，拉取 description，调用分类逻辑 |
| `sjtu_agent/agent/prompts.py` | SYSTEM_PROMPT 增加日报偏好修改和 DDL 分类行为指引 |

## Implementation Order

**Phase 1: DDL 分类基础设施**（更高优先级 — 直接解决失败案例）

1. `ddl_checker.py` — `fetch_canvas()` 增加 `classify` 参数，拉取 description
2. `_core.py` — 新增 `_classify_canvas_ddls()` 批量 LLM 分类函数
3. `_core.py` — `_serialize_ddl()` 增加 `type`/`type_confidence`，`tool_get_ddls()` 增加 `classify` 参数
4. `_core.py` — DDL 缓存扩展，分类结果随缓存一起存储（15 分钟 TTL）
5. `daily_report.py` — 日报调用时传 `classify=True`，DDL 区段过滤 notification

**Phase 2: 日报偏好系统**

1. `_core.py` — 新增 `tool_update_report_preferences` + `tool_get_report_preferences`
2. `prompts.py` — SYSTEM_PROMPT 增加日报偏好修改行为指引
3. `daily_report.py` — `build_report()` 读取偏好，动态构建提示词

## Verification

1. **DDL 分类**: `python ddl_checker.py` 检查输出包含 `type` 字段；验证已知案例（课程综合评分 → notification）
2. **日报过滤**: `python daily_report.py --test --type morning` 确认不展示通知类条目；行动建议合理
3. **对话交互**: 飞书中说「早报不要教务通知」→ 确认 `config.json` 更新 → 下次日报生效
4. **回归**: `pytest` 全量通过，不破坏现有 DDL 获取功能
