# 飞书 Bot 常见问题排查

## Bot 无任何回复

按以下顺序逐项排查：

### 1. 检查凭据是否有效

```bash
sjtu-agent feishu-bot -- --test
```

或手动验证：

```python
import requests, json
r = requests.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
    json={'app_id': 'cli_xxx', 'app_secret': 'xxx'}, timeout=10)
print(r.json())  # code=0 表示有效
```

**常见错误码**：
- `10014 app id not exists` → App ID 错误或应用已删除
- `10014 app secret invalid` → App Secret 错误
- `1000040345 app_id or app_secret is invalid` → WebSocket 连接时凭据无效

> 飞书开放平台可能会重置应用凭据。如果凭据突然失效，去 https://open.feishu.cn/app 重新获取。

### 2. 检查 Bot 进程是否存活

```bash
psmux -L sjtu-agent server-info   # psmux 后端
schtasks /Query /TN SJTUAgent-FeishuBot  # Task Scheduler 后端
```

如果进程反复崩溃（启动后几秒内退出），看下一步。

### 3. Windows GBK 终端 emoji 崩溃

**症状**：Bot 启动后立即崩溃，或 `sjtu-agent feishu-bot` 直接报错 `UnicodeEncodeError`

**原因**：Python 代码中的 `✅` `❌` `⚠` 等 emoji 字符在 Windows GBK 控制台打印时抛出 `UnicodeEncodeError`，导致进程在 WebSocket 连接建立前崩溃。

**修复**：将所有 `print()` 中的 emoji 替换为纯文本标记：
- `✅` → `[OK]`
- `❌` → `[X]`
- `⚠` → `[!]`
- `ℹ` → `[i]`

> `_reply_text()` 中发送给飞书 API 的消息不受影响（API 接受 UTF-8）。

### 4. 白名单被双 JSON 编码

**症状**：Bot 回复"你不在该机器人的允许列表中"，但 `config.json` 里确实有你的 open_id。

**检查**：

```python
import json
cfg = json.loads(open('config.json').read())
v = cfg.get('feishu_allowed_open_ids')
print(type(v).__name__)  # 应该是 list，如果是 str 就是 bug
```

**原因**：Web UI 或某些保存路径将 `feishu_allowed_open_ids` 数组双编码为 JSON 字符串（`"[\"ou_xxx\"]"` 而非 `["ou_xxx"]`）。`set("[\"ou_xxx\"]")` 会把每个字符当作一个 open_id，导致匹配失败。

**修复**：
1. 手动将 config.json 中的值改为真正的 JSON 数组
2. `feishu_bot.py` 已加入防御：如果值是字符串，自动 `json.loads()` 解析

### 5. 检查事件订阅配置

1. 打开 https://open.feishu.cn/app → 你的应用
2. 「事件与回调」→ 确认**使用长连接接收事件**（不是回调 URL）
3. 确认已订阅 `im.message.receive_v1` 事件
4. 确认应用已发布

### 6. 检查权限

飞书开放平台 → 你的应用 → 「权限管理」：
- `im:message` ✓
- `im:message.p2p_msg:readonly` ✓
- `im:message:send_as_bot` ✓

## 快速诊断命令

```powershell
# 凭据测试
sjtu-agent feishu-bot -- --test

# whoami 模式（回显 open_id）
sjtu-agent feishu-bot -- --whoami

# 检查 psmux 会话
psmux -L sjtu-agent ls
psmux -L sjtu-agent server-info

# 重启 bot（psmux 后端）
sjtu-agent install-daemons --backend psmux --services feishu-bot
```
