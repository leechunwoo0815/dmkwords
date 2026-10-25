# backend/domain/billing/models.py — 押金（R-312 状态机）
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, Numeric, String

from backend.common.base_model import BaseModel


class Deposit(BaseModel):
    """押金（按孩子独立，R-312）。"""

    __tablename__ = "deposits"

    STATUS_UNPAID = "unpaid"
    STATUS_PAID = "paid"
    STATUS_PARTIALLY_DEDUCTED = "partially_deducted"
    STATUS_FULLY_DEDUCTED = "fully_deducted"
    STATUS_REFUNDING = "refunding"
    STATUS_REFUNDED = "refunded"

    #: 状态转移矩阵（第十轮 W1-4，2026-10-25）：只列**真实存在的边**（写点清单见 docs/09 第一百一十五批）。
    #: 同值写（幂等写回）由 `assert_transition` 放行，不进矩阵。
    ALLOWED_TRANSITIONS = {
        STATUS_UNPAID: {STATUS_PAID},
        STATUS_PAID: {
            STATUS_PARTIALLY_DEDUCTED,
            STATUS_FULLY_DEDUCTED,
            STATUS_REFUNDING,
            STATUS_REFUNDED,  # 退款执行完可直接落终态（wm10._finalize_refund，实测边）
        },
        STATUS_PARTIALLY_DEDUCTED: {
            STATUS_PAID,
            STATUS_PARTIALLY_DEDUCTED,
            STATUS_FULLY_DEDUCTED,
            STATUS_REFUNDING,
            STATUS_REFUNDED,
        },
        STATUS_FULLY_DEDUCTED: {
            STATUS_PAID,
            STATUS_PARTIALLY_DEDUCTED,
            STATUS_FULLY_DEDUCTED,
            STATUS_REFUNDING,
            STATUS_REFUNDED,
        },
        # 退款在途可回退到 paid/已扣（退款单被拒或家长撤销、钱没出去）+ 完成退款到 refunded
        STATUS_REFUNDING: {
            STATUS_PAID,
            STATUS_PARTIALLY_DEDUCTED,
            STATUS_FULLY_DEDUCTED,
            STATUS_REFUNDED,
        },
        # 终态 refunded 只留一条出边：重新入会时归零重来（PRD §3.6 2026-10-17 增补）
        STATUS_REFUNDED: {STATUS_UNPAID},
    }

    def can_transition(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def assert_transition(self, new_status: str) -> None:
        """转移断言（钱/库存状态机红线：改状态先画矩阵）。非法边抛 ConflictError；同值写放行。"""
        from backend.common.exceptions import ConflictError

        if new_status == self.status:
            return
        if not self.can_transition(new_status):
            raise ConflictError(
                f"{type(self).__name__} 当前状态 {self.status} 不允许转到 {new_status}"
            )

    child_id = Column(
        Integer, unique=True, nullable=False, index=True, comment="孩子ID（一人一份）"
    )
    amount = Column(Numeric(10, 2), nullable=False, default=0, comment="押金标准额（配置值）")
    available_amount = Column(
        Numeric(10, 2), nullable=False, default=0, comment="可用余额（退会退这个）"
    )
    deducted_amount = Column(Numeric(10, 2), nullable=False, default=0, comment="累计扣除（不退）")
    supplemented_total = Column(Numeric(10, 2), nullable=False, default=0, comment="累计补缴")
    status = Column(String(24), nullable=False, default=STATUS_UNPAID, comment="押金状态")
    unpaid_balance = Column(
        Numeric(10, 2), nullable=False, default=0, comment="待结清（扣除超出部分）"
    )
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class DepositLedger(BaseModel):
    """押金流水（每笔缴纳/扣除/补缴/退款）。"""

    __tablename__ = "deposit_ledgers"

    ENTRY_PAY = "pay"  # 缴纳（首笔）
    ENTRY_DEDUCT = "deduct"  # 赔偿扣除
    ENTRY_SUPPLEMENT = "supplement"  # 补缴
    ENTRY_REFUND = "refund"  # 退款

    deposit_id = Column(Integer, nullable=False, index=True)
    entry_type = Column(String(20), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False, comment="发生额（正数；方向由类型定）")
    balance_after = Column(Numeric(10, 2), nullable=False, comment="发生后可用余额")
    reason = Column(String(200), nullable=False, default="", comment="事由（赔偿关联图书等）")
    related_copy_id = Column(Integer, nullable=True, comment="关联副本（赔偿时）")
    operator_id = Column(Integer, nullable=True, comment="操作管理员")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
