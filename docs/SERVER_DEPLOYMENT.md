# 服务器部署指南（Linux systemd，轻量版）

> 目标：在一台不关机的 Linux 服务器上运行飞书 / Telegram Bot 和定时报告。
> 当前不推荐 Docker：本项目是单 Python 代码库，`uv + systemd` 更轻、更好排查。

## 0. 适用与不适用

**适合**：

- 飞书 Bot（WebSocket 长连接）
- Telegram Bot（长轮询）
- daily-report / remind-check / email-watcher / canvas-watcher / news-digest / aihot-push 等定时任务
- `sjtu-agent web --host 0.0.0.0` 远程配置页（需自行加 HTTPS 反代）

**暂时不适合 / 需要手工处理**：

- 微信 Bot：需要扫码登录，建议首次在本地完成，再复制配置到服务器
- 首次在服务器上登录 jAccount：容易触发异地 / 二次验证。建议**先在本地完成配置，再把运行时数据目录复制到服务器**
- QQ Bot 在服务器上通常可用，但同样建议先本地配置好凭据

## 1. 安装

Ubuntu / Debian 示例：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
curl -LsSf https://astral.sh/uv/install.sh | sh
# 重新登录，或 export PATH="$HOME/.local/bin:$PATH"

git clone https://github.com/kuan-er/sjtu-agent.git
cd sjtu-agent
bash install/install.sh --no-setup --skip-playwright
```

解释：

- `--no-setup`：服务器上不走交互向导，配置手工写入或从本机复制。
- `--skip-playwright`：暂时不需要自动登录；之后需要刷新 cookie 时再 `uv run playwright install chromium`（或在有桌面依赖的机器上安装）。

## 2. 准备配置（推荐：从本机复制）

在**你平时使用的电脑**上跑通一次 setup 后，把运行时数据目录整个复制到服务器：

```bash
# 本机（macOS 示例）
scp -r ~/Library/Application\ Support/sjtu-agent user@server:.sjtu-agent-local

# 服务器
mkdir -p ~/.local/share
mv ~/.sjtu-agent-local ~/.local/share/sjtu-agent
chmod 700 ~/.local/share/sjtu-agent
```

默认路径：

| 平台 | 路径 |
| --- | --- |
| Linux 服务器 | `~/.local/share/sjtu-agent` |
| 自定义 | 设置 `SJTU_AGENT_HOME=/opt/sjtu-agent` |

如果服务器和本机用同一套路径，`scp` 时注意不要覆盖服务器上已有的配置。也可以只复制三个核心文件：

- `config.json`：平台 Token、Bot 凭据、Cookie
- `.env`：jAccount、LLM API Key
- `agent_config.json`：大模型配置

## 3. 无交互检查

```bash
sjtu-agent doctor
sjtu-agent daily-report --test
sjtu-agent feishu-bot -- --test   # 若配置了飞书
```

配置缺失时再补；不建议在服务器直接跑完整交互 setup。

## 4. 安装 systemd 用户服务

```bash
# 关键：用户级服务在登出后继续运行
loginctl enable-linger "$USER"

# 安装需要的服务，而不是全部；--no-browser 避免服务器上等待/尝试打开浏览器
sjtu-agent install-daemons --no-browser --services feishu-bot telegram-bot daily-report remind-check news-digest

# 查看
sjtu-agent daemons status
systemctl --user status 'sjtu-agent-*'
```

服务定义在 `~/.config/systemd/user/sjtu-agent-*.service` 和 `*.timer`。日志：

```bash
journalctl --user -u sjtu-agent-feishu-bot -n 100 -f
tail -f ~/.local/share/sjtu-agent/logs/feishu_bot.systemd.log
```

## 5. 更新

```bash
sjtu-agent update
```

更新工具会读取后台服务清单，自动停止旧服务、更新代码、按原参数恢复。如果目录发生移动或手动重装：

```bash
sjtu-agent daemons resync
```

## 6. 远程 Web 配置页（可选）

Web UI 默认只监听 `127.0.0.1`。服务器上有两种安全打开方式：

### 方式 A：SSH 隧道（推荐）

```bash
ssh -L 7860:127.0.0.1:7860 user@server
sjtu-agent web --no-browser          # 在服务器执行
```

本机浏览器访问 `http://127.0.0.1:7860`。

### 方式 B：监听网卡 + HTTPS 反代

```bash
sjtu-agent web --host 0.0.0.0 --port 7860 --no-browser
```

然后用 Nginx / Caddy 反代到 `https://你的域名`。**不要**把带明文 HTTP 的 `0.0.0.0:7860` 直接暴露到公网；Web UI 的访问令牌走 Cookie，明文传输会被窃取。

## 7. 已知限制与建议

- jAccount 风控：服务器 IP 登录校园平台可能触发异地登录。优先复用本机复制的 Cookie；失效后在本地刷新再同步，或手动在服务器上完成一次带二次验证的登录。
- Canvas / AI 好课 / phycai 的自动登录依赖 Playwright Chromium；无桌面 Linux 也能 headless 运行，但要先装系统依赖（`playwright install --with-deps chromium`）。
- 微信 Bot 建议保持本地运行；如必须上服务器，先本地扫码保存 token，再复制配置并启动。
- `SJTU_AGENT_HOME` 可把数据目录放到独立磁盘；设置后所有 CLI 和 systemd 服务必须能看到同一个值（systemd 用户环境可用 `systemctl --user set-environment SJTU_AGENT_HOME=/opt/sjtu-agent`）。
