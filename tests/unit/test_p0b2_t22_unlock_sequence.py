# tests/unit/test_p0b2_t22_unlock_sequence.py — P0 第二批 T22（B-2）解锁时序统一
"""红测试：主动退会两笔结算退款单（会员费+押金）——会员费 execute 成功即解锁
（_complete_refund_withdrawal 内 operation_locked=0），押金单仍在途而孩子已解锁，
状态语义不一致。

修复：删除该行解锁，唯一解锁点 = _advance_withdrawal（全部退款单终态才
completed+解锁；refund_linked 单申请路径首单成功即 completed，行为不变）。
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay, _pay_deposit


def test_withdrawal_unlock_only_after_all_refunds_done(client: TestClient):
    h = _h(client)
    p, c, mini = _family(client, h, "13981022001", "双单解锁孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    from backend.domain.identity.models import Child, WithdrawalRequest

    with _db() as db:
        w = WithdrawalRequest(
            child_id=c["id"],
            source=WithdrawalRequest.SOURCE_NORMAL,
            reason="双单退会",
            status=WithdrawalRequest.STATUS_APPLYING,
        )
        db.add(w)
        db.flush()
        wid = w.id
        child = db.query(Child).filter(Child.id == c["id"]).first()
        child.operation_locked = 1
        db.commit()

    # approve → 结算两张退款单（会员费 order 项 + 押金项）
    r = client.post(
        f"/api/admin/withdrawals/{wid}/review",
        json={"approve": True, "remark": "双单退会通过"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    from backend.domain.identity.models import RefundRequest

    with _db() as db:
        rrs = (
            db.query(RefundRequest)
            .filter(RefundRequest.withdrawal_id == wid, RefundRequest.is_deleted == 0)
            .all()
        )
        assert len(rrs) == 2, f"结算应两张退款单，实 {len(rrs)}"
        rr_order = next(x for x in rrs if x.kind == RefundRequest.KIND_ORDER)
        rr_dep = next(x for x in rrs if x.kind == RefundRequest.KIND_DEPOSIT)

    # 会员费单：approve → execute success
    client.post(
        f"/api/admin/refund-requests/{rr_order.id}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    ex1 = client.post(
        f"/api/admin/refund-requests/{rr_order.id}/execute",
        json={"success": True, "remark": "会员费原路退回"},
        headers=h,
    )
    assert ex1.status_code == 200, ex1.text

    # T22 灵魂断言：第一笔成功后孩子仍锁定、退会单仍 refunding（押金单在途）
    with _db() as db:
        child = db.query(Child).filter(Child.id == c["id"]).first()
        w2 = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
        assert child.member_status == Child.MEMBER_WITHDRAWN
        assert child.operation_locked == 1, (
            f"会员费单成功后押金单仍在途，孩子应仍锁定，实 locked={child.operation_locked}"
            "（RED=半程解锁）"
        )
        assert w2.status == WithdrawalRequest.STATUS_REFUNDING

    # 押金单：approve → execute success → 全部终态 → completed + 解锁
    client.post(
        f"/api/admin/refund-requests/{rr_dep.id}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    ex2 = client.post(
        f"/api/admin/refund-requests/{rr_dep.id}/execute",
        json={"success": True, "remark": "押金原路退回"},
        headers=h,
    )
    assert ex2.status_code == 200, ex2.text
    with _db() as db:
        child = db.query(Child).filter(Child.id == c["id"]).first()
        w2 = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
        assert w2.status == WithdrawalRequest.STATUS_COMPLETED
        assert child.operation_locked == 0, "全部退款单终态后应解锁"
