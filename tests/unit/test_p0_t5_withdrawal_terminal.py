# tests/unit/test_p0_t5_withdrawal_terminal.py — P0 第一批 T5（B-13）退款 reject/cancel 增加退会状态推进
"""退会死锁红测试。

现象（B-13）：退会批准后 w=REFUNDING，结算退款单全部被 reject/cancel →
_advance_withdrawal 仅由 execute 成功触发 → w 永卡 REFUNDING，child 永久
operation_locked=1，无出口（execute 失败有出口回 pending_settle，但 reject/cancel 没有）。

业务语义（用户已确认）：全拒 = 退会整体失败（REJECTED + 解锁 + member 不变）；
部分成功 = execute 成功路径已有 _complete_refund_withdrawal 处理，保持不变。

三条：
1. 全部 reject → w=REJECTED + child.operation_locked=0 + member_status 未变
2. 1 笔 execute 成功 + 其余 reject → w=COMPLETED
3. 家长 cancel 路径同款推进（结算单全 cancel → REJECTED；1 成功 + 其余 cancel → COMPLETED）
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h, _pay, _pay_deposit


def _db():
    from backend.database import get_session

    return get_session()


def _withdrawal_applying(client, h, child_id):
    from backend.domain.identity.models import Child, WithdrawalRequest

    with _db() as db:
        w = WithdrawalRequest(
            child_id=child_id,
            source=WithdrawalRequest.SOURCE_NORMAL,
            reason="退会",
            status=WithdrawalRequest.STATUS_APPLYING,
        )
        db.add(w)
        db.flush()
        wid = w.id
        ch = db.query(Child).filter(Child.id == child_id).first()
        ch.operation_locked = 1
        db.commit()
    return wid


def _approve_settle(client, h, wid):
    from backend.domain.identity.models import RefundRequest

    r = client.post(
        f"/api/admin/withdrawals/{wid}/review",
        json={"approve": True, "remark": "同意退会"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        settle = (
            db.query(RefundRequest)
            .filter(RefundRequest.withdrawal_id == wid, RefundRequest.is_deleted == 0)
            .all()
        )
        return [s.id for s in settle]


def test_withdrawal_all_settle_rejected_restores(client: TestClient):
    """场景1：全部结算单 reject → 退会 REJECTED + 解锁 + member 保留（不死锁）。"""
    from backend.domain.identity.models import Child, WithdrawalRequest

    h = _h(client)
    p, c, mini = _family(client, h, "13980005501", "退会全拒孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    wid = _withdrawal_applying(client, h, c["id"])
    sids = _approve_settle(client, h, wid)
    assert len(sids) >= 2, f"应生成 ≥2 张结算单，实 {len(sids)}"

    for sid in sids:
        r = client.post(
            f"/api/admin/refund-requests/{sid}/review",
            json={"approve": False, "remark": "拒绝"},
            headers=h,
        )
        assert r.status_code == 200, r.text

    with _db() as db:
        w = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
        ch = db.query(Child).filter(Child.id == c["id"]).first()
        assert w.status == WithdrawalRequest.STATUS_REJECTED, f"w 应 REJECTED，实 {w.status}"
        assert ch.operation_locked == 0, "孩子应解锁（全拒退会失败）"
        assert ch.member_status == Child.MEMBER_OBSERVATION, (
            f"member 应保留 observation，实 {ch.member_status}"
        )


def test_withdrawal_partial_settle_rejected_completed(client: TestClient):
    """场景2：1 笔 execute 成功 + 其余 reject → 退会 COMPLETED（_advance_withdrawal 语义）。"""
    from backend.domain.identity.models import WithdrawalRequest

    h = _h(client)
    p, c, mini = _family(client, h, "13980005502", "退会部分孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    wid = _withdrawal_applying(client, h, c["id"])
    sids = _approve_settle(client, h, wid)

    # 第 1 张：approve + execute 成功
    r = client.post(
        f"/api/admin/refund-requests/{sids[0]}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/admin/refund-requests/{sids[0]}/execute",
        json={"success": True, "remark": "线下打款"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    # 其余 reject
    for sid in sids[1:]:
        r = client.post(
            f"/api/admin/refund-requests/{sid}/review",
            json={"approve": False, "remark": "拒绝"},
            headers=h,
        )
        assert r.status_code == 200, r.text

    with _db() as db:
        w = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
        assert w.status == WithdrawalRequest.STATUS_COMPLETED, f"w 应 COMPLETED，实 {w.status}"


def test_withdrawal_settle_cancelled_all_restores(client: TestClient):
    """场景3（cancel）：全部结算单家长 cancel → 退会 REJECTED + 解锁。"""
    from backend.domain.identity.models import Child, WithdrawalRequest

    h = _h(client)
    p, c, mini = _family(client, h, "13980005503", "退会全撤孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    wid = _withdrawal_applying(client, h, c["id"])
    sids = _approve_settle(client, h, wid)

    for sid in sids:
        r = client.post(
            f"/api/miniapp/refund-requests/{sid}/cancel",
            json={"child_id": c["id"]},
            headers=mini,
        )
        assert r.status_code == 200, r.text

    with _db() as db:
        w = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
        ch = db.query(Child).filter(Child.id == c["id"]).first()
        assert w.status == WithdrawalRequest.STATUS_REJECTED, f"w 应 REJECTED，实 {w.status}"
        assert ch.operation_locked == 0, "孩子应解锁（全撤退会失败）"
        assert ch.member_status == Child.MEMBER_OBSERVATION, "member 应保留 observation"
