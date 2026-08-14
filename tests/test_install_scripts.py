"""Regression tests for install script path handling."""

from pathlib import Path


def test_install_sh_project_dir_is_parent():
    """install.sh 的 PROJECT_DIR 应为脚本目录的父目录（仓库根）。

    bug: 曾把 SCRIPT_DIR（install/）当项目根，导致 pyproject.toml 检查失败。
    """
    script = Path("install/install.sh").read_text(encoding="utf-8")
    assert 'PROJECT_DIR="$(dirname "$SCRIPT_DIR")"' in script
    assert 'PROJECT_DIR="$SCRIPT_DIR"' not in script


def test_install_ps1_project_dir_is_parent():
    script = Path("install/install.ps1").read_text(encoding="utf-8")
    assert "$ProjectDir = Split-Path -Parent $ScriptDir" in script
    assert "$ProjectDir = $ScriptDir" not in script


def test_readme_uses_install_prefix():
    """README 安装命令应为 install/install.sh / install\install.ps1。"""
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "bash install/install.sh" in readme
    assert ".\install\install.ps1" in readme
    assert "bash install.sh" not in readme.replace("bash install/install.sh", "")
