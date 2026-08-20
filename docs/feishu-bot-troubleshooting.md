# 飞书 Bot 常见问题排查

## Bot 无任何回复

按以下顺序逐项排查：

### 1. 检查凭据是否有效

```bash
sjtu-agent feishu-bot --test
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
sjtu-agent feishu-bot --test

# whoami 模式（回显 open_id）
sjtu-agent feishu-bot --whoami

# 检查 psmux 会话
psmux -L sjtu-agent ls
psmux -L sjtu-agent server-info

# 重启 bot（psmux 后端）
sjtu-agent install-daemons --backend psmux --services feishu-bot
```

## 长时间指令报"处理超时"

**症状**：输入需要 AI 处理较久的指令后，机器人回复"处理超时，请稍后重试"。

**原因**：飞书 bot 对单轮处理有一个整轮等待上限（旧版本固定 120 秒）。而引擎内部单次 LLM 请求的预算就是 180 秒、一轮最多可包含 8 次工具迭代加长时间工具（Canvas / Playwright / MATLAB 等），120 秒比引擎自身预算还小，正常但较慢的请求会被误杀。Telegram / WeChat / QQ 与网页版没有这个整轮硬超时（网页版还有流式 token 进度），只有飞书 bot 受影响。

**调节方式**（改配置即可，无需重启 bot，下一条消息生效）：

```json
{
  "feishu_capture_timeout": 600,
  "feishu_progress_interval": 120
}
```

- `feishu_capture_timeout`：单轮处理最大等待秒数，默认 `600`（10 分钟）；设 `0` 表示不限时（与 Telegram 等端行为一致）。
- `feishu_progress_interval`：等待期间发送"仍在处理中"心跳的间隔秒数，默认 `120`，最多发 3 条，避免刷屏。

**超时后的行为**：会先做一次 15 秒的 API 健康探测（`max_tokens=1` 的最小请求）来区分故障原因：

- 探测失败 → 提示"检测到 API 端点异常"，指向密钥失效 / 服务不可用 / 网络问题，请检查 `api_key`、`base_url`、模型名。
- 探测正常 → 提示"模型响应较慢或任务过于复杂"，建议拆小重试或调大 `feishu_capture_timeout`。

注意：超时**不会中断**已经在跑的旧任务（Python 无法安全强杀线程），但旧任务的结果会被丢弃、不再写入会话，也不会补发一条"迟到回复"，避免一条消息两条回复造成混乱；日志会记录 `超时任务在 N 秒后完成（结果已丢弃）`，可用于判断是否阈值仍然偏小。
