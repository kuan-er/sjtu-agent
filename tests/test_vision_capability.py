"""识图能力判定：配置 → 已知清单 → 探测一次（issue #203）。"""

from __future__ import annotations

import json

import pytest

from sjtu_agent import vision_capability as vc


class _FakeCompletions:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return {"ok": True}


class _FakeClient:
    def __init__(self, behaviour=None):
        self.completions = _FakeCompletions(behaviour)
        self.chat = type("_Chat", (), {"completions": self.completions})()


@pytest.fixture(autouse=True)
def _cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv(vc._CACHE_PATH_ENV, str(tmp_path / "vision_capability.json"))


@pytest.fixture
def no_config(monkeypatch):
    """默认没有显式配置。"""
    import sjtu_agent.agent as agent_pkg

    monkeypatch.setattr(agent_pkg, "load_agent_config", lambda: {})


# ── 已知清单 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model", ["gpt-4o", "claude-3-5-sonnet", "deepseek-flash", "gemini-3-pro", "qwen-vl-max"]
)
def test_known_vision_models(model):
    assert vc.known_vision_model(model) is True


@pytest.mark.parametrize("model", ["deepseek-chat", "deepseek-reasoner", ""])
def test_known_text_models(model):
    assert vc.known_vision_model(model) is False


def test_new_model_is_unknown_not_assumed_text_only(no_config):
    """issue #203 的核心：清单里没有的新模型不能直接判成「不能识图」。"""
    assert vc.vision_capability("gpt-6.1-sol") == "unknown"


# ── 显式配置优先 ────────────────────────────────────────────────────────────


def test_config_flag_overrides_everything(monkeypatch):
    import sjtu_agent.agent as agent_pkg

    monkeypatch.setattr(agent_pkg, "load_agent_config", lambda: {"vision_capable": True})
    assert vc.vision_capability("gpt-6.1-sol") == "yes"
    monkeypatch.setattr(agent_pkg, "load_agent_config", lambda: {"vision_capable": False})
    assert vc.vision_capability("gpt-4o") == "no"


def test_unsupported_model_list_is_respected(monkeypatch):
    import sjtu_agent.agent as agent_pkg

    monkeypatch.setattr(
        agent_pkg,
        "load_agent_config",
        lambda: {"vision_unsupported_models": ["some-text-model"]},
    )
    assert vc.vision_capability("some-text-model") == "no"


# ── 行为探测 ────────────────────────────────────────────────────────────────


def test_probe_accepts_model_that_answers(no_config):
    client = _FakeClient()
    assert vc.probe_vision_capability(client, "gpt-6.1-sol") == "yes"
    call = client.completions.calls[0]
    assert call["max_tokens"] == 1
    payload = call["messages"][0]["content"]
    assert any(block.get("type") == "image_url" for block in payload)
    # 结论落地：下一次不再探测
    assert vc.vision_capability("gpt-6.1-sol") == "yes"


def test_probe_marks_model_that_rejects_images(no_config):
    client = _FakeClient(RuntimeError("400 invalid_request_error: unsupported content type image_url"))
    assert vc.probe_vision_capability(client, "gpt-6.1-sol") == "no"
    assert vc.vision_capability("gpt-6.1-sol") == "no"


def test_probe_returns_none_on_inconclusive_error(no_config):
    client = _FakeClient(RuntimeError("Connection timed out"))
    assert vc.probe_vision_capability(client, "gpt-6.1-sol") is None
    # 判定不出来就不写缓存，下次还能再试
    assert vc.vision_capability("gpt-6.1-sol") == "unknown"


def test_resolve_uses_probe_for_unknown_model(no_config):
    """最终判定：未知模型试一次，而不是直接落到视觉模型/OCR。"""
    assert vc.resolve_vision_capability("gpt-6.1-sol", _FakeClient()) is True
    assert vc.resolve_vision_capability("gpt-6.1-sol", _FakeClient()) is True
    assert vc.resolve_vision_capability("other-new-model", _FakeClient(RuntimeError(
        "model does not support image input"))) is False


def test_resolve_without_client_falls_back_to_false(no_config):
    assert vc.resolve_vision_capability("gpt-6.1-sol", None) is False


@pytest.mark.parametrize(
    "message",
    [
        "400 unsupported content type: image_url",
        "This model does not support image input.",
        "multimodal not supported",
        "该模型不支持图片输入",
    ],
)
def test_image_rejection_detection(message):
    assert vc.is_image_rejection(RuntimeError(message)) is True


@pytest.mark.parametrize(
    "message", ["Connection timed out", "429 rate limit exceeded", "401 invalid api key"]
)
def test_non_rejection_errors(message):
    assert vc.is_image_rejection(RuntimeError(message)) is False


def test_cache_file_is_json(tmp_path):
    client = _FakeClient()
    vc.probe_vision_capability(client, "gpt-6.1-sol")
    data = json.loads(
        (tmp_path / "vision_capability.json").read_text(encoding="utf-8")
    )
    assert data == {"gpt-6.1-sol": True}
