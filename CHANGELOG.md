# Changelog

本文件记录各版本的用户可见变化。Agent 通过 `get_recent_updates` 读取（问「最近更新了什么」时），不写入 system prompt。

## Unreleased
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
