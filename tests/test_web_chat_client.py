"""tests/test_web_chat_client.py — WebUI 聊天客户端配置解析。

回归：WebUI 与 CLI 的 LLM 配置不一致 bug（同学反馈"CLI 能通、WebUI timed out"）。
CLI 的 load_agent_config() 在只有 `.env` 的 ZHIYUAN_API_KEY、没有 agent_config.json
时会把 base_url 默认补成 https://models.sjtu.edu.cn/api/v1；而 Web 的
_get_chat_client() 缺这一步 → base_url=None → OpenAI 客户端默认走
api.openai.com，校外连不上就 timed out。这里锁住修复后的行为。
"""

import pytest


@pytest.fixture(autouse=True)
def _reset():
    """每个测试后清空模块缓存状态（如有）。"""
    yield


def _get_chat_client(monkeypatch, agent_cfg=None, env=None):
    from sjtu_agent.web import server

    monkeypatch.setattr(server, "_read_agent_config", lambda: dict(agent_cfg or {}))
    monkeypatch.setattr(server, "_read_env", lambda: dict(env or {}))
    return server._get_chat_client()


def test_zhiyuan_env_only_defaults_base_url(monkeypatch):
    """只有 .env 的 ZHIYUAN_API_KEY、无 agent_config.json：应默认致远一号端点。"""
    client, model, proto = _get_chat_client(
        monkeypatch,
        agent_cfg={},
        env={"ZHIYUAN_API_KEY": "sk-zhiyuan-test"},
    )
    assert proto == "openai"
    assert model == "deepseek-v4-flash"
    assert "models.sjtu.edu.cn" in str(client.base_url)
    assert client.api_key == "sk-zhiyuan-test"


def test_explicit_base_url_wins_over_preset(monkeypatch):
    """agent_config.json 显式给了 base_url 时，以它为准（不覆盖用户配置）。"""
    client, model, proto = _get_chat_client(
        monkeypatch,
        agent_cfg={
            "provider": "custom",
            "base_url": "https://my-gateway.example.com/v1",
            "api_key": "k2",
            "model": "some-model",
        },
        env={},
    )
    assert proto == "openai"
    assert model == "some-model"
    assert "my-gateway.example.com" in str(client.base_url)


def test_anthropic_provider_defaults_from_preset(monkeypatch):
    """provider 已知且只有 env key：用预设的终点与 claude 模型。"""
    client, model, proto = _get_chat_client(
        monkeypatch,
        agent_cfg={},
        env={"ANTHROPIC_API_KEY": "sk-ant"},
    )
    assert proto == "anthropic"
    assert model.startswith("claude")
    assert "api.anthropic.com" in str(client.base_url)


def test_env_key_fallback_when_agent_cfg_has_provider_only(monkeypatch):
    """agent_config.json 只有 provider、key 来自 env：能拿到 key 并补默认端点。"""
    client, model, proto = _get_chat_client(
        monkeypatch,
        agent_cfg={"provider": "zhiyuan"},
        env={"ZHIYUAN_API_KEY": "sk-fallback"},
    )
    assert proto == "openai"
    assert "models.sjtu.edu.cn" in str(client.base_url)
    assert client.api_key == "sk-fallback"