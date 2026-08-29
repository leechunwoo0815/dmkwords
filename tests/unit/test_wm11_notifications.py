# tests/unit/test_wm11_notifications.py — WM11 通知中心（事件驱动全场景）
"""断言锚点：PRD §十 8 类通知全清单 / FEAT-067 / docs/09 §七通知清单。
- 站内消息必达（事件触发即入库）
- 微信订阅尽力送达（通道未启用/无 openid → skipped 记录，站内不受影响）
- 幂等去重（同场景同对象只发一次）
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from backend.common.notification_models import Notification
from backend.database import get_session
from tests.unit.helpers import force_book_on


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk(client, h, phone="13800003001"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "通知孩"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    book = client.post(
        "/api/admin/books",
        json={"isbn": "9780545582889", "title": "Dog Man", "word_count": 2500},
        headers=h,
    ).json()
    force_book_on(client, h, book["id"])
    mini = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    return p, c, book, {"Authorization": f"Bearer {mini.json()['token']}"}


def _notifs(parent_id: int) -> list[Notification]:
    with get_session() as db:
        return (
            db.query(Notification)
            .filter(Notification.parent_id == parent_id, Notification.is_deleted == 0)
            .order_by(Notification.id.desc())
            .all()
        )


# ---------- 借阅类：借书成功 / 还书成功 ----------


def test_borrow_and_return_generate_notifications(client: TestClient):
    h = _h(client)
    p, c, book, _ = _mk(client, h)
    r = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    assert r.status_code == 200, r.text
    assert any(n.scene == "borrow.success" for n in _notifs(p["id"]))

    copy_id = r.json()["copy_id"]
    rr = client.post(
        "/api/admin/circulation/return",
        json={"copy_id": copy_id, "condition": "normal"},
        headers=h,
    )
    assert rr.status_code == 200, rr.text
    assert any(n.scene == "borrow.returned" for n in _notifs(p["id"]))


# ---------- 资金类：付款成功 / 押金补缴 / 退款审核与到账 ----------


def test_order_paid_and_deposit_notifications(client: TestClient):
    h = _h(client)
    p, c, book, _ = _mk(client, h)
    # 押金缴纳（_mk 已缴）→ 已有 deposit.paid；再补缴走 supplement
    assert any(n.scene == "money.deposit_paid" for n in _notifs(p["id"]))
    # 观察期订单在 _mk 已 confirm → money.order_paid
    assert any(n.scene == "money.order_paid" for n in _notifs(p["id"]))


def test_refund_review_and_execute_notifications(client: TestClient):
    h = _h(client)
    p2, c2, b2, mini2 = _mk(client, h, phone="13800003003")
    f2 = client.post(
        "/api/admin/orders", json={"child_id": c2["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{f2['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    ra2 = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c2["id"], "order_id": f2["id"], "reason": "想退"},
        headers=mini2,
    )
    assert ra2.status_code == 200, ra2.text
    rid = ra2.json()["id"]
    rr = client.post(
        f"/api/admin/refund-requests/{rid}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert rr.status_code == 200, rr.text
    assert any(n.scene == "money.refund_result" for n in _notifs(p2["id"]))
    rex = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "已打款"},
        headers=h,
    )
    assert rex.status_code == 200, rex.text
    assert any(n.scene == "money.refund_received" for n in _notifs(p2["id"]))


# ---------- 活动类：报名成功 / 活动取消 ----------


def test_activity_enroll_and_cancel_notifications(client: TestClient):
    h = _h(client)
    p, c, book, mini = _mk(client, h, phone="13800003004")
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "读书会",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(days=3)).isoformat(),
            "location": "馆内",
            "max_quota": 5,
            "fee": 0,
        },
        headers=h,
    ).json()
    r = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    )
    assert r.status_code == 200, r.text
    assert any(n.scene == "activity.enroll" for n in _notifs(p["id"]))
    # 门店取消 → 活动取消通知
    cr = client.post(f"/api/admin/activities/{act['id']}/cancel", headers=h)
    assert cr.status_code == 200, cr.text
    assert any(n.scene == "activity.cancel" for n in _notifs(p["id"]))


# ---------- 微信通道尽力送达：未启用 → skipped，站内消息不丢 ----------


def test_wechat_skipped_when_disabled(client: TestClient):
    h = _h(client)
    p, c, book, _ = _mk(client, h, phone="13800003005")
    client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    with get_session() as db:
        row = (
            db.query(Notification)
            .filter(Notification.parent_id == p["id"], Notification.scene == "borrow.success")
            .first()
        )
        assert row.wechat_status == Notification.WECHAT_SKIPPED  # 通道未启用
        assert row.wechat_error == "通道未启用"
        assert row.title == "借书成功"  # 站内消息不受影响


# ---------- P0：并发撞唯一索引不连坐主事务（savepoint 隔离） ----------


def test_send_integrity_error_keeps_pending_main_write(monkeypatch):
    """强制走 IntegrityError 分支：mock count 返回 0（模拟并发窗口查不到），
    预置同键冲突行触发唯一索引；断言主事务待写入（家长）未被连坐回滚。
    P0 回归：notifications.send 的 savepoint（begin_nested）隔离通知插入。"""
    from sqlalchemy import literal

    import backend.common.notifications as nmod
    from backend.common.notifications import SCENE_BORROW_SUCCESS, NotificationService
    from backend.database import SessionLocal
    from backend.domain.identity.models import Parent

    monkeypatch.setattr(nmod.func, "count", lambda col: literal(0))

    with SessionLocal() as db:
        db.add(
            Notification(
                parent_id=1,
                scene=SCENE_BORROW_SUCCESS,
                title="借书成功",
                content="旧",
                category=Notification.CATEGORY_BORROW,
                ref_type="borrow_record",
                ref_id="100",
                dedup_key="1",
            )
        )
        db.commit()
        # 主业务待写入（模拟 EventBus 共享事务里订单 paid 等未 commit 的写入）
        db.add(Parent(name="主业务家长", phone="13800009999"))
        ok = NotificationService(db).send(
            parent_id=1,
            scene=SCENE_BORROW_SUCCESS,
            title="借书成功",
            content="新",
            category=Notification.CATEGORY_BORROW,
            ref_type="borrow_record",
            ref_id="100",
            dedup_key="1",
        )
        assert ok is False  # 撞唯一 → savepoint 回滚通知插入
        db.commit()  # 若被 rollback() 连坐会抛 / Parent 丢失
        parent = db.query(Parent).filter(Parent.phone == "13800009999").first()
        assert parent is not None  # 主业务写入仍在
        assert parent.name == "主业务家长"
        dup = len(
            db.query(Notification)
            .filter(Notification.ref_id == "100", Notification.is_deleted == 0)
            .all()
        )
        assert dup == 1  # 未重复插入


# ---------- 幂等去重：同场景同对象不重复 ----------


# ---------- 幂等去重：同场景同对象不重复 ----------


def test_notification_dedup_unique(client: TestClient):
    h = _h(client)
    p, c, book, _ = _mk(client, h, phone="13800003006")
    # 借书两次相同的场景（重复调用 send 场景用同一 ref）
    from backend.common.notification_models import Notification
    from backend.common.notifications import SCENE_BORROW_SUCCESS, NotificationService
    from backend.database import SessionLocal

    with SessionLocal() as db:
        svc = NotificationService(db)
        assert svc.send(
            parent_id=p["id"],
            scene=SCENE_BORROW_SUCCESS,
            title="借书成功",
            content="test",
            category=Notification.CATEGORY_BORROW,
            child_id=c["id"],
            ref_type="borrow_record",
            ref_id="999",
        )
        assert not svc.send(
            parent_id=p["id"],
            scene=SCENE_BORROW_SUCCESS,
            title="借书成功",
            content="test",
            category=Notification.CATEGORY_BORROW,
            child_id=c["id"],
            ref_type="borrow_record",
            ref_id="999",
        )
        db.commit()
    assert len([n for n in _notifs(p["id"]) if n.ref_id == "999"]) == 1


# ---------- miniapp 消息中心 + 已读 ----------


def test_miniapp_message_center_and_read(client: TestClient):
    h = _h(client)
    p, c, book, mini = _mk(client, h, phone="13800003007")
    client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    resp = client.get("/api/miniapp/notifications", headers=mini)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert data["unread"] >= 1
    assert any(i["title"] == "借书成功" for i in data["items"])
    nid = data["items"][0]["id"]
    rr = client.post("/api/miniapp/notifications/read", json={"ids": [nid]}, headers=mini)
    assert rr.status_code == 200
    resp2 = client.get("/api/miniapp/notifications", headers=mini).json()
    assert resp2["unread"] == data["unread"] - 1


# ---------- 管理端通知记录中心 ----------


def test_admin_notification_center(client: TestClient):
    h = _h(client)
    p, c, book, _ = _mk(client, h, phone="13800003008")
    client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    resp = client.get("/api/admin/notifications", headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(i["title"] == "借书成功" and i["wechat_status"] == "skipped" for i in body["items"])
    filtered = client.get("/api/admin/notifications", params={"category": "借阅"}, headers=h)
    assert filtered.json()["total"] >= 1


# ---------- 审查返工 Q2：unread 计数 + 管理端已读切换（审计留痕） ----------


def test_admin_unread_count_and_toggle_read(client: TestClient):
    h = _h(client)
    p, c, book, _ = _mk(client, h, phone="13800003009")
    client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    body = client.get("/api/admin/notifications", headers=h).json()
    assert body["unread"] >= 1
    target = next(i for i in body["items"] if i["title"] == "借书成功")
    assert target["read"] is False
    # 标记已读（运营介入 + 审计留痕）
    r = client.post(
        f"/api/admin/notifications/{target['id']}/read-status",
        json={"read": True, "reason": "家长电话确认已读"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["read"] is True
    body2 = client.get("/api/admin/notifications", headers=h).json()
    assert body2["unread"] == body["unread"] - 1
    # 审计留痕
    from backend.database import SessionLocal
    from backend.domain.admin.models import AuditLog

    with SessionLocal() as db:
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.action == "notification.toggle_read")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.target_id == str(target["id"])
    # 标记未读（按钮文案语义）
    r2 = client.post(
        f"/api/admin/notifications/{target['id']}/read-status",
        json={"read": False, "reason": ""},
        headers=h,
    )
    assert r2.status_code == 200
    assert r2.json()["read"] is False


def test_miniapp_notifications_category_filter(client: TestClient):
    h = _h(client)
    p, c, book, mini = _mk(client, h, phone="13800003010")
    client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    r = client.get("/api/miniapp/notifications", params={"category": "借阅"}, headers=mini)
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and all(i["category"] == "借阅" for i in items)
    r2 = client.get("/api/miniapp/notifications", params={"category": "资金"}, headers=mini)
    assert all(i["category"] == "资金" for i in r2.json()["items"])


# ---------- 审查必修 bug：unread 过滤下沉 SQL（total 正确、页不滤薄） ----------


def test_unread_tab_sql_filter_total_and_page(client: TestClient):
    """造 25 条（20 已读 5 未读）→ unread Tab page_size=20 → total=5 且只 1 页。"""
    from backend.common.notifications import SCENE_BORROW_SUCCESS, NotificationService
    from backend.database import SessionLocal
    from backend.domain.identity.models import Parent

    h = _h(client)
    r = client.post(
        "/api/admin/members/parents",
        json={"name": "未读测试家长", "phone": "13800003011"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    p = r.json()
    from datetime import datetime

    with SessionLocal() as db:
        parent = db.query(Parent).filter(Parent.id == p["id"]).first()
        svc = NotificationService(db)
        for i in range(25):
            assert svc.send(
                parent_id=parent.id,
                scene=SCENE_BORROW_SUCCESS,
                title="借书成功",
                content=f"演示通知 {i}",
                category=Notification.CATEGORY_BORROW,
                ref_type="borrow_record",
                ref_id=str(1000 + i),
                dedup_key="1",
            )
            db.flush()
        from backend.common.notification_models import Notification as NModel

        rows = (
            db.query(NModel)
            .filter(NModel.parent_id == parent.id, NModel.is_deleted == 0)
            .order_by(NModel.id.asc())
            .limit(20)
            .all()
        )
        for r in rows:
            r.read_at = datetime.now()
        db.commit()

    all_body = client.get("/api/admin/notifications", headers=h).json()
    assert all_body["total"] == 25
    assert all_body["unread"] == 5
    unread_body = client.get(
        "/api/admin/notifications",
        params={"unread": "true", "page_size": 20},
        headers=h,
    ).json()
    assert unread_body["total"] == 5  # 页数正确（1 页）
    assert len(unread_body["items"]) == 5  # 未被页内过滤滤薄
    assert all(not i["read"] for i in unread_body["items"])


# ---------- F1b（C37）：toggle_read 响应携带全局口径计数，前端不再本地推算 ----------


def test_toggle_read_response_carries_global_counts(client: TestClient):
    h = _h(client)
    p, c, book, _ = _mk(client, h, phone="13800003011")
    client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    body = client.get("/api/admin/notifications", headers=h).json()
    before_unread = body["unread"]  # 本用例无筛选 = 全局口径
    before_total = body["total"]
    assert before_unread >= 1
    target = next(i for i in body["items"] if i["read"] is False)

    r = client.post(
        f"/api/admin/notifications/{target['id']}/read-status",
        json={"read": True, "reason": "电话确认"},
        headers=h,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["unread_count"] == before_unread - 1
    assert data["total"] == before_total

    # 标回未读 → 计数复原
    r2 = client.post(
        f"/api/admin/notifications/{target['id']}/read-status",
        json={"read": False, "reason": ""},
        headers=h,
    )
    assert r2.status_code == 200
    assert r2.json()["unread_count"] == before_unread


# ---------- C41：Tab 下拉"全部（N）"计数口径——响应需携带 all_count（不含已读过滤） ----------


def test_list_response_carries_all_count(client: TestClient):
    """all_count = 当前 category/scene/parent 筛选下、不含 unread 过滤的总数；
    unread=true 请求时 total 缩水为未读数，all_count 不得随之缩水。"""
    from backend.common.notifications import SCENE_BORROW_SUCCESS, NotificationService
    from backend.database import SessionLocal
    from backend.domain.identity.models import Parent

    h = _h(client)
    r = client.post(
        "/api/admin/members/parents",
        json={"name": "计数口径家长", "phone": "13800003012"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    p = r.json()

    with SessionLocal() as db:
        parent = db.query(Parent).filter(Parent.id == p["id"]).first()
        svc = NotificationService(db)
        for i in range(3):
            assert svc.send(
                parent_id=parent.id,
                scene=SCENE_BORROW_SUCCESS,
                title="借书成功",
                content=f"口径演示 {i}",
                category=Notification.CATEGORY_BORROW,
                ref_type="borrow_record",
                ref_id=str(2000 + i),
                dedup_key=str(i),
            )
            db.flush()
        from backend.common.notification_models import Notification as NModel

        rows = (
            db.query(NModel)
            .filter(NModel.parent_id == parent.id, NModel.is_deleted == 0)
            .order_by(NModel.id.asc())
            .all()
        )
        rows[0].read_at = datetime.now()
        rows[1].read_at = datetime.now()
        db.commit()

    # 无筛选：total == all_count == 3
    plain = client.get("/api/admin/notifications", headers=h).json()
    assert plain["all_count"] == 3
    assert plain["total"] == 3
    assert plain["unread"] == 1

    # unread=true：total 缩为 1，all_count 保持 3（缺陷 C41 的核心断言）
    unread_body = client.get(
        "/api/admin/notifications", params={"unread": "true"}, headers=h
    ).json()
    assert unread_body["total"] == 1
    assert unread_body["all_count"] == 3
    assert unread_body["unread"] == 1

    # 家长名搜索筛选下：all_count 是筛选口径（匹配该家长的 3 条）
    searched = client.get(
        "/api/admin/notifications", params={"parent_name": "计数口径"}, headers=h
    ).json()
    assert searched["all_count"] == 3


# ---------- C42：Tab 增加「已读」——read 过滤参数（与 unread 同款 SQL 下沉） ----------


def test_list_read_filter(client: TestClient):
    """read=true → 只返回已读行（total=2），all_count/unread 口径不受影响。"""
    from backend.common.notifications import SCENE_BORROW_SUCCESS, NotificationService
    from backend.database import SessionLocal
    from backend.domain.identity.models import Parent

    h = _h(client)
    r = client.post(
        "/api/admin/members/parents",
        json={"name": "已读筛选家长", "phone": "13800003013"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    p = r.json()

    with SessionLocal() as db:
        parent = db.query(Parent).filter(Parent.id == p["id"]).first()
        svc = NotificationService(db)
        for i in range(3):
            assert svc.send(
                parent_id=parent.id,
                scene=SCENE_BORROW_SUCCESS,
                title="借书成功",
                content=f"已读筛选演示 {i}",
                category=Notification.CATEGORY_BORROW,
                ref_type="borrow_record",
                ref_id=str(3000 + i),
                dedup_key=str(i),
            )
            db.flush()
        from backend.common.notification_models import Notification as NModel

        rows = (
            db.query(NModel)
            .filter(NModel.parent_id == parent.id, NModel.is_deleted == 0)
            .order_by(NModel.id.asc())
            .all()
        )
        rows[0].read_at = datetime.now()
        rows[1].read_at = datetime.now()
        db.commit()

    body = client.get("/api/admin/notifications", params={"read": "true"}, headers=h).json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert all(i["read"] for i in body["items"])
    assert body["all_count"] == 3
    assert body["unread"] == 1

    # 互斥保护：unread 与 read 同时传时 unread 优先（返回未读行）
    both = client.get(
        "/api/admin/notifications", params={"unread": "true", "read": "true"}, headers=h
    ).json()
    assert both["total"] == 1
    assert all(not i["read"] for i in both["items"])
