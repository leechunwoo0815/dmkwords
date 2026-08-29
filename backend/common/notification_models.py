# backend/common/notification_models.py — 通知中心 / 任务运行日志 / 事件死信
"""WM11 横切实体：站内消息（必达）+ 微信订阅（尽力）+ 任务日志 + 死信。

设计说明（架构）：
- 通知要被各业务域（事件订阅器）写入、被 admin 域查询、被 miniapp 读——归 common 层
  （同 AuditLog/SystemConfig 先例，架构关零违规）；
- 幂等：唯一索引 (parent_id, scene, ref_type, ref_id, dedup_key, is_deleted)，
  定时提醒场景 dedup_key=提醒节点值（如 "30"），事件场景 dedup_key="1"；
- 软删除后允许重建（唯一索引含 is_deleted，同 orders 模式）。
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from backend.common.base_model import BaseModel


class Notification(BaseModel):
    """站内消息（保底通道必达）+ 微信订阅发送记录。"""

    __tablename__ = "notifications"
    __table_args__ = (
        # 幂等去重：同一家长×同一场景×同一业务对象×同一去重键只发一次
        # is_deleted 入唯一索引，软删后可重建（同 orders.uq_order_no）
        UniqueConstraint(
            "parent_id",
            "scene",
            "ref_type",
            "ref_id",
            "dedup_key",
            "is_deleted",
            name="uq_notif_dedup",
        ),
    )

    # 场景分类（PRD §十 8 类）
    CATEGORY_MONEY = "资金"
    CATEGORY_BORROW = "借阅"
    CATEGORY_READING = "阅读"
    CATEGORY_MEMBER = "会员"
    CATEGORY_ACTIVITY = "活动"
    CATEGORY_RESERVATION = "预约"
    CATEGORY_REPORT = "报告"
    CATEGORY_OTHER = "其他"

    # 微信通道状态
    WECHAT_NONE = "none"  # 未启用/未发送
    WECHAT_SENT = "sent"  # 已送达
    WECHAT_FAILED = "failed"  # 发送失败（记录原因）
    WECHAT_SKIPPED = "skipped"  # 主动跳过（未授权/额度不足/模板缺失/通道未启用）

    parent_id = Column(Integer, nullable=False, index=True, comment="接收家长ID")
    child_id = Column(Integer, nullable=True, index=True, comment="关联孩子ID（可为空）")
    scene = Column(String(64), nullable=False, index=True, comment="场景标识（如 borrow.success）")
    category = Column(String(24), nullable=False, default="", comment="分类（资金/借阅/阅读/…）")
    title = Column(String(100), nullable=False, comment="中文标题")
    content = Column(String(500), nullable=False, default="", comment="中文内容")
    ref_type = Column(String(32), nullable=False, default="", comment="业务对象类型")
    ref_id = Column(String(64), nullable=False, default="", comment="业务对象ID")
    dedup_key = Column(String(64), nullable=False, default="", comment="去重键（提醒节点/固定1）")
    read_at = Column(DateTime, nullable=True, comment="家长已读时间")
    wechat_status = Column(String(16), nullable=False, default=WECHAT_NONE, comment="微信通道状态")
    wechat_error = Column(String(500), nullable=False, default="", comment="微信发送失败/跳过原因")

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class TaskRunLog(BaseModel):
    """定时任务运行记录（管理端任务看板）。"""

    __tablename__ = "task_run_logs"

    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"

    task_name = Column(String(64), nullable=False, index=True, comment="任务标识")
    started_at = Column(DateTime, nullable=False, default=datetime.now, comment="开始时间")
    finished_at = Column(DateTime, nullable=True, comment="结束时间")
    status = Column(String(16), nullable=False, default=STATUS_RUNNING, comment="结果状态")
    processed = Column(Integer, nullable=False, default=0, comment="处理条数")
    error = Column(Text, nullable=True, comment="失败原因")


class DeadLetter(BaseModel):
    """事件死信落库（D6 正式形态；此前仅结构化日志）。"""

    __tablename__ = "dead_letters"

    event_type = Column(String(64), nullable=False, index=True, comment="事件类型")
    handler_name = Column(String(100), nullable=False, comment="处理器名")
    payload = Column(Text, nullable=True, comment="事件负载JSON")
    error = Column(Text, nullable=True, comment="失败原因")
    retry_count = Column(Integer, nullable=False, default=0, comment="重试次数")
