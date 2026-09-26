# 功能建议：接入水源社区（只读：搜索 + 订阅提醒）

> 面向校内官方 AI 助手（如 jAide）的功能建议，2026-09-25 整理。
> 本项目（sjtu-agent，MIT）已把整条链路跑通并实测，接口清单与坑位可直接照搬。
> 文中实测数据来自水源社区真实帖子（9831 楼）与同款 Discourse 公开实例。

## 一、一句话诉求

水源社区是交大师生日常信息的主要集散地（选课评价、课程资料、实验室招募、二手失物、通知讨论）。
建议接入**只读**能力：**① 站内搜索；② 订阅与提醒**。**不做**发帖、回复、点赞等写操作。

## 二、建议能力

| 能力 | 说明 | 优先级 |
|---|---|---|
| 站内搜索 | 关键词搜索 + 按板块/标签筛选；命中后读相关楼层并附原帖链接 | **必须** |
| 关键词订阅 | 新帖命中关键词即提醒（可多关键词、可限定板块） | **必须** |
| 板块/标签订阅 | 关注板块或标签的新帖 | 建议 |
| 关注帖新回复 | 关注指定帖子，有新回复时提醒 | 建议 |
| 摘要推送 | 每日/每周"水源热帖"摘要 | 可选 |

## 三、接进已有对象（零新造轮子）

jAide 现有的数据模型已经覆盖了这些场景，水源只要作为**新数据源**接进去：

| 水源能力 | 复用现有对象 | 字段映射 |
|---|---|---|
| 帖子列表采集 | `news_items` + `news_source_settings`（现有 3 个源：sjtu-voice / second-classroom / learning-advanced） | `external_id`=topic id、`title`、`summary`=excerpt、`url`=`/t/<slug>/<id>`、`tags_json`=板块/标签、`published_at`=`created_at`；推送开关与周期沿用 `push_enabled`/`push_period` |
| 关键词订阅 | `calendar_automations`（`schedule_json`/`action_json`/`notify_json`/`consecutive_failures`） | 定时扫描新帖，命中即通知；失败计数与告警现成 |
| 报名/截止类帖子 | `calendar_ddls`（`source_label`/`source_url`/`remind_at`/`reminder_fired`） | 从正文识别截止时间，生成带来源链接的 DDL 提醒 |
| 首页展示（可选） | `dashboard_cards` | 新增一张水源热帖卡片 |

## 四、接口清单（Discourse JSON API）

```
搜索      GET /search.json?q=<关键词>[ topic:<id>][ after:<date>][ @username]
列表      GET /latest.json?order=created     /top.json?period=daily|weekly|monthly
          GET /c/<slug>/<category_id>.json   /tag/<tag>.json
单帖      GET /t/<topic_id>.json                     （含 post_stream：posts 首屏 + stream 全量 id 表）
按 id 取楼 GET /t/<topic_id>/posts.json?post_ids[]=<id>&post_ids[]=<id>…   （批量、稳定）
```
常用字段：`id`/`post_number`/`title`/`slug`/`created_at`/`last_posted_at`/`posts_count`/
`highest_post_number`/`views`/`like_count`/`excerpt`/`category_id`/`tags`，
以及每楼的 `reply_count`、`quote_count`、`reads`、`reactions`、`accepted_answer`、`wiki`。

## 五、鉴权：用官方 User API Key（scope 只给 `read`）

- Discourse 官方 **User API Key** 流程：应用生成密钥对与 nonce → 用户在浏览器里点一次授权 →
  应用解密拿回只读 key 存本地；用户可随时在论坛侧撤销。
- **不建议**让用户填账号密码；**不建议**长期依赖 `_forum_session` cookie（权限过大、易过期）。
- 本项目已实现该流程（`sjtu_agent/get_shiyuan_api.py`，`DEFAULT_SCOPES = ['read']`），实盘可用。

## 六、长帖读取策略（**补充**：几千楼的帖子怎么读）

**问题**：水源有大量几千楼的帖子（实测一帖 **9831 楼**）。顺序读前 N 楼既看不到中间的有价值讨论，
也看不到最新的回复——那些内容恰恰在末尾。**提高 N 没有用**：9831 楼 × 约 200 tokens ≈ 60 万 tokens，
全读不可能。

**四个抓手（已在 sjtu-agent 实盘验证）**

| 抓手 | 做法 | 实测 |
|---|---|---|
| **① 帖内检索（首选）** | `search.json?q=<关键词> topic:<id>` → 只取命中的楼层 | 9831 楼帖搜 "Anthropic" → 精确命中 **#1720、#5821** |
| ② 结构化抽样 | 主楼 + per-post `accepted_answer` 楼 + `wiki` 楼（长帖里常是 OP 维护的索引）+ **末尾若干楼** | 返回 `[1,2,3, 9917…9921]` |
| ③ 中段定位 | 按楼层号取区间时，**对 `post_stream.stream` 二分**找到起点，再顺序扫 | 取 4960–4962 → 精确命中 |
| ④ 增量游标 | 只取"比上次更新"的楼层（追更/提醒场景） | — |

**两个只有实盘才暴露的陷阱（务必避开）**

1. **`posts_count` ≠ 楼层号**：删楼会留空号——实测该帖报 9831 楼，但**最新楼层号是 #9921**。
   因此**不要**用 `posts.json?post_number=<posts_count>` 去取"最后一块"（会落在中段）；
   正确做法是切 `post_stream.stream`（全量 id 顺序表）再用 `post_ids[]` 批量取。
2. **`topic_accepted_answer` 是主题级标志**，会出现在同屏每一楼上；拿它当"这楼是答案"会把
   整个首屏都判成最佳答案（实测 summary 从 8 楼膨胀到 25 楼）。只认 **per-post 的 `accepted_answer`**。
3. 同理，楼层号在 stream 上是单调的但**与下标不对齐**，区间查询必须二分，不能按下标直算。

**预算与防幻觉**

- 单楼截断（本项目取 1200 字符）、整轮预算（8000 字符）；
- 返回值里带 `mode`/`sampled`/`truncated` 和一句 **note**：「这是抽样视图，共 N 楼只有 M 楼，
  不要声称读过全文」——否则模型会拿一个切片去下全局结论；
- 让模型在信息不足时如实说"我只看了 N 楼，要不要换个关键词再查"。

## 七、其他工程注意

1. **限流**：会遇到 429，需要退避重试（本项目 30s × 尝试次数，最多 3 次）；轮询间隔 ≥2 分钟。
2. **正文是 HTML**：需转纯文本再喂模型，注意 `<aside class="quote">` 引用块与图片。
3. **区分"未授权"与"没有结果"**：凭证过期要提示用户重新授权，不要显示成"没搜到"。
4. **增量状态原子写**：否则崩溃后会重复推送一批旧帖。

## 八、参考实现（MIT，可直接借鉴）

sjtu-agent 已实现上述全部能力的 Python 版本，思路与接口清单可直接照搬：

| 文件 | 内容 |
|---|---|
| `sjtu_agent/agent/tools/_core.py` | `search_campus`（含水源站内搜索）、`read_shuiyuan_topic`（**四种读取模式**、二分定位、预算控制）、`_shuiyuan_request`（429 退避） |
| `sjtu_agent/get_shiyuan_api.py` | User API Key 只读授权流程（RSA 密钥对 + nonce + 浏览器授权 + 加密 payload 解密） |
| `sjtu_agent/news_aggregator/sources/shuiyuan.py` | 列表采集（`/top.json` + `/latest.json`）与去重 |
| `scripts/shuiyuan_watcher.py` | 关注帖新回复监控（增量游标 + 通知） |
