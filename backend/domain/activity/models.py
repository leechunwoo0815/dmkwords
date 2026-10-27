# backend/domain/activity/models.py — 线下活动 / 报名（9 态缩减为实际 6 态）/ 入场券
from datetime import datetime

from sqlalchemy import (
    Column,
    Computed,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)

from backend.common.base_model import BaseModel


class Activity(BaseModel):
    """活动发布（FEAT-057：类型/时间/地点/名额/费用/限制/截止）。"""

    __tablename__ = "activities"

    STATUS_PUBLISHED = "published"
    STATUS_CANCELLED = "cancelled"
    STATUS_FINISHED = "finished"

    #: 状态转移矩阵（第十轮 W1-4，2026-10-25）：published 是初始态；取消/结束都是终态。
    #: 同值写（幂等写回）由 `assert_transition` 放行，不进矩阵。
    ALLOWED_TRANSITIONS = {
        STATUS_PUBLISHED: {STATUS_CANCELLED, STATUS_FINISHED},
        STATUS_CANCELLED: set(),
        STATUS_FINISHED: set(),
    }

    def can_transition(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def assert_transition(self, new_status: str) -> None:
        """转移断言（红线：改状态机先画矩阵）。非法边抛 ConflictError；同值写放行。"""
        from backend.common.exceptions import ConflictError

        if new_status == self.status:
            return
        if not self.can_transition(new_status):
            raise ConflictError(
                f"{type(self).__name__} 当前状态 {self.status} 不允许转到 {new_status}"
            )

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
    end_at = Column(
        DateTime,
        nullable=True,
        comment="结束时间（签到窗=开始前60分钟~结束后30分钟；历史行为空时按开始+3h）",
    )
    location = Column(String(200), nullable=False, default="", comment="地点")
    max_quota = Column(Integer, nullable=False, default=0, comment="最大报名人数")
    fee = Column(Numeric(10, 2), nullable=False, default=0, comment="报名费用（0=免费）")
    description = Column(Text, nullable=True, comment="活动介绍")
    cover_path = Column(String(255), nullable=True, comment="封面图")
    member_only = Column(
        SmallInteger,
        nullable=False,
        default=1,
        comment="1=仅会员（默认开启；首场/开放日显式关）",
    )
    enroll_deadline = Column(DateTime, nullable=True, comment="报名截止")
    status = Column(String(20), nullable=False, default=STATUS_PUBLISHED, index=True)
    # 图文详情块（2026-09-20 客户需求「像公众号一样」）：JSON 数组，块类型仅 paragraph / image。
    # 存 JSON 文本而非建子表：块是**整体读写的展示快照**，没有独立查询/排序需求（同 card_data 先例）。
    detail_blocks = Column(Text, nullable=True, comment="图文详情块（JSON 数组）")


#: 活跃报名唯一键的**生成表达式**（条件唯一约束用）。
#:
#: 第八轮审查 P2-9（2026-10-20）：`uq_enroll_active_child` 名字像唯一索引，实际
#: `unique=False`（`activity/models.py:52` 与迁移 `c9d3a6b8e2f1` 都写死了非唯一）——
#: PRD §9.2「同一活动同一孩子只能有一个有效报名」全靠应用层"活动行锁 + 先查后插"，
#: 漏锁的写口（管理端造单）并发即可造出两条活跃报名。
#:
#: MySQL 没有部分索引，故把"活跃态"编码进一个**生成列**：活跃 → `活动id-孩子id`，
#: 其余（取消/退款/软删）→ NULL（唯一索引不比较 NULL）→ 同一孩子可**重新报名**。
#: 状态清单必须与 `ACTIVE_STATUSES` 逐字一致：`tests/unit/test_audit8_a8_enroll_unique_20261020.py`
#: 直接从 `information_schema` 读回真实表达式与 `ACTIVE_STATUSES` 比对，改一处忘另一处即红。
ACTIVE_UNIQ_KEY_SQL = (
    "IF(is_deleted = 0 AND status IN "
    "('pending_payment','enrolled','checked_in','refund_pending'),"
    " CONCAT(activity_id, '-', child_id), NULL)"
)


class ActivityEnrollment(BaseModel):
    """活动报名（FEAT-058：每孩子单独报名占名额；待支付先占名额；入场券）。

    状态机：pending_payment → enrolled → checked_in；
            enrolled → refund_pending → refunded / enrolled（拒绝恢复）；
            pending_payment/enrolled → cancelled；活动取消 → 批量 refund_pending。
    """

    __tablename__ = "activity_enrollments"
    # P2-9：唯一约束落在生成列 `active_uniq_key` 上（只约束活跃四态），索引名沿用旧名。
    __table_args__ = (Index("uq_enroll_active_child", "active_uniq_key", unique=True),)

    STATUS_PENDING_PAYMENT = "pending_payment"
    STATUS_ENROLLED = "enrolled"
    STATUS_CHECKED_IN = "checked_in"
    STATUS_CANCELLED = "cancelled"
    STATUS_REFUND_PENDING = "refund_pending"
    STATUS_REFUNDED = "refunded"

    #: 状态转移矩阵（第十轮 W1-4，2026-10-25）：只列**真实存在的边**（写点清单见 docs/09 第一百一十五批）。
    #: 同值写（幂等写回）由 `assert_transition` 放行，不进矩阵。
    ALLOWED_TRANSITIONS = {
        STATUS_PENDING_PAYMENT: {STATUS_ENROLLED, STATUS_CANCELLED},
        STATUS_ENROLLED: {
            STATUS_CHECKED_IN,
            STATUS_CANCELLED,
            STATUS_REFUND_PENDING,
            STATUS_REFUNDED,  # 退款执行完直接落终态（wm10._refund_activity_enrollment，实测边）
        },
        STATUS_CHECKED_IN: {STATUS_CANCELLED, STATUS_REFUND_PENDING, STATUS_REFUNDED},
        # 退款被拒 → 退回 enrolled（人还在名单里）；退款执行完 → refunded
        STATUS_REFUND_PENDING: {STATUS_ENROLLED, STATUS_REFUNDED},
        STATUS_CANCELLED: set(),
        STATUS_REFUNDED: set(),
    }

    def can_transition(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def assert_transition(self, new_status: str) -> None:
        """转移断言（红线：改状态机先画矩阵）。非法边抛 ConflictError；同值写放行。"""
        from backend.common.exceptions import ConflictError

        if new_status == self.status:
            return
        if not self.can_transition(new_status):
            raise ConflictError(
                f"{type(self).__name__} 当前状态 {self.status} 不允许转到 {new_status}"
            )

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
    # 条件唯一键（DB 生成，应用只读；表达式见模块级 ACTIVE_UNIQ_KEY_SQL）
    active_uniq_key = Column(
        String(40),
        Computed(ACTIVE_UNIQ_KEY_SQL, persisted=True),
        nullable=True,
        comment="活跃报名唯一键（生成列：活跃态=活动-孩子，其余 NULL）",
    )
