# backend/tasks/task_guard.py — 任务级互斥锁与失败重试排程（第十轮 W2-D4，2026-10-25）
"""定时任务的**执行期互斥**与**失败一次性重试**（从 `registry.py` 抽出，避免 god file）。

与调度器领导锁的分工（两把锁都要有）：
  - `dmkwords:scheduler`（`registry.SCHEDULER_LOCK_NAME`）= **谁负责调度**：跨副本领导权，
    启动时取、长期持有，拿不到就整个副本不注册任务；
  - `dmkwords:task:<name>`（本模块）= **这个任务此刻跑没跑**：只在单次执行期间持有。
为什么需要后者：看板手动触发**有意**不受领导锁限制（人按的按钮必须响应），于是"手动 + 自动"
会撞车——同一任务两份并发跑（`TaskRunLog` 双条；幂等不彻底的任务会重复处理，例如重复发通知）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

#: 任务级锁名前缀
TASK_LOCK_PREFIX = "dmkwords:task:"

#: 自动调度失败后的重试退避（秒）。退避走"排一个 date 任务"而不是 sleep——
#: 调度线程不被占住（否则同进程其它任务被推迟）。
TASK_RETRY_BACKOFF_SECONDS = 30


def try_task_lock(task_name: str) -> tuple[bool, object]:
    """尝试取任务级锁。返回 `(是否可执行, 需释放的连接或 None)`。

    "锁设施不可用 ⇒ 放行"与调度器锁同款纪律：连库失败 / 数据库不支持 `GET_LOCK` 时，
    不该让定时任务整体停摆（那时真正要处理的是数据库本身）。
    """
    from sqlalchemy import text

    from backend.database import engine

    try:
        conn = engine.connect()
        got = conn.execute(
            text("SELECT GET_LOCK(:n, 0)"), {"n": TASK_LOCK_PREFIX + task_name}
        ).scalar()
        conn.commit()  # 结束隐式事务（命名锁是会话级，提交不影响持有）
    except Exception as exc:  # noqa: BLE001
        logger.warning("任务级锁不可用（%s），本次直接执行: %s", exc, task_name)
        return True, None
    if got != 1:
        conn.close()
        return False, None
    return True, conn


def release_task_lock(task_name: str, conn) -> None:
    """释放任务级锁。

    **必须显式 `RELEASE_LOCK`**（与调度器锁同一个坑）：`close()` 只是把连接还回池，
    DBAPI 会话没断、会话级命名锁会跟着池里的连接留下来，下一轮就永远取不到锁。
    """
    if conn is None:
        return
    from sqlalchemy import text

    try:
        conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": TASK_LOCK_PREFIX + task_name})
        conn.commit()
    except Exception:  # noqa: BLE001 — 释放失败不影响收尾（连接关闭后由 MySQL 自解）
        logger.warning("任务级锁释放失败（连接关闭后由 MySQL 自解）: %s", task_name)
    finally:
        conn.close()


def schedule_retry(scheduler, task_name: str, runner, *, misfire_grace: int) -> bool:
    """为失败任务排一次**一次性**重试（排程失败只记日志，不影响本次返回）。

    `runner` 由调用方传入（就是调度入口 `guarded_run`），避免本模块反向依赖 `registry`
    造成循环 import；`runner` 会以 `is_retry=True` 被调起，因此重试再失败**不会再排下一次**
    （否则失败任务会无限自我复制）。
    """
    if scheduler is None:
        return False
    try:
        scheduler.add_job(
            runner,
            trigger="date",
            run_date=datetime.now() + timedelta(seconds=TASK_RETRY_BACKOFF_SECONDS),
            args=[task_name],
            kwargs={"is_retry": True},
            id=f"{task_name}:retry",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=misfire_grace,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("任务重试排程失败: %s", task_name, exc_info=True)
        return False


def run_with_task_lock(task_name: str, display_name: str, body, *, manual: bool) -> dict:
    """在任务级锁下跑 `body()`；抢不到锁时**手动报错 / 自动跳过**。

    `body` 由调用方传入（`registry` 的加锁执行体），避免本模块反向依赖 `registry`。
    """
    may_run, conn = try_task_lock(task_name)
    if not may_run:
        from backend.common.exceptions import ConflictError

        if manual:
            raise ConflictError(f"任务「{display_name}」正在运行中，请稍后再试")
        logger.warning("任务正在运行中，跳过本轮: %s", task_name)
        return {
            "task": task_name,
            "display_name": display_name,
            "status": "skipped",
            "reason": "running",
        }
    try:
        return body()
    finally:
        release_task_lock(task_name, conn)
