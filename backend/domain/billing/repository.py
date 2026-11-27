# backend/domain/billing/repository.py — billing 域数据访问（只读单表）
"""资金域纪律：这里只收**非锁定读**（普通 SELECT）。

`with_for_update` 的锁定读一律留在服务层（`DepositService.on_deposit_order_paid` /
`deduct_for_compensation` 等）——锁的获取时机与事务边界是业务语义的一部分，
下沉到 repository 会把"先锁谁"这个并发判据藏起来。
"""

from sqlalchemy.orm import Session

from backend.common.base_repo import BaseRepository
from backend.domain.billing.models import Deposit, DepositLedger


class DepositRepository(BaseRepository[Deposit]):
    def __init__(self, db: Session):
        super().__init__(db, Deposit)

    def get_by_child(self, child_id: int) -> Deposit | None:
        """孩子的押金账户（非锁定读；口径来自 `DepositService.get_by_child` 的押金查询：
        `child_id` + `is_deleted == 0`，返回 None 表示尚未建卡）。
        """
        return self.get_by_field("child_id", child_id)


class DepositLedgerRepository(BaseRepository[DepositLedger]):
    def __init__(self, db: Session):
        super().__init__(db, DepositLedger)

    def list_by_deposit(self, deposit_id: int) -> list[DepositLedger]:
        """某押金账户的全部流水，**id 倒序**（口径来自 `DepositService.get_by_child` 的
        ledger 查询：`deposit_id` + `is_deleted == 0` + `order_by(id.desc())`，不限量）。
        """
        return (
            self.db.query(DepositLedger)
            .filter(DepositLedger.deposit_id == deposit_id, DepositLedger.is_deleted == 0)
            .order_by(DepositLedger.id.desc())
            .all()
        )
