# tests/unit/test_p0b2_t18_cancel_timeout_guard.py — P0 第二批 T18（E-6/B-17）cancel_timeout_orders 条件 UPDATE 守卫
"""并发红测试：快照读 + 无条件 status=CANCELLED——管理员并发 confirm_payment 的
PAID 单被覆盖回 CANCELLED（活动报名联动取消、名额释放、钱收了单没了）。

时序（线程模式）：
- s2 锁订单行（confirm_payment 锁定读阶段）
- s1 线程 cancel_timeout_orders：快照读到超时 pending_manual → 循环 UPDATE 阻塞在 s2 行锁
- s2 完成收款确认（PAID）提交释放锁
- 修复后：条件 UPDATE 当前读 status=PAID 不匹配 → rowcount=0 跳过（订单仍 PAID）
- 修复前：无条件覆盖 → PAID 被打回 CANCELLED（RED）
"""

import threading
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _family, _h


def test_cancel_timeout_not_override_paid(client: TestClient, session_pair):
    h = _h(client)
    p, c, mini = _family(client, h, "13981018001", "僵尸竞争孩")
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    # 造超时：create_time 推到 72h 前（阈值默认 48h）
    from backend.domain.identity.models import Order

    with _db() as db:
        row = db.query(Order).filter(Order.id == o["id"]).first()
        row.create_time = datetime.now() - timedelta(hours=72)
        db.commit()

    s1, s2 = session_pair
    # s2：管理员锁定该单（confirm_payment 锁定读阶段），持锁未提交
    locked = s2.query(Order).filter(Order.id == o["id"]).with_for_update().first()
    assert locked is not None

    from backend.domain.identity.order_service import OrderService

    admin = type("A", (), {"id": 1, "display_name": "超管"})()
    results = {}

    def a_cleanup():
        try:
            results["n"] = OrderService(s1).cancel_timeout_orders()
        except Exception as e:  # noqa: BLE001
            results["err"] = type(e).__name__

    t = threading.Thread(target=a_cleanup)
    t.start()
    time.sleep(1.0)  # s1 应阻塞在该单行锁上（UPDATE 等待）
    # s2 完成收款确认：pending_manual → PAID，提交释放锁
    OrderService(s2).confirm_payment(
        admin,
        o["id"],
        type("R", (), {"pay_method": "scan", "remark": "并发收款确认"})(),
    )
    s2.commit()
    t.join(timeout=8)
    assert not t.is_alive(), "清理线程 8s 未结束（疑似死锁）"

    with _db() as db:
        row = db.query(Order).filter(Order.id == o["id"]).first()
        assert row.status == Order.STATUS_PAID, (
            f"并发收款确认后订单应保持 PAID，实 {row.status}（RED=钱收了单被僵尸清理覆盖）"
        )
