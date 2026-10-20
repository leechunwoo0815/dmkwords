# tests/unit/test_audit8_a7_cancel_locking_20261019.py — 第八轮审查 A 批：P2-2 撤销侧加锁
"""**缺陷（第八轮审查 P2-2）**：退款/退会/转让的**家长撤销**走普通读，而审核/执行侧全是
`with_for_update()` 锁定读 → 撤销的 `SELECT` 可以在审核提交前读到 `pending`，其 `UPDATE`
排在审核/执行的行锁之后执行，**把已 `approved/processing/refunded` 的单覆盖回 `cancelled`**
（线上退款 Phase 1 已提交网关时最糟：本地显示"已撤销"而钱正在退出，押金还会被复位）。

**修复**：三处撤销的读取加 `with_for_update().populate_existing()`，与审核侧抢同一把行锁。

**判据（可判定，不用 `sleep` 猜时序）**：让审核事务**先持锁不提交**，再发起撤销，轮询
`information_schema.innodb_trx` 直到出现 `trx_state='LOCK WAIT'` —— 这一步直接证明"撤销真的在等锁"，
而不是"还没跑到"。修复前（普通读）永不出现 LOCK WAIT → 用例红。
"""

import threading

from fastapi.testclient import TestClient

from backend.database import get_session
from backend.domain.identity.models import RefundRequest
from tests.unit.helpers import lock_wait_query
from tests.unit.test_audit4_p2_batch3_20261017 import _activity, _enrolled_paid, _member_child
from tests.unit.test_wm11_tasks import _h


def _pending_refund_of_activity(client: TestClient, h, c, mini, title: str) -> int:
    act = _activity(client, h, hours_later=72, title=title)
    e = _enrolled_paid(client, h, mini, c["id"], act["id"])
    r = client.post(
        f"/api/miniapp/enrollments/{e['enrollment']['id']}/refund-apply",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    with get_session() as db:
        return (
            db.query(RefundRequest.id)
            .filter(
                RefundRequest.order_id == e["order_id"],
                RefundRequest.status == RefundRequest.STATUS_PENDING,
            )
            .scalar()
        )


def test_refund_cancel_blocks_on_review_lock(client: TestClient, session_pair):
    """审核事务持锁时，家长撤销必须**阻塞**；审核提交后撤销读到新状态 → 422，且终态不被覆盖。

    RED（还原修复：撤销走普通读）：被阻塞的是 `UPDATE`（断言 `SELECT` 前缀即红），
    且撤销会把状态覆盖成 `cancelled`（终态断言同时红）。
    """
    h = _h(client)
    c, mini = _member_child(client, h, "13800003601")
    rr_id = _pending_refund_of_activity(client, h, c, mini, "撤销加锁探针 A")

    _s1, s2 = session_pair
    locked = (
        s2.query(RefundRequest)
        .filter(RefundRequest.id == rr_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    assert locked is not None
    locked.status = RefundRequest.STATUS_APPROVED  # 模拟"审核已通过"（未提交）

    result: dict = {}

    def _cancel():
        r = client.post(
            f"/api/miniapp/refund-requests/{rr_id}/cancel",
            json={"child_id": c["id"]},
            headers=mini,
        )
        result["code"] = r.status_code
        result["detail"] = (
            r.json().get("detail")
            if r.headers.get("content-type", "").startswith("application/json")
            else r.text
        )

    t = threading.Thread(target=_cancel)
    t.start()
    try:
        blocked_stmt = lock_wait_query()
        assert blocked_stmt, "撤销请求没有等在任何锁上（说明读取没加锁）"
        assert blocked_stmt.lstrip().upper().startswith("SELECT"), (
            f"被阻塞的必须是**锁定读**（撤销侧的 SELECT ... FOR UPDATE）；"
            f"实际是 {blocked_stmt[:60]!r}——说明读没加锁，只有写被挡在审核后面"
        )
    finally:
        s2.commit()  # 审核提交，释放锁
    t.join(timeout=10)
    assert not t.is_alive(), "撤销线程 10s 未结束（疑似死锁）"

    assert result["code"] == 422, f"审核已通过后撤销必须被拒：{result}"
    assert "不可撤销" in str(result["detail"])
    with get_session() as db:
        assert db.get(RefundRequest, rr_id).status == RefundRequest.STATUS_APPROVED, (
            "已审批状态不得被撤销覆盖"
        )


def test_refund_cancel_still_works_when_pending(client: TestClient, session_pair):
    """对照组（**防止修过头**）：无人持锁时，待审单照常可撤销（cancelled + 订单状态复位）。"""
    h = _h(client)
    c, mini = _member_child(client, h, "13800003602")
    rr_id = _pending_refund_of_activity(client, h, c, mini, "撤销加锁探针 B")

    r = client.post(
        f"/api/miniapp/refund-requests/{rr_id}/cancel",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    with get_session() as db:
        assert db.get(RefundRequest, rr_id).status == RefundRequest.STATUS_CANCELLED
