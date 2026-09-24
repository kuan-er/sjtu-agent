# AI Agent token 量级调研（2026 下半年）

> **观测日期：2026-09-24**（所有价格均为此日抓取的官方页面快照；价格随时变动，使用前请复核）
> 方法：优先抓取厂商官方定价页 / 模型页 / 发布公告；官方页面不可得时才用二手源，并在「缺口」中标注。
> 标注约定：**未验证** = 官方页面未给出该字段；**估计** = 由官方数据推算，非官方直接声明。

---

## 1. 对比总表（价格单位：USD / 1M tokens）

| # | 模型 (API ID) | 上下文 | 最大输出 | 输入（未命中缓存） | 缓存命中读取 | 缓存写入 | 输出 | 思考 token 计费 | 官方服务端工具 | 知识截止 | 发布/观测 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **deepseek-flash** (V4.1-Flash) | 1M | 384K | 0.15 谷 / 0.30 峰 | 0.003 谷 / 0.006 峰 | 未单列（无独立写入费） | 0.60 谷 / 1.20 峰 | 思考输出按 output 计费 | — | 未验证 | 2026-09-10 |
| 2 | **deepseek-v4-pro**（退场中） | 1M | 384K | 0.66 谷 / 1.32 峰 | 0.022 谷 / 0.044 峰 | 未单列 | 1.98 谷 / 3.96 峰 | 同上 | — | 未验证 | 2026-08-13 GA |
| 3 | **claude-opus-5-5** | 1M | 128K（Batch 300K） | 4 | 0.20 | 5m: 5 / 1h: 8 | 20 | 是，按 output 计费 | web search / code exec / computer use | **Jun 2026** | 2026-09-22 |
| 4 | **claude-sonnet-5** | 1M | 128K（Batch 300K） | 2 | 0.20 | 5m: 2.50 / 1h: 4 | 10 | 是，按 output 计费 | 同上 | **Jan 2026** | 2026-06-30 |
| 5 | **claude-fable-5-1** | 1M | 128K | 10 | 0.25 | 5m: 12.50 / 1h: 20 | 50 | 是，不可关闭 | 同上 | **Jun 2026** | 未验证 |
| 6 | **claude-haiku-4-5** | 200K | 64K | 1 | 0.10 | 5m: 1.25 / 1h: 2 | 5 | Extended thinking | 同上 | Feb 2025 | 2025-10-15 |
| 7 | **gpt-6-astra** | 1,050,000 | 128K | 10（>272K 输入：20） | 1（>272K：2） | 12.50 | 50（>272K：75） | 是，按 output 计费 | web search / file search / code interpreter / hosted shell / computer use / MCP | **Apr 30, 2026** | 未验证 |
| 8 | **gpt-6-sol** | 1,050,000 | 128K | 2（>272K：4） | 0.20（>272K：0.40） | 2.50 | 10（>272K：15） | 同上 | 同上 | **Apr 20, 2026** | 未验证 |
| 9 | **gpt-5.6-terra** | 1,050,000 | 128K | 2（>272K：4） | 0.20 | 2.50 | 12（>272K：18） | 同上 | 同上 | **Feb 16, 2026** | 未验证 |
| 10 | **gemini-3.8-flash** | 1,048,576 | 65,536 | 0.75（促销价，2027-01-01 起 1.50） | 0.075（促销；1M 输入缓存另计存储费） | 隐式缓存无独立写入费 | 3.75（含 thinking token；2027 起 7.50） | **含在 output 价内** | Google Search 接地 / Google Maps / code exec / URL context / computer use(预览) / file search | 未验证 | 2026-09（页面 2026-09-02 更新） |
| 11 | **kimi-k3** | 1,048,576 | 默认 131,072，上限 1,048,576 | ¥20 | ¥2 | 5min TTL ¥20 / 1h TTL ¥40 | ¥100 | 是，思考始终开启，按 output 计费 | 联网搜索（官方称正在更新，暂不建议生产） | 未验证 | 未验证 |
| 12 | **kimi-k2.7-code** | 262,144 | 未验证 | ¥6.50 | ¥1.30 | 未验证（K2 无写入费字段） | ¥27 | 是 | 同上 | 未验证 | 未验证 |
| 13 | **glm-5.3** | 1M | 128K | 1.40 | 0.26 | 未单列，缓存存储「限时免费」 | 4.40 | 是，思考强制开启（low/high/max） | Web Search $0.01/次 | 未验证 | 未验证 |
| 14 | **glm-5.3-flash** | 1M | 128K | 0.15 | 0.03 | 限时免费 | 0.50 | 同上 | 同上 | 未验证 | 未验证 |
| 15 | **glm-5.3-flashx** | 1M | 128K | 0.37 | 0.075 | 限时免费 | 1.25 | 同上 | 同上 | 未验证 | 未验证 |
| 16 | **qwen3.8-max** | 1,000,000 | 131,072（CoT 上限 262,144） | 2.00（新加坡；中国北京 1.65） | 隐式缓存 0.25（新加坡）/ 0.206（北京） | 显式缓存创建 2.50（新加坡）/ 2.063（北京） | 6.00（新加坡；含 CoT）/ 4.951（北京） | **思考输出与答案合并计入 output** | Web Search（仅北京/新加坡支持） | 未验证 | 快照 0902 = 2026-09-02 |
| 17 | **MiniMax-M3** | 1,000,000（官方保证最低 512K） | 未验证 | 0.30（≤512K，永久 5 折）；>512K 为 0.60 | 0.06（≤512K）；>512K 为 0.12 | 未验证（M3 表未列写入价） | 1.20（≤512K）；>512K 为 2.40 | 是（thinking 可开关，价格相同） | Server Tools（Beta） | 未验证 | 2026-06-01 |
| 18 | **MiniMax-M2.7** | 204,800 | 未验证 | 0.30 | 0.06 | 0.375 | 1.20 | 是 | Server Tools（Beta） | 未验证 | 未验证 |

**开权重（可自托管）模型**：MiniMax-M3（官方称「首个同时具备前沿编码 + 1M 上下文 + 原生多模态的开权重模型」，权重已开源）、Kimi K3（2.8T 参数，官方称全球首个开源的 3 万亿级模型，权重已发布）、DeepSeek-V4.1-Flash（552B MoE，权重见 HuggingFace）、GLM-5.3（Z.AI 官网标为 flagship；有报道称权重因网络安全风险延后发布，见缺口）。

---

## 2. 分厂商要点（缓存 / 思考计费的特殊之处）

### DeepSeek
- **峰谷定价是 agent 成本的主要变量**：谷时价为峰时 5 折。峰时 = 周一至周五 UTC 01:00–04:00 与 06:00–10:00（不含中国法定节假日）；其余时间（含周末与节假日全天）为谷时。
- **缓存写入不单独计价**（表中未列该计费项）：未命中缓存的 input 直接按 cache-miss 价计费，命中后按 cache-hit 价。缓存命中价极低（flash 谷时 $0.003，约为未命中价的 1/50），是 agent 长前缀复用的关键杠杆。
- 官方明确「cache-hit charges often account for a large share of agent costs」，V4.1-Flash 将 KV cache 压到上一代的 1/4 HBM、1/8 SSD 存储。
- **产品线正在切换**：2026-09-14 04:00 UTC 起，所有 `deepseek-v4-pro` 请求被路由到 V4.1-Flash 并按 Flash 价计费，直到 V4.1-Pro 发布；官方称正在淘汰 V4-Pro。**因此「deepseek-v4-pro」目前是价格表条目，不是独立模型。**
- 模型名兼容：`deepseek-v4-flash`、`deepseek-v4-flash-vision-exp` 已退役但仍可调用，路由到 V4.1-Flash。

### Anthropic
- **缓存写入按 TTL 分档计价，且贵于输入**：5 分钟 TTL = 1.25× 输入价，1 小时 TTL = 2× 输入价。缓存读取极便宜（Opus 5.5 为输入价的 1/20）。最小可缓存 prompt 长度 512 tokens（Opus 5.5）。
- **思考不可关闭**：Opus 5.5 与 Fable 5.1 为 adaptive thinking「always on」，只能通过 `effort` 参数调深度；同理 thinking blocks 与产生它的模型绑定（换模型不能复用）。
- 长输出走 beta：Batch API 上 Opus 5.5 / Sonnet 5 可用 `output-300k-2026-03-24` 头拿到 300K 输出。
- 供应商文档中同时存在 `Claude Mythos 5.1 / 5`、`Claude Fable 5` 等同价位条目，属于「Additional models」，选用前需确认可用性。

### OpenAI
- **两段式上下文计价是最大陷阱**：超过 272K 输入 tokens 时，**整个请求**的输入与缓存价翻倍、输出价 1.5×。「短上下文 / 长上下文」两列即为该分界。
- 新一代（GPT-6 / GPT-5.6）**显式收取 cache writes**，价格为未缓存输入价的 1.25×，且 >272K 时同步翻倍；老一代（gpt-5.5 及以前）无 cache-write 列。
- 服务端工具单独计费：Web Search $10 / 1k calls（非推理模型的 preview 版 $25/1k，检索内容 token 免费），且检索内容 tokens 另按模型费率计；Container（Hosted Shell / Code Interpreter）按 20 分钟会话计 $0.03–$1.92。
- Batch / Flex = Standard 的 50%，Fast mode = 2×（2026-07-30 起 priority 更名 fast）。

### Google
- **thinking token 直接含在 output 单价内**，不单独计价，因此推理强度越高账单越高但不改变单价。
- **促销价有时限**：Gemini 3.8 Flash 现为 $0.75/$3.75，2027-01-01 起回到 $1.50/$7.50。
- **Interactions API 只支持隐式缓存**（无独立 cache write 费）；隐式缓存默认开启，最小 4,096 tokens 才可能命中；显式缓存需切回 `generateContent` 且含存储费。
- **旗舰缺位**：官方 `/models` 列表当前最高为 Gemini 3.8 Flash（3.8 Live / Flash-Lite / 3.7 / 3.6 / 3.5 等），**没有可用的 Pro 级旗舰**；多家媒体报道 Pro 版持续延期。
- 工具：Google Search 接地、Google Maps 接地、code execution、URL context、computer use(预览)、file search。

### Moonshot（Kimi）
- **K3 是唯一单独收缓存写入的国产模型**，且按 TTL 分档（5min ¥20 / 1h ¥40，均等于未命中输入价），命中后 ¥2；官方说明命中后有效期自动续期、不再重复收写入费。K2 系列表格**没有**缓存写入列。
- 价格以 **人民币** 计价（$ 换算随汇率变动，本表不换算）。
- **思考无法关闭**：K3 始终开启，支持 `low`/`high`/`max`，默认 `max`。
- `max_completion_tokens` 默认 131,072、最大 1,048,576 —— 输出上限接近上下文长度，对 agent 一次性大产出友好。
- 1M 上下文 **不按长度分段计价**；缓存命中要求上一请求 prompt > 256 tokens，否则请求被丢弃不入缓存。

### 智谱 / Z.AI
- 定价表把「缓存存储」单列，当前为**限时免费**，缓存读取约为输入价的 1/5–1/6。
- **GLM-5.3 思考强制开启**（`thinking.type` 只能 `enabled`），支持 `low`/`high`/`max`，默认 `max`；从旧版迁移时若仍传 `disabled` 会直接报错。
- 内置 Web Search 极便宜：$0.01 / 次。
- 另有 Coding Plan 订阅（按 points 计费，非高峰时段 5 折），与 API 按量计费是两套体系。

### 阿里 Qwen
- **思考 token 与答案合并计入 output 价**，但表格把非思考/思考输出价分列（部分模型两者相同，部分不同），选型时要看清用的是哪一列。
- **区域价差显著**：qwen3.8-max 新加坡（国际）$2/$6，中国北京 $1.65/$4.951，且同一模型在欧洲/美国/日本/香港区域同为 $1.65/$4.951。
- 缓存三层：隐式缓存命中（约输入价 10–12.5%）、显式缓存创建（125% 输入价）、显式缓存读取。北京区域另有 Batch 文件/对话价。
- 上下文缓存折扣与 Batch 折扣**不可同时叠加**。
- 注意：本表数字取自阿里云 Model Studio 国际站英文文档；**阿里云百炼中国站与 Qwen 自有平台的定价未在本次核验范围内**。

### MiniMax
- M3 **按输入长度分档**：≤512K 与 >512K 两档，且当前有「永久 5 折」；Priority 服务层为标准价的 1.5×。
- M3 定价表只有 Prompt caching **Read**，无 Write 列；老一代 M2.7/M2.5 有 Write（$0.375）。
- M3 的 thinking 可开关，**两种模式价格相同**。
- Token Plan 订阅用 credits（Plus $20/月 ≈ 1.7B tokens 等），与按量计费语义不同。
- 自托管：M3 权重已开源，官方提供「Local & Self-hosted」文档章节。

---

## 3. 缺口与不确定项（明确列出，勿当作事实使用）

1. **知识截止日期大面积缺失**：Anthropic 与 OpenAI 在模型页明确给出（已录入），但 **DeepSeek、Gemini 3.8 Flash、Kimi K3、GLM-5.3、Qwen3.8-Max、MiniMax-M3 的官方页面均未给出 knowledge cutoff** —— 全部标为「未验证」，不要用旧版本的数字替代。
2. **最大输出 tokens 缺失**：Kimi K2.7-code / K2.6、MiniMax-M3 / M2.7、以及旧版 GLM-5.1/5/4.7 的官方页面未列出 max output（GLM-5.3 与 GLM-5.3-Flash 的 128K 已在官方模型页确认并录入），标为「未验证」。
3. **GLM-5.3 权重是否已发布存疑**：有二手报道称 Z.AI 因网络安全风险推迟权重发布；官方 overview 称其为 flagship 且宣传语含「Open-source SOTA coding capabilities」，但未见明确的权重下载页。**自托管可行性需另行确认。**
4. **Gemini 旗舰缺位**：当前 API 目录无 Pro 级旗舰，官方未公布时间表；媒体报道均为二手信息。若调研需要「Gemini 旗舰」口径，需明确说明这一空白。
5. **Kimi K3 发布日与汇率**：官方文档未给出 K3 发布日期；价格以人民币计价，本表不做汇率换算，跨厂商比较时需自行换算并记录汇率与日期。
6. **DeepSeek 知识截止**：官方新闻页与定价页均未列出，仅有二手百科类来源，**未采用**。
7. **各厂商「缓存写入」计费口径不统一**：Anthropic 按 TTL 分档、OpenAI 统一 1.25×、Kimi 按 TTL 分档且等于输入价、阿里分隐式/显式、MiniMax 老代有写入新一代无、Google 隐式缓存无写入费。务必按厂商逐项核对，不要用统一模型套算。
8. **本表未覆盖**：Grok/xAI、Mistral、Llama 系列、Gemma、gpt-oss-120b/20b、Cohere、以及自托管场景的 GPU 成本；中国境内各厂商（百炼中国站、火山引擎、腾讯云等）转售价。
9. **促销价的时效性**：Gemini（至 2026-12-31）、MiniMax（永久 5 折标注）、OpenAI GPT-5.6 Sol（至少至 2026-11-21）、阿里夜间/白天折扣均会变动。
10. **Anthropic 长尾模型**：`Claude Mythos 5.1/5`、`Claude Fable 5` 出现在定价表中但未纳入对比表的规格核验。

---

## 4. 来源（按行号对应总表）

| # | 来源 URL |
|---|---|
| 1,2 | https://api-docs.deepseek.com/quick_start/pricing ・ https://api-docs.deepseek.com/news/news260910 ・ https://api-docs.deepseek.com/news/news260813 |
| 3 | https://platform.claude.com/docs/en/models/opus-5-5/overview ・ https://platform.claude.com/docs/en/about-claude/pricing |
| 4 | https://platform.claude.com/docs/en/models/sonnet-5/overview |
| 5 | https://platform.claude.com/docs/en/about-claude/pricing |
| 6 | https://platform.claude.com/docs/en/models/haiku-4-5/overview |
| 7,8,9 | https://developers.openai.com/api/docs/pricing ・ https://developers.openai.com/api/docs/models/gpt-6-astra ・ …/gpt-6-sol ・ …/gpt-5.6-terra |
| 10 | https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash ・ https://ai.google.dev/gemini-api/docs/pricing ・ https://ai.google.dev/gemini-api/docs/latest-model ・ https://ai.google.dev/gemini-api/docs/caching |
| 11,12 | https://platform.kimi.com/docs/pricing/chat ・ https://platform.kimi.com/docs/guide/kimi-k3-quickstart ・ https://platform.kimi.com/docs/models |
| 13,14,15 | https://docs.z.ai/guides/overview/pricing ・ https://docs.z.ai/guides/llm/glm-5.3 ・ https://docs.z.ai/guides/overview/overview |
| 16 | https://www.alibabacloud.com/help/en/model-studio/qwen3-8-max ・ https://www.alibabacloud.com/help/en/model-studio/model-pricing |
| 17,18 | https://platform.minimax.io/docs/guides/pricing-paygo ・ https://platform.minimax.io/docs/guides/text-generation ・ https://www.minimax.io/models/text/m3 ・ https://www.minimax.io/blog/minimax-m3 |

**二手来源（仅用于缺口项的背景判断，未用于表格数值）**：
- Gemini 旗舰延期报道：https://fortune.com/2026/09/03/google-shipped-four-gemini-flash-models-in-106-days-but-its-flagship-frontier-model-is-still-nowhere-to-be-seen/
- GLM-5.3 权重延期报道：https://www.deeplearning.ai/the-batch/glm-5-3-makes-cybersecurity-gains
