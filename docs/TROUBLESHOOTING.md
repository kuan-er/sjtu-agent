# SJTU Agent 排错手册

这是一份通用排错手册。如果这里没有解决你的问题，请按仓库中的 Issue 模板提交求助；如果你解决了新问题，欢迎直接提 PR 把经验补进来。

> 安全提醒：日志和截图不要包含 API Key、jAccount 密码、Token、Cookie 等敏感信息。

## 0. 先做三件事

```bash
sjtu-agent doctor          # 检查运行时路径和配置状态
sjtu-agent --version       # 确认版本
sjtu-agent daemons status  # 检查后台服务（可选，见下文）
```

`doctor` 会输出运行时数据目录。日志统一位于：

| 平台 | 日志目录 |
| --- | --- |
| macOS | `~/Library/Application Support/sjtu-agent/logs/` |
| Linux | `~/.local/share/sjtu-agent/logs/` |
| Windows | `%LOCALAPPDATA%\sjtu-agent\sjtu-agent\logs\`（以 `sjtu-agent doctor` 输出为准） |

没有 `sjtu-agent` 命令时，先看 [安装 / PATH 问题](#安装--path-问题)。

---

## 1. 安装 / PATH 问题

### 装完提示 `sjtu-agent` 不是可识别的命令

- 关闭并重新打开终端（PATH 更新不会进入已打开的终端）。
- 或直接用绝对路径运行：

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m sjtu_agent
```

```bash
# macOS / Linux
.venv/bin/python -m sjtu_agent
```

### 安装慢 / 依赖失败

- 安装脚本默认使用 `uv`。Windows 可先 `winget install astral-sh.uv`；macOS / Linux 可 `curl -LsSf https://astral.sh/uv/install.sh | sh`。
- 网络不稳时 Chromium 容易失败，可以跳过后补装：

```bash
bash install/install.sh --skip-playwright
python -m playwright install chromium
```

### 更新后模块报错 `ModuleNotFoundError`

`sjtu-agent update` 会自动停止并恢复此前安装的后台服务。如果仍报错，运行：

```bash
sjtu-agent update --skip-git   # 只重装包
sjtu-agent daemons resync      # 按清单恢复后台服务
```

---

## 2. Windows 后台服务（Task Scheduler / psmux）

### 为什么重装后要重新配置后台服务？

Windows 任务计划里保存的是**绝对路径**：

```text
<项目目录>\.venv\Scripts\pythonw.exe -m sjtu_agent <服务名>
```

重新 clone、移动项目目录、重建 `.venv` 后，旧任务指向的路径会失效，因此需要重新注册。

新版项目会把安装记录写到运行时数据目录的 `.daemon_manifest.json`。重新运行 `install.ps1` 或 `sjtu-agent update` 时会自动执行 `daemons resync`，无需再手动 `install-daemons`。手动触发：

```powershell
sjtu-agent daemons resync
```

### 查看 / 卸载后台服务

```powershell
sjtu-agent daemons status
sjtu-agent daemons status --services feishu-bot daily-report
sjtu-agent daemons uninstall
sjtu-agent daemons uninstall --services feishu-bot
```

### Task Scheduler 任务存在但 Bot 不运行

1. 确认任务没有指向旧路径：
   ```powershell
   schtasks /Query /TN SJTUAgent-FeishuBot /V /FO LIST
   ```
2. 看日志：`%LOCALAPPDATA%\sjtu-agent\sjtu-agent\logs\feishu_bot.task.log`（以 `sjtu-agent doctor` 输出为准）。
3. 路径变了直接重新安装：
   ```powershell
   sjtu-agent install-daemons --services feishu-bot
   ```

### psmux 会话丢失

psmux 会话依赖 psmux 服务端进程存活；机器重启后 psmux 不保证自动恢复所有会话。重新执行：

```powershell
winget install psmux
sjtu-agent install-daemons --backend psmux --services feishu-bot
```

### 为什么 install-daemons 会删除未选择的其他服务？

`install-daemons --services ...` 的语义是“让后台服务集合等于你指定的集合”。想保留全部就直接 `sjtu-agent install-daemons`；只想调整一个服务时，使用完整集合或 `daemons status` 确认清单。

---

## 3. macOS / Linux 后台服务

### macOS launchd

```bash
sjtu-agent install-daemons
sjtu-agent daemons status
sjtu-agent daemons uninstall
```

plist 文件在 `~/Library/LaunchAgents/`，日志在运行时数据目录 `logs/`。

### Linux systemd

```bash
systemctl --user status 'sjtu-agent-*'
journalctl --user -u sjtu-agent-feishu-bot -n 100
```

**如果机器重启后 Bot 没有自动启动**：用户级 systemd 服务默认随登录会话结束而停止，需要启用 linger：

```bash
loginctl enable-linger "$USER"
```

完整服务器部署步骤见 [docs/SERVER_DEPLOYMENT.md](SERVER_DEPLOYMENT.md)。

---

## 4. 水源社区授权

### 现状：优先用 session cookie，不再强求 User API Key

当前版本（v0.10.0+）的“配置水源”已经改为：用 Playwright 登录水源 → 校验登录态 → 保存 `shuiyuan_cookies`。新闻聚合和水源搜索会自动使用 cookie。

旧仓库中的 `get_shiyuan_api.py` 是早期 User API Key 方案的参考实现，**没有接入主流程**。所以现在不需要再纠结“User API Key 授权窗口弹不出来”了。

### 反复报异地登录 / 二次验证

常见原因：

- jAccount 把无头浏览器 / 服务器 IP 判定为异地登录，触发交我办 / 邮箱 / 手机验证。
- 项目会先复用已保存的 `shuiyuan_cookies`，只有在失效时才重新登录；如果你每次都被要求重新登录，说明 cookie 没有保存成功或被风控拦截。
- Playwright 登录现在会复用运行时数据目录下的 `shuiyuan_browser_profile/`（持久化浏览器 profile，保留 cookie、localStorage 和浏览器指纹），比每次新建无痕窗口更不容易触发风控。

建议按顺序尝试：

1. 先在本机浏览器登录一次 [水源社区](https://shuiyuan.sjtu.edu.cn)。
2. 对 Agent 说「配置水源」，让它先复用已有 session；不要从服务器 IP 首次登录。
3. 本机自动化失败时，手动导出 cookie（见下节）。
4. 服务器部署时，优先把本机已经配好的 `config.json` 复制到服务器，而不是让服务器去登录 jAccount。
5. 若怀疑 profile 已损坏或想重置浏览器指纹，可删除 `sjtu-agent doctor` 显示的 `shuiyuan_profile_dir` 后重试。

### 手动导出水源 cookie（兜底方案）

1. 用 Chrome / Edge 登录 `shuiyuan.sjtu.edu.cn`。
2. 打开 DevTools（F12）→ Network → 刷新页面 → 点任意 `shuiyuan.sjtu.edu.cn` 请求 → Headers → Request Headers → 复制整个 `Cookie:` 值。
3. 填入运行时 `config.json`：

```json
{
  "shuiyuan_cookies": {
    "_forum_session": "从 Cookie 中复制 _forum_session= 后面的值",
    "_t": "从 Cookie 中复制 _t= 后面的值"
  }
}
```

也可以直接保存一个 `shuiyuan_cookies` 对象，字段名与 cookie 名保持一致。之后搜索和新闻聚合会自动携带。

### 如何验证 cookie 是否有效

```bash
curl -s -H "Cookie: _forum_session=...; _t=..." \
  https://shuiyuan.sjtu.edu.cn/session/current.json
```

返回 JSON 中有 `current_user` 即有效；`"current_user": null` 表示已失效。

---

## 5. jAccount 验证码 / 二次验证

- 项目会自动尝试图形验证码识别（极客协会 API → Claude 视觉 → 手动输入）。
- 如果出现“交我办 / 邮箱 / 手机”三选一，脚本无法自动完成，需要在浏览器手动登录一次。
- 服务器上首次登录 jAccount 很容易触发异地风控，建议采用“本机配置 → 复制运行时数据目录到服务器”的方式。

---

## 6. Playwright Chromium

```bash
python -m playwright install chromium
sjtu-agent doctor
```

Windows 若下载超时，可以设置代理后重试，或从安装脚本加 `-SkipPlaywright` 跳过。

---

## 7. 飞书 Bot

飞书专属排查见 [docs/feishu-bot-troubleshooting.md](feishu-bot-troubleshooting.md)。

## 8. 服务器部署常见问题

### 手动安装 `pip install -e .` 报错

报错中包含：

```bash
ERROR: Could not find a version that satisfies the requirement setuptools>=69 (from versions: none)
```

这种情况通常是 Python 版本问题，先运行 `python --version`，若 python 版本过低（<=3.7）则需升级：

```bash
sudo apt update
sudo apt install python3.14
```

若版本较新，则可能是服务器提供商的 `pip` 镜像老旧，可使用清华镜像源替代安装：

```bash
pip install -e /home/ubuntu/sjtu-agent -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 传递依赖版本冲突

例如：OCR 插件中的依赖 `paddlex` 需要 `pyyaml==6.0.2`，而 `kubernetes` 要求 `pyyaml>=6.0.3`。

运行：

```bash
python -c "import kubernetes; print('kubernetes OK')"
python -c "import paddlex; print('paddlex OK')"
```
若分别输出 `kubernetes OK` 和 `paddlex OK`，则说明实际依赖可用，无需在意报错。

### 运行 `pip` 安装报错

报错如下：

```bash
error: externally-managed-environment
× This environment is externally managed
╰─> To install Python packages system-wide, try apt install
python3-xyz, where xyz is the package you are trying to
install.
If you wish to install a non-Debian-packaged Python package,
create a virtual environment using python3 -m venv path/to/venv.
Then use path/to/venv/bin/python and path/to/venv/bin/pip. Make
sure you have python3-full installed.
If you wish to install a non-Debian packaged Python application,
it may be easiest to use pipx install xyz, which will manage a
virtual environment for you. Make sure you have pipx installed.
See /usr/share/doc/python3.14/README.venv for more information.
note: If you believe this is a mistake, please contact your Python installation or OS distribution provider. You can override this, at the risk of breaking your Python installation or OS, by passing --break-system-packages.
hint: See PEP 668 for the detailed specification.
```

先运行：

```bash
which pip
which python
```

终端会输出两个路径。观察路径中是否存在 `.venv` 字样。

若返回类似于：

```bash
$ which pip
/usr/bin/pip # 没有 .venv
$ which python
/home/ubuntu/sjtu-agent/.venv/bin/python # 有 .venv
```

则说明 venv 创建不完整，运行： `python3 -m venv --upgrade-deps .venv` 使得 venv 自带最新 pip，再运行 `which` 指令检查 `pip` 是否位于虚拟环境中。

---

## 9. 其他常见问题

### 配置到底存在哪里？

见 `sjtu-agent doctor` 输出的路径。默认：

| 平台 | 运行时数据目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\sjtu-agent\sjtu-agent`（以 `sjtu-agent doctor` 输出为准） |
| macOS | `~/Library/Application Support/sjtu-agent` |
| Linux | `~/.local/share/sjtu-agent` |

核心文件：`config.json`（平台凭据）、`.env`（jAccount / API Key）、`agent_config.json`（LLM 配置）。

### 重装后配置还在吗？

运行时数据目录独立于代码仓库，普通重装不会删除。只有手动删除该目录或更换用户才会丢配置。项目目录移动后，Windows 后台服务需要 `sjtu-agent daemons resync`（安装脚本会自动做）。

### 怎么把本机配置搬到服务器？

```bash
# 本机
sjtu-agent export-config --output sjtu-agent-config.tar.gz

# 服务器
sjtu-agent import-config sjtu-agent-config.tar.gz --yes
```

推荐直接走 SSH 管道：`sjtu-agent export-config --output - | ssh server "sjtu-agent import-config - --yes"`。归档默认 24 小时过期；导入前会备份同名文件到运行时目录 `backups/`。远程 Web UI 的 HTTPS 反代配置用 `sjtu-agent web-proxy --domain <域名>` 生成。详见 [SERVER_DEPLOYMENT.md](SERVER_DEPLOYMENT.md)。

### 仍然无法解决？

到 [GitHub Issues](https://github.com/kuan-er/sjtu-agent/issues) 用对应模板提交，附上脱敏后的 `sjtu-agent doctor` 输出和 `logs/` 中相关日志。
