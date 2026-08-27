"""Tests for newcomer onboarding surfaces:

- tool_check_setup: 未配置项附带新手教程 help_url（doctor / LLM 引导引用）
- cli._missing_required_setup: doctor 人类可读提示的必填缺项列表

注意：sjtu_agent.agent 包把同名函数 chat_loop 导出到了包属性上，
拿模块本体必须走 importlib。
"""

from __future__ import annotations

import importlib
import json

import pytest

import sjtu_agent.cli as cli
from sjtu_agent.agent.tools import _core as tools_core

_chat_loop_mod = importlib.import_module("sjtu_agent.agent.chat_loop")


@pytest.fixture(autouse=True)
def _clean_setup_env(monkeypatch, tmp_path):
    """隔离环境变量与 agent_config 路径，让判定完全由用例控制。"""
    for var in (
        "JACCOUNT_USERNAME",
        "JACCOUNT_PASSWORD",
        "MOOC_USERNAME",
        "MOOC_PASSWORD",
        "ZHIYUAN_API_KEY",
        "ZHIYUAN_BASE_URL",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        _chat_loop_mod,
        "AGENT_CONFIG_PATH",
        tmp_path / "no-agent-config.json",
    )
    monkeypatch.setattr(tools_core._cfg, "raw", lambda: {})


def test_check_setup_marks_help_url_for_missing_required_items():
    setup = tools_core.tool_check_setup()

    assert setup["agent"]["help_url"] == tools_core._SETUP_GUIDE_URL
    assert setup["jaccount"]["help_url"] == tools_core._SETUP_GUIDE_URL
    assert setup["canvas"]["help_url"] == tools_core._SETUP_GUIDE_URL
    # 可选平台同样给出指路（未配置 cookie 时）
    assert setup["icourse"]["help_url"] == tools_core._SETUP_GUIDE_URL


def test_check_setup_omits_help_url_when_configured(monkeypatch, tmp_path):
    cfg_path = tmp_path / "agent-config.json"
    cfg_path.write_text(
        json.dumps({"base_url": "https://x/v1", "api_key": "sk-x", "model": "m"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("JACCOUNT_USERNAME", "zhangsan")
    monkeypatch.setenv("JACCOUNT_PASSWORD", "secret")
    monkeypatch.setattr(_chat_loop_mod, "AGENT_CONFIG_PATH", cfg_path)
    monkeypatch.setattr(
        tools_core._cfg,
        "raw",
        lambda: {"canvas_token": "real-token", "icourse_cookies": {"sid": "x"}},
    )

    setup = tools_core.tool_check_setup()

    assert "help_url" not in setup["agent"]
    assert "help_url" not in setup["jaccount"]
    assert "help_url" not in setup["canvas"]
    assert "help_url" not in setup["icourse"]
    # 水源保持原有 note 引导，不注入 URL
    assert setup["shuiyuan"].get("needs_attention") is True


def test_missing_required_setup_lists_all_labels_when_empty():
    assert cli._missing_required_setup({}) == ["大模型 API", "jAccount 账号", "Canvas Token"]


def test_missing_required_setup_skips_ready_items():
    ready = {
        "agent": {"configured": True},
        "jaccount": {"has_credentials": True},
        "canvas": {"has_token": False},
    }
    assert cli._missing_required_setup(ready) == ["Canvas Token"]
