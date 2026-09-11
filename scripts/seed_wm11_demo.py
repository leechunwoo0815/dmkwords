# scripts/seed_wm11_demo.py — WM11 演示数据一键重建（UX 返工 Q5 裁决）
"""覆盖四态供验收：通知（未读/已读 × wechat skipped/failed/sent）、
运行记录（success/failed/skipped）、任务"从未运行"态（不造即天然存在）。

幂等：INSERT IGNORE（唯一索引去重），可重跑。用法：python -m scripts.seed_wm11_demo
"""

from __future__ import annotations

import os
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

    # 1) 积分流水（无唯一索引，先查后插）。19 号 T-B：创建时直挂 related_id——
    # 原先插后补（UPDATE WHERE related_id IS NULL）在 SessionLocal autoflush=False
    # 下单次运行必扑空（教训 41 同族第三犯，用户目视 +0 积分实锤），
    # 直挂即真链路入账口径（E-20260904-01）。
    # 锚点与 journey 同款 title 定位（Brown Bear）——books[0] 在脏库（测试残留
    # 书占前位）下会与 journey 演示三书错位，get_quiz points_added 挂空。
    points_anchor = (
        db.query(Book).filter(Book.is_deleted == 0, Book.title.like("Brown Bear%")).first()
        or books[0]
    )
    if not db.query(PointLedger).filter(PointLedger.child_id == child.id).first():
        db.add(
            PointLedger(
                child_id=child.id,
                points=5,
                reason_type="quiz_first_pass",
                related_id=points_anchor.id,
                detail="演示：首次通过测验",
            )
        )
        db.add(
            PointLedger(
                child_id=child.id,
                points=3,
                reason_type="quiz_full_marks",
                related_id=points_anchor.id,
                detail="演示：测验满分",
            )
        )
        db.add(
            PointLedger(
                child_id=child.id,
                points=2,
                reason_type="words_convert",
                related_id=points_anchor.id,
                detail="演示：词数兑换",
            )
        )
    # 存量自愈（19 号 T-B 择 a 保留）：补挂历史版本留下的 related_id=NULL 行——
    # 行已在库（跨事务），execute 能命中；全体有值后本段天然空转 0 行，保留无害
    db.execute(
        update(PointLedger)
        .where(
            PointLedger.child_id == child.id,
            PointLedger.related_id.is_(None),
            PointLedger.reason_type.in_(["quiz_first_pass", "quiz_full_marks", "words_convert"]),
            PointLedger.detail.like("演示：%"),
        )
        .values(related_id=points_anchor.id)
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
    # 19 号 T-C：同表循环同秒也是假数据特征（book1/2 submitted_at 曾同秒）——
    # 逐书递增 3 小时，跨表+同表全错开
    for idx, (prefix, finished, score, total_q, passed) in enumerate(targets):
        book = db.query(Book).filter(Book.is_deleted == 0, Book.title.like(f"{prefix}%")).first()
        if not book:
            continue
        total = int(book.audio_duration_seconds or 6)
        cov = total if finished else max(1, int(total * 0.6))
        now = datetime.now() - timedelta(hours=idx * 3)

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


def _ensure_demo_t41_data(db: Session, child) -> None:
    """R3（插修 15）：T41 六项复核/押金动线演示数据——此前 seed 无逾期书与
    独立押金孩，目视无法复验复核拦截。真实链路造数（E-20260904-01 铁律）：
    ① 演示孩 1 本逾期借阅（BorrowRecord OVERDUE + due_at 过去 3 天 + copy borrowed）
    ② 独立押金孩（WM3 异常态家长名下 formal + _ensure_demo_deposit 复用）。"""
    from backend.domain.catalog.models import Book, BookCopy
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.identity.models import Child

    # ① 逾期书（title 定位演示书——19 号 T-B 教训：不占 id 前位）
    overdue_exists = (
        db.query(BorrowRecord)
        .filter(
            BorrowRecord.child_id == child.id,
            BorrowRecord.status == BorrowRecord.STATUS_OVERDUE,
            BorrowRecord.is_deleted == 0,
        )
        .first()
    )
    if not overdue_exists:
        book = (
            db.query(Book)
            .filter(Book.is_deleted == 0, Book.status == Book.STATUS_ON)
            .order_by(Book.id.desc())
            .first()
        )
        if book:
            copy = (
                db.query(BookCopy)
                .filter(BookCopy.book_id == book.id, BookCopy.is_deleted == 0)
                .first()
            )
            if not copy:
                copy = BookCopy(
                    book_id=book.id,
                    copy_code=f"DEMO-OVERDUE-{book.id}",
                    status=BookCopy.STATUS_AVAILABLE,
                )
                db.add(copy)
                db.flush()
            copy.status = BookCopy.STATUS_BORROWED
            db.add(
                BorrowRecord(
                    child_id=child.id,
                    copy_id=copy.id,
                    book_id=book.id,
                    status=BorrowRecord.STATUS_OVERDUE,
                    borrowed_at=datetime.now() - timedelta(days=17),
                    due_at=datetime.now() - timedelta(days=3),
                )
            )
            db.flush()

    # ② 押金孩（formal + 演示押金——T41 待结清/押金退款动线可目视）
    wm3_parent = (
        db.query(Parent).filter(Parent.phone == "13800007777", Parent.is_deleted == 0).first()
    )
    if wm3_parent:
        dep_kid = (
            db.query(Child)
            .filter(Child.parent_id == wm3_parent.id, Child.name == "押金孩", Child.is_deleted == 0)
            .first()
        )
        if not dep_kid:
            dep_kid = Child(
                parent_id=wm3_parent.id,
                name="押金孩",
                member_status=Child.MEMBER_FORMAL,
                member_expire=datetime.now().date() + timedelta(days=365),
            )
            db.add(dep_kid)
            db.flush()
        _ensure_demo_deposit(db, dep_kid)


def _ensure_activity_covers(db: Session) -> None:
    """R4（插修 15）：双演示活动补绘本风封面（gen_cover 同款；幂等——
    cover_path 非空跳过）。轮播 cover_path.isnot(None) 命中→首页有真数据。"""
    import secrets

    from backend.common.file_storage import _uploads_root
    from backend.domain.activity.models import Activity
    from scripts.seed_demo_library import gen_cover

    acts = db.query(Activity).filter(Activity.is_deleted == 0, Activity.cover_path.is_(None)).all()
    os.makedirs(os.path.join(_uploads_root(), "cover", "activity"), exist_ok=True)
    for i, a in enumerate(acts):
        data = gen_cover(a.title, "DmkWords 演示", i, "线下活动")
        rel = f"cover/activity/{a.id}_{secrets.token_hex(6)}.jpg"
        with open(os.path.join(_uploads_root(), rel), "wb") as f:
            f.write(data)
        a.cover_path = rel
        db.flush()


def _ensure_demo_activity(db: Session) -> None:
    """T47-2（gate p0batch4 核验首战命中）：活动演示造数——此前 seed 从不含活动，
    基线 1 系 WM13 验收时手动创建，T40 BDD 清库后永久丢失。补幂等造数：
    付费活动（PUBLISHED 未开始，演示家长孩子可报名动线）+ 免费（轮播无封面占位）。"""
    from backend.domain.activity.models import Activity

    now = datetime.now()
    start = now + timedelta(days=3)
    a1 = (
        db.query(Activity)
        .filter(Activity.title == "周末英文绘本读书会（演示）", Activity.is_deleted == 0)
        .first()
    )
    if not a1:
        db.add(
            Activity(
                title="周末英文绘本读书会（演示）",
                activity_type="book_club",
                start_at=start,
                location="馆内一层阅读区",
                max_quota=20,
                fee=Decimal("50"),
                description="WM13 演示动线：报名→收款确认→签到→退款全链可复验。",
                member_only=False,
                status=Activity.STATUS_PUBLISHED,
            )
        )
    a2 = (
        db.query(Activity)
        .filter(Activity.title == "亲子共读体验课（演示）", Activity.is_deleted == 0)
        .first()
    )
    if not a2:
        db.add(
            Activity(
                title="亲子共读体验课（演示）",
                activity_type="parent_child",
                start_at=start + timedelta(days=1),
                location="馆内二层活动室",
                max_quota=15,
                fee=Decimal("0"),
                description="免费活动演示（家长端直接报名，无收款单）。",
                member_only=False,
                status=Activity.STATUS_PUBLISHED,
            )
        )
    db.flush()


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


def _seed_passed_book(db: Session, child, book, words_at: datetime) -> None:
    """一本书完整通过旅程（三表一致铁律 E-20260904-01）：ReadingProgress 读完
    → QuizAttempt passed → WordsLedger 入账 + PointLedger 首过加分。时间戳错开
    禁同秒（E-20260904-01 假数据特征）。幂等：WordsLedger 唯一索引 IGNORE，
    其余先查后插/upsert。"""
    from backend.domain.growth.models import PointLedger, QuizAttempt, WordsLedger
    from backend.domain.reading.models import ReadingProgress

    total = int(book.audio_duration_seconds or 6)
    finished_at = words_at - timedelta(hours=5)
    progress = (
        db.query(ReadingProgress)
        .filter(ReadingProgress.child_id == child.id, ReadingProgress.book_id == book.id)
        .first()
    )
    if progress:
        progress.intervals = f"[[0,{total}]]"
        progress.coverage_seconds = total
        progress.total_seconds = total
        progress.finished = 1
        progress.finished_at = finished_at
        progress.last_position = total
        progress.last_report_at = finished_at
    else:
        db.add(
            ReadingProgress(
                child_id=child.id,
                book_id=book.id,
                intervals=f"[[0,{total}]]",
                coverage_seconds=total,
                total_seconds=total,
                finished=1,
                finished_at=finished_at,
                last_position=total,
                last_report_at=finished_at,
            )
        )
    db.flush()
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
                score=4,
                total_questions=5,
                passed=1,
                snapshot="[]",
                submitted_at=words_at - timedelta(hours=2),
            )
        )
        db.flush()
    db.execute(
        mysql_insert(WordsLedger)
        .values(
            child_id=child.id,
            book_id=book.id,
            word_count=book.word_count,
            source="quiz",
            created_at=words_at,
        )
        .prefix_with("IGNORE")
    )
    if (
        not db.query(PointLedger)
        .filter(
            PointLedger.child_id == child.id,
            PointLedger.related_id == book.id,
            PointLedger.reason_type == "quiz_first_pass",
            PointLedger.is_deleted == 0,
        )
        .first()
    ):
        db.add(
            PointLedger(
                child_id=child.id,
                points=5,
                reason_type="quiz_first_pass",
                related_id=book.id,
                detail="演示：首次通过测验",
                created_at=words_at - timedelta(hours=2),
            )
        )
    db.flush()


def _sync_growth_state(db: Session, child) -> None:
    """成长汇总与流水对齐（words/books/points 均按真实入账求和——禁手拍数字）。"""
    from backend.domain.growth.models import ChildGrowthState, PointLedger, WordsLedger

    words_total = (
        db.query(func.sum(WordsLedger.word_count))
        .filter(WordsLedger.child_id == child.id, WordsLedger.is_deleted == 0)
        .scalar()
        or 0
    )
    books_total = (
        db.query(func.count(WordsLedger.id))
        .filter(WordsLedger.child_id == child.id, WordsLedger.is_deleted == 0)
        .scalar()
        or 0
    )
    points_total = (
        db.query(func.sum(PointLedger.points))
        .filter(PointLedger.child_id == child.id, PointLedger.is_deleted == 0)
        .scalar()
        or 0
    )
    state = db.query(ChildGrowthState).filter(ChildGrowthState.child_id == child.id).first()
    if not state:
        state = ChildGrowthState(child_id=child.id, level="A")
        db.add(state)
    state.words_total = int(words_total)
    state.books_total = int(books_total)
    state.points_total = int(points_total)
    db.flush()


def _ensure_wm4_10_acceptance_data(db: Session) -> None:
    """WM4-10 批量验收补数（2026-09-09 验收前盘点缺口落地，全部真实链路/三表一致）：

    ① 小红（演示家长名下 none 孩）——WM6-3/5 切换+音频拦截、WM9-2 双孩报名
    ② Activity 1 报名三态——演示孩/押金孩 enrolled+paid（WM9-5 签到、7 退款、16 造单 422），
       观察期孩 checked_in（WM9-6 已签到退款被拒）；enroll→confirm_payment→signin 真链
    ③ 过期孩在借一本书——WM9-22 担保语义（过期仅在借书可听；非在借书 403 同孩可测）
    ④ 榜单对比数据——观察期孩 本周480+上周180、押金孩 上周260（周榜缺席对照）、
       退会孩 历史1200（总榜"历史学员"标签）→ 周/月/总/进步四榜排序两两不同
    ⑤ 演示孩生词本 2 词（ham/adventure）——WM8-3/4 查词收录动线
    ⑥ 退款演示孩押金 1200——WM10-4 退会通过后押金退款单自动生成链
    时效性数据（90 分钟边界活动/refunded 报名/pending 报名）不预造——验收时
    用户自建更真实（清单注明步骤）。"""
    from types import SimpleNamespace

    from backend.domain.activity.models import Activity, ActivityEnrollment
    from backend.domain.activity.service import ActivityService
    from backend.domain.catalog.models import Book, BookCopy
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.identity.models import Child
    from backend.domain.identity.order_service import OrderService
    from backend.domain.reading.models import Vocabulary

    admin = db.query(AdminUser).filter(AdminUser.username == "admin").first()
    demo_parent = (
        db.query(Parent).filter(Parent.phone == "13800008888", Parent.is_deleted == 0).first()
    )
    wm3_parent = (
        db.query(Parent).filter(Parent.phone == "13800007777", Parent.is_deleted == 0).first()
    )
    wm13_parent = (
        db.query(Parent).filter(Parent.phone == "13800006666", Parent.is_deleted == 0).first()
    )
    if not (admin and demo_parent and wm3_parent and wm13_parent):
        print("c WM4-10 验收补数跳过：依赖家长/admin 未就位（先跑完整 seed）", flush=True)
        return

    def _kid(parent_id: int, name: str, status: str, expire=None, english: str | None = None):
        c = (
            db.query(Child)
            .filter(Child.parent_id == parent_id, Child.name == name, Child.is_deleted == 0)
            .first()
        )
        if not c:
            c = Child(
                parent_id=parent_id,
                name=name,
                english_name=english,
                member_status=status,
                member_expire=expire,
            )
            db.add(c)
            db.flush()
        return c

    # ① 小红
    _kid(demo_parent.id, "小红", Child.MEMBER_NONE)

    # ② 活动报名三态（真实链路：enroll→confirm_payment→[signin]）
    act = (
        db.query(Activity)
        .filter(Activity.title == "周末英文绘本读书会（演示）", Activity.is_deleted == 0)
        .first()
    )
    tickets: dict[str, str] = {}
    if act and act.start_at > datetime.now():
        obs_kid = _kid(wm3_parent.id, "观察期孩", Child.MEMBER_OBSERVATION)
        dep_kid = _kid(wm3_parent.id, "押金孩", Child.MEMBER_FORMAL)
        demo_child = (
            db.query(Child)
            .filter(
                Child.parent_id == demo_parent.id, Child.name == "演示孩", Child.is_deleted == 0
            )
            .first()
        )

        def _ensure_enrollment(kid, checked_in: bool) -> None:
            e = (
                db.query(ActivityEnrollment)
                .filter(
                    ActivityEnrollment.activity_id == act.id,
                    ActivityEnrollment.child_id == kid.id,
                    ActivityEnrollment.status.in_(ActivityEnrollment.ACTIVE_STATUSES),
                    ActivityEnrollment.is_deleted == 0,
                )
                .first()
            )
            if e:
                tickets[kid.name] = e.ticket_code
                return
            result = ActivityService(db).enroll(kid, act.id)
            if result["order_id"]:
                OrderService(db).confirm_payment(
                    admin,
                    result["order_id"],
                    SimpleNamespace(pay_method="scan", remark="验收演示：活动费收款确认"),
                )
            e = (
                db.query(ActivityEnrollment)
                .filter(
                    ActivityEnrollment.order_id == result["order_id"],
                    ActivityEnrollment.is_deleted == 0,
                )
                .first()
            )
            if checked_in and e:
                ActivityService(db).signin(admin, e.ticket_code)
            if e:
                tickets[kid.name] = e.ticket_code

        if demo_child is not None:
            _ensure_enrollment(demo_child, checked_in=False)  # WM9-7 退款矩阵载体
        _ensure_enrollment(dep_kid, checked_in=False)  # WM9-5 签到载体（enrolled 待签）
        _ensure_enrollment(obs_kid, checked_in=True)  # WM9-6 已签到退款被拒
    elif act:
        print("c 活动报名三态跳过：演示活动已开始（重跑 seed 可重建）", flush=True)

    # ③ 过期孩在借（担保语义：过期仅在借书可听）
    expired_kid = _kid(wm3_parent.id, "过期孩", Child.MEMBER_EXPIRED)
    has_active_borrow = (
        db.query(BorrowRecord)
        .filter(
            BorrowRecord.child_id == expired_kid.id,
            BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
            BorrowRecord.is_deleted == 0,
        )
        .first()
    )
    if not has_active_borrow:
        book = (
            db.query(Book)
            .filter(
                Book.is_deleted == 0,
                Book.status == Book.STATUS_ON,
                Book.audio_path.isnot(None),
                Book.title.like("The Magic School Bus%"),
            )
            .first()
        )
        if book is None:
            book = (
                db.query(Book)
                .filter(
                    Book.is_deleted == 0,
                    Book.status == Book.STATUS_ON,
                    Book.audio_path.isnot(None),
                )
                .first()
            )
        if book:
            copy = (
                db.query(BookCopy)
                .filter(
                    BookCopy.book_id == book.id,
                    BookCopy.status == BookCopy.STATUS_AVAILABLE,
                    BookCopy.is_deleted == 0,
                )
                .first()
            )
            if copy is None:
                copy = BookCopy(
                    book_id=book.id,
                    copy_code=f"DEMO-EXPIRED-{book.id}",
                    status=BookCopy.STATUS_AVAILABLE,
                )
                db.add(copy)
                db.flush()
            copy.status = BookCopy.STATUS_BORROWED
            db.add(
                BorrowRecord(
                    child_id=expired_kid.id,
                    copy_id=copy.id,
                    book_id=book.id,
                    borrowed_at=datetime.now() - timedelta(days=5),
                    due_at=datetime.now() + timedelta(days=25),
                    status=BorrowRecord.STATUS_ACTIVE,
                )
            )
            db.flush()

    # ④ 榜单对比数据（周一边界锚定，重跑不过期错位）
    #    演示孩 journey 词账时间戳冻结在首次 seed 时（IGNORE 不刷新），不可依赖其入周榜
    #    ——故待评估孩补一笔本周词数，保证周榜/进步榜也有 2 人可对比排序
    today = datetime.now().date()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    obs_kid = _kid(wm3_parent.id, "观察期孩", Child.MEMBER_OBSERVATION)
    pend_kid = _kid(wm3_parent.id, "待评估孩", Child.MEMBER_PENDING_EVALUATION)
    dep_kid = _kid(wm3_parent.id, "押金孩", Child.MEMBER_FORMAL)
    alumni_kid = _kid(wm3_parent.id, "退会孩", Child.MEMBER_WITHDRAWN, None, english="Alumni")
    board_plan = (
        # (孩, 书 title 前缀, 词数入账时间)
        (
            obs_kid,
            "Frog and Toad%",
            datetime.combine(this_monday, datetime.min.time()) + timedelta(hours=10),
        ),
        (
            obs_kid,
            "The Snowy Day%",
            datetime.combine(last_monday, datetime.min.time())
            + timedelta(days=2, hours=10),  # 上周三（锚周一可能落在上月——月榜对照需要九月内）
        ),
        (
            pend_kid,
            "Chicka Chicka%",
            datetime.combine(this_monday, datetime.min.time()) + timedelta(hours=16),
        ),
        (
            dep_kid,
            "If You Give a Mouse%",
            datetime.combine(last_monday, datetime.min.time()) + timedelta(days=3, hours=15),
        ),
        (
            alumni_kid,
            "Nate the Great%",
            datetime.combine(today - timedelta(days=40), datetime.min.time()) + timedelta(hours=16),
        ),
    )
    for kid, prefix, when in board_plan:
        book = db.query(Book).filter(Book.is_deleted == 0, Book.title.like(prefix)).first()
        if book:
            _seed_passed_book(db, kid, book, when)
    for kid in (obs_kid, pend_kid, dep_kid, alumni_kid):
        _sync_growth_state(db, kid)

    # ⑤ 生词本（查词收录演示——WM8-3 播放页查 adventure、WM6-F 查 ham）
    demo_child = (
        db.query(Child)
        .filter(Child.parent_id == demo_parent.id, Child.name == "演示孩", Child.is_deleted == 0)
        .first()
    )
    if demo_child is not None:
        source_book = (
            db.query(Book).filter(Book.is_deleted == 0, Book.title.like("Brown Bear%")).first()
        )
        for word in ("ham", "adventure"):
            exists = (
                db.query(Vocabulary)
                .filter(
                    Vocabulary.child_id == demo_child.id,
                    Vocabulary.word == word,
                    Vocabulary.is_deleted == 0,
                )
                .first()
            )
            if not exists and source_book:
                db.add(Vocabulary(child_id=demo_child.id, word=word, book_id=source_book.id))
        db.flush()

    # ⑥ 退款演示孩押金（WM10-4 退会通过 → 押金退款单自动生成 + WM10-5 审核退余额）
    refund_kid = (
        db.query(Child)
        .filter(
            Child.parent_id == wm13_parent.id, Child.name == "退款演示孩", Child.is_deleted == 0
        )
        .first()
    )
    if refund_kid is not None:
        _ensure_demo_deposit(db, refund_kid)

    db.commit()
    ticket_note = "；券码 " + " / ".join(f"{k}={v}" for k, v in tickets.items()) if tickets else ""
    print(f"c WM4-10 验收补数完成{ticket_note}", flush=True)


def _ensure_demo_circle_rank(db: Session) -> None:
    """WM14-B 周榜演示：造两周真实链路数据，供上榜卡/上升卡/周报卡/突破卡验收。

    真链路纪律（E-20260904-01）：词数只从 WordsLedger 入账、快照由
    CircleSnapshotService **真跑**（不直插快照表）——历史周靠 today 注入结算，
    与线上定时任务同一段代码。书目复用既有 35 本（不新增，保核验基线不变）。
    幂等：上一完整周快照已存在即整体跳过。
    """
    from datetime import datetime as _dt
    from datetime import time as _time
    from datetime import timedelta as _td

    from backend.domain.catalog.models import Book
    from backend.domain.growth.models import WordsLedger
    from backend.domain.identity.models import Child
    from backend.domain.reading_circle.models import CircleRankSnapshot
    from backend.domain.reading_circle.snapshot_service import CircleSnapshotService

    today = _dt.now().date()
    this_monday = today - _td(days=today.weekday())
    last_monday = this_monday - _td(days=7)
    prev_monday = last_monday - _td(days=7)

    done = (
        db.query(CircleRankSnapshot.id).filter(CircleRankSnapshot.week_start == prev_monday).first()
    )
    if done:
        print("c 阅读圈周榜快照已存在，跳过", flush=True)
        return

    def _avail_books(child_id: int):
        """该孩子尚未入账的在架书目，按词数倒序（复用既有 35 本，不新建）。"""
        used = {
            r[0]
            for r in db.query(WordsLedger.book_id).filter(WordsLedger.child_id == child_id).all()
        }
        rows = (
            db.query(Book)
            .filter(Book.is_deleted == 0, Book.status == Book.STATUS_ON, Book.word_count > 0)
            .order_by(Book.word_count.desc())
            .all()
        )
        return [b for b in rows if b.id not in used]

    # 剧本：上上周 观察期孩读大书(#1)、演示孩读小书(#2)；上周反过来 → 演示孩 2→1（上升 1 位）。
    # 词数一律取书目真实 word_count（真链路：ledger.word_count ≡ 该书总词数，禁自定值）。
    plan = [
        ("演示孩", "big", last_monday + _td(days=2)),
        ("演示孩", "small", prev_monday + _td(days=2)),
        ("观察期孩", "small", last_monday + _td(days=3)),
        ("观察期孩", "big", prev_monday + _td(days=3)),
    ]
    for child_name, size, day in plan:
        child = db.query(Child).filter(Child.name == child_name, Child.is_deleted == 0).first()
        if not child:
            continue
        avail = _avail_books(child.id)
        if not avail:
            continue
        book = avail[0] if size == "big" else avail[-1]
        db.execute(
            mysql_insert(WordsLedger)
            .values(
                child_id=child.id,
                book_id=book.id,
                word_count=book.word_count,
                source="quiz",
                created_at=_dt.combine(day, _time(10, 0)),
            )
            .prefix_with("IGNORE")
        )
        db.flush()  # autoflush=False：不 flush 则下轮选书仍看不到本行（教训 41 同族）

    svc = CircleSnapshotService(db)
    # 结算上上周：today 需落在 [上周一, 本周一) 才会把「上上周」判为上一完整周
    n_prev = svc.run_weekly_snapshot(today=last_monday + _td(days=3))
    n_last = svc.run_weekly_snapshot(today=today)  # 结算上周
    print(f"c 阅读圈周榜快照：上上周 {n_prev} 条 / 上周 {n_last} 条", flush=True)


def _ensure_demo_circle_visuals(db: Session) -> None:
    """WM15 演示：① 演示孩子内置头像（avatar_id）② 存量帖缩略图对齐当前规格（B3/fix33 R1）。

    真链路纪律：头像只写白名单 id；缩略图由 card_engine 用**冻结的 card_data 快照**
    重渲（**大图 image_path 一律不动**）。幂等：头像已设即跳、缩略图已是当前规格即跳。
    """
    from backend.domain.identity.models import Child
    from backend.domain.reading_circle import card_engine
    from backend.domain.reading_circle.models import CirclePost

    # ① 头像（让演示账号进页面就能看到新头像库；按名字稳定定位）
    avatar_plan = {
        "演示孩": "cat_sun",
        "观察期孩": "panda_sun",
        "押金孩": "dino_mint",
        "小红": "bunny_sun",
        "退会孩": "owl_sun",
        "退款演示孩": "fox_mint",
    }
    n_avatar = 0
    for name, aid in avatar_plan.items():
        child = db.query(Child).filter(Child.name == name, Child.is_deleted == 0).first()
        if child and not child.avatar:
            child.avatar = aid
            n_avatar += 1

    # ①b fix34 R6 生日彩蛋：把演示孩生日对齐"今天"（月日）→ 进 TA 的名片页即可看见 🎂；
    # 演示账号带订单 → 管理端生日字段被锁（已创建订单的孩子锁定姓名/性别/生日），
    # 故由 seed 造这个演示条件；已是今天的月日则跳过（幂等）
    n_bday = 0
    demo_kid = db.query(Child).filter(Child.name == "演示孩", Child.is_deleted == 0).first()
    if demo_kid:
        today = datetime.now().date()
        if not demo_kid.birthday or (demo_kid.birthday.month, demo_kid.birthday.day) != (
            today.month,
            today.day,
        ):
            try:
                demo_kid.birthday = today.replace(year=2019)  # 演示用固定出生年
            except ValueError:  # 2/29 落在非闰年 → 换闰年
                demo_kid.birthday = today.replace(year=2020)
            n_bday = 1

    # ② 缩略图对齐当前规格（fix33 R1：缺图补渲 / 旧规格重渲并删旧文件；幂等）
    thumbs = card_engine.ensure_circle_thumbs(db)
    # ②b fix34d：馆长赞与计数对齐——`admin_liked=1` 必须计入 like_count（真链路
    # `admin_like()` 就是这么 +1 的），否则点赞墙出现「馆长头像 + 计数 0」的自相矛盾
    n_admin = (
        db.query(CirclePost)
        .filter(CirclePost.is_deleted == 0, CirclePost.admin_liked == 1, CirclePost.like_count == 0)
        .update({CirclePost.like_count: 1}, synchronize_session=False)
    )

    # ②c fix34e：馆长赞的「金光播报」演示——演示帖带 admin_liked 但库里没有对应通知
    # （seed 直接写标志位，不走 admin_like 真链路）→ 按真链路口径补一条馆长赞通知，
    # 进阅读圈即会弹一次金光播报（幂等：唯一键 parent+scene+ref_type+ref_id+dedup_key）
    n_admin_note = 0
    for post in (
        db.query(CirclePost).filter(CirclePost.is_deleted == 0, CirclePost.admin_liked == 1).all()
    ):
        owner = db.query(Parent).filter(Parent.id == post.parent_id).first()
        if not owner:
            continue
        child = db.query(Child).filter(Child.id == post.child_id).first()
        before = (
            db.query(Notification)
            .filter(
                Notification.parent_id == owner.id,
                Notification.scene == "circle.liked",
                Notification.ref_type == "circle_admin",
                Notification.ref_id == str(post.id),
                Notification.is_deleted == 0,
            )
            .count()
        )
        _upsert_notification(
            db,
            owner,
            scene="circle.liked",
            category="其他",
            title="馆长为你点赞",
            content=f"馆长赞了 {child.english_name or f'小朋友{child.id:03d}'} 的成就"
            if child
            else "馆长赞了孩子的成就",
            child_id=post.child_id,
            ref_type="circle_admin",
            ref_id=str(post.id),
            dedup_key="admin",
        )
        if not before:
            n_admin_note += 1
    db.commit()

    # ③ fix34 R5：演示里程碑帖补齐「全馆第 N 位」快照（老 card_data 无此字段 →
    # 副标题/卡面都缺播报）。演示数据专享的一次性补齐：重算 + 重渲双规格 + 删旧图；
    # 补齐后字段已在快照里 → 重跑即跳（幂等）。**生产历史帖按"无字段不显示"容错，不动。**
    n_hall = _backfill_demo_hall_rank(db)
    print(
        f"c 阅读圈视觉：头像设置 {n_avatar} 个 / 缩略图重渲 {thumbs['rendered']} 张"
        f"（跳过 {thumbs['skipped']}）/ 里程碑播报补齐 {n_hall} 帖 / 生日彩蛋对齐 {n_bday} 人"
        f" / 馆长赞计数对齐 {n_admin} 帖 / 馆长赞通知补 {n_admin_note} 条",
        flush=True,
    )


def _backfill_demo_hall_rank(db: Session) -> int:
    """给缺 hall_rank 的**演示**里程碑帖补「全馆第 N 位达成」并重渲双规格（幂等）。"""
    import json
    import os

    from backend.domain.reading_circle import card_engine
    from backend.domain.reading_circle.models import CirclePost

    posts = (
        db.query(CirclePost)
        .filter(
            CirclePost.is_deleted == 0,
            CirclePost.card_type == CirclePost.CARD_MILESTONE,
        )
        .all()
    )
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
    n = 0
    for post in posts:
        data = json.loads(post.card_data or "{}")
        if data.get("hall_rank"):
            continue  # 已补齐（幂等）
        award = (
            db.query(card_engine.MilestoneAward)
            .filter(
                card_engine.MilestoneAward.child_id == post.child_id,
                card_engine.MilestoneAward.is_deleted == 0,
            )
            .order_by(card_engine.MilestoneAward.id.desc())
            .first()
        )
        if not award:
            continue
        rank = card_engine.milestone_hall_rank(db, award)
        data["hall_rank"] = rank
        data["value_label"] = f"累计有效阅读词数 · 全馆第 {rank} 位达成"
        post.card_data = json.dumps(data, ensure_ascii=False)
        rendered = card_engine.render_card(data)  # 版式更新 → 大图与缩略图一起重出
        old = [post.image_path, post.thumb_path]
        post.image_path = rendered["image_path"]
        post.thumb_path = rendered["thumb_path"]
        for rel in old:
            full = os.path.abspath(os.path.join(root, rel or ""))
            if rel and full.startswith(root) and os.path.isfile(full):
                try:
                    os.remove(full)
                except OSError:
                    pass  # 删不掉只留孤儿文件，不影响正确性
        n += 1
    db.commit()
    return n


def _ensure_demo_circle(db: Session) -> None:
    """WM14-A 阅读圈演示帖：3 帖覆盖三类卡 + 金色态/置顶样例（验收步骤 13/18 用）。

    - 演示孩 milestone 卡（admin_liked=1 馆长赞金色态样例）
    - 观察期孩 perfect_quiz 满分卡
    - 押金孩 streak 连击卡（is_pinned=1 置顶样例）
    幂等：成就/帖子均先查后插；不预造点赞（用户验收自己点）。
    卡片图走 card_engine 真实渲染管线（零 UGC——后端生成）。"""
    from backend.domain.catalog.models import Book
    from backend.domain.growth.models import (
        CheckinStreakRecord,
        MilestoneAward,
        QuizAttempt,
    )
    from backend.domain.reading_circle import card_engine
    from backend.domain.reading_circle.models import CirclePost

    def _kid_by_name(parent_id: int, name: str):
        return (
            db.query(Child)
            .filter(Child.parent_id == parent_id, Child.name == name, Child.is_deleted == 0)
            .first()
        )

    demo_parent = db.query(Parent).filter(Parent.phone == "13800008888").first()
    wm3_parent = db.query(Parent).filter(Parent.phone == "13800007777").first()
    if not (demo_parent and wm3_parent):
        print("c 阅读圈演示帖跳过：依赖家长未就位（先跑完整 seed）", flush=True)
        return
    demo_child = (
        db.query(Child)
        .filter(Child.parent_id == demo_parent.id, Child.name == "演示孩", Child.is_deleted == 0)
        .first()
    )
    obs_kid = _kid_by_name(wm3_parent.id, "观察期孩")
    dep_kid = _kid_by_name(wm3_parent.id, "押金孩")
    if not (demo_child and obs_kid and dep_kid):
        print("c 阅读圈演示帖跳过：依赖孩子未就位（先跑完整 seed）", flush=True)
        return

    def _ensure_post(child, parent_id, card_type, ref_id, *, admin_liked=0, is_pinned=0):
        exists = (
            db.query(CirclePost)
            .filter(
                CirclePost.child_id == child.id,
                CirclePost.card_type == card_type,
                CirclePost.ref_id == ref_id,
            )
            .first()
        )
        if exists:
            return False
        card_data = card_engine.assemble_card_data(db, child, card_type, ref_id)
        # WM15-R2：render_card 返回**双规格 dict**（含字大图 + 无字缩略图）——两列都落
        rendered = card_engine.render_card(card_data)
        db.add(
            CirclePost(
                parent_id=parent_id,
                child_id=child.id,
                card_type=card_type,
                ref_id=ref_id,
                card_data=card_engine.card_data_json(card_data),
                image_path=rendered["image_path"],
                thumb_path=rendered["thumb_path"],
                admin_liked=admin_liked,
                is_pinned=is_pinned,
            )
        )
        db.flush()
        return True

    created = 0

    # ① 演示孩里程碑卡（造 MilestoneAward 节点 10 万词）——admin_liked=1 金色态样例
    ms = (
        db.query(MilestoneAward)
        .filter(MilestoneAward.child_id == demo_child.id, MilestoneAward.node_words == 100000)
        .first()
    )
    if not ms:
        ms = MilestoneAward(
            child_id=demo_child.id,
            node_words=100000,
            awarded_at=datetime.now() - timedelta(days=2),
        )
        db.add(ms)
        db.flush()
    created += _ensure_post(demo_child, demo_parent.id, "milestone", ms.id, admin_liked=1)

    # ② 观察期孩满分卡（造 5/5 满分测验——真实满分卡口径）
    perfect = (
        db.query(QuizAttempt)
        .filter(
            QuizAttempt.child_id == obs_kid.id, QuizAttempt.score == QuizAttempt.total_questions
        )
        .first()
    )
    if not perfect:
        book = (
            db.query(Book).filter(Book.is_deleted == 0, Book.title.like("Chicka Chicka%")).first()
        )
        if book:
            perfect = QuizAttempt(
                child_id=obs_kid.id,
                book_id=book.id,
                score=5,
                total_questions=5,
                passed=1,
                snapshot="[]",
                submitted_at=datetime.now() - timedelta(days=1, hours=3),
            )
            db.add(perfect)
            db.flush()
    if perfect:
        created += _ensure_post(obs_kid, wm3_parent.id, "perfect_quiz", perfect.id)

    # ③ 押金孩连击卡（造 7 天连击记录）——is_pinned=1 置顶样例
    streak = (
        db.query(CheckinStreakRecord).filter(CheckinStreakRecord.child_id == dep_kid.id).first()
    )
    if not streak:
        streak = CheckinStreakRecord(
            child_id=dep_kid.id,
            cycle_type="days7",
            cycle_no=1,
            streak_at=7,
            awarded_at=datetime.now() - timedelta(days=1),
        )
        db.add(streak)
        db.flush()
    created += _ensure_post(dep_kid, wm3_parent.id, "streak", streak.id, is_pinned=1)

    db.commit()
    print(f"c 阅读圈演示帖完成（新建 {created} 帖：里程碑[馆长赞]/满分/连击[置顶]）", flush=True)


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
        _ensure_demo_activity(db)
        _ensure_demo_wm3_states(db)
        # 逾期借阅依赖演示孩（if 块内造）；押金孩依赖 WM3 家长（wm3_states 造）——
        # 故 t41_data 必须在两者之后（R3 顺序教训：跨段依赖按建序排）
        _ensure_demo_t41_data(db, demo_child)
        _ensure_activity_covers(db)
        _ensure_demo_wm13_states(db)
        # WM4-10 批量验收补数（依赖：演示孩/小红家长、WM3 三孩、Activity 1、WM13 退款演示孩
        # ——必须在这些段之后；教训 65：跨段依赖按建序排）
        _ensure_wm4_10_acceptance_data(db)
        # WM14-A 阅读圈演示帖（依赖：演示孩/WM3 观察期孩+押金孩——上述段之后）
        _ensure_demo_circle(db)
        # WM14-B 周榜快照演示（依赖：上述孩子档案 + 书目；词账 → 快照任务真跑）
        _ensure_demo_circle_rank(db)
        # WM15 视觉演示（依赖：上述帖子 + 孩子档案；白名单头像 + 缩略图回填）
        _ensure_demo_circle_visuals(db)
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
