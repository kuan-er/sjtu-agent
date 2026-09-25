# 提案：把 sjtu-agent 接到 DeepSeek Harness（DSH）上

> **⚠️ 2026-09-25 状态更新（先读这条）**：校方今日发布官方桌面产品 **jAide v1.0.0**（`geek.sjtu.edu.cn/products/jaide`）。对本机安装内容的结构分析显示：它是 Electron + Drizzle/SQLite 应用，内嵌 **pi / pi-ai** agent 运行时（**不是** DSH——包内无 Cordis、无 `dsh-*` 痕迹），已具备 Canvas(`oc.sjtu`)、GPA、课表、DDL + 提醒、新闻聚合与推送开关、邮箱（feature flag 关闭）、7 张 dashboard 插件卡片、workflows/workflow_runs，以及 **MCP 客户端支持**（`mcpServers` / `stdio` / `sse` / `streamable`）。
>
> 因此 **§四·五 路线 D（以 DSH 为底座做自己的发行版/客户端）的定位已被现实推翻**：客户端赛道由官方产品占据，且它不在 DSH 生态内——我们不应与之竞争。**§四 的路线 A（MCP + skills）价值反而上升**：DSH 与 jAide **都支持 MCP**，把校园能力做成能力层可以同时服务两边。
>
> sjtu-agent 的剩余差异化：**IM 多通道入口（飞书/微信/QQ/Telegram）、无头/服务端运行、开源可扩展**。

> 评估日期 2026-09-24｜证据来源：**本机 DSH 运行时的 167 个官方包与随包文档**（一手材料）+ 官方仓库开发指南
> 证据分级：**[源码]** 本机官方包 README/源码；**[官方]** 官方文档页；**[实测]** 本仓库测量；**[建议]** 我的取值主张

## 一、结论（先看这段）

**可行，而且不必重写。** 但有三个必须说清的前提：

1. **DSH 的插件是 TypeScript/Cordis 包**（[官方开发指南](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/development.zh.md)：pnpm monorepo、Node 22.19+、tsc/tsdown、Host/Client 双 aggregate、Typert 生成）。把 sjtu-agent 的 Python 代码"重写成 DSH 插件"是**错的方向**——那是重写一个已经在跑的系统。
2. **DSH 没有 IM 通道插件**：本机 167 个官方包里没有 Telegram / 飞书 / 微信 / QQ 的任何形态（`dsh-webhook` 是入站 HTTP 事件，`dsh-message-feedback` 是 Web 界面的消息反馈）。而**这正是 sjtu-agent 对学生的主要入口**（日报、提醒、随手问一句）。所以"整体搬进 DSH"会丢掉产品面。
3. **正确的接法是"能力下沉、入口保留"**：把 sjtu-agent 的**校园能力**以 DSH 原生的方式挂进去（MCP + skills），把 **harness 该干的活**（上下文管理、缓存纪律、沙箱、审批、子代理、会话持久化）交给 DSH；同时**保留** sjtu-agent 自己的入口（CLI / 四个 bot / 定时任务）。

一句话：**sjtu-agent 从"自己造 harness 的校园助手"变成"带聊天入口的校园能力包"，DSH 当引擎。**

> **补充（2026-09-24 追加调研后）**：官方还提供两条让这件事更彻底的路——**组合包 + profile** 的发行机制（bundle/profile/patch 三层，见 §四·五）与**官方 Python SDK**（`deepseek-harness-sdk` + `deepseek-harness-runtime-bin`，**wheel 内自带 `dsh` 可执行程序**，且**启动时必须显式指定 harness home、绝不静默读 `~/.dsh`**）。因此目标形态升级为 **路线 D：以 DSH 为底座、由 sjtu-agent 发行的 Python 包**——学生 `pip install` 就同时得到 harness 与校园能力，不需要装 Node；开发/验证用我们自己的 harness home，**不碰你本机的 DSH 环境**。

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

## 四、三条路线（早期备选；见 §四·五的路线 D）

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

## 四·五、路线 D（**新推荐形态**）：以 DSH（MIT）为底座做自己的发行版

> 你问的是"能不能基于 DSH 建立我们自己的 sjtu-agent，把各项内容分类成插件/skill，自由修改"。答案：**能，而且官方机制就是为这件事设计的**——组合包（bundle）+ profile + patch 层。

**官方机制**（[打包与安装插件](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/publish.zh.md)）

- **组合包（bundle）**＝一个 npm 包，声明 `dsh.bundle.patch` 指向自己的 patch 文件 → 回答"这个包贡献什么"；
- **profile**＝`$DSH_HOME/profiles/<name>/`，声明 `dsh.profile.bundles` 的有序列表 + 自己的 `cordis.patch.yml` → 回答"这套配置由哪些组合包按什么顺序组成"；
- **层顺序**：各 bundle 的 patch（按列表序）→ profile 自己的 patch → `$DSH_HOME/cordis.patch.yml` → `--patch`；后层按行覆盖，**patch 替换整行 config 而非深合并**；
- **安装**：`dsh plugin --profile <name> add <npm 包 | ./目录 | github:owner/repo | .tgz>`（底层 pnpm；首次自动以 `@deepseek-ai/dsh-base` 初始化 profile）；
- **发行方式**：npm 预构建（推荐）｜`pnpm pack` 出 tarball｜git 直装（**需用户授权安装期执行构建脚本**，官方明说是安全面——建议锁定 commit，或干脆不用）；
- **表层 bundle 可以有自己命令行**：挂一个 startup provider 插件（`inject = ['cmdlineArgs']` + `parseCmdline`）→ 例如 `dsh --profile sjtu ...` 拥有我们自己的 flag。

**Python 侧的关键事实**（[官方 Python SDK](https://github.com/deepseek-ai/deepseek-harness/tree/master/python) + PyPI 元数据实测）

- `deepseek-harness-sdk`（`deepseek_harness`）：用 stdio 上按行分隔的 JSON-RPC，以**子进程**方式驱动 harness 的高层轮次 API；
- `deepseek-harness-runtime-bin`（`deepseek_harness_runtime`）：官方原文——"packages the normal `dsh` CLI and its closed Node dependency tree into a native executable, so **SDK use requires no system Node.js**" → **消费者不需要 Node**；
- 同一 wheel 还带着 `sdk-minimal` 与**完整 `web` profile（含前端产物）** → 学生从 Python 侧就能用上 DSH 的 Web UI；
- **隔离有保证**：官方原文"requires a non-empty `DSH_HOME`; **it never falls back to `~/.dsh`**" → 我们用自己的 harness home（如 `~/.sjtu-agent/harness`），**不会碰你本机的 DSH 环境**；
- 许可证 MIT，包所有者是官方账号（DeepSeek-Harness / tianyicui）→ 可放心作为依赖。

**必须一起交代的限制**（否则会踩坑）：

1. **平台覆盖不全**：只发布 Linux x64 / Linux arm64 / **macOS arm64** / Windows x64 —— **没有 macOS Intel、没有 Windows arm64**，这些平台的学生仍得走原来的纯 Python 路线；
2. **体积**：wheel 每个约 **69–78 MB**（自带 Node 闭包与 ripgrep 等伴随文件）→ `pip install` 会明显变重；
3. **`dsh plugin --profile ...` 需要 `pnpm`**，但"ordinary SDK/profile execution does not" → 我们的发行版应当**自己物化 profile（直接写 package.json / cordis.patch.yml / 拷 bundle 文件）**，而不是让学生跑 `dsh plugin add`；
4. **还是预发布**：SDK `0.1.5rc1`、runtime-bin `0.1.2a3` → 必须**锁死精确版本**，并保留现有纯 Python 实现作为回退（不能把学生唯一入口押在 alpha 上）。

**内容分类（你问的"各项内容分类成插件、skill 等，自选"）**

| sjtu-agent 内容 | 落到 DSH 的形态 | 说明 |
|---|---|---|
| 校园工具（DDL/课表/成绩/食堂/邮箱/Canvas/水源/新闻/提醒，约 15-25 个） | **MCP server**（保持 Python） | 由我们 bundle 的 patch 挂 `dsh-mcp-client` 配置行；**不需要写 TS**，已实测 stdio 握手通过 |
| 通用工具（fs/bash/web/搜索/子代理） | **不提供** | 用 DSH 原生，避免重复与 schema 税 |
| 高频玩法（查作业、查课表、去哪吃、报修、讲座…） | **skills**（markdown） | `dsh-skill-filesystem` 按需加载；改文案不用发版 |
| 人格与身份（"交大校园助手"的语气与边界） | **preset / persona 配置行** | `dsh-agent-presets`、`dsh-persona`、`dsh-system-prompt` |
| IM 机器人（飞书/微信/QQ/Telegram） | **独立 Python 进程（先用 SDK 驱动）**；长期可做成 TS 插件包 | DSH 生态没有 IM 通道，这块只能我们补——也正是差异化 |
| 定时（日报/提醒） | `dsh-schedule`（何时跑）+ 我们的 Notifier（推到哪） | 调度与推送渠道解耦 |
| 安装/配置向导/doctor/凭据 | **我们 bundle 的 startup provider**（`--profile sjtu` 自有命令）；过渡期先由 Python CLI 包装 | 学生入口保持 `sjtu-agent setup` 不变 |
| 自研 harness（`runner.py` 循环、`context.py` 折叠、缓存纪律） | **丢掉** | 换 DSH 的 agent loop + compaction + 缓存纪律（这正是收益） |
| Web GUI / TUI | **用 DSH 自带** | 省一大块维护成本；我们只保留配置页或直接不做 |

**发行形态（推荐）**：`pip install sjtu-agent` → 依赖官方 SDK/runtime wheel → 首次运行把我们的 profile/bundle 物化到**自己的** harness home → 学生拿到「DSH 级 harness + 校园能力 + 我们的 IM 入口」，且不需要装 Node。

**"自由修改"的边界**：MIT 允许 fork 与修改；但**优先用组合（bundle + patch + 插件）而不是 fork**——fork 会把上游更新变成我们的负担。只有当某个行为必须改内核（例如想要 DSH 没有的通道抽象）时，才 fork 单个包，并在我们的 bundle 里用 patch 覆盖那一行。

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

1. **走路线 D 吗？**（以 DSH 为底座、我们自己的 profile + bundle + Python 发行包）如果确定，我就按 §五 的 Phase 0 起步。
2. **Phase 0 是否现在做？** 我可以在**独立的 harness home**（`~/.sjtu-agent/harness` 之类）里做端到端验证——扩 MCP 能力面 → 物化 profile → 在隔离 home 里跑通"这周有什么作业"，**全程不碰你本机的 DSH**。验证通过再决定是否正式提交。
3. **MCP 暴露面**要不要我先按"学生最常用的 15 个"给一版清单？
4. **bot 长期保留吗？** 决定 Phase 1 是"换引擎"还是"双引擎并存"。
5. **发行渠道**：先只支持 `pip install` + 本地物化 profile，还是一并做 npm 包（让已经装了 DSH 的同学能 `dsh plugin add`）？
