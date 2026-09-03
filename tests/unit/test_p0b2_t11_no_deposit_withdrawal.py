# tests/unit/test_p0b2_t11_no_deposit_withdrawal.py — P0 第二批 T11（B-13 挂账）无押金孩退会 approve 500
"""红测试：wm10_withdrawal_service.review approve 分支的审计 detail 引用 dep，
但 dep 仅在 settle 含押金项（KIND_DEPOSIT）时赋值——无押金孩子 approve →
UnboundLocalError → 500。

修复：settle 循环前初始化 dep = None（专家裁定：第二批首卡）。
场景：无押金 + 付观察期费 → settle 仅 order 项 → refunding（当前 500 = RED）。
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay


def test_withdrawal_approve_without_deposit_ok(client: TestClient):
    h = _h(client)
    p, c, mini = _family(client, h, "13981011001", "无押金退会孩")
    _pay(client, h, c["id"], "observation_fee")
    # 直插退会申请 applying（对齐 T4 模式：无借书/无进行中退款申请 → preconditions 空）
    from backend.domain.identity.models import Child, WithdrawalRequest

    with _db() as db:
        w = WithdrawalRequest(
            child_id=c["id"],
            source=WithdrawalRequest.SOURCE_NORMAL,
            reason="无押金退会",
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
        json={"approve": True, "remark": "无押金退会通过"},
        headers=h,
    )
    assert r.status_code == 200, f"无押金 approve 不应 500：{r.status_code} {r.text[:120]}"

    with _db() as db:
        from backend.domain.identity.models import RefundRequest, WithdrawalRequest as W

        w2 = db.query(W).filter(W.id == wid).first()
        assert w2.status == W.STATUS_REFUNDING
        rr = (
            db.query(RefundRequest)
            .filter(RefundRequest.withdrawal_id == wid, RefundRequest.is_deleted == 0)
            .all()
        )
        assert len(rr) == 1 and rr[0].kind == RefundRequest.KIND_ORDER
