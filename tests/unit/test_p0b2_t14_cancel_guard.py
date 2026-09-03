# tests/unit/test_p0b2_t14_cancel_guard.py — P0 第二批 T14（B-6）活动 cancel 资金守卫
"""红测试：cancel() docstring 说"免费活动"但代码不查 fee/order_id——付费活动
收款确认后家长直接 cancel：报名 CANCELLED、订单留 PAID、不进退款矩阵、零审核
记录，绕过整个 R-308 链。

修复（与 T16 对称：cancel 仅免费、refund 仅付费）：fee>0 或有 order_id → 422。
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child


def test_paid_activity_cancel_blocked(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13981014001", "付费取消孩")
    act = _mk_activity(client, h, quota=2, fee=50, title="付费活动T14")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
    ).json()
    assert e["enrollment"]["status"] == "pending_payment"
    r = client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r.status_code == 200, r.text

    r2 = client.post(
        f"/api/miniapp/enrollments/{e['enrollment']['id']}/cancel",
        json={"child_id": c["id"]},
        headers=m,
    )
    assert r2.status_code == 422, (
        f"付费活动 cancel 应 422 走退款流程，实 {r2.status_code} {r2.text[:80]}（RED=绕过 R-308）"
    )


def test_free_activity_cancel_ok(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13981014002", "免费取消孩")
    act = _mk_activity(client, h, quota=2, fee=0, title="免费活动T14")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
    ).json()
    assert e["enrollment"]["status"] == "enrolled"
    r = client.post(
        f"/api/miniapp/enrollments/{e['enrollment']['id']}/cancel",
        json={"child_id": c["id"]},
        headers=m,
    )
    assert r.status_code == 200, f"免费活动 cancel 应 200 回归：{r.status_code} {r.text[:80]}"
