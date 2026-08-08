# Changelog

本文件记录各版本的用户可见变化。Agent 通过 `get_recent_updates` 读取（问「最近更新了什么」时），不写入 system prompt。

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
