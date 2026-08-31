# tests/unit/test_wm13_admin_notify.py — WM13 运营审核工作台批次一（底座：模型+服务+触发点）
"""不变量映射：S3（dedup 幂等）/ S1（StatusResolver 分支 = 反例表孤儿通知族）/
L1（申请落库 → 通知落库同事务）。触发点全部走真实 API（家长 miniapp + 管理端）。"""

from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, h, phone, name="孩"):
    """造 家长+孩子+mini token（不自动开会员）。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "测试家长", "phone": phone}, headers=h
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


def _mk_activity(client, h, fee=50, title="读书会"):
    r = client.post(
        "/api/admin/activities",
        json={
            "title": title,
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内一层",
            "max_quota": 10,
            "fee": fee,
            "description": "测试活动",
            "member_only": True,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _db():
    from backend.database import get_session

    return get_session()


def _notifs(db, scene=None):
    from backend.common.admin_notification_models import AdminNotification

    q = db.query(AdminNotification).filter(AdminNotification.is_deleted == 0)
    if scene:
        q = q.filter(AdminNotification.scene == scene)
    return q.order_by(AdminNotification.id).all()


# ---------- 触发点 5 处（L1：申请落库 → 通知落库） ----------


def test_refund_apply_creates_admin_notification(client: TestClient):
    """触发点1：家长申请退款 → admin.refund_apply 落库（content 含原因原文，applicant=家长名·孩子名）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001401", "退款孩")
    o = _pay(client, h, c["id"], "observation_fee")
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": o["id"], "reason": "孩子转学去外地"},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        rows = _notifs(db, "admin.refund_apply")
        assert len(rows) == 1
        n = rows[0]
        assert n.ref_type == "refund_request"
        assert n.ref_id == str(r.json()["id"])
        assert "测试家长" in n.applicant_name and "退款孩" in n.applicant_name
        assert "孩子转学去外地" in n.content  # 原文引用不加润色
        assert n.amount is not None and Decimal(n.amount) == Decimal("500")


def test_withdrawal_apply_creates_admin_notification(client: TestClient):
    """触发点2：家长主动退会 → admin.withdrawal_apply 落库。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001402", "退会孩")
    _pay(client, h, c["id"], "formal_fee")
    r = client.post(
        "/api/miniapp/withdrawals",
        json={"child_id": c["id"], "reason": "搬家了"},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        rows = _notifs(db, "admin.withdrawal_apply")
        assert len(rows) == 1
        assert rows[0].ref_type == "withdrawal_request"
        assert "搬家了" in rows[0].content


def test_transfer_apply_creates_admin_notification(client: TestClient):
    """触发点3：家长发起转让 → admin.transfer_apply 落库。"""
    h = _h(client)
    p, src, mini = _family(client, h, "13800001403", "转出孩")
    tgt = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "受让孩"}, headers=h
    ).json()
    _pay(client, h, src["id"], "formal_fee")
    r = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": src["id"], "target_child_id": tgt["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        rows = _notifs(db, "admin.transfer_apply")
        assert len(rows) == 1
        assert rows[0].ref_type == "transfer"
        assert rows[0].ref_id == str(r.json()["id"])


def test_activity_cancel_batch_refund_creates_admin_notification(client: TestClient):
    """触发点4：超管取消有已付费报名的活动 → admin.activity_batch_refund 汇总通知（一条）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001404", "活动孩")
    _pay(client, h, c["id"], "observation_fee")  # member_only 活动需会员
    act = _mk_activity(client, h, fee=50)
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    assert e["enrollment"]["status"] == "pending_payment"
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    r = client.post(f"/api/admin/activities/{act['id']}/cancel", headers=h)
    assert r.status_code == 200, r.text
    with _db() as db:
        rows = _notifs(db, "admin.activity_batch_refund")
        assert len(rows) == 1
        assert rows[0].ref_type == "activity"
        assert rows[0].ref_id == str(act["id"])
        assert "1 笔" in rows[0].content


def test_refund_execute_failed_creates_admin_notification(client: TestClient):
    """触发点5（Q5 裁定新增）：退款执行失败 → admin.refund_execute_failed 落库（挂 execute 失败分支）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001405", "失败孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": o["id"], "reason": "测试执行失败"},
        headers=mini,
    ).json()
    assert (
        client.post(
            f"/api/admin/refund-requests/{rr['id']}/review",
            json={"approve": True, "remark": "同意"},
            headers=h,
        ).status_code
        == 200
    )
    r = client.post(
        f"/api/admin/refund-requests/{rr['id']}/execute",
        json={"success": False, "remark": "银行退回"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        rows = _notifs(db, "admin.refund_execute_failed")
        assert len(rows) == 1
        assert rows[0].ref_type == "refund_request"
        assert rows[0].ref_id == str(rr["id"])
        assert "银行退回" in rows[0].content


# ---------- S3 / S4 / Q8：幂等与审计 ----------


def test_send_dedup_idempotent(db):
    """S3：同一业务事件重复发送仅落一条（先查后插 + 唯一索引双保险）。"""
    from backend.common.admin_notifications import AdminNotifyService

    svc = AdminNotifyService(db)
    kw = dict(
        scene="admin.refund_apply",
        title="【退款申请】",
        content="【退款申请】测试家长为孩申请退款 ￥500。原因：x",
        ref_type="refund_request",
        ref_id="99001",
        applicant_name="测试家长·孩",
        amount=Decimal("500"),
    )
    assert svc.send(**kw) is True
    assert svc.send(**kw) is False  # 幂等：已存在
    db.commit()
    assert len(_notifs(db, "admin.refund_apply")) == 1


def test_mark_handled_idempotent(db):
    """Q8：mark_handled 幂等——已处理保留首次审计（handled_at/handled_by 不覆盖）。"""
    from backend.common.admin_notification_models import AdminNotification
    from backend.common.admin_notifications import AdminNotifyService

    db.add(
        AdminNotification(
            scene="admin.refund_apply",
            title="t",
            content="c",
            ref_type="refund_request",
            ref_id="99002",
            created_at=datetime.now(),
        )
    )
    db.commit()
    admin = type("A", (), {"id": 7, "display_name": "超管甲"})()
    svc = AdminNotifyService(db)
    n1 = svc.mark_handled(ref_type="refund_request", ref_id="99002", admin=admin)
    assert n1 == 1
    first = _notifs(db)[0]
    first_at = first.handled_at
    n2 = svc.mark_handled(ref_type="refund_request", ref_id="99002", admin=admin)
    assert n2 == 0  # 幂等：跳过
    assert _notifs(db)[0].handled_at == first_at
    assert _notifs(db)[0].handled_by == 7


# ---------- StatusResolver（显示态实时算；S1 反例表族） ----------


def _mk_notif_and_refund(db, status: str, ref_id="99010"):
    from backend.common.admin_notification_models import AdminNotification
    from backend.domain.identity.models import RefundRequest

    db.add(
        RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            child_id=1,
            amount=Decimal("500"),
            reason="x",
            status=status,
        )
    )
    db.flush()
    req = db.query(RefundRequest).order_by(RefundRequest.id.desc()).first()
    n = AdminNotification(
        scene="admin.refund_apply",
        title="t",
        content="c",
        ref_type="refund_request",
        ref_id=str(req.id),
        created_at=datetime.now(),
    )
    db.add(n)
    db.commit()
    return n


def test_status_resolver_refund_scenes(db):
    """refund_apply 分支：pending→待处理；cancelled→已失效·家长已撤销；approved/failed→已审结。"""
    from backend.common.admin_notifications import AdminNotifyService

    svc = AdminNotifyService(db)
    n_pending = _mk_notif_and_refund(db, "pending")
    r = svc.resolve_many([n_pending])
    assert r[n_pending.id] == {"effective_status": "pending", "status_text": "待处理"}

    n_cancelled = _mk_notif_and_refund(db, "cancelled")
    r = svc.resolve_many([n_cancelled])
    assert r[n_cancelled.id]["effective_status"] == "invalid"
    assert "撤销" in r[n_cancelled.id]["status_text"]

    n_approved = _mk_notif_and_refund(db, "approved")
    r = svc.resolve_many([n_approved])
    assert r[n_approved.id] == {"effective_status": "done", "status_text": "已审结"}

    n_failed = _mk_notif_and_refund(db, "failed")  # Q5 裁定：failed 归已审结
    r = svc.resolve_many([n_failed])
    assert r[n_failed.id]["effective_status"] == "done"


def test_status_resolver_refund_execute_failed_scene(db):
    """refund_execute_failed 分支：failed→待处理（需重试）；refunded→已审结。"""
    from backend.common.admin_notification_models import AdminNotification
    from backend.common.admin_notifications import AdminNotifyService
    from backend.domain.identity.models import RefundRequest

    svc = AdminNotifyService(db)
    db.add(
        RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            child_id=1,
            amount=Decimal("500"),
            reason="x",
            status=RefundRequest.STATUS_FAILED,
        )
    )
    db.flush()
    req = db.query(RefundRequest).order_by(RefundRequest.id.desc()).first()
    n = AdminNotification(
        scene="admin.refund_execute_failed",
        title="t",
        content="c",
        ref_type="refund_request",
        ref_id=str(req.id),
        created_at=datetime.now(),
    )
    db.add(n)
    db.commit()
    r = svc.resolve_many([n])
    assert r[n.id]["effective_status"] == "pending"

    req.status = RefundRequest.STATUS_REFUNDED
    db.commit()
    r = svc.resolve_many([n])
    assert r[n.id]["effective_status"] == "done"


def test_status_resolver_withdrawal_transfer_scenes(db):
    """withdrawal/transfer/transfer_expiring 分支（Q5 映射表）。"""
    from backend.common.admin_notification_models import AdminNotification
    from backend.common.admin_notifications import AdminNotifyService
    from backend.domain.identity.models import TransferRequest, WithdrawalRequest

    svc = AdminNotifyService(db)
    # withdrawal：applying→待处理；cancelled→失效；rejected→已审结
    w = WithdrawalRequest(child_id=1, reason="x", status=WithdrawalRequest.STATUS_APPLYING)
    db.add(w)
    db.flush()
    n_w = AdminNotification(
        scene="admin.withdrawal_apply",
        title="t",
        content="c",
        ref_type="withdrawal_request",
        ref_id=str(w.id),
        created_at=datetime.now(),
    )
    db.add(n_w)
    db.commit()
    assert svc.resolve_many([n_w])[n_w.id]["effective_status"] == "pending"
    w.status = WithdrawalRequest.STATUS_CANCELLED
    db.commit()
    r = svc.resolve_many([n_w])
    assert r[n_w.id]["effective_status"] == "invalid" and "撤销" in r[n_w.id]["status_text"]
    w.status = WithdrawalRequest.STATUS_REJECTED
    db.commit()
    assert svc.resolve_many([n_w])[n_w.id]["effective_status"] == "done"

    # transfer：pending→待处理；expired→已失效·已超时；approved→已审结
    t = TransferRequest(
        source_child_id=1,
        target_child_id=2,
        status=TransferRequest.STATUS_PENDING,
        expires_at=datetime.now() + timedelta(hours=72),
    )
    db.add(t)
    db.flush()
    n_t = AdminNotification(
        scene="admin.transfer_apply",
        title="t",
        content="c",
        ref_type="transfer",
        ref_id=str(t.id),
        created_at=datetime.now(),
    )
    db.add(n_t)
    db.commit()
    assert svc.resolve_many([n_t])[n_t.id]["effective_status"] == "pending"
    t.status = TransferRequest.STATUS_EXPIRED
    db.commit()
    r = svc.resolve_many([n_t])
    assert r[n_t.id]["effective_status"] == "invalid" and "超时" in r[n_t.id]["status_text"]
    t.status = TransferRequest.STATUS_APPROVED
    db.commit()
    assert svc.resolve_many([n_t])[n_t.id]["effective_status"] == "done"

    # transfer_expiring：expired→失效；pending→待处理（同一业务对象不同 scene）
    n_te = AdminNotification(
        scene="admin.transfer_expiring",
        title="t",
        content="c",
        ref_type="transfer",
        ref_id=str(t.id),
        created_at=datetime.now(),
    )
    db.add(n_te)
    t.status = TransferRequest.STATUS_PENDING
    db.commit()
    assert svc.resolve_many([n_te])[n_te.id]["effective_status"] == "pending"
    t.status = TransferRequest.STATUS_EXPIRED
    db.commit()
    r = svc.resolve_many([n_te])
    assert r[n_te.id]["effective_status"] == "invalid" and "超时" in r[n_te.id]["status_text"]


def test_status_resolver_activity_scene(db):
    """activity_batch_refund：有 REFUND_PENDING→待处理；全部终态→已审结（Q5/A3）。"""
    from backend.common.admin_notification_models import AdminNotification
    from backend.common.admin_notifications import AdminNotifyService
    from backend.domain.activity.models import Activity, ActivityEnrollment

    svc = AdminNotifyService(db)
    a = Activity(
        title="测试活动",
        activity_type="book_club",
        start_at=datetime.now() + timedelta(hours=72),
        location="x",
        max_quota=10,
        fee=Decimal("50"),
        description="",
        status=Activity.STATUS_CANCELLED,
    )
    db.add(a)
    db.flush()
    e = ActivityEnrollment(
        activity_id=a.id,
        child_id=1,
        ticket_code="TK-ACT-1",
        status=ActivityEnrollment.STATUS_ENROLLED,
    )
    db.add(e)
    n = AdminNotification(
        scene="admin.activity_batch_refund",
        title="t",
        content="c",
        ref_type="activity",
        ref_id=str(a.id),
        created_at=datetime.now(),
    )
    db.add(n)
    db.commit()
    assert svc.resolve_many([n])[n.id]["effective_status"] == "done"  # 无 REFUND_PENDING

    e.status = ActivityEnrollment.STATUS_REFUND_PENDING
    db.commit()
    assert svc.resolve_many([n])[n.id]["effective_status"] == "pending"

    e.status = ActivityEnrollment.STATUS_REFUNDED
    db.commit()
    assert svc.resolve_many([n])[n.id]["effective_status"] == "done"


# ---------- S1 反例表：孤儿通知（端到端：撤销/超时后显示态失效+计数归零） ----------


def test_s1_orphan_refund_cancelled(client: TestClient):
    """S1-孤儿：家长申请退款（通知落库）→ 家长撤销 → 显示态=已失效·家长已撤销，不占待处理计数。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001406", "撤销孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": o["id"], "reason": "临时"},
        headers=mini,
    ).json()
    from backend.common.admin_notifications import AdminNotifyService

    with _db() as db:
        rows = _notifs(db, "admin.refund_apply")
        assert len(rows) == 1
        r = AdminNotifyService(db).resolve_many(rows)
        assert r[rows[0].id]["effective_status"] == "pending"
        # 家长撤销
        assert (
            client.post(
                f"/api/miniapp/refund-requests/{rr['id']}/cancel",
                json={"child_id": c["id"]},
                headers=mini,
            ).status_code
            == 200
        )
        db.commit()  # 结束快照事务（REPEATABLE READ），读取 app 已提交的撤销
        db.expire_all()
        rows = _notifs(db, "admin.refund_apply")
        r = AdminNotifyService(db).resolve_many(rows)
        assert r[rows[0].id]["effective_status"] == "invalid"
        assert "撤销" in r[rows[0].id]["status_text"]
        pending_cnt = sum(1 for v in r.values() if v["effective_status"] == "pending")
        assert pending_cnt == 0  # 计数归零


def test_s1_orphan_transfer_expired(client: TestClient):
    """S1-超时：转让通知 → expire_overdue 跑过 → 显示态=已失效·已超时，计数归零。"""
    h = _h(client)
    p, src, mini = _family(client, h, "13800001407", "超时孩")
    tgt = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "受让乙"}, headers=h
    ).json()
    _pay(client, h, src["id"], "formal_fee")
    tr = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": src["id"], "target_child_id": tgt["id"]},
        headers=mini,
    ).json()
    from backend.common.admin_notifications import AdminNotifyService
    from backend.domain.identity.transfer_service import TransferService

    with _db() as db:
        rows = _notifs(db, "admin.transfer_apply")
        assert len(rows) == 1
        assert (
            AdminNotifyService(db).resolve_many(rows)[rows[0].id]["effective_status"] == "pending"
        )
    # 超时（expires_at 改到过去 → 跑 expire_overdue 真实任务方法）
    with _db() as db:
        from backend.domain.identity.models import TransferRequest

        t = db.query(TransferRequest).filter(TransferRequest.id == tr["id"]).first()
        t.expires_at = datetime.now() - timedelta(hours=1)
        db.commit()
        expired = TransferService(db).expire_overdue()
        assert expired >= 1
        db.expire_all()
        rows = _notifs(db, "admin.transfer_apply")
        r = AdminNotifyService(db).resolve_many(rows)
        assert r[rows[0].id]["effective_status"] == "invalid"
        assert "超时" in r[rows[0].id]["status_text"]
        assert sum(1 for v in r.values() if v["effective_status"] == "pending") == 0
