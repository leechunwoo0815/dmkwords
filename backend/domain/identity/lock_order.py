# backend/domain/identity/lock_order.py — 资金/身份链路**全局锁序**（单一事实源，2026-11-27 B1-1/B1-2）
"""为什么单开一个模块：锁序是**跨文件**的约定（退款执行、退款审核、退款申请、退会审核/撤销、
聚合推进、网关回调、订单结算），写在哪一个文件里都会漂移；写在这里，谁加锁谁引用。

## 全局锁序（不可违反）

    Child → Order → RefundRequest → Withdrawal → Deposit

规则：
1. **只许沿这个方向加锁**。需要"先拿到的键"时（比如只知道退款单 id 而不知道该锁哪个孩子/订单），
   先做一次**无锁**定位读拿到 `child_id/order_id`，再按锁序逐级加锁，最后用
   `with_for_update().populate_existing()` **锁定读刷新**该实体（否则 ORM 身份映射里还是旧快照）。
2. `RefundRequest` 与 `Withdrawal` 的**相对**顺序固定为 `RefundRequest → Withdrawal`
   （`_advance_withdrawal` 在持有退款单锁的事务里推进退会单；反向的"先锁退会单再锁退款单"
   在仓内不存在，也不要写）。
3. `Deposit` **永远最后**：押金余额的读-改-写必须在最后一级锁内完成。
4. **不在本锁序内**的行（`Activity` / `ActivityEnrollment` / `BorrowRecord` / `Reservation`…）按各自域
   已有的锁序（如活动域 `Activity → Enrollment → Order`）。两域交叉处仍有**已知**的环
   （`test_b1_b5_concurrency_20261031.py::test_b2_cancel_and_confirm_lock_ring_deadlocks` 实证
   "恰一方 1213"；docs/09 第一百五十一批裁定不统一锁序、三条失败路径全部可见），本模块不改变那一结论。

## 为什么必须统一（B1-1/B1-2 的事故面）

- 订单退款执行原先是 `RefundRequest →（普通读）Order`，而结算/确认收款/退款申请是
  `Order → RefundRequest`（`OrderService` 锁订单后建单）——只要执行侧也去锁订单行就会成环（1213），
  所以历史上**干脆不锁订单行**，代价是同一订单的两笔退款可以并发穿透累计闸（重复出款）。
  统一到 `Child → Order → RefundRequest` 后，订单行锁可以安全地作为"同一订单退款串行化点"。
- 退会侧原先是 `WithdrawalRequest → Child`，与家长侧申请（`Child → 建退会单`）方向相反；
  同理会成环，而"两边都只做普通读判重"的直接后果是**两张活跃退会单**（押金退两遍）。

## 本模块只提供"加锁原语"，不做业务判断

`populate_existing()` 是硬要求：SQLAlchemy 默认在身份映射里命中旧对象就直接返回，
锁定读拿到的**最新已提交值**会被丢掉——那样等于白锁（历史事故：P1-F1/B-14）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def lock_child(db: Session, child_id: int):
    """第一级：锁孩子主体行（并发退款/退会/借还/收款全部先抢它）。"""
    from backend.domain.identity.models import Child

    return (
        db.query(Child).filter(Child.id == child_id).with_for_update().populate_existing().first()
    )


def lock_order(db: Session, order_id: int):
    """第二级：锁订单行。**同一订单的退款执行以此串行化**（B1-1）。"""
    from backend.domain.identity.models import Order

    return (
        db.query(Order).filter(Order.id == order_id).with_for_update().populate_existing().first()
    )


def lock_refund_request(db: Session, request_id: int):
    """第三级：锁退款单行（状态机的判定与翻转都在锁内）。"""
    from backend.domain.identity.models import RefundRequest

    return (
        db.query(RefundRequest)
        .filter(RefundRequest.id == request_id, RefundRequest.is_deleted == 0)
        .with_for_update()
        .populate_existing()
        .first()
    )


def lock_withdrawal(db: Session, withdrawal_id: int):
    """第四级（相对退款单）：锁退会单行（审核/撤销/聚合推进共用）。

    不带 `is_deleted` 过滤：聚合推进必须能"看见"它原本就能看见的那一行，
    加过滤等于把推进路径的行为改了一半（软删在退会单上无写口，不存在可过滤的数据）。
    """
    from backend.domain.identity.models import WithdrawalRequest

    return (
        db.query(WithdrawalRequest)
        .filter(WithdrawalRequest.id == withdrawal_id)
        .with_for_update()
        .populate_existing()
        .first()
    )


def lock_refund_head(db: Session, request_id: int):
    """**无锁**定位读：只取加锁所需的键（child_id / kind / order_id），不得据此做业务判断。

    注意：这一步拿到的值**可能是旧快照**，只允许用来决定"该锁哪几行"。
    """
    from backend.domain.identity.models import RefundRequest

    return (
        db.query(
            RefundRequest.id,
            RefundRequest.kind,
            RefundRequest.order_id,
            RefundRequest.deposit_id,
            RefundRequest.child_id,
        )
        .filter(RefundRequest.id == request_id, RefundRequest.is_deleted == 0)
        .first()
    )
