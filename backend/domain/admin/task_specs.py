# backend/domain/admin/task_specs.py — 任务看板数据组装（从 admin/service.py 抽出）
"""为什么单开：`admin/service.py` 触及架构关 god-file 上限（>800 行）。

任务列表 + 最近一次运行 + **连续失败次数**（2026-10-12 拍板 P10：一次成功不该把连续失败洗白）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.notification_models import TaskRunLog  # noqa: F401 — 类型/常量引用


def build_specs(db: Session) -> list[dict]:
    """任务清单 + 每任务最近一次运行 + 连续失败次数。"""
    from backend.tasks.registry import list_task_specs

    specs = list_task_specs()
    # 每个任务的最新一条运行记录（Q2 裁决：last_run join；空值前端显示"从未运行"）
    by_task: dict[str, dict] = {}
    rows = (
        db.query(TaskRunLog)
        .filter(TaskRunLog.is_deleted == 0)
        .order_by(TaskRunLog.id.desc())
        .limit(500)
        .all()
    )
    for r in rows:
        by_task.setdefault(
            r.task_name,
            {
                "status": r.status,
                "processed": r.processed,
                "error": r.error,
                "started_at": r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else "",
            },
        )
    # 2026-10-12 拍板（P10）：**连续失败要在看板标红**——原先只有"最后一次状态"，
    # 一次成功就把连续失败盖掉了（失败三次最后一次碰巧成功 = 看板全绿）。
    history: dict[str, list[str]] = {}
    for r in rows:  # rows 已按 id desc（最新在前）
        history.setdefault(r.task_name, []).append(r.status)
    for s in specs:
        last = by_task.get(s["name"])
        s["last_run"] = last or None
        streak = 0
        for st in history.get(s["name"], []):
            if st == TaskRunLog.STATUS_FAILED:
                streak += 1
            else:
                break
        s["consecutive_failures"] = streak
    return specs
