# tests/unit/test_review11_regressions_20261025.py — 第十一轮审查：复核确认的两处回归/缺口的判据
"""第十一轮全量审查中**机器复核确认**的两条（其余代理发现仍待逐条复核，见审查报告）：

1. **FE-3 修复引入的锁回归**（`review10-b3b` 54b87e0 引入、已随 f7745d6 推送）：
   `ReservationService.create()` 先 `with_for_update()` 锁 Child 行，随后调 `expire_due()`；
   而 `expire_due` 在有待释放行时**自己 commit** → **Child 行锁被提前释放**，
   后面的"同书进行中预约"判据与额度计数退化成无锁读 ⇒ 并发双击可落下两条 active 预约
   （各锁一册、额度双扣）。修法：`expire_due(commit=False)`，提交权交回 `create()` 末尾。
2. **转让前置条件不认"过期未释放"的预约**：原先只按 `status == active` 计数，
   调度器停摆时一条到期预约让转让申请/审核双双报"转出方没有进行中的预约"。

RED：① 改回 `self.expire_due()`（不带 commit=False）→ 用例 1 红；② 去掉 `expires_at > now` → 用例 3 红。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from backend.database import SessionLocal
from backend.domain.billing.models import Deposit
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.identity.models import Child, Parent
from backend.domain.reading.models import Reservation
from backend.domain.reading.reservation_service import ReservationService

_SEQ = iter(range(5101, 5999))


def _child_with_deposit(db) -> Child:
    parent = Parent(name="R11 家长", phone=f"1390002{next(_SEQ)}")
    db.add(parent)
    db.flush()
    child = Child(
        parent_id=parent.id,
        name="R11 孩子",
        member_status=Child.MEMBER_FORMAL,
        member_expire=date.today() + timedelta(days=30),
    )
    db.add(child)
    db.flush()
    db.add(
        Deposit(
            child_id=child.id,
            amount=Decimal("1200.00"),
            available_amount=Decimal("1200.00"),
            deducted_amount=Decimal("0.00"),
            unpaid_balance=Decimal("0.00"),
            status=Deposit.STATUS_PAID,
        )
    )
    db.commit()
    return child


def _book_with_copy(db) -> Book:
    book = Book(
        isbn=f"9780002{next(_SEQ):06d}",
        title="R11 靶书",
        author="probe",
        word_count=100,
        status=Book.STATUS_ON,
    )
    db.add(book)
    db.flush()
    db.add(BookCopy(book_id=book.id, copy_code=f"R11-{book.id}", status=BookCopy.STATUS_AVAILABLE))
    db.commit()
    return book


def test_create_defers_expire_due_commit(db, monkeypatch):
    """`create()` 必须用 `expire_due(commit=False)`——否则 Child 行锁被提前放掉（回归）。

    RED：改回 `self.expire_due()` → `calls == [True]` → 本用例红。
    """
    calls: list[bool] = []
    orig = ReservationService.expire_due

    def spy(self, *, commit: bool = True, child_id: int | None = None):
        # B8（2026-10-28）：create 路径改成只过期本孩子（child_id 透传）——spy 跟着放行
        calls.append(commit)
        return orig(self, commit=commit, child_id=child_id)

    monkeypatch.setattr(ReservationService, "expire_due", spy)

    child = _child_with_deposit(db)
    book = _book_with_copy(db)
    ReservationService(db).create(child, book.id)
    assert calls == [False], f"create() 里的懒过期必须 defer 提交，实际 commit 参数={calls}"


def test_expire_due_commit_false_leaves_work_uncommitted(db):
    """`expire_due(commit=False)` 不得自行提交（提交权在调用方）。

    构造：一张到期 active 预约 → `expire_due(commit=False)` → 本会话 rollback →
    **新会话**看到的仍是 active（若函数自己 commit 了，这里会是 expired）。
    """
    child = _child_with_deposit(db)
    book = _book_with_copy(db)
    copy = db.query(BookCopy).filter(BookCopy.book_id == book.id).first()
    res = Reservation(
        child_id=child.id,
        book_id=book.id,
        copy_id=copy.id,
        status=Reservation.STATUS_ACTIVE,
        expires_at=datetime.now() - timedelta(hours=3),
    )
    db.add(res)
    copy.status = BookCopy.STATUS_RESERVED
    db.commit()
    res_id = res.id

    released = ReservationService(db).expire_due(commit=False)
    assert released == 1, f"应扫到 1 条到期单，实际 {released}"
    db.rollback()  # 不提交 → 视图回到释放前

    fresh = SessionLocal()
    try:
        assert fresh.get(Reservation, res_id).status == Reservation.STATUS_ACTIVE, (
            "commit=False 时不得提交（否则 Child 行锁会被提前释放）"
        )
    finally:
        fresh.close()


def test_transfer_ignores_expired_reservation(db):
    """转让前置条件的"无进行中预约"不看过期未释放的单（读路径同口径）。

    RED：去掉 `Reservation.expires_at > datetime.now()` → 过期单仍被判为"进行中" → 本用例红。
    """
    from backend.domain.identity.transfer_service import TransferService

    source = _child_with_deposit(db)
    target = _child_with_deposit(db)
    target.parent_id = source.parent_id  # 同一家长账号（R-303 同家长前提）
    db.commit()
    book = _book_with_copy(db)
    copy = db.query(BookCopy).filter(BookCopy.book_id == book.id).first()

    def _reservation_ok(*, expired: bool) -> bool:
        db.query(Reservation).filter(Reservation.child_id == source.id).delete()
        db.commit()
        db.add(
            Reservation(
                child_id=source.id,
                book_id=book.id,
                copy_id=copy.id,
                status=Reservation.STATUS_ACTIVE,
                expires_at=(
                    datetime.now() - timedelta(hours=2)
                    if expired
                    else datetime.now() + timedelta(hours=2)
                ),
            )
        )
        db.commit()
        parent = db.get(Parent, source.parent_id)
        checks = TransferService(db).check_conditions(parent, source, target)
        # 返回形态是 [{"name": 条件文案, "ok": 是否满足}]
        row = next(c for c in checks if "预约" in c["name"])
        return bool(row["ok"])

    assert _reservation_ok(expired=False) is False, "未过期的预约必须仍然拦住转让"
    assert _reservation_ok(expired=True) is True, "过期未释放的预约不该拦住转让"
