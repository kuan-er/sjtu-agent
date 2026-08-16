# Textual TUI

`sjtu-agent tui` 是全屏终端聊天界面。它和 Web GUI 共用同一个 SQLite 会话库和 SSE 引擎，会话、消息、命令结果实时同步。

## 安装与启动

```bash
pip install -e ".[tui]"
sjtu-agent tui
```

未安装 Textual 时会打印安装提示，其他 CLI 功能不受影响。

## 快捷键

| 快捷键 | 功能 |
| --- | --- |
| `Enter` | 发送消息 |
| `Shift+Enter` | 输入多行 |
| `/` | 打开命令补全面板 |
| `Tab` / `Shift+Tab` | 切换命令候选 |
| `ctrl+n` | 新建会话 |
| `ctrl+r` | 重命名当前会话 |
| `ctrl+d` | 删除当前会话（二次确认） |
| `ctrl+l` | 聚焦输入框 |
| `ctrl+x` | 停止生成 |

## 斜杠命令

TUI 与 Web GUI 共用 `sjtu_agent.commands`：

- 可执行命令：`/hw` `/news` `/news_block` `/news_reset` `/eat` `/template`
- `/ddl`、`/help` 会翻译成自然语言走普通聊天
- 结构化命令结果（食堂 / 新闻 / 作业 / 模板）渲染成终端 Markdown 卡片

## 附件

```text
/attach <本地路径>    # 暂存文件
/attach               # 查看暂存附件
/attach clear         # 清空暂存
```

文件会复制到运行时目录 `web_attachments/`（与 Web GUI 同一白名单），发送下一条消息时后台预解析并注入上下文；原始路径不会交给 Agent。单文件上限 20MB。

## 会话同步

TUI 直接读写 `web_sessions.sqlite3`：

- Web GUI 里新建的会话会出现在 TUI 会话列表；
- TUI 里发送的消息 / 命令结果，刷新 Web GUI 即可看到；
- 每轮结束后 TUI 会从共享存储重建消息区，长对话不会丢历史。

## 故障排查

- TUI 闪退：运行时目录 `logs/tui_error.log` 会保存完整 traceback。
- 输出中无法上翻：确认已更新到 v0.20+；只有停留在底部时才自动跟随新 token。
- 附件解析较慢：解析在后台线程执行，界面应显示“正在解析附件”，不会卡死。
