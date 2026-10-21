# backend/common/batch.py — 批处理公共件：批量取数 + 分块提交（P2-41）
"""定时任务/批量链路的两个公共件：**批量取数**（消 N+1）与**分块提交**（缩短持锁窗口）。

为什么要有它（P2-41，2026-10-20 第八轮大审查，实测口径）：
- **N+1**：到期提醒类任务原先**每条记录**各查一次 `Child`/`Book`/`Parent`（3N 条 SELECT）
  → 6000 条 = 1.8 万条 SQL（逾期链路 W6 压测实测 104.39 秒，见 `circulation/overdue_notify.py`）；
- **整批单事务长锁**：这些任务原先"整批一个事务"——6000 条 ≈ 8000 条 SQL 全在一个事务里，
  行锁与 undo 一路累积、持锁数十秒；期间任何一条报错还会把整批已做的工作**全部回滚**。
  而这类批量任务**逐条独立**（状态守卫 UPDATE + 带去重键的通知），天然可分块提交：
  部分进度落库、下轮幂等续跑（这正是"分块"在本项目安全的前提，不是通用结论）。

分块大小取 200：一次提交 ≈ 400 条语句，持锁窗口从"数十秒"降到"亚秒级"；
6000 条 = 30 次提交，提交开销远低于逐条提交。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

#: 分块提交的粒度（已处理条数）。改这个值要同步 `docs/04` 的性能口径说明。
BATCH_COMMIT_CHUNK = 200


def commit_every(db: Session, done: int, chunk: int | None = None) -> bool:
    """每处理满 `chunk` 条提交一次（`done` = 已处理条数，从 1 开始）。返回本次是否提交。

    `chunk` 缺省取模块常量 `BATCH_COMMIT_CHUNK`——**在调用时解析**（不是定义时绑定），
    这样回归用例可以临时把粒度调小来观测"确实分块提交了"（见
    `tests/unit/test_audit8_d2_batch_20261020.py`）。
    调用方仍应在批次尾部保留一次最终 `commit()`（收尾未满一块的尾巴）。
    """
    size = BATCH_COMMIT_CHUNK if chunk is None else chunk
    if done > 0 and size > 0 and done % size == 0:
        db.commit()
        return True
    return False


def fetch_by_ids(db: Session, model, ids: list[int]) -> list:
    """一次 `IN` 批量取行（空列表直接返回空，避免 `IN ()` 语法错）。"""
    if not ids:
        return []
    return db.query(model).filter(model.id.in_(set(ids))).all()
