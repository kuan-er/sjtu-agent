# Web GUI 重设计（Phase 计划）

> 目标：在保持 Python stdlib 后端和现有 API 兼容的前提下，把单页配置 + 单会话聊天升级为现代多会话 Web GUI。UX 参考 `CoderPPX/sjtuclaw`，但不复制其代码（该仓库未提供 License）。

## 现状

- 前端：`sjtu_agent/web/static/index.html`，单文件 vanilla JS。
- 后端：`http.server.ThreadingHTTPServer`。
- 聊天：SSE（token / tool_start / tool_end / error），单会话全局内存，刷新丢失。
- 配置页：API / 凭据 / Telegram / 飞书 / 推送渠道已可用。

## 目标架构

```
browser
  ├─ /            新 React GUI（React + esbuild，构建到 static/）
  ├─ /legacy      旧版配置页（功能完整，兼容保留）
  └─ /api/*
        ├─ /api/sessions             会话 CRUD
        ├─ /api/sessions/{id}/messages
        ├─ /api/chat                 SSE，支持 session_id
        └─ 现有 /api/config, /api/status ...
```

会话持久化使用运行时数据目录下的 `web_sessions.sqlite3`（SQLite + WAL，每操作独立连接）。

## Phase 1（当前）

- [x] `SessionStore`：会话 / 消息 CRUD、清空、重命名、删除
- [x] `/api/sessions*` REST 接口
- [x] `/api/chat` 支持 `session_id`，user/assistant 消息落盘
- [x] React + esbuild 新前端：会话侧边栏、流式消息、工具卡片、旧版配置页入口
- [x] 旧版页面迁移到 `/legacy`，保持现有 API 兼容
- [x] CI 校验 `npm run build:webui` 产物与仓库一致

## Phase 2（当前）

- [x] Markdown / 代码高亮 / KaTeX 渲染
- [x] 工具卡片：耗时、状态、折叠
- [ ] 工具调用重试（留到 Phase 3）
- [x] 停止生成（客户端 abort + 服务端取消标记）
- [x] 主题 / 强调色持久化
- [x] 移动端会话抽屉

## Phase 3（当前）

- [x] 附件上传 / 下载 / 图片与 PDF 预览
- [x] 危险工具审批 UI（`approval_required` SSE 事件）
- [x] Canvas / 水源搜索结果卡片
- [x] 会话搜索、Markdown 复制按钮、流式中断历史回写
- [ ] 工具调用重试
- [ ] 多会话同时生成（当前为单流，但会话列表有运行状态标记）

## TUI（并行）

- 使用 Textual 新增 `sjtu-agent tui`
- 会话列表 + 消息流 + 工具执行进度面板 + `/` 命令补全
- 保留现有 Rich 终端对话作为轻量模式

## 兼容策略

- 旧版 `/legacy` 页面继续可用
- 现有 `/api/chat` 不带 `session_id` 时保持旧行为（全局内存会话）
- 配置写入路径不变
