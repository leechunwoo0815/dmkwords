# tests/unit/test_audit8_a4_deposit_refund_cumulative_20261019.py — 第八轮审查 A 批：P1-2 押金退款累计闸
"""**缺陷（第八轮审查 P1-2，实测复现）**：`assert_deposit_refund_executable` 只比"点余额"，
而 `available_amount` 到**终态**才扣减 → 两张押金退款单可同时处于 `PROCESSING`，第二张同额单
**原样放行**（第八轮探针：余额 1200 + 一张在途 1200 → 第二张 1200 过闸）→ 押金余额可被打成负数。
订单侧早有同款累计闸（`assert_order_refund_executable`），押金侧一直缺。

**修复**：补"在途累计"判据 —— `本次 + 其它在途 ≤ 当前可用余额`；**只数在途**（已退的金额在终态
已从余额扣掉，再计入会跨会员周期误伤——与 `existing_dep_rr` 的历史判重同一个坑）。

**造数说明**：押金退款单只有"退会结算"与"自动重建"两个真实生产点，**没有**任何 API 能造出
"第二张在途单"（那正是缺陷本身），所以这里直插 `RefundRequest` 行——被测对象就是这道闸。
"""

from decimal import Decimal

from fastapi.testclient import TestClient

from backend.common.exceptions import ValidationError
from backend.database import get_session
from backend.domain.billing.models import Deposit
from backend.domain.identity.deposit_refund_guards import assert_deposit_refund_executable
from backend.domain.identity.models import RefundRequest
from tests.unit.test_audit4_p2_batch3_20261017 import _member_child
from tests.unit.test_wm11_tasks import _h


def _deposit_of(child_id: int) -> tuple[int, Decimal]:
    with get_session() as db:
        dep = db.query(Deposit).filter(Deposit.child_id == child_id).first()
        return dep.id, dep.available_amount


def _add_refund(deposit_id: int, child_id: int, amount: Decimal, status: str) -> int:
    with get_session() as db:
        rr = RefundRequest(
            child_id=child_id,
            kind=RefundRequest.KIND_DEPOSIT,
            deposit_id=deposit_id,
            amount=amount,
            status=status,
            reason=f"探针：{status}",
        )
        db.add(rr)
        db.commit()
        return rr.id


def _guard_raises(child_id: int, req_id: int) -> str | None:
    """对指定退款单跑执行前复核；返回错误文案（None = 放行）。"""
    with get_session() as db:
        req = db.get(RefundRequest, req_id)
        try:
            assert_deposit_refund_executable(db, req)
        except ValidationError as exc:
            return str(exc)
        return None


def test_in_flight_sibling_blocks_second_refund(client: TestClient):
    """核心守护：同一押金已有一张在途退款单时，第二张同额单必须被拒。

    RED（还原修复：只比点余额）：返回 None（放行）→ 押金余额可被打成负数。
    """
    h = _h(client)
    c, _mini = _member_child(client, h, "13800003301")
    dep_id, balance = _deposit_of(c["id"])
    assert balance == Decimal("1200.00")

    # 只数 PROCESSING（钱已交网关、余额未扣）——见守卫 docstring 的三态说明
    in_flight = _add_refund(dep_id, c["id"], balance, RefundRequest.STATUS_PROCESSING)
    second = _add_refund(dep_id, c["id"], balance, RefundRequest.STATUS_PENDING)
    assert in_flight != second

    msg = _guard_raises(c["id"], second)
    assert msg is not None, "第二张同额退款单必须被累计闸拦下"
    assert "该押金退款将超额" in msg
    assert "其它已提交网关的在途" in msg and "1200" in msg


def test_refunded_history_does_not_block_new_cycle(client: TestClient):
    """对照组（**防止修过头**）：上一周期已退的历史单不得拦住新周期的正常退款。

    这正是 `existing_dep_rr` 历史判重踩过的坑——重新入会复位押金后，历史 REFUNDED 单还在表里。
    """
    h = _h(client)
    c, _mini = _member_child(client, h, "13800003302")
    dep_id, balance = _deposit_of(c["id"])

    _add_refund(dep_id, c["id"], balance, RefundRequest.STATUS_REFUNDED)  # 上一周期已退
    current = _add_refund(dep_id, c["id"], balance, RefundRequest.STATUS_PENDING)

    assert _guard_raises(c["id"], current) is None, "历史已退单不该拦新周期退款"


def test_single_refund_passes(client: TestClient):
    """对照组：唯一一张退款单、金额=可用余额 → 放行（原行为不回退）。"""
    h = _h(client)
    c, _mini = _member_child(client, h, "13800003303")
    dep_id, balance = _deposit_of(c["id"])

    only = _add_refund(dep_id, c["id"], balance, RefundRequest.STATUS_PENDING)
    assert _guard_raises(c["id"], only) is None


def test_over_balance_still_blocked(client: TestClient):
    """原 P0-D 闸不退化：单张超过可用余额 → 仍按"余额不足"拒（文案不变）。"""
    h = _h(client)
    c, _mini = _member_child(client, h, "13800003304")
    dep_id, balance = _deposit_of(c["id"])

    over = _add_refund(dep_id, c["id"], balance + Decimal("100.00"), RefundRequest.STATUS_PENDING)
    msg = _guard_raises(c["id"], over)
    assert msg is not None and "押金可用余额不足" in msg
