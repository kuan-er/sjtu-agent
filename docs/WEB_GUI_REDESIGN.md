# Web GUI

SJTU Agent 的浏览器界面：多会话聊天 + 附件 + 危险工具审批 + 斜杠命令卡片。后端保持 Python stdlib（`http.server.ThreadingHTTPServer`），前端为 React，使用 esbuild 构建到 `sjtu_agent/web/static/`。

## 启动与访问

```bash
sjtu-agent web                          # http://127.0.0.1:7860
sjtu-agent web --host 0.0.0.0 --no-browser   # 服务器监听，配合 web-proxy 使用
```

首次打开页面时服务端自动下发 `sjtu_token` HttpOnly Cookie；后续所有 `/api/*` 请求按该 Cookie 鉴权。旧版配置页保留在 `/legacy`。

## 功能

| 能力 | 说明 |
|------|------|
| 多会话 | 会话 / 消息持久化到运行时目录 `web_sessions.sqlite3`；新建、重命名、清空、删除、搜索 |
| 流式聊天 | SSE 推送 `token` / `tool_start` / `tool_end` / `approval_required` / `error` |
| 富文本 | Markdown、KaTeX、代码高亮；工具调用折叠卡片（参数 / 结果 / 耗时） |
| 附件 | 上传图片 / PDF / Office 文件，后端预解析进上下文；图片与 PDF 预览、待发送管理 |
| 审批 | 危险工具（发送邮件、写配置、安装依赖等）通过 `approval_required` 事件要求浏览器确认 |
| 斜杠命令 | 快捷 chips、`/` 补全面板；`POST /api/command` 经共享 `sjtu_agent.commands` 执行 |
| 命令卡片 | 服务端返回结构化 `{view, text, data}`，按视图渲染食堂 / 新闻 / 作业 / 模板卡片；刷新后仍可渲染 |

## 斜杠命令

WebUI 的命令元数据来自 `GET /api/commands`，`exec: true` 的命令会走 `POST /api/command`，其余命令（`/ddl`、`/help`）翻译成自然语言走普通 `/api/chat`。

| 命令 | WebUI 展示 |
|------|-----------|
| `/hw`、`/hw due <N>`、`/hw past`、`/hw all` | 作业列表卡片，点「分析」填入 `/hw do <N>` |
| `/hw do <N>`、`/hw brief <N>` | Markdown 解答（当前无细粒度步骤流） |
| `/news` | 新闻条目卡片 + 分类屏蔽按钮 + 完整摘要折叠 |
| `/news_block <分类>`、`/news_reset` | 新闻偏好状态卡片 |
| `/eat [闵行|徐汇|张江]` | 食堂推荐卡片 + 校区切换按钮 |
| `/template` | 模板列表卡片，点「套用」填入命令 |
| `/template compile`、`clone`、`push` | 编译 / 克隆 / 推送状态卡片 |

命令执行的 SSE 事件：

- `command_start {name, raw}`
- `command_progress {stage, message}`
- `command_result {name, view, text, data}`
- `[DONE]`

结构化结果同时编码为 session 消息（`__SJTU_COMMAND_RESULT__` 前缀 JSON），因此刷新页面后卡片仍可渲染。

## API 概览

```
/                         React GUI；/legacy 为旧版配置页
/api/sessions             会话 CRUD
/api/sessions/{id}/messages
/api/chat                 SSE 聊天，支持 session_id
/api/command              斜杠命令 SSE
/api/commands             命令元数据
/api/commands/resolve     命令 → 自然语言提示
/api/attachments          附件上传 / 下载 / 预览
/api/approvals/{id}       危险工具审批
/api/config /api/status   配置与运行状态
```

## 架构

```
webui/src/main.jsx + style.css
  → esbuild（npm run build:webui）
  → sjtu_agent/web/static/app.js / app.css / index.html

sjtu_agent/web/server.py      纯标准库 HTTP server + SSE
sjtu_agent/web/session_store.py    SQLite 会话 / 消息
sjtu_agent/web/attachment_store.py 附件元数据与文件
sjtu_agent/commands/              共享命令层（元数据 / 执行 / 结构化结果）
```

## 维护约定

- 前端源码只改 `webui/src/`，然后运行 `npm run build:webui` 提交产物。
- 新命令结果优先返回结构化 `CommandResult`；没有专属卡片时 `view="markdown"` 兜底。
- 飞书只消费 `CommandResult.text`，WebUI 增加卡片时不得改变飞书文本。
