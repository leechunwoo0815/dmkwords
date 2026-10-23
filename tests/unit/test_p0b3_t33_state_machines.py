# tests/unit/test_p0b3_t33_state_machines.py — P0 第三批 T33（B-3）状态机显式声明
"""防御性声明卡（只做声明+主链接入，不改行为）：

- **独立期望表**：四张矩阵（Child 会员态 / Order / RefundRequest / WithdrawalRequest）的合法边
  在下面**写死**，与实现表逐边比对；合法边不抛、其余组合必抛 ConflictError
- 非法跳转被拦：构造 applying→completed 直接跳（绕过结算/退款主链）→ 409
- HTTP 链路抽查：真实 review 流程转换零拦截（存量转换全合法的实链证词）

**P2-20（第九轮 D 批）**：本文件原先的判据是
`if to in m.ALLOWED_TRANSITIONS.get(frm, set()): inst.assert_transition(to)` —— 期望**直接取自
被读的那张实现表**（同源判据）：删掉一条合法边，实现与用例同时"变宽松"，照样绿。
现在期望表独立声明 ⇒ ① 实现少一条边 → "合法边不抛"必红；② 实现多一条边 → "实现表 == 期望表"必红。
**扩充状态机 = 有意修改本用例**（这正是"改状态机先画矩阵"要的摩擦）。
"""

import pytest
from fastapi.testclient import TestClient

from backend.common.exceptions import ConflictError
from backend.domain.identity.models import Child, Order, RefundRequest, WithdrawalRequest
from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay

# ---------------- 独立期望表（唯一事实源；改矩阵必须先改这里） ----------------

#: Child 会员态（R-301；self-loop = 续费顺延）
CHILD_EXPECTED: set[tuple[str, str]] = {
    ("none", "observation"),
    ("none", "formal"),
    ("observation", "pending_evaluation"),
    ("observation", "formal"),
    ("observation", "expired"),
    ("observation", "withdrawn"),
    ("pending_evaluation", "formal"),
    ("pending_evaluation", "withdrawn"),
    ("formal", "formal"),  # 续费顺延
    ("formal", "expired"),
    ("formal", "withdrawn"),
    ("expired", "observation"),
    ("expired", "formal"),
    ("expired", "withdrawn"),
    ("withdrawn", "observation"),
    ("withdrawn", "formal"),
}

#: Order 主状态（WM12 线上待支付 + 人工确认；终态 cancelled 无出边）
ORDER_EXPECTED: set[tuple[str, str]] = {
    ("pending_payment", "paid"),
    ("pending_payment", "cancelled"),
    ("pending_manual_confirm", "paid"),
    ("pending_manual_confirm", "cancelled"),
    ("paid", "refunded"),
}

#: RefundRequest 七态（R-308）
REFUND_EXPECTED: set[tuple[str, str]] = {
    ("pending", "approved"),
    ("pending", "rejected"),
    ("pending", "cancelled"),
    ("approved", "processing"),
    ("processing", "refunded"),
    ("processing", "failed"),
    ("failed", "processing"),  # 失败可重试
}

#: WithdrawalRequest 六态（R-311）
WITHDRAWAL_EXPECTED: set[tuple[str, str]] = {
    ("applying", "pending_settle"),
    ("applying", "refunding"),
    ("applying", "rejected"),
    ("applying", "cancelled"),
    ("pending_settle", "refunding"),
    ("pending_settle", "completed"),
    ("pending_settle", "rejected"),
    ("refunding", "completed"),
    ("refunding", "rejected"),
    ("refunding", "pending_settle"),
}


def _matrix(model) -> set[tuple[str, str]]:
    return {(frm, to) for frm, tos in model.ALLOWED_TRANSITIONS.items() for to in tos}


def _check_matrix(model, expected: set[tuple[str, str]], name: str) -> None:
    actual = _matrix(model)
    assert actual == expected, (
        f"{name} 状态机与**独立期望表**不一致——"
        f"实现多出 {sorted(actual - expected)}；实现缺少 {sorted(expected - actual)}。"
        f"（改状态机必须同时改本文件的期望表，这是有意设计的摩擦）"
    )
    if not hasattr(model, "assert_transition"):
        return
    statuses = sorted({s for pair in expected for s in pair})
    for frm, to in sorted(expected):
        model(status=frm).assert_transition(to)  # 合法边不抛
    for frm in statuses:
        for to in statuses:
            if (frm, to) in expected:
                continue
            with pytest.raises(ConflictError):
                model(status=frm).assert_transition(to)


def test_child_member_status_matrix_matches_expectation():
    _check_matrix(Child, CHILD_EXPECTED, "Child 会员态")


def test_order_matrix_matches_expectation():
    _check_matrix(Order, ORDER_EXPECTED, "Order")


def test_refund_request_matrix_matches_expectation():
    _check_matrix(RefundRequest, REFUND_EXPECTED, "RefundRequest")


def test_withdrawal_request_matrix_matches_expectation():
    _check_matrix(WithdrawalRequest, WITHDRAWAL_EXPECTED, "WithdrawalRequest")


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
