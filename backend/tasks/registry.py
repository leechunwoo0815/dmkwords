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
    #: 可选 cron（分 时 日 月 周，Asia/Shanghai）：给"必须固定钟点跑"的任务用
    #: （例：媒体体检要**每天早上**出一份报告——用 interval 会随每次重启向后漂移）。
    #: 为 None 时按 interval_seconds 跑；interval_seconds 仍要填，它是看板"执行周期"的展示源。
    cron_expr: str | None = None


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


def _circle_rank_snapshot(db: Session) -> int:
    """WM14-B：周榜快照（结算上一完整自然周）。interval 每小时自检，
    靠 (child_id, week_start) 唯一索引实现「每周一次」幂等。"""
    from backend.domain.reading_circle.snapshot_service import CircleSnapshotService

    return CircleSnapshotService(db).run_weekly_snapshot()


def _circle_image_cleanup(db: Session) -> int:
    """WM14-B：清理已删帖超过 30 天的卡片图物理文件（Q13 二期挂账）。"""
    from backend.domain.reading_circle.admin_service import CircleImageCleanupService

    return CircleImageCleanupService(db).cleanup_orphan_images()


def _media_census(db: Session) -> int:
    """媒体体检（docs/15 §二十二）：盘点孤儿图 + 回收站对账 + 到期处理。

    返回值 = **孤儿图张数**——就是管理端任务看板上那个"数字"。
    顺序不能换：先**对账**（把清场重建抹掉记账的文件重新管起来），再**到期清除**（清除前复检引用，
    又被引用的自动还原）——五道防线的第 5/6 道。
    """
    from backend.domain.admin.media_models import MediaCensus
    from backend.domain.admin.media_service import MediaHealthService

    service = MediaHealthService(db)
    report = service.record_census(MediaCensus.TRIGGER_SCHEDULED)
    service.reconcile_trash()
    service.purge_expired()
    return int(report["orphan_files"])


# 16 项定时任务（WM13-4 新增 transfer_expiring_warn 后 12→13；WM14-B 新增
# circle_rank_snapshot / circle_image_cleanup 后 13→15；2026-09-23 新增 media_census 后 15→16；
# 周月报定时生成不在本批）
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
    # WM14-B：阅读圈周榜快照（每小时自检；(child_id, week_start) 唯一索引保证每周一次）
    "circle_rank_snapshot": TaskSpec(
        "circle_rank_snapshot", "阅读圈周榜快照", "阅读圈", 3600, _circle_rank_snapshot
    ),
    # WM14-B：孤儿卡片图清理（每日；30 天阈值下多跑无害）
    "circle_image_cleanup": TaskSpec(
        "circle_image_cleanup", "阅读圈卡片图清理", "阅读圈", 86400, _circle_image_cleanup
    ),
    # 媒体体检（2026-09-23 用户裁定；docs/15 §二十二）：每天早上 08:00 盘点孤儿图并出报告
    # ——"数字悄悄变大"要能被人看见，这是这个任务的唯一目的（只看不删；清理由超管在管理端点按钮）
    "media_census": TaskSpec(
        "media_census",
        "媒体体检（孤儿图盘点+回收站到期）",
        "系统",
        86400,
        _media_census,
        cron_expr="0 8 * * *",
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
        job_kwargs: dict = {
            "args": [spec.name],
            "id": spec.name,
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": CRON_MISFIRE_GRACE_SECONDS if spec.cron_expr else 120,
        }
        if spec.cron_expr:
            # 固定钟点任务（例：媒体体检每天 08:00）——interval 会随每次重启向后漂移
            from apscheduler.triggers.cron import CronTrigger

            job_kwargs["trigger"] = CronTrigger.from_crontab(
                spec.cron_expr, timezone="Asia/Shanghai"
            )
        else:
            job_kwargs["trigger"] = "interval"
            job_kwargs["seconds"] = spec.interval_seconds
        scheduler.add_job(run_task, **job_kwargs)
    scheduler.start()
    _scheduler = scheduler
    _schedule_cron_catchup(scheduler)
    logger.info("APScheduler started with %d tasks", len(TASKS))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


#: cron 任务的"迟到宽限"（秒）。机器休眠会把钟点任务记成 missed，
#: 超过宽限就被 APScheduler 直接丢弃——2026-09-24 实测：Mac 睡过 08:00，
#: 08:00 那场媒体体检被漏掉（日志 `Run time of job ... was missed by 0:04:04`），
#: 于是"每天早上出一份报告"成了空话。给 12 小时：当天醒来/开机还能补上。
CRON_MISFIRE_GRACE_SECONDS = 12 * 3600

#: 启动补跑延迟（秒）：进程启动时若发现"今天该跑的 cron 还没跑"，等这么久再补。
CRON_CATCHUP_DELAY_SECONDS = 30


def cron_due_today(cron_expr: str, now: datetime) -> datetime | None:
    """今天的钟点任务应跑时刻；已过则返回该时刻，未到或表达式不支持则 None。

    只解析标准 5 段的第一、二段（分 时）——本项目 cron 全是 `分 时 日 月 周` 的日级任务。
    """
    fields = cron_expr.split()
    if len(fields) != 5:
        return None
    try:
        minute, hour = int(fields[0]), int(fields[1])
    except ValueError:
        return None
    if "*" in fields[0] or "*" in fields[1] or "," in fields[0] or "/" in fields[0]:
        return None  # 非"每天固定钟点"形态：交给调度器，不做补跑判断
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return due if now >= due else None


def _schedule_cron_catchup(scheduler) -> None:
    """启动补跑：cron 任务今天该跑而没跑（休眠错过 / 进程当时不在）→ 启动后补一次。

    与 `CRON_MISFIRE_GRACE_SECONDS` 互补：宽限管"进程一直在、只睡了"，
    本函数管"进程当时根本没起来"（开发机天天 restart 就是这种）。判据是**当日**是否
    已有该任务的成功记录（TaskRunLog），查库失败只告警不阻断启动。
    """
    from datetime import timedelta

    now = datetime.now()
    for spec in TASKS.values():
        if not spec.cron_expr:
            continue
        due = cron_due_today(spec.cron_expr, now)
        if due is None:
            continue
        try:
            from backend.common.notification_models import TaskRunLog
            from backend.database import get_session

            session = get_session()
            try:
                done = (
                    session.query(TaskRunLog)
                    .filter(
                        TaskRunLog.task_name == spec.name,
                        TaskRunLog.status == TaskRunLog.STATUS_SUCCESS,
                        TaskRunLog.started_at >= due,
                    )
                    .first()
                )
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 — 启动期不因查库失败而崩
            logger.warning("cron 补跑检查跳过（查库失败）: %s", exc)
            continue
        if done:
            continue
        scheduler.add_job(
            run_task,
            trigger="date",
            run_date=now + timedelta(seconds=CRON_CATCHUP_DELAY_SECONDS),
            args=[spec.name],
            id=f"{spec.name}-catchup",
            max_instances=1,
        )
        logger.info(
            "cron 任务 %s 今日 %s 未跑（休眠或进程当时不在），%d 秒后补跑",
            spec.name,
            due.strftime("%H:%M"),
            CRON_CATCHUP_DELAY_SECONDS,
        )


def _schedule_text(spec: TaskSpec) -> str:
    """看板"执行周期"列的可读文案（cron 优先，否则按 interval 折算）。"""
    if spec.cron_expr:
        fields = spec.cron_expr.split()
        if len(fields) == 5:
            return f"每天 {int(fields[1]):02d}:{int(fields[0]):02d}"
        return spec.cron_expr
    return format_interval(spec.interval_seconds)


def format_interval(seconds: int) -> str:
    if seconds >= 86400:
        return "每天"
    if seconds >= 3600:
        return f"每 {seconds / 3600:g} 小时"
    return f"每 {seconds / 60:g} 分钟"


def list_task_specs() -> list[dict]:
    return [
        {
            "name": s.name,
            "display_name": s.display_name,
            "group": s.group,
            "interval_seconds": s.interval_seconds,
            "cron_expr": s.cron_expr,
            "schedule_text": _schedule_text(s),
        }
        for s in TASKS.values()
    ]
