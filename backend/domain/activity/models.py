# backend/domain/activity/models.py — 线下活动 / 报名（9 态缩减为实际 6 态）/ 入场券
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, Numeric, SmallInteger, String, Text

from backend.common.base_model import BaseModel


class Activity(BaseModel):
    """活动发布（FEAT-057：类型/时间/地点/名额/费用/限制/截止）。"""

    __tablename__ = "activities"

    STATUS_PUBLISHED = "published"
    STATUS_CANCELLED = "cancelled"
    STATUS_FINISHED = "finished"

    TYPE_OPTIONS = (
        "lecture",
        "book_club",
        "experience_sharing",
        "award_ceremony",
        "theme_reading",
        "parent_child",
    )

    title = Column(String(120), nullable=False, comment="活动名称")
    activity_type = Column(String(30), nullable=False, default="book_club", comment="类型")
    start_at = Column(DateTime, nullable=False, comment="开始时间")
    location = Column(String(200), nullable=False, default="", comment="地点")
    max_quota = Column(Integer, nullable=False, default=0, comment="最大报名人数")
    fee = Column(Numeric(10, 2), nullable=False, default=0, comment="报名费用（0=免费）")
    description = Column(Text, nullable=True, comment="活动介绍")
    cover_path = Column(String(255), nullable=True, comment="封面图")
    member_only = Column(SmallInteger, nullable=False, default=0, comment="1=仅会员")
    enroll_deadline = Column(DateTime, nullable=True, comment="报名截止")
    status = Column(String(20), nullable=False, default=STATUS_PUBLISHED, index=True)


class ActivityEnrollment(BaseModel):
    """活动报名（FEAT-058：每孩子单独报名占名额；待支付先占名额；入场券）。

    状态机：pending_payment → enrolled → checked_in；
            enrolled → refund_pending → refunded / enrolled（拒绝恢复）；
            pending_payment/enrolled → cancelled；活动取消 → 批量 refund_pending。
    """

    __tablename__ = "activity_enrollments"
    __table_args__ = (Index("uq_enroll_active_child", "activity_id", "child_id"),)

    STATUS_PENDING_PAYMENT = "pending_payment"
    STATUS_ENROLLED = "enrolled"
    STATUS_CHECKED_IN = "checked_in"
    STATUS_CANCELLED = "cancelled"
    STATUS_REFUND_PENDING = "refund_pending"
    STATUS_REFUNDED = "refunded"

    ACTIVE_STATUSES = (
        STATUS_PENDING_PAYMENT,
        STATUS_ENROLLED,
        STATUS_CHECKED_IN,
        STATUS_REFUND_PENDING,
    )

    activity_id = Column(Integer, nullable=False, index=True)
    child_id = Column(Integer, nullable=False, index=True)
    order_id = Column(Integer, nullable=True, comment="收费活动关联订单")
    ticket_code = Column(String(32), nullable=False, unique=True, comment="入场券码（签到用）")
    status = Column(String(24), nullable=False, default=STATUS_ENROLLED, index=True)
    checked_in_at = Column(DateTime, nullable=True, comment="签到时间")
    checked_in_by = Column(Integer, nullable=True, comment="签到操作管理员")
    cancel_reason = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
