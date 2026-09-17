# tests/unit/test_fix19_seed_points_related_id.py — 19 号 T-B 红测试（2026-09-17 按新规则语义重写）
#
# 【T-B 原始教训，仍然锁死】seed 先 db.add() 积分行、再 execute(UPDATE ... WHERE related_id
# IS NULL) 补挂——SessionLocal autoflush=False 下 UPDATE 发出时 pending INSERT 尚未落库 →
# 单次运行必匹配 0 行 → commit 后 related_id=None → get_quiz points_added=0 → 兜底成绩单
# +0 积分（用户目视实锤 2026-09-05）。断言核心=**单次运行后每行积分的 related_id 必须已直挂**。
#
# 【2026-09-17 重写断言的原因】用户「成绩单的积分都是横线，这个按规则增加积分」：
# seed 不再手拍固定 3 行（5/3/2，全挂一本书），改为**按 on_quiz_passed 的规则从词账 +
# 测验记录推导**——没有阅读数据的空孩子不再凭空得积分。原断言 `len(rows) == 3` 锁的是
# 手拍值的行数，在新语义下已无意义；本测试同步改为「造一条真实阅读链 → 跑一次推导 →
# 断言积分按规则得出、且 related_id 直挂」，比原断言更强（连金额口径一起锁）。
#
# 【2026-09-17 二次调整：入口函数换了】推导逻辑从 `_ensure_demo_growth` 移到
# `_rebuild_demo_points`（**必须在词账/测验就位之后跑**——清库重建实证：放在流程早期时
# 增量脏库能过、从零跑则 0 行）。本测试直接呼该函数，锁的不变量不变。
from backend.common.config_service import ConfigService
from backend.database import get_session
from backend.domain.catalog.models import Book
from backend.domain.growth.models import PointLedger, QuizAttempt, WordsLedger
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
            book = Book(
                isbn="9787199900001",
                title="seed积分红测书",
                word_count=100,
                status=Book.STATUS_ON,
            )
            db.add(book)
            db.flush()
        # 真实链路三件套里的两件：词账（折算口径的来源）+ 满分测验（奖励口径的来源）
        db.add(WordsLedger(child_id=child.id, book_id=book.id, word_count=book.word_count))
        db.add(
            QuizAttempt(
                child_id=child.id,
                book_id=book.id,
                score=5,
                total_questions=5,
                passed=1,
                snapshot="[]",
            )
        )
        db.flush()

        from scripts.seed_wm11_demo import _rebuild_demo_points

        _rebuild_demo_points(db, child)
        db.commit()

        rows = db.query(PointLedger).filter(PointLedger.child_id == child.id).all()
        # ① T-B 不变量：单次运行即直挂 related_id（禁"第二轮 UPDATE 补挂"）
        assert all(r.related_id is not None for r in rows), [
            (r.reason_type, r.related_id) for r in rows
        ]
        assert all(r.related_id == book.id for r in rows), [
            (r.reason_type, r.related_id) for r in rows
        ]
        # ② 按规则推导（规则常量从配置读，不写死数字）：词数折算 + 满分奖
        per_point = max(1, int(ConfigService(db).get_value("words_per_point") or 1))
        expected_types = {"quiz_full_marks"}
        expected_points = int(ConfigService(db).get_value("quiz_full_marks_bonus"))
        if book.word_count >= per_point:
            expected_types.add("words_convert")
            expected_points += book.word_count // per_point
        assert {r.reason_type for r in rows} == expected_types, [
            (r.reason_type, r.points) for r in rows
        ]
        assert sum(r.points for r in rows) == expected_points, [
            (r.reason_type, r.points) for r in rows
        ]
