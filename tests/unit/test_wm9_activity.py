# tests/unit/test_wm9_activity.py — 线下活动（发布/报名/签到/退款矩阵/99 元链）
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_child(client, h, phone, name, member=True):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    if member:
        o = client.post(
            "/api/admin/orders",
            json={
                "child_id": c["id"],
                "order_type": "observation_fee",
            },
            headers=h,
        ).json()
        client.post(
            f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
        )
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    mini = {"Authorization": f"Bearer {r.json()['token']}"}
    return c, mini


def _mk_activity(client, h, quota=2, fee=50, hours_later=72, member_only=False, title="读书会"):
    r = client.post(
        "/api/admin/activities",
        json={
            "title": title,
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=hours_later)).isoformat(),
            "location": "馆内一层",
            "max_quota": quota,
            "fee": fee,
            "description": "测试活动",
            "member_only": member_only,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_publish_enroll_quota_and_payment(client: TestClient):
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=50)
    c1, m1 = _mk_child(client, h, "13800000901", "孩一")
    c2, m2 = _mk_child(client, h, "13800000902", "孩二")
    c3, m3 = _mk_child(client, h, "13800000903", "孩三")
    # 两个孩子报名（收费 → 待支付占名额）
    e1 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c1["id"]}, headers=m1
    ).json()
    assert e1["enrollment"]["status"] == "pending_payment"
    assert e1["order_id"]
    e2 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c2["id"]}, headers=m2
    ).json()
    assert e2["enrollment"]["status"] == "pending_payment"
    # 名额满 → 第三个被拒
    e3 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c3["id"]}, headers=m3
    )
    assert e3.status_code == 409
    assert "已满" in e3.json()["detail"]
    # 重复报名拒
    dup = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c1["id"]}, headers=m1
    )
    assert dup.status_code == 409
    # 收款确认 → 报名转正
    client.post(
        f"/api/admin/orders/{e1['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    mine = client.get(f"/api/miniapp/enrollments?child_id={c1['id']}", headers=m1).json()
    assert mine[0]["status"] == "enrolled"
    assert mine[0]["ticket_code"].startswith("TK")
    # 详情页额度
    d = client.get(f"/api/miniapp/activities/{act['id']}?child_id={c1['id']}", headers=m1).json()
    assert d["quota_used"] == 2 and d["full"] is True


def test_free_activity_cancel_and_reenroll(client: TestClient):
    h = _h(client)
    act = _mk_activity(client, h, quota=1, fee=0)
    c1, m1 = _mk_child(client, h, "13800000904", "免费孩")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c1["id"]}, headers=m1
    ).json()
    assert e["enrollment"]["status"] == "enrolled"
    # 取消 → 名额释放
    r = client.post(
        f"/api/miniapp/enrollments/{e['enrollment']['id']}/cancel",
        json={"child_id": c1["id"]},
        headers=m1,
    )
    assert r.status_code == 200
    # 再报（另一个孩子）可成功
    c2, m2 = _mk_child(client, h, "13800000905", "免费孩二")
    e2 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c2["id"]}, headers=m2
    )
    assert e2.status_code == 200


def test_signin_and_refund_matrix(client: TestClient):
    h = _h(client)
    act = _mk_activity(client, h, quota=3, fee=50, hours_later=48)
    c1, m1 = _mk_child(client, h, "13800000906", "签到孩")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c1["id"]}, headers=m1
    ).json()
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    eid = e["enrollment"]["id"]
    ticket = e["enrollment"]["ticket_code"]
    # 签到
    s = client.post("/api/admin/activity-signin", json={"ticket_code": ticket}, headers=h)
    assert s.status_code == 200
    assert s.json()["checked_in_at"]
    # 已签到 → 退款被拒
    r = client.post(
        f"/api/miniapp/enrollments/{eid}/refund-apply", json={"child_id": c1["id"]}, headers=m1
    )
    assert r.status_code == 422
    assert "签到" in r.json()["detail"]

    # 未签到 + 未开始 + >2h → 全额退款待审核
    c2, m2 = _mk_child(client, h, "13800000907", "退款孩")
    e2 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c2["id"]}, headers=m2
    ).json()
    client.post(
        f"/api/admin/orders/{e2['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    r2 = client.post(
        f"/api/miniapp/enrollments/{e2['enrollment']['id']}/refund-apply",
        json={"child_id": c2["id"]},
        headers=m2,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "refund_pending"

    # 90 分钟窗口：开始前 1.5h → 线下提示
    act2 = _mk_activity(client, h, quota=1, fee=30, hours_later=1.5 / 1, title="临期活动")
    c3, m3 = _mk_child(client, h, "13800000908", "临期孩")
    e3 = client.post(
        f"/api/miniapp/activities/{act2['id']}/enroll", json={"child_id": c3["id"]}, headers=m3
    ).json()
    client.post(
        f"/api/admin/orders/{e3['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    r3 = client.post(
        f"/api/miniapp/enrollments/{e3['enrollment']['id']}/refund-apply",
        json={"child_id": c3["id"]},
        headers=m3,
    )
    assert r3.status_code == 422
    assert "线下" in r3.json()["detail"]


def test_refund_review_flow(client: TestClient):
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=50)
    c1, m1 = _mk_child(client, h, "13800000909", "审一")
    c2, m2 = _mk_child(client, h, "13800000910", "审二")
    e1 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c1["id"]}, headers=m1
    ).json()
    e2 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c2["id"]}, headers=m2
    ).json()
    for e in (e1, e2):
        client.post(
            f"/api/admin/orders/{e['order_id']}/confirm-payment",
            json={"pay_method": "scan"},
            headers=h,
        )
    # 两个都申请退款
    client.post(
        f"/api/miniapp/enrollments/{e1['enrollment']['id']}/refund-apply",
        json={"child_id": c1["id"]},
        headers=m1,
    )
    client.post(
        f"/api/miniapp/enrollments/{e2['enrollment']['id']}/refund-apply",
        json={"child_id": c2["id"]},
        headers=m2,
    )
    # staff01 不能审
    hs = _h(client, "staff01")
    denied = client.get("/api/admin/activity-refunds", headers=hs)
    assert denied.status_code == 403
    # 待审列表
    pend = client.get("/api/admin/activity-refunds", headers=h).json()
    assert len(pend) == 2
    # 通过第一个 → T16 新语义：approve 后报名保持 refund_pending（approve≠钱已退），
    # 统一台账 rr→approved，execute 联动后才 refunded + 名额释放
    ok = client.post(
        f"/api/admin/activity-refunds/{e1['enrollment']['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "refund_pending"
    rr_e1 = next(
        r
        for r in client.get("/api/admin/refund-requests", headers=h).json()
        if r["order_id"] == e1["order_id"]
    )
    ok2 = client.post(
        f"/api/admin/refund-requests/{rr_e1['id']}/execute",
        json={"success": True, "remark": "原路退回"},
        headers=h,
    )
    assert ok2.status_code == 200, ok2.text
    # 拒绝第二个 → 恢复已报名
    rej = client.post(
        f"/api/admin/activity-refunds/{e2['enrollment']['id']}/review",
        json={"approve": False, "remark": "不符合"},
        headers=h,
    )
    assert rej.json()["status"] == "enrolled"
    # 名额释放后第三个孩子可报名
    c3, m3 = _mk_child(client, h, "13800000911", "替补孩")
    e3 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c3["id"]}, headers=m3
    )
    assert e3.status_code == 200


def test_cancel_activity_batch_refund(client: TestClient):
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=50)
    c1, m1 = _mk_child(client, h, "13800000912", "取消一")
    e1 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c1["id"]}, headers=m1
    ).json()
    client.post(
        f"/api/admin/orders/{e1['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    # 取消整场 → 已付未签到转退款待审
    r = client.post(f"/api/admin/activities/{act['id']}/cancel", headers=h)
    assert r.status_code == 200
    assert r.json()["refund_pending"] == 1
    pend = client.get("/api/admin/activity-refunds", headers=h).json()
    assert pend[0]["activity_id"] == act["id"]
    # 审核通过（T16 新语义：approve → refund_pending 保持，execute 联动后 refunded）
    ok = client.post(
        f"/api/admin/activity-refunds/{e1['enrollment']['id']}/review",
        json={"approve": True},
        headers=h,
    )
    assert ok.json()["status"] == "refund_pending"
    rr_id = client.get("/api/admin/refund-requests", headers=h).json()[0]["id"]
    ok2 = client.post(
        f"/api/admin/refund-requests/{rr_id}/execute",
        json={"success": True, "remark": "活动取消批量退款"},
        headers=h,
    )
    assert ok2.status_code == 200, ok2.text
    mine = client.get(f"/api/miniapp/enrollments?child_id={c1['id']}", headers=m1).json()
    assert mine[0]["status"] == "refunded"


def test_member_only_activity(client: TestClient):
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=0, member_only=True, title="会员专场")
    c, m = _mk_child(client, h, "13800000913", "非会员", member=False)
    r = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
    )
    assert r.status_code == 422
    assert "仅限会员" in r.json()["detail"]


def test_first_activity_fee_chain(client: TestClient):
    """99 元链：购买 → 再购被拒 → 退款成功 → 资格恢复可再购。"""
    h = _h(client)
    c, m = _mk_child(client, h, "13800000914", "九九孩")
    o1 = client.post(
        "/api/admin/orders",
        json={
            "child_id": c["id"],
            "order_type": "first_activity_fee",
        },
        headers=h,
    ).json()
    client.post(
        f"/api/admin/orders/{o1['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    # 再购被拒
    dup = client.post(
        "/api/admin/orders",
        json={
            "child_id": c["id"],
            "order_type": "first_activity_fee",
        },
        headers=h,
    )
    assert dup.status_code == 409
    # 退款走审核链（B-15 改造 20260903：超管代发起 → pending → review → execute 成功）
    rf = client.post(
        f"/api/admin/orders/{o1['id']}/refund", json={"remark": "未参加，全额退"}, headers=h
    )
    assert rf.status_code == 200
    assert rf.json()["status"] == "pending", f"应返回 pending 审核链状态，实 {rf.json()}"
    rid = rf.json()["id"]
    assert (
        client.post(
            f"/api/admin/refund-requests/{rid}/review",
            json={"approve": True, "remark": "同意"},
            headers=h,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/admin/refund-requests/{rid}/execute",
            json={"success": True, "remark": "线下打款"},
            headers=h,
        ).status_code
        == 200
    )
    # 资格恢复 → 可再购
    o2 = client.post(
        "/api/admin/orders",
        json={
            "child_id": c["id"],
            "order_type": "first_activity_fee",
        },
        headers=h,
    )
    assert o2.status_code == 200
    # staff01 不能退款
    hs = _h(client, "staff01")
    denied = client.post(
        f"/api/admin/orders/{o2.json()['id']}/refund", json={"remark": "x"}, headers=hs
    )
    assert denied.status_code == 403
