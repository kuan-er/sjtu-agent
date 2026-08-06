"""SJTU Agent package metadata and shared utilities."""

# ── Windows 后台 stdout/stderr 保护 ─────────────────────────────────────────
# 在 psmux / Task Scheduler（pythonw 无控制台）下运行 daemon 时，sys.stdout /
# sys.stderr 可能是 None。任何 print(..., flush=True) 或 logging.StreamHandler
# 调用 .flush() 都会抛 "'NoneType' object has no attribute 'flush'"。
# 这里是包导入最早执行的代码，所有 bot/daemon 都 import sjtu_agent，故能覆盖
# 全部入口：把 None 替换为 devnull（写入即丢弃），前台有控制台时不受影响。
import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

__all__ = ["__version__"]

__version__ = "0.7.5"
