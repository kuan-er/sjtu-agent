# SJTU Agent

[![Test](https://github.com/kuan-er/sjtu-agent/actions/workflows/test.yml/badge.svg)](https://github.com/kuan-er/sjtu-agent/actions/workflows/test.yml)
[![Pages](https://github.com/kuan-er/sjtu-agent/actions/workflows/pages.yml/badge.svg)](https://github.com/kuan-er/sjtu-agent/actions/workflows/pages.yml)
[![Release](https://github.com/kuan-er/sjtu-agent/actions/workflows/release.yml/badge.svg)](https://github.com/kuan-er/sjtu-agent/actions/workflows/release.yml)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.13-blue)](https://www.python.org/)

面向上海交通大学学生的校园助手，提供终端对话、飞书 / Telegram / 微信 / QQ Bot、DDL 聚合、日报推送和 MCP Server。

[English Version](README_EN.md) · [项目展示页](https://kuan-er.github.io/sjtu-agent) · [文档站](https://kuan-er.github.io/sjtu-agent/docs/) · [排错手册](docs/TROUBLESHOOTING.md) · [服务器部署](docs/SERVER_DEPLOYMENT.md)

如果这个项目对你有帮助，欢迎点一个 ⭐ Star！

## 目录

- [快速开始](#快速开始)
- [常用命令速查](#常用命令速查)
- [功能](#功能)
- [配置](#配置)
- [后台服务](#后台服务)
- [平台接入](#平台接入)
- [安全说明](#安全说明)

---

## 快速开始

```bash
# macOS / Linux
git clone https://github.com/kuan-er/sjtu-agent.git && cd sjtu-agent && bash install/install.sh

# Windows PowerShell
git clone https://github.com/kuan-er/sjtu-agent.git; cd sjtu-agent; powershell -ExecutionPolicy Bypass -File .\install\install.ps1
```

安装脚本自动创建 `.venv`、安装依赖和 Playwright Chromium，然后启动 `sjtu-agent setup`。setup 向导引导你配置大模型 API，依次保存校园平台凭据、自动创建 Canvas Token、从 Chrome 导入 Cookie。

**安装选项：**

```bash
bash install/install.sh --no-setup          # 只安装，不进入 setup
bash install/install.sh --skip-playwright   # 跳过 Chromium
```

**手动安装：**

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
sjtu-agent setup
```

### 配置大模型 API

推荐使用交大官方 [致远一号](https://zhiyuan.sjtu.edu.cn)（免费）。运行 `sjtu-agent setup` 自动配置，或手动在 `.env` 中写入：

```bash
ZHIYUAN_API_KEY=你的APIKey
```

默认模型 `deepseek-chat`（DeepSeek V3.2）。也可用 DeepSeek 官方、OpenAI 等其他兼容接口，在 Web 配置页选「自定义」填入即可。

### 配置视觉模型（可选，用于识图）

如果你的主模型不支持视觉输入（如 `deepseek-chat`），可单独配置一个视觉模型（如 `qwen-vl-max`），飞书收到图片时优先用它识图，OCR 兜底。**三种配置方式任选其一**：

1. **交互式**：运行 `sjtu-agent setup`，配完主模型后按提示配置视觉模型（API Key 输入**不回显**）。

2. **命令行**：
   ```bash
   sjtu-agent setup \
     --vision-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
     --vision-api-key 你的视觉模型Key \
     --vision-model qwen-vl-max \
     --vision-enabled
   ```

3. **手动编辑** `agent_config.json`：
   ```json
   "vision_model": {
     "enabled": true,
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "api_key": "你的视觉模型Key",
     "model": "qwen-vl-max"
   }
   ```

> 视觉模型仅用于识图（一次性调用），不参与对话历史。API Key 仅存本地，输入时不回显、不打印。完整配置模板见 [agent_config.example.json](agent_config.example.json)。

---

## 常用命令速查

| 命令 | 用途 |
| --- | --- |
| `sjtu-agent` | 终端对话 |
| `sjtu-agent doctor` | 检查配置、路径和依赖 |
| `sjtu-agent update` | 更新代码并自动恢复后台服务 |
| `sjtu-agent web` | 本地 Web 配置页（`--host 0.0.0.0` 供服务器监听） |
| `sjtu-agent web-proxy --domain <域名>` | 生成 Nginx / Caddy HTTPS 反代配置 |
| `sjtu-agent install-daemons` | 安装后台服务；`daemons status/uninstall/resync` 管理服务 |
| `sjtu-agent export-config / import-config` | 本机配置迁移到服务器（SSH 管道直传） |
| `sjtu-agent ddl` / `daily-report` / `news-digest` | DDL、日报、校园新闻 |
| `sjtu-agent feishu-bot` 等 | 启动对应平台 Bot |

---

## 功能

### 终端对话

```bash
sjtu-agent              # 交互式对话
sjtu-agent doctor       # 查看配置和运行时路径
sjtu-agent update       # 一键更新到最新版本
```

### 多平台 Bot

| 平台 | 启动命令 | 斜杠命令 |
| --- | --- | --- |
| 飞书 | `sjtu-agent feishu-bot` | `/hw` `/news` `/eat` `/aihot` `/template` `/list` `/new` `/switch` `/name` `/delete` `/history` `/news_block` `/news_reset` `/help` |
| Telegram | `sjtu-agent telegram-bot` | — |
| 微信 | `sjtu-agent wechat-bot` | — |
| QQ | `sjtu-agent qq-bot` | — |

飞书 Bot 基于 WebSocket 长连接，支持多会话、斜杠命令、多模态（图片/文件/音频）。详见 [平台接入](#平台接入)。

### DDL 聚合

一键拉取 Canvas、AI 好课、中国大学 MOOC、phycai 四个平台的作业 DDL，区分今日截止 / 周内截止 / 远期。

```bash
sjtu-agent ddl
sjtu-agent ddl --canvas-only
```

### 每日报告

自动生成晨间早报（今日课表 + DDL）、午间速报（下午课程）、晚间日报（明日课表 + AI 学习建议），通过飞书 / Telegram 推送。

```bash
sjtu-agent daily-report --test            # 预览
sjtu-agent daily-report --type morning    # 早报
sjtu-agent install-daemons                # 安装定时推送
```

### 作业助手

Canvas 集成 + Claude Code 引擎，`/hw do <序号>` 下载作业 → 分析思路 → 生成 PDF 解答。支持 MATLAB 图表和 LaTeX 排版。

```text
/hw                # 列出作业
/hw do 3           # 分析第 3 个作业
/hw due 7          # 7 天内截止
/hw past           # 历史作业
```

### LaTeX 模板

内置 SJTU 本科毕业论文模板（源自 [sjtug/SJTUThesis](https://github.com/sjtug/SJTUThesis)），支持飞书 Bot 内一键编译。

```text
/template                     # 列出可用模板
/template bachelor-thesis     # 套用论文模板
/template compile             # xelatex 编译 PDF
/template clone <project-id>  # 从 Overleaf 克隆
/template push                # 推送回 Overleaf
```

需要 MiKTeX（Windows: `winget install MiKTeX.MiKTeX`）并安装 ctex 宏包：`mpm --install ctex`。

### AI 资讯

飞书 Bot `/aihot` 命令获取每日 AI 圈精选新闻，按模型 / 产品 / 行业 / 论文 / 技巧分类。数据来源 [aihot.virxact.com](https://aihot.virxact.com)（MIT，无需 Key）。

```text
/aihot                       # 今日 AI 新闻
sjtu-agent aihot             # 终端推送
```

灵感来源：[KKKKhazix/khazix-skills](https://github.com/KKKKhazix/khazix-skills) 的 ai-hot 技能（MIT）。

### 校园新闻

智能新闻聚合——采集教务处、水源社区、交大新闻网、Canvas 四个源，两阶段排序（关键词初筛 + LLM 精排），按用户画像个性化推荐。飞书 Bot `/news` 即时获取，定时推送每天 10:00 自动发送。

```text
/news                         # 校园新闻摘要
/news_block <分类>            # 屏蔽某类新闻
/news_reset                   # 重置画像
```

```bash
sjtu-agent news-digest --dry-run   # 预览
sjtu-agent news-digest --no-llm    # 纯关键词排序
sjtu-agent install-daemons          # 自动注册 news-digest 每日定时任务
```

### 食堂推荐

基于 campuslife.sjtu.edu.cn 实时拥挤度 API + 历史用餐偏好学习，智能推荐最佳就餐地点。支持模糊名称匹配（如「三餐」「哈乐」）。

```text
/eat                    # 闵行校区推荐
/eat 徐汇               # 徐汇校区推荐
/eat 张江               # 张江校区推荐
```

选择食堂后告诉 Bot 「我去 XX 吃了」，自动记录偏好，下次推荐会更符合口味。

### Canvas 课程文件

浏览/下载 Canvas 课程文件（课件、资料），追踪已处理进度。

```text
「列出 Canvas 文件」「下载这个课件」「看看还有哪些没整理」
```

### Canvas 课程监控

定时检查 Canvas 课程公告、quiz、待办事项，通过飞书 / Telegram / 系统通知推送。

```bash
sjtu-agent canvas-watcher --once --test   # 预览
sjtu-agent canvas-watcher --once          # 推送一次
sjtu-agent install-daemons --services canvas-watcher
```

可在对话中让 Agent 配置监控范围：「只监控 ECE2300」「每 10 分钟查一次」。

### 邮件监控

检查 mail.sjtu.edu.cn 新邮件，通过飞书推送（纯通知，不发送/不删除/不修改）。

```bash
sjtu-agent email-watcher --once
sjtu-agent install-daemons --services email-watcher
```

### MCP 与技能扩展

加载外部 MCP Server 作为额外工具，或创建 prompt-only 技能扩展 Agent 能力。

```bash
sjtu-agent add-mcp-server my-tools --transport stdio --command python --arg server.py
sjtu-agent add-skill my-skill --content-file SKILL.md
```

也可在对话中让 Agent 操作：「添加一个 MCP 服务器」「创建一个技能」。

### 多模态解析

支持 OCR（图片文字提取）、ASR（语音转文字）、PDF 解析。可选安装：

```bash
sjtu-agent install-parse-backends --backend pdf_ocr
sjtu-agent install-parse-backends --backend whisper
```

### 记忆

飞书 Bot 基于 ChromaDB 实现跨会话语义记忆。对话结束后自动提取关键信息（课程、考试、学习偏好），下次对话时检索相关记忆注入上下文。**需要安装可选依赖** `pip install -e ".[memory]"`（按需所取，默认不装以保持安装轻量）；装好后无需配置，首次使用自动初始化。

---

## 配置

### 运行时数据

所有配置和缓存文件存储在平台用户数据目录，首次运行自动从项目根目录迁移旧文件：

| 平台 | 路径 |
|------|------|
| macOS | `~/Library/Application Support/sjtu-agent` |
| Linux | `~/.local/share/sjtu-agent` |
| Windows | 以 `sjtu-agent doctor` 输出为准（通常为 `%LOCALAPPDATA%\sjtu-agent\sjtu-agent`） |

三个核心文件：

- `config.json` — 平台 Token、Cookie、Bot 凭据
- `.env` — jAccount 账号密码、致远一号 API Key
- `agent_config.json` — 大模型配置（已有 `ZHIYUAN_API_KEY` 则不需要；模板见 [agent_config.example.json](agent_config.example.json)）

本机配置迁移到服务器：

```bash
sjtu-agent export-config --output - | ssh user@server "sjtu-agent import-config - --yes"
```

归档默认 **24 小时过期**（`--expires-hours` 调整，`--no-expiry` 关闭），导入端默认拒绝过期归档（`--allow-expired` 可放宽）；可选 `--encrypt` 加密。详见 [服务器部署](docs/SERVER_DEPLOYMENT.md)。

### 远程访问 Web UI

默认 `sjtu-agent web` 只监听 `127.0.0.1`。服务器上启用远程访问并生成 HTTPS 反代配置：

```bash
sjtu-agent web --host 0.0.0.0 --port 7860 --no-browser
sjtu-agent web-proxy --type nginx --domain sjtu-agent.example.com --output sjtu-agent.conf
# 或 --type caddy 直接放入 Caddyfile
```

### 安全说明

凭据（API Key、密码、Token）以明文存储在本地文件中。Web UI 需要 `?token=xxx` 访问令牌（首次启动打印在终端）。`execute_python` 工具执行时会自动剥离敏感环境变量。建议保持运行时数据目录为私有（macOS/Linux 已自动设为 `0o600`）。

### 环境变量

| 变量 | 用途 |
|------|------|
| `ZHIYUAN_API_KEY` | 致远一号 LLM API Key |
| `JACCOUNT_USERNAME` | jAccount 学号 |
| `JACCOUNT_PASSWORD` | jAccount 密码 |
| `SJTU_AGENT_HOME` | 覆盖默认运行时数据目录 |
| `SJTU_HOMEWORK_DIR` | 作业文件存放目录 |
| `SJTU_PAPERS_DIR` | LaTeX 论文模板目标目录 |
| `MATLAB_PATH` | MATLAB 可执行文件路径 |

---

## 后台服务

### macOS (launchd)

```bash
sjtu-agent install-daemons                    # 安装全部服务
sjtu-agent install-daemons --services daily-report remind-check
```

服务列表：`web` `daily-report` `remind-check` `canvas-watcher` `news-digest` `aihot-push` `telegram-bot` `qq-bot` `feishu-bot` `wechat-bot`

### Windows

**Task Scheduler**（默认，适合定时任务）：

```powershell
sjtu-agent install-daemons
sjtu-agent daemons status      # 查看已安装服务
sjtu-agent daemons uninstall   # 卸载全部服务
```

**psmux**（适合常驻进程，无弹窗）：

```powershell
winget install psmux
sjtu-agent install-daemons --backend psmux --services feishu-bot telegram-bot
```

飞书 Bot 还提供桌面 GUI 启动器：双击 `install\launch-feishu.bat` 即可。

> 后台服务安装记录保存在运行时数据目录的 `.daemon_manifest.json`。重新安装、移动目录或重建 `.venv` 后，安装脚本和 `sjtu-agent update` 会自动执行 `sjtu-agent daemons resync` 恢复服务，无需手动重新配置。

### Linux (systemd)

```bash
loginctl enable-linger "$USER"   # 登出后继续运行（一次性设置）
sjtu-agent install-daemons       # 服务器无桌面环境时加 --no-browser
```

服务器 24/7 部署完整步骤见 [docs/SERVER_DEPLOYMENT.md](docs/SERVER_DEPLOYMENT.md)。

---

## 平台接入

### 飞书 Bot

1. 在 [open.feishu.cn](https://open.feishu.cn) 创建企业自建应用
2. 添加「机器人」能力，申请权限 `im:message` `im:message.p2p_msg:readonly` `im:message:send_as_bot`
3. 事件订阅切到**长连接**，添加 `im.message.receive_v1` 事件
4. **「版本管理与发布」→ 创建版本 → 申请发布**（否则搜不到 bot）
5. 在 WebUI 飞书卡片中填入 App ID / Secret，启动 Bot
6. 在飞书里搜应用名称发消息，终端日志会显示你的 `open_id`，回填到白名单

详细故障排查见 [docs/feishu-bot-troubleshooting.md](docs/feishu-bot-troubleshooting.md)。

### QQ Bot

登录 [q.qq.com](https://q.qq.com/qqbot/openclaw/) 创建机器人获取 AppID / AppSecret → 对话中让 Agent 调用 `setup_qq` → `sjtu-agent qq-bot` 启动。

### 水源社区

在对话中对 Agent 说「配置水源」即可授权。当前版本使用 session cookie 方案（旧 User API Key 流程已废弃）；如遇异地登录 / 二次验证，或需要手动导出 cookie，见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

---

## 健壮性

飞书 Bot 启动时自检凭据、ChromaDB、Agent API 连通性；每 30s 写心跳文件供启动器监控（>90s 无心跳 → 无响应）；退出时自动清理线程池和临时文件。

## 版本

当前版本：**v0.13.0**。发布历史见 [Releases](https://github.com/kuan-er/sjtu-agent/releases)。
