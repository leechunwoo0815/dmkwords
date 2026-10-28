# backend/domain/circulation/models.py — 借阅记录
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String

from backend.common.base_model import BaseModel


class BorrowRecord(BaseModel):
    """借阅记录。借阅状态与阅读进度/Quiz 状态三分离（红线：状态机三分离）。"""

    __tablename__ = "borrow_records"
    # 并发防线：with_for_update 副本行锁（MySQL 无部分索引，唯一索引会挡住历史记录复用副本）
    __table_args__ = (
        # 逾期扫描（overdue_mark / overdue_list）：WHERE status=? AND due_at < NOW()
        # （上线前审查 P1-6：原先只有 status 单列索引 → 取回全部在借行再过滤 + filesort）
        # W3-07（第十轮审查，2026-10-27）：实际业务查询是
        # `status IN ('active','overdue') AND due_at < NOW() ORDER BY due_at`——
        # IN 跨两段后索引序不再等于 due_at 全局序，**仍会 filesort**（演示库 EXPLAIN
        # 实证 `Using where; Using filesort`）。本索引只保证"status 等值 + due_at 范围"
        # 的取数效率；要消掉 filesort 得改成两条等值查询 UNION ALL 或加覆盖索引，
        # 属另行优化，不在 B5 批内动语义。
        Index("ix_borrow_records_status_due_at", "status", "due_at"),
        # P2-36（第八轮审查，2026-10-20）：借出/归还**日期**筛选原先无索引——
        # 仪表盘趋势 `WHERE is_deleted=0 AND borrowed_at >= ?` GROUP BY DATE 实测 `type=ALL`
        # 全表扫 59,899 行；借还记录按日期筛同理。索引后为 range 扫描 + 覆盖索引。
        Index("ix_borrow_records_is_deleted_borrowed_at", "is_deleted", "borrowed_at"),
        Index("ix_borrow_records_is_deleted_returned_at", "is_deleted", "returned_at"),
    )

    STATUS_ACTIVE = "active"  # 借出中
    STATUS_OVERDUE = "overdue"  # 逾期（**只由 `overdue_mark` 任务推进**；展示态按 due_at 实时判定）
    STATUS_RETURNED = "returned"  # 已还
    STATUS_LOST = "lost"  # 遗失

    #: 状态转移矩阵（第十轮 W1-4，2026-10-25）：只列**真实存在的边**（写点清单见 docs/09 第一百一十五批）。
    #: 同值写（幂等写回）由 `assert_transition` 放行，不进矩阵。
    ALLOWED_TRANSITIONS = {
        STATUS_ACTIVE: {STATUS_OVERDUE, STATUS_RETURNED, STATUS_LOST},
        # 逾期可回到 active（续借顺延到期日）
        STATUS_OVERDUE: {STATUS_ACTIVE, STATUS_RETURNED, STATUS_LOST},
        # 归还登记在**同一事务内**先写 returned 再按 condition 覆盖为 lost（`CirculationService.return_book`；
        # 只有最终值会提交）。这条边是实测出来的形状——理想写法是"只写一次"，属后续清理项。
        STATUS_RETURNED: {STATUS_LOST},
        STATUS_LOST: set(),
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

    child_id = Column(Integer, nullable=False, index=True)
    copy_id = Column(Integer, nullable=False, index=True)
    book_id = Column(Integer, nullable=False, index=True)
    borrowed_at = Column(DateTime, nullable=False, default=datetime.now)
    due_at = Column(DateTime, nullable=False, comment="到期日")
    returned_at = Column(DateTime, nullable=True)
    returned_condition = Column(
        String(20), nullable=True, comment="归还状态 normal/maintenance/lost"
    )
    status = Column(String(20), nullable=False, default=STATUS_ACTIVE, index=True)
    renew_used = Column(Integer, nullable=False, default=0, comment="已用续借次数（上限1）")
    borrowed_by = Column(Integer, nullable=True, comment="办理借书的馆员ID")
    # 归还操作人（2026-09-21 任务包 D 批，用户拍板"要归还操作人"）：此前只记借出人，
    # 还书这一步"谁办的"查不到——借还记录查询要能追到人。历史行 NULL = 未记录（如实展示"未记录"）。
    returned_by = Column(Integer, nullable=True, comment="办理归还的馆员ID（历史行 NULL=未记录）")
    override_reason = Column(String(200), nullable=True, comment="人工放行原因（异常借书留痕）")
