# tests/unit/test_p0b2_t15_fee_decimal.py — P0 第二批 T15（B-8）活动费 float→Decimal 全链
"""红测试：宪法"金额严禁 float"。router fee: float 直塞 Numeric(10,2)、
service float(fee)/float(a.fee) 比较判断。

修复：router fee: Decimal + service Decimal 语义。
断言：fee=19.99 创建 → 落库精确 Decimal("19.99")（禁 float 比较）；
报名订单 amount 同源精确。
"""

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child


def _db():
    from backend.database import get_session

    return get_session()


def test_activity_fee_stored_exact_decimal(client: TestClient):
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=19.99, title="Decimal 活动")
    with _db() as db:
        from backend.domain.activity.models import Activity

        a = db.query(Activity).filter(Activity.id == act["id"]).first()
        assert isinstance(a.fee, Decimal), f"落库应为 Decimal，实 {type(a.fee)}"
        assert a.fee == Decimal("19.99"), f"应精确 19.99，实 {a.fee}"


def test_activity_fee_order_amount_exact(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13981015001", "Decimal 报名孩")
    act = _mk_activity(client, h, quota=2, fee=19.99, title="Decimal 订单活动")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
    ).json()
    assert e["order_id"]
    with _db() as db:
        from backend.domain.identity.models import Order

        o = db.query(Order).filter(Order.id == e["order_id"]).first()
        assert o.amount == Decimal("19.99"), f"订单金额应精确 19.99，实 {o.amount}"
