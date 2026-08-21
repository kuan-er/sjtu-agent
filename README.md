# SJTU Agent

[![Test](https://github.com/kuan-er/sjtu-agent/actions/workflows/test.yml/badge.svg)](https://github.com/kuan-er/sjtu-agent/actions/workflows/test.yml)
[![Pages](https://github.com/kuan-er/sjtu-agent/actions/workflows/pages.yml/badge.svg)](https://github.com/kuan-er/sjtu-agent/actions/workflows/pages.yml)
[![Release](https://github.com/kuan-er/sjtu-agent/actions/workflows/release.yml/badge.svg)](https://github.com/kuan-er/sjtu-agent/actions/workflows/release.yml)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

> 面向上海交通大学学生的本地优先校园 AI 助手。
> 终端对话、Textual TUI、Web GUI、飞书 / Telegram / 微信 / QQ Bot，一个引擎全打通。

- **仓库**：https://github.com/kuan-er/sjtu-agent
- **文档站**：https://kuan-er.github.io/sjtu-agent/docs/
- **项目展示页**：https://kuan-er.github.io/sjtu-agent
- [English Version](README_EN.md)

如果这个项目对你有帮助，欢迎点一个 ⭐ Star！

---

## 目录

- [为什么是 SJTU Agent](#为什么是-sjtu-agent)
- [快速开始](#快速开始)
- [使用界面](#使用界面)
- [核心能力](#核心能力)
- [配置](#配置)
- [后台服务](#后台服务)
- [平台接入](#平台接入)
- [安全说明](#安全说明)
- [开发与测试](#开发与测试)
- [版本](#版本)

---

## 为什么是 SJTU Agent

| 痛点 | SJTU Agent 的解法 |
| --- | --- |
| 查 DDL 要开四五个网站 | `get_ddls` 一次聚合 Canvas / AI 好课 / MOOC |
| 查成绩、课表要反复登录 | 本地保存凭证，专用工具自动 SSO |
| 食堂该去哪吃 | 实时拥挤度 + 历史偏好 + 食堂百科综合推荐 |
| 作业不会做 | `/hw do` 下载题目 → Claude Code 分析 → 生成 PDF 解答 |
| 校园通知太分散 | 教务处 / 水源 / 交大新闻网 / Canvas 智能聚合 |
| 只想在聊天软件里用 | 飞书 / Telegram / 微信 / QQ Bot |
| 想要好看的图形界面 | Web GUI + 全屏 Textual TUI，会话互通 |

**本地优先**：凭证、Cookie、聊天记录都保存在你自己的设备上，不上传任何远程服务器。

---

## 快速开始

### macOS / Linux

```bash
git clone https://github.com/kuan-er/sjtu-agent.git
cd sjtu-agent
bash install/install.sh
```

### Windows PowerShell

```powershell
git clone https://github.com/kuan-er/sjtu-agent.git
cd sjtu-agent
powershell -ExecutionPolicy Bypass -File .\install\install.ps1
```

安装脚本会自动创建 `.venv`、安装依赖与 Playwright Chromium，然后进入 `sjtu-agent setup` 配置向导。

**安装选项**

```bash
bash install/install.sh --no-setup          # 只安装，不进配置向导
bash install/install.sh --skip-playwright   # 跳过 Chromium 下载
```

**手动安装**

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
sjtu-agent setup
```

### 配置大模型 API

推荐使用交大官方 [致远一号](https://zhiyuan.sjtu.edu.cn)（免费）。运行 `sjtu-agent setup` 自动配置，或在运行时目录 `.env` 中写入：

```bash
ZHIYUAN_API_KEY=你的APIKey
```

默认模型 `public-models`（致远一号 API 模型 ID）。DeepSeek 官方、OpenAI 等其他兼容接口可在 Web GUI 或 setup 中选择「自定义」。

### 配置视觉模型（可选）

主模型不支持视觉（如 `public-models`）时，可额外配置 `qwen-vl-max` 等视觉模型用于识图。`sjtu-agent setup` 会引导配置，详见 [agent_config.example.json](agent_config.example.json)。

---

## 使用界面

### 终端对话

```bash
sjtu-agent          # 交互式聊天
sjtu-agent doctor   # 查看版本、配置状态与运行时路径
sjtu-agent update   # 一键更新并恢复后台服务
```

### Textual TUI

```bash
pip install -e ".[tui]"
sjtu-agent tui
```

全屏终端聊天界面，与 Web GUI 共用会话存储：

- Markdown 流式消息、`/` 命令补全、结构化命令卡片
- `/attach <本地路径>` 上传图片 / PDF，后台解析不卡 UI
- `ctrl+n` 新会话、`ctrl+r` 重命名、`ctrl+d` 删除、`ctrl+x` 停止生成

### Web GUI

```bash
sjtu-agent web                          # 默认 http://127.0.0.1:7860
sjtu-agent web --host 0.0.0.0 --no-browser
```

多会话聊天、附件预览、危险工具审批、快捷命令 chips、结构化结果卡片。旧版配置页保留在 `/legacy`。

### 多平台 Bot

| 平台 | 启动命令 | 特点 |
| --- | --- | --- |
| 飞书 | `sjtu-agent feishu-bot` | WebSocket 长连接、斜杠命令、多会话、多模态 |
| Telegram | `sjtu-agent telegram-bot` | `/news` `/news_block` `/news_reset` 等命令 |
| 微信 | `sjtu-agent wechat-bot` | ilink 长轮询 |
| QQ | `sjtu-agent qq-bot` | 官方 botpy + 白名单管理 |

---

## 核心能力

### DDL 聚合

一次拉取 Canvas、AI 好课、中国大学 MOOC、phycai 的作业与实验 DDL，自动分类真实作业与课程通知，区分今日 / 周内 / 远期。

```bash
sjtu-agent ddl
sjtu-agent ddl --canvas-only
```

### 课表 / 成绩 / 校园事务

- 课表：`get_schedule` 查单日 / 单周，自动适配校历调休。
- 成绩：`query_grades` 直连教学信息服务网，返回课程、绩点、加权 GPA。
- 门户事务：`browse_mysjtu` 处理选课、缴费、校车预约、报修等交我办业务。

### 作业解题助手

```text
/hw                # 列出作业
/hw do 3           # 下载并分析第 3 个作业
/hw brief 3        # 只出摘要
/hw due 7          # 7 天内截止
/hw past           # 历史作业
```

Canvas 下载 → Claude Code 分析 → 生成 Markdown / LaTeX / PDF 解答；支持 MATLAB 图表和公式排版。飞书里回复「给我答案」获取完整解答。

### LaTeX 模板

内置 SJTU 本科毕业论文模板（源自 [SJTUTeX](https://github.com/sjtug/SJTUThesis)）。

```text
/template                     # 列出模板
/template bachelor-thesis     # 套用论文模板
/template compile             # xelatex 编译 PDF
/template clone <project-id>  # 从 Overleaf 克隆
/template push                # 推送回 Overleaf
```

Windows 需安装 MiKTeX 与 ctex 宏包：`winget install MiKTeX.MiKTeX`，`mpm --install ctex`。

### 校园新闻

聚合教务处、水源社区、交大新闻网、Canvas，关键词初筛 + LLM 精排 + 用户画像个性化。

```text
/news                      # 即时摘要
/news_block <分类>         # 屏蔽某类
/news_reset                # 重置画像
```

```bash
sjtu-agent news-digest --dry-run
sjtu-agent news-digest --no-llm
sjtu-agent install-daemons   # 注册每日定时推送
```

### 食堂推荐

`campuslife` 实时拥挤度 + 历史用餐偏好 + 食堂百科，支持模糊名称匹配。选择后告诉 Bot「我去 XX 吃了」即可学习偏好。

```text
/eat          # 闵行校区
/eat 徐汇
/eat 张江
```

### Canvas 课程与文件

- 浏览 / 下载课件、资料，追踪处理进度。
- 课程公告、quiz、待办监控，飞书 / Telegram / 系统通知推送。

```bash
sjtu-agent canvas-watcher --once --test
sjtu-agent install-daemons --services canvas-watcher
```

### 邮件与提醒

```bash
sjtu-agent email-watcher --once      # 检查新邮件并推送
sjtu-agent remind-check --list       # 查看提醒
```

对话中可直接说「帮我记一下明天 14:00 交实验报告」。

### AI 资讯

飞书 `/aihot` 获取每日 AI 圈精选，按模型 / 产品 / 行业 / 论文 / 技巧分类。

### MCP 与技能扩展

```bash
sjtu-agent add-mcp-server my-tools --transport stdio --command python --arg server.py
sjtu-agent add-skill my-skill --content-file SKILL.md
```

也可以直接对话：「添加一个 MCP 服务器」「创建一个技能」。

### 多模态解析

OCR、ASR、PDF 解析按需安装：

```bash
sjtu-agent install-parse-backends --backend pdf_ocr
sjtu-agent install-parse-backends --backend whisper
```

### 记忆（可选）

飞书 Bot 的跨会话语义记忆基于 ChromaDB。安装：`pip install -e ".[memory]"`，装好后自动初始化。

---

## 配置

### 运行时数据

所有配置与缓存存放在平台用户数据目录，首次运行自动迁移仓库根目录旧文件：

| 平台 | 路径 |
| --- | --- |
| macOS | `~/Library/Application Support/sjtu-agent` |
| Linux | `~/.local/share/sjtu-agent` |
| Windows | 以 `sjtu-agent doctor` 输出为准（通常 `%LOCALAPPDATA%\sjtu-agent\sjtu-agent`） |

核心文件：

- `config.json` — 平台 Token、Cookie、Bot 凭据
- `.env` — jAccount 账号、致远一号 API Key
- `agent_config.json` — LLM 提供商与模型

### 迁移配置

```bash
sjtu-agent export-config --output - | ssh user@server "sjtu-agent import-config - --yes"
```

归档默认 24 小时过期，可选 `--encrypt` 加密。

### 环境变量

| 变量 | 用途 |
| --- | --- |
| `ZHIYUAN_API_KEY` | 致远一号 API Key |
| `JACCOUNT_USERNAME` | jAccount 用户名 |
| `JACCOUNT_PASSWORD` | jAccount 密码 |
| `SJTU_AGENT_HOME` | 覆盖运行时数据目录 |
| `SJTU_HOMEWORK_DIR` | 作业文件目录 |
| `SJTU_PAPERS_DIR` | LaTeX 论文目录 |
| `MATLAB_PATH` | MATLAB 路径 |

---

## 后台服务

```bash
sjtu-agent install-daemons
sjtu-agent daemons status
sjtu-agent daemons uninstall
```

支持 macOS launchd、Linux systemd、Windows Task Scheduler / psmux。服务清单：

`web` · `daily-report` · `remind-check` · `canvas-watcher` · `news-digest` · `aihot-push` · 四个 Bot

安装记录保存在 `.daemon_manifest.json`，重装 / 移动目录 / 重建 `.venv` 后自动恢复。

---

## 平台接入

### 飞书 Bot（约 5 分钟）

1. 在 [open.feishu.cn](https://open.feishu.cn) 创建企业自建应用；
2. 添加机器人能力，申请 `im:message` / `im:message.p2p_msg:readonly` / `im:message:send_as_bot`；
3. 事件订阅切换为**长连接**，订阅 `im.message.receive_v1`；
4. 创建版本并申请发布；
5. Web GUI 飞书卡片填入 App ID / Secret，启动 Bot；
6. 给 Bot 发消息，终端会显示你的 `open_id`，回填白名单。

排错见 [docs/feishu-bot-troubleshooting.md](docs/feishu-bot-troubleshooting.md)。

### QQ Bot

[q.qq.com](https://q.qq.com/qqbot/openclaw/) 创建机器人 → 对话中让 Agent 调用 `setup_qq` → `sjtu-agent qq-bot`。

### 水源社区

对话中说「配置水源」即可授权。session cookie 方案优先；遇到二次验证见 [排错手册](docs/TROUBLESHOOTING.md)。

---

## 安全说明

- 凭据只存本地文件，不上传服务器；Unix/macOS 下敏感文件自动 `0o600`。
- Web GUI 访问令牌通过 HttpOnly Cookie 自动下发。
- `execute_python` 执行时自动剥离敏感环境变量，并拦截危险操作。
- 远程访问 Web GUI 时使用：

```bash
sjtu-agent web --host 0.0.0.0 --no-browser
sjtu-agent web-proxy --type nginx --domain sjtu-agent.example.com
```

---

## 开发与测试

```bash
pytest -q                    # 全量测试（当前 524+）
pytest tests/test_calendar.py # 单文件
npm run build:webui          # 构建 Web GUI
npm run docs:build           # 构建文档站
```

架构与重构状态见 [CLAUDE.md](CLAUDE.md)；历史设计文档归档在 [docs/](docs/)。

---

## 版本

当前版本：**v0.21.1**。发布历史见 [Releases](https://github.com/kuan-er/sjtu-agent/releases)。
