# scripts/seed_wm11_demo.py — WM11 演示数据一键重建（UX 返工 Q5 裁决）
"""覆盖四态供验收：通知（未读/已读 × wechat skipped/failed/sent）、
运行记录（success/failed/skipped）、任务"从未运行"态（不造即天然存在）。

幂等：INSERT IGNORE（唯一索引去重），可重跑。用法：python -m scripts.seed_wm11_demo
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from backend.common.notification_models import Notification, TaskRunLog
from backend.database import SessionLocal
from backend.domain.admin.models import AdminUser
from backend.domain.identity.models import Child, Order, Parent


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
        # WM13 验收第二链配套：确保演示孩有一笔 paid 订单可供"申请退款→撤销"反例
        # （否则步骤 9 无单可退；幂等按 order_no 前缀查）
        child = exists
        has_paid = (
            db.query(Order)
            .filter(
                Order.child_id == child.id,
                Order.order_no.like("WM11-DEMO-%"),
                Order.is_deleted == 0,
            )
            .first()
        )
        if not has_paid:
            db.add(
                Order(
                    order_no=f"WM11-DEMO-{int(datetime.now().timestamp())}",
                    order_type=Order.TYPE_OBSERVATION,
                    parent_id=parent.id,
                    child_id=child.id,
                    amount=Decimal("500.00"),
                    status=Order.STATUS_PAID,
                )
            )
            db.commit()
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
    from backend.domain.catalog.models import Book, BookCopy, QuizQuestion
    from scripts.seed_demo_library import make_questions

    # 演示书缺题则补（测验链路可测；幂等：只给 0 题的书补）
    demo_isbns = [row[0] for row in DEMO_BOOKS]
    for b in db.query(Book).filter(Book.isbn.in_(demo_isbns), Book.is_deleted == 0).all():
        if db.query(QuizQuestion).filter(QuizQuestion.book_id == b.id).count() == 0:
            db.add_all(make_questions(b))
            db.flush()

    for isbn, title, author, words, ar, grade, topic, audio_isbn in DEMO_BOOKS:
        # 查重含软删行（ISBN 唯一索引不含 is_deleted，软删行会挡 INSERT——C50 同族）：
        # 命中软删行直接复活，不重复建书
        existing = db.query(Book).filter(Book.isbn == isbn).first()
        if existing is not None:
            if existing.is_deleted:
                existing.is_deleted = 0
                existing.status = Book.STATUS_ON
                db.flush()
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


def _ensure_demo_growth(db: Session, child) -> None:
    """演示成长数据（小程序 v5 首页任务台配套）：打卡 3 天 + 积分流水 + 在借书 40%
    听读进度。词数入账不再单表直插（E-20260904-01：假 passed 曾致三表断链），
    统一走 _ensure_demo_quiz_journey 真链路三态。幂等：先查后插/IGNORE。"""
    from backend.domain.catalog.models import Book
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.growth.models import ChildGrowthState, PointLedger, WordsLedger
    from backend.domain.reading.models import CheckIn, ReadingProgress

    books = (
        db.query(Book)
        .filter(Book.is_deleted == 0, Book.status == Book.STATUS_ON, Book.word_count > 0)
        .order_by(Book.id)
        .limit(3)
        .all()
    )
    if not books:
        return

    # 1) 积分流水（无唯一索引，先查后插）
    if not db.query(PointLedger).filter(PointLedger.child_id == child.id).first():
        db.add(
            PointLedger(
                child_id=child.id,
                points=5,
                reason_type="quiz_first_pass",
                detail="演示：首次通过测验",
            )
        )
        db.add(
            PointLedger(
                child_id=child.id, points=3, reason_type="quiz_full_marks", detail="演示：测验满分"
            )
        )
        db.add(
            PointLedger(
                child_id=child.id, points=2, reason_type="words_convert", detail="演示：词数兑换"
            )
        )
    # 插修10：演示积分补 related_id=book_id——真实链路三类入账积分都挂
    # related_id（growth/service 入账处），get_quiz points_added 按 related_id
    # 求和，缺失会让兜底成绩单显示 0 积分（E-20260904-01 真链路口径）
    db.execute(
        update(PointLedger)
        .where(
            PointLedger.child_id == child.id,
            PointLedger.related_id.is_(None),
            PointLedger.reason_type.in_(["quiz_first_pass", "quiz_full_marks", "words_convert"]),
            PointLedger.detail.like("演示：%"),
        )
        .values(related_id=books[0].id)
    )

    # 2) 打卡近 3 天（先查后插）
    have = {
        c.checkin_date
        for c in db.query(CheckIn)
        .filter(CheckIn.child_id == child.id, CheckIn.is_deleted == 0)
        .all()
    }
    for i in (0, 1, 2):
        day = (datetime.now() - timedelta(days=i)).date()
        if day in have:
            continue
        db.add(
            CheckIn(
                child_id=child.id,
                checkin_date=day,
                book_id=books[0].id,
                streak=3 - i,
                created_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=19),
            )
        )

    # 3) 在借书 40% 听读进度（先查后插）
    borrow = (
        db.query(BorrowRecord)
        .filter(
            BorrowRecord.child_id == child.id,
            BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
            BorrowRecord.is_deleted == 0,
        )
        .first()
    )
    if (
        borrow
        and not db.query(ReadingProgress)
        .filter(ReadingProgress.child_id == child.id, ReadingProgress.book_id == borrow.book_id)
        .first()
    ):
        pbook = db.query(Book).filter(Book.id == borrow.book_id).first()
        total = int(pbook.audio_duration_seconds or 6) if pbook else 6
        cov = max(1, int(total * 0.4))
        intervals_json = f"[[0,{cov}]]"
        db.add(
            ReadingProgress(
                child_id=child.id,
                book_id=borrow.book_id,
                # total 用书真实音频时长（20260830：硬编码 20s 与 4-6s 真音频不符，
                # 完播判定 coverage/total 永远不可达 → "永远读不完"）
                intervals=intervals_json,
                coverage_seconds=cov,
                total_seconds=total,
                finished=0,
                last_position=cov,
                last_report_at=datetime.now(),
            )
        )

    # 4) ChildGrowthState 同步（等级由词数决定，演示量级保持 A）——
    #    words_total 用真实入账口径（E-20260904-01：不再按书单求和）
    words_total = (
        db.query(func.sum(WordsLedger.word_count))
        .filter(WordsLedger.child_id == child.id, WordsLedger.is_deleted == 0)
        .scalar()
        or 0
    )
    state = db.query(ChildGrowthState).filter(ChildGrowthState.child_id == child.id).first()
    if not state:
        state = ChildGrowthState(child_id=child.id, level="A")
        db.add(state)
    state.words_total = words_total
    state.books_total = len(books)
    state.points_total = 10
    db.flush()


def _ensure_demo_quiz_journey(db: Session, child) -> None:
    """插修9-R7（E-20260904-01）：演示测验旅程真链路三态——演示/测试数据禁止
    只插单表造业务终态（假 passed 无 QuizAttempt/无进度曾致金卡 0 分、首进无卡、
    弹窗被拦，用户实测三项全撞）。三表一致（ReadingProgress/QuizAttempt/
    WordsLedger）且时间戳错开禁同秒（同秒批量即假数据特征）；按 title 稳定定位
    防 id 漂移；只动演示孩的演示三书，其余数据（如 book 6 用户真实通过全链）
    严禁触碰。幂等：progress 按 (child,book) upsert 对齐目标态，attempt 先查后插，
    words 唯一索引 IGNORE。"""
    from backend.domain.catalog.models import Book
    from backend.domain.growth.models import QuizAttempt, WordsLedger
    from backend.domain.reading.models import ReadingProgress

    # (title 前缀, 是否读完, 答对数, 总题数, 是否通过)
    targets = (
        ("Brown Bear", True, 5, 5, 1),  # 金卡：best_score=100 → 五星
        ("Chicka Chicka", True, 3, 5, 0),  # 蓝卡：测过未过，attempts_left=2
        ("Corduroy", False, 0, 0, 0),  # 灰态：读到 60%
    )
    for prefix, finished, score, total_q, passed in targets:
        book = db.query(Book).filter(Book.is_deleted == 0, Book.title.like(f"{prefix}%")).first()
        if not book:
            continue
        total = int(book.audio_duration_seconds or 6)
        cov = total if finished else max(1, int(total * 0.6))
        now = datetime.now()

        # 1) ReadingProgress：读完/读到一半（upsert 对齐目标态——旧演示行可能是
        #    任意态，强制收敛到演示语义）
        progress = (
            db.query(ReadingProgress)
            .filter(ReadingProgress.child_id == child.id, ReadingProgress.book_id == book.id)
            .first()
        )
        finished_at = now - timedelta(days=3, hours=2) if finished else None
        if progress:
            progress.intervals = f"[[0,{cov}]]"
            progress.coverage_seconds = cov
            progress.total_seconds = total
            progress.finished = 1 if finished else 0
            progress.finished_at = finished_at
            progress.last_position = cov
            progress.last_report_at = now - timedelta(hours=1)
        else:
            db.add(
                ReadingProgress(
                    child_id=child.id,
                    book_id=book.id,
                    intervals=f"[[0,{cov}]]",
                    coverage_seconds=cov,
                    total_seconds=total,
                    finished=1 if finished else 0,
                    finished_at=finished_at,
                    last_position=cov,
                    last_report_at=now - timedelta(hours=1),
                )
            )
        db.flush()
        if not finished:
            continue  # 未读完：不测验不入账（locked 灰态）

        # 2) QuizAttempt（先查后插；submitted_at 与 words created_at 错开）
        attempt = (
            db.query(QuizAttempt)
            .filter(QuizAttempt.child_id == child.id, QuizAttempt.book_id == book.id)
            .first()
        )
        if not attempt:
            db.add(
                QuizAttempt(
                    child_id=child.id,
                    book_id=book.id,
                    score=score,
                    total_questions=total_q,
                    passed=passed,
                    snapshot="[]",
                    submitted_at=now - timedelta(days=2, hours=5),
                )
            )
            db.flush()

        # 3) WordsLedger：仅通过书入账（get_quiz 的 passed_before 判定源）
        if passed:
            db.execute(
                mysql_insert(WordsLedger)
                .values(
                    child_id=child.id,
                    book_id=book.id,
                    word_count=book.word_count,
                    source="quiz",
                    created_at=now - timedelta(days=2, hours=3),
                )
                .prefix_with("IGNORE")
            )
    db.flush()


def _ensure_demo_fav_reservation(db: Session, child) -> None:
    """演示收藏 2 本 + 预约 1 条（书架页三 tab 有真实数据可验）。先查后插幂等。"""
    from backend.domain.catalog.models import Book
    from backend.domain.reading.models import Favorite, Reservation

    if (
        not db.query(Favorite)
        .filter(Favorite.child_id == child.id, Favorite.is_deleted == 0)
        .first()
    ):
        fav_books = (
            db.query(Book)
            .filter(Book.is_deleted == 0, Book.status == Book.STATUS_ON, Book.word_count > 500)
            .order_by(Book.word_count.desc())
            .limit(2)
            .all()
        )
        for b in fav_books:
            db.add(Favorite(child_id=child.id, book_id=b.id))
        db.flush()
    if (
        not db.query(Reservation)
        .filter(Reservation.child_id == child.id, Reservation.is_deleted == 0)
        .first()
    ):
        from backend.domain.catalog.models import BookCopy

        pick = (
            db.query(Book)
            .filter(Book.is_deleted == 0, Book.status == Book.STATUS_ON, Book.word_count > 1000)
            .order_by(Book.word_count.desc())
            .first()
        )
        if pick is not None:
            copy = (
                db.query(BookCopy)
                .filter(
                    BookCopy.book_id == pick.id,
                    BookCopy.status == BookCopy.STATUS_AVAILABLE,
                    BookCopy.is_deleted == 0,
                )
                .first()
            )
            if copy is not None:
                copy.status = BookCopy.STATUS_RESERVED
                db.add(
                    Reservation(
                        child_id=child.id,
                        book_id=pick.id,
                        copy_id=copy.id,
                        status=Reservation.STATUS_ACTIVE,
                        expires_at=datetime.now() + timedelta(hours=72),
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


def _ensure_demo_wm3_states(db: Session) -> None:
    """W10：WM3 异常态演示覆盖（幂等）——observation/pending_evaluation/expired 孩 + 1 笔待人工确认订单。

    expired 孩 = member_status formal + member_expire 昨天（D1 读时即时判定，不写 expired 状态）。
    """
    import time
    from decimal import Decimal

    from backend.domain.identity.models import Child

    parent = db.query(Parent).filter(Parent.phone == "13800007777", Parent.is_deleted == 0).first()
    if not parent:
        parent = Parent(name="WM3异常态演示", phone="13800007777")
        db.add(parent)
        db.flush()
    today = datetime.now().date()

    def ensure_child(name: str, status: str, expire) -> Child:
        c = (
            db.query(Child)
            .filter(Child.parent_id == parent.id, Child.name == name, Child.is_deleted == 0)
            .first()
        )
        if not c:
            c = Child(parent_id=parent.id, name=name, member_status=status, member_expire=expire)
            db.add(c)
            db.flush()
        return c

    obs = ensure_child("观察期孩", Child.MEMBER_OBSERVATION, today + timedelta(days=30))
    pend = ensure_child("待评估孩", Child.MEMBER_PENDING_EVALUATION, None)
    expired = ensure_child("过期孩", Child.MEMBER_FORMAL, today - timedelta(days=1))
    # WM3-D2：临期孩（formal + today+3，相对日期每次 seed 都新鲜）——验收第 19 步橙字「剩 3 天」
    expiring = ensure_child("临期孩", Child.MEMBER_FORMAL, today + timedelta(days=3))

    # 插修2 用户拍板 A：演示孩补配套订单（真实化——会员状态由订单收款驱动，
    # 真实链路 formal/observation 必有订单；否则 B1 守卫按"无订单"放行编辑删除，
    # 出现「正式会员可删」的假象）。幂等：按 child_id+type+status 查存在即跳过。

    # 插修4-X5：paid_at 按孩错开天数——比例退「剩余天数」在预估/可退卡片上可见
    # （全今天则永远接近全额，验收看不出按天折算）；exists 分支同步归一，重 seed 生效。
    def ensure_paid_order(child: Child, order_type: str, amount: str, days_ago: int = 0) -> None:
        paid_at = datetime.now() - timedelta(days=days_ago)
        exists = (
            db.query(Order)
            .filter(
                Order.child_id == child.id,
                Order.order_type == order_type,
                Order.status == Order.STATUS_PAID,
                Order.is_deleted == 0,
            )
            .first()
        )
        if exists:
            exists.paid_at = paid_at
            return
        admin_id = db.query(AdminUser).filter(AdminUser.username == "admin").first()
        db.add(
            Order(
                order_no=f"WM3-DEMO-P-{int(time.time() * 1000) % 10**10}-{child.id}",
                order_type=order_type,
                parent_id=parent.id,
                child_id=child.id,
                amount=Decimal(amount),
                status=Order.STATUS_PAID,
                pay_method="scan",
                paid_at=paid_at,
                paid_by=admin_id.id if admin_id else None,
                remark="演示数据：配套已支付订单（状态真实化）",
            )
        )
        db.flush()

    ensure_paid_order(obs, Order.TYPE_OBSERVATION, "500.00", days_ago=10)
    ensure_paid_order(pend, Order.TYPE_OBSERVATION, "500.00", days_ago=5)
    ensure_paid_order(expiring, Order.TYPE_FORMAL, "6000.00", days_ago=100)
    pending_order = (
        db.query(Order)
        .filter(
            Order.child_id == expired.id,
            Order.status == Order.STATUS_PENDING_MANUAL,
            Order.is_deleted == 0,
        )
        .first()
    )
    if not pending_order:
        db.add(
            Order(
                order_no=f"WM3-DEMO-{int(time.time())}",
                order_type=Order.TYPE_FORMAL,
                parent_id=parent.id,
                child_id=expired.id,
                amount=Decimal("6000.00"),
                status=Order.STATUS_PENDING_MANUAL,
            )
        )
    db.commit()
    print("c WM3 异常态演示：观察/待评估/过期孩 + 待确认订单", flush=True)


def _ensure_demo_wm13_states(db: Session) -> None:
    """WM13 演示数据（幂等）：1 待审退款 + 1 待审转让——走真实 service 链路（禁直改 DB）。

    RefundService.apply / TransferService.apply 内部同事务触发 AdminNotifyService.send，
    管理待办通知自然落库；幂等由业务 dup 检查 + dedup_key 唯一索引双保险。
    """
    import time
    from decimal import Decimal

    from backend.domain.identity.models import (
        Child,
        RefundRequest,
        TransferRequest,
    )
    from backend.domain.identity.transfer_service import TransferService
    from backend.domain.identity.wm10_service import RefundService

    parent = db.query(Parent).filter(Parent.phone == "13800006666", Parent.is_deleted == 0).first()
    if not parent:
        parent = Parent(name="WM13演示家长", phone="13800006666")
        db.add(parent)
        db.flush()

    def ensure_child(name: str, status: str, expire) -> Child:
        c = (
            db.query(Child)
            .filter(Child.parent_id == parent.id, Child.name == name, Child.is_deleted == 0)
            .first()
        )
        if not c:
            c = Child(parent_id=parent.id, name=name, member_status=status, member_expire=expire)
            db.add(c)
            db.flush()
        return c

    today = datetime.now().date()
    src = ensure_child("退款演示孩", Child.MEMBER_FORMAL, today + timedelta(days=180))
    transfer_src = ensure_child("转让源孩", Child.MEMBER_FORMAL, today + timedelta(days=180))
    transfer_tgt = ensure_child("转让受让孩", Child.MEMBER_NONE, None)
    # 已支付订单（observation_fee 500，演示用小额）——供退款申请挂靠
    paid = (
        db.query(Order)
        .filter(
            Order.child_id == src.id,
            Order.status == Order.STATUS_PAID,
            Order.is_deleted == 0,
        )
        .first()
    )
    if not paid:
        paid = Order(
            order_no=f"WM13-DEMO-{int(time.time())}",
            order_type=Order.TYPE_OBSERVATION,
            parent_id=parent.id,
            child_id=src.id,
            amount=Decimal("500.00"),
            status=Order.STATUS_PAID,
        )
        db.add(paid)
        db.commit()
    # 待审退款（真实链路：apply 同事务发 admin.refund_apply；重复申请会被 dup 检查拦截）
    existing = (
        db.query(RefundRequest)
        .filter(RefundRequest.child_id == src.id, RefundRequest.is_deleted == 0)
        .first()
    )
    if not existing:
        try:
            RefundService(db).apply(src, paid.id, "演示：孩子转学去外地")
        except Exception as exc:  # 演示数据容错：不因状态冲突中断 seed
            db.rollback()
            print(f"c WM13 退款演示跳过（{exc}）", flush=True)
    # 待审转让（真实链路：apply 同事务发 admin.transfer_apply）
    existing_transfer = (
        db.query(TransferRequest)
        .filter(TransferRequest.source_child_id == transfer_src.id, TransferRequest.is_deleted == 0)
        .first()
    )
    if not existing_transfer:
        try:
            TransferService(db).apply(parent, transfer_src.id, transfer_tgt.id)
        except Exception as exc:
            db.rollback()
            print(f"c WM13 转让演示跳过（{exc}）", flush=True)
    db.commit()
    print("c WM13 演示：待审退款 + 待审转让（管理待办通知已落库）", flush=True)


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
            _ensure_demo_growth(db, demo_child)
            _ensure_demo_quiz_journey(db, demo_child)
            _ensure_demo_fav_reservation(db, demo_child)
        _ensure_demo_wm3_states(db)
        _ensure_demo_wm13_states(db)
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
