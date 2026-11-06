# backend/tasks/scheduler.py
"""
[What] 定时任务调度器
[Why] 会员到期提醒、学习报告生成
[How] 使用APScheduler，启动时注册所有定时任务
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func as sql_func
from sqlalchemy.orm import Session

from backend.common.distributed_lock import distributed_lock
from backend.common.types import MemberStatus

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def init_scheduler(app):
    """
    [What] 初始化定时任务调度器
    [Why] FastAPI lifespan中调用
    [How] 注册所有任务并启动
    """
    # 每天早上9点：检查会员到期提醒
    scheduler.add_job(
        check_member_expiry,
        CronTrigger(hour=9, minute=0),
        id="check_member_expiry",
        replace_existing=True,
    )

    # 每周一早上8点：生成周报
    scheduler.add_job(
        generate_weekly_reports,
        CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="generate_weekly_reports",
        replace_existing=True,
    )

    # 每月1号早上8:15：生成月报（F-037：与周一 8:00 周报错峰，
    # 避免周一恰逢 1 号时两个重任务并行争锁）
    scheduler.add_job(
        generate_monthly_reports,
        CronTrigger(day=1, hour=8, minute=15),
        id="generate_monthly_reports",
        replace_existing=True,
    )

    # 每天凌晨2点：检查缓冲期关停
    scheduler.add_job(
        check_grace_period_shutdown,
        CronTrigger(hour=2, minute=0),
        id="check_grace_period_shutdown",
        replace_existing=True,
    )

    # 每分钟：订单30分钟未支付自动关闭
    scheduler.add_job(
        close_expired_orders,
        CronTrigger(minute="*/1"),
        id="close_expired_orders",
        replace_existing=True,
    )

    # 每5分钟：活动状态自动迁移
    scheduler.add_job(
        migrate_activity_status,
        CronTrigger(minute="*/5"),
        id="migrate_activity_status",
        replace_existing=True,
    )

    # 每天11点：晋级待审超过7天提醒
    scheduler.add_job(
        remind_pending_submissions,
        CronTrigger(hour=11, minute=0),
        id="remind_pending_submissions",
        replace_existing=True,
    )

    # 每天12点：退款7天未到账告警
    scheduler.add_job(
        alert_stale_refunds,
        CronTrigger(hour=12, minute=0),
        id="alert_stale_refunds",
        replace_existing=True,
    )

    # 每天凌晨0点：损坏报告过期自动确认
    scheduler.add_job(
        confirm_expired_damage_reports,
        CronTrigger(hour=0, minute=0),
        id="confirm_expired_damage_reports",
        replace_existing=True,
    )

    # 每天凌晨1点：借阅到期提醒
    scheduler.add_job(
        check_due_date_reminders,
        CronTrigger(hour=1, minute=0),
        id="check_due_date_reminders",
        replace_existing=True,
    )

    # 每30分钟：预约过期检查
    scheduler.add_job(
        expire_reservations,
        CronTrigger(minute="*/30"),
        id="expire_reservations",
        replace_existing=True,
    )

    # 每小时：预约取书提醒（B4：到期前24h未取 → 提醒）
    scheduler.add_job(
        remind_reservation_pickup,
        CronTrigger(minute=45),
        id="remind_reservation_pickup",
        replace_existing=True,
    )

    # 每天9点20分：人工审核 SLA 巡检（E2：超24h未审升级提醒超管）
    scheduler.add_job(
        audit_sla_escalation,
        CronTrigger(hour=9, minute=20),
        id="audit_sla_escalation",
        replace_existing=True,
    )

    # 每天凌晨4点：满15岁毕业流程（F2）
    scheduler.add_job(
        graduate_children,
        CronTrigger(hour=4, minute=0),
        id="graduate_children",
        replace_existing=True,
    )

    # 每天凌晨2点30分：逾期检测
    scheduler.add_job(
        mark_overdue_books,
        CronTrigger(hour=2, minute=30),
        id="mark_overdue_books",
        replace_existing=True,
    )

    # 每天早上9点：观察期到期提醒
    scheduler.add_job(
        check_observation_reminders,
        CronTrigger(hour=9, minute=0),
        id="check_observation_reminders",
        replace_existing=True,
    )

    # 每天凌晨4点15分：观察期到期检查（L2-018：由 09:30 白天重任务下移，
    # 避开 04:00 graduate_children / 04:30 purge_expired_data 错峰）
    scheduler.add_job(
        check_observation_expiry,
        CronTrigger(hour=4, minute=15),
        id="check_observation_expiry",
        replace_existing=True,
    )

    # 每天早上10点：活动开始前3天提醒
    scheduler.add_job(
        check_activity_reminders,
        CronTrigger(hour=10, minute=0),
        id="check_activity_reminders",
        replace_existing=True,
    )

    # 每天凌晨3点：库存双口径对账
    scheduler.add_job(
        reconcile_stock,
        CronTrigger(hour=3, minute=0),
        id="reconcile_stock",
        replace_existing=True,
    )

    # 每天凌晨3点半：执行冷静期已过的儿童数据级联删除
    scheduler.add_job(
        execute_child_deletions,
        CronTrigger(hour=3, minute=30),
        id="execute_child_deletions",
        replace_existing=True,
    )

    # 每天凌晨3点45：统计字段对账
    scheduler.add_job(
        reconcile_child_stats,
        CronTrigger(hour=3, minute=45),
        id="reconcile_child_stats",
        replace_existing=True,
    )

    # 每天凌晨4点30：数据保留期到期清理（H5：消息1年/行为退出后2年/财务5年/语音6个月）
    scheduler.add_job(
        purge_expired_data,
        CronTrigger(hour=4, minute=30),
        id="purge_expired_data",
        replace_existing=True,
    )

    # 每天凌晨4点40：清理孤儿上传分片会话（与 purge 错峰）
    scheduler.add_job(
        cleanup_orphan_chunks,
        CronTrigger(hour=4, minute=40),
        id="cleanup_orphan_chunks",
        replace_existing=True,
    )

    # 每天凌晨5点：支付成功但会员未激活对账（F7：告警超管 + 人工队列，PRD §1.2 定时修复）
    scheduler.add_job(
        check_paid_not_activated,
        CronTrigger(hour=5, minute=0),
        id="check_paid_not_activated",
        replace_existing=True,
    )

    # 每天凌晨1:30：废弃押金支付单（PENDING 超时未回调）复位 UNPAID
    scheduler.add_job(
        reset_stale_pending_deposits,
        CronTrigger(hour=1, minute=30),
        id="reset_stale_pending_deposits",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(f"Scheduler started with {len(scheduler.get_jobs())} jobs")


def stop_scheduler():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def _get_db_session():
    """获取数据库会话"""
    from backend.database import get_session

    return get_session()()


def _create_message(
    db, user_id: int, title: str, content: str, msg_type: int = 1, priority: int = 0
):
    """写入系统消息"""
    from backend.domain.message.models import SystemMessage

    msg = SystemMessage(
        user_id=user_id,
        title=title,
        content=content,
        msg_type=msg_type,
        priority=priority,
    )
    db.add(msg)


@distributed_lock("job:reconcile_stock", timeout=300)
def reconcile_stock(db: Session | None = None):
    """每日库存对账：Book.total_stock/available_stock 与 BookCopy 实际计数对齐

    参数 db：可选的 session 注入（测试用），不传则自行创建。
    """
    import json

    from backend.common.types import BookCopyStatus
    from backend.domain.book.models import Book, BookCopy

    valid_statuses = (
        BookCopyStatus.AVAILABLE,
        BookCopyStatus.BORROWED,
        BookCopyStatus.MAINTENANCE,
        BookCopyStatus.DAMAGED,
    )
    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        # 一次查询所有图书的副本计数（避免 N+1）
        total_counts = dict(
            db.query(BookCopy.book_id, sql_func.count(BookCopy.id))
            .filter(
                BookCopy.is_deleted == 0,
                BookCopy.status.in_(valid_statuses),
            )
            .group_by(BookCopy.book_id)
            .all()
        )
        avail_counts = dict(
            db.query(BookCopy.book_id, sql_func.count(BookCopy.id))
            .filter(
                BookCopy.is_deleted == 0,
                BookCopy.status == BookCopyStatus.AVAILABLE,
            )
            .group_by(BookCopy.book_id)
            .all()
        )

        # L3-035：行锁 Book 防对账用过期快照覆盖并发的借还/预约库存变更（丢失更新）
        books = db.query(Book).filter(Book.is_deleted == 0).with_for_update().all()
        fixed = 0
        for book in books:
            total_count = total_counts.get(book.id, 0)
            avail_count = avail_counts.get(book.id, 0)
            if book.total_stock != total_count or book.available_stock != avail_count:
                detail = json.dumps(
                    {
                        "book_id": book.id,
                        "expected": {
                            "total_stock": total_count,
                            "available_stock": avail_count,
                        },
                        "actual": {
                            "total_stock": book.total_stock,
                            "available_stock": book.available_stock,
                        },
                    },
                    ensure_ascii=False,
                )
                from backend.domain.admin.models import OperationLog

                log = OperationLog(
                    admin_id=0,
                    module="book",
                    operation="stock_reconciliation",
                    content=detail,
                )
                db.add(log)
                book.total_stock = total_count
                book.available_stock = avail_count
                fixed += 1
        db.commit()
        if fixed:
            logger.info(f"Stock reconciliation: {fixed} books fixed")
        else:
            logger.info("Stock reconciliation: all consistent")
    except Exception as e:
        logger.error(f"Stock reconciliation failed: {e}", exc_info=True)
        db.rollback()
    finally:
        if own_session:
            db.close()


@distributed_lock("job:execute_child_deletions", timeout=600)
def execute_child_deletions(db: Session | None = None):
    """每天凌晨3:30：执行冷静期（24h）已过的儿童数据级联删除（P0-3 删除权）

    参数 db：可选的 session 注入（测试用），不传则自行创建。
    """
    from backend.domain.child.deletion_service import ChildDeletionService

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        result = ChildDeletionService(db).execute_due_deletions()
        if result["due"]:
            logger.info(f"Child deletions executed: {result['executed']}/{result['due']}")
    except Exception as e:
        logger.error(f"Child deletion job failed: {e}", exc_info=True)
        db.rollback()
    finally:
        if own_session:
            db.close()


@distributed_lock("job:reconcile_child_stats", timeout=600)
def reconcile_child_stats(db: Session | None = None):
    """每日统计字段对账：child 统计字段与源表重算对齐

    对账口径：
      - total_words_read = 通过测验（score >= quiz_pass_rate×100，同一 child+book 只计一次）
        的图书 word_count 之和
      - total_reading_minutes = reading_session.duration_seconds 之和 // 60
      - total_books_finished = reading_progress(is_finished=1) 条数（P1-3 修正）
      - current_streak_days = 从今天（或昨天）向前连续打卡天数
      - longest_streak_days = max(现存值, 全量打卡日期最长连续段)（只升不降，保护历史）
    偏差修正并记录 operation_log。
    """
    from backend.common.config_service import ConfigService
    from backend.domain.advancement.models import Quiz
    from backend.domain.book.models import Book
    from backend.domain.child.models import Child
    from backend.domain.reading.models import CheckIn, ReadingSession

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        pass_rate = ConfigService.get_decimal(db, "quiz_pass_rate", Decimal("0.80"))
        # 保持 Decimal 与 Quiz.score(Numeric) 同精度比较，避免 float 边界偏差（审查 P2-2）
        pass_score = (pass_rate * 100).quantize(Decimal("0.01"))

        # F-036：增量窗口（近 7 天）——每日只重算近期有数据变更的孩子；
        # 每月 1 日全量兜底（历史数据修复/管理员改老记录仍会被对账修正）。
        # streak：longest 需历史全量连续段（只升不降），但 L2-016 优化——
        # 增量日只查窗口内有打卡的孩子（候选收窄），再按 child_id 索引取候选全量历史；
        # 全量日（1 号）保持全表 distinct。行为不变（同一孩子计算结果一致）。
        full_reconcile = date.today().day == 1
        cutoff = datetime.now() - timedelta(days=7)

        # words：通过测验的去重 (child, book) × word_count
        words_query = db.query(Quiz.child_id, Quiz.book_id).filter(
            Quiz.status == Quiz.STATUS_COMPLETED,
            Quiz.score >= pass_score,
            Quiz.is_deleted == 0,
        )
        if not full_reconcile:
            words_query = words_query.filter(Quiz.create_time >= cutoff)
        pairs = words_query.distinct().subquery()
        words_map = dict(
            db.query(pairs.c.child_id, sql_func.sum(Book.word_count))
            .join(Book, Book.id == pairs.c.book_id)
            .group_by(pairs.c.child_id)
            .all()
        )

        # minutes：阅读会话总秒数 // 60
        minutes_query = db.query(
            ReadingSession.child_id, sql_func.sum(ReadingSession.duration_seconds)
        ).filter(ReadingSession.is_deleted == 0)
        if not full_reconcile:
            minutes_query = minutes_query.filter(ReadingSession.start_time >= cutoff)
        minutes_map = {
            cid: int((secs or 0) // 60)
            for cid, secs in minutes_query.group_by(ReadingSession.child_id).all()
        }

        # books：累计读完本数（P1-3 修正：以 ReadingProgress.is_finished=1 为准，
        # 此前误用 TYPE_FINISH_BOOK 打卡条数——打卡每日每类型仅 1 次，会低估本数）
        from backend.domain.reading.models import ReadingProgress

        books_query = db.query(ReadingProgress.child_id, sql_func.count(ReadingProgress.id)).filter(
            ReadingProgress.is_finished == 1,
            ReadingProgress.is_deleted == 0,
        )
        if not full_reconcile:
            books_query = books_query.filter(ReadingProgress.create_time >= cutoff)
        books_map = dict(books_query.group_by(ReadingProgress.child_id).all())

        # streak：全量日扫全表；增量日先收窄窗口内有打卡的候选，再取候选全量历史
        if full_reconcile:
            date_rows = (
                db.query(CheckIn.child_id, sql_func.date(CheckIn.check_date))
                .filter(CheckIn.is_deleted == 0)
                .distinct()
                .all()
            )
        else:
            candidates = [
                r[0]
                for r in db.query(CheckIn.child_id)
                .filter(
                    CheckIn.is_deleted == 0,
                    CheckIn.create_time >= cutoff,
                )
                .distinct()
                .all()
            ]
            if candidates:
                date_rows = (
                    db.query(CheckIn.child_id, sql_func.date(CheckIn.check_date))
                    .filter(
                        CheckIn.is_deleted == 0,
                        CheckIn.child_id.in_(candidates),
                    )
                    .distinct()
                    .all()
                )
            else:
                date_rows = []
        dates_by_child: dict[int, set] = {}
        for cid, d in date_rows:
            if d is None:
                continue
            if isinstance(d, str):
                d = date.fromisoformat(d)
            elif isinstance(d, datetime):
                d = d.date()
            dates_by_child.setdefault(cid, set()).add(d)

        def _streaks(dates: set) -> tuple[int, int]:
            if not dates:
                return 0, 0
            today = date.today()
            current = 0
            cursor = today if today in dates else (today - timedelta(days=1))
            if cursor in dates:
                while cursor in dates:
                    current += 1
                    cursor -= timedelta(days=1)
            longest = run = 1
            prev = None
            for d in sorted(dates):
                if prev is not None and (d - prev).days == 1:
                    run += 1
                    longest = max(longest, run)
                else:
                    run = 1
                prev = d
            return current, longest

        fixed = 0
        if full_reconcile:
            # 全量：所有孩子都参与，未聚合到 map 的字段视为 0（对齐历史语义）
            children = db.query(Child).filter(Child.is_deleted == 0).all()
        else:
            # 增量：只对"窗口内有数据变更"的孩子做对账，且只修正本次聚合到的字段——
            # 窗口外孩子的历史字段不受影响（F-036：避免把未聚合字段误清零）
            affected_ids = set(words_map) | set(minutes_map) | set(books_map) | set(dates_by_child)
            children = (
                db.query(Child)
                .filter(
                    Child.is_deleted == 0,
                    Child.id.in_(affected_ids),
                )
                .all()
            )
        for child in children:
            expected_words = int(words_map.get(child.id, 0))
            expected_minutes = minutes_map.get(child.id, 0)
            expected_books = int(books_map.get(child.id, 0))
            cur_streak, longest_run = _streaks(dates_by_child.get(child.id, set()))

            deviations = []
            if (child.id in words_map or full_reconcile) and (
                child.total_words_read or 0
            ) != expected_words:
                deviations.append(f"words {child.total_words_read or 0}→{expected_words}")
                child.total_words_read = expected_words
            if (child.id in minutes_map or full_reconcile) and (
                child.total_reading_minutes or 0
            ) != expected_minutes:
                deviations.append(f"minutes {child.total_reading_minutes or 0}→{expected_minutes}")
                child.total_reading_minutes = expected_minutes
            if (child.id in books_map or full_reconcile) and (
                child.total_books_finished or 0
            ) != expected_books:
                deviations.append(f"books {child.total_books_finished or 0}→{expected_books}")
                child.total_books_finished = expected_books
            if (child.current_streak_days or 0) != cur_streak:
                deviations.append(f"streak {child.current_streak_days or 0}→{cur_streak}")
                child.current_streak_days = cur_streak
            if longest_run > (child.longest_streak_days or 0):
                deviations.append(f"longest {child.longest_streak_days or 0}→{longest_run}")
                child.longest_streak_days = longest_run

            if deviations:
                from backend.domain.admin.models import OperationLog

                db.add(
                    OperationLog(
                        admin_id=0,
                        module="child",
                        operation="stats_reconciliation",
                        content=f"child={child.id} " + ", ".join(deviations),
                    )
                )
                fixed += 1

        db.commit()
        logger.info(f"Child stats reconciliation: {fixed} children fixed")
    except Exception as e:
        logger.error(f"Child stats reconciliation failed: {e}", exc_info=True)
        db.rollback()
    finally:
        if own_session:
            db.close()


@distributed_lock("job:check_member_expiry", timeout=600)
def check_member_expiry(db: Session | None = None):
    """
    [What] 会员到期提醒
    [Why] 正式会员到期前提醒续费
    [How] 一次查询即将到期的正式会员，按到期日分组写入消息表

    参数 db：可选的 session 注入（测试用），不传则自行创建。
    """
    from collections import defaultdict

    from backend.domain.child.models import Child

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        from backend.common.config_service import ConfigService

        today = date.today()
        notify_days = ConfigService.get_int_list(
            db, "member_expire_remind_days", [30, 15, 7, 3, 2, 1, 0]
        )

        if not notify_days:
            return

        # 一次查询所有到期日在提醒范围内的孩子（避免 N 次循环查询）
        max_days = max(notify_days)
        min_days = min(notify_days)
        date_upper = today + timedelta(days=max_days)
        date_lower = today + timedelta(days=min_days)
        # F-015：范围比较替代 func.date 包裹（索引失效全表扫描）
        lower_dt = datetime.combine(date_lower, datetime.min.time())
        upper_dt = datetime.combine(date_upper + timedelta(days=1), datetime.min.time())

        children = (
            db.query(Child)
            .filter(
                Child.status == MemberStatus.OFFICIAL,
                Child.member_expire_time.isnot(None),
                Child.member_expire_time >= lower_dt,
                Child.member_expire_time < upper_dt,
                Child.is_deleted == 0,
            )
            .all()
        )

        # 按到期日期分组
        children_by_date: dict[date, list] = defaultdict(list)
        for child in children:
            expire_date = child.member_expire_time.date()
            children_by_date[expire_date].append(child)

        for days in notify_days:
            target_date = today + timedelta(days=days)
            for child in children_by_date.get(target_date, []):
                _create_message(
                    db,
                    user_id=child.user_id,
                    title="会员续费提醒",
                    content=f"您的孩子 {child.name} 的正式会员将在{days}天后（{target_date}）到期，请及时续费以免影响阅读。",
                    msg_type=1,  # 系统通知
                    priority=1 if days <= 3 else 0,
                )
                logger.info(
                    f"MEMBER_EXPIRY: child={child.id}, name={child.name}, "
                    f"expires={target_date}, days_left={days}"
                )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception(f"check_member_expiry failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:check_grace_period_shutdown", timeout=600)
def check_grace_period_shutdown():
    """
    [What] 检查缓冲期关停
    [Why] 到期15天后自动关停账号
    [How] 查询已过期超过15天的会员，标记为已过期
    """
    from backend.domain.child.models import Child

    db = _get_db_session()
    try:
        # 从配置读取缓冲期天数
        from backend.common.config_service import ConfigService

        grace_days = ConfigService.get_int(db, "member_grace_days", 15)
        cutoff = datetime.now() - timedelta(days=grace_days)

        expired = (
            db.query(Child)
            .filter(
                Child.status == MemberStatus.OFFICIAL,
                Child.member_expire_time.isnot(None),
                Child.member_expire_time < cutoff,
                Child.is_deleted == 0,
            )
            .all()
        )

        for child in expired:
            # F-046：写前重取+行锁+状态守卫（防与续费并发把新会员覆盖为 EXPIRED）
            child = (
                db.query(Child)
                .filter(
                    Child.id == child.id,
                    Child.status == MemberStatus.OFFICIAL,
                    Child.is_deleted == 0,
                )
                .with_for_update()
                .first()
            )
            if child is None or child.member_expire_time >= cutoff:
                continue
            old_status = child.status
            child.status = MemberStatus.EXPIRED
            if child.exited_at is None:
                # F-105：EXPIRED 也写退出时间（流失统计口径；purge 有 status 过滤不受影响）
                child.exited_at = datetime.now()
            logger.info(
                f"GRACE_SHUTDOWN: child={child.id}, name={child.name}, {old_status} -> {MemberStatus.EXPIRED}"
            )

        if expired:
            db.commit()
            logger.info(f"Grace period shutdown: {len(expired)} children expired")
    except Exception as e:
        db.rollback()
        logger.exception(f"check_grace_period_shutdown failed: {e}")
    finally:
        db.close()


@distributed_lock("job:generate_weekly_reports", timeout=600)
def generate_weekly_reports():
    """
    [What] 生成周报
    [Why] 每周一自动生成上周阅读报告
    [How] 查询每个child上周的阅读数据，生成报告
    """
    from backend.domain.child.models import Child
    from backend.domain.report.service import ReportService

    db = _get_db_session()
    try:
        children = (
            db.query(Child)
            .filter(
                Child.status.in_([MemberStatus.OBSERVATION, MemberStatus.OFFICIAL]),
                Child.is_deleted == 0,
            )
            .all()
        )

        svc = ReportService(db)
        count = 0
        for child in children:
            try:
                report = svc.generate_weekly_report(child.id)
                logger.info(
                    f"WEEKLY_REPORT: child={child.id}, minutes={report.total_minutes}, books={report.books_finished}"
                )
                # H3：周报生成后系统消息触达家长
                _create_message(
                    db,
                    user_id=child.user_id,
                    title="孩子的周报来啦",
                    content=(
                        f"{child.name}上周阅读 {report.total_minutes} 分钟，"
                        f"读完 {report.books_finished} 本书，点击查看完整周报～"
                    ),
                    msg_type=1,  # 系统通知
                    priority=1,
                )
                count += 1
            except Exception as e:
                logger.error(f"Weekly report failed for child {child.id}: {e}", exc_info=True)

        logger.info(f"Weekly reports generated: {count}")
        # L3-016：成功路径补 commit——此前报告/消息全部未落库（数据丢失）
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception(f"generate_weekly_reports failed: {e}")
    finally:
        db.close()


@distributed_lock("job:generate_monthly_reports", timeout=600)
def generate_monthly_reports():
    """
    [What] 生成月报 + 平台级月度统计
    [Why] 每月1日自动生成上月阅读报告，同时汇总平台级运营指标
    [How] 查询每个child上月的阅读数据；汇总新增用户/活跃用户/借阅TOP10/测评通过率/退费率
    """
    from backend.domain.advancement.models import Quiz
    from backend.domain.book.models import Book
    from backend.domain.borrow.models import BorrowRecord
    from backend.domain.child.models import Child
    from backend.domain.order.models import Order
    from backend.domain.reading.models import ReadingSession
    from backend.domain.report.service import ReportService
    from backend.domain.user.models import User

    db = _get_db_session()
    try:
        # 上月时间范围
        today = date.today()
        last_month_end = today.replace(day=1) - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        next_month_start = (last_month_start + timedelta(days=32)).replace(day=1)

        # === 平台级月度统计 ===

        # 新增用户数
        new_users = (
            db.query(sql_func.count(User.id))
            .filter(
                User.create_time >= last_month_start,
                User.create_time < next_month_start,  # F-035：避免漏月末当天
                User.is_deleted == 0,
            )
            .scalar()
            or 0
        )

        # 活跃用户数（上月有阅读会话的不同 child）
        active_users = (
            db.query(sql_func.count(sql_func.distinct(ReadingSession.child_id)))
            .filter(
                ReadingSession.start_time >= last_month_start,
                ReadingSession.start_time < next_month_start,  # F-035：避免漏月末当天
                ReadingSession.is_deleted == 0,
            )
            .scalar()
            or 0
        )

        # 借阅 TOP10（上月借阅量最多的书）
        top_books = (
            db.query(
                BorrowRecord.book_id,
                sql_func.count(BorrowRecord.id).label("borrow_count"),
            )
            .filter(
                BorrowRecord.create_time >= last_month_start,
                BorrowRecord.create_time < next_month_start,  # F-035：避免漏月末当天
                BorrowRecord.is_deleted == 0,
            )
            .group_by(BorrowRecord.book_id)
            .order_by(sql_func.count(BorrowRecord.id).desc())
            .limit(10)
            .all()
        )

        top_books_info = []
        for book_id, borrow_count in top_books:
            # F-038：月报 TOP10 过滤已下架图书（软删书不得展示）
            book = db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
            top_books_info.append(
                {
                    "book_id": book_id,
                    "title": book.title if book else "未知",
                    "borrow_count": borrow_count,
                }
            )

        # 测评通过率
        total_quizzes = (
            db.query(sql_func.count(Quiz.id))
            .filter(
                Quiz.status == Quiz.STATUS_COMPLETED,
                Quiz.create_time >= last_month_start,
                Quiz.create_time < next_month_start,  # F-035：避免漏月末当天
                Quiz.is_deleted == 0,
            )
            .scalar()
            or 0
        )
        passed_quizzes = (
            db.query(sql_func.count(Quiz.id))
            .filter(
                Quiz.status == Quiz.STATUS_COMPLETED,
                Quiz.score >= 70,
                Quiz.create_time >= last_month_start,
                Quiz.create_time < next_month_start,  # F-035：避免漏月末当天
                Quiz.is_deleted == 0,
            )
            .scalar()
            or 0
        )
        quiz_pass_rate = (
            round(passed_quizzes / total_quizzes * 100, 1) if total_quizzes > 0 else 0.0
        )

        # 退费率
        total_paid_orders = (
            db.query(sql_func.count(Order.id))
            .filter(
                Order.pay_status == 1,
                Order.create_time >= last_month_start,
                Order.create_time < next_month_start,  # F-035：避免漏月末当天
                Order.is_deleted == 0,
            )
            .scalar()
            or 0
        )
        refunded_orders = (
            db.query(sql_func.count(Order.id))
            .filter(
                Order.refund_status.in_(
                    [Order.REFUND_PROCESSING, Order.REFUND_DONE]
                ),  # F-078 终审：魔法数字改常量
                Order.create_time >= last_month_start,
                Order.create_time < next_month_start,  # F-035：避免漏月末当天
                Order.is_deleted == 0,
            )
            .scalar()
            or 0
        )
        refund_rate = (
            round(refunded_orders / total_paid_orders * 100, 1) if total_paid_orders > 0 else 0.0
        )

        logger.info(
            f"MONTHLY_PLATFORM_STATS: period={last_month_start}~{last_month_end}, "
            f"new_users={new_users}, active_users={active_users}, "
            f"quiz_pass_rate={quiz_pass_rate}%, refund_rate={refund_rate}%, "
            f"top_books={[b['title'] + '(' + str(b['borrow_count']) + ')' for b in top_books_info]}"
        )

        # === 逐 child 生成月报 ===
        children = (
            db.query(Child)
            .filter(
                Child.status.in_([MemberStatus.OBSERVATION, MemberStatus.OFFICIAL]),
                Child.is_deleted == 0,
            )
            .all()
        )

        svc = ReportService(db)
        count = 0
        for child in children:
            try:
                report = svc.generate_monthly_report(child.id)
                logger.info(
                    f"MONTHLY_REPORT: child={child.id}, minutes={report.total_minutes}, "
                    f"books={report.books_finished}, checkin_rate={report.checkin_rate}%"
                )
                # H3：月报生成后系统消息触达家长
                _create_message(
                    db,
                    user_id=child.user_id,
                    title="孩子的月报来啦",
                    content=(
                        f"{child.name}上月阅读 {report.total_minutes} 分钟，"
                        f"读完 {report.books_finished} 本书，点击查看完整月报～"
                    ),
                    msg_type=1,  # 系统通知
                    priority=1,
                )
                count += 1
            except Exception as e:
                logger.error(f"Monthly report failed for child {child.id}: {e}", exc_info=True)

        logger.info(f"Monthly reports generated: {count}")
        # L3-016：成功路径补 commit——此前报告/统计/消息全部未落库（数据丢失）
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception(f"generate_monthly_reports failed: {e}")
    finally:
        db.close()


@distributed_lock("job:close_expired_orders", timeout=120)
def close_expired_orders():
    """
    [What] 关闭超时未支付订单
    [Why] 下单后30分钟未支付自动关闭
    [How] 查询待支付且创建时间超过30分钟的订单
    """
    from backend.common.types import PayStatus
    from backend.domain.order.models import Order

    db = _get_db_session()
    try:
        # 从配置读取订单超时时间
        from backend.common.config_service import ConfigService

        timeout_minutes = ConfigService.get_int(db, "order_expire_minutes", 30)
        cutoff = datetime.now() - timedelta(minutes=timeout_minutes)
        expired_ids = [
            r[0]
            for r in db.query(Order.id)
            .filter(
                Order.pay_status == PayStatus.PENDING,
                Order.create_time < cutoff,
                Order.is_deleted == 0,
            )
            .all()
        ]
        if expired_ids:
            # F5 修复：条件更新带 pay_status=PENDING 前置，防"先付后关"竞态覆盖已支付订单
            closed = (
                db.query(Order)
                .filter(
                    Order.id.in_(expired_ids),
                    Order.pay_status == PayStatus.PENDING,
                )
                .update(
                    {Order.pay_status: PayStatus.CLOSED},
                    synchronize_session=False,
                )
            )
            db.commit()
            logger.info(f"Expired orders closed: {closed}")
    except Exception as e:
        db.rollback()
        logger.exception(f"close_expired_orders failed: {e}")
    finally:
        db.close()


@distributed_lock("job:migrate_activity_status", timeout=120)
def migrate_activity_status():
    """
    [What] 活动状态自动迁移
    [Why] 报名截止/进行中/结束 三个时间点自动切换状态
    [How] 根据当前时间与活动时间比较，自动迁移状态
    """
    from backend.domain.activity.models import Activity

    db = _get_db_session()
    try:
        now = datetime.now()
        migrated = 0

        # 报名中 → 报名截止（到达报名截止时间）
        activities = (
            db.query(Activity)
            .filter(
                Activity.status == Activity.STATUS_ENROLLING,
                Activity.enroll_deadline.isnot(None),
                Activity.enroll_deadline < now,
                Activity.is_deleted == 0,
            )
            .all()
        )
        for a in activities:
            if a.status != Activity.STATUS_ENROLLING:
                continue  # F-046：状态守卫（并发管理端改动则跳过）
            a.status = Activity.STATUS_ENROLL_CLOSED
            migrated += 1

        # 报名截止 → 进行中（到达开始时间）
        activities = (
            db.query(Activity)
            .filter(
                Activity.status == Activity.STATUS_ENROLL_CLOSED,
                Activity.start_time <= now,
                Activity.is_deleted == 0,
            )
            .all()
        )
        for a in activities:
            if a.status != Activity.STATUS_ENROLL_CLOSED:
                continue  # F-046：状态守卫
            a.status = Activity.STATUS_IN_PROGRESS
            migrated += 1

        # 进行中 → 已结束（到达结束时间）
        activities = (
            db.query(Activity)
            .filter(
                Activity.status == Activity.STATUS_IN_PROGRESS,
                Activity.end_time < now,
                Activity.is_deleted == 0,
            )
            .all()
        )
        for a in activities:
            if a.status != Activity.STATUS_IN_PROGRESS:
                continue  # F-046：状态守卫
            a.status = Activity.STATUS_FINISHED
            migrated += 1

        if migrated:
            db.commit()
            logger.info(f"Activity status migrated: {migrated} changes")
    except Exception as e:
        db.rollback()
        logger.exception(f"migrate_activity_status failed: {e}")
    finally:
        db.close()


@distributed_lock("job:remind_pending_submissions", timeout=120)
def remind_pending_submissions():
    """
    [What] 晋级待审超过7天提醒
    [Why] 提交审核超过7天未处理时提醒老师
    [How] 查询超过7天仍为待审核的提交记录，写入消息表
    """
    from backend.domain.advancement.models import ReadingSubmission
    from backend.domain.child.models import Child

    db = _get_db_session()
    try:
        cutoff = datetime.now() - timedelta(days=7)
        pending = (
            db.query(ReadingSubmission)
            .filter(
                ReadingSubmission.status == ReadingSubmission.STATUS_PENDING,
                ReadingSubmission.submitted_at < cutoff,
                ReadingSubmission.is_deleted == 0,  # F-038：软删提交不提醒
                ReadingSubmission.pending_reminded == 0,  # F-085：已提醒过不再重复
            )
            .all()
        )
        for s in pending:
            # F-038：软删孩子不提醒（家长已删除档案后不得再收到待审提醒）
            child = db.query(Child).filter(Child.id == s.child_id, Child.is_deleted == 0).first()
            if child and child.teacher_id:
                days = (datetime.now() - s.submitted_at).days
                _create_message(
                    db,
                    user_id=child.user_id,
                    title="待审核提醒",
                    content=f"孩子 {child.name} 的阅读提交已等待审核 {days} 天，请及时处理",
                    msg_type=5,
                    priority=1,
                )
                s.pending_reminded = 1  # F-085：标记已提醒（同一提交只提醒一次）
            logger.warning(f"STALE_SUBMISSION: id={s.id}, child={s.child_id}, book={s.book_id}")
        if pending:
            db.commit()
            logger.info(f"Stale submissions reminder: {len(pending)}")
    except Exception as e:
        db.rollback()
        logger.exception(f"remind_pending_submissions failed: {e}")
    finally:
        db.close()


@distributed_lock("job:alert_stale_refunds", timeout=120)
def alert_stale_refunds(db: Session | None = None):
    """
    [What] 退款7天未到账告警
    [Why] 审核通过超过7天仍未退款成功时告警
    [How] 查询退款状态为已批准且审核时间超过7天的记录，写入消息表
    """
    from backend.domain.refund.models import RefundApplication

    own_session = db is None
    db = db or _get_db_session()
    try:
        cutoff = datetime.now() - timedelta(days=7)
        stale = (
            db.query(RefundApplication)
            .filter(
                RefundApplication.status == RefundApplication.STATUS_APPROVED,
                RefundApplication.review_time < cutoff,
                RefundApplication.is_deleted == 0,
                RefundApplication.stale_alerted == 0,  # F-110：已告警过不再重复
            )
            .all()
        )
        for r in stale:
            logger.warning(
                f"STALE_REFUND: id={r.id}, order={r.order_id}, amount={r.refund_amount}, "
                f"review_time={r.review_time}, days_overdue={(datetime.now() - r.review_time).days}"
            )
            _create_message(
                db,
                user_id=0,  # F72：告警发给运营而非家长（E2 升级超管语义）
                title="退款超时告警（运营）",
                content=(
                    f"退款申请 #{r.id}（用户 {r.user_id}）已审核通过超过7天仍未到账，"
                    f"金额 {r.refund_amount} 元"
                ),
                msg_type=1,  # 系统通知
                priority=2,
            )
            r.stale_alerted = 1  # F-110：标记已告警（同一退款只告警一次）

        # F55：押金 REFUNDING 超时巡检——回退 REFUND_PENDING（可重试）+ 运营告警
        from backend.common.types import DepositStatus
        from backend.domain.deposit.models import DepositRecord

        stale_deposits = (
            db.query(DepositRecord)
            .filter(
                DepositRecord.status == DepositStatus.REFUNDING,
                DepositRecord.refund_time < cutoff,
                DepositRecord.is_deleted == 0,
            )
            .all()
        )
        for dr in stale_deposits:
            # F-046：写前重取+行锁+状态守卫（防与退款回调并发覆盖）
            dr = (
                db.query(DepositRecord)
                .filter(
                    DepositRecord.id == dr.id,
                    DepositRecord.status == DepositStatus.REFUNDING,
                    DepositRecord.is_deleted == 0,
                )
                .with_for_update()
                .first()
            )
            if dr is None:
                continue
            dr.status = DepositStatus.REFUND_PENDING
            _create_message(
                db,
                user_id=0,
                title="押金退款超时告警（运营）",
                content=(
                    f"押金退款（child {dr.child_id}，单 {dr.pay_order_id}）超7天未到账，"
                    "已回退待审核可重试"
                ),
                msg_type=1,
                priority=2,
            )
        if stale or stale_deposits:
            db.commit()
            logger.info(f"Stale refunds alert: {len(stale)}")
    except Exception as e:
        db.rollback()
        logger.exception(f"alert_stale_refunds failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:check_due_date_reminders", timeout=600)
def check_due_date_reminders(db: Session | None = None):
    """
    [What] 借阅到期提醒
    [Why] 到期前5/3/1/当天发送提醒
    [How] 查询即将到期的借阅记录，写入消息表
    """
    from backend.common.types import BorrowStatus
    from backend.domain.book.models import Book
    from backend.domain.borrow.models import BorrowRecord
    from backend.domain.child.models import Child

    own_session = db is None
    db = db or _get_db_session()
    try:
        from backend.common.config_service import ConfigService

        today = date.today()
        remind_days = ConfigService.get_int_list(db, "due_remind_days", [5, 3, 1, 0])

        # 一次查询所有即将到期的记录（避免 4 × 全表遍历）
        # 加 due_date 上界过滤，防生产全量加载
        max_remind_days = max(remind_days) if remind_days else 0
        # 开区间上界 = 最后提醒日次日零点：due 当天任意时刻都应收（此前 <= 当日零点漏当天到期）
        due_date_upper = today + timedelta(days=max_remind_days + 1)
        # 使用 JOIN 一次查询，避免 N+1
        records = (
            db.query(BorrowRecord, Child, Book)
            .join(Child, BorrowRecord.child_id == Child.id)
            .outerjoin(Book, BorrowRecord.book_id == Book.id)
            .filter(
                BorrowRecord.status == BorrowStatus.BORROWING,
                BorrowRecord.is_deleted == 0,
                BorrowRecord.due_date.isnot(None),
                BorrowRecord.due_date <= due_date_upper,
            )
            .all()
        )

        # 按到期日期分组
        from collections import defaultdict

        records_by_date: dict[date, list] = defaultdict(list)
        for record, child, book in records:
            if record.due_date:
                records_by_date[record.due_date.date()].append((record, child, book))

        for days in remind_days:
            target_date = today + timedelta(days=days)
            for record, child, book in records_by_date.get(target_date, []):
                if not child:
                    continue
                book_name = book.title if book else "图书"
                if days == 0:
                    msg = f"您借阅的《{book_name}》今天到期，请尽快归还"
                elif days == 1:
                    msg = f"您借阅的《{book_name}》将于明天到期"
                else:
                    msg = f"您借阅的《{book_name}》将于{days}天后到期"
                _create_message(
                    db,
                    user_id=child.user_id,
                    title="借阅到期提醒",
                    content=msg,
                    msg_type=3,  # 借阅通知
                )
                logger.info(f"DUE_REMIND: child={child.id}, book={record.book_id}, days={days}")
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception(f"check_due_date_reminders failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:expire_reservations", timeout=120)
def expire_reservations(db: Session | None = None):
    """
    [What] 预约过期检查
    [Why] 72小时未取书自动取消预约并释放库存
    [How] 查询过期预约，通过 Service 层处理过期逻辑
    """
    from backend.common.types import ReservationStatus
    from backend.domain.reservation.models import Reservation
    from backend.domain.reservation.service import ReservationService

    own_session = db is None
    db = db or _get_db_session()
    try:
        now = datetime.now()
        expired = (
            db.query(Reservation)
            .filter(
                Reservation.status == ReservationStatus.PENDING,
                Reservation.expire_time < now,
                Reservation.is_deleted == 0,
            )
            .all()
        )

        svc = ReservationService(db)
        success = 0
        for r in expired:
            try:
                svc.expire_reservation(r.id)
                # F71-⑦：预约过期通知用户本人（此前静默释放库存）
                from backend.domain.child.models import Child as _Child
                from backend.domain.message.models import SystemMessage as _Msg

                child = (
                    db.query(_Child).filter(_Child.id == r.child_id, _Child.is_deleted == 0).first()
                )
                if child:
                    db.add(
                        _Msg(
                            user_id=child.user_id,
                            title="预约已过期",
                            content="您预约的图书超过 72 小时未取，预约已自动取消，库存已释放。可重新预约。",
                            msg_type=2,  # 活动/预约通知
                            priority=1,
                        )
                    )
                db.commit()
                success += 1
                logger.info(f"RESERVATION_EXPIRED: id={r.id}, child={r.child_id}, book={r.book_id}")
            except Exception as e:
                # L3-034：逐条 rollback——单条失败不污染其他预约的变更（原统一 commit 会提交半成品）
                db.rollback()
                logger.error(f"Failed to expire reservation {r.id}: {e}", exc_info=True)

        if expired:
            logger.info(f"Reservations expired: {success}/{len(expired)}")
    except Exception as e:
        db.rollback()
        logger.exception(f"expire_reservations failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:remind_reservation_pickup", timeout=300)
def remind_reservation_pickup(db: Session | None = None):
    """
    [What] 预约取书提醒（B4：到期前 24h 未取 → 提醒一次）
    [Why] 预约取书率仅 40-50%，提醒可显著降低空锁
    [How] PENDING 且 expire_time 落在未来 24h 内且未提醒 → 发消息 + 标记

    参数 db：可选的 session 注入（测试用），不传则自行创建。
    """
    from backend.common.config_service import ConfigService
    from backend.common.types import ReservationStatus
    from backend.domain.book.models import Book
    from backend.domain.child.models import Child
    from backend.domain.message.models import SystemMessage
    from backend.domain.reservation.models import Reservation

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        now = datetime.now()
        remind_hours = ConfigService.get_int(db, "reservation_remind_hours", 24)
        deadline = now + timedelta(hours=remind_hours)

        pending = (
            db.query(Reservation)
            .filter(
                Reservation.status == ReservationStatus.PENDING,
                Reservation.expire_time > now,
                Reservation.expire_time <= deadline,
                Reservation.pickup_reminded == 0,
                Reservation.is_deleted == 0,
            )
            .all()
        )

        # L3-036：批量预取 Child/Book 替代循环内 N+1 点查（1+2N → 3 次固定查询）
        child_ids = {r.child_id for r in pending}
        book_ids = {r.book_id for r in pending}
        children = {
            c.id: c
            for c in db.query(Child).filter(Child.id.in_(child_ids), Child.is_deleted == 0).all()
        }
        books = {
            b.id: b
            for b in db.query(Book).filter(Book.id.in_(book_ids), Book.is_deleted == 0).all()
        }

        count = 0
        for r in pending:
            child = children.get(r.child_id)
            book = books.get(r.book_id)
            if not child or not book:
                continue
            db.add(
                SystemMessage(
                    user_id=child.user_id,
                    title="预约取书提醒",
                    content=(
                        f"您预约的《{book.title}》将于 "
                        f"{r.expire_time.strftime('%m月%d日 %H:%M')} 过期，"
                        f"请尽快到门店取书哦～"
                    ),
                    msg_type=3,  # 借阅通知
                    priority=1,
                )
            )
            r.pickup_reminded = 1
            count += 1

        db.commit()
        if count:
            logger.info(f"Reservation pickup reminders sent: {count}")
    except Exception as e:
        db.rollback()
        logger.exception(f"remind_reservation_pickup failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:audit_sla_escalation", timeout=300)
def audit_sla_escalation(db: Session | None = None):
    """
    [What] 人工审核 SLA 巡检（E2：超 24h 未审 → 升级提醒超管）
    [Why] 管理员非 24h 在线，审核堆积会让家长等到第二天，必须有人兜底
    [How] 每日扫描 4 个人工队列（退款/押金退款/定责复核/权益转让），
          有超 24h 未审项 → 写系统告警（user_id=0 管理端可见）
    参数 db：可选的 session 注入（测试用），不传则自行创建。
    """
    from backend.common.types import DepositStatus
    from backend.domain.book.damage_model import BookDamageReport
    from backend.domain.child.benefit_transfer_model import BenefitTransferApplication
    from backend.domain.deposit.models import DepositRecord
    from backend.domain.refund.models import RefundApplication

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        # E2 SLA 小时数配置化（P1-2：默认 24，范围 1-168）
        from backend.common.config_service import ConfigService

        sla_hours = ConfigService.get_int(db, "review_sla_hours", 24)
        cutoff = datetime.now() - timedelta(hours=sla_hours)
        stale_items = []

        refund_count = (
            db.query(RefundApplication)
            .filter(
                RefundApplication.status == RefundApplication.STATUS_PENDING,
                RefundApplication.create_time < cutoff,
                RefundApplication.is_deleted == 0,
            )
            .count()
        )
        if refund_count:
            stale_items.append(f"退款申请 {refund_count} 笔")

        deposit_count = (
            db.query(DepositRecord)
            .filter(
                DepositRecord.status == DepositStatus.REFUND_PENDING,
                DepositRecord.update_time < cutoff,
                DepositRecord.is_deleted == 0,
            )
            .count()
        )
        if deposit_count:
            stale_items.append(f"押金退款 {deposit_count} 笔")

        damage_count = (
            db.query(BookDamageReport)
            .filter(
                BookDamageReport.status == BookDamageReport.STATUS_PENDING_REVIEW,
                BookDamageReport.create_time < cutoff,
                BookDamageReport.is_deleted == 0,
            )
            .count()
        )
        if damage_count:
            stale_items.append(f"定责复核 {damage_count} 条")

        transfer_count = (
            db.query(BenefitTransferApplication)
            .filter(
                BenefitTransferApplication.status == 0,  # PENDING
                BenefitTransferApplication.create_time < cutoff,
                BenefitTransferApplication.is_deleted == 0,
            )
            .count()
        )
        if transfer_count:
            stale_items.append(f"权益转让 {transfer_count} 笔")

        if stale_items:
            content = (
                f"以下人工审核已超过 {sla_hours} 小时未处理："
                + "、".join(stale_items)
                + "。请超管尽快处理。"
            )
            # L3-034：告警去重——7 天内同标题告警已存在则不再重复写入（原每日扫描重复告警刷屏）
            from backend.domain.message.models import SystemMessage as _SlaMsg

            recent = (
                db.query(_SlaMsg.id)
                .filter(
                    _SlaMsg.user_id == 0,
                    _SlaMsg.title == f"审核超时提醒（SLA {sla_hours}h）",
                    _SlaMsg.create_time > datetime.now() - timedelta(days=7),
                    _SlaMsg.is_deleted == 0,
                )
                .count()
            )
            if not recent:
                _create_message(
                    db,
                    user_id=0,  # 管理端告警
                    title=f"审核超时提醒（SLA {sla_hours}h）",
                    content=content,
                    msg_type=1,
                    priority=2,
                )
                db.commit()
                logger.warning(f"AUDIT_SLA: {content}")
            else:
                logger.info("AUDIT_SLA: 已有近期告警，跳过重复写入")
        else:
            logger.info("AUDIT_SLA: 无超时审核项")
    except Exception as e:
        db.rollback()
        logger.exception(f"audit_sla_escalation failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:graduate_children", timeout=300)
def graduate_children(db: Session | None = None):
    """
    [What] 满 15 岁毕业流程（F2）
    [Why] 15 岁后不再提供借阅服务，转"校友"状态（不可借可查历史）+ 引导退押金
    [How] age>=15 且为会员状态 → ALUMNI + 毕业通知；age==14 → 毕业提醒（每年最多1条）
    参数 db：可选的 session 注入（测试用），不传则自行创建。
    """
    from backend.domain.child.models import Child

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        member_statuses = [
            MemberStatus.OBSERVATION,
            MemberStatus.OFFICIAL,
            MemberStatus.EXPIRED,
        ]

        # 满 15 岁 → 毕业（ALUMNI）
        graduates = (
            db.query(Child)
            .filter(
                Child.age >= 15,
                Child.status.in_(member_statuses),
                Child.is_deleted == 0,
            )
            .all()
        )
        for child in graduates:
            # F-046：写前重取+行锁+状态守卫（防与续费/复活并发覆盖）
            child = (
                db.query(Child)
                .filter(
                    Child.id == child.id,
                    Child.status.in_(member_statuses),
                    Child.is_deleted == 0,
                )
                .with_for_update()
                .first()
            )
            if child is None:
                continue
            # F-111：毕业前检查活跃借阅（BORROWING/OVERDUE）——毕业消息须提醒还书
            # （未还书将悬空：罚款持续累计 + 押金退还与未还书冲突）
            from backend.common.types import BorrowStatus
            from backend.domain.borrow.models import BorrowRecord

            active_borrows = (
                db.query(BorrowRecord)
                .filter(
                    BorrowRecord.child_id == child.id,
                    BorrowRecord.status.in_([BorrowStatus.BORROWING, BorrowStatus.OVERDUE]),
                    BorrowRecord.is_deleted == 0,
                )
                .count()
            )
            return_hint = (
                f"您有 {active_borrows} 本图书尚未归还，请先办理还书手续，归还后方可退还押金。"
                if active_borrows
                else "如尚有押金未退，请在小程序「会员中心-押金」申请退还。"
            )
            child.status = MemberStatus.ALUMNI
            _create_message(
                db,
                user_id=child.user_id,
                title="毕业快乐",
                content=(
                    f"{child.name}已经 15 岁啦，从 DmkWords 正式毕业！"
                    "历史阅读数据将永久保留。" + return_hint
                ),
                msg_type=1,
                priority=1,
            )
            logger.info(f"GRADUATED: child={child.id}, name={child.name[:1]}**")

        # 14 岁 → 毕业提醒（每年最多 1 条，近似"15 岁前 90 天"——当前仅有年龄无生日）
        pre_grads = (
            db.query(Child)
            .filter(
                Child.age == 14,
                Child.status.in_([MemberStatus.OBSERVATION, MemberStatus.OFFICIAL]),
                Child.is_deleted == 0,
            )
            .all()
        )
        remind_count = 0
        current_year = datetime.now().year
        for child in pre_grads:
            # F23：独立留痕字段按自然年去重——消息表防重会被 purge（1 年保留期）
            # 物理删除，静态 age 下会导致提醒反复发送
            if child.grad_remind_year == current_year:
                continue
            _create_message(
                db,
                user_id=child.user_id,
                title="毕业提醒",
                content=(
                    f"{child.name}即将年满 15 岁，会员到期后将转为校友身份"
                    "（不可再借阅，历史数据保留）。如有押金请提前安排退还。"
                ),
                msg_type=1,
                priority=1,
            )
            child.grad_remind_year = current_year
            remind_count += 1

        db.commit()
        if graduates or remind_count:
            logger.info(f"graduate_children: graduated={len(graduates)}, reminded={remind_count}")
    except Exception as e:
        db.rollback()
        logger.exception(f"graduate_children failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:mark_overdue_books", timeout=600)
def mark_overdue_books(db: Session | None = None):
    """
    [What] 逾期检测 + 服务费按日累计（B7：宽限期/上限/首次免罚走 fine_policy）
    [Why] 超过21天未还的借阅记录标记为逾期，已逾期的更新服务费
    [How] 查询到期日已过的BORROWING/OVERDUE记录，更新状态和服务费
    """
    from backend.common.types import BorrowStatus
    from backend.domain.borrow.models import BorrowRecord
    from backend.domain.child.models import Child

    own_session = db is None
    db = db or _get_db_session()
    try:
        now = datetime.now()
        from backend.common.fine_policy import (
            apply_fine,
            calc_overdue_days,
            get_overdue_policy,
        )

        policy = get_overdue_policy(db)

        # 新逾期：BORROWING → OVERDUE
        new_overdue = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.status == BorrowStatus.BORROWING,
                BorrowRecord.due_date < now,
                BorrowRecord.is_deleted == 0,
            )
            .all()
        )

        for record in new_overdue:
            # F58：任务内逐条加行锁重取 + 状态守卫（防与还书并发把 RETURNED 覆盖回 OVERDUE）
            record = (
                db.query(BorrowRecord)
                .filter(
                    BorrowRecord.id == record.id,
                    BorrowRecord.status == BorrowStatus.BORROWING,
                    BorrowRecord.is_deleted == 0,
                )
                .with_for_update()
                .first()
            )
            if record is None:
                continue
            overdue_days = calc_overdue_days(now, record.due_date)
            record.status = BorrowStatus.OVERDUE
            apply_fine(db, record, overdue_days, policy)
            logger.debug(
                f"BOOK_OVERDUE: id={record.id}, child={record.child_id}, book={record.book_id}, days={overdue_days}"
            )

        # 已逾期：更新服务费按日累计（宽限期/上限/首次免罚统一走 fine_policy）
        existing_overdue = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.status == BorrowStatus.OVERDUE,
                BorrowRecord.is_deleted == 0,
            )
            .all()
        )

        for record in existing_overdue:
            # F58：同新逾期——行锁重取 + 状态守卫
            record = (
                db.query(BorrowRecord)
                .filter(
                    BorrowRecord.id == record.id,
                    BorrowRecord.status == BorrowStatus.OVERDUE,
                    BorrowRecord.is_deleted == 0,
                )
                .with_for_update()
                .first()
            )
            if record is None:
                continue
            current_days = calc_overdue_days(now, record.due_date)
            if current_days > (record.overdue_days or 0):
                apply_fine(db, record, current_days, policy)

        # 按孩子差额增量维护 outstanding_fines（F35：只动逾期服务费部分，
        # 不覆写损坏/丢失/手工罚款；标记列防双计）
        from backend.common.fine_policy import sync_outstanding_fine

        all_overdue = new_overdue + existing_overdue
        if all_overdue:
            child_ids = {r.child_id for r in all_overdue}
            children = {
                c.id: c
                for c in db.query(Child)
                .filter(Child.id.in_(child_ids), Child.is_deleted == 0)
                .with_for_update()  # F80：任务内持行锁，防与还书/退款并发丢更新
                .all()
            }
            for record in all_overdue:
                child = children.get(record.child_id)
                if child is not None:
                    sync_outstanding_fine(db, child, record)

        total = len(new_overdue) + len(existing_overdue)
        if total:
            db.commit()
            logger.info(
                f"Overdue books processed: {len(new_overdue)} new, {len(existing_overdue)} updated"
            )
    except Exception as e:
        db.rollback()
        logger.exception(f"mark_overdue_books failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:check_observation_expiry", timeout=600)
def check_observation_expiry(db: Session | None = None):
    """
    [What] 观察期到期检查 + 自动生成评估报告
    [Why] 观察期到期后自动设置为 EXPIRED，并生成报告引导转化
    [How] 1. 生成观察期报告 2. 状态变更 OBSERVATION→EXPIRED

    参数 db：可选的 session 注入（测试用），不传则自行创建。
    """
    from backend.common.types import MemberStatus
    from backend.domain.child.models import Child

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        now = datetime.now()
        expired = (
            db.query(Child)
            .filter(
                Child.status == MemberStatus.OBSERVATION,
                Child.member_expire_time < now,
                Child.is_deleted == 0,
            )
            .all()
        )

        if not expired:
            return

        # 1. 生成观察期报告（F14：per-child 隔离——单孩失败只留 OBSERVATION 下轮重试，
        #    不队头阻塞；生成成功或已有报告的孩子才转 EXPIRED）
        from backend.domain.report.models import ObservationReport
        from backend.domain.report.service import ReportService

        expired_ids = [c.id for c in expired]
        existing_report_ids = {
            r[0]
            for r in db.query(ObservationReport.child_id)
            .filter(ObservationReport.child_id.in_(expired_ids))
            .all()
        }
        generated = ReportService(db).generate_due_reports()
        ready_ids = existing_report_ids | {g["child_id"] for g in generated}
        logger.info(
            f"Observation reports ready: {len(ready_ids)}/{len(expired)} "
            f"(new={len(generated)}, existing={len(existing_report_ids)})"
        )

        # 2. 状态变更
        expired_count = 0
        for child in expired:
            if child.id in ready_ids:
                # F-046：写前重取+状态守卫（防与管理员改状态并发覆盖）
                child = (
                    db.query(Child)
                    .filter(
                        Child.id == child.id,
                        Child.status == MemberStatus.OBSERVATION,
                        Child.is_deleted == 0,
                    )
                    .with_for_update()
                    .first()
                )
                if child is None:
                    continue
                child.status = MemberStatus.EXPIRED
                if child.exited_at is None:
                    # F-105：观察期到期转 EXPIRED 同样记录退出时间
                    child.exited_at = datetime.now()
                expired_count += 1
                logger.info(f"Observation expired: child_id={child.id}, name={child.name}")
            else:
                logger.warning(
                    f"Observation report not ready, keep OBSERVATION for retry: child_id={child.id}"
                )

        db.commit()
        logger.info(f"Observation expiry: {expired_count}/{len(expired)} children expired")
    except Exception as e:
        db.rollback()
        logger.exception(f"check_observation_expiry failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:check_observation_reminders", timeout=600)
def check_observation_reminders(db: Session | None = None):
    """
    [What] 观察期到期提醒
    [Why] 观察期到期前发送提醒，引导转化
    [How] 查询即将到期的观察期用户，写入消息表

    参数 db：可选的 session 注入（测试用），不传则自行创建。
    """
    from backend.common.config_service import ConfigService
    from backend.domain.child.models import Child

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        today = date.today()
        remind_days = ConfigService.get_int_list(db, "observation_remind_days", [7, 5, 3, 2, 1, 0])

        # F-015 终审同类漏改：一次范围查询替代 N 次 func.date 逐日查询
        # （func.date 包裹 member_expire_time 令索引失效全表扫描，check_member_expiry 已同款）
        from collections import defaultdict

        max_days = max(remind_days)
        min_days = min(remind_days)
        date_upper = today + timedelta(days=max_days)
        date_lower = today + timedelta(days=min_days)
        lower_dt = datetime.combine(date_lower, datetime.min.time())
        upper_dt = datetime.combine(date_upper + timedelta(days=1), datetime.min.time())

        children = (
            db.query(Child)
            .filter(
                Child.status == MemberStatus.OBSERVATION,
                Child.member_expire_time.isnot(None),
                Child.member_expire_time >= lower_dt,
                Child.member_expire_time < upper_dt,
                Child.is_deleted == 0,
            )
            .all()
        )
        children_by_date: dict[date, list] = defaultdict(list)
        for child in children:
            children_by_date[child.member_expire_time.date()].append(child)

        for days in remind_days:
            target_date = today + timedelta(days=days)
            for child in children_by_date.get(target_date, []):
                if days == 0:
                    msg = f"您的孩子 {child.name} 的观察期今天到期，请决定是否升级为正式会员"
                else:
                    msg = f"您的孩子 {child.name} 的观察期将在{days}天后到期"
                _create_message(
                    db,
                    user_id=child.user_id,
                    title="观察期到期提醒",
                    content=msg,
                    msg_type=1,  # 系统通知
                    priority=1 if days <= 2 else 0,
                )
                logger.info(f"OBSERVATION_REMIND: child={child.id}, days_left={days}")
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception(f"check_observation_reminders failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:check_activity_reminders", timeout=120)
def check_activity_reminders():
    """活动开始前 3 天提醒 — 每天 10:00 执行"""
    db = _get_db_session()
    try:
        from backend.domain.activity.models import Activity, ActivityEnrollment
        from backend.domain.child.models import Child

        now = datetime.now()
        target_start = now + timedelta(days=3)
        target_end = now + timedelta(days=4)

        # 一次 JOIN 查询 activity → enrollment → child（避免 N+1 × N+1）
        rows = (
            db.query(Activity, ActivityEnrollment, Child)
            .join(ActivityEnrollment, Activity.id == ActivityEnrollment.activity_id)
            .join(Child, ActivityEnrollment.child_id == Child.id)
            .filter(
                Activity.start_time >= target_start,
                Activity.start_time < target_end,
                Activity.status.in_([Activity.STATUS_ENROLLING, Activity.STATUS_ENROLL_CLOSED]),
                Activity.is_deleted == 0,
                ActivityEnrollment.status == ActivityEnrollment.STATUS_APPROVED,
                ActivityEnrollment.is_deleted == 0,
            )
            .all()
        )

        for activity, enrollment, child in rows:
            if not child:
                continue
            _create_message(
                db,
                user_id=child.user_id,
                title="活动开始提醒",
                content=f"您报名的活动「{activity.title}」将于 3 天后（{activity.start_time.strftime('%m月%d日 %H:%M')}）开始，请做好准备！",
                msg_type=2,  # 活动通知
                priority=0,
            )
            logger.info(f"ACTIVITY_REMIND: child={child.id}, activity={activity.id}")

        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception(f"check_activity_reminders failed: {e}")
    finally:
        db.close()


@distributed_lock("job:confirm_expired_damage_reports", timeout=300)
def confirm_expired_damage_reports():
    """确认过期未申诉的损坏报告（超过7天自动确认）"""
    from backend.domain.admin.services.damage_admin_service import DamageAdminService

    db = _get_db_session()
    try:
        svc = DamageAdminService(db)
        count = svc.batch_confirm_expired()
        if count:
            logger.info("定时任务 confirm_expired_damage_reports: 确认 %d 条", count)
    except Exception as e:
        # L3-034：补 rollback——异常时释放悬挂事务（原只 logger 不回滚）
        db.rollback()
        logger.exception(f"confirm_expired_damage_reports failed: {e}")
    finally:
        db.close()


@distributed_lock("job:cleanup_orphan_chunks", timeout=300)
def cleanup_orphan_chunks():
    """L3-039 O-30：清理孤儿上传分片会话（客户端中断残留，每日 4:10）"""
    from backend.domain.admin.services.upload_service import (
        cleanup_orphan_chunks as _do,
    )

    removed = _do()
    if removed:
        logger.info(f"Orphan chunk cleanup: {removed} sessions removed")


@distributed_lock("job:purge_expired_data", timeout=1800)
def purge_expired_data(db: Session | None = None):
    """H5 数据保留期到期清理（隐私政策承诺的自动删除）

    统一口径（PRD N.10 / 前端隐私政策一致）：
      - 消息类（system_message/teacher_message + message_read_status 级联）：
        创建超 data_retention_message_years（默认 1 年）→ 物理删除
      - 行为类：EXITED 且退出超 data_retention_behavior_years（默认 2 年）的孩子，
        其学习行为数据（阅读进度/会话/打卡/生词/测验/书架/候补/预约）→ 物理删除
      - 财务类：EXITED 孩子的 order/deposit_record/fine_payment/refund_application/
        borrow_record/book_damage_report/benefit_transfer_application，
        记录创建超 data_retention_finance_years（默认 5 年）→ 物理删除
      - 语音录音：创建超 voice_retention_months（默认 6 个月）→ 物理删除行 + 删音频文件
      - consent_record 永不物理删除（法定留存，与删除权级联一致）
    """
    from backend.common.config_service import ConfigService
    from backend.common.types import MemberStatus
    from backend.domain.child.models import Child

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        now = datetime.now()
        msg_years = ConfigService.get_int(db, "data_retention_message_years", 1)
        behavior_years = ConfigService.get_int(db, "data_retention_behavior_years", 2)
        finance_years = ConfigService.get_int(db, "data_retention_finance_years", 5)
        voice_months = ConfigService.get_int(db, "voice_retention_months", 6)

        stats: dict[str, int] = {}

        # ── 1) 消息类（所有用户，按记录年龄；user_id=0 管理端告警豁免保留审计链）──
        from backend.domain.message.models import (
            MessageReadStatus,
            SystemMessage,
            TeacherMessage,
        )

        msg_cutoff = now - timedelta(days=msg_years * 365)
        # F-014：消息清理分批（limit 5000 循环，防大表一次性全量拉爆）
        while True:
            batch = [
                r[0]
                for r in db.query(SystemMessage.id)
                .filter(
                    SystemMessage.create_time < msg_cutoff,
                    (SystemMessage.user_id.is_(None)) | (SystemMessage.user_id != 0),
                )
                .limit(5000)
                .all()
            ]
            if not batch:
                break
            stats["message_read_status"] = stats.get("message_read_status", 0) + db.query(
                MessageReadStatus
            ).filter(MessageReadStatus.message_id.in_(batch)).delete(synchronize_session=False)
            stats["system_message"] = stats.get("system_message", 0) + (
                db.query(SystemMessage)
                .filter(SystemMessage.id.in_(batch))
                .delete(synchronize_session=False)
            )
            db.commit()
        # F-07d/L2-019：TeacherMessage 同样分批（原单次全量 delete）
        stats["teacher_message"] = 0
        while True:
            t_batch = [
                r[0]
                for r in db.query(TeacherMessage.id)
                .filter(TeacherMessage.create_time < msg_cutoff)
                .limit(5000)
                .all()
            ]
            if not t_batch:
                break
            stats["teacher_message"] += (
                db.query(TeacherMessage)
                .filter(TeacherMessage.id.in_(t_batch))
                .delete(synchronize_session=False)
            )
            db.commit()

        # ── 2) 行为类（仅 EXITED 且退出已久的孩子；exited_at 为计时基准，不受后续字段更新影响）──
        behavior_cutoff = now - timedelta(days=behavior_years * 365)
        exited_ids = [
            r[0]
            for r in db.query(Child.id)
            .filter(
                Child.status == MemberStatus.EXITED,
                Child.exited_at.isnot(None),
                Child.exited_at < behavior_cutoff,
            )
            .all()
        ]
        if exited_ids:
            from backend.domain.activity.models import ActivityEnrollment
            from backend.domain.advancement.models import (
                ChildAchievement,
                ChildLevel,
                Quiz,
                QuizAnswer,
                ReadingSubmission,
            )
            from backend.domain.assessment.models import Assessment
            from backend.domain.bookshelf.models import Bookshelf
            from backend.domain.certificate.models import LevelCertificate
            from backend.domain.evaluation.models import (
                AREvaluation,
                GuidanceRecord,
                ObservationEvaluation,
            )
            from backend.domain.reading.models import (
                CheckIn,
                ReadingProgress,
                ReadingSession,
            )
            from backend.domain.report.models import (
                LearningReport,
                ObservationReport,
            )
            from backend.domain.reservation.models import BookWaitlist, Reservation
            from backend.domain.vocabulary.models import UserVocabulary

            # quiz_answer 经 quiz_id 级联（先删子表）
            quiz_ids = [r[0] for r in db.query(Quiz.id).filter(Quiz.child_id.in_(exited_ids)).all()]
            if quiz_ids:
                stats["quiz_answer"] = (
                    db.query(QuizAnswer)
                    .filter(QuizAnswer.quiz_id.in_(quiz_ids))
                    .delete(synchronize_session=False)
                )
                stats["quiz"] = (
                    db.query(Quiz).filter(Quiz.id.in_(quiz_ids)).delete(synchronize_session=False)
                )
            for model, key in (
                (ReadingProgress, "reading_progress"),
                (ReadingSession, "reading_session"),
                (ReadingSubmission, "reading_submission"),
                (CheckIn, "check_in"),
                (UserVocabulary, "user_vocabulary"),
                (Bookshelf, "bookshelf"),
                (BookWaitlist, "book_waitlist"),
                (Reservation, "reservation"),
                (ChildLevel, "child_level"),
                (ChildAchievement, "child_achievement"),
                (LevelCertificate, "level_certificate"),
                (Assessment, "assessment"),
                (AREvaluation, "ar_evaluation"),
                (ObservationEvaluation, "observation_evaluation"),
                (GuidanceRecord, "guidance_record"),
                (ObservationReport, "observation_report"),
                (LearningReport, "learning_report"),
                (ActivityEnrollment, "activity_enrollment"),
            ):
                stats[key] = (
                    db.query(model)
                    .filter(model.child_id.in_(exited_ids))
                    .delete(synchronize_session=False)
                )

        # ── 3) 财务类（EXITED 孩子 + 记录超 5 年；法定保留期内不动）──
        finance_cutoff = now - timedelta(days=finance_years * 365)
        if exited_ids:
            from backend.domain.book.damage_model import BookDamageReport
            from backend.domain.borrow.models import BorrowRecord
            from backend.domain.child.benefit_transfer_model import (
                BenefitTransferApplication,
            )
            from backend.domain.deposit.models import DepositRecord, FinePayment
            from backend.domain.order.models import Order
            from backend.domain.refund.models import RefundApplication

            for model, key in (
                (Order, "order"),
                (DepositRecord, "deposit_record"),
                (FinePayment, "fine_payment"),
                (RefundApplication, "refund_application"),
                (BorrowRecord, "borrow_record"),
                (BookDamageReport, "book_damage_report"),
            ):
                stats[key] = (
                    db.query(model)
                    .filter(
                        model.child_id.in_(exited_ids),
                        model.create_time < finance_cutoff,
                    )
                    .delete(synchronize_session=False)
                )
            stats["benefit_transfer_application"] = (
                db.query(BenefitTransferApplication)
                .filter(
                    BenefitTransferApplication.source_child_id.in_(exited_ids),
                    BenefitTransferApplication.create_time < finance_cutoff,
                )
                .delete(synchronize_session=False)
            )

        # ── 4) 语音录音（所有记录，按 6 个月承诺；含文件清理）──
        from backend.common.file_utils import delete_voice_files
        from backend.domain.reading.models import VoiceRecording

        voice_cutoff = now - timedelta(days=voice_months * 30)
        # F-07d/L2-019：语音记录分批（原全量载入内存），每批删库+清文件
        stats["voice_recording"] = 0
        stats["voice_files"] = 0
        while True:
            v_batch = (
                db.query(VoiceRecording)
                .filter(VoiceRecording.create_time < voice_cutoff)
                .limit(1000)
                .all()
            )
            if not v_batch:
                break
            audio_urls = [v.audio_url for v in v_batch if v.audio_url]
            voice_ids = [v.id for v in v_batch]
            stats["voice_recording"] += (
                db.query(VoiceRecording)
                .filter(VoiceRecording.id.in_(voice_ids))
                .delete(synchronize_session=False)
            )
            stats["voice_files"] += delete_voice_files(audio_urls)
            db.commit()

        db.commit()

        total = sum(v for k, v in stats.items() if k != "voice_files")
        if total or stats.get("voice_files"):
            logger.info(f"定时任务 purge_expired_data: 清理 {stats}")
            from backend.domain.admin.services.system_service import (
                AdminSystemService,
            )

            AdminSystemService(db).write_operation_log(
                admin_id=None,
                module="system",
                operation="purge_expired_data",
                content=f"数据保留期到期清理: {stats}",
            )
        return stats
    except Exception as e:
        db.rollback()
        logger.exception(f"purge_expired_data failed: {e}")
        return {}
    finally:
        if own_session:
            db.close()


@distributed_lock("job:check_paid_not_activated", timeout=300)
def check_paid_not_activated(db: Session | None = None):
    """F7：每日对账——PAID 但会员未激活的订单 → 告警超管 + 人工队列

    背景：支付回调的事件处理器在状态非法（EXITED / 不允许迁移）时 warn-skip，
    订单照常 PAID 但会员未激活。本任务扫描 activation_issue=1 的会员类订单：
      - 孩子已被人工激活（member_expire_time > pay_time）→ 清除标记（已解决）
      - 仍未激活 → 写超管告警（7 天内同单去重），留待人工核对/退款
    兑现 PRD §1.2"支付成功但状态未更新 → 重试/定时任务修复"承诺（修复动作=告警+人工）。
    """
    from backend.common.types import OrderType, PayStatus
    from backend.domain.admin.models import OperationLog
    from backend.domain.child.models import Child
    from backend.domain.message.models import SystemMessage
    from backend.domain.order.models import Order

    own_session = db is None
    if own_session:
        db = _get_db_session()
    try:
        flagged = (
            db.query(Order)
            .filter(
                Order.pay_status == PayStatus.PAID,
                Order.activation_issue == 1,
                Order.is_deleted == 0,
                Order.type.in_(
                    [
                        OrderType.OBSERVATION,
                        OrderType.OFFICIAL_MEMBER,
                        OrderType.QUARTERLY,
                        OrderType.SEMI_ANNUAL,
                    ]
                ),
            )
            .all()
        )
        resolved = 0
        alerts = 0
        for order in flagged:
            child = (
                db.query(Child).filter(Child.id == order.child_id, Child.is_deleted == 0).first()
            )
            activated = (
                child is not None
                and child.status
                in (
                    MemberStatus.OBSERVATION,
                    MemberStatus.OFFICIAL,
                )  # 状态守卫防 EXITED 残留
                and child.member_expire_time is not None
                and order.pay_time is not None
                and child.member_expire_time > order.pay_time
            )
            if activated:
                order.activation_issue = 0
                db.add(
                    OperationLog(
                        admin_id=0,
                        module="order",
                        operation="paid_not_activated_resolved",
                        content=f"order={order.id} 已确认激活，清除未激活标记",
                    )
                )
                resolved += 1
            else:
                recent = (
                    db.query(SystemMessage.id)
                    .filter(
                        SystemMessage.user_id == 0,
                        SystemMessage.title == "支付未激活告警",
                        SystemMessage.content.like(f"%（order={order.id}）%"),  # 右括号防前缀碰撞
                        SystemMessage.create_time > datetime.now() - timedelta(days=7),
                    )
                    .count()
                )
                if not recent:
                    db.add(
                        SystemMessage(
                            user_id=0,
                            title="支付未激活告警",
                            content=(
                                f"订单 {order.order_no}（order={order.id}）已支付但会员未激活，"
                                "请人工核对：确认开通会员或走退款流程"
                            ),
                            msg_type=1,  # 系统通知
                            priority=2,
                        )
                    )
                    alerts += 1
        db.commit()
        if resolved or alerts:
            logger.info(f"check_paid_not_activated: resolved={resolved}, alerts={alerts}")
    except Exception as e:
        db.rollback()
        logger.exception(f"check_paid_not_activated failed: {e}")
    finally:
        if own_session:
            db.close()


@distributed_lock("job:reset_stale_pending_deposits", timeout=300)
def reset_stale_pending_deposits(db: Session | None = None):
    """F39：废弃押金支付单（PENDING 超时未回调）复位 UNPAID，允许重新缴纳

    用户调起微信支付后放弃支付，PENDING 记录若不复位将永久阻塞再次缴纳
    （get_active_by_child 把 PENDING 视为活跃）。超时窗口由配置
    deposit_pending_expire_minutes 控制（默认 30 分钟）。
    """
    from backend.domain.deposit.service import DepositService

    own_session = db is None
    db = db or _get_db_session()
    try:
        count = DepositService(db).reset_stale_pending_deposits()
        if count:
            logger.info(f"reset_stale_pending_deposits: {count} records reset to UNPAID")
    except Exception as e:
        db.rollback()
        logger.exception(f"reset_stale_pending_deposits failed: {e}")
    finally:
        if own_session:
            db.close()
