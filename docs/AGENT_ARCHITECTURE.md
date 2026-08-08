# sjtu-agent Agent 架构改进设计

> 目标：让核心 Agent（4 个 bot + CLI 共用）**更可靠、更省、更可扩展**。
> 起点：画像"念旧账" bug —— 这是 **Context Rot（上下文腐烂）** 的典型症状：窗口里塞进陈旧事实，模型照单全收。

---

## 一、四层工程体系（Prompt / Context / Harness / Loop）+ Graph

各层是"俄罗斯套娃"关系，**外层不取消内层，而是增加新维度**：

```
Loop Engineering  → 设计"自动操作 Agent 的系统"
  └─ Harness Engineering → 设计"Agent 的运行环境"
       └─ Context Engineering → 策展"模型看到什么信息"
            └─ Prompt Engineering → 优化"怎么问"
```

**演进时间线（2026 视角）**：

| 阶段 | 时间 | 核心 |
|------|------|------|
| Prompt Engineering | ~2024 | 怎么问 |
| Context Engineering | 2025 | 喂什么（策展上下文窗口） |
| Harness Engineering | 2026 初 | Agent = Model + Harness；**改变环境让失败不再复发** |
| **Loop Engineering** | **2026 中（当前）** | 设计系统自动 prompt agent；**验证是瓶颈** |

| 层 | 核心问题 | 关键原则 |
|----|---------|---------|
| **Prompt** | 怎么问 | 单次提示词；角色/少样本/CoT/格式约束 |
| **Context** | 给什么看 | 上下文腐烂是最大敌人；目标=**最小高信号 token 集/轮** |
| **Harness** | 在什么环境跑 | **确定性约束 > 概率性遵守**；Agent = Model + Harness |
| **Loop** | 怎么持续推进 | 循环模式 + 迭代预算 + 终止条件 + **可验证目标** |
| **Graph** | 状态怎么流转 | 显式状态机；节点=函数，边=路由，条件边=决策 |

> **核心洞察（2026）**：
> - Harness 综述：**Grok Code Fast 1 只改 edit format，benchmark 6.7% → 68.3%，模型完全不变** —— Harness/Context 的杠杆效应远超换模型。
> - "如果你不是模型，你就是 harness"（Harrison Chase / LangChain）；Hashimoto 定义：**"agent 失败时，改变它的环境，让失败无法再发生"**。
> - **验证是 Loop 的天花板**：loop 的性能上限由验证器（测试/编译/独立审查 agent）决定，而非生成模型；没有可靠验证的 loop = "非常自信的 token 熔炉"。
> - Codex 与 Claude Code **独立收敛到几乎相同的 loop 架构** —— 这是编码 agent 的自然均衡，我们的选择应对齐它。

---

## 二、现状诊断（已对照代码核实）

| 层 | 现状 | 差距 |
|----|------|------|
| **Prompt** | `prompts.py` 638 行 SYSTEM_PROMPT，一次性注入 | 未分层、可能有冗余；未做"金发姑娘区"校准 |
| **Context** | 会话消息**无截断**（`_core.run_one_turn` 无限 append）；日期+画像+记忆每轮全量注入；无缓存感知 | **无压缩（带摘要）、无 tool 结果清理、前缀可能不稳定** → 长会话腐烂（1M 窗口下仍是质量杀手） |
| **Harness** | 工具注册表 `_TOOL_REGISTRY`（~59 个）；`execute_python` 跑在本地无沙箱 | 无运行时 schema 校验、无危险操作拦截、无成本/调用可观测 |
| **Loop** | `_run_one_turn` 单轮 tool-call 循环（隐式 ReAct） | 无迭代预算、无失败重试、无反射/规划 |
| **Graph** | 无 | 需评估是否引入显式状态机 |

---

## 三、可迁移思想（来自多智能体 Agent Harness 项目笔记）

参照项目是"文件系统记忆的 Agent Harness"（作者参与的内部项目），其第 0 层正是本主题。可迁移的核心思想：

| 思想 | 含义 | 迁移到 sjtu-agent |
|------|------|------------------|
| **文件系统 = 上下文 swap 空间** | LOG 把短期记忆"换出"到文件，PLAN 再"换入"；窗口只留处理中内容 | 会话超预算 → 旧对话落盘（已有 conversation_log），窗口只留摘要+近期 |
| **滑动窗口** | 最近 N 轮全文、更早压缩、再早仅索引 | `trim_session()` 按轮次降保真度 |
| **驱逐优先级** | 效果已持久化 → 原文可释放 | tool 结果已写入文件 → 从窗口释放 |
| **三保真度** | FULL / COMPRESSED / PLACEHOLDER | 会话历史按距离降保真度 |
| **渐进加载** | 先索引后深入，不做全量注入 | 59 个工具定义按需加载（MCP 式 lazy） |
| **确定性约束 > 概率性遵守** | Linter/拦截器/测试门，不靠模型自觉 | execute_python 白名单 + 危险指令拦截 |
| **模型分档调度** | 简单逻辑用廉价小模型，复杂用旗舰 | 简单工具调用降级到便宜模型 |
| **成本审计** | token/调用可视化 | 日志记录每轮 model/tokens |

> 也借鉴"一理念五支柱"里的 **经济与资源学**（成本即生命线）和 **安全与审计学**（红线+成本溯源）——它们直接对应 Harness 的 Enforcement 与 Observability。

---

## 四、设计建议（按层 + 优先级）

### 4.1 Context 管理（🔴 最高优先 —— 抗腐烂，非省容量）

**2026 前提（已核实）**：
- DeepSeek V4 Flash：**1M 上下文 + 自动前缀缓存**（命中价 $0.0028/M vs miss $0.14/M ≈ 50 倍）。**窗口不是约束**；缓存把"重发历史"变便宜（前缀命中）。
- 但 **Context Rot 在 1M 下照样发生**（Chroma 研究：200K 窗口 50K 就明显退化；"1M 只是悬崖来得更晚，不是消失"）。紧凑上下文（如 8K）通常**优于**松垮的 200K。
- 所以 Context 工程目标是 **质量（抗腐烂）+ 缓存感知（省成本）**，不是省容量。

**六条原则**：
1. **稳定前缀喂缓存**：system prompt + 工具定义放最前且**稳定**（不重排、不改动）→ 命中缓存。
2. **append-only 历史**：旧轮次**不修改**（改中间 → 缓存前缀断裂 → 全 miss，反而更贵）。
3. **压缩带摘要（Compassion，非纯丢弃）**：超质量预算（如 50K）时，把旧轮压缩为密集摘要——**保留决定 / 事实 / 标识符原文**（"压缩成摘要别丢三步后才发现有用的细节"），保留最近几轮原文。
4. **Clearing 原始 tool 输出**：大 tool 结果换占位符（"feed raw tool output verbatim" 是腐烂主因之一），保留 tool_call 记录。
5. **动态内容放最后**：时间戳/会话元数据放最后一条用户消息，不进前缀（否则每轮缓存失效）。
6. **折叠摘要防健忘**：压缩时保留要点（用户意图 + 决定 + 标识符），细节才让用户重述——"不翻旧账也不健忘"的度。

**实施（分步）**：
- **A. Clearing tool 结果**（无损、收益最大）——保留 `tool_call_id`，内容换占位符；OpenAI 兼容接口自行实现。
- **B. 质量预算 + 压缩**——`trim_session()` 按**质量预算**（非容量）触发，带摘要、保留最近几轮；摘要 prompt 要求保留日期/学期断言并标注时效（与画像时效性同一原则）。
- **C. 缓存感知改造**——确认 system prompt 稳定、历史 append-only、动态内容置尾。
- **D. 可观测**——先记录真实 token/成本（尤其涨价在即 + 峰谷 2 倍），再调参，不盲优化。

### 4.2 Prompt 工程（🟡）

- 审计 638 行 SYSTEM_PROMPT：按 身份 / 能力 / 工具使用 / 边界 / 输出格式 分层。
- 校准"金发姑娘区"：具体到可执行、通用到不脆弱。
- 动态部分（日期/画像/记忆）与静态部分分离，便于 prompt caching 命中和后续维护。

### 4.3 Harness（🟡）

- **运行时 schema 校验**：工具函数已有 function schema，`run_tool` 入口加参数类型校验。
- **execute_python 加固**：危险操作白名单 + 拦截器（禁删仓库、禁打印敏感 env），把"靠模型自觉"改成确定性约束。
- **可观测**：日志记录每轮 model / input tokens / 工具调用次数，为成本审计打底。

### 4.4 Loop（🟢 中优先级 —— 2026 年当前战场）

- **迭代预算**：tool-call 循环加 `max_iterations`（如 8），超限收敛为"已尽力，建议分步"。
- **失败重试**：瞬时错误（网络/超时）重试 1 次；单工具失败降级，不中断整轮。
- **可验证目标（2026 核心）**：loop 必须有明确的"完成判据"——对 DDL/课表这类有确定答案的查询，可用结果校验（如抓取结果非空、字段完整）；无验证的循环只会放大错误。
- **轻量 Reflection / maker-checker**：对高风险动作（提交作业、删除、发送消息）由第二个视角复核，或先自检确认——契合 2026 的"验证是瓶颈"判断。
- **子 agent 隔离**：复杂子任务放到干净上下文窗口的子 agent 执行，只回传 1-2k token 摘要（如 homework_agent 已是外部 Claude Code，可内化此模式）。

### 4.5 Graph（评估，不急于引入）

- 现状是隐式 ReAct 循环。LangGraph 式显式状态机对**单 agent 聊天助手收益有限**（复杂度换不来明显体验提升）。
- 更务实的中间态：**Plan-Execute**——复杂任务先规划再执行，作为循环增强或一个工具。
- 若未来做多 Agent（如经理/执行分离），再评估引入状态机。

---

## 五、实施路线（增量、每步可测）

**已完成（上下文工程单元）**：
- ✅ **Phase 1 稳定前缀**：动态时间/记忆移出 system prompt → 用户消息；system 稳定喂缓存（5 个入口全改）
- ✅ **Phase 2 Clearing + 质量预算**：tool 结果清理（无损）+ 64K 质量预算折叠带摘要（缓存感知）
- ✅ **skills 死代码修复**：`build_system_prompt()` 接入全部入口（CLI / 4 bot / web），prompt-only skills 生效
- ✅ **web 聊天补齐**：稳定前缀 + skills + trim 钩子（原绕过点）

| Phase | 内容 | 价值 | 风险 |
|-------|------|------|------|
| **3. Prompt 审计** | SYSTEM_PROMPT（638 行）分层重构 | 🟡 指令遵循更稳 | 低 |
| **4. Harness** | schema 校验 + execute_python 拦截 + 调用日志 | 🟡 可靠/安全 | 中 |
| **5. Loop** | 迭代预算 + 重试 + 轻量反射 | 🟢 更自主 | 中 |
| **6. Graph 评估** | Plan-Execute 试点 | 🟢 复杂任务 | 高（可后置） |

每步独立测试（现有 353 个测试作回归基线），可随时停下不返工。

---

## 六、参考

- 本地：本节"可迁移思想"源自作者参与的一个多智能体 Agent Harness 项目的内部设计笔记（文件系统记忆、Prompt/Context/Harness/Loop 工程），不外链。
- 在线（2025 + 2026）：
  - [What is an agent harness? Why harnesses are replacing agent frameworks](https://arize.com/blog/what-is-an-agent-harness-why-harnesses-are-replacing-agent-frameworks/)（2026，Hashimoto/Chase 观点）
  - [Loop Engineering: The New Division of Labor in the Agent Era](https://pandaily.com/loop-engineering-agent-era-paradigm-jun2026)（2026-06，Loop 六组件 + 验证瓶颈）
  - [从 Prompt 到 Loop：企业 Agent 落地的四层工程进化论](https://cloud.tencent.cn/developer/article/2688156)（2026）
  - [Anthropic Context Engineering cookbook](https://github.com/anthropics/claude-cookbooks/pull/481)（compaction / clearing / 渐进加载 / 子 agent 隔离）
  - [Context Engineering: Why More Tokens Makes Agents Worse](https://www.morphllm.com/context-engineering)
  - [Agent Context Compaction for Long-Running Sessions](https://zylos.ai/research/2026-04-21-agent-context-compaction-long-running-sessions/)（2026，缓存与压缩的交互、"Don't Break the Cache"）
  - [How to Stop Context Rot in 1M-Token Agents](https://dev.to/unfairhq/how-to-stop-context-rot-in-1m-token-ai-coding-agents-a-practical-guide-to-memory-budgeting-and-5clh)（1M 下腐烂仍发生，50% 窗口预算）
  - [DeepSeek API Pricing](https://api-docs.deepseek.com/quick_start/pricing/)（V4 Flash：缓存命中 $0.0028/M、miss $0.14/M、输出 $0.28/M；自动前缀缓存）
  - [LangGraph 架构解析：状态机引擎](https://cloud.tencent.com.cn/developer/article/2551628)（节点/边/条件边/checkpointing）

> ⚠️ 参照项目的 Harness 综述部分来自技术博客，未学术核实；2026 观点（Loop 时代、验证瓶颈）为行业共识性博客/演讲整理，均非同行评审——本设计取其工程原则。
