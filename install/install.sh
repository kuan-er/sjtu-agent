#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# 脚本在 install/ 下，项目根目录是它的父目录
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_CMD="python3"
INSTALL_PLAYWRIGHT=1
RUN_SETUP=1
FORCE_RECREATE_VENV=0

print_help() {
  cat <<'EOF'
SJTU Agent 一键安装脚本

用法:
  ./install.sh [选项]

选项:
  --python <cmd>          指定 Python 可执行文件，默认 python3
  --skip-playwright       跳过 Playwright Chromium 安装
  --no-setup              安装完成后不自动启动 sjtu-agent setup
  --force-recreate-venv   强制重建 .venv
  -h, --help              显示帮助
EOF
}

log() {
  printf '\n[%s] %s\n' "sjtu-agent-install" "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      [[ $# -ge 2 ]] || fail "--python 需要一个参数"
      PYTHON_CMD="$2"
      shift 2
      ;;
    --skip-playwright)
      INSTALL_PLAYWRIGHT=0
      shift
      ;;
    --no-setup)
      RUN_SETUP=0
      shift
      ;;
    --force-recreate-venv)
      FORCE_RECREATE_VENV=1
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      fail "未知参数: $1"
      ;;
  esac
done

[[ -f "$PROJECT_DIR/pyproject.toml" ]] || fail "请从仓库根目录运行这个脚本。"
command -v "$PYTHON_CMD" >/dev/null 2>&1 || fail "未找到 Python: $PYTHON_CMD"

"$PYTHON_CMD" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 或更高版本是必需的（browser-use 需要 3.11+）。")
PY

VENV_DIR="$PROJECT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

# ── 确保 uv 可用（未装则自动安装，2026 标准，比 pip 快一个数量级）───────────
if ! command -v uv >/dev/null 2>&1; then
  log "未检测到 uv，正在安装…（https://astral.sh/uv/install.sh）"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv 安装失败，请手动安装: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

if [[ $FORCE_RECREATE_VENV -eq 1 && -d "$VENV_DIR" ]]; then
  log "按要求重建虚拟环境: $VENV_DIR"
  rm -rf "$VENV_DIR"
fi

if [[ -d "$VENV_DIR" && ! -x "$VENV_PY" ]]; then
  log "检测到损坏的虚拟环境，准备重建: $VENV_DIR"
  rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  log "创建虚拟环境（uv）: $VENV_DIR"
  uv venv "$VENV_DIR" --python "$(command -v "$PYTHON_CMD")"
fi

log "安装 SJTU Agent（uv）"
uv pip install -e "$PROJECT_DIR" --python "$VENV_PY"

if [[ $INSTALL_PLAYWRIGHT -eq 1 ]]; then
  log "安装 Playwright Chromium"
  "$VENV_PY" -m playwright install chromium
fi

# ── 将 .venv/bin 加入 PATH（写入 shell rc 文件）──────────────────────────────
VENV_BIN="$VENV_DIR/bin"
EXPORT_LINE="export PATH=\"$VENV_BIN:\$PATH\"  # sjtu-agent"

add_to_shell_rc() {
  local rc="$1"
  if [[ -f "$rc" ]] && grep -qF "sjtu-agent" "$rc" 2>/dev/null; then
    log "PATH 设置已存在于 $rc，跳过"
    return
  fi
  printf '\n# Added by sjtu-agent install.sh\n%s\n' "$EXPORT_LINE" >> "$rc"
  log "已将 .venv/bin 加入 PATH → $rc"
}

ADDED_TO_RC=0
if [[ -f "$HOME/.zshrc" ]]; then
  add_to_shell_rc "$HOME/.zshrc"
  ADDED_TO_RC=1
fi
if [[ -f "$HOME/.bash_profile" ]]; then
  add_to_shell_rc "$HOME/.bash_profile"
  ADDED_TO_RC=1
elif [[ -f "$HOME/.bashrc" ]]; then
  add_to_shell_rc "$HOME/.bashrc"
  ADDED_TO_RC=1
fi
if [[ $ADDED_TO_RC -eq 0 ]]; then
  log "未找到 ~/.zshrc / ~/.bash_profile，请手动将以下行添加到你的 shell 配置文件："
  echo "  $EXPORT_LINE"
fi

SETUP_EXIT=0
if [[ $RUN_SETUP -eq 1 ]]; then
  log "启动 sjtu-agent setup"
  set +e
  "$VENV_BIN/sjtu-agent" setup
  SETUP_EXIT=$?
  set -e
fi

# 安装/重建 venv 后，按之前的安装清单自动恢复后台服务（launchd / systemd）。
# 若项目目录或 Python 路径没有变化，resync 会直接跳过。
log "恢复此前安装过的后台服务"
if "$VENV_PY" -m sjtu_agent daemons resync >/dev/null 2>&1; then
  log "后台服务已是最新，或已从安装清单自动恢复。"
else
  log "后台服务恢复跳过或失败；如需安装请运行 sjtu-agent install-daemons"
fi

if [[ $SETUP_EXIT -ne 0 ]]; then
  exit "$SETUP_EXIT"
fi

cat <<EOF

安装完成。

新开一个终端（或运行 source ~/.zshrc）后，直接输入：
  sjtu-agent         # 启动主对话
  sjtu-agent setup   # 重新配置
  sjtu-agent doctor  # 检查环境状态

如果当前终端想立即使用，运行：
  export PATH="$VENV_BIN:\$PATH"
EOF
