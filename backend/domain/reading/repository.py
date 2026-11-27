# backend/domain/reading/repository.py — reading 域数据访问（非锁定单表读）
"""阅读域纪律：播放心跳的**锁定读 + 区间合并覆盖写**（`ReadingService.report_progress`
的 `with_for_update` 双段）留在服务层；这里只收无锁普通 SELECT。
"""

from sqlalchemy.orm import Session

from backend.common.base_repo import BaseRepository
from backend.domain.reading.models import ReadingProgress


class ReadingProgressRepository(BaseRepository[ReadingProgress]):
    def __init__(self, db: Session):
        super().__init__(db, ReadingProgress)

    def get_by_child_book(self, child_id: int, book_id: int) -> ReadingProgress | None:
        """孩子对某书的进度行（非锁定读；口径来自 `ReadingService.get_progress`：
        `child_id` + `book_id` + `is_deleted == 0`）。

        注意：心跳上报那条链路要锁行（`report_progress`），**不走这里**。
        """
        return (
            self.db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child_id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
