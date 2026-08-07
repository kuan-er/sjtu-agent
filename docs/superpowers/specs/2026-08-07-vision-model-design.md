# Design: 双模型架构 — 独立视觉模型（识图）

- **Date**: 2026-08-07
- **Status**: Draft
- **Author**: Azzygoatcoder

## Context

Issue #113 的识图问题暴露了现状缺陷：主模型（如 deepseek-v4-pro）不支持视觉输入时，飞书 bot 无法识图。此前发现 OCR 路径被 `return` 跳过（已修复 fallthrough），但 OCR 准确度有限。

用户提出**双模型思路**（类似本地 Claude Code 的纯文本逻辑模型 + 独立视觉模型配置）：主模型处理逻辑/文本，独立配置一个视觉模型（如 Qwen-vl）专门识图。

**决策**（已与用户确认）：
- 配置入口：**terminal setup 向导为主**（用户实际配置路径），**不做 web UI**
- 识图优先级：**视觉模型 > OCR > 提示**
- 开关：`vision_model.enabled` 字段
- **API key 安全**：输入用 getpass 不回显，日志/对话不打印 key

## 配置结构

`agent_config.json` 扩展（当前只有 provider/api_key/base_url/model）：

```json
{
  "provider": "custom",
  "api_key": "<主模型 key>",
  "base_url": "<主模型 url>",
  "model": "deepseek-chat",
  "vision_model": {
    "enabled": true,
    "api_key": "<视觉模型 key>",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen-vl-max"
  }
}
```

`vision_model` 缺失或 `enabled=false` → 视觉模型不可用（识图走 OCR 兜底）。

## 组件

### 1. `sjtu_agent/vision.py`（新建，独立可复用）

```python
def load_vision_config() -> dict | None:
    """读 agent_config.json 的 vision_model；未启用返回 None。
    返回 {"base_url", "api_key", "model"}。"""

def analyze_image(image_bytes: bytes, prompt: str = "") -> str:
    """用视觉模型多模态识图，返回描述文本。
    复用 agent._make_client()（OpenAI 兼容，发 image_url + text），
    一次性调用，不进入对话历史。失败抛异常。"""
```

- 复用现有 `_make_client`（`runner.py`）创建 OpenAI 客户端
- 图片转 base64 data URL 发 `image_url`
- 独立于主对话（纯识图，视觉模型不参与聊天历史）

### 2. setup 向导（`setup_wizard.py`）

在 `handle_agent`（主模型配置）后加**可选视觉模型步骤**：

```
是否配置视觉模型（用于识图，如 qwen-vl-max）？[y/N]
  → y：
    Base URL [https://dashscope.aliyuncs.com/compatible-mode/v1]:
    Model [qwen-vl-max]:
    API Key:  ← 用 _read_secret()（getpass，不回显）
    测试连通性 → 保存 vision_model（enabled=true）
  → n/skip：跳过（识图走 OCR 兜底）
```

- API key 输入必须用现有的 `_read_secret()`（`setup_wizard.py:208`，TTY 下 getpass 不回显）
- 测试连通性用现有 `_test_llm_connection()` 模式
- 保存用现有 `_apply_agent_config_updates()` 模式（需扩展支持 vision_model）

### 3. CLI 参数（`cli.py` setup 解析器）

镜像主模型 `--llm-*` 参数，新增：
```
--vision-base-url
--vision-api-key
--vision-model
--vision-enabled / --no-vision-enabled
```
非交互模式下用这些参数配置视觉模型。

### 4. 飞书识图（`feishu_bot.py _process_media_in_thread`）

```
图片消息
 ├─ 主模型支持视觉 → 现有多模态识图
 ├─ 否则 → load_vision_config() 非 None？
 │    ├─ 是 → analyze_image(img_bytes) 识图
 │    │    成功 → 回复识图结果
 │    │    失败 → 落 OCR（不打印 key）
 │    └─ 否 → OCR 提取（已修复路径）
 └─ OCR 也失败 → 提示"附件解析失败，请描述图片内容"
```

## 安全约束

- API key 输入用 `_read_secret()`（getpass，不回显）
- `analyze_image` / 连通性测试的错误消息**不包含 api_key**
- 日志、对话、测试输出**不打印 key**（复用脱敏模式，如有）
- `agent_config.json` 本地存储 key（正常），不进入任何打印

## 不做（本轮）

- web UI 视觉模型配置（后续）
- 主模型 api key 回显改造（现有行为，非本轮范围）

## 验证

1. `vision.py` 单元测试：`load_vision_config()` 读 enabled=true/false/缺失；`analyze_image()` mock `_make_client` 验证发 image_url+text payload，验证异常路径
2. setup 向导手动验证：配置视觉模型步骤，确认 api key 不回显（getpass），`agent_config.json` 保存正确
3. CLI 参数验证：`--vision-*` 写入 agent_config.json
4. 飞书识图链：主模型支持视觉 / 不支持+视觉模型 / 不支持+无视觉模型 / OCR 失败 — 四种路径
5. 全量 pytest 无回归
