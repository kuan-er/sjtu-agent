# MCP and Skill Support Update

## English

This branch now focuses only on generic MCP and prompt-only skill integration.

### Included

- Dynamic tool registry that merges built-in tools with enabled MCP tools.
- External MCP client support (`stdio`, `sse`, `streamable_http` / `http`).
- Prompt-only skill loading from `SKILL.md`.
- Unified integration across terminal chat, Web UI, Telegram bot, Feishu bot, and WeChat bot.
- Custom MCP registration via:
  - CLI: `sjtu-agent add-mcp-server ...`
  - Agent tool: `add_mcp_server` (with explicit confirmation flow).
- Custom skill workflows via:
  - CLI: `sjtu-agent add-skill`, `sjtu-agent list-skills`, `sjtu-agent manage-skill`
  - Agent tools: `add_skill`, `create_skill`, `list_skills`, `manage_skill`

### Removed From This Branch Scope

- All dedicated install/setup flows for specific third-party MCP repositories.
- All chat-trigger routes for dedicated third-party MCP installation.
- All CLI commands for dedicated third-party MCP installation.

## 中文

这个分支现在只保留通用 MCP 和 prompt-only skill 接入能力。

### 已包含

- 动态工具注册：合并内置工具与已启用 MCP 工具。
- 外部 MCP 客户端支持（`stdio`、`sse`、`streamable_http` / `http`）。
- 基于 `SKILL.md` 的 prompt-only skill 加载。
- 终端对话、Web UI、Telegram、飞书、微信五个入口统一接入。
- 自定义 MCP 注册方式：
  - CLI：`sjtu-agent add-mcp-server ...`
  - 对话工具：`add_mcp_server`（带外部命令/URL确认流程）。
- 自定义 skill 工作流：
  - CLI：`sjtu-agent add-skill`、`sjtu-agent list-skills`、`sjtu-agent manage-skill`
  - 对话工具：`add_skill`、`create_skill`、`list_skills`、`manage_skill`

### 已移出本分支范围

- 所有针对特定第三方 MCP 仓库的专用安装/配置流程。
- 所有针对特定第三方 MCP 安装的对话触发路径。
- 所有针对特定第三方 MCP 安装的 CLI 子命令。
