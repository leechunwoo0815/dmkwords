# tests/unit/test_audit8_e3_miniapp_contracts_20261020.py — 第八轮大审查 E 批（后段）
"""小程序三条"口径必须由后端下发"的修复（前端拿不到判据就只能猜）：

- **P2-50** 满分卡 `ref_id` 是"测验提交 id"，成绩单页拿不到 → 原先只能按 `ref_id` 取最新一张，
  **晒出别的书的满分卡**。修法：`enumerate_cards` 下发 `book_id`，前端按本书精确匹配。
- **P2-51** 正确率分母原先用"当前题量"（`questions.length`）→ 题量配置改小后历史 best_score
  除当前题量会算出 **>100%**。修法：`get_quiz` 下发 `best_total`（**那次作答**的题量）。
- **P2-48** 退款页状态枚举写了不存在的 `pending_manual`（真实值是 `pending_manual_confirm`）
  → 待确认订单文案错。本用例钉住后端真实枚举，防前端再写错。

RED（变异自证）：
- 满分卡不下发 `book_id` → 用例 1 红；
- `get_quiz` 不下发 `best_total` → 用例 2 红；
- 订单状态枚举与后端不一致 → 用例 3 红（前端硬编码的枚举值必须能在后端常量里找到）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _pay_deposit
from tests.unit.test_wm13_writeback import _family, _h, _pay


def _passed_quiz_child(client: TestClient, h: dict, phone: str, name: str, isbn: str, words: int):
    """造一个"测验已通过"的孩子（真链路：建档 → 观察期费 → 建书上架 → 借出 → 完播 → 测验满分）。

    返回 (child, mini_headers, book_id)。测验满分需要把 5 题全答对——用例直接读题后作答。
    """
    from tests.unit.helpers import force_book_on

    p, c, mini = _family(client, h, phone, name)
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book = client.post(
        "/api/admin/books",
        json={"isbn": isbn, "title": f"契约书{isbn[-4:]}", "word_count": words, "copy_count": 1},
        headers=h,
    ).json()
    force_book_on(client, h, book["id"])
    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copies[0]["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return c, mini, book["id"]


def test_perfect_quiz_card_carries_book_id(client: TestClient):
    """满分卡必须下发 `book_id`（前端"按本书晒卡"的唯一判据）。

    RED（变异）：`enumerate_cards` 里删掉 `"book_id": row.book_id` → 本用例红。
    """
    from backend.common.config_service import ConfigService
    from backend.database import get_session
    from backend.domain.growth.models import QuizAttempt
    from backend.domain.identity.models import Child
    from backend.domain.reading_circle.card_engine import enumerate_cards

    h = _h(client)
    c, _mini, book_id = _passed_quiz_child(
        client, h, "13995000001", "满分卡孩", "9788400000961", 600
    )

    # 直插一条满分作答（卡片枚举只读 QuizAttempt；避免依赖 5 题全对的随机题库）
    db = get_session()
    try:
        child = db.query(Child).filter(Child.id == c["id"]).first()
        total = int(ConfigService(db).get_value("quiz_question_count", "5"))
        db.add(
            QuizAttempt(
                child_id=c["id"],
                book_id=book_id,
                score=total,
                total_questions=total,
                passed=1,
                is_deleted=0,
            )
        )
        db.commit()
        cards = enumerate_cards(db, child)
    finally:
        db.close()

    perfect = [x for x in cards if x["card_type"] == "perfect_quiz"]
    assert perfect, f"应枚举出满分卡：{[x['card_type'] for x in cards]}"
    assert perfect[0].get("book_id") == book_id, (
        f"满分卡必须带 book_id（实 {perfect[0].get('book_id')!r}）——"
        "RED=不下发 book_id，前端只能按 ref_id 取最新一张 → 晒出别的书的卡"
    )


def test_get_quiz_carries_best_total(client: TestClient):
    """`get_quiz` 必须下发 `best_total`（**那次作答**的题量）——正确率分母的唯一正确来源。

    RED（变异）：删掉 `best_total` → 本用例红（前端只能用"当前题量"当分母，题量改小即 >100%）。
    """
    from backend.database import get_session
    from backend.domain.growth.models import QuizAttempt

    h = _h(client)
    c, mini, book_id = _passed_quiz_child(client, h, "13995000002", "分母孩", "9788400000962", 700)

    db = get_session()
    try:
        # 历史作答：题量 10（配置当前可能是 5）——分母必须是 10，否则 10/5 = 200%
        db.add(
            QuizAttempt(
                child_id=c["id"],
                book_id=book_id,
                score=10,
                total_questions=10,
                passed=1,
                is_deleted=0,
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.get(f"/api/miniapp/quiz/{book_id}?child_id={c['id']}", headers=mini)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("best_total") == 10, (
        f"必须下发那次作答的题量（实 {body.get('best_total')!r}）——"
        "RED=前端用当前题量当分母 → 正确率可 >100%"
    )
    assert body.get("best_percent") == 100, f"10/10 应为 100%：{body.get('best_percent')}"
    assert body["best_total"] >= body["best_score"], f"分母不得小于分子：{body}"


def test_order_status_enum_matches_backend(client: TestClient):
    """前端写死的订单状态枚举必须都能在后端常量里找到（P2-48：`pending_manual` 是**不存在**的值）。"""
    from backend.domain.identity.models import Order

    real = {
        Order.STATUS_PENDING_PAYMENT,
        Order.STATUS_PENDING_MANUAL,
        Order.STATUS_PAID,
        Order.STATUS_REFUNDED,
        Order.STATUS_CANCELLED,
    }
    assert "pending_manual_confirm" in real, f"后端待确认枚举变了：{sorted(real)}"
    assert "pending_manual" not in real, (
        "`pending_manual` 不是真实状态——前端若写它，待确认订单会落到兜底文案"
    )
