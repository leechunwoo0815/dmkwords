# tests/unit/test_p0b2_t20_overdue_list_guard.py — P0 第二批 T20（E-11）overdue_list 状态覆盖守卫
"""并发红测试：无条件 record.status=OVERDUE——已还书（RETURNED）被旧快照复活为
逾期，孩子被误催还。return_book 有锁、overdue_list 无，不对称。

时序（线程模式）：
- 借书 → due_at 推到过去（active 超期）
- s2 锁 BorrowRecord 行（模拟还书事务进行中）
- s1 线程 overdue_list：快照 join 读到 ACTIVE 超期 → 逐行**锁定读探针**阻塞在 s2 锁
- s2 完成还书（RETURNED）提交
- 修复后：锁定读拿到当前版本 RETURNED → 不在 (ACTIVE, OVERDUE) → 不展示、不复活
- 修复前：无条件覆盖 → RETURNED 被打回 OVERDUE（RED）

第九轮审查（P3）：探针从"条件 UPDATE"改成"`SELECT ... FOR UPDATE`"（GET **不再写库**）。
判据不变（当前读 vs 快照读），只是不再落写入；本用例的并发断言因此跨两种实现都成立
（`assert_blocked` 只判"对方确实在等锁"，不判语句形状）。
"""

import threading
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.helpers import assert_blocked, lock_wait_query
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
    assert_blocked(lock_wait_query(), "overdue_list 的锁定读探针")
    locked.status = BorrowRecord.STATUS_RETURNED  # 还书落库
    locked.returned_at = datetime.now()
    s2.commit()  # 还书提交，释放锁
    t.join(timeout=8)
    assert not t.is_alive(), "逾期名单线程 8s 未结束（疑似死锁）"
    assert results.get("rows") == 0, (
        f"刚还的书不得出现在逾期催还名单，实 {results.get('rows')} 行（探针失效=旧快照）"
    )

    with _db() as db:
        row = db.query(BorrowRecord).filter(BorrowRecord.id == rid).first()
        assert row.status == BorrowRecord.STATUS_RETURNED, (
            f"并发还书后记录应保持 RETURNED，实 {row.status}（RED=已还书被复活为逾期）"
        )
