# 提案：把 sjtu-agent 接到 DeepSeek Harness（DSH）上

> 评估日期 2026-09-24｜证据来源：**本机 DSH 运行时的 167 个官方包与随包文档**（一手材料）+ 官方仓库开发指南
> 证据分级：**[源码]** 本机官方包 README/源码；**[官方]** 官方文档页；**[实测]** 本仓库测量；**[建议]** 我的取值主张

## 一、结论（先看这段）

**可行，而且不必重写。** 但有三个必须说清的前提：

1. **DSH 的插件是 TypeScript/Cordis 包**（[官方开发指南](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/development.zh.md)：pnpm monorepo、Node 22.19+、tsc/tsdown、Host/Client 双 aggregate、Typert 生成）。把 sjtu-agent 的 Python 代码"重写成 DSH 插件"是**错的方向**——那是重写一个已经在跑的系统。
2. **DSH 没有 IM 通道插件**：本机 167 个官方包里没有 Telegram / 飞书 / 微信 / QQ 的任何形态（`dsh-webhook` 是入站 HTTP 事件，`dsh-message-feedback` 是 Web 界面的消息反馈）。而**这正是 sjtu-agent 对学生的主要入口**（日报、提醒、随手问一句）。所以"整体搬进 DSH"会丢掉产品面。
3. **正确的接法是"能力下沉、入口保留"**：把 sjtu-agent 的**校园能力**以 DSH 原生的方式挂进去（MCP + skills），把 **harness 该干的活**（上下文管理、缓存纪律、沙箱、审批、子代理、会话持久化）交给 DSH；同时**保留** sjtu-agent 自己的入口（CLI / 四个 bot / 定时任务）。

一句话：**sjtu-agent 从"自己造 harness 的校园助手"变成"带聊天入口的校园能力包"，DSH 当引擎。**

## 二、DSH 提供了哪些接缝（本机一手证据）

| 接缝 | 官方包 | 对本项目的意义 |
|---|---|---|
| **MCP 客户端** | `dsh-mcp-client` | **配置级接入**：任何语言的 MCP server 都能挂成原生工具，命名 `mcp__<server>__<tool>`，支持 `stdio` 与 `streamable-http`、自动重连、超时、`failOnStartupError` [源码] |
| **skills** | `dsh-skill` + `dsh-skill-filesystem` + `dsh-tool-skill` | 本地目录里的 markdown 技能可被模型按需加载（`modelInvocable` / `userInvocable` 两种调用策略）→ **校园玩法可以写成技能，不用写 TypeScript** [源码] |
| **一次性运行** | `dsh-headless` | `dsh --profile headless "任务"`：不开端口、跑完即退、退出码判定成败 → **cron / bot 后台调用的现成入口** [源码] |
| **进程内嵌** | `dsh-sdk-app`（`--profile sdk`）+ `dsh-sdk-jsonrpc-server` | 换行分隔的 **JSON-RPC over stdio**，模型/工作区由初始化请求传入；[官方文档](https://deepseek-harness.github.io/deepseek-harness/)提到有 Python SDK → sjtu-agent 的 bot 可以用 Python 驱动 DSH [源码/官方] |
| **定时任务** | `dsh-schedule` | DSH 内部的调度能力（可承载日报/提醒的"何时跑"） [源码] |
| **入站事件** | `dsh-webhook`、`dsh-webhook-github` | 外部系统触发 DSH 会话的通道 [源码] |
| **hooks** | `dsh-hooks-claude-code`、`dsh-hooks-codex` | 复用既有的 hook 约定 [源码] |
| **persona / preset** | `dsh-agent-presets`、`dsh-persona`、`dsh-system-prompt` | 定义"交大校园助手"这个 agent 的人格与提示词装配 [源码] |
| **harness 内功** | `dsh-compaction*`、`dsh-session-*`、`dsh-sandbox*`、`dsh-fs-*`、`dsh-token-meter` | 上下文折叠（`thresholdRatio=0.8`）、会话持久化/检索、沙箱与审批、用量计量——**这些正是我们这次调研里手工调整的部分** [源码] |

## 三、能力映射

| sjtu-agent 现状 | DSH 对应物 | 处理方式 |
|---|---|---|
| 76 个工具（DDL/课表/成绩/食堂/邮箱/Canvas/水源/提醒…） | MCP 工具（`mcp__sjtu__*`） | **暴露校园域工具即可**；不要重复 DSH 已有的 fs/bash/web/搜索工具 |
| `runner.py` 的 LLM 循环 + 上下文折叠 + 缓存纪律 | DSH agent loop + compaction + 稳定前缀 | **丢掉自研，换 DSH**（这正是"放在框架上"的收益） |
| 4 个 bot（Telegram/飞书/微信/QQ） | **无对应物** | **保留**；内部由 `dsh --profile headless` 或 SDK 驱动 |
| 定时任务（launchd/systemd/taskschd/psmux） | `dsh-schedule` | 可迁移；但**推送**仍走 sjtu-agent 的 Notifier |
| Web GUI（`sjtu-agent web`）+ TUI | DSH 自带 Web UI / CLI | 二选一：学生直接用 DSH 界面，sjtu-agent 的 Web 退为配置页 |
| skills / AGENTS.md 引导 | `dsh-skill-filesystem` | 校园玩法改写成技能（markdown） |
| MCP server（现有 `scripts/mcp_server.py`，仅 DDL 3 个工具） | `dsh-mcp-client` | **扩成校园能力面**（Phase 0 的全部工作） |
| 凭据/登录（jAccount SSO、Playwright） | 沙箱 + bash 工具 | 留 Python，由 MCP 工具内部完成 |

## 四、三条路线

| | **A. MCP + skills 先行**（推荐先做） | **B. 深度嵌入**（中期） | **C. 全量插件化**（不建议） |
|---|---|---|---|
| 做法 | 扩 `scripts/mcp_server.py` 成校园能力面；写 2-3 个 markdown 技能；文档教学生挂进 DSH | bot/定时任务改为调用 `dsh --profile headless` / SDK，LLM 循环与上下文管理交给 DSH | 把 76 个工具与全部逻辑重写为 TS/Cordis 插件包 |
| 工作量 | **1-3 天** | 1-2 周 | 数月，且要维护双语言栈 |
| 收益 | 立即可用；DSH 用户直接获得校园能力 | 自研 harness 退役，缓存/折叠/沙箱白拿 | 名义上"最原生" |
| 风险 | 工具 schema 占 token（见下） | 依赖 DSH SDK 稳定性（当前 0.1.5-rc） | 重写风险 + 把产品面（bots）丢掉 |
| 保留产品面 | ✅ 全部 | ✅ 全部 | ❌ IM 入口与推送要重建 |

**MCP 路线的已知代价**（必须提前说）：工具定义**每次请求都进上下文**。本仓库实测 76 个工具 schema ≈ 37.8K 字符 ≈ **1.3 万 tokens**；本机 DSH 会话实测单次调用上下文中位 28.9 万 tokens、缓存命中占 99.6%。所以：
- 走 MCP 时**只暴露校园域工具**（估计 15-25 个），把 fs/bash/web/搜索留给 DSH 原生工具——否则白付一份 schema 税；
- 若确实要暴露大工具面，按需分组（skills 承载"什么时候用哪组"，与本次调研 §5 的第 3 条建议一致）。

## 五、建议的分阶段计划

**Phase 0（1-3 天，可先做）**
1. 把 `scripts/mcp_server.py` 从 3 个工具扩成**校园能力面**（建议：`get_ddls`、`get_schedule`、`query_grades`、`recommend_canteen`、`search_campus`、`read_shuiyuan_topic`、`get_news`、`add_reminder` 等 10-20 个），复用现有 `run_tool` 注册表，**不重写业务逻辑**；
2. 确认传输方式：DSH 支持 `stdio` 与 `streamable-http`，仓库现有 `--http` 走的是旧 SSE → **优先用 stdio**；
3. 写 `docs/dsh-integration.md`：学生视角的三步（装 DSH → 在 `settings.yaml` 加一条 `dsh-mcp-client` 配置 → 说人话）；
4. 写 2-3 个技能（`skills/sjtu-ddl.md`、`skills/sjtu-schedule.md`…）覆盖高频玩法。

**验收**：在 DSH 里能问出"这周有什么作业"并拿到真实 DDL；MCP 工具调用出现在会话日志里。

**Phase 0 的前置验证（2026-09-24 实测，已通过）**：直接对 `scripts/mcp_server.py` 做标准 stdio 握手 —— `initialize` 返回 `{"name": "sjtu-ddl", ...}`，`tools/list` 返回 `['get_ddls', 'get_next_lab', 'get_all']`。也就是说 **DSH 的 `dsh-mcp-client` 用 `transport: stdio` 即可挂载**（`command` 指向仓库的 venv python，`args` 指向该脚本），不需要先改任何代码。

顺带两个实测发现：
- FastMCP 的 `serverInfo.version` 报的是 **mcp SDK 自己的版本（1.26.0）**，不是本产品版本 → 已在 `instructions` 字段里带上 `sjtu-agent vX`（本次一并修掉），避免接入方被版本号误导；
- 该 SDK 同时支持 `sse_path` 与 `streamable_http_path`（默认 `/mcp`）→ 将来要用 HTTP 传输不必额外改造，但 **stdio 仍是最省事的选择**。

**Phase 1（1-2 周）**：把 bot 的 LLM 循环切到 `dsh --profile headless` / SDK，自研 `runner.py` 与 `context.py` 退役（保留为回退路径）；日报/提醒的"何时跑"可逐步交给 `dsh-schedule`。

**Phase 2（可选）**：如果 DSH 生态成熟到有 IM 通道插件，或官方愿意提供"校园助手 preset"，再做 TS 插件包把安装体验压到一条命令。

## 六、Non-goals

- 不把校园爬虫/SSO 逻辑改成 TypeScript；
- 不在 Phase 0 动 `runner.py`（先证明能力面可用，再谈换引擎）；
- 不重复实现 DSH 已有的 fs/bash/web/搜索/子代理/沙箱能力。

## 七、待确认

1. Phase 0 是否现在做？（我可以在本机脚手架 + 注入一个最小 MCP 插件做端到端验证，再决定是否正式提交）
2. MCP 暴露面清单要不要我先按"学生最常用的 15 个"给一版？
3. bot 是否确定长期保留？（决定了 Phase 1 是"换引擎"还是"双引擎并存"）
