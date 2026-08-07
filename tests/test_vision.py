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
            message = _Msg()
        choices = [_Choice()]

    class _FakeCompletions:
        def create(self, model, messages, max_tokens):
            captured["model"] = model
            captured["messages"] = messages
            return _FakeResp()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

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
