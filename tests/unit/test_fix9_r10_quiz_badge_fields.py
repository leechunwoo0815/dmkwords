# tests/unit/test_fix10_quiz_badge_fields.py — 插修10 返工：金卡口径与兜底数据真实化
# 用户目视实锤（2026-09-04）：①金卡"最佳成绩 5 分"+一星——get_quiz.best_score 是答对
# 题数口径，星级档位（≥90 五星）被带偏；②seed 造的书无本地缓存走 quiz-result 兜底，
# words_added/points 硬编码 0，而护照片显示 +120 词已到账（+0 误导）。
# 修法：get_quiz 响应补 best_percent（百分制最佳）/words_added（该书真实入账）/
# points_added（related_id=book_id 的积分求和）——前端金卡与兜底成绩单消费。
from fastapi.testclient import TestClient

from tests.unit.test_wm7_growth import _h, _setup_finished_book

ALL_RIGHT = ["A", "A", "A", "A", "对"]  # 5/5 满分
THREE_RIGHT = ["A", "A", "A", "B", "错"]  # 3/5 未过


def _get_quiz(client, mini, book_id, child_id):
    return client.get(f"/api/miniapp/quiz/{book_id}?child_id={child_id}", headers=mini).json()


def test_get_quiz_returns_best_percent_words_points_after_pass(client: TestClient):
    """5/5 通过后：best_percent=100（金卡五星口径）、words_added=真实入账、
    points_added=related_id 口径积分求和。"""
    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13800000711", "9787100000011")
    r = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={"child_id": c["id"], "answers": ALL_RIGHT},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    q = _get_quiz(client, mini, book["id"], c["id"])
    assert q["status"] == "passed"
    # 插修10 新三字段
    assert q["best_percent"] == 100, q
    assert q["words_added"] == book["word_count"], q
    # points_added 与 DB related_id 求和一致（折算+测验奖励都挂 book_id）
    from backend.database import get_session
    from backend.domain.growth.models import PointLedger

    with get_session() as db:
        expect = (
            db.query(PointLedger)
            .filter(
                PointLedger.child_id == c["id"],
                PointLedger.related_id == book["id"],
                PointLedger.reason_type.in_(
                    ["words_convert", "quiz_first_pass", "quiz_full_marks"]
                ),
                PointLedger.is_deleted == 0,
            )
            .all()
        )
    assert q["points_added"] == sum(p.points for p in expect), q
    assert q["points_added"] > 0


def test_get_quiz_best_percent_partial_fail_no_words(client: TestClient):
    """3/5 未通过：best_percent=60（60 分档两星口径）、words_added=0、points_added=0。"""
    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13800000712", "9787100000012")
    r = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={"child_id": c["id"], "answers": THREE_RIGHT},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    q = _get_quiz(client, mini, book["id"], c["id"])
    assert q["status"] == "available"  # 未过仍有机会
    assert q["best_percent"] == 60, q
    assert q["words_added"] == 0, q
    assert q["points_added"] == 0, q
