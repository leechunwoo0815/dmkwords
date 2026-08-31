# tests/unit/test_wm13_todo_counts.py — WM13 批次三（感知层：todo-counts 聚合）
"""不变量映射：S2（staff 审计类为 0）/ 口径一致（todo-counts 与 inbox pending_count
同一 StatusResolver）/ Q9 权限粒度（审计五类仅超管；order_pending_manual 跟 member.manage）。"""

from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _db():
    from backend.database import get_session

    return get_session()


def _seed_mixed(db):
    """与收件箱测试同构：2 退款（1 pending 1 cancelled）+ 1 转让 pending + 1 退会 rejected。"""
    from backend.common.admin_notifications import AdminNotifyService
    from backend.domain.identity.models import RefundRequest, TransferRequest, WithdrawalRequest

    r_pending = RefundRequest(
        kind="order", child_id=1, amount=Decimal("500"), reason="x", status="pending"
    )
    r_cancelled = RefundRequest(
        kind="order", child_id=1, amount=Decimal("500"), reason="x", status="cancelled"
    )
    w_rejected = WithdrawalRequest(child_id=1, reason="x", status="rejected")
    t_pending = TransferRequest(
        source_child_id=1,
        target_child_id=2,
        status="pending",
        expires_at=datetime.now().replace(year=2030),
    )
    db.add_all([r_pending, r_cancelled, w_rejected, t_pending])
    db.flush()
    svc = AdminNotifyService(db)
    for scene, ref_type, ref in [
        ("admin.refund_apply", "refund_request", r_pending.id),
        ("admin.refund_apply", "refund_request", r_cancelled.id),
        ("admin.withdrawal_apply", "withdrawal_request", w_rejected.id),
        ("admin.transfer_apply", "transfer", t_pending.id),
    ]:
        svc.send(
            scene=scene,
            title="t",
            content="c",
            ref_type=ref_type,
            ref_id=str(ref),
            applicant_name="测试家长·孩",
        )
    db.commit()
    return {"refund_pending": r_pending.id, "transfer_pending": t_pending.id}


def _pending_order(client, h):
    """造一笔待人工确认收款订单（WM3 链路）。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "计数家长", "phone": "13800001601"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "计数孩"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    assert o["status"] == "pending_manual_confirm"
    return o


def test_todo_counts_consistent_with_inbox(client: TestClient):
    """口径一致：todo-counts.admin_total == inbox.pending_count（同一 resolver）。"""
    h = _h(client)
    with _db() as db:
        _seed_mixed(db)
        db.commit()
    counts = client.get("/api/admin/todo-counts", headers=h).json()
    inbox = client.get("/api/admin/admin-notifications", headers=h).json()
    assert counts["admin_total"] == inbox["pending_count"]
    assert counts["admin_total"] == 2
    assert counts["refund_pending"] == 1
    assert counts["transfer_pending"] == 1
    assert counts["withdrawal_pending"] == 0  # rejected 已审结不计


def test_todo_counts_staff_granularity(client: TestClient):
    """Q9 延伸：staff 审计五类全 0（S2）；order_pending_manual 跟 member.manage 看真实数。"""
    h = _h(client)
    with _db() as db:
        _seed_mixed(db)
        db.commit()
    _pending_order(client, h)
    staff = _h(client, "staff01")
    c = client.get("/api/admin/todo-counts", headers=staff).json()
    assert c["refund_pending"] == 0
    assert c["withdrawal_pending"] == 0
    assert c["transfer_pending"] == 0
    assert c["transfer_expiring"] == 0
    assert c["activity_batch_refund"] == 0
    assert c["admin_total"] == 0
    assert c["order_pending_manual"] == 1  # staff 有 member.manage，看真实数
    # 超管两侧都全
    a = client.get("/api/admin/todo-counts", headers=h).json()
    assert a["order_pending_manual"] == 1
    assert a["admin_total"] == 2


def test_todo_counts_refund_execute_failed_counts_as_refund(client: TestClient):
    """refund_execute_failed pending 计入 refund_pending（同属退款处理事项）。"""
    h = _h(client)
    with _db() as db:
        from backend.common.admin_notifications import AdminNotifyService
        from backend.domain.identity.models import RefundRequest

        r = RefundRequest(
            kind="order", child_id=1, amount=Decimal("500"), reason="x", status="failed"
        )
        db.add(r)
        db.flush()
        AdminNotifyService(db).send(
            scene="admin.refund_execute_failed",
            title="t",
            content="c",
            ref_type="refund_request",
            ref_id=str(r.id),
        )
        db.commit()
    c = client.get("/api/admin/todo-counts", headers=h).json()
    assert c["refund_pending"] == 1
    assert c["admin_total"] == 1


def test_todo_counts_scene_split(client: TestClient):
    """六 scene 计数分键正确（transfer_expiring 单列）。"""
    h = _h(client)
    with _db() as db:
        from backend.common.admin_notifications import AdminNotifyService
        from backend.domain.activity.models import Activity, ActivityEnrollment
        from backend.domain.identity.models import TransferRequest, WithdrawalRequest

        svc = AdminNotifyService(db)
        w = WithdrawalRequest(child_id=1, reason="x", status="applying")
        t_expired = TransferRequest(
            source_child_id=1,
            target_child_id=2,
            status="expired",
            expires_at=datetime.now() - timedelta_hours(1),
        )
        a = Activity(
            title="活动X",
            activity_type="book_club",
            start_at=datetime.now().replace(year=2030),
            location="x",
            max_quota=10,
            fee=Decimal("50"),
            description="",
            status=Activity.STATUS_CANCELLED,
        )
        db.add_all([w, t_expired, a])
        db.flush()
        # A3 口径：活动批量退款待处理 = 该场仍有 REFUND_PENDING 报名
        db.add(
            ActivityEnrollment(
                activity_id=a.id,
                child_id=1,
                ticket_code="TK-WM13-CNT",
                status=ActivityEnrollment.STATUS_REFUND_PENDING,
            )
        )
        db.flush()
        for scene, ref_type, ref in [
            ("admin.withdrawal_apply", "withdrawal_request", w.id),
            ("admin.transfer_expiring", "transfer", t_expired.id),
            ("admin.activity_batch_refund", "activity", a.id),
        ]:
            svc.send(
                scene=scene, title="t", content="c", ref_type=ref_type, ref_id=str(ref)
            )
        db.commit()
    c = client.get("/api/admin/todo-counts", headers=h).json()
    assert c["withdrawal_pending"] == 1
    assert c["transfer_expiring"] == 0  # expired 已失效不计待办（S1）
    assert c["activity_batch_refund"] == 1
    assert c["admin_total"] == 2


def timedelta_hours(n: int):
    from datetime import timedelta

    return timedelta(hours=n)
