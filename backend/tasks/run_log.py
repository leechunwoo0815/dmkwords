# backend/tasks/run_log.py — 定时任务运行记录的失败收口（R11-03，2026-10-26）
"""`TaskRunLog` 的失败语义集中在这里：失败必须落在**同一条**运行记录上。

背景：任务体可以中途 `commit()`（`backend/common/batch.py` 分批提交），这会把 RUNNING 行
一起提交；失败后的 `rollback()` 撤不掉它。若再插一条 failed，行序成为
`failed, running, failed, running…`，而看板按"最近记录连续 failed"算 streak
（`backend/domain/admin/task_specs.py`）⇒ "连续失败"恒为 1（假绿）。

做法：失败时按 id 找回已落库的行，就地改 `failed / finished_at / error`；从未落库才新插
一行，并沿用真实开始时间（不是失败时刻）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from backend.common.notification_models import TaskRunLog


def finalize_failed_run(
    session: Session,
    log_id: int | None,
    *,
    task_name: str,
    started_at: datetime,
    exc: Exception,
) -> TaskRunLog:
    """把失败收口到同一条 `TaskRunLog`（调用方负责 commit）。

    `log_id` 为 None（失败发生在首次 flush 之前）或行已随 rollback 消失时，新建一行。
    用 Query 而不是 `session.get`：行若不存在，get 会对身份映射里已失效的实例发 refresh
    并抛 `ObjectDeletedError`；SELECT 查不到只是返回 None。
    """
    error = str(exc)[:2000]
    row = (
        session.query(TaskRunLog).filter(TaskRunLog.id == log_id).one_or_none()
        if log_id is not None
        else None
    )
    if row is None:
        row = TaskRunLog(task_name=task_name, started_at=started_at)
        session.add(row)
    row.status = TaskRunLog.STATUS_FAILED
    row.finished_at = datetime.now()
    row.processed = 0
    row.error = error
    return row
