<!--
SJTU Agent PR 模板。
填写时删除本注释和不需要的说明行；标题按 CONTRIBUTING 规范：
  fix: <scope> — <一句话描述>
  feat: <scope> — <一句话描述>
  docs / refactor / chore / test 同理。
-->

## 关联 Issue

<!-- 修复/关联哪个 issue：写 `Fixes #N`（合并后自动关闭）或 `Part of #N`；没有就写「无」。 -->

Fixes #N

## 变更类型

- [ ] feat（新功能）
- [ ] fix（修复 bug）
- [ ] refactor（重构，无行为变更）
- [ ] docs（文档）
- [ ] test（测试）
- [ ] chore / ci（构建、配置、CI）

## 背景 / 要解决的问题

<!-- 为什么需要这次改动？描述现象与影响（用户可见行为变化请写清楚）。 -->

## 改动内容

<!-- 逐条列出：改了什么、改在哪里（文件/模块级别即可）。 -->

- 

## 测试证据

<!-- 必须真实填写：跑过的命令 + 结果。新增功能请附新增测试。 -->
<!-- 例：`pytest tests/ -q` → `590 passed`；`pytest tests/test_dining.py -q` → `10 passed` -->

- [ ] `pytest tests/ -q` 通过

## 影响范围

<!-- 勾选涉及面；说明是否需要发版（新增特性/用户可见修复 → 建议下次发版包含）。 -->

- [ ] CLI / chat
- [ ] Web GUI
- [ ] TUI
- [ ] Bot（飞书 / Telegram / 微信 / QQ）
- [ ] 守护进程 / 调度（daemons / scheduler）
- [ ] 文档
- [ ] 其他：<!-- 说明 -->

## 提交前自检

<!-- 依据 CONTRIBUTING.md「PR 中不要包含的内容」，逐项确认。 -->

- [ ] diff 只包含与功能直接相关的源码和测试，无临时脚本 / 中间产物 / 大二进制
- [ ] 无凭据、Token、`.env`、`config.json` 内容；截图已脱敏
- [ ] 用户可见行为变化已补充 CHANGELOG（Unreleased 节）
- [ ] 若主要借助 Coding Agent（Claude Code / Codex / Cursor / DSH 等）生成，已在描述中标注

## 备注

<!-- 可选：AI 辅助说明、设计取舍、后续 TODO。 -->