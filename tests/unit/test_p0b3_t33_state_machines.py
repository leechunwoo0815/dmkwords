# tests/unit/test_p0b3_t33_state_machines.py — P0 第三批 T33（B-3）状态机显式声明
"""防御性声明卡（只做声明+主链接入，不改行为）：

- 枚举测试：两模型 ALLOWED_TRANSITIONS 全部合法边 assert_transition 不抛；
  全部非法边抛 ConflictError
- 非法跳转被拦：构造 applying→completed 直接跳（绕过结算/退款主链）→ 409
- HTTP 链路抽查：真实 review 流程转换零拦截（存量转换全合法的实链证词）
"""

import pytest
from fastapi.testclient import TestClient

from backend.common.exceptions import ConflictError
from backend.domain.identity.models import RefundRequest, WithdrawalRequest
from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay


def _all_statuses(model):
    return [v for k, v in vars(model).items() if k.startswith("STATUS_") and isinstance(v, str)]


def test_refund_request_full_matrix_enum():
    m = RefundRequest
    for frm in _all_statuses(m):
        for to in _all_statuses(m):
            inst = m(status=frm)
            if to in m.ALLOWED_TRANSITIONS.get(frm, set()):
                inst.assert_transition(to)  # 合法边不抛
            else:
                with pytest.raises(ConflictError):
                    inst.assert_transition(to)


def test_withdrawal_request_full_matrix_enum():
    m = WithdrawalRequest
    for frm in _all_statuses(m):
        for to in _all_statuses(m):
            inst = m(status=frm)
            if to in m.ALLOWED_TRANSITIONS.get(frm, set()):
                inst.assert_transition(to)
            else:
                with pytest.raises(ConflictError):
                    inst.assert_transition(to)


def test_withdrawal_skip_settlement_blocked(client: TestClient):
    """applying → completed 直接跳（绕过结算/退款主链）被状态机拦（409）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981033001", "状态机孩")
    _pay(client, h, c["id"], "observation_fee")
    from backend.domain.identity.models import WithdrawalRequest

    with _db() as db:
        w = WithdrawalRequest(
            child_id=c["id"],
            source=WithdrawalRequest.SOURCE_NORMAL,
            reason="跳态尝试",
            status=WithdrawalRequest.STATUS_APPLYING,
        )
        db.add(w)
        db.flush()
        wid = w.id
        db.commit()

    with _db() as db:
        w2 = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
        with pytest.raises(ConflictError):
            w2.assert_transition(WithdrawalRequest.STATUS_COMPLETED)


def test_real_review_flow_transitions_all_legal(client: TestClient):
    """HTTP 实链抽查：真实 review→结算→refund 流程的每次转换都被矩阵放行。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981033002", "实链状态机孩")
    _pay(client, h, c["id"], "observation_fee")
    from backend.domain.identity.models import Child, WithdrawalRequest

    with _db() as db:
        w = WithdrawalRequest(
            child_id=c["id"],
            source=WithdrawalRequest.SOURCE_NORMAL,
            reason="实链抽查",
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
        json={"approve": True, "remark": "实链抽查"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        w2 = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
        assert w2.status in (
            WithdrawalRequest.STATUS_REFUNDING,
            WithdrawalRequest.STATUS_COMPLETED,
        )
