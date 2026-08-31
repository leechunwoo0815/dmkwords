# tests/unit/test_wm13_writeback.py — WM13 批次五（审计回写闭环 8 终态路径 L2）
"""双层设计容错红利验证：即使回写遗漏，显示态仍正确（实时算）——
回写只为审计展示完整（handled_at/handled_by/note）。家长撤销/超时路径 note 标注来源。"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _db():
    from backend.database import get_session

    return get_session()


def _family(client, h, phone, name="孩"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "回写家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': phone, 'code': '1234'}).json()['token']}"
    }
    return p, c, mini


def _pay(client, h, child_id, order_type):
    o = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return o


def _apply_refund(client, mini, c, o, reason="测试原因"):
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": o["id"], "reason": reason},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _refund_notifs(db):
    from backend.common.admin_notification_models import AdminNotification

    return (
        db.query(AdminNotification)
        .filter(AdminNotification.ref_type == "refund_request", AdminNotification.is_deleted == 0)
        .order_by(AdminNotification.id)
        .all()
    )


def test_refund_review_marks_handled(client: TestClient):
    """路径 1-2：退款审核（approve）→ 该单通知 handled_at/handled_by 回写。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001801", "审核孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rr = _apply_refund(client, mini, c, o)
    r = client.post(
        f"/api/admin/refund-requests/{rr['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        rows = _refund_notifs(db)
        assert len(rows) == 1
        assert rows[0].handled_at is not None
        assert rows[0].handled_by == 1
        # 显示态不受审计字段影响（双层设计）：approved → 已审结
        from backend.domain.admin.todo_service import AdminTodoService

        assert AdminTodoService(db).resolve_many(rows)[rows[0].id]["effective_status"] == "done"


def test_refund_parent_cancel_marks_handled_with_note(client: TestClient):
    """路径 8（S1 反例配对）：家长撤销 → handled_at 落 + extra 来源='家长已撤销'。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001802", "撤销孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rr = _apply_refund(client, mini, c, o)
    assert (
        client.post(
            f"/api/miniapp/refund-requests/{rr['id']}/cancel",
            json={"child_id": c["id"]},
            headers=mini,
        ).status_code
        == 200
    )
    with _db() as db:
        rows = _refund_notifs(db)
        assert len(rows) == 1
        assert rows[0].handled_at is not None
        assert rows[0].handled_by is None  # 家长操作无管理员
        assert "家长已撤销" in (rows[0].extra or "")


def test_transfer_expire_marks_handled(client: TestClient):
    """路径 8（转让超时）：预警通知 → expire_overdue → handled + note='已超时自动失效'。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001803", "超时源孩")
    tgt = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "超时受让"}, headers=h
    ).json()
    _pay(client, h, c["id"], "formal_fee")
    tr = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": c["id"], "target_child_id": tgt["id"]},
        headers=mini,
    ).json()
    with _db() as db:
        from backend.domain.identity.models import TransferRequest
        from backend.domain.identity.transfer_service import TransferService

        t = db.query(TransferRequest).filter(TransferRequest.id == tr["id"]).first()
        t.expires_at = datetime.now() + timedelta(hours=2)  # 临近超时（预警条件）
        db.commit()
        assert TransferService(db).transfer_expiring_warn() == 1
        t.expires_at = datetime.now() - timedelta(hours=1)
        db.commit()
        assert TransferService(db).expire_overdue() >= 1
        db.expire_all()
        from backend.common.admin_notification_models import AdminNotification

        rows = (
            db.query(AdminNotification)
            .filter(AdminNotification.ref_type == "transfer", AdminNotification.is_deleted == 0)
            .all()
        )
        # 预警通知 + 申请通知都回写
        assert len(rows) == 2
        for n in rows:
            assert n.handled_at is not None
            assert "已超时自动失效" in (n.extra or "")


def test_withdrawal_review_marks_handled(client: TestClient):
    """路径 3-4：退会审核（reject）→ 通知回写。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001804", "退会孩")
    _pay(client, h, c["id"], "formal_fee")
    r = client.post(
        "/api/miniapp/withdrawals", json={"child_id": c["id"], "reason": "搬家"}, headers=mini
    )
    assert r.status_code == 200, r.text
    ok = client.post(
        f"/api/admin/withdrawals/{r.json()['id']}/review",
        json={"approve": False, "remark": "借阅未还"},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    with _db() as db:
        from backend.common.admin_notification_models import AdminNotification

        n = (
            db.query(AdminNotification)
            .filter(
                AdminNotification.ref_type == "withdrawal_request",
                AdminNotification.is_deleted == 0,
            )
            .first()
        )
        assert n is not None and n.handled_at is not None and n.handled_by == 1


def test_activity_last_review_marks_batch_handled(client: TestClient):
    """路径 7：活动逐单退款全部终态 → 汇总通知回写（A3：最后一笔审完 remaining=0）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001805", "活动孩")
    _pay(client, h, c["id"], "observation_fee")
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "回写测试活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内",
            "max_quota": 10,
            "fee": 50,
            "description": "",
            "member_only": True,
        },
        headers=h,
    ).json()
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert client.post(f"/api/admin/activities/{act['id']}/cancel", headers=h).status_code == 200
    # 逐单审核通过（最后一笔）
    ok = client.post(
        f"/api/admin/activity-refunds/{e['enrollment']['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    with _db() as db:
        from backend.common.admin_notification_models import AdminNotification

        n = (
            db.query(AdminNotification)
            .filter(AdminNotification.ref_type == "activity", AdminNotification.is_deleted == 0)
            .first()
        )
        assert n is not None
        assert n.handled_at is not None and n.handled_by == 1
