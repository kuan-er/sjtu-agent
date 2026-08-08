# 本机安装优化设计（DEPLOYMENT）

> 目标：让同学**从 `git clone` 到能对话**的时间最短、摩擦最少。
> 当前主场景是**本机自托管**（学生自己的电脑），非服务器 24/7。

---

## 一、目标与度量

| 指标 | 现状（估） | 目标 |
|------|-----------|------|
| 安装时间（依赖解析+安装） | pip 全量装重依赖，分钟级～十分钟级 | 一分钟内（uv 解析+缓存） |
| 交互步骤数（setup 向导） | 多轮提问（jAccount → Canvas → MOOC + cookie 导入） | 核心必填 ≤ 3 步，其余可选 |
| Playwright Chromium | ~150MB 单独下载 | 保持自动，失败可跳过可重试 |

## 二、现状诊断（已对照代码核实）

**流程**（`install/install.sh` / `install.ps1`）：
```
创建 .venv → pip install -e .（全量依赖）→ playwright install chromium → 启动 setup 向导
```

**摩擦点**：

| # | 摩擦 | 来源 |
|---|------|------|
| 1 | **pip 安装慢** | requirements 全量装重依赖：`chromadb`（拉 onnx/torch 链）、`langchain-openai`、`browser-use`——`pip install -e .` 是最大时间成本 |
| 2 | **Playwright 下载大** | Chromium ~150MB，网络差时易失败 |
| 3 | **交互向导步骤多** | setup 问 jAccount / Canvas / MOOC / cookie，新手易卡 |
| 4 | **Windows 差异化** | install.ps1 与 install.sh 各维护一份，逻辑重复 |

## 三、方案（2026 调研落地）

### 主方案：uv 迁移

`uv` 是 2026 年 Python 依赖/环境管理的标准——并行解析、自带缓存、安装快一个数量级。用它替换 `venv + pip`：

- install.sh / install.ps1：`uv venv` + `uv sync`（或 `uv pip install -e .`）+ `uv run playwright install chromium`
- 未装 uv 时脚本先引导安装（`curl -LsSf https://astral.sh/uv/install.sh | sh` 或 `winget install uv`）
- 生成 `uv.lock`（可复现、更快 CI/安装）

> 风险低：uv 对 pip 依赖完全兼容；本地 `pip install -e .` 仍可用，uv 只是默认路径。

### 次方案：可选依赖拆分（extras）

重依赖只有部分功能用到。拆成 extras，基础安装只装核心（聊天 / DDL / 新闻 / 食堂 / 日报），重功能按需补装：

- 候选移出基础包：`chromadb`（语义记忆）、`langchain-openai`（若仅 MCP 用）、`browser-use`（浏览）、OCR/ASR 后端（已有 `install-parse-backends` 模式可沿用）
- 安装形态：`pip install -e ".[full]"` 全量 / `pip install -e .` 基础；或沿用"功能提示后按需装"
- **原则**：核心功能必须零额外步骤；重/可选功能明确标注"需要额外安装"

### 配套：向导简化

setup 已按"只问一项"推进。进一步：
- 必填（jAccount、LLM Key）与非必填（Canvas、MOOC、cookie）分组，非必填可跳过且后续随时补
- cookie 导入流程给更明确的引导（哪步失败该做什么）
- 安装完成后给出"下一步做什么"的清晰清单

### 非目标（附录：未来延伸）

服务器 24/7 部署（uv+systemd / Docker）不在当前范围。研究结论：Docker 对本项目（单 Python 代码库）偏重，uv+systemd 是更贴的轻量路径；真正的卡点是**头无化 setup + 登录/扫码服务器化**——留待资源充足时再做。

## 四、分阶段实施路线

| Phase | 内容 | 收益 | 风险 |
|-------|------|------|------|
| **1. uv 迁移** | install.sh/install.ps1 换 uv；生成 uv.lock；文档更新 | 🔴 安装时间数量级下降 | 低 |
| **2. extras 拆分** | 定义依赖边界，重依赖移入 extras，安装形态明确 | 🟡 基础安装轻量 | 中（边界要定准） |
| **3. 向导打磨** | 必填/可选分组 + cookie 引导 + 完成清单 | 🟡 交互摩擦降 | 低 |

每步独立验证（本地跑安装计时、新机器冒烟），可随时停下。

## 五、参考

- [uv 官方文档](https://docs.astral.sh/uv/)（安装/缓存/项目工作流）
- [Self-Hosted AI Agent 部署 2026 指南](https://www.hermify.io/en/blog/self-hosted-ai-agent-docker)（方案对比：systemd/Compose/uv）
- [ax — uv 免 Docker 单租户部署](https://github.com/narnia-sh/ax)（uv + systemd 模式参考）
- [Kubernetes vs systemd](https://gigagpu.com/kubernetes-vs-systemd-ai-inference-workloads/)（单服务器 overkill 的结论）
