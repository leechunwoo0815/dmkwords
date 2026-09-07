# tests/unit/test_p0_fix21.py — 插修 11 R2（#1）：管理端活动单确认收款与"不联动报名"设计对齐
"""红测试：R3 设计边界自相矛盾（专家认账）——管理端活动单创建不建报名
（FEAT-080 线下收钱语义），confirm_payment 却无条件调 on_activity_order_paid
（查无报名 → 404"该订单没有关联的活动报名"）。

修法：按订单有无报名分流——有报名走家长端联动转正（照旧）；无报名纯资金入账
（管理端直建单，FEAT-080 §3.5.2 设计边界）。"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h


def _db():
    from backend.database import get_session

    return get_session()


def _mk_act(client, h, title="活动单确认测试"):
    r = client.post(
        "/api/admin/activities",
        json={
            "title": title,
            "activity_type": "book_club",
            "start_at": "2026-09-20T15:00:00",
            "max_quota": 5,
            "fee": "50",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_admin_activity_order_confirm_without_enrollment(client: TestClient):
    """管理端直建活动单（无报名）→ confirm → 200 PAID（当前 404 = RED）。"""
    from backend.domain.identity.models import Order

    h = _h(client)
    act = _mk_act(client, h)
    p, c, mini = _family(client, h, "13900030001", "活动单孩")
    o = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "activity_fee", "activity_id": act["id"]},
        headers=h,
    )
    assert o.status_code == 200, o.text
    order = o.json()
    r = client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "线下收款"},
        headers=h,
    )
    assert r.status_code == 200, (
        f"管理端活动单（无报名）confirm 应 200 纯入账，实 {r.status_code} {r.text[:120]}"
    )
    with _db() as db:
        o_db = db.query(Order).filter(Order.id == order["id"]).first()
        assert o_db.status == Order.STATUS_PAID, f"应 PAID，实 {o_db.status}"


def test_admin_activity_order_free_confirm(client: TestClient):
    """免费活动单（插修 14 R7 防呆改写：原"免费可造单 confirm 纯入账"前提被
    产品决策推翻——免费活动无收款单语义，管理端造单 422 防呆硬拦，
    免费报名走家长端 enroll 即可）。"""
    h = _h(client)
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "免费活动单",
            "activity_type": "book_club",
            "start_at": "2026-09-20T15:00:00",
            "max_quota": 5,
            "fee": "0",
        },
        headers=h,
    ).json()
    p, c, mini = _family(client, h, "13900030002", "免费活动孩")
    r = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "activity_fee", "activity_id": act["id"]},
        headers=h,
    )
    assert r.status_code == 422, f"免费活动造单应 422 防呆，实 {r.status_code} {r.text[:120]}"


def test_parent_enrollment_link_regression(client: TestClient):
    """家长端报名链回归：enroll → confirm → 报名转 ENROLLED（分流不得破坏）。"""
    h = _h(client)
    act = _mk_act(client, h, "报名链回归活动")
    p, c, mini = _family(client, h, "13900030003", "报名回归孩")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    from backend.domain.activity.models import ActivityEnrollment

    with _db() as db:
        row = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.id == e["enrollment"]["id"])
            .first()
        )
        assert row.status == ActivityEnrollment.STATUS_ENROLLED, (
            f"家长报名链 confirm 应转 ENROLLED（回归），实 {row.status}"
        )
