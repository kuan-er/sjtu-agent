"""Tests for vision_model config saving in setup_wizard."""
import importlib
import json

import pytest


@pytest.fixture
def fake_agent_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "agent_config.json"
    cfg_path.write_text(json.dumps({"api_key": "main", "model": "deepseek-chat"}), encoding="utf-8")
    # 写入路径：_apply_agent_config_updates / _apply_vision_config_updates 用 setup_wizard 模块级 AGENT_CONFIG_PATH
    monkeypatch.setattr("sjtu_agent.setup_wizard.AGENT_CONFIG_PATH", cfg_path)
    # 读取路径：setup_wizard 只在函数内 `import agent`（无模块级导入），monkeypatch
    # setup_wizard.agent 无效。真实 agent.load_agent_config() 读取的是
    # sjtu_agent.agent.chat_loop 模块的全局 AGENT_CONFIG_PATH，必须一并 patch，
    # 让 load_agent_config 读到同一个临时文件。
    # 注意：不能用字符串路径 "sjtu_agent.agent.chat_loop.AGENT_CONFIG_PATH" —
    # __init__.py 的 `from ... import chat_loop` 把包属性 chat_loop 重绑为函数。
    chat_loop = importlib.import_module("sjtu_agent.agent.chat_loop")
    monkeypatch.setattr(chat_loop, "AGENT_CONFIG_PATH", cfg_path)
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
