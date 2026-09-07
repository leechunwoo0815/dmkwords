# tests/unit/test_p0_batch4.py — 第四批（26 号任务包 T40-T47）
"""T41（H-1）：退款执行六项复核机器化（R-310）——execute 翻状态前查未清项，
不过 422 不翻状态（M2 裁定语义）；「人工放行:」冒号前缀+超管豁免+审计
manual_override 布尔字段（Q5 批复）。会员资格类（observation/formal）与押金
（KIND_DEPOSIT·担保语义 Q4 批复）触发；活动费/自定义不触发。"""

from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h, _pay
from tests.unit.test_wm13_admin_inbox import _db

REFUND_APPLY_URL = "/api/miniapp/refund-requests"


def _member_refund_to_approved(client, h, mini, child, order_id, reason="退款"):
    """会员费退款单推进到 approved，返回 rr id。"""
    rr = client.post(
        REFUND_APPLY_URL,
        json={"child_id": child["id"], "order_id": order_id, "reason": reason},
        headers=mini,
    ).json()
    r = client.post(
        f"/api/admin/refund-requests/{rr['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return rr["id"]


def _mk_overdue(ctx_db_flag=True, child_id=0, overdue=True):
    """直造借阅记录（preconditions 查询只按 child_id+status+due_at，无 JOIN）。"""
    from backend.database import get_session
    from backend.domain.circulation.models import BorrowRecord

    with get_session() as db:
        db.add(
            BorrowRecord(
                child_id=child_id,
                book_id=0,
                copy_id=0,
                status=BorrowRecord.STATUS_ACTIVE,
                due_at=datetime.now() - timedelta(days=3)
                if overdue
                else datetime.now() + timedelta(days=3),
            )
        )
        db.commit()


def test_t41_member_refund_blocked_by_overdue(client: TestClient):
    """修复前：有逾期孩退款执行直接 200（六项零实现）= RED。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981050001", "逾期孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rid = _member_refund_to_approved(client, h, mini, c, o["id"])
    _mk_overdue(child_id=c["id"], overdue=True)
    r = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "打款"},
        headers=h,
    )
    assert r.status_code == 422, f"有逾期应 422 拦截，实 {r.status_code} {r.text[:120]} = RED"
    assert "逾期" in r.json()["detail"], r.text
    # 不翻状态（M2 语义：校验失败不流转）——仍 approved 可再执行
    from backend.domain.identity.models import RefundRequest

    with _db() as db:
        row = db.query(RefundRequest).filter(RefundRequest.id == rid).first()
        assert row.status == RefundRequest.STATUS_APPROVED, f"应保持 approved，实 {row.status}"


def test_t41_passes_after_overdue_cleared(client: TestClient):
    """清逾期后执行通过。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981050002", "清欠孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rid = _member_refund_to_approved(client, h, mini, c, o["id"])
    _mk_overdue(child_id=c["id"], overdue=True)
    # 还书（状态置 returned）
    from backend.database import get_session
    from backend.domain.circulation.models import BorrowRecord

    with get_session() as db:
        db.query(BorrowRecord).filter(BorrowRecord.child_id == c["id"]).update(
            {"status": BorrowRecord.STATUS_RETURNED}
        )
        db.commit()
    r = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "打款"},
        headers=h,
    )
    assert r.status_code == 200, r.text


def test_t41_manual_override_with_audit(client: TestClient):
    """「人工放行:」前缀+超管 → 跳过复核+审计含 manual_override。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981050003", "放行孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rid = _member_refund_to_approved(client, h, mini, c, o["id"])
    _mk_overdue(child_id=c["id"], overdue=True)
    r = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "人工放行: 家长当面结清承诺"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        from backend.domain.admin.models import AuditLog

        row = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "refund.execute",
                AuditLog.target_id == str(rid),
                AuditLog.is_deleted == 0,
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert row is not None, "执行审计应存在"
        import json

        detail = json.loads(row.detail) if isinstance(row.detail, str) else row.detail
        assert detail.get("manual_override") is True, f"审计应含 manual_override：{detail}"


def test_t41_activity_order_not_checked(client: TestClient):
    """活动费单不触发复核（退款不动会员态）。"""
    from datetime import timedelta

    h = _h(client)
    p, c, mini = _family(client, h, "13981050004", "活动款孩")
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "复核豁免活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内",
            "max_quota": 5,
            "fee": 60,
            "description": "T41",
            "member_only": False,
        },
        headers=h,
    ).json()
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    _mk_overdue(child_id=c["id"], overdue=True)
    rr = client.post(
        REFUND_APPLY_URL,
        json={"child_id": c["id"], "order_id": e["order_id"], "reason": "不参加了"},
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
        json={"success": True, "remark": "打款"},
        headers=h,
    )
    assert r.status_code == 200, f"活动费退款不触发复核，实 {r.status_code} {r.text[:120]}"


def test_t41_deposit_checked(client: TestClient):
    """押金单触发复核（担保语义：未还书退押金=担保落空，Q4 批复）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981050005", "押金孩")
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    _mk_overdue(child_id=c["id"], overdue=True)
    # 造 approved 押金退款单（复核在 execute 层，直达）
    from backend.database import get_session
    from backend.domain.identity.models import RefundRequest

    with get_session() as db:
        from backend.domain.billing.models import Deposit

        dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
        rr = RefundRequest(
            kind=RefundRequest.KIND_DEPOSIT,
            child_id=c["id"],
            deposit_id=dep.id,
            amount=Decimal("200"),
            reason="退会退押金",
            status=RefundRequest.STATUS_APPROVED,
        )
        db.add(rr)
        db.flush()
        rid = rr.id
        db.commit()
    r = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "打款"},
        headers=h,
    )
    assert r.status_code == 422, f"押金退款未还书应 422，实 {r.status_code} {r.text[:120]} = RED"
