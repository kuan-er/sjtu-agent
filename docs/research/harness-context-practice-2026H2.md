# AI Agent token 量级调研（2026 下半年）

> 调研范围：agent harness / coding agent 的**上下文管理实作**。全部条目按证据等级标注：
> **[文档]** = 官方文档或官方工程博客；**[源码]** = 可读实现/源码常量；**[报告]** = 用户 issue / 第三方实测；
> **[估计]** = 由公开数据推导，非官方数字；**未公开** = 检索不到公开数字。
> 调研日期：2026 年（H2）。DeepSeek Harness（dsh）数据来自本机 checkout 源码阅读与本次会话实测。

---

## 1. Compaction / auto-compact

### 触发阈值

- **[文档] Claude Code**：默认「到模型上下文上限再压」，但原生 1M 窗口模型（Sonnet 5 / Fable 系列 / Opus 4.7+）**默认约 967K tokens** 触发；Sonnet 4.6、Opus 4.6 未开 extended context 时以及 Bedrock/Vertex/Foundry 上的 Opus 4.8+ 在 **200K 边界**触发。用户可 `/autocompact 500k`、`--autocompact` 或 `CLAUDE_CODE_AUTO_COMPACT_WINDOW` 设定，**取值 100K–1M**，裸数字 100–1000 表示「千」。[docs/model-config](https://code.claude.com/docs/en/model-config)
- **[报告] 社区逆向 Claude Code 2.1.75**：公式为 `effective = min(native, override) − 20000`，`threshold = effective − 13000`（13K 安全余量），另有 warning/error 阈值 `effective − 20000`、blocking limit `effective − 3000`。Sonnet 4.6（200K）→ 167K（约 93%）；Opus 1M → 967K（约 99%）。[claude-wiki auto-compact deep dive](https://github.com/johnzfitch/claude-wiki/blob/master/02-Claude-Code-CLI/auto-compact-deep-dive.md)
  - **交叉验证**：该逆向公式给出的 967,000 与官方文档「about 967K tokens by default」**完全吻合**，说明这套常量可信度较高。
- **[报告] OpenAI Codex**：默认阈值取**原始窗口的 90%**，而非生效窗口。gpt-5.6-sol 实测 raw 272,000 / effective 95% = 258,400 / 默认阈值 244,800，即**实际等到可用窗口的 94.74% 才压缩**；正确值应为 `floor(258,400 × 0.90) = 232,560`。[codex#40095](https://github.com/openai/codex/issues/40095)
- **[报告] Codex v0.100.0 引入硬钳制**：`effective_auto_compact_limit = min(user_config_limit, context_window × 90%)`，用户自定义的更高阈值被静默忽略（issue 已 closed as not planned）。[codex#11805](https://github.com/openai/codex/issues/11805)
- **[报告] Codex 目录上限 ≠ 模型规格**：gpt-5.6-sol 在 Codex 目录中 `context_window: 372000`、`effective_context_window_percent: 95` → 生效 353,400，默认压缩阈值 334,800；而公开 API 规格为 1,050,000。[codex#31860](https://github.com/openai/codex/issues/31860)
- **[文档] OpenCode v2**：压缩公式 `estimated >= min(input limit − buffer, context limit − max(output reserve, buffer))`。默认 `keep.tokens = 15000`、`buffer = 20000`、`auto = true`，输出预留上限 **32,000**。示例：128,000 输入上限 − 20,000 buffer = **108,000** 触发。[opencode.ai/v2/docs/compaction](https://opencode.ai/v2/docs/compaction/)
- **[源码] OpenHands**：`LLMSummarizingCondenser` 类字段 `max_size` 默认 **240 events**、`keep_first` 默认 **2**；但 SDK `default_condenser()`（主 agent 与子 agent 共用）用 `max_size = 80`、`keep_first = 4`。压缩目标是 `max_size // 2`（减半）。触发原因三档：REQUEST / TOKENS / EVENTS，其中 **TOKENS 记 HARD，EVENTS 记 SOFT**。[llm_summarizing_condenser.py](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py)（默认值曾由 PR 从 120 提到 240：[PR#12267](https://github.com/OpenHands/OpenHands/pull/12267)）
- **[源码] DeepSeek Harness（dsh）**：`compaction-basic` 插件常量 `thresholdRatio = 0.8`、`retainRatio = 0.16`、`maxTokens = 8192`、`compactionRetries = 1`、`maxOverflowRetries = 1`、`auto = true`。阈值按模型容量缩放：`thresholdTokens = floor(contextWindow × 0.8)`，`retainTokens = floor(contextWindow × 0.16)`；加载期强制 `retainTokens < thresholdTokens`。触发点两处：`agent/pre-step`（压力）与 `agent/request-error` + `CONTEXT_WINDOW_EXCEEDED`（溢出）。**[源码]** 本机路径 `<dsh-runtime>/node_modules/.pnpm/@deepseek-ai+dsh-compaction_*/node_modules/@deepseek-ai/dsh-compaction-basic/lib/index.js`

### 保留什么 / 丢什么

- **[文档] Claude Code 压缩后逐项处理**：system prompt 与 output style 保留；项目根 CLAUDE.md、无 `paths:` 的 rules、auto memory **从磁盘重新注入**；git status 重新读取；plan mode 计划重新注入；`paths:` 规则与子目录 CLAUDE.md 在读到匹配文件时重载；**最多重读 5 个最近修改过的文件**（>5,000 tokens 的文件只回路径引用，显示为 `Referenced file`）；已调用 skill 正文重新注入，**每 skill 上限 5,000 tokens、总量上限 25,000 tokens，超限先丢最旧的**；**skill 描述清单不重新注入**。[docs/context-window](https://code.claude.com/docs/en/context-window)
- **[文档] OpenCode v2**：本地 checkpoint 保留「最近 ≤ `keep.tokens` 的序列化上下文」+ 结构化摘要（模板含 Objective / Next Move 等，**至少要有 `## Objective` 这类标题**，否则一次纠正重试后失败）。大 tool result 在 checkpoint 里被压成 **2,000 字符**上限。[opencode.ai/v2/docs/compaction](https://opencode.ai/v2/docs/compaction/)
- **[文档] OpenCode**：明确写「**Compaction is lossy**」，要保留精确近期细节就调大 `keep.tokens`。历史消息仍存在磁盘，只是不进模型上下文。
- **[源码] dsh**：`selectCompactableRange` 从尾部按 token 计费反向累加，累计 ≥ `retainTokens` 为止，保留**逐字尾部**；且**永不切断 assistant tool-call / tool-result 配对**（`toolPairingBalancedBefore/After`）。摘要**必须严格小于**被遮蔽内容，否则抛错（`framedSummaryTokenCount >= shadowedRouteTokenCount`）。
- **[估计] Claude Code 摘要量级**：官方 context-window 可视化里摘要块按 `Math.round(sumTokens × 0.12)` 计，即**约 12%**；同页 FIG B（官方博客）另标「summary 约 20k tokens」。两者都是示意/代表值，非契约。

### 失败模式与代价

- **[报告] 阈值错配的实际代价**：一个成熟 Codex 线程在触发前有 **35 次**请求输入 ≥232,560，这段「缝隙」里处理了 **8,376,048** input tokens。成熟线程 10 分钟峰值：46 次采样、9,833,093 input tokens、均值 213,763/次。[codex#40095](https://github.com/openai/codex/issues/40095)
- **[报告] 压缩收益的量级**：压缩后「每次调用平均输入」下降 **70.3%** 与 **76.4%**；替换历史为 **5 条保留消息 + 1 个不透明 compaction item**，本地估算压缩后约 **26–27k tokens**。远端压缩耗时约 **85s / 104s**。[codex#40095](https://github.com/openai/codex/issues/40095)
- **[报告] 长线程基线规模**：单个成熟 rollout = 114,915,129 input tokens（其中 112,230,400 cached）、840 次采样补全、**9 次 compaction**、238.635 分钟、缓存复用 **97.6637%**。[codex#40095](https://github.com/openai/codex/issues/40095)
- **[报告] Codex 回溯触发误压缩**：TUI backtrack 后上下文指示器跳到 `0% left` 并触发自动压缩——根因是回滚后用 JSON 序列化历史的启发式估算严重高估。[codex#9601](https://github.com/openai/codex/issues/9601)
- **[文档] OpenCode 的「压不动」边界**：当请求几乎全是固定 system prompt + tool schema 时压缩无法腾出空间，官方举例 `128k context = 120k fixed instructions and tools + 8k conversation`。溢出恢复只重试 **1 次**。[opencode.ai/v2/docs/compaction](https://opencode.ai/v2/docs/compaction/)

---

## 2. Sub-agent / 上下文隔离

- **[文档] Claude Code 子代理启动上下文**（官方可视化的代表值）：子代理 system prompt **900**、自己那份 CLAUDE.md **1,800**、MCP + skills **970**、任务 prompt **120**；子代理自己读了 2,200 + 800 + 3,100 = **6,100** tokens 的文件；**回传父上下文只有 420 tokens** 的最终文本 + 一小段元数据。原话：「That's the context savings.」[docs/context-window](https://code.claude.com/docs/en/context-window)
- **[文档] Claude Code 隔离边界**：非 fork 子代理**看不到**父对话历史、已调用 skills、已读文件、auto memory、output style、父的上下文窗口大小（窗口由子代理自己的模型决定）；但**会**加载各层 CLAUDE.md/AGENTS.md 与 git status。内置 Explore / Plan **跳过** CLAUDE.md 和 git status 以求快、省。[docs/sub-agents](https://code.claude.com/docs/en/sub-agents)
- **[文档] 隔离的成本项**：子代理描述本身占上下文，自定义子代理描述合计 **超过 15,000 tokens** 时启动会告警并给出总数。嵌套深度默认 **3 层**（历史：v2.1.172–216 为 5，217–218 为 1，219 起为 3）。同会话并发子代理上限 **20**。[docs/sub-agents](https://code.claude.com/docs/en/sub-agents)
- **[文档] 反例警告**：官方明确「大量子代理各自回传详细结果会显著消耗上下文」——隔离省的是中间过程，不省最终汇报。[docs/sub-agents](https://code.claude.com/docs/en/sub-agents)
- **[文档] Cursor**：子代理「通常从全新的上下文窗口开始，不带父智能体完整对话……父智能体无需承载子智能体的全部工作上下文」；并**移除了**鼓励用子代理探索代码库的提示词（模型已原生掌握该模式），以降低不必要的协调开销。[cursor.com/cn/blog/improved-token-efficiency](https://cursor.com/cn/blog/improved-token-efficiency)
- **[文档] 缓存视角**：子代理首请求**读不到**父缓存（前缀不同），只暖自己的缓存；父缓存不受影响。子代理默认只拿 **5 分钟 TTL**，而订阅内主对话是 1 小时。[docs/prompt-caching](https://code.claude.com/docs/en/prompt-caching)
- **[源码] dsh**：`subagent` / `tool-subagent` / `tool-subagent-fork` 三个独立插件，fork 变体继承父对话（与 Claude Code 的 fork 语义一致）；子代理注册表与 provider 常驻 host plane。

---

## 3. Tool result 处理 / 截断与「外溢到文件」

- **[报告] Codex 硬性按字节截断**：GPT-5.2 harness 用 `TruncationPolicy::Bytes(10_000)`，shell 输出与 MCP 响应在 **~10KB** 处截断，导致 >10KB 文件无法一次读完；系统提示里明写「command line output will be truncated after 10KB regardless」。反馈要求改成按 token 计。[codex#7906](https://github.com/openai/codex/issues/7906)
- **[报告] Codex 两级丢失（legacy shell 路径）**：第一级 `DEFAULT_OUTPUT_BYTES_CAP = 1024*1024`（**1 MiB**）静默丢弃且**不计数**；第二级做 50/50 头尾省略，把 `…N tokens truncated…` 插在**载荷中间**。模型侧预算 10,000 × 4 = **40,000 bytes**。实测一条记录交付 **40,069 字符**，提示落在 offset **20,044**；>1 MiB 的输出提示数字恒为 **252,144**（常数，不携带信息）。作者统计 3 天：**7,070 条 tool output，84 条被截断（1.19%）**，其中 9 条带该常数。[codex#35421](https://github.com/openai/codex/issues/35421)
- **[报告] Codex 协议默认值**：`ModelInfo.truncation_policy` 默认 `TruncationPolicyConfig::tokens(10000)`；app-server 的 `outputBytesCap` 缺省回落到**每流 1 MiB**。[codex#9536](https://github.com/openai/codex/issues/9536)、[app-server README](https://raw.githubusercontent.com/openai/codex/rust-v0.148.0/codex-rs/app-server/README.md)
- **[文档] Claude Code 外溢到文件**：hook 输出**超过 10,000 字符**会被存到文件，Claude 只拿预览 + 文件路径；压缩后重读的文件 **>5,000 tokens** 也只回路径引用。[docs/context-window](https://code.claude.com/docs/en/context-window)
- **[文档] OpenCode**：checkpoint 内大 tool result 以 **2,000 字符**上限的短记录表示（原文示例 `tool output (limited to 2,000 characters)`）；附件只留描述符、去掉内嵌数据。[opencode.ai/v2/docs/compaction](https://opencode.ai/v2/docs/compaction/)
- **[文档] Microsoft Agent Framework**：`ToolResultCompactionStrategy` 把旧 tool-call 组折叠成 `[Tool calls: get_weather, search_docs]` 这类摘要消息，`MinimumPreserved` 默认 **2**（保住当前轮的 tool 交互）；更激进的 `SelectiveToolCallCompactionStrategy` 只留最后 N 组。[learn.microsoft.com compaction](https://learn.microsoft.com/en-us/agent-framework/concepts/agents/conversations/compaction)
- **[源码] dsh「先剪后压」顺序**：溢出路径先调 `toolResultPruner.pruneSession(session)`、**重新计量**，若已低于阈值就直接不压缩；压力路径同理（剪完仍超阈值才压缩）。即：tool-result 剪枝被当作比 compaction 更廉价的第一道闸。剪枝的具体字符/token 阈值在本次 checkout 的依赖闭包内**未公开**（`tool-result-pruner` 仅作为插件 id 出现在 `cordis.patch.yml`）。
- **[实测] dsh 本次会话**：一次 `web_fetch` 返回约 50KB 页面时，harness 提示 `Omitted 12895 bytes. Full formatted result stored at: <temp path>`，即**超限部分落盘 + 主上下文只保留截断视图与路径**——与 Codex / Claude Code 的外溢模式同构。

---

## 4. 渐进式披露（skills / tools on demand）

- **[报告] MCP tool schema 的真实开销**（用 Anthropic `count_tokens` API 实测 11 个生产工具）：重的约 **1,024 tokens**（`ctx_batch_execute`、`ctx_execute`），`ctx_fetch_and_index` 972、`ctx_execute_file` 822、`ctx_index` 858、`ctx_search` 785；轻的 `ctx_stats` **103**、`ctx_doctor` **107**。结论：**比最简 schema 贵 5–15×**；20–30 个 MCP 工具 = 发第一条用户消息前就占掉 **15–30 KB**；20 工具 × 500 tokens = **10,000 tokens**。生产成本：2,600 次会话首轮 schema 约 **$0.15/会话**（$15/1M）≈ **$390**，按 75% 缓存命中降到约 **$0.04/会话 ≈ $100**。建议上限：发现层 **300 tokens**、调用层 **1,000 tokens**。[MCP#2808](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/2808)
- **[文档] Claude Code 的应对**：MCP 工具默认**只列名字**（可视化里 **120 tokens**），完整 schema 延后，模型按需用 tool search 拉取；`ENABLE_TOOL_SEARCH=auto` 表示「装得下（**窗口的 10%** 以内）就预加载」，`=false` 全量加载。skill 启动只占**描述**（**450 tokens**），`disable-model-invocation: true` 的 skill **完全不进上下文**直到被调用。工具用 `defer_loading: true` 发**轻量 stub**而不是删除定义。[docs/context-window](https://code.claude.com/docs/en/context-window)、[claude.dev prompt caching](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/)
- **[文档] Cursor 的三个实测数字**：① 把内置工具移出静态上下文，**静态上下文中的工具描述 tokens 减少 60%**；② 更早把 MCP 工具移入动态上下文，**调用过 MCP 工具的会话总 token 数减少 46.9%**；③ 判断依据是「**大多数工具真正用得上的对话不到 20%**」。另有精简系统提示 **约 66%**。[cursor.com/cn/blog/improved-token-efficiency](https://cursor.com/cn/blog/improved-token-efficiency)
- **[报告] 什么时候「按需加载」反而更贵**（arXiv 2608.14943，五套配对基准）：skill 规模 SearchQA ≈1,966、SpreadsheetBench ≈8,116、ALFWorld ≈1,087、ScienceWorld ≈6,097、SynthProc ≈9,728 tokens。结果：SpreadsheetBench 上 hybrid **−39.8%**、Skill Block **−35.6%**、reference **−31.7%**（静态删减 **−55.5%**）；ScienceWorld 上 Skill Block **−62.5%**、hybrid **−52.8%**；SynthProc 上 **−73.0% / −66.6%**。但 SearchQA（小 skill）上 hybrid 只 **−27.4%**，被强引导的 Skill Block **+48.4%**；ALFWorld 上 Skill Block 仅 **−12.6%**、hybrid **−3.2%**、reference **+24.2%**。结论句：**「conditional loading 只有在 skill 含大量非每轮必需内容时才划算；对又小又常用的 skill，加载开销会吃掉全部收益。」** 模型化盈亏平衡：`净收益 ≈ Σ(1−p_i)s_i − [(r−1)(F+c+X) + rT]`。[arXiv 2608.14943](https://arxiv.org/html/2608.14943v1)
- **[报告] 缓存纠正的记账法**：多轮任务里 provider 报的 `input_tokens` 含 cache read，直接当新 token 会严重误导；该论文用 `effective input = new input + 0.1 × cache read`。[arXiv 2608.14943](https://arxiv.org/html/2608.14943v1)

---

## 5. Prompt / KV 缓存策略

- **[文档] Claude Code 三层前缀**：① system prompt + 工具定义（工具集变则失效）② project context：CLAUDE.md / auto memory / 无 scope 的 rules（会话开始、`/clear`、`/compact` 时变）③ conversation（每轮都变）。**前缀精确匹配，改动发生在前缀任何位置，其后全部重算；没有按文件或按段的缓存**。[docs/prompt-caching](https://code.claude.com/docs/en/prompt-caching)
- **[文档] 会打断缓存的动作**：切模型、改 effort、开 fast mode、连/断 MCP server（仅当工具被载入前缀）、启用/禁用提供 MCP 的插件、整工具 deny（tool search 不可用时）、**compaction 本身**、堆积大量图片、升级 Claude Code。[docs/prompt-caching](https://code.claude.com/docs/en/prompt-caching)
- **[文档] 不打断裂缓存的动作**：改仓库文件、会话中改 CLAUDE.md（但改动**不会**在本会话生效，要等 `/clear`/`/compact`/重启）、切 permission mode、切 output style、调用 skill/command、`/recap`、`/rewind`、**spawn 子代理**。[docs/prompt-caching](https://code.claude.com/docs/en/prompt-caching)
- **[文档] TTL 经济学**：两档 TTL，5 分钟与 1 小时；1 小时档**写入按更高费率计费**。默认策略：Claude 订阅且在套餐额度内 → 主对话 **1h**、其余（子代理/compaction/标题）**5m**；走 usage credits / API key / 云厂商 → 全部 **5m**。短促工作从不满 5 分钟空闲时，1h 档「多付写入费却用不上长寿命」。[docs/prompt-caching](https://code.claude.com/docs/en/prompt-caching)
- **[文档] 缓存作用域**：实际按**一台机器 + 一个目录**隔离；同一仓库的不同 worktree 各自建前缀、互不命中；同目录并发会话可互相命中。命中率指标为 `cache_creation_input_tokens` 与 `cache_read_input_tokens`，`/usage` 会打印 `Prompt cache (main)` 的命中率、miss 次数与最近一次 miss 的可能原因（如 `tool definitions changed`）。[docs/prompt-caching](https://code.claude.com/docs/en/prompt-caching)
- **[文档] Claude Code 团队的硬规矩**（官方工程博客，2026-04-30）：为了缓存，「静态在前、动态在后」；历史踩坑包括**在静态 system prompt 里塞精细时间戳、工具定义顺序非确定性打乱、改工具参数**。更新信息要走**下一条 user message / tool result 里的 `<system-reminder>`**，不改 prompt。**会话中绝不增删工具**——所以 Plan Mode 不换工具集，而是把 `EnterPlanMode`/`ExitPlanMode` 做成工具。**会话中不换模型**：原话「已经聊到 100k tokens 的 Opus 会话，切到 Haiku 反而更贵，因为要重建缓存」；要换就用子代理做 hand-off。[claude.dev](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/)
- **[文档] 缓存安全的 compaction fork**：压缩调用必须用与父对话**完全相同**的 system prompt、user/system context、工具定义，把父的历史前置、压缩指令作为**最后一条 user message** 追加，才能命中父前缀，官方称命中价约为**十分之一**；代价是需要预留「compaction buffer」。[claude.dev](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/)
  - **[源码] dsh 采用同一设计**：`buildSummarizationInput` 重放 system + header tools + 区域消息，`COMPACTION_INSTRUCTION` 作为最后一条 user message 追加；源码注释明写目的是「让这次辅助调用成为最后一次路由请求的真实前缀，复用 provider 的 KV cache 而不是使其失效」。
- **[文档] Cursor 的显式断点**：GPT-5.6 起 OpenAI API 允许客户端显式标缓存断点，Cursor 把断点放在**稳定部分之后、不断增长的对话之前**；并把可变设置挪到缓存边界**之后的「幻影用户消息」**（承载技能、子智能体、环境信息）。效果：**冷缓存未命中率降低 20%**。另一项：`Read` 工具原先每行标行号（**每行约 3–5 tokens**），改为**每十行标一次**，**缓存读取 tokens 减少 1.6%**，质量无下降。[cursor.com/cn/blog/improved-token-efficiency](https://cursor.com/cn/blog/improved-token-efficiency)
- **[报告] 命中率高≠便宜**：Codex 成熟线程缓存复用 **97.66%**，但用量仍高——「cached input 只是被反复作为超大 prompt 的一部分处理」，经验式 `input-token throughput ≈ active prompt size × sampling-call density`。[codex#40095](https://github.com/openai/codex/issues/40095)
- **[文档] 官方态度**：Claude Code 团队对缓存命中率**设告警、太低就开 SEV**，与可用性同级别监控。[claude.dev](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/)

---

## 6. Memory 文件 / 项目指令（AGENTS.md / CLAUDE.md 风格）

- **[文档] 典型量级（Claude Code 官方可视化的代表值）**：system prompt **4,200**、auto memory（MEMORY.md）**680**、环境信息 **280**、MCP 工具名（延后加载）**120**、skill 描述 **450**、`~/.claude/CLAUDE.md` **320**、项目 CLAUDE.md **1,800**。**合计约 7,850 tokens** 在用户敲第一个字之前就已占用；对照「Your prompt」仅 **45 tokens**。[估计]（由官方示意数字求和）来源：[docs/context-window](https://code.claude.com/docs/en/context-window)
- **[文档] 是否常驻**：CLAUDE.md 与 auto memory **每次会话开头都加载**，以 user message 形式在 system prompt **之后**注入（因此是「上下文」而非强制配置，无合规保证）。`~/.claude/CLAUDE.md` 与项目 CLAUDE.md 都常驻；**path-scoped rules（`paths:`）与子目录 CLAUDE.md 是按需加载**的——读到匹配文件才进上下文，也因此**会被 compaction 一并摘要掉**。[docs/memory](https://code.claude.com/docs/en/memory)
- **[文档] 尺寸规范**：CLAUDE.md **目标每文件 <200 行**，「更长的文件消耗更多上下文并降低遵循度」；单文件**超过 4 MiB 直接跳过**。auto memory 的 `MEMORY.md` **只加载前 200 行或前 25 KB（先到者为准）**，超出部分下次加载被丢弃。`@path` import **最多 4 跳**，且「import 只是组织手段，**不会减少上下文**」，因为导入文件在启动时全部展开。path 通配符共享一份 **1,000 条展开模式 / 4 MiB** 预算。[docs/memory](https://code.claude.com/docs/en/memory)
- **[文档] 子代理描述也占常驻预算**：自定义子代理 `description` 合计 **>15,000 tokens** 时启动告警，官方建议「描述写短，细节移到子代理自己的 system prompt（只在该子代理运行时加载）」。[docs/sub-agents](https://code.claude.com/docs/en/sub-agents)
- **[文档] AGENTS.md 的加载优先级**：Claude Code 默认**只在工作目录及其上层都没有 `CLAUDE.md` / `.claude/CLAUDE.md` / `CLAUDE.local.md` 时才读 `AGENTS.md`**（需 v2.1.277+）；可用 **Project instructions** 四档切换：`claude-md-or-agents-md`（默认）/ `claude-md-and-agents-md` / `claude-md` / `managed-only`。`AGENTS.local.md`、`AGENTS.override.md`、`.agents/` 下的文件**不读**。[docs/memory](https://code.claude.com/docs/en/memory)
- **[实测] 本仓库真实值**：`CLAUDE.md` = 9,614 字符 / 155 行 ≈ **2,404 tokens**（chars/4 估计）；`AGENTS.md` = 851 字符 / 24 行 ≈ **213 tokens**。两者均在官方 <200 行建议内，合计约占 200K 窗口的 **1.3%**。
- **[文档] Cline**：明确把「system prompts（Cline 自身内部指令）」列为占用项，量级「相对小，**几千 tokens**」；并给出换算基线 **1 token ≈ 4 字符 ≈ 0.75 词**，源码 **250–400 tokens/KB**、JSON **300–500/KB**、Markdown **200–300/KB**、纯文本 **200–250/KB**；「500 行 TypeScript 文件约 3,000–5,000 tokens」。[Cline Context Windows](https://mintlify.wiki/cline/cline/models/context-windows)
- **[估计] 其他 harness 的常驻项未公开**：Codex 的 `AGENTS.md` 精确 token 成本、Cursor 的 rules 常驻量、dsh 的 system prompt 与 AGENTS.md 预算，均**未检索到官方数字**。

---

## 7. 上下文窗口与 history 预算的取舍（「太小截断 / 太大更贵」）

- **[文档] 官方给出的「实用区间」**（Cline，直接回答该权衡）：模型标称 vs **实用范围** —— Claude Sonnet 4.5 **200K → ~100K–500K**；GPT-5 **400K → ~200K–300K**；GPT-4o **128K → ~80K**；Gemini 2.5 Pro **1M+ → ~600K**；DeepSeek V3 **128K → ~100K**；Qwen3 Coder **256K → ~200K**。原话：**「所有模型在接近硬上限时都会不同程度退化——按实用区间规划，而不是按天花板。」**[Cline Context Windows](https://mintlify.wiki/cline/cline/models/context-windows)
- **[文档] 操作阈值**：Cline 建议上下文表接近模型上限 **80%** 时就考虑压缩或开新任务。按项目规模给窗口档：**<50 文件 → 128K 够用**；**50–500 文件 → 至少 128K–200K**；**500+ 文件 → 200K+，理想 1M**。[Cline Context Windows](https://mintlify.wiki/cline/cline/models/context-windows)
- **[文档] 「太大」的具体代价**：Cursor 的整套 harness 优化把**用户 token 成本降低 7%** 而质量未降，并指出「系统与工具定义会贯穿整个对话，是我们能完全掌控的最大支出来源之一」——即窗口越大，常驻项的乘数效应越强。[cursor.com/cn/blog/improved-token-efficiency](https://cursor.com/cn/blog/improved-token-efficiency)
- **[文档] 「太大」还有质量维度**：Skill Blocks 论文引述输入长度增长会削弱模型对上下文的利用，并给出「大 skill 收益更明显、小 skill 反被开销吃掉」的 regime map。[arXiv 2608.14943](https://arxiv.org/html/2608.14943v1)（同向结论亦见 arXiv 2604.02688 关于「远未到标称上限时推理质量已随输入变长退化」的表述）
- **[文档] 「太大」的压缩质量代价**：1M 窗口若在 ~967K 才压缩，意味着要总结极大的历史；社区分析据此建议对 1M 模型用 `WINDOW=300000–350000` + `PCT=85`，把触发点拉回 **238K–280K**，理由是「保持与 200K 模型相近的压缩频率、允许更大的单次输入、压缩更快、避免上下文失控」。[claude-wiki](https://github.com/johnzfitch/claude-wiki/blob/master/02-Claude-Code-CLI/auto-compact-deep-dive.md)（**社区建议，非官方**）
- **[文档] 「太小」的具体形态**：OpenCode 指出当固定部分（system prompt + tool schemas）几乎吃满窗口时，压缩**无法腾出空间**——`128k = 120k 固定 + 8k 对话`。Cline 的「太小」症状表：上下文窗口超限报错、建议与近期改动矛盾、响应重复/打转、丢失近期编辑、明显变慢。[opencode.ai/v2/docs/compaction](https://opencode.ai/v2/docs/compaction/)、[Cline](https://mintlify.wiki/cline/cline/models/context-windows)
- **[报告] 「窗口标称」不可信**：Codex 目录把 gpt-5.6-sol 的生效窗口压到 **353,400**，而公开 API 规格是 **1,050,000** —— 产品侧 cap 与模型规格是两回事，选窗口要按 **harness 实际给到的 effective window** 而不是模型 spec。[codex#31860](https://github.com/openai/codex/issues/31860)
- **[文档] 业界通用做法：按比例而非绝对值**。可比的默认触发比例：Claude Code **~93%（200K 模型）/ ~99%（1M 模型）**、Codex **90% of raw（≈94.7% of effective）**、OpenCode **input limit − 20,000**、dsh **80%**、OpenHands 事件数而非 token、Microsoft AF 需显式传阈值。**80%–90% 是 2026 H2 的常见落点**，1M 窗口下的「绝对余量固定」（如 dsh 的 16% 保留 vs Claude Code 的固定 13K）已明显分化。

---

## 8. 未公开 / 待验证缺口

1. **dsh `tool-result-pruner` 的阈值**：本次 checkout 的依赖闭包内只有插件 id 与「剪枝先于压缩、剪后重新计量」的调用顺序，**具体字符/token 上限、剪哪些节点未公开**。需读 `@deepseek-ai/dsh-tool-result-pruner` 包本体或其文档。
2. **dsh system prompt / AGENTS.md 的实际 token 预算**：未找到公开数字；建议在 dsh 内用 `/context` 类能力实测。
3. **Claude Code 的绝对 token 阈值契约**：官方只给「about 967K」与 200K 边界，**13K headroom / 20K buffer / 3K blocking** 来自社区逆向（v2.1.75），未见官方确认；且 2.1.75 之后的版本未验证。
4. **Claude Code 压缩摘要的实际压缩比**：官方可视化用 12%、官方博客 FIG B 标 ~20k，**均为示意值**，无官方发布的「摘要 = 被压缩内容的 x%」统计。
5. **`MAX_MCP_OUTPUT_TOKENS` 等上限的默认值**：该环境变量存在，但**默认值未在已获取文档中确认**（中文 env-vars 页提到该变量，未取到默认数值）。
6. **Codex 的自动压缩在实测中的净收益**：issue #40095 作者本人声明「不是匹配的 A/B，14.2% 差异不能全归因于阈值改动」，**账号点数级节省未知**。
7. **OpenHands 的 token 阈值默认值**：SDK 主路径用事件数（80 events / keep_first 4），**token 维度的默认 `max_tokens` 未公开**（`max_tokens` 默认 None）。
8. **Cursor 的 subagent 上下文预算**：只有「全新上下文窗口」的定性描述，**无 token 数字**。
9. **Aider**：repo map 默认 `--map-tokens 1k`，会随会话状态动态扩张（「通常在该值内，但在没有文件加入 chat 时会显著扩张」）——**扩张上限未公开**。[aider.chat/docs/repomap.html](https://aider.chat/docs/repomap.html)
10. **OpenAI「Rethinking skills and prompts for GPT-6 Astra」官方博客**：曾尝试抓取，页面正文未取到（只返回导航），**其中可能含官方渐进式披露数字，待补**。
11. **Microsoft Agent Framework**：`HarnessAgentOptions` 的默认 `ContextWindowCompactionStrategy` 内部比例**未公开**（文档只给 MaxContextWindowTokens=128,000 / MaxOutputTokens=16,384 的示例）。
12. **跨 harness 的横向可比基准缺失**：目前唯一系统性对比是 arXiv 2608.14943（同一个 harness 内四种 loading 机制、GHCP 端点），**没有跨厂商 harness 的 token 效率对照实验**。
