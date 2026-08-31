# backend/common/admin_notification_models.py — 管理端通知（WM13 运营审核工作台）
"""WM13 横切实体：管理待办通知（运营审核事项）。

设计说明（架构）：
- 与家长通知（notification_models.Notification）完全隔离——家长通知链路零接触（WM13 红线 1）；
- B10 模式：被 identity/activity 等业务域写入、被 admin 域查询——归 common 层；
- 显示态实时算、审计态事件写（任务包 v2 灵魂）：
  "待处理/已审结/已失效"不落库，查询时 StatusResolver 实时 JOIN 业务表判定；
  handled_at/handled_by 只作审计展示，不参与显示态判定；
- 幂等：唯一索引 (scene, ref_type, ref_id, dedup_key, is_deleted)（B11 模式，
  is_deleted 入唯一索引软删后可重建）；无 recipient 字段——全局待办墙，按查看者权限过滤（v2）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Numeric, String, Text, UniqueConstraint

from backend.common.base_model import BaseModel


class AdminNotification(BaseModel):
    """管理端待办通知（审核事项级：一条申请一条通知）。"""

    __tablename__ = "admin_notifications"
    __table_args__ = (
        UniqueConstraint(
            "scene",
            "ref_type",
            "ref_id",
            "dedup_key",
            "is_deleted",
            name="uq_admin_notif_dedup",
        ),
    )

    SCENE_REFUND_APPLY = "admin.refund_apply"
    SCENE_REFUND_EXECUTE_FAILED = "admin.refund_execute_failed"
    SCENE_WITHDRAWAL_APPLY = "admin.withdrawal_apply"
    SCENE_TRANSFER_APPLY = "admin.transfer_apply"
    SCENE_ACTIVITY_BATCH_REFUND = "admin.activity_batch_refund"
    SCENE_TRANSFER_EXPIRING = "admin.transfer_expiring"

    # ref_type 取值
    REF_REFUND_REQUEST = "refund_request"
    REF_WITHDRAWAL_REQUEST = "withdrawal_request"
    REF_TRANSFER = "transfer"
    REF_ACTIVITY = "activity"

    scene = Column(String(64), nullable=False, index=True, comment="场景标识（admin.*）")
    title = Column(String(100), nullable=False, comment="事项标题")
    content = Column(String(500), nullable=False, default="", comment="事项内容（含申请原因原文）")
    ref_type = Column(String(32), nullable=False, default="", comment="业务对象类型")
    ref_id = Column(String(64), nullable=False, default="", comment="业务对象ID")
    applicant_name = Column(
        String(128), nullable=False, default="", comment="申请人（家长名·孩子名/活动名）"
    )
    amount = Column(Numeric(10, 2), nullable=True, comment="涉及金额（可空）")
    dedup_key = Column(String(64), nullable=False, default="1", comment="去重键（固定1）")
    handled_at = Column(DateTime, nullable=True, comment="审计：处理时间（不参与显示态）")
    handled_by = Column(
        BigInteger, nullable=True, comment="审计：处理管理员ID（展示时 JOIN AdminUser 取名）"
    )
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment="通知创建时间")

    # 审计留痕扩展说明（批次二 handle 端点用，S4：手动标记已处理必须填原因）
    extra = Column(Text, nullable=True, comment="扩展JSON（手动处理原因等审计留痕）")
