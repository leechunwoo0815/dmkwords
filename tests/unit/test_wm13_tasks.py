# tests/unit/test_wm13_tasks.py — WM13 批次四（守护层：transfer_expiring_warn）
"""不变量映射：S1（预警显示态由 resolver 实时判定，超时后自动失效不孤儿）/
B12（任务注册表 + 幂等重跑）/ dedup 每单一次（S3）。"""

from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _db():
    from backend.database import get_session

    return get_session()


def _pending_transfer(client, h, phone, expires_in_hours: float) -> int:
    """走真实链路造 pending 转让，然后把 expires_at 改到指定剩余时间。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "预警家长", "phone": phone}, headers=h
    ).json()
    src = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "预警源孩"}, headers=h
    ).json()
    tgt = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "预警受让"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": src["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': phone, 'code': '1234'}).json()['token']}"
    }
    r = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": src["id"], "target_child_id": tgt["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    with _db() as db:
        from backend.domain.identity.models import TransferRequest

        t = db.query(TransferRequest).filter(TransferRequest.id == tid).first()
        t.expires_at = datetime.now() + timedelta(hours=expires_in_hours)
        db.commit()
    return tid


def _run_warn() -> int:
    from backend.domain.identity.transfer_service import TransferService

    with _db() as db:
        return TransferService(db).transfer_expiring_warn()


def _notifs(db, scene):
    from backend.common.admin_notification_models import AdminNotification

    return (
        db.query(AdminNotification)
        .filter(
            AdminNotification.scene == scene,
            AdminNotification.is_deleted == 0,
        )
        .all()
    )


def test_transfer_expiring_warn_creates_notification(client: TestClient):
    """临近超时（剩 10h）→ 跑任务 → admin.transfer_expiring 落库，content 含剩余时长。"""
    h = _h(client)
    tid = _pending_transfer(client, h, "13800001701", 10)
    sent = _run_warn()
    assert sent == 1
    with _db() as db:
        rows = _notifs(db, "admin.transfer_expiring")
        assert len(rows) == 1
        assert rows[0].ref_type == "transfer"
        assert rows[0].ref_id == str(tid)
        assert "小时" in rows[0].content


def test_transfer_expiring_warn_idempotent_and_scope(client: TestClient):
    """再跑不重复（dedup）；远离超时（48h）不预警。"""
    h = _h(client)
    _pending_transfer(client, h, "13800001702", 10)
    _pending_transfer(client, h, "13800001703", 48)  # 不临近
    assert _run_warn() == 1
    assert _run_warn() == 0  # 幂等：dedup 已存在
    with _db() as db:
        rows = _notifs(db, "admin.transfer_expiring")
        assert len(rows) == 1  # 只有临近那单


def test_transfer_expiring_auto_invalidates_after_expired(client: TestClient):
    """S1 端到端：预警落库 → 转让超时 expired → 显示态自动失效 + todo-counts 归零。"""
    h = _h(client)
    tid = _pending_transfer(client, h, "13800001704", 10)
    _run_warn()
    with _db() as db:
        from backend.common.admin_notifications import AdminNotifyService
        from backend.domain.identity.models import TransferRequest
        from backend.domain.identity.transfer_service import TransferService

        rows = _notifs(db, "admin.transfer_expiring")
        assert len(rows) == 1
        r = AdminNotifyService(db).resolve_many(rows)
        assert r[rows[0].id]["effective_status"] == "pending"
        # 到期 → 跑真实超时任务
        t = db.query(TransferRequest).filter(TransferRequest.id == tid).first()
        t.expires_at = datetime.now() - timedelta(hours=1)
        db.commit()
        assert TransferService(db).expire_overdue() >= 1
        db.expire_all()
        rows = _notifs(db, "admin.transfer_expiring")
        r = AdminNotifyService(db).resolve_many(rows)
        assert r[rows[0].id]["effective_status"] == "invalid"
        assert "超时" in r[rows[0].id]["status_text"]
    c = client.get("/api/admin/todo-counts", headers=h).json()
    assert c["transfer_expiring"] == 0  # 计数归零（实时口径）


def test_registry_contains_transfer_expiring_warn():
    """B12：任务已注册（看板可见 + 手动触发可达）。"""
    from backend.tasks.registry import TASKS

    spec = TASKS["transfer_expiring_warn"]
    assert spec.display_name == "转让超时预警"
    assert spec.group == "会员"
