# backend/domain/admin/media_models.py — 媒体体检（孤儿图盘点）与回收站
"""admin 域媒体表（2026-09-23，规范 `docs/15 §二十二`）：

表：
  media_censuses      每次"点数"的结果快照（孤儿张数/字节/按目录明细/悬空引用）
  media_trash_entries 回收站条目（**只记"从哪来"**；文件本体在 uploads/.trash/<批次>/）

为什么单独建表而不是复用 system_configs：运营要看的是**趋势**（"数字是不是悄悄涨了"），
单值配置只能存"当前"，历史一问就没了；且审计要能回答"这批图是谁、什么时候、为什么清的"。
"""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text

from backend.common.base_model import BaseModel


class MediaCensus(BaseModel):
    """一次媒体盘点的结果快照（**只读报告**，本身不改任何文件）。"""

    __tablename__ = "media_censuses"

    #: 触发来源：定时任务 / 手动点数 / 清理后刷新 / 还原后刷新
    TRIGGER_SCHEDULED = "scheduled"
    TRIGGER_MANUAL = "manual"
    TRIGGER_AFTER_TRASH = "after_trash"
    TRIGGER_AFTER_RESTORE = "after_restore"

    trigger = Column(
        String(16),
        nullable=False,
        default=TRIGGER_MANUAL,
        comment="触发来源（scheduled/manual/after_trash/after_restore）",
    )
    files_total = Column(
        Integer, nullable=False, default=0, comment="uploads 文件总数（不含回收站）"
    )
    referenced_total = Column(Integer, nullable=False, default=0, comment="DB 引用路径数")
    protected_total = Column(Integer, nullable=False, default=0, comment="保护目录跳过数")
    orphan_files = Column(Integer, nullable=False, default=0, comment="孤儿文件数（可清理）")
    orphan_bytes = Column(BigInteger, nullable=False, default=0, comment="孤儿字节数")
    missing_refs = Column(Integer, nullable=False, default=0, comment="引用存在但磁盘缺失数")
    breakdown = Column(Text, nullable=True, comment="按目录明细 JSON: [{bucket,files,bytes}]")


class MediaTrashEntry(BaseModel):
    """回收站条目：文件已移到 `uploads/.trash/<batch>/<rel_path>`，原路径腾空。"""

    __tablename__ = "media_trash_entries"

    STATE_TRASHED = "trashed"
    STATE_RESTORED = "restored"
    STATE_PURGED = "purged"

    rel_path = Column(String(512), nullable=False, index=True, comment="uploads 下原相对路径")
    bucket = Column(String(32), nullable=False, default="", comment="一级目录（统计用）")
    bytes = Column(BigInteger, nullable=False, default=0, comment="文件字节")
    batch = Column(String(32), nullable=False, default="", comment="批次目录名（时间戳）")
    state = Column(
        String(16), nullable=False, default=STATE_TRASHED, comment="trashed/restored/purged"
    )
    actor_id = Column(Integer, nullable=True, comment="操作用户 id")
    actor_name = Column(String(64), nullable=False, default="", comment="操作用户显示名")
    restore_until = Column(DateTime, nullable=True, comment="保留截止（到期复检后再清除）")
    restored_at = Column(DateTime, nullable=True, comment="还原时间")
    restored_by = Column(Integer, nullable=True, comment="还原人 id")
