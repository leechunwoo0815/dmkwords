# tests/unit/test_p0b2_t24_ledger_locks.py — P0 第二批 T24（B-4+B-5）押金台账余额 + review 订单锁
"""B-4 红测试：押金退款 execute 硬编码 balance_after=Decimal("0") 且直接清零
available_amount——未来部分退款台账断链（台账记 0、余额也 0，但实退 amount<余额）。

修复：先扣减再记账（balance_after=实扣后余额）。
测试造部分退场景：结算押金单后、execute 前直插改 rr.amount=available-100 →
断言 available==100 且 ledger.balance_after==100（修复前 available=0 = RED）。

B-5（review 读 Order 加锁定读）为防御纵深，无独立行为断言——锁存在性由
架构关 populate_existing 规则背书。
"""

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay, _pay_deposit


def test_deposit_refund_ledger_balance_after_partial(client: TestClient):
    h = _h(client)
    p, c, mini = _family(client, h, "13981024001", "台账余额孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    from backend.domain.billing.models import Deposit, DepositLedger
    from backend.domain.identity.models import Child, RefundRequest, WithdrawalRequest

    with _db() as db:
        dep = db.query(Deposit).filter(Deposit.child_id == c["id"], Deposit.is_deleted == 0).first()
        initial = dep.available_amount
        assert initial > 0
        w = WithdrawalRequest(
            child_id=c["id"],
            source=WithdrawalRequest.SOURCE_NORMAL,
            reason="台账余额退会",
            status=WithdrawalRequest.STATUS_APPLYING,
        )
        db.add(w)
        db.flush()
        wid = w.id
        child = db.query(Child).filter(Child.id == c["id"]).first()
        child.operation_locked = 1
        db.commit()

    r = client.post(
        f"/api/admin/withdrawals/{wid}/review",
        json={"approve": True, "remark": "台账余额通过"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        rr_dep = (
            db.query(RefundRequest)
            .filter(
                RefundRequest.withdrawal_id == wid,
                RefundRequest.kind == RefundRequest.KIND_DEPOSIT,
                RefundRequest.is_deleted == 0,
            )
            .first()
        )
        assert rr_dep is not None, "结算应含押金退款单"

    # 造部分退：金额改为 余额-100（模拟未来部分退款）
    partial = initial - Decimal("100")
    with _db() as db:
        rr = db.query(RefundRequest).filter(RefundRequest.id == rr_dep.id).first()
        rr.amount = partial
        db.commit()

    client.post(
        f"/api/admin/refund-requests/{rr_dep.id}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    ex = client.post(
        f"/api/admin/refund-requests/{rr_dep.id}/execute",
        json={"success": True, "remark": "部分退"},
        headers=h,
    )
    assert ex.status_code == 200, ex.text

    with _db() as db:
        dep = db.query(Deposit).filter(Deposit.child_id == c["id"], Deposit.is_deleted == 0).first()
        expected_left = initial - partial  # 扣后余额（1200-1100=100）
        assert dep.available_amount == expected_left, (
            f"部分退后余额应 {expected_left}，实 {dep.available_amount}（RED=硬编码清零台账断链）"
        )
        ledger = (
            db.query(DepositLedger)
            .filter(
                DepositLedger.deposit_id == dep.id,
                DepositLedger.entry_type == DepositLedger.ENTRY_REFUND,
            )
            .order_by(DepositLedger.id.desc())
            .first()
        )
        assert ledger.balance_after == expected_left, (
            f"台账 balance_after 应 {expected_left}，实 {ledger.balance_after}（RED=硬编码 0）"
        )
