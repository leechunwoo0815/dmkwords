# tests/unit/test_p0_t4_review_locks.py — P0 第一批 T4（B-14+B-1）退会/转让 review 双加行锁
"""退会/转让 review 无行锁并发红测试。

现象（B-14/B-1）：WithdrawalService.review / TransferService.review 第一步
普通读（无 with_for_update），identity map 返回旧实例 applying/pending →
并发双审批双双通过校验 → 双倍结算退款单 / 双 12 步事务。

结构（identity map 语义，参照 T2）：
- s2 普通读载入 applying 旧实例
- A 走 HTTP review approve（提交：refunding + 结算退款单）
- B（s2 旧实例）再 review approve：修复前普通读返回 applying → 双结算（RED）；
  修复后锁定读 + populate_existing → refunding → 422（GREEN）
"""

import pytest
from fastapi.testclient import TestClient

from backend.common.exceptions import ValidationError
from tests.unit.test_wm10_concurrency import _h, _family, _pay, _pay_deposit


def _db():
    from backend.database import get_session

    return get_session()


def _admin():
    return type("A", (), {"id": 1, "display_name": "超管"})()


def test_withdrawal_review_double_approve_blocked(client: TestClient, session_pair):
    from backend.domain.identity.models import RefundRequest, WithdrawalRequest
    from backend.domain.identity.wm10_withdrawal_service import WithdrawalService

    h = _h(client)
    p, c, mini = _family(client, h, "13980004401", "退会锁孩")
    order = _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    # 直插退会申请 applying（child 无借书/无进行中退款申请 → preconditions 空，避开联动链）
    from backend.domain.identity.models import Child, WithdrawalRequest

    with _db() as db:
        w = WithdrawalRequest(
            child_id=c["id"],
            source=WithdrawalRequest.SOURCE_NORMAL,
            reason="并发退会",
            status=WithdrawalRequest.STATUS_APPLYING,
        )
        db.add(w)
        db.flush()
        wid = w.id
        child = db.query(Child).filter(Child.id == c["id"]).first()
        child.operation_locked = 1
        db.commit()

    s1, s2 = session_pair
    # s2 普通读载入 applying 旧实例（identity map）
    stale = s2.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
    assert stale.status == WithdrawalRequest.STATUS_APPLYING

    # A 走 HTTP review approve（提交：refunding + 结算退款单）
    r_a = client.post(
        f"/api/admin/withdrawals/{wid}/review",
        json={"approve": True, "remark": "先到者同意"},
        headers=h,
    )
    assert r_a.status_code == 200, r_a.text

    # B（s2 identity map 旧实例 applying）再 approve：修复后锁定读 → refunding → 422
    with pytest.raises(ValidationError):
        WithdrawalService(s2).review(_admin(), wid, True, "并发同意")
    s2.rollback()

    # 断言结算退款单仅 1 套（A 的）
    with _db() as db:
        db.commit()
        rrs = (
            db.query(RefundRequest)
            .filter(RefundRequest.withdrawal_id == wid, RefundRequest.is_deleted == 0)
            .all()
        )
        assert len(rrs) == 2, f"结算退款单应 1 套(2 张)，实 {len(rrs)}（双结算 RED）"


def test_transfer_review_double_approve_blocked(client: TestClient, session_pair):
    """线程真并发（参照 T3）：s1 锁 TransferRequest 模拟先到者进行中；
    B 线程走 TransferService.review——
    修复前无锁：B 普通读不阻塞 → 校验通过 → approve 的 UPDATE 阻塞在 s1 锁上，
    s1 提交后 B 覆盖 approve（双处理 RED）；
    修复后：B 锁定读阻塞 → s1 提交 → B 读 approved → 422（GREEN）。"""
    import threading
    import time

    from backend.domain.identity.models import TransferRequest
    from backend.domain.identity.transfer_service import TransferService

    h = _h(client)
    # 源孩（权益转出方）：formal + 押金；目标孩：无会籍（受让方校验现状仅接受无会籍/已退会）
    src = client.post(
        "/api/admin/members/parents", json={"name": "转让家长", "phone": "13980004402"}, headers=h
    ).json()
    sc = client.post(
        f"/api/admin/members/parents/{src['id']}/children", json={"name": "源孩"}, headers=h
    ).json()
    _pay(client, h, sc["id"], "formal_fee")
    _pay_deposit(client, h, sc["id"])
    tc = client.post(
        f"/api/admin/members/parents/{src['id']}/children", json={"name": "目标孩"}, headers=h
    ).json()

    # 创建转让申请（家长侧）
    mini_src = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': '13980004402', 'code': '1234'}).json()['token']}"
    }
    r = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": sc["id"], "target_child_id": tc["id"]},
        headers=mini_src,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        tr = db.query(TransferRequest).filter(TransferRequest.source_child_id == sc["id"]).first()
        tid = tr.id

    s1, s2 = session_pair
    # s1 锁 TransferRequest（模拟先到者 A 进行中）
    s1.query(TransferRequest).filter(TransferRequest.id == tid).with_for_update().first()

    results = {}

    def b_review():
        try:
            TransferService(s2).review(_admin(), tid, True, "并发同意")
            s2.commit()
            results["ok"] = True
        except Exception as e:  # noqa: BLE001
            results["err"] = type(e).__name__

    t = threading.Thread(target=b_review)
    t.start()
    time.sleep(1.0)  # 给 B 时间进入 review（修复后应阻塞在 TransferRequest 锁）
    # s1 提交 A 的终态（approved）——模拟先到者已完成审批
    s1.query(TransferRequest).filter(TransferRequest.id == tid).update(
        {"status": TransferRequest.STATUS_APPROVED, "reviewed_by": 1}
    )
    s1.commit()
    t.join(timeout=8)
    assert not t.is_alive(), "B 线程 8s 未结束（疑似死锁）"

    # 修复后：B 锁定读阻塞 → 读 approved → 422；修复前 B 覆盖 approve 成功（RED）
    assert results.get("err") == "ValidationError", f"应 422 已处理，实 {results}"
