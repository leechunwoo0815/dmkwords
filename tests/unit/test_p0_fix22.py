# tests/unit/test_p0_fix22.py — 插修 12 S4（#5）：refunded 死记录占报名入口位（双层修）
"""红测试：_my_enrollment_map 不过滤状态——refunded/cancelled 终态记录也进 map
→ 详情 my_enrollment 永远非空 → 前端 wx:if={{!my_enrollment}} 走不到"立即报名"。

修法：map 取最新一条且仅 ACTIVE_STATUSES（pending_payment/enrolled/checked_in/
refund_pending）入 map——终态不占入口位（历史在"我的活动"列表仍全量可见）；
enroll 判定本就正确（refunded 不在 ACTIVE_STATUSES=可重报）。"""

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _mk_activity
from tests.unit.test_wm10_concurrency import _family, _h


def _refunded_flow(client, h, mini, c, act):
    """报名→付款→退款申请→审核通过→execute 成功（e=refunded）。返回 enrollment_id。"""
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    r = client.post(
        f"/api/miniapp/enrollments/{e['enrollment']['id']}/refund-apply",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    eid = e["enrollment"]["id"]
    # 超管审核通过（rr→approved，e 保持 refund_pending）
    ra = client.post(
        f"/api/admin/activity-refunds/{eid}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert ra.status_code == 200, ra.text
    # 拿统一台账 RefundRequest（kind=order + order_id）execute 成功 → e 翻 refunded
    from backend.domain.identity.models import RefundRequest

    with __import__("backend.database", fromlist=["get_session"]).get_session() as db:
        rr = (
            db.query(RefundRequest)
            .filter(
                RefundRequest.order_id == e["order_id"],
                RefundRequest.is_deleted == 0,
            )
            .order_by(RefundRequest.id.desc())
            .first()
        )
        rid = rr.id
    re_ = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "线下打款"},
        headers=h,
    )
    assert re_.status_code == 200, re_.text
    return eid


def test_refunded_enrollment_releases_entry(client: TestClient):
    """refunded 后：detail my_enrollment=None（当前返回死记录 = RED）+ 可重报。"""
    from backend.domain.activity.models import ActivityEnrollment

    h = _h(client)
    act = _mk_activity(client, h, quota=3, fee=50, hours_later=72, title="重报活动")
    p, c, mini = _family(client, h, "13900040001", "重报孩")
    eid = _refunded_flow(client, h, mini, c, act)

    # 终态实锤
    with __import__("backend.database", fromlist=["get_session"]).get_session() as db:
        e_db = db.query(ActivityEnrollment).filter(ActivityEnrollment.id == eid).first()
        assert e_db.status == ActivityEnrollment.STATUS_REFUNDED, f"应 refunded，实 {e_db.status}"

    # 详情接口：my_enrollment 应 None（活跃态过滤）——当前返回 refunded 记录 = RED
    d = client.get(
        f"/api/miniapp/activities/{act['id']}", params={"child_id": c["id"]}, headers=mini
    )
    assert d.status_code == 200, d.text
    assert d.json().get("my_enrollment") is None, (
        f"refunded 死记录不应占报名入口位，实 {d.json().get('my_enrollment')}"
    )

    # 再报名：200 成功（enroll 判定本就允许——API 层断言锁定）
    e2 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    )
    assert e2.status_code == 200, f"refunded 后应可重报，实 {e2.status_code} {e2.text[:100]}"
