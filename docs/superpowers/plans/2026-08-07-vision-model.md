# 双模型视觉架构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 SJTU Agent 增加独立视觉模型（如 qwen-vl），主模型不支持视觉时用视觉模型识图，解决 issue #113 #2。

**Architecture:** agent_config.json 的 `vision_model` 块存独立视觉模型配置；新建 `sjtu_agent/vision.py`（`load_vision_config` + `analyze_image`）；setup 向导加可选配置步骤（terminal 为主）；飞书识图优先级链：视觉模型 > OCR > 提示。

**Tech Stack:** Python 3.10+, OpenAI 兼容 API（复用 `agent._make_client`）, stdlib getpass。

## Global Constraints

- **API key 安全**：视觉模型 api_key 输入必须用 `setup_wizard._read_secret()`（getpass 不回显）；任何日志/错误消息/测试输出**不得包含 api_key**
- **不做 web UI**（本轮）
- 视觉模型**不进入主对话历史**（纯识图一次性调用）
- 视觉模型假设 OpenAI 兼容格式（qwen-vl 等）；Anthropic 视觉模型本轮不支持
- 识图优先级：视觉模型 > OCR > 提示

---

### Task 1: 新建 `sjtu_agent/vision.py`

**Files:**
- Create: `sjtu_agent/vision.py`
- Test: `tests/test_vision.py`

**Interfaces:**
- Produces: `load_vision_config() -> dict | None`（返回 `{"base_url", "api_key", "model"}` 或 None）、`analyze_image(image_bytes: bytes, prompt: str = "") -> str`

- [ ] **Step 1: 写失败测试**

```python
"""Tests for sjtu_agent/vision.py — standalone vision model for image analysis."""
import base64

import pytest


def _write_agent_config(tmp_path, monkeypatch, vision_model):
    """写一个含 vision_model 的 agent_config.json 到临时目录。"""
    import json
    from sjtu_agent.paths import AGENT_CONFIG_PATH
    cfg_path = tmp_path / "agent_config.json"
    cfg = {"api_key": "main-key", "model": "deepseek-chat"}
    if vision_model is not None:
        cfg["vision_model"] = vision_model
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("sjtu_agent.vision.AGENT_CONFIG_PATH", cfg_path)
    return cfg_path


def test_load_vision_config_enabled(tmp_path, monkeypatch):
    from sjtu_agent.vision import load_vision_config
    _write_agent_config(tmp_path, monkeypatch, {
        "enabled": True, "base_url": "https://x/v1", "api_key": "vm-key", "model": "qwen-vl-max",
    })
    cfg = load_vision_config()
    assert cfg is not None
    assert cfg["model"] == "qwen-vl-max"
    assert cfg["api_key"] == "vm-key"  # 不应打印，仅测试读取


def test_load_vision_config_disabled(tmp_path, monkeypatch):
    from sjtu_agent.vision import load_vision_config
    _write_agent_config(tmp_path, monkeypatch, {"enabled": False, "model": "qwen-vl-max"})
    assert load_vision_config() is None


def test_load_vision_config_missing(tmp_path, monkeypatch):
    from sjtu_agent.vision import load_vision_config
    _write_agent_config(tmp_path, monkeypatch, None)
    assert load_vision_config() is None


def test_analyze_image_sends_vision_payload(monkeypatch):
    """验证 analyze_image 用视觉模型发 image_url+text payload，不打印 key。"""
    import sjtu_agent.vision as vision
    captured = {}

    class _FakeResp:
        class _Choice:
            class _Msg:
                content = "图片里有一份课表"
            msg = _Msg()
        choices = [_Choice()]

    class _FakeClient:
        class _Completions:
            def create(self, model, messages, max_tokens):
                captured["model"] = model
                captured["messages"] = messages
                return _FakeResp()
        chat = _Completions()

    def _fake_make_client(cfg):
        captured["client_cfg"] = cfg
        return _FakeClient()

    monkeypatch.setattr(vision, "_make_client", _fake_make_client)
    monkeypatch.setattr(vision, "load_vision_config", lambda: {
        "base_url": "https://x/v1", "api_key": "vm-key", "model": "qwen-vl-max",
    })

    img = b"\x89PNG fake-image-bytes"
    reply = vision.analyze_image(img, "这张图有什么？")

    assert reply == "图片里有一份课表"
    assert captured["model"] == "qwen-vl-max"
    assert captured["client_cfg"]["api_key"] == "vm-key"
    content = captured["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert "data:image/jpeg;base64," in content[1]["image_url"]["url"]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_vision.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sjtu_agent.vision'`

- [ ] **Step 3: 实现 `vision.py`**

```python
"""Standalone vision model for image analysis.

独立于主对话模型：主模型（纯文本逻辑）不支持视觉时，用这里配置的
视觉模型（如 qwen-vl-max）识图。仅做一次性识图，不进入对话历史。
"""

import base64
import json

from sjtu_agent.paths import AGENT_CONFIG_PATH


def load_vision_config() -> dict | None:
    """读 agent_config.json 的 vision_model；未启用返回 None。

    返回 {"base_url", "api_key", "model"}。api_key 绝不进入任何打印。
    """
    try:
        if AGENT_CONFIG_PATH.exists():
            cfg = json.loads(AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
            vm = cfg.get("vision_model")
            if vm and vm.get("enabled") and vm.get("api_key") and vm.get("model"):
                return {
                    "base_url": vm.get("base_url"),
                    "api_key": vm["api_key"],
                    "model": vm["model"],
                }
    except Exception:
        pass
    return None


def analyze_image(image_bytes: bytes, prompt: str = "") -> str:
    """用视觉模型识图，返回描述文本。失败抛异常。

    使用 OpenAI 兼容格式（image_url base64）。视觉模型只用于本次识图，
    不参与主对话历史。
    """
    from sjtu_agent.agent import _make_client

    cfg = load_vision_config()
    if not cfg:
        raise RuntimeError("视觉模型未配置（agent_config.json 的 vision_model）")

    client = _make_client(cfg)
    b64 = base64.b64encode(image_bytes).decode()
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt or "请描述这张图片的内容，并提取其中的文字。"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    }]
    resp = client.chat.completions.create(
        model=cfg["model"], messages=messages, max_tokens=1024,
    )
    return resp.choices[0].message.content or ""
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_vision.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add sjtu_agent/vision.py tests/test_vision.py
git commit -m "feat: add vision.py — standalone vision model for image analysis"
```

---

### Task 2: 扩展 `_apply_agent_config_updates` 保留 vision_model + 新增 `_apply_vision_config_updates`

**Files:**
- Modify: `sjtu_agent/setup_wizard.py:192-205`
- Test: `tests/test_setup_vision_config.py`（新建）

**Interfaces:**
- Consumes: `agent.load_agent_config()`、`AGENT_CONFIG_PATH`
- Produces: `_apply_vision_config_updates(updates: dict) -> dict | None`（保存 vision_model 块）

- [ ] **Step 1: 写失败测试**

```python
"""Tests for vision_model config saving in setup_wizard."""
import json

import pytest


@pytest.fixture
def fake_agent_config(tmp_path, monkeypatch):
    from sjtu_agent.paths import AGENT_CONFIG_PATH
    cfg_path = tmp_path / "agent_config.json"
    cfg_path.write_text(json.dumps({"api_key": "main", "model": "deepseek-chat"}), encoding="utf-8")
    monkeypatch.setattr("sjtu_agent.setup_wizard.AGENT_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("sjtu_agent.setup_wizard.agent", __import__("agent"))
    return cfg_path


def test_apply_agent_updates_keeps_vision_model(fake_agent_config):
    """主模型更新不应清掉已保存的 vision_model。"""
    import json
    from sjtu_agent.setup_wizard import _apply_agent_config_updates
    # 先写一个 vision_model
    cfg = json.loads(fake_agent_config.read_text(encoding="utf-8"))
    cfg["vision_model"] = {"enabled": True, "model": "qwen-vl-max"}
    fake_agent_config.write_text(json.dumps(cfg), encoding="utf-8")

    _apply_agent_config_updates({"base_url": "https://y/v1", "api_key": "new", "model": "glm-5"})
    saved = json.loads(fake_agent_config.read_text(encoding="utf-8"))
    assert saved["model"] == "glm-5"
    assert saved["vision_model"]["model"] == "qwen-vl-max"  # 不被覆盖


def test_apply_vision_config_updates_saves_block(fake_agent_config):
    import json
    from sjtu_agent.setup_wizard import _apply_vision_config_updates
    _apply_vision_config_updates({
        "enabled": True, "base_url": "https://v/v1", "api_key": "vm-key", "model": "qwen-vl-max",
    })
    saved = json.loads(fake_agent_config.read_text(encoding="utf-8"))
    assert saved["vision_model"]["enabled"] is True
    assert saved["vision_model"]["model"] == "qwen-vl-max"
    assert saved["vision_model"]["api_key"] == "vm-key"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_setup_vision_config.py -v`
Expected: FAIL（`_apply_vision_config_updates` 不存在）

- [ ] **Step 3: 修改 `_apply_agent_config_updates` 保留 vision_model + 新增保存函数**

替换 `setup_wizard.py:192-205` 的 `_apply_agent_config_updates`，并新增 `_apply_vision_config_updates`：

```python
def _apply_agent_config_updates(updates: dict[str, str]) -> dict[str, str] | None:
    if not any(updates.values()):
        return None

    import agent

    current = agent.load_agent_config()
    saved = {
        "base_url": updates["base_url"] or current.get("base_url") or "https://models.sjtu.edu.cn/api/v1",
        "api_key": updates["api_key"] or current.get("api_key") or "",
        "model": updates["model"] or current.get("model") or "deepseek-chat",
    }
    # 保留已保存的 vision_model（主模型更新不清掉视觉模型）
    if isinstance(current.get("vision_model"), dict):
        saved["vision_model"] = current["vision_model"]
    AGENT_CONFIG_PATH.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    return saved


def _apply_vision_config_updates(updates: dict) -> dict | None:
    """保存/更新 vision_model 块到 agent_config.json。

    updates: {"enabled": bool, "base_url": str, "api_key": str, "model": str}
    api_key 仅在本地文件，绝不打印。
    """
    import agent

    current = agent.load_agent_config()
    vm = dict(current.get("vision_model") or {})
    for key in ("enabled", "base_url", "api_key", "model"):
        if updates.get(key):
            vm[key] = updates[key]
    if not vm:
        return None
    vm.setdefault("enabled", True)
    vm.setdefault("model", "qwen-vl-max")
    saved = dict(current)
    saved["vision_model"] = vm
    AGENT_CONFIG_PATH.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    return vm
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_setup_vision_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add sjtu_agent/setup_wizard.py tests/test_setup_vision_config.py
git commit -m "feat: vision_model config save — keep on agent update + add _apply_vision_config_updates"
```

---

### Task 3: setup 向导加视觉模型配置步骤

**Files:**
- Modify: `sjtu_agent/setup_wizard.py`（`handle_agent` 末尾 + 新增方法）
- Test: 手动验证（无自动化）

**Interfaces:**
- Consumes: `_read_secret()`（getpass）、`_test_llm_connection()`、`_apply_vision_config_updates()`
- Produces: `_configure_vision_model(self)` 方法（handle_agent 保存后调用）

- [ ] **Step 1: 新增 `_configure_vision_model` 方法**

在 `setup_wizard.py` 的 Wizard 类中新增方法（放在 `handle_agent` 附近）：

```python
def _configure_vision_model(self) -> None:
    """可选步骤：配置独立视觉模型（用于识图，如 qwen-vl-max）。"""
    self.say("\n接下来是可选的「视觉模型」配置。")
    self.say("如果你的主模型（如 deepseek）不支持识图，可以单独配一个视觉模型（如 qwen-vl-max），"
             "飞书收到图片时优先用它识图。不想配可以回复 skip。")
    while True:
        raw = self.prompt()
        intent = self.handle_common(raw, "vision", {})
        if intent == "quit":
            return
        if intent == "skip":
            self.say("好的，跳过视觉模型配置（识图将走 OCR 兜底）。")
            return
        if intent in {"yes", "empty"}:
            break
        self.say("配置视觉模型请输入 y，或回复 skip 跳过。")
        continue

    base_url = self.prompt("视觉模型 Base URL [https://dashscope.aliyuncs.com/compatible-mode/v1]: ") \
        .strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = self.prompt("视觉模型名称 [qwen-vl-max]: ").strip() or "qwen-vl-max"
    api_key = _read_secret("视觉模型 API Key（输入不回显）: ").strip()
    if not api_key:
        self.say("没有收到 API Key，跳过视觉模型配置。")
        return

    self.say("正在测试视觉模型连接，请稍候…")
    ok, err = _test_llm_connection(base_url, api_key, model)
    if not ok:
        self.say(f"视觉模型连接测试失败：{err}（不会保存 key）")
        self.say("你可以稍后重新运行 setup 配置，或手动编辑 agent_config.json 的 vision_model。")
        return

    saved = _apply_vision_config_updates({
        "enabled": True, "base_url": base_url, "api_key": api_key, "model": model,
    })
    if saved:
        self.say(f"✅ 视觉模型已保存：{saved.get('model')}（enabled=true）")
```

- [ ] **Step 2: 在 `handle_agent` 末尾调用**

在 `handle_agent` 的主模型保存完成后（找到保存后的 return True 之前），加：

```python
            # 主模型配置成功后，可选配置视觉模型
            self._configure_vision_model()
            return True
```

**注意**：需要确认 `handle_agent` 现有结构（保存后 return 的位置），把调用插在正确分支。如果 `handle_agent` 有多个 return，只在"保存成功"分支加。

- [ ] **Step 3: 手动验证**

Run: `python -c "import ast; ast.parse(open('sjtu_agent/setup_wizard.py', encoding='utf-8').read()); print('OK')"`
然后跑 `sjtu-agent setup` 走一遍 agent → vision 步骤，确认：
- API Key 输入**不回显**（getpass）
- `agent_config.json` 保存 vision_model 块

- [ ] **Step 4: Commit**

```bash
git add sjtu_agent/setup_wizard.py
git commit -m "feat: setup wizard vision model step (getpass, test connection, save)"
```

---

### Task 4: CLI 参数 `--vision-*`

**Files:**
- Modify: `sjtu_agent/setup_wizard.py:1178-1180`（parser 参数）+ `_cli_agent_updates` 附近新增 `_cli_vision_updates`
- Modify: `sjtu_agent/cli.py`（setup 命令调用处，如需要）

- [ ] **Step 1: 新增 CLI 参数**

在 `setup_wizard.py:1178-1180` 的 parser 参数后加：

```python
    parser.add_argument("--vision-base-url", default="", help="视觉模型 base URL to save")
    parser.add_argument("--vision-api-key", default="", help="视觉模型 API key to save")
    parser.add_argument("--vision-model", default="", help="视觉模型名称 to save")
    parser.add_argument("--vision-enabled", action="store_true", help="启用视觉模型")
    parser.add_argument("--no-vision-enabled", dest="vision_enabled", action="store_false", help="停用视觉模型")
```

- [ ] **Step 2: 新增 `_cli_vision_updates` + 保存调用**

在 `_cli_agent_updates`（`setup_wizard.py:142`）附近新增：

```python
def _cli_vision_updates(args: argparse.Namespace) -> dict:
    """收集 --vision-* CLI 参数。"""
    updates = {
        "base_url": args.vision_base_url or "",
        "api_key": args.vision_api_key or "",
        "model": args.vision_model or "",
    }
    if hasattr(args, "vision_enabled"):
        updates["enabled"] = bool(args.vision_enabled)
    return updates
```

在 `setup_wizard.py` 的 setup 主流程（`register_setup_parser` 或 main 里，`_apply_agent_config_updates(_cli_agent_updates(args))` 附近）加：

```python
    vision_updates = _cli_vision_updates(args)
    if any(vision_updates.values()) or vision_updates.get("enabled") is not None:
        _apply_vision_config_updates(vision_updates)
        print("Saved vision model config")
```

**注意**：需要确认 `vision_enabled` 在非交互模式下有默认值（`store_true` 默认 False；未传时 `hasattr` 判断）。非交互路径只在这些参数传入时保存。

- [ ] **Step 3: 验证 CLI**

Run:
```bash
python -c "import sjtu_agent.setup_wizard as s; import ast; ast.parse(open('sjtu_agent/setup_wizard.py', encoding='utf-8').read()); print('OK')"
```
然后（--test 或实际运行 setup 传 --vision-model 验证保存，注意 key 用占位测试值，不要用真实 key）

- [ ] **Step 4: Commit**

```bash
git add sjtu_agent/setup_wizard.py sjtu_agent/cli.py
git commit -m "feat: CLI --vision-* args for vision model config"
```

---

### Task 5: 飞书识图优先级链接入视觉模型

**Files:**
- Modify: `scripts/feishu_bot.py`（`_process_media_in_thread` 的 image 分支）

**Interfaces:**
- Consumes: `sjtu_agent.vision.load_vision_config()`、`analyze_image()`

- [ ] **Step 1: 修改图片分支 — 主模型不支持视觉时先试视觉模型**

在 `scripts/feishu_bot.py` `_process_media_in_thread` 的 `if msg_type == "image":` 块内，当前结构是：

```python
        if msg_type == "image":
            if _model_supports_vision(model):
                # ... 现有多模态识图 ...
                return
            # 模型不支持视觉 → 落到下方 OCR 解析路径（现有注释）
```

改为在 `_model_supports_vision(model)` 为 False 时，先尝试视觉模型，失败再落 OCR：

```python
        if msg_type == "image":
            if _model_supports_vision(model):
                # ... 现有多模态识图（保持不变）...
                return
            # 主模型不支持视觉 → 优先用独立视觉模型识图
            img_bytes = local_path.read_bytes()
            from sjtu_agent.vision import load_vision_config, analyze_image
            if load_vision_config() is not None:
                try:
                    desc = analyze_image(img_bytes, "请描述这张图片并提取其中的文字。")
                    _reply_text(message_id, f"🖼️ [视觉模型识图]\n{desc}")
                    return
                except Exception as e:
                    _logger.warning(f"[feishu] 视觉模型识图失败，回退 OCR: {e}")
            # 视觉模型不可用/失败 → 落到下方 OCR 解析路径
```

- [ ] **Step 2: 验证语法 + 测试**

Run:
```bash
python -c "import ast; ast.parse(open('scripts/feishu_bot.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_feishu_bot.py tests/test_feishu_conversations.py tests/test_vision.py -q
```
Expected: 全部通过（含 Task 1 的 vision 测试）

- [ ] **Step 3: Commit**

```bash
git add scripts/feishu_bot.py
git commit -m "feat: feishu image — vision model before OCR fallback (issue #113 #2)"
```

---

### Task 6: 全量验证 + 提交 PR

- [ ] **Step 1: 全量测试**

Run: `pytest tests/ -q --ignore=tests/test_dining.py` + `pytest tests/test_dining.py -q`
Expected: 全绿（~305+）

- [ ] **Step 2: 手动冒烟**

Run: `python -c "from sjtu_agent.vision import load_vision_config, analyze_image; print('vision import OK')"`

- [ ] **Step 3: 确认无 api_key 泄漏**

Run: `grep -rn "vision.*api_key\|api_key.*print\|print.*api_key" sjtu_agent/vision.py sjtu_agent/setup_wizard.py scripts/feishu_bot.py | grep -v "test"` — 确认没有打印 api_key 的代码

- [ ] **Step 4: 分支 + PR + CI + merge**

按项目流程：创建 `feat/vision-model` 分支 → push fork → PR（含 spec/plan 摘要）→ 等 CI 双绿 → merge

## 验证汇总

1. `tests/test_vision.py`：load_vision_config（enabled/disabled/missing）+ analyze_image payload
2. `tests/test_setup_vision_config.py`：主模型更新保留 vision_model + 保存 vision_model 块
3. setup 向导手动：getpass 不回显，agent_config.json 正确
4. 飞书识图四路径：视觉模型 / OCR / 视觉模型失败回退 / 都失败
5. 全量 pytest + 无 api_key 打印
