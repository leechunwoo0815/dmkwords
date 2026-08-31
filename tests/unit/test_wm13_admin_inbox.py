# tests/unit/test_wm13_admin_inbox.py — WM13 批次二（管理待办收件箱 API）
"""不变量映射：S2（staff 空数据不 403）/ S4（handle reason 必填+审计留痕）/ Q8（幂等）/
口径一致反例（pending_count = 列表待处理数，同一 StatusResolver）。"""

from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _db():
    from backend.database import get_session

    return get_session()


def _send(db, scene, ref_type, ref_id, applicant="测试家长·孩", amount=None):
    from backend.common.admin_notifications import AdminNotifyService

    AdminNotifyService(db).send(
        scene=scene,
        title="【测试】",
        content=f"【测试】{applicant} 事项 {ref_id}",
        ref_type=ref_type,
        ref_id=str(ref_id),
        applicant_name=applicant,
        amount=amount,
    )
    db.commit()


def _seed_mixed(db):
    """混合数据：2 待处理（退款 pending + 转让 pending）+ 1 已失效（退款 cancelled）+
    1 已审结（退会 rejected）。返回业务对象 id 映射。"""
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
    _send(db, "admin.refund_apply", "refund_request", r_pending.id, amount=Decimal("500"))
    _send(db, "admin.refund_apply", "refund_request", r_cancelled.id, amount=Decimal("500"))
    _send(db, "admin.withdrawal_apply", "withdrawal_request", w_rejected.id)
    _send(db, "admin.transfer_apply", "transfer", t_pending.id)
    return {
        "refund_pending": r_pending.id,
        "refund_cancelled": r_cancelled.id,
        "withdrawal_rejected": w_rejected.id,
        "transfer_pending": t_pending.id,
    }


def test_list_inbox_effective_status_and_counts(client: TestClient):
    """口径一致反例：3 待处理+1 已失效混合数据 → 徽标计数=列表待处理数（同一 resolver）。"""
    h = _h(client)
    with _db() as db:
        ids = _seed_mixed(db)
        db.commit()
    r = client.get("/api/admin/admin-notifications", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    # 混合：refund pending + transfer pending = 2 待处理（refund cancelled=失效，
    # withdrawal rejected=已审结）
    assert data["pending_count"] == 2
    assert data["total"] == 4
    by_ref = {(i["ref_type"], i["ref_id"]): i for i in data["items"]}
    assert by_ref[("refund_request", str(ids["refund_pending"]))]["effective_status"] == "pending"
    assert by_ref[("transfer", str(ids["transfer_pending"]))]["effective_status"] == "pending"
    assert by_ref[("refund_request", str(ids["refund_cancelled"]))]["effective_status"] == "invalid"
    assert "撤销" in by_ref[("refund_request", str(ids["refund_cancelled"]))]["status_text"]
    assert (
        by_ref[("withdrawal_request", str(ids["withdrawal_rejected"]))]["effective_status"]
        == "done"
    )
    assert "amount" in data["items"][0]  # 金额字段存在（可空）


def test_list_inbox_status_filter(client: TestClient):
    """status_filter=pending 只回待处理（total=pending_count）；finished 回其余。"""
    h = _h(client)
    with _db() as db:
        _seed_mixed(db)
        db.commit()
    r = client.get(
        "/api/admin/admin-notifications", params={"status_filter": "pending"}, headers=h
    )
    data = r.json()
    assert data["total"] == 2
    assert data["pending_count"] == 2
    assert all(i["effective_status"] == "pending" for i in data["items"])
    r2 = client.get(
        "/api/admin/admin-notifications", params={"status_filter": "finished"}, headers=h
    )
    data2 = r2.json()
    assert data2["total"] == 2
    assert all(i["effective_status"] != "pending" for i in data2["items"])


def test_s2_staff_sees_empty_not_403(client: TestClient):
    """S2：staff 调列表 → 200 空数据（不 403 不空转）；staff 调 handle → 403。"""
    hs = _h(client, "staff01")
    with _db() as db:
        _seed_mixed(db)
        db.commit()
    r = hs and client.get("/api/admin/admin-notifications", headers=hs)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["items"] == []
    assert data["pending_count"] == 0
    r2 = client.post(
        "/api/admin/admin-notifications/1/handle", json={"reason": "x"}, headers=hs
    )
    assert r2.status_code == 403


def test_s4_handle_requires_reason_and_audits(client: TestClient):
    """S4：handle 不填 reason → 422；填写 → handled_at 落 + 审计留痕 + handled_by_name。"""
    h = _h(client)
    with _db() as db:
        _seed_mixed(db)
        db.commit()
        from backend.common.admin_notification_models import AdminNotification

        nid = (
            db.query(AdminNotification)
            .filter(AdminNotification.is_deleted == 0)
            .order_by(AdminNotification.id)
            .first()
            .id
        )
    r = client.post(f"/api/admin/admin-notifications/{nid}/handle", json={"reason": ""}, headers=h)
    assert r.status_code == 422
    r2 = client.post(
        f"/api/admin/admin-notifications/{nid}/handle",
        json={"reason": "家长线下协商解决，手动归档"},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    with _db() as db:
        from backend.common.admin_notification_models import AdminNotification
        from backend.domain.admin.models import AuditLog

        n = db.query(AdminNotification).filter(AdminNotification.id == nid).first()
        assert n.handled_at is not None
        assert n.handled_by == 1
        assert "线下协商" in (n.extra or "")
        log = (
            db.query(AuditLog)
            .filter(AuditLog.action == "admin_notification.handle")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert log is not None and "线下协商" in (log.reason or "")
    # 列表显示处理人
    r3 = client.get("/api/admin/admin-notifications", headers=h)
    row = next(i for i in r3.json()["items"] if i["id"] == nid)
    assert row["handled_by_name"] is not None and row["handled_by_name"] != ""


def test_handle_idempotent_keeps_first(client: TestClient):
    """Q8：handle 幂等——重复调用不覆盖首次审计。"""
    h = _h(client)
    with _db() as db:
        _seed_mixed(db)
        db.commit()
        from backend.common.admin_notification_models import AdminNotification

        nid = db.query(AdminNotification).order_by(AdminNotification.id).first().id
    client.post(
        f"/api/admin/admin-notifications/{nid}/handle", json={"reason": "第一次处理"}, headers=h
    )
    r2 = client.post(
        f"/api/admin/admin-notifications/{nid}/handle", json={"reason": "第二次处理"}, headers=h
    )
    assert r2.status_code == 200
    assert r2.json()["already"] is True
    with _db() as db:
        from backend.common.admin_notification_models import AdminNotification

        n = db.query(AdminNotification).filter(AdminNotification.id == nid).first()
        assert "第一次处理" in (n.extra or "")  # 保留首次
        assert "第二次处理" not in (n.extra or "")


def test_inbox_scene_and_keyword_filter(client: TestClient):
    """筛选：scene 按事项类型；keyword 按申请人/内容模糊。"""
    h = _h(client)
    with _db() as db:
        _seed_mixed(db)
        db.commit()
    r = client.get(
        "/api/admin/admin-notifications",
        params={"scene": "admin.refund_apply"},
        headers=h,
    )
    assert r.json()["total"] == 2
    r2 = client.get(
        "/api/admin/admin-notifications", params={"keyword": "测试家长"}, headers=h
    )
    assert r2.json()["total"] == 4
