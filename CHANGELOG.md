# Changelog

本文件记录各版本的用户可见变化。Agent 通过 `get_recent_updates` 读取（问「最近更新了什么」时），不写入 system prompt。

## v0.26.0 (2026-09-24)
- 🛰 **接入 DeepSeek 官方搜索**（默认 `auto`）：本机配了 DeepSeek 官方 Key（`DEEPSEEK_API_KEY`，或 `agent_config.json` 的 LLM 指向 `api.deepseek.com`）时，`web_search` 优先调用官方**服务端搜索**（Anthropic 兼容 Messages API 的 `web_search_20250305` 工具——结构化结果，不受反爬与页面改版影响），失败自动回落到免 Key 抓取栈。结果新增 `backend` 字段说明本轮走的哪条路，`SJTU_WEB_SEARCH_BACKEND=auto|scrapers|deepseek` 可控。代价要说清：**一次官方搜索 = 一个完整模型轮次**（延迟 + token 计费），想零额外花费就用 `scrapers`
- 📐 **上下文预算改为按「后端 + 模型」自适应**（不再写死 256K）：官方 DeepSeek / Anthropic 1M 窗口 → **500K**；**致远一号 `deepseek-chat` / `deepseek-reasoner` 512k → 256K、`qwen` 256k → 128K**（校园网关官方口径就是 512k/256k，**不是**官方的 1M）；其它未公开窗口的网关 → 64K。部署方可用 `SJTU_CONTEXT_WINDOW` / `SJTU_CONTEXT_BUDGET` 直接声明。折叠事件现在记日志（预算、折叠轮数、折叠前后估算），便于用真实数据继续校准
- 📤 **单轮输出上限自适应**：`min(厂商上限, 窗口 − 已用 prompt − 余量)`，下限 1024 —— DeepSeek V4.1-Flash **393,216（384K，思考与正文共享该配额）**、Claude / GPT-6 128K、Gemini 3.8 Flash 65,536、认不出的模型 8192；替换 Anthropic 路径写死的 4096（v0.24.0 日报空回复的根因），后端拒收时自动降档重试。`SJTU_MAX_OUTPUT_TOKENS` 可覆盖
- 📚 **新增 2026 下半年 Agent token 量级调研**（本机 3,076 次真实调用实测 + 官方价目交叉核对）：单次调用上下文中位 **28.9 万 tokens**、p90 58.1 万、峰值 71.4 万，而新增输入中位仅 282 tokens；**缓存命中占上下文 99.6%**（8 天负载按官方价 ≈$4.5，无缓存则 ≈$135）；harness 固定开销约 **1.74 万 tokens**（76 个工具 schema 占 1.3 万）。附 18 个模型的窗口/输出/三档价格对照表。文档站新增「调研与参考」分组（只放结论与对照表，原始调研留在仓库）
- 🧹 文档同步新口径：`README_EN` 的搜索说明（去掉早已失效的 DuckDuckGo）、`AGENT_ARCHITECTURE` 的预算数字、`SERVER_DEPLOYMENT` 的引擎列表与搜索方案、`CLAUDE.md` 环境变量表

## v0.25.0 (2026-09-24)
- 🔍 **联网搜索重做**：DuckDuckGo 兜底其实早就死了（lite 端点返回 202 反爬页、解析恒为空，且大陆网络不可达），Bing 的 HTML 结果链接 **100% 是 `bing.com/ck/a` 跳转壳**——模型引用给用户的"来源"根本点不开，按 URL 去重也随之失效。现在改为 **Bing RSS（主，真实 URL + 干净摘要）+ Bing HTML（备，用 `ck/a` 里的 `u=a1<base64url>` 还原真实地址，实测 6/6 可还原）+ 360 搜索（中文兜底，真实地址在 `data-mdurl`、摘要自带日期）** 三通路
- 🎯 **按质量而非条数升级**：Bing 常常一口气给满 8 条品牌官网/百科，条数够但"封号""风评"一个都没覆盖——现在会判定"没打到问题焦点"，自动换来源（360）与换关键词形态（缩写、求评价类加「知乎」），最后按焦点覆盖度重排，官网不再霸榜；同时丢弃跳转壳、过滤搜索引擎自家工具页、同域最多 2 条
- 📄 **新增「搜索即阅读」**：`web_search(..., read_top=N)` 会**并发**抓回前几条正文（单页 1500 字、整轮 4000 字上限），摘要普遍偏短时自动开启（且只读命中焦点的条目），一次调用就拿到能作答的材料，比"先搜再 fetch_url"少一轮往返
- 🛡 **失败与限流不再静默**：引擎被反爬拦截会被识别出来（不再当成"没有结果"）、10 分钟内冷却不再撞，结果里用 `degraded` 标注"本轮有来源被限流"；错误归一化成 `error` + `hint`（TLS / 超时 / 限流 / 代理），偶发错误退避重试一次
- 🔐 `fetch_url` 加固：SSRF 校验补上**域名解析**（公网域名指向 10.x 不再绕过）并**逐跳校验重定向**；非微信站点改用普通浏览器 UA；正文提取改为按文本密度挑选容器（与搜索阅读共用一套实现）
- 🐛 修掉一个会让 **fake-ip 代理用户全网抓不到页面**的问题：SSRF 判定不再用 `ipaddress.is_private`（它把 Clash fake-ip 使用的 198.18.0.0/15 也算内网），改为明确的内网网段；配了代理时不再拿本机 DNS 结果下结论
- 🧠 提示词：把「同一个问题最多试 2 次就停」改为「每轮必须换策略（最多 3 轮）」，并明确「结果全是官网 = 关键词不对」的判据——此前模型搜两次就放弃、转而只答校内内容
- ✅ 测试：新增/重写 35 个搜索与抓取用例（跳转壳还原、质量驱动降级、反爬冷却、阅读截断、DNS 与重定向 SSRF），全量 681 passed

## v0.24.3 (2026-09-21)
- 📚 新手指南「省力路线」改推荐 **DeepSeek Harness（DSH）**，下线 ZCode：安装流程改为 Node.js + `npx @deepseek-ai/dsh web`（自带浏览器界面），模型配置给出 DeepSeek 官方 Key 与致远一号（自定义 OpenAI 兼容提供方，`models.sjtu.edu.cn/api/v1`）两条路线，并注明 DSH 仍在开发者预览；新增提示框交代换用原因（9/18 ZCode 被曝仓库索引 / Repo Wiki 功能默认开启时上传仓库数据，官方致歉、称已修复并宣布开源整改）。`AGENTS.md`、AI 基础小词典与指南总览的 Coding Agent 示例同步改为 DSH
- 💬 飞书 Markdown 渲染稳定（#202）：模型混用 `__粗体__` 时不再被斜体规则吞掉（下划线分支补 lookbehind），`3 * 4` 这类乘法不再被误判为斜体；表格分隔行从「每格至少 3 个短横」放宽到 1 个（GFM 合法写法），`|--|--|` 不再把原始管道符漏进消息
- 🩹 日报不再把「(报告生成失败，请重试)」当正文推送（#201）：生成空回复时自动重试一次并记录 `finish_reason`，仍为空则改用真实数据的兜底摘要（错误占位符整条删除）；OpenAI 路径去掉会截断推理内容的 `max_tokens` 上限，Anthropic 路径改为拼接全部文本块

## v0.24.2 (2026-09-15)
- 🗓 日报触发时刻锚定北京时间：晨 8:00 / 午 12:00 / 晚 22:00 均按校园时钟，安装时自动换算为机器本地等价时刻（此前按本地时钟触发，海外同学本地早晨收到的"晨报"报道的是北京快结束的一天）；头行以北京日期为主、当地时刻作参考注记。**身处东八区之外的用户更新后需重装后台服务生效**：`sjtu-agent daemons uninstall && sjtu-agent install-daemons`
- 🔐 查课表/成绩的教务登录补上凭证兜底（#198）：此前只复用已保存的 jAccount 会话 cookie（新版 Chrome 的 cookie 加密会让浏览器导入失效，导致登录无从谈起），现直接用 `.env` 凭证走完整 Playwright jAccount 登录（验证码三级自动识别），成功后刷新保存的会话；报错文本同步给出真实修复步骤（`sjtu-agent login`）并明确禁止"接管用户浏览器会话"的不实建议
- 📖 两份 README 在 ⭐ 呼吁下新增 Star History 图表（暗色/亮色主题自适应）

## v0.24.1 (2026-09-14)
- 🩹 日报时区语义修正：身处非东八区（交换学期/海外/回国）时，推送时刻与「晨/午/晚」标签按用户本地时钟，头行日期也以用户当地为主、北京时间降为校园时钟参考（此前会读成"09/14 晚报 · 你当地 09/13"的日期错乱）；AI 数据上下文明确时区语境，「今日/明日」指北京校历日，课程/DDL 事实保持北京时间
- 🧹 日报行动建议不再"翻旧账"：过期 DDL 不再进入 AI 上下文（早已截止/被取消的作业被反复提起的根源）；用户记忆限近 7 天（此前纯语义检索会捞回几周前随口提过的内容）；提示词明确"只能引用上下文出现过的事物"
- 🛡 Bot 工具调用韧性：DeepSeek 流式偶发的工具参数截断/损坏不再杀死整轮（自动修复尾随垃圾、回填错误结果让模型重试，用户只会多等几秒）；异常中断留下的悬空调用每轮自动补齐——**出过错的会话下条消息自动痊愈**，不再需要 `/new`；飞书兜底报错附带异常类型与处理指引
- 🧰 修复 `telegram_bot` 与安装脚本测试中的无效转义序列（Python 3.12+ SyntaxWarning，#190 by @MBK-fr）

## v0.24.0 (2026-09-12)
- 🗓 新学期适配三连：课表周次锚点以官方校历为准自动对齐（config 里遗留的旧 `semester_start` 不再污染新学期）；教务会话有效性探测的学期码改为动态推导（旧版硬编码 2025 春，开学后可能误判过期反复触发重新登录）；调休补课日课表自动替换——按校方通知 9/20 执行第 3 周周五课表、10/10 执行第 4 周周二课表，查询时自动按映射过滤并标注（校历数据补元旦、期末周边界校准）
- 🤖 DeepSeek 官方预设切换 **deepseek-flash**（V4.1 Flash，原生视觉）：`DEEPSEEK_API_KEY` 环境变量兜底与 Web GUI 预设同步更新，四个 Bot 收图直接走主模型多模态识图（不再依赖独立视觉模型）；setup 向导视觉步骤注明可跳过；致远一号预设不变。一个 Key 同时搞定对话 + 识图，新用户配置少一步
- 🩹 飞书发图三连修（deepseek-flash 实测暴露）：图片消息不再被上下文折叠机制误删（图片改按固定视觉 token 计费、最近几轮永不折叠、历史预算 64K→256K 适配推理模型时代）；DeepSeek 流式偶发把工具调用以 DSML 标记混入正文——自动剥离并要求模型走结构化通道重发，用户不再看到原始标记；附件解析失败时模型不再漂移去"汇报配置状态"
- 📚 新手指南接入 Coding Agent 辅助安装路线：克隆仓库 → 打开 ZCode（智谱官方，含官网/安装/配置链接）→ 一句话说「按指南帮我装」，凭据自行输入的安全警告框；新增 **AGENTS.md** 让各类 Coding Agent 进仓库即获项目引导（含帮新手安装的行为规范）；排错手册补「发图说看不到问题 / DSML 标记」条目与"Bot 不热加载需重启"提醒

## v0.23.0 (2026-08-27)
- 📚 文档站新增「新手指南」专区：总览（这是什么 / 诚实边界 / 四种界面怎么选 / 上手三步走）、AI 使用基础（模型比喻、幻觉与核对、API Key 与保管纪律、Token 与上下文、提问技巧、安全三原则、小词典）、从零安装（致远一号 Key → Python/Git 三平台安装 → 一键脚本 → setup 向导逐项解释 → 第一次对话，含报错速查）、日常话术库（DDL/课表成绩/提醒/食堂/校园事务/新闻/LaTeX/附件等场景的可复制问法 + 斜杠命令速查）；README 中英快速开始与文档站首页/顶部导航同步接线
- 💬 CLI 就绪开场白不再只有一句"输入问题继续对话"：附三个可直接复制的示例问题（这周作业 / 明天课表 / 注册提醒）与话术库链接
- 🧙 setup 向导收尾引导：自动模式完成后打印「接下来三件事」——先问一句真实 DDL 验证数据链路 → 开 Web GUI 或 TUI → 绑定聊天软件；交互模式两条退出路径与「推荐事项」列表追加新手指南入口
- 🩺 缺配置处处指路：`check_setup` 各未配置项新增 `help_url` 字段（doctor 输出、CLI 冷启动与四个 Bot 的 LLM 引导都能直接引用）；`sjtu-agent doctor` 在 JSON 之后对必填缺失项追加两行中文提示与教程链接

## v0.22.0 (2026-08-25)
- 🌐 `web_search` 支持专用搜索代理：设置 `SJTU_WEB_SEARCH_PROXY` 后仅搜索引擎请求走代理，其余流量（校园 API/DDL/推送）完全直连，解决"服务器要代理才能搜索但又怕全局挂代理"的两难；服务器部署文档新增代理与搜索章节（HTTPS_PROXY / NO_PROXY / 专用代理三选一，含云厂商封机风险澄清）
- 🧠 Harness 强化「知识审慎」：系统提示词新增元认知块——知识库≠事实（可能过时/幻觉）、超出知识截止≠不存在（现实演进）、无法验证的机制/原理必须标注猜测、用户提供的领域/环境事实优先采信；新增提示词契约测试锁定护栏
- 💬 微信 iLink 协议修复：补齐参考实现的客户端身份头（`iLink-App-Id: bot`、版本 1.0.3 编码 65539），HTTP 与非零 `ret/errcode` 错误如实抛出（长轮询自动退避重试），`--test` 不再把会话错误误报为"token 有效 + 0 条消息"（PR #170）

## v0.21.4 (2026-08-21)
- 🩹 修复飞书媒体"路径越权"：`parse_local_file` / `read_assignment_file` 允许根目录新增 `DATA_DIR/feishu_media`（bot 下载的图片/文件在此，之前校验越权导致 bot 读不到通讯软件发来的图/附件）
- 📖 文档：README 中英 / `.env.example` 补充致远一号官方调用指南链接（claw.sjtu.edu.cn/guide/sjtu-api，调用名/限流/校园网要求以官方为准）

## v0.21.3 (2026-08-21)
- 🤖 致远一号默认模型按平台文档定回 **`deepseek-chat`**（DeepSeek V4 Flash 常规模式调用名；`deepseek-reasoner` 为思考模式、`minimax`、`qwen` 亦可用），`public-models` 作为团队受限（403）时的公共池备选；引导向导与示例配置同步列出真实调用名

## v0.21.2 (2026-08-21)
- 🚑 致远一号模型 ID 勘误（v0.21.1 引发 403 hotfix）：`models.sjtu.edu.cn` 的 API 只允许访问模型 **`public-models`**，`deepseek-v4-flash` 仅是其产品展示名——zhiyuan 预设/默认模型改回 `public-models`，DeepSeek 官方预设与通用兜底改为 `deepseek-chat`

## v0.21.1 (2026-08-21)
- 🤖 默认模型切换：`deepseek-v4-flash` 取代已弃用的 `deepseek-chat`（Zhiyuan/DeepSeek 预设、CLI/Web/各 bot 的默认模型、setup 向导提示与文档同步更新）
  - ⚠️ 勘误：致远一号 API 的模型 ID 实为 `public-models`（`deepseek-v4-flash` 会 403），见 v0.21.2 热修
- 🖥 WebUI 修复：聊天客户端与 CLI 对齐——只有 `.env` 的 API Key（如 `ZHIYUAN_API_KEY`）、没有 `agent_config.json` 时，按 provider 预设补默认 `base_url` / `model`，不再默认走 api.openai.com（校园外"WebUI timed out、CLI 正常"的根因）

## v0.21.0 (2026-08-20)
- 📘 服务器部署文档补全：定时推送（remind-check 守护进程）生效链路与排查 + 工作区（`SJTU_AGENT_HOME` / `SJTU_HOMEWORK_DIR`）跨机器三种方案（SSHFS / rsync / 各自独立）（issue #149-3、#149-6）
- 🩹 附件解析失败如实透出 + 防幻觉：失败上下文携带真实原因，并明确禁止模型编造"权限不足/白名单/沙箱限制"等不实说法（issue #149-4）
- 🖼️ 飞书富文本（post）支持：一段文字 + 若干图片组合为多模态输入（有视觉模型时）、无图富文本按普通文本处理；解析 text/a/at/img 元素并保留标题（issue #149-1）
- 🔌 MCP 修复：runner 工具列表真正聚合 MCP 服务器动态工具（之前 add_mcp_server 写入的配置永远不会下发到模型）；单个 server 连接/发现超时产出可调用状态工具（不再拖死整轮）；`add_mcp_server` 返回带正确配置路径 + 依赖 venv + `daemons restart` 指引（issue #149-2）
- 🔄 新增 `sjtu-agent daemons restart`：一键重启后台服务（停止 → 按安装清单参数重建并启动），支持 `--services` 子集选择（issue #149-5）

## v0.20.1 (2026-08-20)
- ⏱️ 飞书 Bot 请求超时阈值放宽并可配置：`feishu_capture_timeout`（默认 600 秒，设 0 不限时，与 Telegram 等端一致）替代旧的固定 120 秒；等待期间每 `feishu_progress_interval` 秒（默认 120 秒，最多 3 条）发送"仍在处理中"心跳
- 🔌 超时后自动做 15 秒 API 健康探测，区分"密钥失效/服务不可用"与"模型只是慢/任务复杂"，并给出对应处理建议；日志记录超时任务的实际完成耗时，便于判断阈值是否需要继续调大
- 🧵 修复超时竞态：单轮对话改在消息快照上执行，成功才原子提交；残留线程无法再污染会话历史，也不会补发"迟到回复"造成一条消息两条回复

## v0.20.0 (2026-08-16)
- 🧠 智商优化：新会话不再自动 check_setup 抢戏，直接回答首轮问题；启动开场按校历自适应（寒暑假不推荐在校事务，并提示距开学天数）
- 🔍 新增 `web_search`：Bing + DuckDuckGo 双引擎合并，自动生成缩写 / 全称查询变体；未知名词 / 黑话 / 时效性信息必须先搜索
- 🐙 新增 `github_repo_search`：GitHub REST API 仓库搜索；Agent 认识自己的仓库（kuan-er/sjtu-agent）与作者
- 🖥 TUI 修复：流式输出时可上翻历史；每轮结束后重建完整会话历史，不再只剩当前回复
- 📝 README 全面重写：按场景组织能力、界面与配置，作为项目主牌面

## v0.19.0 (2026-08-16)
- 🃏 TUI 结构化命令卡片：`/eat` `/news` `/hw` `/template` 等命令结果按 `{view, text, data}` 渲染成终端 Markdown 卡片，历史会话同样生效
- ⌨️ TUI 会话管理快捷键：`ctrl+r` 重命名、`ctrl+d` 删除（二次确认），删除后自动切换/新建会话
- 📎 TUI 附件上传：`/attach <本地路径>` 把文件复制进 `web_attachments/` 白名单目录，`/attach` 查看暂存、`/attach clear` 清空；原始路径不进入 Agent
- 🧵 附件预解析移出 UI 线程：视觉模型 / OCR 解析期间 TUI 保持响应，解析完成后自动发送
- 🧩 抽取 `web/attachment_context.py`：Web GUI 与 TUI 共用附件预解析与上下文注入逻辑

## v0.18.0 (2026-08-16)
- 🖥 新增 Textual TUI：`sjtu-agent tui` 全屏终端聊天界面（需 `pip install -e ".[tui]"`；未安装时给出提示）
- 🔄 TUI 与 Web GUI 共用同一 SQLite 会话存储和 SSE 引擎，会话 / 消息 / 命令结果实时同步
- ⌨️ TUI 支持：会话列表、Markdown 流式消息、`/` 命令补全面板、危险工具 approve/deny、`ctrl+x` 停止、`ctrl+n` 新会话
- 🛡 TUI 稳定性：Textual 8.x 异步 API 适配、80ms 流式节流、UI worker 防闪退、未捕获异常落盘 `logs/tui_error.log`
- 🧪 CI 测试矩阵安装 `[tui]`，Textual headless UI 测试（布局 / 流式 / 命令补全 / 压力）随 PR 执行

## v0.17.0 (2026-08-16)
- 🧭 斜杠命令统一为共享执行层：新增 `sjtu_agent.commands`（元数据 / dispatch / homework / news / dining / template），飞书 Bot 与 WebUI 共用同一份 `/hw` `/news*` `/eat` `/template` 逻辑；飞书文本输出保持不变
- 🖥 WebUI 命令体验：输入框上方快捷 chips（作业 / 新闻 / 食堂 / 模板 / DDL / 配置）、`/` 命令补全面板（↑↓ 选择、Enter 填入、Esc 关闭）
- ⚡ 新增 `POST /api/command`：命令经共享层执行并通过 SSE 推送 `command_start` / `command_progress` / `command_result`，进度与结果写入 Web 会话
- 🃏 命令结果结构化：服务端返回 `{view, text, data}`，WebUI 按视图渲染卡片（食堂推荐 / 新闻条目 / 作业列表 / LaTeX 模板 / 新闻偏好），Markdown 兜底；刷新页面后卡片仍可渲染
- 🧠 内部配套：`NewsAggregator.run_structured` 输出结构化新闻条目；`homework_agent.fetch_homework_list` 输出结构化作业列表
- 📝 文档清理：README 中英文同步 Web GUI 能力说明，设计文档标注归档状态

## v0.16.0 (2026-08-15)
- 🔧 水源自动登录修复：jAccount 落在 Welcome 首页/登录框未加载时清 cookie 重新发起 SSO；`_fill_jaccount` 对缺失登录框给出明确错误而不是干等 30 秒
- 🍪 新增 `save_shuiyuan_cookie` 工具：自动登录失败时可直接粘贴浏览器 Cookie 恢复水源会话
- 🔑 恢复 User API Key 授权流程：`start_shuiyuan_api_key` 生成授权链接并复用 client_id，`submit_shuiyuan_api_key` 解密校验 payload 后保存
- 🖥 Web GUI 改为视口内固定布局：消息区独立滚动，输入框始终可见，切换会话无需回到页面顶部
- 📎 附件可随文字一起发送或单独发送；待发送附件可预览/取消；上传后后端预解析内容并注入对话上下文
- 🧠 Web 附件解析复用飞书链路：图片先视觉模型、再 OCR，其他文件走 parse_file，不再让主模型重复询问安装 OCR
- 🔐 `parse_local_file` / `read_assignment_file` 白名单增加 Web GUI 上传目录（`web_attachments/`），仍拒绝读取运行时目录凭据文件

## v0.15.1 (2026-08-15)
- 🖥 GUI 细节修复：新会话按首条消息自动命名、收紧主页行距、legacy 页增加返回入口
- 💧 修正水源状态提示：session cookie 已足够搜索/读帖，不再提示需要 User API Key

## v0.15.0 (2026-08-15)
- 🖥 Web GUI Phase 3：附件上传/下载/图片与 PDF 预览、危险工具审批、Canvas/水源结果卡片、会话搜索、复制按钮、流式中断历史回写

## v0.14.0 (2026-08-15)
- 🖥 Web GUI Phase 2：Markdown / 代码高亮 / KaTeX、工具卡片耗时与状态、停止生成、主题与强调色、移动端会话抽屉

## v0.13.0 (2026-08-15)
- 🖥 Web GUI Phase 1：新增 React 多会话界面（会话列表 / 新建 / 重命名 / 删除 / 清空），SQLite 持久化消息；旧版配置页保留在 `/legacy`

## v0.12.0 (2026-08-15)
- 🌐 新增 `sjtu-agent web-proxy`：生成 Nginx / Caddy HTTPS 反向代理配置（SSE 长连接参数、HTTP→HTTPS 跳转）
- ⏳ 配置归档增加过期与校验策略：默认 24 小时过期、SHA-256 校验、拒绝过期归档（`--allow-expired` 放宽）
- 🚀 新增 GitHub Actions 自动发布：推送 `v*` tag 后自动测试、构建 wheel/sdist、从 CHANGELOG 生成 Release Notes 并创建 Release
- 📝 优化 README：目录、常用命令速查、远程 Web UI 与归档安全说明

## v0.11.2 (2026-08-15)
- 📦 新增 `sjtu-agent export-config / import-config`：核心凭据打包迁移、SSH 管道直传、可选 PBKDF2+ Fernet 加密、导入前自动备份
- 🗂 `export-config` / `import-config` 新增 `--state-file`，可按需选择 reminders / user_profile / dining_history 状态文件
- 📚 新增 VitePress 文档站（GitHub Pages `/docs/`），与项目展示页一起自动构建部署

## v0.11.1 (2026-08-15)
- 🧬 水源 Playwright 登录复用持久化浏览器 profile（`shuiyuan_browser_profile/`），降低 jAccount 风控概率；异地登录二次验证时给出更明确的处理提示

## v0.11.0 (2026-08-15)
- 🔁 后台服务安装清单（`.daemon_manifest.json`）：安装脚本 / `sjtu-agent update` 自动恢复此前安装的 Task Scheduler / psmux / launchd / systemd 服务，重装后无需手动重新配置
- 🧰 新增 `sjtu-agent daemons status / uninstall / resync`
- 🌐 Web UI 新增 `--host`（服务器可监听 0.0.0.0）
- 🖥 `install-daemons` / setup 新增 `--no-browser`；未安装 `web` 服务时不再等待或尝试打开浏览器
- 🐧 systemd 补齐 `web`、`news-digest`、`aihot-push` 服务，并修正早报/午报时间
- 💧 水源授权优先复用仍有效的 session cookie，登录后校验当前用户，减少异地登录触发
- 📚 新增排错手册、服务器部署指南和 GitHub Issue 模板

## v0.10.0 (2026-08-09)
- ⚡ **uv 迁移**：install 脚本换 uv，安装时间从分钟级降到 ~30 秒（数量级提升）
- 📦 **依赖按需拆分**：移除死依赖（browser-use / langchain-openai）；语义记忆（chromadb）改为可选 extra `[memory]`
- 🧭 **setup 向导打磨**：必填/可选分组、完成清单、确定性 y/N 决策
- 🔧 Python 版本要求 3.10 → 3.11（browser-use 依赖约束）

## v0.9.0 (2026-08-08)
- 🏗 **Agent 核心架构升级**（Prompt/Context/Harness/Loop 四层工程）：
  - ⚡ 稳定 system 前缀：动态时间/记忆移出前缀 → 用户消息，命中 DeepSeek 前缀缓存（成本大降）
  - 📊 上下文质量：tool 结果清理 + 64K 质量预算折叠带摘要（抗腐烂、防"念旧账"也防"健忘"）
  - 🧩 Prompt 审计：SYSTEM_PROMPT 模块化拆分，-20% 体积；近期更新 / Bot 配置引导移出前缀（按需工具读取）
  - 🛡 Harness：工具参数 schema 校验 + `execute_python` 危险操作拦截 + 工具调用日志（可观测）
  - 🔁 Loop：工具循环迭代预算（8 轮收敛）+ 网络重试上限（2 次）
  - 🔧 飞书斜杠命令重构：注册表化、修复 `/news`、统一错误处理、集中帮助文案

## v0.8.0 (2026-08-07)
- 🧠 bot 记忆开机自动分析：user-profile 首次会话后台 LLM 重分析，画像注入 system prompt（#113 #4）
- 🌐 画像在飞书/微信/QQ 跨平台积累（之前只在 telegram）
- 💬 CLI 终端对话注入用户画像
- 📊 日报安静日跳过：无 DDL/课表/新闻时不再推送模板噪音；空模块自动抑制
- ⏱ 电报/飞书日报跟随 bot 运行状态（心跳门控）
- 📦 配置示例补齐（.env / agent_config / config）
- 🧹 死代码与归档文档清理

## v0.7.7 (2026-08-07)
- 🖼 双模型视觉架构：主模型无视觉时，独立视觉模型（如 qwen-vl-max）识图，OCR 兜底
- 🔧 飞书识图链路修复（此前图片消息未接入视觉/OCR）

## v0.7.6 (2026-08-06)
- 🐛 修复 #113：`sjtu-agent update` 自动停止/重启后台服务；stdout=None 崩溃
- 📋 统一日志（RotatingFileHandler）

## v0.7.5 (2026-08-04)
- 🔄 Notifier 收尾（remind/care 共享推送）+ BotRunner 去重（bots/_core.py）+ 统一日志

## v0.7.4 (2026-08-03)
- ⚙️ ConfigStore 迁移（纯读点）+ run_tool 注册表化

## v0.7.3 (2026-08-03)
- 🔒 配置写回保护（读失败不再清空凭据）+ 静默异常可见化

## v0.7.2 (2026-08-03)
- 🐛 Web UI JS 修复 + 飞书回复加固

## v0.7.1 (2026-07-06)
- 🔢 DDL 智能分类 Rule 0（评分/问卷→通知）

## v0.7.0 (2026-07-05)
- 📊 日报定制化（对话调整模块显隐）+ DDL 智能分类
- 🤖 QQ Bot 接入（白名单管理）
- 🧩 MCP 与 Skills 扩展（自定义 MCP Server + prompt-only 技能）
- 📝 作业解题助手（/hw do 先思路后答案）
- 📊 MATLAB 作业图表
- 📧 邮件监控（飞书推送）
- 📄 LaTeX 模板（SJTU 毕业论文）
- ✅ CI 流水线（Python 3.11/3.13）
