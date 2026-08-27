# SJTU Agent 文档

SJTU Agent 是面向上海交通大学学生的校园助手，提供终端对话、Textual TUI、本地 Web GUI、飞书 / Telegram / 微信 / QQ Bot、DDL 聚合、日报推送、校园新闻、食堂推荐、作业助手和 MCP Server。

## 快速导航

**第一次接触 AI 工具？** 从[新手指南](guide/index.md)开始：[AI 使用基础](guide/ai-basics.md) → [从零安装](guide/install.md) → [日常话术库](guide/cookbook.md)

- [安装与快速开始](https://github.com/kuan-er/sjtu-agent#%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B)
- [Web GUI](WEB_GUI_REDESIGN.md)
- [Textual TUI](TUI.md)
- [服务器部署](SERVER_DEPLOYMENT.md)
- [排错手册](TROUBLESHOOTING.md)
- [飞书 Bot 排错](feishu-bot-troubleshooting.md)

## 常用命令

```bash
sjtu-agent                  # 终端对话
sjtu-agent tui              # 全屏 Textual TUI（可选依赖 [tui]；/attach 可上传附件）
sjtu-agent doctor           # 检查配置和运行时路径
sjtu-agent update           # 一键更新
sjtu-agent install-daemons  # 安装后台服务
sjtu-agent web              # 本地 Web GUI（聊天、会话、附件、斜杠命令、配置）
```

本机配置迁移到服务器：

```bash
sjtu-agent export-config --output - | ssh user@server "sjtu-agent import-config - --yes"
```

## 项目入口

- [项目展示页](https://kuan-er.github.io/sjtu-agent)
- [GitHub 仓库](https://github.com/kuan-er/sjtu-agent)
