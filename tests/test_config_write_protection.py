"""Tests for config write protection.

Guards against the "read config fails → pass → rewrite whole config as {}
→ silently wipes all other credentials" pattern (P0-1 technical debt).
Rule: never write config back unless the read succeeded.
"""

import pytest


@pytest.fixture
def corrupt_config(monkeypatch, tmp_path):
    """Point module-level CONFIG_PATH at an unreadable config.json.

    Returns (module, config_path) so the test can compare bytes before/after.
    """
    def _patch(module):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("{corrupt json!!!", encoding="utf-8")  # 非法 JSON
        monkeypatch.setattr(module, "CONFIG_PATH", cfg_path)
        return cfg_path
    return _patch


def test_tool_save_credentials_aborts_on_corrupt_config(corrupt_config):
    from sjtu_agent.agent.tools import _core
    cfg_path = corrupt_config(_core)
    before = cfg_path.read_bytes()

    result = _core.tool_save_credentials(canvas_token="abc123")

    assert "error" in result, f"expected error, got {result}"
    assert cfg_path.read_bytes() == before, "config.json 被覆盖写回，凭据可能被清空"


def test_tool_setup_feishu_aborts_on_corrupt_config(corrupt_config):
    from sjtu_agent.agent.tools import _platforms
    cfg_path = corrupt_config(_platforms)
    before = cfg_path.read_bytes()

    result = _platforms.tool_setup_feishu(
        feishu_app_id="cli_x", feishu_app_secret="secret",
    )

    assert "error" in result, f"expected error, got {result}"
    assert cfg_path.read_bytes() == before, "config.json 被覆盖写回，凭据可能被清空"


def test_tool_setup_telegram_aborts_on_corrupt_config(corrupt_config):
    from sjtu_agent.agent.tools import _platforms
    cfg_path = corrupt_config(_platforms)
    before = cfg_path.read_bytes()

    result = _platforms.tool_setup_telegram(telegram_token="123:ABC")

    assert "error" in result, f"expected error, got {result}"
    assert cfg_path.read_bytes() == before, "config.json 被覆盖写回，凭据可能被清空"


def test_tool_setup_qq_aborts_on_corrupt_config(corrupt_config):
    from sjtu_agent.agent.tools import _platforms
    cfg_path = corrupt_config(_platforms)
    before = cfg_path.read_bytes()

    result = _platforms.tool_setup_qq(qq_app_id="111", qq_app_secret="secret")

    assert "error" in result, f"expected error, got {result}"
    assert cfg_path.read_bytes() == before, "config.json 被覆盖写回，凭据可能被清空"
