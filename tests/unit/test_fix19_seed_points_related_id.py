# tests/unit/test_fix19_seed_points_related_id.py — 19 号 T-B 红测试：seed 积分 related_id 单次运行必落
# 教训 41 同族第三犯：seed 先 db.add() 三行积分（无 related_id）再 execute(UPDATE ... WHERE
# related_id IS NULL) 补挂——SessionLocal autoflush=False 下 UPDATE 发出时 pending INSERT
# 尚未落库 → 单次运行必匹配 0 行 → commit 后 related_id=None → get_quiz points_added=0
# → 兜底成绩单 +0 积分（用户目视实锤 2026-09-05）。昨日验证=10 系 seed 双跑巧合。
# 断言：单次跑 _ensure_demo_growth 后三行积分 related_id 直挂演示书 id。
from backend.database import get_session
from backend.domain.catalog.models import Book
from backend.domain.growth.models import PointLedger
from backend.domain.identity.models import Child, Parent


def test_seed_demo_points_related_id_set_on_single_run():
    with get_session() as db:
        parent = Parent(name="seed积分红测家长", phone="13899990001")
        db.add(parent)
        db.flush()
        child = Child(name="seed积分红测孩", parent_id=parent.id, gender=1)
        db.add(child)
        db.flush()
        # _ensure_demo_growth 选书口径：词数>0 的前三本在架书——保底一本
        book = (
            db.query(Book)
            .filter(Book.is_deleted == 0, Book.status == Book.STATUS_ON, Book.word_count > 0)
            .order_by(Book.id)
            .first()
        )
        if not book:
            db.add(
                Book(
                    isbn="9787199900001",
                    title="seed积分红测书",
                    word_count=100,
                    status=Book.STATUS_ON,
                )
            )
            db.flush()

        from scripts.seed_wm11_demo import _ensure_demo_growth

        _ensure_demo_growth(db, child)
        db.commit()

        rows = db.query(PointLedger).filter(PointLedger.child_id == child.id).all()
        assert len(rows) == 3, rows
        # T-B：创建时直挂 related_id——单次运行即落，不再依赖第二轮补挂
        assert all(r.related_id is not None for r in rows), [
            (r.reason_type, r.related_id) for r in rows
        ]
