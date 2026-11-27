# backend/tasks/failure_marker.py — 任务失败日志的**文件兜底**（P2-15；2026-11-27 从 registry 抽出）
"""为什么单独一个模块：`registry.py` 已贴着 god-file 800 行红线（B5-2 加锁名工具时实测 808 行）。

**为什么需要文件兜底**：同一 DB 故障会让"失败日志"也写不进去，看板上就永远看不到这条失败——
进程日志之外再落一份可 grep 的标记（路径可用 `TASK_FAILURE_LOG` 覆盖；`.dev-logs/` 已在 .gitignore）。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def append_failure_marker(task_name: str, exc: Exception) -> None:
    """把一次任务失败追加到标记文件（DB 写不进时的唯一留痕）。失败本身不再抛。"""
    path = Path(os.environ.get("TASK_FAILURE_LOG", ".dev-logs/task-failures.log"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{datetime.now().isoformat(timespec='seconds')}\t{task_name}\t"
                f"{type(exc).__name__}: {str(exc)[:500]}\n"
            )
    except OSError:
        logger.error("任务失败兜底落盘也失败（只能靠进程日志）: %s", path)
