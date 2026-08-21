# Changelog

本文件记录各版本的用户可见变化。Agent 通过 `get_recent_updates` 读取（问「最近更新了什么」时），不写入 system prompt。

## Unreleased
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
