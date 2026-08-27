# 从零安装

面向第一次配置开发环境的同学，按顺序照抄命令即可。全程约 20–30 分钟（大部分时间在等下载）。遇到概念不懂时查 [AI 使用基础](./ai-basics.md)。

::: warning 开始之前你需要
1. 一台 **Windows / macOS / Linux** 电脑；
2. **Python 3.11 或更新版本**（下面第二步会装）；
3. **Git**（同上）;
4. 能登录 [jAccount](https://jaccount.sjtu.edu.cn)，访问致远一号和学校系统建议在**校园网环境下**；校外是否需要 VPN 以[官方说明](https://claw.sjtu.edu.cn/guide/sjtu-api/)为准。
:::

## 第一步：拿到致远一号 API Key

1. 打开 <https://zhiyuan.sjtu.edu.cn>，用 jAccount 登录；
2. 在站内找到 API Key 管理页面，创建一个新 Key（各人名字随意，比如 `sjtu-agent`）；
3. **立刻把 Key 复制保存好**。它相当于你的账户钥匙，别发给别人、别截图外传。

具体页面入口可能随平台改版变化，站内找不到就看官方调用指南：<https://claw.sjtu.edu.cn/guide/sjtu-api/>。

## 第二步：装基础环境（Python + Git）

先检查电脑里有没有：打开终端，输入

```bash
python --version   # Windows 若无输出试试: py -3 --version
git --version
```

版本号 ≥ 3.11 且 git 正常输出 → 跳过本步，直接去第三步。否则按系统装：

**Windows**

1. 到 <https://www.python.org/downloads/> 下载并运行 Python 安装器，**第一屏务必勾选 "Add python.exe to PATH"**（这是新手最常见的翻车点），再点 Install Now；
2. 到 <https://git-scm.com/download/win> 安装 Git，一路默认即可；
3. 重开一个 PowerShell 窗口（PATH 变更只对新窗口生效），再验证一次上面的版本命令。

**macOS**

```bash
# 装 Git（会顺带装编译工具）
xcode-select --install
```

系统自带的 python3 往往版本偏旧，建议到 <https://www.python.org/downloads/> 装最新版，或用 Homebrew：

```bash
brew install python@3.13 git
```

**Linux**（以 Debian/Ubuntu 为例）

```bash
sudo apt update && sudo apt install -y python3.12 python3-venv git
```

## 第三步：克隆仓库并一键安装

选你顺手的位置存放项目（下例放在用户主目录），然后运行安装脚本——它会自动创建虚拟环境、装依赖、下载浏览器内核，最后直接进入配置向导。

::: code-group

```powershell [Windows PowerShell]
git clone https://github.com/kuan-er/sjtu-agent.git
cd sjtu-agent
powershell -ExecutionPolicy Bypass -File .\install\install.ps1
```

```bash [macOS / Linux]
git clone https://github.com/kuan-er/sjtu-agent.git
cd sjtu-agent
bash install/install.sh
```

:::

> 💡 不想进配置向导可加参数 `--no-setup`（Windows 用 `-NoSetup`）；网速差可加 `--skip-playwright` 先跳过浏览器内核下载，代价是之后无法自动登录 jAccount 抓数据。

## 第四步：配置向导（setup 向导）

脚本结束后会自动进入 `sjtu-agent setup`。按提示逐项走：

| 步骤 | 会发生什么 | 新手建议 |
| --- | --- | --- |
| 1. 大模型 | 选「致远一号」，粘贴第一步的 Key，向导会当场测试连通性 | 直接选默认 |
| 2. 视觉模型（可选） | 配 `qwen-vl-max` 等，用于识图（看照片里的题目、解析扫描件） | 可跳过，不影响文字聊天 |
| 3. Playwright Chromium | 下载自动操作浏览器的内核，供自动登录 jAccount 拉课表/DDL 用 | 建议安装 |
| 4. jAccount 账号 | 填用户名（**不是学号**，通常是你登录 my.sjtu.edu.cn 的拼音名）和密码。只保存在本机 `.env` 文件里，用于自动 SSO | 建议填写；介意也可跳过，后续手动登录 |
| 5. MOOC 账号（可选） | 用于聚合中国大学 MOOC 的 DDL | 在用就填，不用就跳过 |
| 6. 后台服务（可选） | 注册定时任务（每日提醒检查等） | 先跳过，玩熟了再说 |

任何一步都可以求助或跳过，之后随时能重新跑 `sjtu-agent setup` 补配。

完成后验证一下状态：

```bash
sjtu-agent doctor   # 应显示大模型已配置 + 各项路径正常
```

## 第五步：第一次对话

```bash
sjtu-agent
```

进去先来一句「**你好，请介绍一下你能帮我做什么**」确认大脑在线，然后直接试真实功能（可能触发一次 jAccount 自动登录；弹出验证码时人工输入即可）：

```text
这周有什么作业要交？
明天上午有课吗？
现在去哪吃？
```

回答慢或者一直转圈？看看是不是不在校园网/代理环境导致连不上学校系统，具体见[排错手册](../TROUBLESHOOTING.md)。

## 第六步（可选）：换更好看的界面 / 绑定聊天软件

```bash
sjtu-agent web        # Web GUI，浏览器自动打开 http://127.0.0.1:7860
pip install -e ".[tui]" && sjtu-agent tui   # 全屏终端界面
```

想在飞书/微信/QQ 里随口使唤它，见 README 的[多平台 Bot](https://github.com/kuan-er/sjtu-agent#多平台-bot)章节；飞书详细排错在[这里](../feishu-bot-troubleshooting.md)。日常玩法直接翻[话术库](./cookbook.md)。

## 出问题了？

| 症状 | 处理 |
| --- | --- |
| `python 不是内部或外部命令` / 未找到 Python | 回第二步装 Python（Windows 别忘勾 Add to PATH），**重开终端**再试 |
| 版本低于 3.11 | 卸载旧的另装新版，或用安装参数 `--python` 指定新装的 Python |
| PowerShell 提示"禁止运行脚本" | 你应该是直接双击/右键运行的 ps1；请按第三步原样整条复制运行（命令里已带 `-ExecutionPolicy Bypass`） |
| 致远一号测试连接失败 | 核对 Key 是否复制完整；确认网络能通 `models.sjtu.edu.cn`；限流规则看[官方指南](https://claw.sjtu.edu.cn/guide/sjtu-api/) |
| 查 DDL/课表失败 | 多为 jAccount 登录问题（验证码/二次验证），见[排错手册](../TROUBLESHOOTING.md) |

都不行的话，带着 `sjtu-agent doctor` 的输出去 GitHub 提 issue（[提问模板](https://github.com/kuan-er/sjtu-agent/issues/new/choose)）；**贴日志前确认里面没有密码和 API Key**。
