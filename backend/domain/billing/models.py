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
