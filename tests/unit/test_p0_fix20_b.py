# tests/unit/test_p0_fix20_b.py — 插修 10 T20b（#9b）：staff 活动页 403 清源 + 签到权限语义对齐
"""红测试（Q1 专家裁 A）：
- 诊断偏差声明：任务包"活动签到仅超管"有误——签到端点实际 require_perm("member.manage")，
  staff01（含 member.manage + borrow.operate）当前已能签到。本卡把签到装饰器对齐为
  require_perm("borrow.operate")（PRD §9.2 馆员现场操作语义），行为零变化。
- 红测试降级为"现状回归锁定"：staff 签到 200 / 列活动 200 / 创建 200 / 退款审核 403。
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _mk_activity
from tests.unit.test_wm10_concurrency import _h


def test_staff_activity_signin_regression_lock(client: TestClient):
    """staff01：列活动 200 / 创建 200 / 签到 200（既有行为锁定，防未来误收）。"""
    h = _h(client)
    hs = _h(client, "staff01")
    assert client.get("/api/admin/activities", headers=hs).status_code == 200
    r = client.post(
        "/api/admin/activities",
        json={
            "title": "staff发布活动",
            "activity_type": "book_club",
            "start_at": "2026-09-20T15:00:00",
            "max_quota": 2,
            "fee": "50",
        },
        headers=hs,
    )
    assert r.status_code == 200, f"staff 发布应 200，实 {r.status_code} {r.text[:100]}"
    act = r.json()

    # 报名 + 确认付款 + staff 签到（回归锁定）
    p = client.post(
        "/api/admin/members/parents", json={"name": "签到家长", "phone": "13900021001"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "签到孩"}, headers=h
    ).json()
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': '13900021001', 'code': '1234'}).json()['token']}"
    }
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    ticket = e["enrollment"]["ticket_code"]
    s = client.post("/api/admin/activity-signin", json={"ticket_code": ticket}, headers=hs)
    assert s.status_code == 200, (
        f"staff 签到应 200（语义对齐 borrow.operate 后保持），实 {s.status_code} {s.text[:100]}"
    )


def test_staff_refund_review_still_403(client: TestClient):
    """活动退款审核保持仅超管（Q10 终裁不动）：staff → 403。"""
    hs = _h(client, "staff01")
    r = client.post(
        "/api/admin/activity-refunds/999/review", json={"approve": True, "remark": "x"}, headers=hs
    )
    assert r.status_code == 403, f"staff 退款审核应 403，实 {r.status_code}"