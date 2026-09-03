# tests/unit/test_p0b2_t20_overdue_list_guard.py — P0 第二批 T20（E-11）overdue_list 状态覆盖守卫
"""并发红测试：无条件 record.status=OVERDUE——已还书（RETURNED）被旧快照复活为
逾期，孩子被误催还。return_book 有锁、overdue_list 无，不对称。

时序（线程模式）：
- 借书 → due_at 推到过去（active 超期）
- s2 锁 BorrowRecord 行（模拟还书事务进行中）
- s1 线程 overdue_list：快照 join 读到 ACTIVE 超期 → 循环 UPDATE 阻塞在 s2 锁
- s2 完成还书（RETURNED）提交
- 修复后：条件 UPDATE 当前读 RETURNED 不匹配 → rowcount=0 → 不标且名单剔除
- 修复前：无条件覆盖 → RETURNED 被打回 OVERDUE（RED）
"""

import threading
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _book_with_copies, _db, _family, _h, _pay, _pay_deposit


def test_overdue_list_not_override_returned(client: TestClient, session_pair):
    h = _h(client)
    p, c, mini = _family(client, h, "13981020001", "逾期复活孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book = _book_with_copies(client, h, "逾期复活书", 1)
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        copy_id = db.query(BookCopy).filter(BookCopy.book_id == book).first().id
    r = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "copy_id": copy_id}, headers=h
    )
    assert r.status_code == 200, r.text
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        rec = (
            db.query(BorrowRecord)
            .filter(BorrowRecord.child_id == c["id"], BorrowRecord.is_deleted == 0)
            .first()
        )
        rec.due_at = datetime.now() - timedelta(days=3)
        db.commit()
        rid = rec.id

    s1, s2 = session_pair
    # s2：馆员还书事务进行中（锁 BorrowRecord 行）
    locked = s2.query(BorrowRecord).filter(BorrowRecord.id == rid).with_for_update().first()
    assert locked is not None

    from backend.domain.circulation.service import CirculationService

    results = {}

    def a_list():
        try:
            results["rows"] = len(CirculationService(s1).overdue_list())
        except Exception as e:  # noqa: BLE001
            results["err"] = type(e).__name__

    t = threading.Thread(target=a_list)
    t.start()
    time.sleep(1.0)  # 修复后 s1 阻塞在条件 UPDATE；修复前快照已读、commit 阻塞在行锁
    locked.status = BorrowRecord.STATUS_RETURNED  # 还书落库
    locked.returned_at = datetime.now()
    s2.commit()  # 还书提交，释放锁
    t.join(timeout=8)
    assert not t.is_alive(), "逾期名单线程 8s 未结束（疑似死锁）"

    with _db() as db:
        row = db.query(BorrowRecord).filter(BorrowRecord.id == rid).first()
        assert row.status == BorrowRecord.STATUS_RETURNED, (
            f"并发还书后记录应保持 RETURNED，实 {row.status}（RED=已还书被复活为逾期）"
        )
