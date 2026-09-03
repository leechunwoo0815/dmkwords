# tests/unit/test_p0_t1_rc_isolation.py — P0 第一批 T1（E-00）全局隔离级别
"""RR → READ COMMITTED 红测试。

现象（外部审计 E-00）：MySQL 默认 REPEATABLE READ 下，事务内普通读建立
read view，后续 with_for_update 虽获行锁，但锁内 COUNT/SUM 守卫读旧快照，
"锁+COUNT 守卫"并发失效。

红测试结构：
- session1 锁 Child（建立事务，RR 下同时建立快照）
- session2 提交新 BorrowRecord（不涉及 Child 行锁，可提交）
- session1 锁内 COUNT active：RC 读到最新已提交（2）；RR 读旧快照（1）= RED
- 会话级 @@transaction_isolation 断言 READ-COMMITTED
"""

from datetime import datetime, timedelta

from sqlalchemy import func, text

from backend.database import get_session


def _db():
    return get_session()


def test_locked_count_reads_fresh_under_rc(session_pair):
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.identity.models import Child, Parent

    s1, s2 = session_pair

    # 造 parent + child + 1 条 active borrow（第 1 条，锁内 COUNT 基线 = 1）
    with _db() as db:
        parent = Parent(phone="13990000001")
        db.add(parent)
        db.flush()
        child = Child(name="RC隔离孩", parent_id=parent.id)
        db.add(child)
        db.flush()
        child_id = child.id
        db.add(
            BorrowRecord(
                child_id=child_id,
                book_id=1,
                copy_id=1,
                status=BorrowRecord.STATUS_ACTIVE,
                due_at=datetime.now() + timedelta(days=14),
            )
        )
        db.commit()

    # session1：锁 Child（建立事务；RR 下同步建立本事务 read view）
    locked = s1.query(Child).filter(Child.id == child_id).with_for_update().first()
    assert locked is not None
    iso = s1.execute(text("SELECT @@transaction_isolation")).scalar()
    assert iso == "READ-COMMITTED", f"会话隔离级别应 READ-COMMITTED，实 {iso}"

    # session2：插入第 2 条 active borrow 并提交（不同行，无锁冲突）
    s2.add(
        BorrowRecord(
            child_id=child_id,
            book_id=2,
            copy_id=2,
            status=BorrowRecord.STATUS_ACTIVE,
            due_at=datetime.now() + timedelta(days=14),
        )
    )
    s2.commit()

    # session1：锁内 COUNT active——RC 读最新已提交（2）；RR 读旧快照（1）= RED
    cnt = (
        s1.query(func.count(BorrowRecord.id))
        .filter(
            BorrowRecord.child_id == child_id,
            BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
        )
        .scalar()
    )
    assert cnt == 2, f"锁内 COUNT 应读最新已提交 2，实 {cnt}（RR 旧快照，守卫失效 RED）"
    s1.rollback()