# backend/domain/circulation/borrow_gate.py — 借书资格判定（单一来源，2026-09-21）
"""用户反馈：「未入会的会员，在后端竟然显示可以借 30 本，神奇啊。」

卡面要显示"能不能借、为什么不能"，借书守卫要拦截同一件事——**这两处必须同一判定**，
否则迟早漂移（错误库 §八十六就是"同一个数字被写两遍"的教训；这里换成"同一个资格判两遍"）。

所以把**会员状态段**与**押金段**拆成两个纯函数：
- `CirculationService.borrow()` 按原顺序调用它们（行为、文案一字不改）；
- `child_card()` 调同样的函数得出"卡面为什么不能借"。

额度（30 − 在借 − 预约）与副本状态不在这里判——那两项要按**使用时点**的最新数据算，仍在 borrow() 里查。
"""

from __future__ import annotations

from typing import NamedTuple

# 拦截类型：硬拦截（放行也没用，如退会/开关未开）与可放行（填原因即可，如押金、会员过期）
HARD = "hard"
OVERRIDABLE = "override"


class GateResult(NamedTuple):
    code: str  # "" = 通过；否则为拦截类别（withdrawn/unpaid/expired/deposit_*）
    reason: str  # 中文原因（**报错与卡面提示共用这一句**）
    kind: str  # HARD / OVERRIDABLE


_PASS = GateResult("", "", "")


def member_gate(
    *,
    member_status: str,
    is_active_member: bool,
    allow_unpaid: bool,
    override_reason: str | None,
    held: int,
    withdrawn_status: str,
    none_status: str,
) -> GateResult:
    """会员状态段（R-313 借书矩阵行）：退会=禁；未入会=开关+放行+限 1 本；过期=可放行。

    `withdrawn_status` / `none_status` 由调用方传 `Child.MEMBER_*`，避免本模块反向依赖 identity 域常量。
    """
    if is_active_member:
        return _PASS
    if member_status == withdrawn_status:
        return GateResult("withdrawn", "孩子已退会，禁止借书（R-313）", HARD)
    if member_status == none_status:
        if not allow_unpaid:
            return GateResult(
                "unpaid",
                f"孩子会员状态为 {member_status}，未入会临时借书开关未开启",
                HARD,
            )
        # 先查"已借满"：放行也借不了第二本（HARD）——比"请填原因"更贴切，卡面提示也更准
        if held >= 1:
            return GateResult(
                "unpaid",
                f"未入会临时借书每次限 1 本（当前已借 {held} 本未还），请先归还或办理入会（R-313）",
                HARD,
            )
        if not override_reason:
            return GateResult(
                "unpaid",
                f"孩子会员状态为 {member_status}，未入会借书需管理员放行并填写原因",
                OVERRIDABLE,
            )
        return GateResult("unpaid", "", OVERRIDABLE)  # 放行通过 → 借期 72h
    # 过期（expired 或 formal 已到期未落库）：软提示，馆员放行即可
    if not override_reason:
        return GateResult("expired", "孩子会员已过期，需馆员放行并填写原因（可放行）", OVERRIDABLE)
    return GateResult("expired", "", OVERRIDABLE)


def deposit_gate(
    *,
    deposit_status: str | None,
    deposit_unpaid_balance: int,
    override_reason: str | None,
    unpaid_status: str,
    fully_deducted_status: str,
) -> GateResult:
    """押金段（B-12/T12）：未缴/扣光/未结清赔偿款同险，可人工放行留痕。"""
    if (
        deposit_status is None
        or deposit_status == unpaid_status
        or deposit_status == fully_deducted_status
        or deposit_unpaid_balance > 0
    ):
        if deposit_status is None or deposit_status == unpaid_status:
            block_reason = "押金未缴纳"
        elif deposit_status == fully_deducted_status:
            block_reason = "押金已扣光"
        else:
            block_reason = "有未结清赔偿款"
        if not override_reason:
            return GateResult(
                f"deposit_{block_reason}", f"{block_reason}（可人工放行并填写原因）", OVERRIDABLE
            )
        return GateResult(f"deposit_{block_reason}", "", OVERRIDABLE)
    return _PASS


def first_block(*results: GateResult) -> GateResult | None:
    """卡面用：按 borrow() 的判定顺序取第一条拦截（没有则 None=可借）。"""
    for r in results:
        if r.code:
            return r
    return None
