"""飞书 Bot Windows 桌面启动器 — 无需命令行，一键启动/停止/查看状态。

双击此文件即可运行（关联 pythonw.exe），不会弹出终端窗口。
"""

from __future__ import annotations

import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
BOT_SCRIPT = ROOT / "scripts" / "feishu_bot.py"
SESSION_NAME = "feishu-bot"


def _run_psmux(*args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["psmux", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def bot_running() -> bool:
    try:
        result = _run_psmux("has-session", "-t", SESSION_NAME, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def start_bot() -> str:
    if bot_running():
        return "Bot 已在运行中"
    try:
        _run_psmux("kill-session", "-t", SESSION_NAME, timeout=5)
    except Exception:
        pass
    result = subprocess.run(
        ["psmux", "new", "-s", SESSION_NAME, "-d", "--",
         str(VENV_PYTHON), str(BOT_SCRIPT)],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        return f"Bot 已启动 (session: {SESSION_NAME})"
    return f"启动失败: {result.stderr.strip() or '未知错误'}"


def stop_bot() -> str:
    if not bot_running():
        return "Bot 未在运行"
    try:
        _run_psmux("kill-session", "-t", SESSION_NAME, timeout=10)
        return "Bot 已停止"
    except Exception as e:
        return f"停止失败: {e}"


def status_text() -> str:
    if bot_running():
        try:
            result = _run_psmux("capture-pane", "-t", SESSION_NAME, "-S", "-200", timeout=5)
            last_lines = result.stdout.strip().split("\n")[-5:]
            return "● 运行中\n\n最近输出:\n" + "\n".join(last_lines)
        except Exception:
            return "● 运行中"
    return "○ 未运行"


# ── GUI ─────────────────────────────────────────────────────────────────────

class LauncherApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SJTU Agent — 飞书 Bot 启动器")
        self.root.geometry("580x460")
        self.root.resizable(True, True)
        self.root.configure(bg="#1e1e2e")

        # 字体和颜色
        self.fg = "#cdd6f4"
        self.bg = "#1e1e2e"
        self.btn_bg = "#313244"
        self.accent = "#89b4fa"
        self.green = "#a6e3a1"
        self.red = "#f38ba8"

        # 标题
        title = tk.Label(self.root, text="飞书 Bot 启动器", font=("Segoe UI", 18, "bold"),
                         fg=self.accent, bg=self.bg)
        title.pack(pady=(20, 5))

        sub = tk.Label(self.root, text="SJTU Agent — 无需命令行, 一键管理 Bot 进程",
                       font=("Segoe UI", 9), fg=self.fg, bg=self.bg)
        sub.pack(pady=(0, 15))

        # 按钮区域
        btn_frame = tk.Frame(self.root, bg=self.bg)
        btn_frame.pack(pady=5)

        self.start_btn = tk.Button(btn_frame, text="▶  启动 Bot", font=("Segoe UI", 12),
                                   fg=self.green, bg=self.btn_bg, activebackground=self.btn_bg,
                                   width=12, command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=10)

        self.stop_btn = tk.Button(btn_frame, text="■  停止 Bot", font=("Segoe UI", 12),
                                  fg=self.red, bg=self.btn_bg, activebackground=self.btn_bg,
                                  width=12, command=self._stop)
        self.stop_btn.pack(side=tk.LEFT, padx=10)

        self.status_btn = tk.Button(btn_frame, text="⟳  刷新状态", font=("Segoe UI", 12),
                                    fg=self.accent, bg=self.btn_bg, activebackground=self.btn_bg,
                                    width=12, command=self._refresh)
        self.status_btn.pack(side=tk.LEFT, padx=10)

        # 状态指示器
        self.status_label = tk.Label(self.root, text="○ 未运行", font=("Segoe UI", 14),
                                     fg=self.fg, bg=self.bg)
        self.status_label.pack(pady=(15, 5))

        # 日志/输出区域
        self.output = scrolledtext.ScrolledText(
            self.root, height=12, font=("Cascadia Code", 9),
            bg="#11111b", fg="#cdd6f4", insertbackground=self.fg,
            relief=tk.FLAT, borderwidth=0,
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=20, pady=(5, 20))

        self._refresh()
        self.root.mainloop()

    def _log(self, msg: str) -> None:
        self.output.insert(tk.END, msg + "\n")
        self.output.see(tk.END)

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.config(text=text, fg=color)

    def _start(self) -> None:
        self.start_btn.config(state=tk.DISABLED)
        self._log("正在启动 Bot…")
        t = threading.Thread(target=self._do_start)
        t.start()

    def _do_start(self) -> None:
        msg = start_bot()
        self.root.after(0, lambda: self._log(msg))
        self.root.after(0, self._refresh)
        self.root.after(0, lambda: self.start_btn.config(state=tk.NORMAL))

    def _stop(self) -> None:
        self.stop_btn.config(state=tk.DISABLED)
        self._log("正在停止 Bot…")
        t = threading.Thread(target=self._do_stop)
        t.start()

    def _do_stop(self) -> None:
        msg = stop_bot()
        self.root.after(0, lambda: self._log(msg))
        self.root.after(0, self._refresh)
        self.root.after(0, lambda: self.stop_btn.config(state=tk.NORMAL))

    def _refresh(self) -> None:
        running = bot_running()
        if running:
            self._set_status("● 运行中", self.green)
        else:
            self._set_status("○ 未运行", self.fg)
        self._log(status_text().replace("● 运行中", "").replace("○ 未运行", "").strip())


if __name__ == "__main__":
    if sys.platform != "win32":
        messagebox.showerror("平台不支持", "此启动器仅支持 Windows 系统。")
        sys.exit(1)
    LauncherApp()
