# backend/seeds/seed_demo.py — 演示现场数据重建（幂等，2026-09-04 演示救火）
"""用途：behave 业务表清理（gate）后恢复演示动线所需数据。

幂等：书目存在（books>0）则跳过全部；已有押金/借阅不重复创建。
范围（最小充分演示集）：
- 24 本上架书目 + 每本 2 副本（分级/AR/词数覆盖筛选动线）
- 演示孩（13800008888 formal）押金 1200 + 3 条在借 + 1 笔已付订单
- 1 笔待人工确认订单（审核工作台动线）
用法：.venv/bin/python -m backend.seeds.seed_demo
"""

from datetime import datetime, timedelta
from decimal import Decimal

from backend.database import get_session
from backend.domain.billing.models import Deposit, DepositLedger
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child, Order, Parent

BOOKS = [
    # (title, author, word_count, ar, grade, topic)
    ("The Very Hungry Caterpillar", "Eric Carle", 120, "1.8", "3-4岁（幼儿园）", "自然认知"),
    ("Brown Bear, Brown Bear", "Bill Martin Jr", 90, "1.5", "3-4岁（幼儿园）", "韵文启蒙"),
    ("Goodnight Moon", "Margaret Wise Brown", 100, "1.6", "3-4岁（幼儿园）", "韵文启蒙"),
    ("Dear Zoo", "Rod Campbell", 80, "1.4", "3-4岁（幼儿园）", "想象力"),
    ("Where the Wild Things Are", "Maurice Sendak", 180, "2.2", "5-6岁（幼儿园大班）", "想象力"),
    ("The Gruffalo", "Julia Donaldson", 240, "2.6", "5-6岁（幼儿园大班）", "奇幻章节书"),
    ("Room on the Broom", "Julia Donaldson", 260, "2.7", "5-6岁（幼儿园大班）", "奇幻章节书"),
    ("Green Eggs and Ham", "Dr. Seuss", 220, "2.1", "5-6岁（幼儿园大班）", "韵文启蒙"),
    ("The Cat in the Hat", "Dr. Seuss", 300, "2.9", "7-8岁（小学低年级）", "韵文启蒙"),
    ("Frog and Toad Are Friends", "Arnold Lobel", 350, "3.1", "7-8岁（小学低年级）", "成长故事"),
    ("Curious George", "H. A. Rey", 280, "2.8", "7-8岁（小学低年级）", "幽默桥梁书"),
    (
        "Magic Tree House: Dinosaurs",
        "Mary Pope Osborne",
        900,
        "3.5",
        "7-8岁（小学低年级）",
        "科普百科",
    ),
    ("Charlotte's Web", "E. B. White", 3200, "4.8", "11-12岁（小学高年级）", "成长故事"),
    ("Matilda", "Roald Dahl", 3800, "5.2", "11-12岁（小学高年级）", "成长故事"),
    (
        "Charlie and the Chocolate Factory",
        "Roald Dahl",
        3400,
        "4.9",
        "11-12岁（小学高年级）",
        "想象力",
    ),
    ("The BFG", "Roald Dahl", 3600, "5.0", "13-15岁（初中）", "奇幻章节书"),
    (
        "Harry Potter and the Sorcerer's Stone",
        "J. K. Rowling",
        7800,
        "6.1",
        "13-15岁（初中）",
        "奇幻章节书",
    ),
    (
        "Percy Jackson: The Lightning Thief",
        "Rick Riordan",
        8600,
        "5.8",
        "13-15岁（初中）",
        "奇幻章节书",
    ),
    (
        "National Geographic Kids: Sharks",
        "Anne Schreiber",
        1100,
        "3.8",
        "7-8岁（小学低年级）",
        "科普百科",
    ),
    ("Diary of a Wimpy Kid", "Jeff Kinney", 5200, "5.5", "11-12岁（小学高年级）", "幽默桥梁书"),
    ("The Snowy Day", "Ezra Jack Keats", 110, "1.7", "3-4岁（幼儿园）", "自然认知"),
    ("Corduroy", "Don Freeman", 160, "2.0", "5-6岁（幼儿园大班）", "成长故事"),
    ("Dog Man", "Dav Pilkey", 4100, "4.2", "9-10岁（小学中年级）", "幽默桥梁书"),
    (
        "Magic School Bus: Inside the Earth",
        "Joanna Cole",
        1500,
        "4.1",
        "9-10岁（小学中年级）",
        "科普百科",
    ),
]


def main() -> None:
    with get_session() as db:
        if db.query(Book).count() > 0:
            print("[seed_demo] 书目已存在，跳过（幂等）")
            return

        books: list[Book] = []
        for i, (title, author, wc, ar, grade, topic) in enumerate(BOOKS, 1):
            b = Book(
                isbn=f"978-0-{i:06d}-{i % 10}",
                title=title,
                author=author,
                word_count=wc,
                ar_level=ar,
                grade=grade,
                topic=topic,
                description=f"{title} — 演示书目（seed_demo）",
                status=Book.STATUS_ON,
            )
            db.add(b)
            books.append(b)
        db.flush()
        for b in books:
            for k in range(1, 3):  # 每本 2 副本
                db.add(
                    BookCopy(
                        book_id=b.id,
                        copy_code=f"CP-{b.id:04d}-{k:02d}",
                        status=BookCopy.STATUS_AVAILABLE,
                    )
                )
        print(f"[seed_demo] 书目 {len(books)} 本 × 2 副本 ✓")

        # 演示家庭可能被用户演示时删除（编辑/删除功能）——None 防护跳过业务段
        demo_parent = db.query(Parent).filter(Parent.phone == "13800008888").first()
        demo_child = (
            db.query(Child)
            .filter(
                Child.parent_id == demo_parent.id,
                Child.member_status == Child.MEMBER_FORMAL,
            )
            .first()
            if demo_parent
            else None
        )
        if demo_child and not db.query(Deposit).filter(Deposit.child_id == demo_child.id).first():
            dep = Deposit(
                child_id=demo_child.id,
                amount=Decimal("1200.00"),
                available_amount=Decimal("1200.00"),
                status=Deposit.STATUS_PAID,
            )
            db.add(dep)
            db.flush()
            db.add(
                DepositLedger(
                    deposit_id=dep.id,
                    entry_type=DepositLedger.ENTRY_PAY,
                    amount=Decimal("1200.00"),
                    balance_after=Decimal("1200.00"),
                    reason="演示押金缴纳",
                )
            )
            print(f"[seed_demo] 演示孩 #{demo_child.id} 押金 1200 ✓")

            # 1 笔已付订单（我的订单页）+ 1 笔待人工确认（审核工作台动线）
            o1 = Order(
                order_no=f"DEMO-O-{datetime.now():%Y%m%d%H%M%S}-1",
                order_type=Order.TYPE_OBSERVATION,
                parent_id=demo_parent.id,
                child_id=demo_child.id,
                amount=Decimal("500.00"),
                status=Order.STATUS_PAID,
                pay_method="scan",
                paid_at=datetime.now() - timedelta(days=10),
            )
            o2 = Order(
                order_no=f"DEMO-O-{datetime.now():%Y%m%d%H%M%S}-2",
                order_type=Order.TYPE_FORMAL,
                parent_id=demo_parent.id,
                child_id=demo_child.id,
                amount=Decimal("6000.00"),
                status=Order.STATUS_PENDING_MANUAL,
            )
            db.add_all([o1, o2])

            # 3 条在借（借阅操作台/我的借阅动线）
            copies = (
                db.query(BookCopy)
                .filter(BookCopy.status == BookCopy.STATUS_AVAILABLE, BookCopy.is_deleted == 0)
                .limit(3)
                .all()
            )
            for k, cp in enumerate(copies):
                cp.status = BookCopy.STATUS_BORROWED
                db.add(
                    BorrowRecord(
                        child_id=demo_child.id,
                        copy_id=cp.id,
                        book_id=cp.book_id,
                        borrowed_at=datetime.now() - timedelta(days=5 + k),
                        due_at=datetime.now() + timedelta(days=9 - k),
                        status=BorrowRecord.STATUS_ACTIVE,
                    )
                )
            print(f"[seed_demo] 在借 {len(copies)} 条 + 订单 2 笔 ✓")

        db.commit()
        print("[seed_demo] 演示数据重建完成")


if __name__ == "__main__":
    main()
