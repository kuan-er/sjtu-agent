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

## 2. 准备配置（推荐：export-config / import-config）

在**你平时使用的电脑**上跑通一次 setup 后，用专用命令导出核心配置，而不是手工复制整个运行时目录：

```bash
# 本机：导出核心凭据（config.json / .env / agent_config.json）
sjtu-agent export-config --output sjtu-agent-config.tar.gz
```
- 需要同时迁移提醒/用户画像/食堂偏好时，导出端加 `--with-state`；
- 只迁移其中某个时可用 `--state-file reminders.json`（可重复，可选 `reminders.json` / `user_profile.json` / `dining_history.json`）。

接下来把核心配置导入服务器。

方案一：走中间文件

```bash
# 本机：安全传到服务器（scp 走 SSH 加密）
scp sjtu-agent-config.tar.gz user@server: # user 为服务器登录用户名，server 为服务器公网 ip，运行 curl ifconfig.me 即可

# 服务器：导入；同名文件会先自动备份
sjtu-agent import-config ~/sjtu-agent-config.tar.gz --yes
sjtu-agent doctor
```

方案二：直接 SSH 管道直传：

```bash
sjtu-agent export-config --output - | ssh user@server "sjtu-agent import-config - --yes"
```

- 导入端同样支持 `--state-file` 选择性导入。
- 归档默认 **24 小时过期**：`--expires-hours` 可调整，`--no-expiry` 关闭；
- 导入端默认拒绝过期归档，确认可信时加 `--allow-expired`。

特别提示：归档文件本身包含明文凭据，请**只在 SSH/scp 中传输，用后删除**；需要落到共享磁盘时使用 `--encrypt`（PBKDF2 + Fernet 加密，密码可设置 `SJTU_AGENT_CONFIG_PASSWORD`）。

默认路径：

| 平台 | 路径 |
| --- | --- |
| Linux 服务器 | `~/.local/share/sjtu-agent` |
| 自定义 | 设置 `SJTU_AGENT_HOME=/opt/sjtu-agent` |

导入的三个核心文件：

- `config.json`：平台 Token、Bot 凭据、Cookie
- `.env`：jAccount、LLM API Key
- `agent_config.json`：大模型配置

## 3. 无交互检查

```bash
sjtu-agent doctor
sjtu-agent daily-report --test
sjtu-agent feishu-bot --test   # 若配置了飞书
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

# 生成 Nginx 配置（含 certbot 路径、HTTP→HTTPS 跳转、SSE 参数）
sjtu-agent web-proxy --type nginx --domain sjtu-agent.example.com --output sjtu-agent.conf
sudo cp sjtu-agent.conf /etc/nginx/conf.d/sjtu-agent.conf
sudo certbot --nginx -d sjtu-agent.example.com
sudo nginx -t && sudo systemctl reload nginx

# 或 Caddy（自动 HTTPS）
sjtu-agent web-proxy --type caddy --domain sjtu-agent.example.com
```

**不要**把带明文 HTTP 的 `0.0.0.0:7860` 直接暴露到公网；Web UI 的访问令牌走 Cookie，明文传输会被窃取。

## 7. 已知限制与建议

- jAccount 风控：服务器 IP 登录校园平台可能触发异地登录。优先复用本机复制的 Cookie；失效后在本地刷新再同步，或手动在服务器上完成一次带二次验证的登录。
- Canvas / AI 好课 / phycai 的自动登录依赖 Playwright Chromium；无桌面 Linux 也能 headless 运行，但要先装系统依赖（`playwright install --with-deps chromium`）。
- 微信 Bot 建议保持本地运行；如必须上服务器，先本地扫码保存 token，再复制配置并启动。
- `SJTU_AGENT_HOME` 可把数据目录放到独立磁盘；设置后所有 CLI 和 systemd 服务必须能看到同一个值（systemd 用户环境可用 `systemctl --user set-environment SJTU_AGENT_HOME=/opt/sjtu-agent`）。
