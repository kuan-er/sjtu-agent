"""tests/test_config.py — ConfigStore 单元测试"""
import json
import os
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_config_singleton():
    """每个测试前重置 ConfigStore 单例，避免测试间状态污染。"""
    from sjtu_agent import config as _cfg_mod
    _cfg_mod.ConfigStore._instance = None
    yield
    _cfg_mod.ConfigStore._instance = None


@pytest.fixture()
def tmp_config(tmp_path):
    """创建临时 config.json，返回 (config_path, env_path, write_fn)。"""
    config_path = tmp_path / "config.json"
    env_path    = tmp_path / ".env"
    env_path.write_text("")

    def write(data: dict):
        config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    write({})
    return config_path, env_path, write


@pytest.fixture(autouse=True)
def patch_paths(tmp_config, monkeypatch):
    """把所有测试的 paths.CONFIG_PATH / ENV_PATH 指向 tmp 目录。"""
    config_path, env_path, _ = tmp_config
    import sjtu_agent.paths as _p
    monkeypatch.setattr(_p, "CONFIG_PATH", config_path)
    monkeypatch.setattr(_p, "ENV_PATH", env_path)


# ---------------------------------------------------------------------------
# 基础读写
# ---------------------------------------------------------------------------

def test_read_canvas_token(tmp_config):
    _, _, write = tmp_config
    write({"canvas_token": "tok_abc", "canvas_base_url": "https://oc.sjtu.edu.cn"})

    from sjtu_agent.config import ConfigStore
    store = ConfigStore()
    assert store.canvas_token == "tok_abc"
    assert store.canvas_base_url == "https://oc.sjtu.edu.cn"


def test_missing_key_returns_default(tmp_config):
    _, _, write = tmp_config
    write({})  # 空配置

    from sjtu_agent.config import ConfigStore
    store = ConfigStore()
    assert store.canvas_token == ""
    assert store.canvas_base_url == "https://oc.sjtu.edu.cn"  # 硬编码默认值
    assert store.telegram_allowed_ids == []
    assert store.aihaoke_cookies == {}


def test_telegram_allowed_ids_parsed(tmp_config):
    _, _, write = tmp_config
    write({"telegram_token": "bot:TOKEN", "telegram_allowed_ids": ["12345", "67890"]})

    from sjtu_agent.config import ConfigStore
    store = ConfigStore()
    assert store.telegram_token == "bot:TOKEN"
    assert store.telegram_allowed_ids == [12345, 67890]


def test_qq_fields_parsed(tmp_config):
    _, _, write = tmp_config
    write({
        "qq_app_id": "1024",
        "qq_app_secret": "qq-secret",
        "qq_allowed_user_ids": ["u1", "  ", "u2"],
        "qq_enabled": False,
    })

    from sjtu_agent.config import ConfigStore
    store = ConfigStore()
    assert store.qq_app_id == "1024"
    assert store.qq_app_secret == "qq-secret"
    assert store.qq_allowed_user_ids == ["u1", "u2"]
    assert store.qq_enabled is False


# ---------------------------------------------------------------------------
# 热重载
# ---------------------------------------------------------------------------

def test_reload_if_changed_detects_update(tmp_config):
    _, _, write = tmp_config
    write({"canvas_token": "old_token"})

    from sjtu_agent.config import ConfigStore
    store = ConfigStore()
    assert store.canvas_token == "old_token"

    import time
    time.sleep(0.02)  # 确保 mtime 变化
    write({"canvas_token": "new_token"})

    changed = store.reload_if_changed()
    assert changed is True
    assert store.canvas_token == "new_token"


def test_reload_if_not_changed(tmp_config):
    _, _, write = tmp_config
    write({"canvas_token": "stable"})

    from sjtu_agent.config import ConfigStore
    store = ConfigStore()
    _ = store.canvas_token  # 触发加载
    changed = store.reload_if_changed()
    assert changed is False


# ---------------------------------------------------------------------------
# 单例行为
# ---------------------------------------------------------------------------

def test_singleton_same_instance():
    from sjtu_agent.config import ConfigStore
    a = ConfigStore()
    b = ConfigStore()
    assert a is b


# ---------------------------------------------------------------------------
# 损坏文件 / 缺失文件降级
# ---------------------------------------------------------------------------

def test_corrupt_file_returns_empty(tmp_config):
    config_path, _, _ = tmp_config
    config_path.write_text("{not valid json", encoding="utf-8")

    from sjtu_agent.config import ConfigStore
    store = ConfigStore()
    assert store.raw() == {}
    assert store.canvas_token == ""


def test_missing_file_returns_empty(tmp_config, monkeypatch):
    import sjtu_agent.paths as _p
    from pathlib import Path
    # 指向一个不存在的路径
    monkeypatch.setattr(_p, "CONFIG_PATH", Path("/tmp/__sjtu_agent_nonexistent_test__.json"))

    from sjtu_agent.config import ConfigStore
    store = ConfigStore()
    assert store.raw() == {}


# ---------------------------------------------------------------------------
# paths.py atomic IO
# ---------------------------------------------------------------------------

def test_atomic_write_json_and_read_safe(tmp_path):
    from sjtu_agent.paths import atomic_write_json, read_json_safe

    p = tmp_path / "state.json"
    data = {"a": 1, "中文": True, "list": [1, 2, 3]}
    atomic_write_json(p, data)
    assert read_json_safe(p) == data


def test_read_json_safe_corrupt(tmp_path):
    from sjtu_agent.paths import read_json_safe

    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_json_safe(p, default={"x": 0}) == {"x": 0}


def test_read_json_safe_missing(tmp_path):
    from sjtu_agent.paths import read_json_safe

    p = tmp_path / "gone.json"
    assert read_json_safe(p) == {}


def test_atomic_write_json_crash_safety(tmp_path):
    from sjtu_agent.paths import atomic_write_json, read_json_safe

    p = tmp_path / "state.json"
    atomic_write_json(p, {"safe": "data"})
    atomic_write_json(p, {"safe": "updated"})
    assert read_json_safe(p)["safe"] == "updated"
