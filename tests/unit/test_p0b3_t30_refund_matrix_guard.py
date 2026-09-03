# tests/unit/test_p0b3_t30_refund_matrix_guard.py — P0 第三批 T30（H-4）通用退款入口补活动矩阵
"""安全红测试：PRD §9.3 活动退款矩阵（已签到不退/已开始线下）只在活动侧
apply_refund 入口执行；家长从「我的订单」对活动订单走通用入口（POST
/api/miniapp/refund-requests）→ 矩阵全绕过，已签到活动也能建退款单。

修复：apply() 对 TYPE_ACTIVITY/TYPE_FIRST_ACTIVITY 订单补矩阵检查；
skip_lock_check=True（馆员批量取消）豁免（活动已 cancelled 时 start_at
检查必然失败，批量退款逻辑上必须绕过）。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child


def _db():
    from backend.database import get_session

    return get_session()


def _paid_enrolled(client, h, phone, name, fee=50, hours_later=72):
    c, m = _mk_child(client, h, phone, name)
    act = _mk_activity(
        client, h, quota=2, fee=fee, hours_later=hours_later, title=f"T30活动{phone[-2:]}"
    )
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
    ).json()
    r = client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r.status_code == 200, r.text
    return c, m, act, e["enrollment"]["id"], e["order_id"]


def _signin(client, h, eid):
    ticket = None
    from backend.domain.activity.models import ActivityEnrollment

    with _db() as db:
        e = db.query(ActivityEnrollment).filter(ActivityEnrollment.id == eid).first()
        ticket = e.ticket_code
    r = client.post("/api/admin/activity-signin", json={"ticket_code": ticket}, headers=h)
    assert r.status_code == 200, r.text


def test_checked_in_activity_order_blocked_on_generic_refund(client: TestClient):
    h = _h(client)
    c, m, act, eid, order_id = _paid_enrolled(client, h, "13981030001", "签到绕行孩")
    _signin(client, h, eid)

    # 通用入口（我的订单）对已签到活动订单申请退款 → 422（RED：当前建单 200）
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order_id, "reason": "绕过矩阵尝试"},
        headers=m,
    )
    assert r.status_code == 422, (
        f"已签到活动订单走通用入口应 422，实 {r.status_code} {r.text[:80]}（RED=矩阵只在一侧执行）"
    )


def test_upcoming_activity_order_generic_refund_ok(client: TestClient):
    h = _h(client)
    c, m, act, eid, order_id = _paid_enrolled(client, h, "13981030002", "未开始直退孩")

    # 未开始未签到活动订单走通用入口 → 200 正常建单（矩阵只拦违规，不拦合法）
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order_id, "reason": "通用入口合法退款"},
        headers=m,
    )
    assert r.status_code == 200, f"未开始活动订单通用入口应 200 建单：{r.status_code} {r.text[:80]}"


def test_started_activity_order_blocked_on_generic_refund(client: TestClient):
    h = _h(client)
    # 先建未来活动（创建校验要求未来），再直插把 start_at 改到过去模拟"已开始"
    c, m, act, eid, order_id = _paid_enrolled(client, h, "13981030003", "已开始绕行孩")
    from backend.domain.activity.models import Activity

    with _db() as db:
        a = db.query(Activity).filter(Activity.id == act["id"]).first()
        a.start_at = datetime.now() - timedelta(hours=1)
        db.commit()

    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order_id, "reason": "已开始绕行"},
        headers=m,
    )
    assert r.status_code == 422, f"已开始活动订单走通用入口应 422，实 {r.status_code} {r.text[:80]}"
