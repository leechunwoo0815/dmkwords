# backend/common/admin_notification_models.py — 管理端通知（WM13 运营审核工作台）
"""WM13 横切实体：管理待办通知（运营审核事项）。

设计说明（架构）：
- 与家长通知（notification_models.Notification）完全隔离——家长通知链路零接触（WM13 红线 1）；
- B10 模式：被 identity/activity 等业务域写入、被 admin 域查询——归 common 层；
- 显示态实时算、审计态事件写（任务包 v2 灵魂）：
  "待处理/已审结/已失效"不落库，查询时 StatusResolver 实时 JOIN 业务表判定；
  handled_at/handled_by 优先于机器推导（T20c 20260906 推翻 WM13 原口径：
  "不参与显示态"在实测中被证伪——人工标记意图应优先，标记后不再入待处理）；
- 幂等：唯一索引 (scene, ref_type, ref_id, dedup_key, is_deleted)（B11 模式，
  is_deleted 入唯一索引软删后可重建）；无 recipient 字段——全局待办墙，按查看者权限过滤（v2）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

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
        # P2-2（2026-10-18 第七十六批）：待办墙 `WHERE is_deleted=0 ORDER BY created_at DESC, id DESC`
        # 原先走唯一索引 + **全表扫 + filesort**（造量 3000 行实测）；本索引让排序走索引顺序。
        Index("ix_admin_notif_deleted_created", "is_deleted", "created_at", "id"),
    )

    SCENE_REFUND_APPLY = "admin.refund_apply"
    SCENE_REFUND_EXECUTE_FAILED = "admin.refund_execute_failed"
    SCENE_WITHDRAWAL_APPLY = "admin.withdrawal_apply"
    SCENE_TRANSFER_APPLY = "admin.transfer_apply"
    SCENE_ACTIVITY_BATCH_REFUND = "admin.activity_batch_refund"
    # T6（插修 13）：活动报名待确认（fee>0 报名→待人工收款确认，运营动作入口）
    SCENE_ACTIVITY_ENROLL_MANUAL = "admin.activity_enroll_manual"
    SCENE_TRANSFER_EXPIRING = "admin.transfer_expiring"
    # R-313：未入会临时借书（馆员放行）→ 生成「入会跟进任务」。
    # 2026-09-12 全维度审查发现该规则此前**未实现**（全仓无任何落点，BDD 场景又挂在 @draft 永不跑）。
    SCENE_MEMBER_FOLLOW_UP = "admin.member_follow_up"
    # 2026-10-12 拍板（P4/P5/P10）：活动取消后的订单作废、副本报损赔偿、资金异常
    SCENE_ACTIVITY_ORDER_VOIDED = "admin.activity_order_voided"
    SCENE_COPY_DAMAGE_COMPENSATION = "admin.copy_damage_compensation"
    SCENE_PAYMENT_ANOMALY = "admin.payment_anomaly"
    # G9（2026-10-13 第五十五轮）：PRD §7.4「每达成一个节点…**同时通知家长和馆员**」
    # 的馆员侧通道。它是**知会**（线下表扬/朋友圈/颁奖候选），不是待审事项：
    # 一个节点一条（dedup_key=节点词数）、按孩子去重；显示态固定"已达成·可线下表扬"，
    # **不计入待办数**（待办数只统计需要人工审结的申请类事项）。
    SCENE_MILESTONE_ACHIEVED = "admin.milestone_achieved"
    # G12（2026-10-14 第五十九批）：PRD §3.3「系统每周自动整理一份"待评估超过 7 天"的孩子名单
    # **提醒馆员跟进**」——原先只返回一个计数（名单没落到任何人看得到的地方）。现在逐人写待办，
    # 显示态按会员状态推导（转正/退会即自动审结），dedup_key=ISO 周（同一周只提醒一次）。
    SCENE_PENDING_EVALUATION = "admin.pending_evaluation"
    # G1（2026-10-14 第五十九批）：PRD §3.6「押金不够扣…差额记为"待结清"，**系统持续提醒馆员**」——
    # 原先只有"随时可见 + 操作时被拦"（PRD 该行 2026-10-13 已如实标注缺口）。现在逐人写待办，
    # 显示态按押金未结清余额推导（结清即自动审结），dedup_key=ISO 周（持续提醒 = 每周一次）。
    SCENE_DEPOSIT_SETTLE = "admin.deposit_settle"
    # G5（2026-10-14 第六十轮）：PRD §5.5「预约期间书出意外…转『异常』状态并**通知家长和馆员**」的
    # 馆员侧通道。它是**要人做事的待办**（联系家长改约/补书），不是知会——故进待办墙默认名单、
    # 计入 admin_total；显示态按预约行实时推导（仍是 exception → 待处理；单据推进/消失 → 自动审结）。
    SCENE_RESERVATION_EXCEPTION = "admin.reservation_exception"
    # P2-28（2026-10-17 第六十九批·大审查）：审核超时提醒此前**只有转让一条链**
    # （SCENE_TRANSFER_EXPIRING），退款/退会两条只有配置中心两个零读取方的死键。
    # 现按转让同款补齐：`ref_type` 复用 `refund_request` / `withdrawal_request` →
    # 显示态直接复用既有 resolver（单据仍 pending → 待处理；审结/撤销 → 已审结/已失效），
    # 单据动线推进后提醒**自动归零**、不会变孤儿。dedup 默认值"1"（唯一索引含 ref_id）
    # = **每单一条**，不随每次扫描刷屏。
    SCENE_REFUND_REVIEW_TIMEOUT = "admin.refund_review_timeout"
    SCENE_WITHDRAWAL_REVIEW_TIMEOUT = "admin.withdrawal_review_timeout"

    # ref_type 取值
    REF_REFUND_REQUEST = "refund_request"
    REF_WITHDRAWAL_REQUEST = "withdrawal_request"
    REF_TRANSFER = "transfer"
    REF_ACTIVITY = "activity"
    REF_CHILD = "child"
    REF_RESERVATION = "reservation"

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
    handled_at = Column(
        DateTime, nullable=True, comment="人工标记处理时间（T20c：优先于机器推导显示态）"
    )
    handled_by = Column(
        BigInteger, nullable=True, comment="审计：处理管理员ID（展示时 JOIN AdminUser 取名）"
    )
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment="通知创建时间")

    # 审计留痕扩展说明（批次二 handle 端点用，S4：手动标记已处理必须填原因）
    extra = Column(Text, nullable=True, comment="扩展JSON（手动处理原因等审计留痕）")
