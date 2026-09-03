# tests/unit/test_p0b2_t16_activity_refund_ledger.py — P0 第二批 T16（B-9+E-3）活动退款接入统一七态台账
"""红测试：review_refund 直接翻 order.status=REFUNDED——无 RefundRequest、无
"实际打款"环节追踪、退款台账断链，绕过 R-308 七态矩阵。

方案 A 改造后语义（行为变更卡，专家口径）：
- apply_refund：免费活动 422；付费委托 RefundService.apply（R-309 金额同源、
  0 元拦截、查重、admin 通知复用），e→refund_pending
- review_refund：锁 enrollment（E-3）→ 委托 RefundService.review
  - approve：rr→approved + order.refund_status=approved，**e 保持 refund_pending
    （approve ≠ 钱已退）**，等 execute 联动
  - reject：rr→rejected + e 恢复 enrolled + order.refund_status 回 none
- execute：零改动（L410 联动 e→REFUNDED 已在位）
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child


def _db():
    from backend.database import get_session

    return get_session()


def _paid_enrolled(client, h, phone, name, fee=50):
    c, m = _mk_child(client, h, phone, name)
    act = _mk_activity(client, h, quota=2, fee=fee, title=f"T16活动{phone[-2:]}")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
    ).json()
    r = client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r.status_code == 200, r.text
    return c, m, act, e["enrollment"]["id"], e["order_id"]


def _apply(client, m, c, eid):
    return client.post(
        f"/api/miniapp/enrollments/{eid}/refund-apply", json={"child_id": c["id"]}, headers=m
    )


def _pending_rr(order_id):
    from backend.domain.identity.models import RefundRequest

    with _db() as db:
        return (
            db.query(RefundRequest)
            .filter(RefundRequest.order_id == order_id, RefundRequest.is_deleted == 0)
            .order_by(RefundRequest.id.desc())
            .first()
        )


def test_activity_refund_full_chain(client: TestClient):
    h = _h(client)
    c, m, act, eid, order_id = _paid_enrolled(client, h, "13981016001", "台账全链孩")

    r = _apply(client, m, c, eid)
    assert r.status_code == 200, r.text
    rr = _pending_rr(order_id)
    assert rr is not None, "apply 后应创建统一 RefundRequest（RED=无台账）"
    from backend.domain.identity.models import RefundRequest

    assert rr.status == RefundRequest.STATUS_PENDING

    # approve：rr→approved，e 仍 refund_pending（approve≠翻状态），order.refund_status=approved
    rv = client.post(
        f"/api/admin/activity-refunds/{eid}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert rv.status_code == 200, rv.text
    with _db() as db:
        from backend.domain.activity.models import ActivityEnrollment
        from backend.domain.identity.models import Order

        e = db.query(ActivityEnrollment).filter(ActivityEnrollment.id == eid).first()
        assert e.status == ActivityEnrollment.STATUS_REFUND_PENDING, (
            f"approve 后报名应保持 refund_pending 等执行，实 {e.status}（行为变更灵魂断言）"
        )
        o = db.query(Order).filter(Order.id == order_id).first()
        assert o.status == Order.STATUS_PAID, f"approve 后订单应仍 PAID，实 {o.status}"
        assert o.refund_status == Order.REFUND_STATUS_APPROVED

    # execute：order REFUNDED + e REFUNDED + rr refunded（联动已在位）
    ex = client.post(
        f"/api/admin/refund-requests/{rr.id}/execute",
        json={"success": True, "remark": "原路退回"},
        headers=h,
    )
    assert ex.status_code == 200, ex.text
    with _db() as db:
        from backend.domain.activity.models import ActivityEnrollment
        from backend.domain.identity.models import Order, RefundRequest

        e = db.query(ActivityEnrollment).filter(ActivityEnrollment.id == eid).first()
        o = db.query(Order).filter(Order.id == order_id).first()
        rr2 = db.query(RefundRequest).filter(RefundRequest.id == rr.id).first()
        assert e.status == ActivityEnrollment.STATUS_REFUNDED, (
            f"execute 后报名应 refunded，实 {e.status}"
        )
        assert o.status == Order.STATUS_REFUNDED
        assert rr2.status == RefundRequest.STATUS_REFUNDED


def test_activity_refund_reject_restores(client: TestClient):
    h = _h(client)
    c, m, act, eid, order_id = _paid_enrolled(client, h, "13981016002", "台账拒绝孩")
    assert _apply(client, m, c, eid).status_code == 200
    rr = _pending_rr(order_id)
    assert rr is not None

    rv = client.post(
        f"/api/admin/activity-refunds/{eid}/review",
        json={"approve": False, "remark": "理由充分拒绝"},
        headers=h,
    )
    assert rv.status_code == 200, rv.text
    with _db() as db:
        from backend.domain.activity.models import ActivityEnrollment
        from backend.domain.identity.models import Order, RefundRequest

        e = db.query(ActivityEnrollment).filter(ActivityEnrollment.id == eid).first()
        assert e.status == ActivityEnrollment.STATUS_ENROLLED, (
            f"拒绝后应恢复 enrolled，实 {e.status}"
        )
        assert e.cancel_reason is None
        o = db.query(Order).filter(Order.id == order_id).first()
        assert o.refund_status == Order.REFUND_STATUS_NONE, (
            f"拒绝后 refund_status 应回 none，实 {o.refund_status}"
        )
        rr2 = db.query(RefundRequest).filter(RefundRequest.id == rr.id).first()
        assert rr2.status == RefundRequest.STATUS_REJECTED


def test_free_activity_refund_apply_422(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13981016003", "免费退款孩")
    act = _mk_activity(client, h, quota=2, fee=0, title="T16免费活动")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
    ).json()
    assert e["enrollment"]["status"] == "enrolled"
    r = _apply(client, m, c, e["enrollment"]["id"])
    assert r.status_code == 422, (
        f"免费活动 refund-apply 应 422 请取消报名，实 {r.status_code} {r.text[:80]}"
    )
