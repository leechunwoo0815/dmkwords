# tests/unit/test_p0_t7_refund_order.py — P0 第一批 T7（B-15）refund_order 改走审核链
"""POST /orders/{id}/refund 旁路端点红测试。

现象（B-15）：端点直接把 Order 翻 REFUNDED——无 RefundRequest、无 R-309 可退
金额计算（比例/全额/不可退三形态全跳过）、不走七态审核链。

用户已拍板：改造而非删除（超管代家长发起退款申请，统一走审核链）。

三条：
1. 正常单 → 创建 pending RefundRequest + order.refund_status=pending + order.status 仍 paid
2. 0 元单（观察期用满）→ 422（复用 _refundable_amount 同源）
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _h, _family, _pay


def _db():
    from backend.database import get_session

    return get_session()


def test_refund_order_creates_pending_request(client: TestClient):
    """修复前：直接 order.status=refunded 无 RefundRequest（RED）。"""
    from backend.domain.identity.models import Order, RefundRequest

    h = _h(client)
    p, c, mini = _family(client, h, "13980007701", "T7旁路孩")
    order = _pay(client, h, c["id"], "observation_fee")

    r = client.post(
        f"/api/admin/orders/{order['id']}/refund", json={"remark": "超管代发起"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending", f"应返回 pending 审核链状态，实 {r.json()}"

    with _db() as db:
        o = db.query(Order).filter(Order.id == order["id"]).first()
        assert o.status == Order.STATUS_PAID, f"order 应仍 paid（不再直接 refunded），实 {o.status}"
        assert o.refund_status == Order.REFUND_STATUS_PENDING, (
            f"order.refund_status 应 pending，实 {o.refund_status}"
        )
        req = (
            db.query(RefundRequest)
            .filter(RefundRequest.order_id == order["id"], RefundRequest.is_deleted == 0)
            .first()
        )
        assert req is not None, "应创建 RefundRequest（走审核链，非旁路直退）"
        assert req.status == RefundRequest.STATUS_PENDING
        assert req.amount > 0


def test_refund_order_zero_amount_rejected(client: TestClient):
    """0 元单（观察期 30 天已用满）→ 422（复用 _refundable_amount 同源）。"""
    from backend.domain.identity.models import Order

    h = _h(client)
    p, c, mini = _family(client, h, "13980007702", "T7零元孩")
    order = _pay(client, h, c["id"], "observation_fee")
    # 拨 paid_at 到 40 天前 → 观察期用满 30 天 → 可退 0
    with _db() as db:
        o = db.query(Order).filter(Order.id == order["id"]).first()
        o.paid_at = datetime.now() - timedelta(days=40)
        db.commit()

    r = client.post(
        f"/api/admin/orders/{order['id']}/refund", json={"remark": "超管代发起"}, headers=h
    )
    assert r.status_code == 422, f"0 元单应 422，实 {r.status_code} {r.text[:100]}"