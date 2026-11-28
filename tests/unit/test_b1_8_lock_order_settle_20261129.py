# tests/unit/test_b1_8_lock_order_settle_20261129.py — 专家复审 R1/R3/R4 的回归判据（第一百九十二批）
"""2026-11-29 独立专家复审的三条 P1（去修复即红）：

**R3 锁序成环**：结算侧原先是 `Order → Child`（`confirm_payment` / 网关回调先锁 Order，
`_settle_paid → prepare_settle` 再锁 Child），退款回调是 `Child → Order`（持 Child 后 UPDATE Order）
—— 并发稳定成环（1213，回调方当受害者 → 微信重发 24h）。
修法：结算侧改两段式 `无锁定位 → Child → Order`；回调侧补显式 Order 锁，与 `execute` 的
`Child → Order → RefundRequest` 对齐（消掉"持退款单再去等订单"的隐式升级）。

**R4 撤销绕过 Child 锁**：家长撤销退款只锁退款单，却复位押金 + 撤销联动退会（= 解除孩子冻结），
而冻结的写入口（退会 apply / 转让 create）都在 Child 行锁内 → 撤销不持锁就能与其交错。

**R1 带 key 的赔偿扣款跳过审计**：`if client_request_id:` 分支成功后直接 return，
把 `publish_audit("deposit.deduct")` 整段跳过（前端现在总是传 key）。

判据取"**加锁顺序**"而不是"跑一把看会不会 1213"：1312 类竞态要撞时序才有概率，做进单测必成
flaky；而顺序是所有死锁的充分条件——顺序对了，环就不存在。顺序用 spies 直接观测。
"""

from __future__ import annotations

from decimal import Decimal

from backend.domain.billing.models import Deposit, DepositLedger
from backend.domain.identity import lock_order, refund_online
from backend.domain.identity.models import Order, RefundRequest
from backend.domain.identity.wm10_service import RefundService


def _family_child(client, admin_headers, phone: str, name: str = "锁序孩") -> int:
    p = client.post(
        "/api/admin/members/parents",
        json={"name": "锁序家长", "phone": phone},
        headers=admin_headers,
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": name},
        headers=admin_headers,
    ).json()
    return c["id"]


def _pending_membership_order(client, admin_headers, child_id: int) -> int:
    """造一笔**待收款**的会员单（不确认），返回 order_id。"""
    resp = client.post(
        "/api/admin/orders",
        json={"child_id": child_id, "order_type": "observation_fee", "remark": "锁序用例"},
        headers=admin_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    return body.get("id") or body["order_id"]


def _spy_locks(monkeypatch) -> list[str]:
    """记录 `lock_order` 的加锁调用顺序（child / order / refund / withdrawal / deposit）。"""
    seq: list[str] = []
    for name in ("lock_child", "lock_order", "lock_refund_request", "lock_withdrawal"):
        original = getattr(lock_order, name)

        def wrapper(*args, _name=name, _original=original, **kwargs):
            seq.append(_name.replace("lock_", ""))
            return _original(*args, **kwargs)

        monkeypatch.setattr(lock_order, name, wrapper)
    return seq


# ---------------------------------------------------------------- R3 结算侧


def test_confirm_payment_locks_child_before_order(client, admin_headers, monkeypatch):
    """收款确认必须先锁 Child 再锁 Order（否则与退款回调的 Child→Order 成环）。"""
    child_id = _family_child(client, admin_headers, "13800009801")
    order_id = _pending_membership_order(client, admin_headers, child_id)

    seq = _spy_locks(monkeypatch)
    resp = client.post(
        f"/api/admin/orders/{order_id}/confirm-payment",
        json={"pay_method": "scan"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert seq[:2] == ["child", "order"], f"加锁序应为 Child→Order，实际 {seq}"


def test_refund_callback_locks_child_order_refund(client, admin_headers, monkeypatch, db):
    """退款回调必须 Child → Order → RefundRequest（与 execute 同序，消掉隐式升级）。"""
    child_id = _family_child(client, admin_headers, "13800009802")
    order_id = _pending_membership_order(client, admin_headers, child_id)
    ok = client.post(
        f"/api/admin/orders/{order_id}/confirm-payment",
        json={"pay_method": "scan"},
        headers=admin_headers,
    )
    assert ok.status_code == 200, ok.text

    o = db.query(Order).filter(Order.id == order_id).first()
    assert o.status == "paid"
    rr = RefundRequest(
        kind=RefundRequest.KIND_ORDER,
        order_id=o.id,
        child_id=child_id,
        amount=o.amount,
        reason="R3 锁序回归",
        status=RefundRequest.STATUS_PROCESSING,
        out_refund_no=f"RF{o.id}-1-9",
    )
    db.add(rr)
    db.commit()
    req_id = rr.id
    db.rollback()

    seq = _spy_locks(monkeypatch)
    with db.begin():
        refund_online._lock_child_then_refund(db, req_id, not_found="x")
    db.rollback()
    assert seq[:3] == ["child", "order", "refund_request"], (
        f"回调加锁序应为 Child→Order→RefundRequest，实际 {seq}"
    )


# ---------------------------------------------------------------- R4 撤销


def test_cancel_locks_child_before_refund_request(client, admin_headers, monkeypatch, db):
    """家长撤销退款必须先锁 Child（它要复位押金并解除联动退会冻结）。"""
    child_id = _family_child(client, admin_headers, "13800009803")
    dep = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=admin_headers)
    assert dep.status_code == 200, dep.text
    dep_payload = dep.json()
    deposit_order_id = dep_payload.get("id") or dep_payload["order_id"]
    paid = client.post(
        f"/api/admin/orders/{deposit_order_id}/confirm-payment",
        json={"pay_method": "scan"},
        headers=admin_headers,
    )
    assert paid.status_code == 200, paid.text

    rr = RefundRequest(
        kind=RefundRequest.KIND_DEPOSIT,
        deposit_id=db.query(Deposit).filter(Deposit.child_id == child_id).first().id,
        child_id=child_id,
        amount=Decimal("1200.00"),
        reason="R4 撤销锁序回归",
        status=RefundRequest.STATUS_PENDING,
    )
    db.add(rr)
    db.commit()
    req_id = rr.id
    db.rollback()

    from backend.domain.identity.models import Child

    child = db.query(Child).filter(Child.id == child_id).first()
    # 撤销侧：退款单锁是**内联** `with_for_update`（带 child_id 归属过滤，不能换成共享 helper），
    # 所以这里只断言"Child 先于退款单"这一条不变量；退款单是否真被锁定由状态机用例覆盖。
    seq = _spy_locks(monkeypatch)
    RefundService(db).cancel(child, req_id)
    db.rollback()
    assert seq and seq[0] == "child", f"撤销必须先锁 Child（实际 {seq}）"


# ---------------------------------------------------------------- R1 审计


def test_keyed_compensation_deduct_writes_audit_row(client, admin_headers, db):
    """带 client_request_id 的赔偿扣款成功落台账后，必须留下 `deposit.deduct` 审计行。"""
    child_id = _family_child(client, admin_headers, "13800009804")
    dep = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=admin_headers)
    deposit_order_id = dep.json().get("id") or dep.json()["order_id"]
    assert (
        client.post(
            f"/api/admin/orders/{deposit_order_id}/confirm-payment",
            json={"pay_method": "scan"},
            headers=admin_headers,
        ).status_code
        == 200
    )

    resp = client.post(
        f"/api/admin/deposits/children/{child_id}/deduct",
        json={"amount": "200.00", "reason": "审计回归", "client_request_id": "audit-reg-1"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["available_amount"]) == Decimal("1000.00")

    from backend.domain.admin.models import AuditLog

    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "deposit.deduct",
            AuditLog.target_id
            == str(db.query(Deposit).filter(Deposit.child_id == child_id).first().id),
        )
        .all()
    )
    assert rows, "带 key 的赔偿扣款没有写审计行（R1 未修）"
    assert (
        db.query(DepositLedger).filter(DepositLedger.client_request_id == "audit-reg-1").count()
        == 1
    )
