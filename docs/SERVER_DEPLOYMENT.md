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

## 6. 定时推送怎么生效（提醒 / 日报）

**"好不容易设置的提醒却不准时响" 通常不是功能问题，而是守护进程没装/没跑。**

推送链路是完整的：

```
聊天里让 Agent 设置提醒（`add_reminder` 工具，写入数据目录 `reminders.json`）
  → 写入数据目录 reminders.json
  → remind-check 守护进程（每分钟触发）
  → send_notification → 你配置的通知渠道（飞书 / Telegram 等）
```

设置提醒的入口（`scripts/remind_check.py` 读取 `reminders.json`，`reminders` 工具写它）与日报同源，因此只要 `remind-check` 服务在跑，你的用户级提醒就会**准时主动推送**，不需要等你来找 bot。

安装与验证：

```bash
# 安装（务必包含 remind-check；其余按需）
sjtu-agent install-daemons --no-browser --services remind-check feishu-bot daily-report

# 确认在跑
sjtu-agent daemons status
sjtu-agent remind-check --list          # 打印当前所有提醒与状态

# 改了提醒后想让服务立刻重载/重启
sjtu-agent daemons restart --services remind-check
```

推送渠道需要满足：

- **飞书**：`notify_channels` 含 `feishu`，且数据目录里已存 `feishu_open_id`（bot 收到过你的消息就会自动保存，见 config.json）。
- **Telegram**：`telegram_token` / `telegram_allowed_ids` 配置好，`notify_channels` 含 `telegram`，且已给 bot 发过消息以获得 chat id。

常见排查：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 设置了提醒但不响 | remind-check 服务没装/没跑 | 上面 install-daemons + daemons status |
| 渠道没收到但日志正常 | notify_channels 没含该渠道，或目标 id 未保存 | 检查 config.json 的 `notify_channels` 与 `feishu_open_id` / telegram chat |
| bot 回复"只能你来找我时顺便提醒" | **模型不知道你的部署情况，是错误断言**（功能本身存在） | 按本节安装 daemon；或运行 `sjtu-agent daemons status` 后确认可推送 |

> 注意：`remind-check` 每分钟轮询一次，秒级精度不保证；跨时区/夏令时按服务器本地时间。

## 7. 工作区：作业目录与数据目录（跨机器访问）

项目有两类"工作区"，都需要在服务器部署时想清楚放哪：

**① 数据目录**（`SJTU_AGENT_HOME`，默认 `~/.local/share/sjtu-agent`）

存放 `config.json` / `agent_config.json` / `.env`、`reminders.json`、`user_profile.json`、`web_sessions.sqlite3`、`logs/`、`feishu_media/` 等全部运行时状态。跨机迁移就用 `export-config` / `import-config`（见第 2 节）。要想把数据放独立磁盘：

```bash
export SJTU_AGENT_HOME=/opt/sjtu-agent
# systemd 用户环境也要能看到同一个值：
systemctl --user set-environment SJTU_AGENT_HOME=/opt/sjtu-agent
```

**② 作业 / 附件目录**（`SJTU_HOMEWORK_DIR`，默认 `数据目录/assignments`）

Agent 把作业文件、`/hw do` 等工作产物放在这里。设定：

```bash
export SJTU_HOMEWORK_DIR=/home/you/assignments
```

**服务器 + 本机共用一套工作区的三种方案**（按场景选）：

- **方案 A：服务器为主，本机远程挂载（推荐做作业）**
  服务器 `assignments/` 通过 SSHFS 挂到本机（macOS 需 `brew install macfuse`）：
  ```bash
  mkdir -p ~/assignments
  sshfs user@server:/home/you/assignments ~/assignments -o reconnect
  # 或用 rsync 双向同步（处理简单、无需常驻挂载）
  rsync -avz --delete user@server:/home/you/assignments/ ~/assignments/
  ```
  本机用 IDE 直接编辑，Agent 在服务器上读到的就是同一份文件。

- **方案 B：本机为主，服务器同步过去**
  本机是唯一工作副本，改完推送：
  ```bash
  rsync -avz --delete ~/assignments/ user@server:/home/you/assignments/
  ```
  适合本机离线编辑、偶尔让服务器跑重活（如编译/查 DDL）的场景。

- **方案 C：两边各自独立（最稳，但不同步）**
  谁加工就在谁的 `assignments/` 里跑，不跨机共享。适合 Agent 只在一边用的情况。

> 安全注意：把 `assignments/` 当普通文件处理即可，它包含作业/课程资料；但**不要把 `SJTU_AGENT_HOME`（尤其含密钥的 `config.json` / `.env`）rsync 到不信任机器或网盘**。跨机传配置用第 2 节的 `export-config`（支持加密，`--encrypt`）。

## 8. 远程 Web 配置页（可选）

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

## 9. 网络代理与搜索访问

**背景**：大陆机房（如腾讯云）直连 `bing.com` / `duckduckgo.com` 往往被限制或降级，`web_search` 结果差或超时。这里只解决"搜索需要代理"，**不需要也不会让整个服务长期挂代理**。

### 方案一：全局环境变量（最省事，配合 NO_PROXY 保校园直连）

`HTTPS_PROXY` / `HTTP_PROXY` 只会影响**设置了它们的那个进程**（不是全局、不是 VPN）。给 bot 服务设置后，代理只用于它发出去的外网请求；再配 `NO_PROXY` 保证校园站点永远直连（这是"防封号"的关键——**jAccount/校园账号流量绝不走第三方代理**）：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890   # 换成你代理软件监听的地址:端口
export HTTP_PROXY=http://127.0.0.1:7890
export NO_PROXY=*.sjtu.edu.cn,*.sjtu.edu.cn/*,localhost,127.0.0.1,*.qcloud.com
```

systemd 用户服务记得把环境变量写进 unit（`Environment=`）或用 `systemctl --user set-environment`；直接 psmux 跑的话在启动命令前 `export` 即可。

### 方案二：专用搜索代理（推荐，精准隔离）

只想让 `web_search` 走代理、其余流量（DDL/校园 API/日报/推送）完全直连时，设置**专用环境变量**即可，代码已支持（v0.21.4+ 主线）：

```bash
export SJTU_WEB_SEARCH_PROXY=http://127.0.0.1:7890
```

- 设置了它：只有 `web_search`（Bing/DuckDuckGo）的请求走该代理，**其他请求一个字节都不碰代理**；
- 没设置：行为与原来一致（尊重 `HTTPS_PROXY` 或直连）；
- 下载加速仍按你原来的习惯手动开/关代理，互不冲突。

### 常见疑惑

- **"长期挂代理会不会封号？"** 这里的"封号"通常指**云厂商停用服务器实例/账号**。触发点一般是：①实例**对外提供代理中转**（有人经你的服务器转发流量）；②无鉴权代理端口被公网扫描到；③持续大流量转发 / 异常境外流量。安全做法：
  - 代理**只当客户端用**（服务器自己连出去，不开放入站给任何人）；
  - 监听地址只绑 `127.0.0.1`（绝不绑 `0.0.0.0`），需要认证；
  - 不需要时就关掉。`SJTU_WEB_SEARCH_PROXY` 模式下搜索每次只产生几 KB 出站流量，与普通浏览无异，远低于风险阈值。
- **代理地址端口填什么？** 看你服务器上代理软件的监听地址。常见：Clash/V2Ray 类 `http://127.0.0.1:7890`、`socks5://127.0.0.1:1080`（HTTP(S)_PROXY 建议用 http:// 形式）。验证：`curl -x http://127.0.0.1:7890 -I https://www.bing.com`。
- **搜索还是差？** 挂上代理后仍差，多半是引擎对机房 IP 的降级；可让模型优先用 `search_campus` 查校内源（不需要代理）。

## 10. 已知限制与建议

- jAccount 风控：服务器 IP 登录校园平台可能触发异地登录。优先复用本机复制的 Cookie；失效后在本地刷新再同步，或手动在服务器上完成一次带二次验证的登录。
- Canvas / AI 好课 / phycai 的自动登录依赖 Playwright Chromium；无桌面 Linux 也能 headless 运行，但要先装系统依赖（`playwright install --with-deps chromium`）。
- 微信 Bot 建议保持本地运行；如必须上服务器，先本地扫码保存 token，再复制配置并启动。
- `SJTU_AGENT_HOME` 可把数据目录放到独立磁盘；设置后所有 CLI 和 systemd 服务必须能看到同一个值（systemd 用户环境可用 `systemctl --user set-environment SJTU_AGENT_HOME=/opt/sjtu-agent`）。
