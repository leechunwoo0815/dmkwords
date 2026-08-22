# backend/common/fine_policy.py
"""逾期服务费与音频锁定策略 — 决策 B7/B8 落地

- 宽限期 `overdue_grace_days`（默认 3）：逾期前 N 天免罚、音频不锁，第 N+1 天起算
- 上限 `overdue_fine_cap_ratio`（默认 0.5）：单本逾期服务费 ≤ 图书定价 × 比例
- 首次免罚 `first_overdue_free`（默认 true）：每孩子首次逾期记录免罚
  （实际服务费记 0，原计算金额存 fine_original，标记 fine_waived=1）
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService


@dataclass(frozen=True)
class OverduePolicy:
    grace_days: int
    daily_fine: Decimal
    cap_ratio: Decimal
    first_free: bool


def get_overdue_policy(db: Session) -> OverduePolicy:
    """从动态配置读取逾期策略（运营可改，保留修改接口）"""
    return OverduePolicy(
        grace_days=ConfigService.get_int(db, "overdue_grace_days", 3),
        daily_fine=ConfigService.get_decimal(db, "overdue_fine_per_day", Decimal("1")),
        cap_ratio=ConfigService.get_decimal(db, "overdue_fine_cap_ratio", Decimal("0.5")),
        first_free=ConfigService.get_bool(db, "first_overdue_free", True),
    )


def calc_overdue_days(now: datetime, due_date: datetime) -> int:
    """逾期天数 — 按自然日计（到期日当天=0，次日=1）"""
    return max(0, (now.date() - due_date.date()).days)


def calc_fine(days_overdue: int, book_price: Decimal | None, policy: OverduePolicy) -> Decimal:
    """逾期服务费 = (逾期天数 - 宽限期) × 日费， capped at 定价 × 上限比例"""
    if days_overdue <= policy.grace_days:
        return Decimal("0")
    billable = days_overdue - policy.grace_days
    fine = Decimal(str(billable)) * policy.daily_fine
    if book_price:
        cap = Decimal(str(book_price)) * policy.cap_ratio
        fine = min(fine, cap)
    return fine.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def is_first_overdue(
    db: Session,
    child_id: int,
    exclude_id: int | None = None,
    grace_days: int = 0,
) -> bool:
    """是否该孩子的首次逾期（无其他计费逾期记录）

    F-055：宽限期内的逾期（overdue_days <= grace_days）不计费、不消耗免罚额度——
    只有真正计费的逾期（overdue_days > grace_days）才算"用过一次免罚"。
    """
    from backend.domain.borrow.models import BorrowRecord

    q = db.query(BorrowRecord).filter(
        BorrowRecord.child_id == child_id,
        BorrowRecord.is_deleted == 0,
        BorrowRecord.overdue_days > grace_days,
    )
    if exclude_id is not None:
        q = q.filter(BorrowRecord.id != exclude_id)
    # F-047：当前读（FOR UPDATE）——child 锁串行化后仍需读到并发已提交的
    # fine_waived=1；REPEATABLE READ 下普通 count 用事务快照会漏看（双免根因）
    return q.with_for_update().count() == 0


def apply_fine(db: Session, record, days_overdue: int, policy: OverduePolicy) -> None:
    """把逾期天数与服务费写回借阅记录（含首次免罚核销）"""
    record.overdue_days = days_overdue
    if days_overdue <= 0:
        return
    price = record.book.price if record.book else None
    fine = calc_fine(days_overdue, price, policy)
    record.fine_original = fine
    if fine <= 0:
        record.fine_amount = Decimal("0")
    elif record.fine_waived == 1:
        # 已被免罚的记录保持免罚
        record.fine_amount = Decimal("0")
    elif policy.first_free:
        # F-047：child 行锁串行化首次免罚判定——并发两条记录同时处理同一孩子时，
        # 第二个会看到第一个已写 fine_waived=1 → 不再免罚（防双免）
        from backend.domain.child.models import Child

        child = (
            db.query(Child)
            .filter(Child.id == record.child_id, Child.is_deleted == 0)
            .with_for_update()
            .first()
        )
        if child and is_first_overdue(
            db,
            record.child_id,
            exclude_id=record.id,
            grace_days=policy.grace_days,
        ):
            record.fine_amount = Decimal("0")
            record.fine_waived = 1
        else:
            record.fine_amount = fine
    else:
        record.fine_amount = fine


def sync_outstanding_fine(db: Session, child, record) -> Decimal:
    """按差额增量把借阅罚款同步到 child.outstanding_fines（F35/F36）

    增量口径：delta = record.fine_amount - record.fine_in_outstanding。
    record.fine_in_outstanding 为已入账标记列（迁移 046），已入账部分绝不重复加；
    罚款增长/归零都正确反映。只动"逾期服务费"这部分，不触碰损坏/丢失/手工罚款
    （那些路径维持 += 语义），修复 F35 覆写丢失损坏罚款与 F36 还书不入账。
    调用方须已对 child 加行锁（与记录更新同事务）。
    """
    counted = (
        Decimal(str(record.fine_in_outstanding)) if record.fine_in_outstanding else Decimal("0")
    )
    current = Decimal(str(record.fine_amount)) if record.fine_amount else Decimal("0")
    delta = current - counted
    if delta != 0:
        child.outstanding_fines = (
            Decimal(str(child.outstanding_fines)) if child.outstanding_fines else Decimal("0")
        ) + delta
        record.fine_in_outstanding = current
    return delta
