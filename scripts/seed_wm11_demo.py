# scripts/seed_wm11_demo.py — WM11 演示数据一键重建（UX 返工 Q5 裁决）
"""覆盖四态供验收：通知（未读/已读 × wechat skipped/failed/sent）、
运行记录（success/failed/skipped）、任务"从未运行"态（不造即天然存在）。

幂等：INSERT IGNORE（唯一索引去重），可重跑。用法：python -m scripts.seed_wm11_demo
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from backend.common.notification_models import Notification, TaskRunLog
from backend.database import SessionLocal
from backend.domain.identity.models import Child, Parent


def _ensure_demo_parent(db: Session) -> Parent:
    row = db.query(Parent).filter(Parent.phone == "13800008888").first()
    if row:
        return row
    p = Parent(name="演示家长", phone="13800008888", wechat_openid=None)
    db.add(p)
    db.flush()
    return p


def _ensure_demo_child(db: Session, parent: Parent) -> None:
    """演示孩（C45 配套）：无孩子时 member 页功能网格不渲染，补一个 formal 孩让演示完整。"""
    from backend.domain.identity.models import Child

    exists = db.query(Child).filter(Child.parent_id == parent.id, Child.is_deleted == 0).first()
    if exists:
        return
    today = datetime.now().date()
    db.add(
        Child(
            parent_id=parent.id,
            name="演示孩",
            english_name="Demo",
            member_status=Child.MEMBER_FORMAL,
            member_start=today - timedelta(days=30),
            member_expire=today + timedelta(days=335),
        )
    )
    db.flush()


DEMO_BOOKS = [
    # (isbn, title, author, word_count, ar, grade, topic, audio_isbn)
    (
        "9780394800165",
        "Green Eggs and Ham",
        "Dr. Seuss",
        100,
        "1.5",
        "5-6岁（幼儿园大班）",
        "韵文启蒙",
        "9782000000001",
    ),
    (
        "9780399226908",
        "The Very Hungry Caterpillar",
        "Eric Carle",
        220,
        "2.6",
        "5-6岁（幼儿园大班）",
        "自然认知",
        "9782000000002",
    ),
    (
        "9780060254926",
        "Where the Wild Things Are",
        "Maurice Sendak",
        330,
        "3.4",
        "7-8岁（小学低年级）",
        "想象力",
        "9782000000003",
    ),
    (
        "9780545582889",
        "Dog Man",
        "Dav Pilkey",
        2500,
        "2.3",
        "7-8岁（小学低年级）",
        "幽默桥梁书",
        "9780545582889",
    ),
    (
        "9780064400558",
        "Charlotte's Web",
        "E.B. White",
        4200,
        "4.4",
        "9-10岁（小学中年级）",
        "友谊成长",
        "9782000000001",
    ),
    (
        "9780590353427",
        "Harry Potter and the Sorcerer's Stone",
        "J.K. Rowling",
        78000,
        "5.5",
        "11-12岁（小学高年级）",
        "奇幻章节书",
        "9782000000002",
    ),
]


def _ensure_demo_books(db: Session) -> None:
    """6 本上架演示书目（C48 配套）：带音频（复用 uploads 现有文件）+ 各 2 副本，
    让图书馆/详情/预约/借阅/测验链路都有像样的测试数据。按 ISBN 幂等。"""
    from backend.common.file_storage import _mp3_duration
    from backend.domain.catalog.models import Book, BookCopy

    for isbn, title, author, words, ar, grade, topic, audio_isbn in DEMO_BOOKS:
        if db.query(Book).filter(Book.isbn == isbn, Book.is_deleted == 0).first():
            continue
        audio_rel = f"book_audio/{audio_isbn}/audio.mp3"
        try:
            with open(f"uploads/{audio_rel}", "rb") as fh:
                duration = _mp3_duration(fh.read())
        except OSError:
            continue  # 音频文件缺失则跳过该书（保持脚本可重跑）
        if duration <= 0:
            duration = 60
        book = Book(
            isbn=isbn,
            title=title,
            author=author,
            word_count=words,
            ar_level=ar,
            grade=grade,
            topic=topic,
            status=Book.STATUS_ON,
            audio_path=audio_rel,
            audio_duration_seconds=duration,
        )
        db.add(book)
        db.flush()
        for seq in (1, 2):
            db.add(
                BookCopy(
                    book_id=book.id,
                    copy_code=f"DEMO-{isbn}-{seq}",
                    status=BookCopy.STATUS_AVAILABLE,
                )
            )
        db.flush()


def _ensure_demo_deposit(db: Session, child) -> None:
    """演示押金（C45 配套）：无押金时预约/借阅被守卫拦截，补 paid 押金让链路可测。"""
    from backend.domain.billing.models import Deposit, DepositLedger

    exists = db.query(Deposit).filter(Deposit.child_id == child.id, Deposit.is_deleted == 0).first()
    if exists:
        return
    dep = Deposit(
        child_id=child.id,
        amount=1200,
        available_amount=1200,
        deducted_amount=0,
        supplemented_total=0,
        status=Deposit.STATUS_PAID,
        unpaid_balance=0,
    )
    db.add(dep)
    db.flush()
    db.add(
        DepositLedger(
            deposit_id=dep.id,
            entry_type=DepositLedger.ENTRY_PAY,
            amount=1200,
            balance_after=1200,
            reason="演示押金缴纳",
        )
    )
    db.flush()


def _ensure_demo_borrow(db: Session, child) -> None:
    """演示在借（书架「在借」tab 演示）：借出第一本上架书的一个副本，另留一册可约。"""
    from backend.domain.catalog.models import Book, BookCopy
    from backend.domain.circulation.models import BorrowRecord

    if (
        db.query(BorrowRecord)
        .filter(
            BorrowRecord.child_id == child.id,
            BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
            BorrowRecord.is_deleted == 0,
        )
        .first()
    ):
        return
    book = db.query(Book).filter(Book.is_deleted == 0, Book.status == Book.STATUS_ON).first()
    if not book:
        return
    copies = (
        db.query(BookCopy)
        .filter(BookCopy.book_id == book.id, BookCopy.is_deleted == 0)
        .order_by(BookCopy.id)
        .all()
    )
    while len(copies) < 2:
        copy = BookCopy(
            book_id=book.id,
            copy_code=f"DEMO-{book.id}-{len(copies) + 1}",
            status=BookCopy.STATUS_AVAILABLE,
        )
        db.add(copy)
        db.flush()
        copies.append(copy)
    borrow_copy = next((c for c in copies if c.status == BookCopy.STATUS_AVAILABLE), copies[0])
    borrow_copy.status = BookCopy.STATUS_BORROWED
    db.add(
        BorrowRecord(
            child_id=child.id,
            copy_id=borrow_copy.id,
            book_id=book.id,
            due_at=datetime.now() + timedelta(days=25),
            status=BorrowRecord.STATUS_ACTIVE,
        )
    )
    db.flush()


def _upsert_notification(db: Session, parent: Parent, **kw) -> None:
    stmt = mysql_insert(Notification).values(
        parent_id=parent.id,
        child_id=kw.get("child_id"),
        scene=kw["scene"],
        category=kw["category"],
        title=kw["title"],
        content=kw["content"],
        ref_type=kw.get("ref_type", ""),
        ref_id=kw.get("ref_id", ""),
        dedup_key=kw.get("dedup_key", "1"),
        read_at=kw.get("read_at"),
        wechat_status=kw.get("wechat_status", Notification.WECHAT_SKIPPED),
        wechat_error=kw.get("wechat_error", "通道未启用（演示）"),
        create_time=kw.get("create_time", datetime.now()),
    )
    db.execute(stmt.prefix_with("IGNORE"))
    # 已存在则回写展示态（演示可刷新）
    if kw.get("wechat_status") == Notification.WECHAT_FAILED:
        db.execute(
            update(Notification)
            .where(
                Notification.parent_id == parent.id,
                Notification.scene == kw["scene"],
                Notification.ref_id == kw.get("ref_id", ""),
                Notification.is_deleted == 0,
            )
            .values(
                wechat_status=kw["wechat_status"], wechat_error=kw.get("wechat_error", "演示失败")
            )
        )


def _upsert_run(
    db: Session, task_name: str, status: str, processed: int, error: str | None, when: datetime
) -> None:
    stmt = mysql_insert(TaskRunLog).values(
        task_name=task_name,
        started_at=when,
        finished_at=when + timedelta(seconds=3),
        status=status,
        processed=processed,
        error=error,
        create_time=when,
    )
    db.execute(stmt.prefix_with("IGNORE"))


def seed() -> None:
    db = SessionLocal()
    try:
        parent = _ensure_demo_parent(db)
        _ensure_demo_child(db, parent)
        demo_child = (
            db.query(Child).filter(Child.parent_id == parent.id, Child.is_deleted == 0).first()
        )
        _ensure_demo_books(db)
        demo_child = (
            db.query(Child).filter(Child.parent_id == parent.id, Child.is_deleted == 0).first()
        )
        if demo_child is not None:
            _ensure_demo_deposit(db, demo_child)
            _ensure_demo_borrow(db, demo_child)
        now = datetime.now()
        _upsert_notification(
            db,
            parent,
            scene="borrow.success",
            category="借阅",
            title="借书成功",
            content="《Dog Man》已借出，应还日期 "
            + (now + timedelta(days=30)).strftime("%Y-%m-%d")
            + "，请按时归还或续借。",
            ref_type="borrow_record",
            ref_id="901",
            dedup_key="1",
            wechat_status=Notification.WECHAT_SKIPPED,
        )
        _upsert_notification(
            db,
            parent,
            scene="borrow.overdue",
            category="借阅",
            title="图书已逾期",
            content="《Harry Potter》已逾期 3 天未还（应还日期 "
            + (now - timedelta(days=3)).strftime("%Y-%m-%d")
            + "），请尽快归还。",
            ref_type="borrow_record",
            ref_id="902",
            dedup_key="1",
            wechat_status=Notification.WECHAT_FAILED,
            wechat_error="订阅额度不足（演示失败态）",
        )
        _upsert_notification(
            db,
            parent,
            scene="member.expire_remind",
            category="会员",
            title="会员续费提醒",
            content="孩子 演示孩 的正式会员将在 7 天后（"
            + (now + timedelta(days=7)).strftime("%Y-%m-%d")
            + "）到期，请及时续费。",
            ref_type="child",
            ref_id="1",
            dedup_key="7",
            read_at=now - timedelta(hours=2),
            wechat_status=Notification.WECHAT_SKIPPED,
        )
        _upsert_notification(
            db,
            parent,
            scene="money.refund_received",
            category="资金",
            title="退款到账",
            content="退款 500.00 元已到账（人工打款登记）。",
            ref_type="refund_request",
            ref_id="801",
            dedup_key="1",
            read_at=now - timedelta(hours=5),
            wechat_status=Notification.WECHAT_SENT,
        )
        _upsert_notification(
            db,
            parent,
            scene="activity.remind",
            category="活动",
            title="活动提醒",
            content="《故事会》将于 1 天后（"
            + (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
            + "）开始，地点：馆内，请提前到场。",
            ref_type="activity",
            ref_id="601",
            dedup_key="1",
            wechat_status=Notification.WECHAT_SKIPPED,
        )
        _upsert_run(db, "member_expire_check", "success", 0, None, now - timedelta(minutes=10))
        _upsert_run(
            db,
            "overdue_mark",
            "failed",
            0,
            "演示失败：DatabaseError(Connection refused)（演示 failed 态）",
            now - timedelta(minutes=8),
        )
        _upsert_run(
            db,
            "activity_remind",
            "skipped",
            0,
            "配置节点为空，跳过（演示 skipped 态）",
            now - timedelta(minutes=6),
        )
        # ---------- C42 验收扩充：8 分类全场景演示（19 条，未读 10 / 已读 9） ----------
        demo_notifs = [
            # 未读 · 资金/借阅/阅读/预约/报告
            dict(
                scene="money.order_paid",
                category="资金",
                title="订单支付成功",
                content="观察期会员费 500.00 元已支付成功，会员权益已开通。",
                ref_type="order",
                ref_id="903",
                read_at=None,
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(minutes=30),
            ),
            dict(
                scene="borrow.returned",
                category="借阅",
                title="还书成功",
                content="《The Cat in the Hat》已归还，感谢按时还书，期待下次阅读！",
                ref_type="borrow_record",
                ref_id="904",
                read_at=None,
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=1),
            ),
            dict(
                scene="borrow.due_remind",
                category="借阅",
                title="借阅即将到期",
                content="《Charlotte's Web》将在 3 天后（"
                + (now + timedelta(days=3)).strftime("%Y-%m-%d")
                + "）到期，可续借 1 次（延长 7 天）。",
                ref_type="borrow_record",
                ref_id="905",
                read_at=None,
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=2),
            ),
            dict(
                scene="reading.quiz_result",
                category="阅读",
                title="测验通过",
                content="恭喜！《Dog Man》测验得分 4/5（通过），有效词数 +2500 已入账。",
                ref_type="quiz",
                ref_id="911",
                read_at=None,
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=3),
            ),
            dict(
                scene="reading.milestone",
                category="阅读",
                title="里程碑达成",
                content="累计有效阅读词数突破 100,000！获得「十万词阅读者」勋章。",
                ref_type="milestone",
                ref_id="912",
                read_at=None,
                wechat_status=Notification.WECHAT_FAILED,
                wechat_error="订阅模板未配置（演示失败态）",
                create_time=now - timedelta(hours=4),
            ),
            dict(
                scene="reservation.expiring",
                category="预约",
                title="预约即将到期",
                content="您预约的《Green Eggs and Ham》将在 12 小时后释放，请尽快到店借取。",
                ref_type="reservation",
                ref_id="913",
                read_at=None,
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=5),
            ),
            dict(
                scene="report.generated",
                category="报告",
                title="周报已生成",
                content="本周阅读报告已生成：有效阅读 5 天 / 新增词数 3,200 / 打卡 5 天，点击查看。",
                ref_type="report",
                ref_id="914",
                read_at=None,
                wechat_status=Notification.WECHAT_SENT,
                wechat_error=None,
                create_time=now - timedelta(hours=6),
            ),
            # 已读 · 阅读/会员/活动/预约/资金/其他
            dict(
                scene="reading.level_up",
                category="阅读",
                title="等级提升",
                content="恭喜！累计通过 100 本，阅读等级提升至 B 级，继续加油！",
                ref_type="child",
                ref_id="921",
                read_at=now - timedelta(hours=7),
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=7),
            ),
            dict(
                scene="reservation.released",
                category="预约",
                title="预约已释放",
                content="您预约的《One Fish Two Fish》超过 72 小时未到店核销，已自动释放。",
                ref_type="reservation",
                ref_id="922",
                read_at=now - timedelta(hours=8),
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=8),
            ),
            dict(
                scene="member.withdraw_result",
                category="会员",
                title="退会审核结果",
                content="您的退会申请已审核通过，押金可用余额退款将原路退回。",
                ref_type="withdrawal_request",
                ref_id="923",
                read_at=now - timedelta(hours=9),
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=9),
            ),
            dict(
                scene="member.pending_eval",
                category="会员",
                title="已转入待评估",
                content="观察期已结束，孩子已转入「待评估」，请到馆完成阅读评估后转正。",
                ref_type="child",
                ref_id="924",
                read_at=now - timedelta(hours=10),
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=10),
            ),
            dict(
                scene="activity.cancel",
                category="活动",
                title="活动取消通知",
                content="很抱歉，《绘本共读》活动因故取消，已付费用将全额退款。",
                ref_type="activity",
                ref_id="925",
                read_at=now - timedelta(hours=11),
                wechat_status=Notification.WECHAT_FAILED,
                wechat_error="用户未订阅（演示失败态）",
                create_time=now - timedelta(hours=11),
            ),
            dict(
                scene="money.deposit_paid",
                category="资金",
                title="押金到账",
                content="押金 1,200.00 元已到账，可正常借阅实体书。",
                ref_type="order",
                ref_id="926",
                read_at=now - timedelta(hours=12),
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=12),
            ),
            dict(
                scene="other.evaluation_uploaded",
                category="其他",
                title="评估报告已上传",
                content="孩子的观察期阅读评估报告已上传（共 6 张图），点击查看。",
                ref_type="observation_report",
                ref_id="927",
                read_at=now - timedelta(hours=13),
                wechat_status=Notification.WECHAT_SKIPPED,
                create_time=now - timedelta(hours=13),
            ),
        ]
        for item in demo_notifs:
            _upsert_notification(db, parent, **item)
        db.commit()
        print(
            "WM11 演示数据重建完成：通知 19 条（未读 10 / 已读 9；8 分类全覆盖；"
            "wechat skipped 13 / failed 3 / sent 3），运行记录 3 条（success/failed/skipped）；"
            "其余 9 任务保持『从未运行』态。"
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
