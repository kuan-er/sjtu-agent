# AGENTS.md

本文件供各类 Coding Agent（DeepSeek Harness / DSH、Claude Code、Codex、Cursor 等）在本仓库工作时读取。人类开发者请看 [README](README.md) 与 [CONTRIBUTING](CONTRIBUTING.md)。

## 项目引导

- 架构、开发与测试约定见 [CLAUDE.md](CLAUDE.md)——本文件与其保持同源，冲突时以 CLAUDE.md 为准。
- 测试：`pytest tests/ -q`（全量）或 `pytest tests/test_xxx.py -q`（单文件）；改完代码先跑测试。
- Web GUI 源码在 `webui/src/`，改后需 `npm run build:webui`；文档站改动用 `npm run docs:build` 验证。

## 帮助新手安装时

大量新同学会借助 Coding Agent 完成首次安装。引导他们时：

1. 按 [docs/guide/install.md](docs/guide/install.md) 的流程走（含 setup 向导每步的含义与推荐项）；
2. **敏感凭据（jAccount 密码、API Key）让用户在向导提示时自行输入**，不要要求用户把密码/Key 粘贴到对话里——聊天内容会留在会话记录；
3. 用户贴日志求助时，提醒先删掉其中的 Key 再贴；
4. 遇到查无此问题的新报错，引导用户到 GitHub 提 issue（附 `sjtu-agent doctor` 输出，脱敏后）。

## 安全红线（与 CONTRIBUTING 一致）

- 凭据只存本地运行时文件，任何时候不打印完整 Key / 密码；
- 新增文件读取注意路径白名单；新增网络请求注意 SSRF 校验；
- 提交前跑 `pytest tests/ -q`，全绿再交。
