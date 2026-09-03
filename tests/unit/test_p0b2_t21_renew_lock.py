# tests/unit/test_p0b2_t21_renew_lock.py — P0 第二批 T21（E-1）renew 行锁
"""并发红测试：renew 查询无锁——并发双续借双延期双计数（renew_used=2，每本书
限 1 次被击穿）。修复：查询链加 with_for_update().populate_existing()。

时序（session_pair）：s1 锁 BorrowRecord 行（模拟先到续借进行中）→ s2 线程
renew 阻塞在锁 → s1 完成续借提交（renew_used=1）→ s2 获锁锁定读 → renew_used=1
→ 422"续借机会已用完"。修复前：s2 无锁读 renew_used=0 → 双成功（RED）。
"""

import threading
import time

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _book_with_copies, _db, _family, _h, _pay, _pay_deposit


def test_renew_concurrent_single_use(client: TestClient, session_pair):
    h = _h(client)
    p, c, mini = _family(client, h, "13981021001", "双续借孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book = _book_with_copies(client, h, "双续借书", 1)
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        copy_id = db.query(BookCopy).filter(BookCopy.book_id == book).first().id
    r = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "copy_id": copy_id}, headers=h
    )
    assert r.status_code == 200, r.text
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        rid = (
            db.query(BorrowRecord)
            .filter(BorrowRecord.child_id == c["id"], BorrowRecord.is_deleted == 0)
            .first()
            .id
        )

    s1, s2 = session_pair
    locked = s1.query(BorrowRecord).filter(BorrowRecord.id == rid).with_for_update().first()
    assert locked is not None

    from backend.domain.circulation.service import CirculationService

    admin = type("A", (), {"id": 1, "display_name": "超管"})()
    results = {}

    def b_renew():
        try:
            CirculationService(s2).renew(admin, rid)
            s2.commit()
            results["ok"] = True
        except Exception as e:  # noqa: BLE001
            results["err"] = type(e).__name__

    t = threading.Thread(target=b_renew)
    t.start()
    time.sleep(1.0)  # 修复后 s2 阻塞在锁；修复前 s2 此窗口内已双续借成功
    locked.renew_used = 1  # s1 完成首次续借
    locked.due_at = locked.due_at + __import__("datetime").timedelta(days=14)
    s1.commit()  # 释放锁
    t.join(timeout=8)
    assert not t.is_alive(), "续借线程 8s 未结束（疑似死锁）"
    assert results.get("err") == "ValidationError", (
        f"并发第二笔续借应 422 机会已用完，实 {results}（RED=renew_used 双计数）"
    )
    with _db() as db:
        rec = db.query(BorrowRecord).filter(BorrowRecord.id == rid).first()
        assert rec.renew_used == 1, f"renew_used 应为 1，实 {rec.renew_used}"
