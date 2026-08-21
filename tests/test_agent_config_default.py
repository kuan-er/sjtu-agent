"""tests/test_agent_config_default.py — 致远一号默认模型调用名契约。

致远一号（models.sjtu.edu.cn）平台文档：
- DeepSeek V4 Flash（常规模式）调用名 deepseek-chat
- DeepSeek V4 Flash（思考模式）调用名 deepseek-reasoner
- MiniMax-M2.7 调用名 minimax；Qwen3.6-27B 调用名 qwen

deepseek-chat / deepseek-reasoner 为有效调用名（官方 deprecated 的是旧产品名，
实际路由到 V4 系列新模型）；deepseek-v4-flash 不是调用名会 403。
public-models 是团队受限制时的公共池备选。这里锁住 zhiyuan 默认调用名。
"""

import importlib

chat_loop = importlib.import_module("sjtu_agent.agent.chat_loop")


def test_load_agent_config_zhiyuan_env_uses_deepseek_chat(monkeypatch):
    monkeypatch.setenv("ZHIYUAN_API_KEY", "sk-zhiyuan")
    monkeypatch.delenv("ZHIYUAN_BASE_URL", raising=False)

    cfg = chat_loop.load_agent_config()
    assert cfg["model"] == "deepseek-chat"  # V4 Flash 常规模式调用名
    assert "models.sjtu.edu.cn" in cfg["base_url"]


def test_zhiyuan_default_model_constant_is_deepseek_chat():
    assert chat_loop._ZHIYUAN_DEFAULT_MODEL == "deepseek-chat"