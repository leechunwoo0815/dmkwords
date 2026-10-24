# tests/unit/test_review10_b3_money_business_20261025.py — 第十轮整改 B3：资金/业务口径
"""B3 批（1 P2 + 15 P3）：资金/状态机/口径同源类。

本文件目前覆盖 **W1-1（P2）**：预约入口押金判据与借书闸不同源——押金 `refunding`/`refunded`
的孩子仍可自助预约并锁住副本 72h（到店核销才被拦），后续 B3 条目按批续写。

每条用例都**去修复即红**（RED 见 docstring）。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.common.exceptions import ValidationError
from backend.database import SessionLocal
from backend.domain.billing.models import Deposit
from backend.domain.catalog.models import Book
from backend.domain.circulation.borrow_gate import deposit_block_reason, deposit_gate
from backend.domain.identity.models import Child, Parent
from backend.domain.reading.reservation_service import ReservationService

#: 每次调用换一个手机号/ISBN（parents.phone 与 books.isbn 都是唯一索引）
_PHONE_SEQ = iter(range(3001, 3999))


def _child_with_deposit(db, status: str, unpaid: str = "0.00") -> tuple[Child, Book]:
    """建一个"有效会员 + 指定押金状态"的孩子，以及一本在架可预约的书（含 1 本在馆副本）。"""
    parent = Parent(name="B3 家长", phone=f"1390000{next(_PHONE_SEQ)}")
    db.add(parent)
    db.flush()
    child = Child(
        parent_id=parent.id,
        name="B3 孩子",
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
            unpaid_balance=Decimal(unpaid),
            status=status,
        )
    )
    book = Book(
        isbn=f"9780000{next(_PHONE_SEQ):06d}",
        title="B3 预约靶书",
        author="probe",
        word_count=100,
        status=Book.STATUS_ON,
    )
    db.add(book)
    db.flush()
    # 预约要锁一本**在馆**副本（`无在馆副本可预约` 是另一条既有前置）
    from backend.domain.catalog.models import BookCopy

    db.add(
        BookCopy(
            book_id=book.id,
            copy_code=f"B3-{book.id}-1",
            status=BookCopy.STATUS_AVAILABLE,
        )
    )
    db.commit()
    return child, book


@pytest.mark.parametrize("status", [Deposit.STATUS_REFUNDED, Deposit.STATUS_REFUNDING])
def test_reservation_blocks_refunded_and_refunding_deposit(db, status: str):
    """W1-1（P2）：押金 `refunded`（已退未复位）/ `refunding`（退款在途）不得自助预约。

    RED（修复前）：预约入口的判据是**另一份黑名单**（只拦 unpaid / fully_deducted / 欠款）
    → `refunding`/`refunded` 落进"一律放行" → 预约 200 且副本被置 `reserved`（占额度 72h），
    到店核销时才被借书闸 `deposit_gate` 拦下（家长白约、别的孩子借不到）。同一个判定写两遍。
    """
    child, book = _child_with_deposit(db, status)
    with pytest.raises(ValidationError) as ei:
        ReservationService(db).create(child, book.id)
    msg = str(ei.value)
    assert status in msg, f"报错要说清是哪个押金状态：{msg}"
    assert "不能预约" in msg, msg

    db.rollback()
    from backend.domain.catalog.models import BookCopy
    from backend.domain.reading.models import Reservation

    reserved = (
        db.query(Reservation)
        .filter(Reservation.child_id == child.id, Reservation.status == Reservation.STATUS_ACTIVE)
        .count()
    )
    assert reserved == 0, "被拦下就不该留下进行中的预约"
    copies = (
        db.query(BookCopy)
        .filter(BookCopy.book_id == book.id, BookCopy.status == BookCopy.STATUS_RESERVED)
        .count()
    )
    assert copies == 0, "被拦下就不该锁住任何副本"


def test_reservation_allows_paid_and_blocked_cases(db):
    """对照组（防"修过头"）：`paid` 放行；`unpaid`/`fully_deducted`/有欠款 仍拦住。"""
    child, book = _child_with_deposit(db, Deposit.STATUS_PAID)
    r = ReservationService(db).create(child, book.id)
    assert r.id, "已缴押金的有效会员必须能预约"
    db.rollback()

    for status, unpaid, keyword in (
        (Deposit.STATUS_UNPAID, "0.00", "押金未缴纳"),
        (Deposit.STATUS_FULLY_DEDUCTED, "0.00", "押金已扣光"),
        (Deposit.STATUS_PAID, "50.00", "有未结清赔偿款"),
    ):
        child2, book2 = _child_with_deposit(db, status, unpaid)
        with pytest.raises(ValidationError) as ei:
            ReservationService(db).create(child2, book2.id)
        assert keyword in str(ei.value), f"{status}/{unpaid} 的报错文案：{ei.value}"
        db.rollback()


def test_deposit_judgement_single_source(monkeypatch):
    """W1-1 的**同源**判据：借书闸与预约入口必须共用 `deposit_block_reason`。

    做法：把该函数换成返回哨兵原因的实现 → 两条路径都必须跟着变（RED=还有第二份判据）。
    """
    import backend.domain.circulation.borrow_gate as bg
    import backend.domain.reading.reservation_service as rs

    monkeypatch.setattr(bg, "deposit_block_reason", lambda **_k: "哨兵押金原因")
    monkeypatch.setattr(rs, "deposit_block_reason", lambda **_k: "哨兵押金原因")

    gate = deposit_gate(
        deposit_status=Deposit.STATUS_PAID,
        deposit_unpaid_balance=0,
        override_reason=None,
        unpaid_status=Deposit.STATUS_UNPAID,
        fully_deducted_status=Deposit.STATUS_FULLY_DEDUCTED,
    )
    assert "哨兵押金原因" in gate.reason, f"借书闸没走共用判据：{gate}"

    db = SessionLocal()
    try:
        child, book = _child_with_deposit(db, Deposit.STATUS_PAID)
        with pytest.raises(ValidationError) as ei:
            rs.ReservationService(db).create(child, book.id)
        assert "哨兵押金原因" in str(ei.value), f"预约入口没走共用判据：{ei.value}"
    finally:
        db.rollback()
        db.close()


def test_deposit_block_reason_matrix():
    """单一来源的行为矩阵（含单位：入参是**分**，文案输出**元**）。"""
    cases = [
        (Deposit.STATUS_PAID, 0, None),
        (Deposit.STATUS_PARTIALLY_DEDUCTED, 0, None),
        (Deposit.STATUS_PAID, 1, "有未结清赔偿款 0.01 元"),
        (Deposit.STATUS_UNPAID, 0, "押金未缴纳"),
        (None, 0, "押金未缴纳"),
        (Deposit.STATUS_FULLY_DEDUCTED, 0, "押金已扣光"),
        (Deposit.STATUS_REFUNDING, 0, "押金状态为 refunding（非已缴，需重新缴纳）"),
        (Deposit.STATUS_REFUNDED, 0, "押金状态为 refunded（非已缴，需重新缴纳）"),
    ]
    for status, unpaid, expected in cases:
        got = deposit_block_reason(
            deposit_status=status,
            deposit_unpaid_balance=unpaid,
            unpaid_status=Deposit.STATUS_UNPAID,
            fully_deducted_status=Deposit.STATUS_FULLY_DEDUCTED,
        )
        assert got == expected, f"{status}/{unpaid}：期望 {expected!r}，实际 {got!r}"
