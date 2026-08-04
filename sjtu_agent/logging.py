"""Unified logging for SJTU Agent.

Replaces the ad-hoc "print + append to file + manual rotation" _log() helpers
that were duplicated across remind_check/daily_report/canvas_watcher. Uses
stdlib `logging` with a RotatingFileHandler writing to DATA_DIR/logs/, and
mirrors to stdout so interactive/daemon output stays visible where a terminal
exists.
"""

import logging
from logging.handlers import RotatingFileHandler

from sjtu_agent.paths import DATA_DIR

#: 单个日志文件大小上限（与原 _log 的 200KB 手动轮转一致）
_LOG_MAX_BYTES = 200 * 1024
_LOG_BACKUP_COUNT = 2


def get_logger(name: str) -> logging.Logger:
    """Return a logger writing to DATA_DIR/logs/<name>.log with rotation.

    Each name gets its own file handler + a stdout mirror. Repeated calls with
    the same name reuse the configured logger (no duplicate handlers).
    """
    logger = logging.getLogger(f"sjtu_agent.{name}")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False  # 避免传播到 root 造成重复输出

    log_dir = DATA_DIR / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    file_handler = RotatingFileHandler(
        log_dir / f"{name}.log",
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)

    # stdout 镜像（有终端时可见；pythonw 无控制台时此 handler 静默丢弃）
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(stream_handler)

    return logger
