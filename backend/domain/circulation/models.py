# backend/domain/circulation/models.py — 借阅记录
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from backend.common.base_model import BaseModel


class BorrowRecord(BaseModel):
    """借阅记录。借阅状态与阅读进度/Quiz 状态三分离（红线：状态机三分离）。"""

    __tablename__ = "borrow_records"
    # 并发防线：with_for_update 副本行锁（MySQL 无部分索引，唯一索引会挡住历史记录复用副本）

    STATUS_ACTIVE = "active"  # 借出中
    STATUS_OVERDUE = "overdue"  # 逾期（由任务/查询时判定）
    STATUS_RETURNED = "returned"  # 已还
    STATUS_LOST = "lost"  # 遗失

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
    override_reason = Column(String(200), nullable=True, comment="人工放行原因（异常借书留痕）")
