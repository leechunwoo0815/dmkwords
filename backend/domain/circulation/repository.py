# backend/domain/circulation/repository.py — circulation 域数据访问（非锁定单表读）
"""借阅域纪律：`with_for_update` 的锁定读（借出/归还/续借的主体行锁）**不下沉**——
锁序（record → copy）是并发红线的一部分，必须留在服务层可读位置。
"""

from sqlalchemy.orm import Session

from backend.common.base_repo import BaseRepository
from backend.domain.circulation.models import BorrowRecord


class BorrowRecordRepository(BaseRepository[BorrowRecord]):
    def __init__(self, db: Session):
        super().__init__(db, BorrowRecord)

    def list_active_by_child(self, child_id: int) -> list[BorrowRecord]:
        """孩子在借/逾期记录（口径来自 `CirculationService.child_card`：
        `child_id` + `status in (active, overdue)` + `is_deleted == 0`，不排序、不限量）。

        逾期与否由调用方按 `due_at < now()` 实时判定，本方法不做过滤。
        """
        return (
            self.db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .all()
        )
