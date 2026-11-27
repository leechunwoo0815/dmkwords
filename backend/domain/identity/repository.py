# backend/domain/identity/repository.py — identity 域数据访问（只读单表）
"""身份域纪律：`parents.phone` 的"同号复活"锁定读、订单/退款的条件 UPDATE、
孩子会员状态的条件 UPDATE 一律留在服务层——那些是资金与并发判据，
repository 只收无锁的普通 SELECT。
"""

from sqlalchemy.orm import Session

from backend.common.base_repo import BaseRepository
from backend.domain.identity.models import Parent


class ParentRepository(BaseRepository[Parent]):
    def __init__(self, db: Session):
        super().__init__(db, Parent)

    def get_active(self, parent_id: int) -> Parent | None:
        """未删档家长（口径来自 `ParentService.get`：`id` + `is_deleted == 0`）。

        返回 None 时由服务层抛 `NotFoundError("家长不存在")`，异常文案与原来一致。
        """
        return self.get_by_id(parent_id)
