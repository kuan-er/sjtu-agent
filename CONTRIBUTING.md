# Contributing to SJTU Agent

感谢你的贡献兴趣。

---

## 开发环境

```bash
git clone https://github.com/kuan-er/sjtu-agent.git && cd sjtu-agent
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env   # 填入真实凭据
sjtu-agent setup        # 交互式配置向导
```

---

## 工作流程

### 外部贡献者

**所有改动必须走 PR 流程**，无论大小。

1. Fork 仓库到自己的 GitHub 账号
2. Clone 自己的 fork，添加上游 remote：
   ```bash
   git clone https://github.com/<your-username>/sjtu-agent.git
   cd sjtu-agent
   git remote add upstream https://github.com/kuan-er/sjtu-agent.git
   ```
3. 创建功能分支：`git checkout -b feature/my-feature`
4. 开发并确保测试通过：`pytest tests/ -q`
5. 推送到自己的 fork：`git push origin feature/my-feature`
6. 在 GitHub 上创建 Pull Request，目标分支：`kuan-er/sjtu-agent` 的 `main`
7. 等待维护者 review。CI 必须通过（Python 3.11 + 3.13）

### 内部维护者

- 小修小功能（<100 行、单文件）：可直接 commit 到 `main`
- 大功能 / 重构 / 多文件改动：走 PR 流程（从 fork 发起）

---

## Coding Agent 辅助开发

我们欢迎使用 **Coding Agent**（Claude Code、Codex、Cursor、DeepSeek Harness / DSH 等）辅助开发——项目自身的多数代码也是借助 Coding Agent 完成的。记住 **Agent = Model + Harness**：模型提供能力，Harness（提示词、工具、流程约束）决定可靠性，两者都值得认真打磨。只需注意以下三点：

1. **功能正确、测试通过**。让 Coding Agent 帮你跑 `pytest tests/ -q`，全部绿灯再提交。如果加了新功能，让 Coding Agent 帮你写测试。

2. **安全你自己把关**。Coding Agent 可能忽略路径穿越、SSRF、凭据泄露等问题。提交前快速自查：有没有新的 HTTP 请求没校验 URL？有没有新的文件路径没防穿越？有没有不小心打印了 token？

3. **PR 描述说清楚做了什么**。如果代码主要靠 Coding Agent 生成，在 PR 里标注即可——这不丢人，项目维护者自己也这么开发。

---

## PR 中不要包含的内容

以下内容不应出现在 PR 中，提交前请检查：

| 不应提交的内容 | 说明 | 处理方式 |
|--------------|------|---------|
| 大型设计文档（>500 行 md） | 如 Superpowers 生成的 spec、方案文档 | PR 正文附简短摘要，详细内容放 Issue 或外部链接 |
| 手动测试产物 | `test_*.py` 临时测试脚本、`_debug_*.py` | 确认功能后将测试迁移到 `tests/` 目录，删除临时脚本 |
| LaTeX 辅助文件 | `*.aux`、`*.out`、`*.log` | 已在 `.gitignore` |
| `.pyc`、`__pycache__/` | 编译产物 | 已在 `.gitignore` |
| 凭据文件 | `.env`、`config.json`、Tokens | 已在 `.gitignore`，永远不要提交 |
| 安全审计报告 | `docs/SECURITY_AUDIT.md` | 仅本地留存，已在 `.gitignore` |
| 大二进制文件 | 截图、PDF、数据 dump | 如确需提交图片，放 `docs/images/` 并控制尺寸 |

**原则**：PR diff 应该只包含与功能直接相关的源码和测试。文档、设计讨论、中间产物不要进仓库。

---

## Commit 规范

```
type: scope — 描述

类型：
  fix      修复 bug
  feat     新功能
  refactor 重构（无行为变更）
  docs     文档
  chore    构建/配置/依赖
  test     测试

示例：
  feat: /eat command — canteen crowd + personalized dining recommendations
  fix: email_watcher — decode body with declared charset
  refactor: extract Feishu bot rendering and conversation management
```

---

## 测试

```bash
pytest tests/ -q           # 全量
pytest tests/test_dining.py -q   # 单文件
pytest -k "test_name"      # 按名称筛选
```

所有 PR 通过 GitHub Actions CI 后才能合并。新功能应包含测试。

---

## 项目架构

详见 [CLAUDE.md](CLAUDE.md)。关键设计原则：

- **ConfigStore 单例** (`sjtu_agent/config.py`)：配置访问统一入口
- **原子写入** (`sjtu_agent/paths.py`)：`atomic_write_json()` 防崩溃丢数据
- **Agent 工具模式** (`sjtu_agent/agent/tools/`)：每个工具 = `tool_xxx` 函数 + `TOOLS_ENTRIES` 条目
- 新代码放 `sjtu_agent/` 下，不放 `scripts/` 下

---

## 安全

- 凭据不能出现在代码中。使用 `env` 或环境变量
- 外部 HTTP 请求必须有 URL 校验
- 文件路径操作必须有 `is_relative_to()` 校验
- 用户数据只能存储在本地，不上传远程

---

## 问题与讨论

- Bug / 功能请求：[GitHub Issues](https://github.com/kuan-er/sjtu-agent/issues)
- 标签：`bug`、`enhancement`、`question`、`good first issue`
- 不确定的事先开 Issue 讨论再动手
