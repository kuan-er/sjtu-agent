# AI Agent token 量级调研（2026 下半年）— 实测数据与证据分级

标注约定：**[实测]** 有公开方法学/可复现；**[自测]** 厂商或社区自报（部分披露方法）；**[推算]** 本报告据原始表格换算；**[估算]** 无方法学或口径不明；**[二手]** 经媒体/帖子转述，未见原始数据。

## 0. 三份最硬的底稿（其余数字都应与之对照）

| 来源 | 日期 | 规模与方法 |
|---|---|---|
| TraceLab: Characterizing Coding Agent Workloads for LLM Serving（[arXiv 2606.30560](https://arxiv.org/html/2606.30560v1)） | 2026-06-29 | 43 名开发者、4,265 会话、357,161 次 LLM 调用、432,510 次工具调用，Claude Code + Codex，23 个模型，观测窗 2025-09～2026-06；数据/脚本开源 |
| Agentic Coding in the Wild: GitHub Copilot Traces at Production Scale（[arXiv 2608.00101](https://haoran-qiu.com/publication/agentic-coding-in-the-wild/)） | 2026-07 | 生产采样 2026-06：3.2M 用户、13M 会话、761M 次 LLM 调用、95T tokens |
| Prompt-Induced Waste in Coding Agents（[arXiv 2608.01347v4](https://arxiv.org/html/2608.01347v4)） | 2026-08-21 | 预注册；6 个开源模型 + Sonnet 5、24 任务、2 个 harness、4,644 次有效运行、2,801 条 trace 盲标注 |

## 1. 每任务 / 每会话 / 每轮 token（输入 vs 输出）

| 数字 | 来源与日期 | 性质 |
|---|---|---|
| 中位单步（step）：**119K prefix + 875 append + 214 output** tokens；每次请求 8.8 次 LLM 调用、10.8 次工具调用、4.3 分钟 | [TraceLab](https://arxiv.org/html/2606.30560v1)，2026-06-29 | [实测] |
| 全量 **54.90B 输入**（52.56B 命中缓存前缀 + 2.34B 新增）vs **186.9M 输出** → 输出仅占 0.34%，输入:输出 ≈ **294:1** | [TraceLab](https://arxiv.org/html/2606.30560v1) Table 1，2026-06-29 | [推算] |
| 生产规模：95T tokens / 761M 次 LLM 调用 ≈ **125K tokens/次调用**；13M 会话 ≈ 7.3M tokens/会话、58.5 次调用/会话 | [Copilot 生产 trace](https://haoran-qiu.com/publication/agentic-coding-in-the-wild/)，2026-07 | [推算] |
| 同一任务分档实测（Claude Code，关闭缓存，Sonnet 5 / Opus 4.8）：简单问答 43,144/1,145 in/out（$0.0977）；大段代码生成 132,059/2,501（$0.2891）；GitHub 代码审查 607,153/13,012（$1.3444）；**深度研究（联网搜索）15,808,371 / 26,504（$31.88，Opus 版为 8,970,723 / 13,760 / $45.20）** | DevelopersIO，2026-07-21（[链接](https://dev.classmethod.jp/en/articles/claude-code-token-cost-measurement/)） | [自测]（脚本+JSONL 日志，公开口径） |
| 相同模型、12 个 harness、12 个 Python 任务：每解决一任务 **3,500（Aider architect）～292,000（OpenClaw）tokens**；"启动税" 700～26,000 tokens，"启动税 × 轮数" 预测每任务 token 的 R²=0.99 | 2026-06 独立 benchmark，经 [36kr/InfoQ 转述](https://eu.36kr.com/en/p/3975646062113282)（2026-09-09，原文 [TheNewStack](https://thenewstack.io/agent-harness-token-costs/)） | [二手]（原始脚本未公开，作者自陈每组合仅跑 1 次、无方差） |
| 40× 差距：2 个模型 × 3 个 harness（Goose/OpenCode/OpenHands-SDK）、50 个 Terminal-Bench Pro 任务，**每解决一任务 token 最多差 40×**，通过率仅差 2–10pp | The Scaffold Effect，[Zenodo 19819492](https://zenodo.org/records/19819492)，2026-04-27 | [实测]（2026 H1，数据开源） |
| 8 个框架、30 个企业工作流、240 次执行（129 次成功）：**每个成功任务 $0.028（Pi Agent）～$0.195（Claude Code）**；通过率 46.7%（OpenCode）～66.7%（Pi Agent） | Composio（2026-08），经 [36kr 转述](https://eu.36kr.com/en/p/3975646062113282)，2026-09-09 | [二手]（Composio 自陈 Pi 的推理设置不一致、Prime Agent 仅 24/30 可评分） |
| 同一模型（Kimi K3）、28 任务、3 个 harness 的**每任务中位 token：Kimi Code 61K / Hermes 67K / Claude Code 340K**；每任务成本 $0.22 / $0.28 / $2.00；完成 22 / 21 / 20（共 28） | Composio 实验，经 [AI Primer 转述](https://www.ai-primer.com/engineer/stories/claude-code-usage-window-burn-reports)，2026-07-31 | [二手] |
| 单任务（单轮，13 个任务 ×6 档 effort）：Opus 5.5 **$0.0072/任务** vs Opus 5 $0.0204；工具循环（多跳 4 问）**$0.0326/run** vs $0.0519 | synthorai 实测，[2026-09-23](https://dev.to/synthorai/claude-opus-55-vs-opus-5-same-answers-half-the-output-tokens-114d) | [自测]（468 次评分调用 + 48 次工具循环，列出方法与复算口径） |
| 研究型 agent：DeepSearchQA（900 题）**每千次请求 $300（Ultra, 70% 正确率）～$2,400（Ultra8x, 82%）**；对照 GPT-5.4 $701/63%、Gemini 3.1 Pro $707/62%、Sonar Pro $883/28% | [Parallel.ai](https://parallel.ai/blog/deep-research)，2026-04-07（2026 H1） | [自测]（厂商自评，成本为价目表推算） |

## 2. 输入 token 随会话长度的增长

| 数字 | 来源与日期 | 性质 |
|---|---|---|
| **99.60%（Claude）/ 96.56%（Codex）的步是增长的**；平均每步新增 **+1,719 / +1,838 tokens**；上下文收缩步仅 0.39% / 3.43% | [TraceLab](https://arxiv.org/html/2606.30560v1)，2026-06-29 | [实测] |
| 未触发压缩的常驻会话：单次调用上下文峰值 ~180,000 tokens，3 天累计计费 **66.4M tokens（25.2M 新增 + 41.2M 缓存读）**，每次心跳增 ~1.8K tokens；**其中 77% 花在只回复 `[SILENT]` 的心跳轮**；1,353 条消息、0 次压缩 | [hermes-agent issue #106338](https://github.com/NousResearch/hermes-agent/issues/106338)，2026-09-09 | [自测]（用户贴出 SQL 与日志计法） |
| 重发 vs 记忆：5 轮 1,628→605（−62.8%）、10 轮 6,091→1,273（−79.1%）、15 轮 13,175→2,023（−84.6%）、18 轮 18,688→2,632（−85.9%）；作者明确称总量随轮数近似 **O(N²)** | [SAIHM benchmark](https://dev.to/saihmadmin/the-hidden-on2-tax-in-ai-agent-loops-measured-with-a-benchmark-you-can-run-2m5)，2026-06-23（2026 H1） | [自测]（离线可复现脚本；厂商出品，仅计输入） |
| 反例：多智能体注入记忆 token **随深度线性增长**（R²=0.9974，深度 2–6），二次拟合前导系数为负——在该深度区间**未见凸增长** | Total Cost of Agency，[arXiv 2609.23790](https://arxiv.org/abs/2609.23790)，2026-09-20 | [实测]（200 任务企业基准、真实 API） |

## 3. Prompt cache 实测命中率与成本影响

| 数字 | 来源与日期 | 性质 |
|---|---|---|
| 全局前缀缓存命中率 **95.7%**；缓存未命中导致 **3.8×** 于真实新增 token 的重复 prefill；缓存读取约比全新 prefill 便宜 10×，**缓存读是 API 成本主体** | [TraceLab](https://arxiv.org/html/2606.30560v1)，2026-06-29 | [实测] |
| 生产规模：**轮内缓存命中率 ~90%，跨轮骤降至 55%**；模型切换、上下文压缩会进一步击穿缓存 | [Copilot 生产 trace](https://haoran-qiu.com/publication/agentic-coding-in-the-wild/)，2026-07 | [实测] |
| 同模型不同 harness 的缓存占比：**Claude Code 仅 1.5%** 输入走缓存，Codex ~70%，OMP ~57%（作者归因于走 Anthropic 风格端点经网关转协议） | [Composio](https://eu.36kr.com/en/p/3975646062113282)，经 36kr 转述，2026-09-09 | [二手] |
| Codex 整套测试计费 >1M tokens，其中 **77% 是缓存读**（按约 1/10 价）→ 每解决任务反而比"只用一半原始 token"的 Claude Code 更便宜 | [2026-06 独立 benchmark](https://thenewstack.io/agent-harness-token-costs/)，经 36kr 转述，2026-09-09 | [二手] |
| 编程场景缓存命中率 **V4-Pro ≈95%、V4-Flash ≈91%**（降价后实测 ~96%）；同批 ~35M tokens 账单 ¥31.73 → 降价后 ¥5.34（**−83%**） | [量子位实测](https://www.qbitai.com/2026/04/407850.html)，2026-04-27（2026 H1） | [自测]（未公开脚本/原始日志） |
| 缓存churn 现场：392 秒、15 次 Fable 5 调用、**10,263,334 token-events（5,189,449 一小时缓存写 + 5,069,543 缓存读 + 4,312 输出 + 30 常规输入）**，0 次 Edit/Write；按列表价折 $109.07；每次调用携带 650K–685K 上下文 | Reddit r/ClaudeCode（@cosmintrica），经 AI Primer 转述，2026-07-31 | [二手] |
| 缓存断点显式化（GPT-5.6+，稳定前缀 vs 增长对话分离）使**冷缓存未命中率 −20%**；读文件行号由每行改为每 10 行 → 缓存读 token **−1.6%** | [Cursor 官方博客](https://cursor.com/cn/blog/improved-token-efficiency)，2026-09-23 | [自测]（A/B 于生产流量，未公布绝对量） |
| 1.4B token 的 Claude Code 样本中 **96.8% 是缓存读** | DEV 日志审计，经 AI Primer 转述，2026-07-31 | [二手]（样本与脚本未公开） |

## 4. Harness 开销（系统提示 + 工具 schema + 注入上下文）占比

| 数字 | 来源与日期 | 性质 |
|---|---|---|
| 54 个工具（31 内置 + 23 MCP）时**工具 JSON schema 占每次请求 83.1%**；系统提示 12.1%、历史 4.2%、真实对话仅 **0.6%**；约 27,000 schema tokens/请求、~500 tokens/工具；20 轮会话仅 schema 就 540,000 tokens | [hermes-agent issue #67273](https://github.com/NousResearch/hermes-agent/issues/67273)，2026-07-19 | [自测]（来源 `/usage`，跨多会话复核；单方工具） |
| 只发一句 "hello"：最终 prompt **626 行 / 51,655 字符 / ≈22,300 tokens**，用户输入仅 **54 tokens（0.2%）**；auto-memory 27.2%、Operational Guidelines 19.1%、tool_search 清单 15.6% | [qwen-code issue #6097](https://github.com/QwenLM/qwen-code/issues/6097)，2026-07-01 | [自测]（遥测 22,287 与估算吻合；附完整 prompt） |
| Claude Code 空载启动上下文 **24.8K**（系统提示 4.1K + 系统工具 18.4K + skills 2.3K + 消息 36）；清理用户配置前约 **100.0K** | [DevelopersIO](https://dev.classmethod.jp/en/articles/claude-code-token-cost-measurement/)，2026-07-21 | [自测] |
| Cursor 生产：系统提示精简 **−66%**；静态上下文里的内置工具描述 **−60%**；MCP 工具改为动态加载使相关会话总 token **−46.9%**；合计用户 token 成本 **−7%** | [Cursor 官方博客](https://cursor.com/cn/blog/improved-token-efficiency)，2026-09-23 | [自测] |
| MCP/Tools Tax：典型 4–6 服务器部署 **10k–60k tokens/轮**；GitHub 全套 93 工具 ≈**55,000 tokens/轮**（占 200k 窗口 27.5%）；企业 DB 106 工具 ≈54,600；4 服务器主机 15k–20k/轮；称 schema token 占 agent API 支出 **40–60%** | Tool Attention，[arXiv 2604.21816](https://ar5iv.labs.arxiv.org/html/2604.21816)，2026-04（2026 H1） | [估算]/[二手]（转引实践者审计）；同文的 47.3k→2.4k（−95%）为**模拟实测**，作者明示非真实 agent 运行 |
| 多智能体：记忆注入占可变成本 **13.6%**、占全额计费 **~12%**；按深度从 0% 升到**深度 6 的 27.6%** | [arXiv 2609.23790](https://arxiv.org/abs/2609.23790)，2026-09-20 | [实测]（未评估缓存，全为不缓存口径） |
| 本地工具（无直接费用）的结果回流上下文，占一次运行成本 **4–12%**（有界） | [Prompt-Induced Waste](https://arxiv.org/html/2608.01347v4)，2026-08-21 | [实测] |

## 5. 每任务 / 每天成本，跨工具单任务价格对比

| 数字 | 来源与日期 | 性质 |
|---|---|---|
| 企业口径：**每位开发者每个活跃日约 $13**，**90% 用户 <$30/活跃日** | Anthropic 企业数据，经 [Anthropic 博客](https://claude.com/blog/what-a-task-costs-on-opus-5-5)/[Zenn 转述](https://zenn.dev/d_date/articles/a992ab10bbb248)，2026-09-23 | [估算]（无公开方法学；本报告未见原始统计） |
| 角色模拟（按实测单价外推）：工程师档 Sonnet 5 **$6.36/日、$127.14/月**（20 工作日） | [DevelopersIO](https://dev.classmethod.jp/en/articles/claude-code-token-cost-measurement/)，2026-07-21 | [推算]（基于同文实测单价） |
| 分档月账单：轻度 $5–30；日常驱动 **$150–250/月**；重载（并行 agent、长循环）**$500–2,000/月**；单任务常耗 **400K–2M 累计 tokens**；写代码前已烧 40–80K tokens | [Standard Compute](https://standardcompute.com/ai-coding-agent-monthly-cost)，2026-09-07 | [估算]（聚合行业分析，未给原始样本） |
| 极端个人账单：一个月 **73.8B tokens、$49.6K API 等价**，实际用 3×$200 Claude + 1×$200 Codex 订阅承接；1 个工作日耗尽一周 Fable 额度 | Theo（X），经 AI Primer 转述，2026-07-31 | [二手] |
| 每成功任务价格横比：Composio $0.028–$0.195；Kimi K3 三 harness $0.22–$2.00；Opus 5.5 单轮 $0.0072、工具循环 $0.0326 | 见 §1（各行均含 URL 与日期） | [二手]/[自测] |

## 6. Reasoning / thinking token 占输出比例

| 数字 | 来源与日期 | 性质 |
|---|---|---|
| Codex：reasoning **36.8M / 输出 90.1M = 40.8%**（Claude 侧未上报 reasoning） | [TraceLab](https://arxiv.org/html/2606.30560v1) Table 1，2026-06-29 | [推算]（原始数据公开） |
| Opus 5.5 单答案任务：thinking 占输出 **98.4%（默认）/ 99.6%（max）**，且默认不展示 | [synthorai](https://dev.to/synthorai/claude-opus-55-vs-opus-5-same-answers-half-the-output-tokens-114d)，2026-09-23 | [自测] |
| 多智能体 SDLC（ChatDev + GPT-5）：推理 token 占总 token **21.6%**；分阶段 Design 36.0%、Coding 35.1%、Code Review 23.9% | Tokenomics，[arXiv 2601.14470](https://ar5iv.labs.arxiv.org/html/2601.14470)（MSR 2026，2026-04） | [实测]（30 个任务） |
| 措辞效应：`multiple_approaches` 使推理 token **2.4–7.4×**；`deep_thinking` **1.6–2.2×**；`max_certainty` 1.3–1.9× | [Prompt-Induced Waste](https://arxiv.org/html/2608.01347v4)，2026-08-21 | [实测]（预注册+留出集复核） |
| effort 拨档（同一任务，Sonnet 5 输入 token）：low 212,441 / medium 153,585 / high 90,049 / **xhigh 790,243 / max 3,596,002**；对应成本 $0.2198 → **$7.3335** | [DevelopersIO](https://dev.classmethod.jp/en/articles/claude-code-token-cost-measurement/)，2026-07-21 | [自测]（单任务，注意非单调） |
| 高 effort 路径产生约 **7×** 于低 effort 的 tokens | [Anthropic effort 博客](https://claude.com/blog/claude-model-and-effort-level-in-claude-code)，经 [AI Primer](https://www.ai-primer.com/engineer/stories/claude-code-usage-window-burn-reports) 转述，2026-07-31 | [二手] |
| `max` effort：工具循环成本 **3.3×** 于默认（$0.1087 vs $0.0326）、工具调用中位 12 vs 5、输出 3,124 vs 427 tokens，**解题数完全相同** | [synthorai](https://dev.to/synthorai/claude-opus-55-vs-opus-5-same-answers-half-the-output-tokens-114d)，2026-09-23 | [自测] |

## 7. 浪费 token 的失败 / 抖动模式

| 数字 | 来源与日期 | 性质 |
|---|---|---|
| 冗余验证分级成本（相对干净运行中位）：level 1 **1.48×**、level 2 **2.36×**、level 3+ **18.25×**（工具调用 15 次、墙钟 3×），**成功率无梯度**；最极端案例把已全绿的测试套件重跑 6 次 | [Prompt-Induced Waste](https://arxiv.org/html/2608.01347v4)，2026-08-21 | [实测]（n=1,585 干净 vs 213 高冗余） |
| 被丢弃的备选方案：有 1 个未用分支的运行中位成本 **≈1.9×**，而工具调用仅 7→8（浪费走 token 而非工具）；强制"多方案"使成功率 96.5%→**95.2%**（26:13 偏向 baseline），scope 违规 ~3× | [Prompt-Induced Waste](https://arxiv.org/html/2608.01347v4)，2026-08-21 | [实测] |
| 常驻心跳会话：**77%** 的 66.4M 计费 tokens 花在回 `[SILENT]` 的轮次 | [hermes-agent #106338](https://github.com/NousResearch/hermes-agent/issues/106338)，2026-09-09 | [自测] |
| 一次 392 秒会话烧 10.26M token-events，**0 次编辑**（15 次模型调用、8 次 Bash、7 次读文件） | [Reddit @cosmintrica](https://www.reddit.com/r/ClaudeCode/comments/1vc6vp0/my_claude_100_plan_5h_usage_limit_got_used_in_6/)，2026-07-31 | [二手] |
| "每 2 秒 spawn 一个 checker" → 5 小时额度约 10 分钟耗尽 | [Theo（X）](https://x.com/theo/status/2083296221159121156)，2026-07-31 | [二手] |
| 对抗式复审循环：**26 轮、>10M tokens、17h25m** 墙钟才收敛 | [Doodlestein（X）](https://x.com/doodlestein/status/2082852140133793954)，2026-07-31 | [二手] |
| 0→1 生成任务：**"昂贵且无产出的调试循环"普遍存在**，token 消耗更高 ≠ 表现更好；最好模型总成功率仅 43.78% | CLI-Tool-Bench，[arXiv 2604.06742v2](https://arxiv.org/html/2604.06742v2)，2026-07-17 | [实测] |
| 子智能体隔离有协调成本：不共享上下文会重复劳动/做无用任务 | [Cursor 官方博客](https://cursor.com/cn/blog/improved-token-efficiency)，2026-09-23 | [自测]（定性，未给浪费比例） |

## 8. 被广泛引用但缺方法学的数字（点名）

- **"每位开发者每活跃日 $13 / 90% 低于 $30"**：Anthropic 企业口径，被大量二手文章复述（Standard Compute、Zenn 等），未见样本量、口径与统计方法。
- **"单个 agentic 任务 400K–2M tokens"**：Standard Compute 给出区间，未附原始测量。
- **"一个 harness 开机发 33K tokens、另一个 7K，同任务同模型 ~5× 账单"**：与 2026-06 benchmark 的 700 vs 26,000 启动税口径不一致，来源不明，建议不引用。
- **"schema tokens 占 agent API 支出 40–60%"**：Tool Attention 转引"FinOps 审计"，未给可核查出处。
- **"上下文利用率 >70% 后推理质量崩塌"**：多篇转引"实践者报告"，本报告未找到 2026 受控实验支撑。
- **"agentic 工作负载是等效聊天用量的 5–30×"**：无方法学的经验倍数（与 TraceLab 的输入:输出结构一致，但量级口径不同）。
- **"96.8% 是缓存读"**：单一样本、无审计脚本，与 TraceLab 的 95.7% 量级一致但不可独立验证。
- **"70× token 差"**：底层是 3,500 vs 292,000（真实测量），但标题把 harness 差异表述成工具成本黑洞，跨模型/跨配置混合，需谨慎引用。

## 9. 证据强度评估

- **硬数据（可复现、大样本、预注册或开源）**：TraceLab（唯一跨厂商长窗 trace，输入/前缀/新增/输出四分解）；Copilot 生产 trace（95T tokens 量级）；Prompt-Induced Waste（预注册+盲标注+留出复核，浪费机制倍数最可信）；The Scaffold Effect（40× harness 差，数据开源）；Total Cost of Agency（记忆注入占比与线性增长）；CLI-Tool-Bench（无产出调试循环）。以上构成本报告的中枢。
- **中等（厂商/第三方自测，披露部分方法）**：Cursor 效率博客（A/B 于生产流量，但只给相对值）；Composio 两组 harness 对照（作者自陈局限）；synthorai（方法透明、样本小）；DevelopersIO（逐任务原始 token 与脚本，但单账号、无缓存）；量子位（有数字、无原始日志）；Parallel.ai（厂商自评）；Artificial Analysis 索引（论文中作为"更可信统计工具"被引，本报告未直接核验其页面）。
- **软数据（issue/Reddit/X/媒体转述，无脚本）**：两个 hermes-agent issue、qwen-code issue（虽是单方，但附完整 prompt/SQL，可复核性中上）、cosmintrica 与 Theo 的推文/帖、AI Primer、Standard Compute、36kr/InfoQ 编译稿。**这类数字只能用作"存在性证据"，不可作为量级基准。**

## 10. 缺口清单

1. **非编码 chat 智能体（客服/办公，带工具）每轮 token 的公开实测：未找到 2026 数据。** 2026-09 有面向 BFCL multi-round / tau2-Retail / tau2-Telecom 的 harness 优化研究（[arXiv 2609.05736](https://arxiv.org/abs/2609.05736)，EMNLP 2026），但其公开摘要给的是效果提升（14.2/14.9/10.1 个百分点），**没有每轮 input/output/工具结果拆分**。
2. **研究型 agent 的 token 分布：未找到 2026 分布级数据。** 只有单点（一次深度研究 15.8M 输入 / 26.5K 输出 / $31.88）与每千次请求报价（$300–$2,400），缺少中位/分位与输入构成。
3. **分 provider / 分网关的缓存命中率长期统计**：现有仅为 3 个 harness 的单次对照（1.5% vs 70% vs 57%）与两条 trace（95.7%、轮内 90%/跨轮 55%）；无按周/按模型版本的公开分布。
4. **中文社区原创系统实测（含脚本+原始日志）**：量子位有明确数字但未公开脚本/日志；**未找到 2026 可复现的中文实测数据集**（现有中文稿件多为编译稿）。
5. **非编码场景的 harness 开销占比**：只有 issue 级自报（83.1%、22.3K/0.2%），无论文级测量。
6. **"浪费占比"的行业级总量**：有机制级倍数（18.25×、1.9×、77%），**未找到**任何 2026 研究给出"浪费 token 占全行业/全量比例"的估计。
7. **失败重试与缓存未命中的交互**：TraceLab 给出未命中 3.8× 重复 prefill，但**未找到**把"失败重试"与"缓存击穿"联合量化的 2026 数据。
