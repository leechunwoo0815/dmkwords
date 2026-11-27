# backend/domain/reading_circle/repository.py — reading_circle 域数据访问（单表读）
from sqlalchemy.orm import Session

from backend.common.base_repo import BaseRepository
from backend.domain.reading_circle.models import CirclePost


class CirclePostRepository(BaseRepository[CirclePost]):
    def __init__(self, db: Session):
        super().__init__(db, CirclePost)

    def list_by_child(self, child_id: int) -> list[CirclePost]:
        """孩子发过的全部帖（口径来自 `CircleService.my_cards`：`child_id` + `is_deleted == 0`，
        不排序、不限量——调用方只取 `(card_type, ref_id)` 集合做"已晒/未晒"分组）。
        """
        return (
            self.db.query(CirclePost)
            .filter(CirclePost.child_id == child_id, CirclePost.is_deleted == 0)
            .all()
        )
