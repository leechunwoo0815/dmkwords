# backend/domain/growth/repository.py — growth 域数据访问（非锁定单表读）
"""成长域纪律：积分/词数等**读改写**路径（`_add_points` 的原子自增、`ensure_state` 的
SAVEPOINT 兜底）留在服务层；这里只收普通 SELECT。
"""

from sqlalchemy.orm import Session

from backend.common.base_repo import BaseRepository
from backend.domain.growth.models import ChildGrowthState, PointLedger


class ChildGrowthStateRepository(BaseRepository[ChildGrowthState]):
    def __init__(self, db: Session):
        super().__init__(db, ChildGrowthState)

    def get_by_child(self, child_id: int) -> ChildGrowthState | None:
        """孩子的成长状态行（口径来自 `GrowthService.ensure_state` 的两次首查：
        `child_id` + `is_deleted == 0`，不存在返回 None 由服务层建行）。
        """
        return self.get_by_field("child_id", child_id)


class PointLedgerRepository(BaseRepository[PointLedger]):
    def __init__(self, db: Session):
        super().__init__(db, PointLedger)

    def list_by_child(self, child_id: int, limit: int) -> list[PointLedger]:
        """孩子积分流水，**id 倒序 + limit**（口径来自 `GrowthService.points_list`：
        `child_id` + `is_deleted == 0` + `order_by(id.desc())` + `limit`）。
        """
        return (
            self.db.query(PointLedger)
            .filter(PointLedger.child_id == child_id, PointLedger.is_deleted == 0)
            .order_by(PointLedger.id.desc())
            .limit(limit)
            .all()
        )
