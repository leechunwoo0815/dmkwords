# tests/unit/test_p0_t3_reservation_lock.py — P0 第一批 T3（E-7 提前）预约 create 补 Child 行锁
"""预约 create 并发竞态红测试。

现象（E-7 + 任务包 T3）：ReservationService.create 全程无 Child 行锁，
且 Reservation 表无唯一索引兜底（child_id/book_id 仅 index）——
并发双预约同书（check-then-insert 无锁串行化）→ 双 active 预约。
T1 的 RC 也修不了：两事务同时 dup=0（未提交互不可见）→ 都插入。

结构（线程真并发，模拟先到者持锁进行中）：
- s1 锁 Child + 插入 active 预约（未提交）——模拟先到者 A 进行中
- B 线程走 ReservationService.create（同书）：
  - 修复前：无 Child 锁 → 不阻塞 → dup 查不到 A 未提交 → 插入成功（双预约 RED）
  - 修复后：create 入口锁 Child 阻塞 → A 提交 → B 获锁 → dup 读到 A 预约 → 409
- 终态断言单 active 预约 + ConflictError
"""

import threading
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func

from tests.unit.test_wm10_concurrency import _book_with_copies, _family, _h, _pay, _pay_deposit


def _db():
    from backend.database import get_session

    return get_session()


def test_reservation_create_concurrent_same_book_locked(client: TestClient, session_pair):
    h = _h(client)
    p, c, mini = _family(client, h, "13980003301", "预约锁孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book_id = _book_with_copies(client, h, "预约锁书", copies=2)

    from backend.domain.catalog.models import BookCopy
    from backend.domain.identity.models import Child
    from backend.domain.reading.models import Reservation
    from backend.domain.reading.service import ReservationService

    with _db() as db:
        copy_ids = [r.id for r in db.query(BookCopy).filter(BookCopy.book_id == book_id).all()]
        assert len(copy_ids) >= 2

    s1, s2 = session_pair
    # s1 模拟先到者 A：锁 Child + 插入 active 预约（未提交，模拟进行中）
    locked = s1.query(Child).filter(Child.id == c["id"]).with_for_update().first()
    assert locked is not None
    s1.add(
        Reservation(
            child_id=c["id"],
            book_id=book_id,
            copy_id=copy_ids[0],
            expires_at=datetime.now() + timedelta(hours=72),
            status=Reservation.STATUS_ACTIVE,
        )
    )
    s1.flush()

    results = {}

    def b_create():
        try:
            child = s2.query(Child).filter(Child.id == c["id"]).first()
            r = ReservationService(s2).create(child, book_id)
            s2.commit()
            results["created"] = r.id
        except Exception as e:  # noqa: BLE001
            results["err"] = type(e).__name__

    t = threading.Thread(target=b_create)
    t.start()
    time.sleep(1.0)  # 给 B 足够时间进入 create（修复后应阻塞在 Child 锁）
    s1.commit()  # A 提交（释放锁）
    t.join(timeout=8)
    assert not t.is_alive(), "B 线程 8s 未结束（疑似死锁）"

    # 修复后：B 被 Child 锁串行化 → dup 读到 A 已提交预约 → 409 ConflictError
    assert results.get("err") == "ConflictError", f"应 409 同书唯一，实 {results}"
    with _db() as db:
        cnt = (
            db.query(func.count(Reservation.id))
            .filter(
                Reservation.child_id == c["id"],
                Reservation.status == Reservation.STATUS_ACTIVE,
            )
            .scalar()
        )
        assert cnt == 1, f"active 预约应 1 条，实 {cnt}（双预约，check-then-insert 竞态 RED）"
