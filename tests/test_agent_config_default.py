"""tests/test_agent_config_default.py — 致远一号默认模型 ID 契约。

致远一号 API（models.sjtu.edu.cn）只允许团队访问模型 public-models；
deepseek-v4-flash 只是产品展示名，作为 API 模型 ID 会得到 403
("team not allowed to access model ... can only access ['public-models']")。
这里锁住 zhiyuan 环境的默认模型 ID。
"""

import importlib

chat_loop = importlib.import_module("sjtu_agent.agent.chat_loop")


def test_load_agent_config_zhiyuan_env_uses_public_models(monkeypatch):
    monkeypatch.setenv("ZHIYUAN_API_KEY", "sk-zhiyuan")
    monkeypatch.delenv("ZHIYUAN_BASE_URL", raising=False)

    cfg = chat_loop.load_agent_config()
    assert cfg["model"] == "public-models"
    assert "models.sjtu.edu.cn" in cfg["base_url"]


def test_zhiyuan_default_model_constant_is_public_models():
    assert chat_loop._ZHIYUAN_DEFAULT_MODEL == "public-models"