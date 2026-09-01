# backend/tasks/registry.py — 定时任务注册表 + 运行包装（WM11，ADR-008）
"""进程内 APScheduler 注册表：任务逻辑在域 service（ADR-008），本模块只做注册与包装。

- 每个任务：TaskSpec(name, display_name, group, interval_seconds, fn: (Session)->int)
- run_task()：记 TaskRunLog（管理端任务看板）+ 失败捕获 + 手动触发统一入口
- 失败告警：TaskRunLog status=failed（管理端看板可见）；站内告警通知超管按需扩展
- 幂等：任务方法设计为"重跑无副作用"（状态已流转的不会再次命中）
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from backend.common.notification_models import TaskRunLog

logger = logging.getLogger(__name__)

# APScheduler 全局单例（BackgroundScheduler，非阻塞 daemon 线程）
_scheduler = None


@dataclass
class TaskSpec:
    name: str
    display_name: str
    group: str
    interval_seconds: int
    fn: Callable[[Session], int]


def _member_expire_check(db: Session) -> int:
    from backend.domain.identity.service import ChildService

    return ChildService(db).expire_due_members()


def _member_expire_remind(db: Session) -> int:
    from backend.domain.identity.service import ChildService

    return ChildService(db).member_expire_remind()


def _pending_evaluation_weekly(db: Session) -> int:
    from backend.domain.identity.service import ChildService

    return ChildService(db).pending_evaluation_weekly()


def _reservation_expire_check(db: Session) -> int:
    from backend.domain.reading.service import ReservationService

    return ReservationService(db).expire_due()


def _reservation_expire_remind(db: Session) -> int:
    from backend.domain.reading.service import ReservationService

    return ReservationService(db).expire_remind()


def _order_timeout_cancel(db: Session) -> int:
    from backend.domain.identity.service import OrderService

    return OrderService(db).cancel_timeout_orders()


def _transfer_expire_check(db: Session) -> int:
    from backend.domain.identity.transfer_service import TransferService

    return TransferService(db).expire_overdue()


def _transfer_expiring_warn(db: Session) -> int:
    from backend.domain.identity.transfer_service import TransferService

    return TransferService(db).transfer_expiring_warn()


def _book_due_remind(db: Session) -> int:
    from backend.domain.circulation.service import CirculationService

    return CirculationService(db).book_due_remind()


def _overdue_mark(db: Session) -> int:
    from backend.domain.circulation.service import CirculationService

    return CirculationService(db).overdue_mark()


def _activity_remind(db: Session) -> int:
    from backend.domain.activity.service import ActivityService

    return ActivityService(db).activity_remind()


def _activity_auto_finish(db: Session) -> int:
    from backend.domain.activity.service import ActivityService

    return ActivityService(db).activity_auto_finish()


def _first_activity_90d_remind(db: Session) -> int:
    from backend.domain.identity.service import OrderService

    return OrderService(db).first_activity_90d_remind()


# 13 项定时任务（WM13-4 新增 transfer_expiring_warn 后 12→13；周月报定时生成不在本批）
TASKS: dict[str, TaskSpec] = {
    "member_expire_check": TaskSpec(
        "member_expire_check", "会员过期落库", "会员", 300, _member_expire_check
    ),
    "member_expire_remind": TaskSpec(
        "member_expire_remind", "会员到期提醒", "会员", 3600, _member_expire_remind
    ),
    "pending_evaluation_weekly": TaskSpec(
        "pending_evaluation_weekly", "待评估每周名单", "会员", 3600, _pending_evaluation_weekly
    ),
    "reservation_expire_check": TaskSpec(
        "reservation_expire_check", "预约超时释放", "借阅", 300, _reservation_expire_check
    ),
    "reservation_expire_remind": TaskSpec(
        "reservation_expire_remind", "预约到期提醒", "借阅", 3600, _reservation_expire_remind
    ),
    "book_due_remind": TaskSpec("book_due_remind", "借阅到期提醒", "借阅", 3600, _book_due_remind),
    "overdue_mark": TaskSpec("overdue_mark", "逾期标记", "借阅", 300, _overdue_mark),
    "order_timeout_cancel": TaskSpec(
        "order_timeout_cancel", "订单超时取消", "资金", 600, _order_timeout_cancel
    ),
    "transfer_expire_check": TaskSpec(
        "transfer_expire_check", "转让超时取消", "会员", 600, _transfer_expire_check
    ),
    "transfer_expiring_warn": TaskSpec(
        "transfer_expiring_warn", "转让超时预警", "会员", 3600, _transfer_expiring_warn
    ),
    "activity_remind": TaskSpec("activity_remind", "活动开始提醒", "活动", 3600, _activity_remind),
    "activity_auto_finish": TaskSpec(
        "activity_auto_finish", "活动自动结束", "活动", 3600, _activity_auto_finish
    ),
    "first_activity_90d_remind": TaskSpec(
        "first_activity_90d_remind", "99元活动90天提醒", "会员", 86400, _first_activity_90d_remind
    ),
}


def _audit_manual_run(
    session, admin, spec: TaskSpec, status: str, processed: int, error: str = ""
) -> None:
    """手动触发审计（F3/C39）：调度器自动路径不审计，防刷爆。与 TaskRunLog 同事务。"""
    from backend.domain.catalog.audit_events import publish_audit

    publish_audit(
        session,
        admin=admin,
        action="task.manual_run",
        target_type="task",
        target_id=spec.name,
        detail={
            "display_name": spec.display_name,
            "status": status,
            "processed": processed,
            **({"error": error} if error else {}),
        },
        reason="看板手动触发" if status == "success" else "看板手动触发（失败）",
    )


def run_task(task_name: str, manual: bool = False, admin=None) -> dict:
    """执行单个任务（调度器与手动触发共用入口）：记 TaskRunLog + 失败捕获。
    manual=True（馆员手动触发）写审计 task.manual_run（F3/C39）。"""
    spec = TASKS.get(task_name)
    if not spec:
        raise KeyError(f"任务不存在: {task_name}")
    from backend.database import get_session

    session = get_session()
    log = TaskRunLog(
        task_name=task_name, started_at=datetime.now(), status=TaskRunLog.STATUS_RUNNING
    )
    try:
        session.add(log)
        session.flush()
        processed = spec.fn(session)
        log.status = TaskRunLog.STATUS_SUCCESS
        log.finished_at = datetime.now()
        log.processed = processed or 0
        if manual and admin is not None:
            _audit_manual_run(session, admin, spec, "success", processed or 0)
        session.commit()
        return {
            "task": task_name,
            "display_name": spec.display_name,
            "status": "success",
            "processed": processed or 0,
        }
    except Exception as exc:
        session.rollback()
        logger.error("task %s failed: %s", task_name, exc, exc_info=True)
        try:
            fail_log = TaskRunLog(
                task_name=task_name,
                started_at=datetime.now(),
                finished_at=datetime.now(),
                status=TaskRunLog.STATUS_FAILED,
                error=str(exc)[:2000],
            )
            session.add(fail_log)
            if manual and admin is not None:
                _audit_manual_run(session, admin, spec, "failed", 0, error=str(exc)[:2000])
            session.commit()
        except Exception:
            session.rollback()
            logger.error("failed to persist task failure log for %s", task_name)
        return {
            "task": task_name,
            "display_name": spec.display_name,
            "status": "failed",
            "error": str(exc)[:2000],
        }
    finally:
        session.close()


def start_scheduler() -> None:
    """main.py lifespan 调用：进程内 BackgroundScheduler 注册全部任务。"""
    global _scheduler
    if _scheduler is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    for spec in TASKS.values():
        scheduler.add_job(
            run_task,
            trigger="interval",
            args=[spec.name],
            seconds=spec.interval_seconds,
            id=spec.name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )
    scheduler.start()
    _scheduler = scheduler
    logger.info("APScheduler started with %d tasks", len(TASKS))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def list_task_specs() -> list[dict]:
    return [
        {
            "name": s.name,
            "display_name": s.display_name,
            "group": s.group,
            "interval_seconds": s.interval_seconds,
        }
        for s in TASKS.values()
    ]
