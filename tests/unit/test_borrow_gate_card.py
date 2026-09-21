# tests/unit/test_borrow_gate_card.py — 借书资格单一来源（2026-09-21 用户反馈第 4 条）
"""用户原话：「未入会的会员，在后端竟然显示可以借 30 本，神奇啊。」

卡面显示的"能不能借"必须与借书守卫同源。本文件锁两件事：
1. 卡面 `borrow_block` 在各状态下给出正确原因与 hard 标记；
2. **一致性**：卡面说能借 ⇒ 不带放行原因真能借；卡面说不能借 ⇒ 不带放行原因真被拒
   （两处判定漂移的话，这条会红——错误库 §八十六 的同类防线）。
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _book_with_copies, _family, _h, _pay, _pay_deposit


def _db():
    from backend.database import get_session

    return get_session()


def _set_config(key: str, value: str) -> None:
    from backend.common.system_models import SystemConfig

    with _db() as db:
        row = db.query(SystemConfig).filter(SystemConfig.config_key == key).first()
        assert row is not None, f"配置键 {key} 不存在"
        row.config_value = value
        db.commit()


def _card(client: TestClient, h: dict, child_id: int) -> dict:
    return client.get(f"/api/admin/circulation/children/{child_id}/card", headers=h).json()


def _borrow(client: TestClient, h: dict, child_id: int, book_id: int, override: str | None = None):
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        copy_id = db.query(BookCopy).filter(BookCopy.book_id == book_id).first().id
    body: dict = {"child_id": child_id, "copy_id": copy_id}
    if override:
        body["override_reason"] = override
    return client.post("/api/admin/circulation/borrow", json=body, headers=h)


def test_unpaid_child_card_says_not_borrowable(client: TestClient):
    """未入会（开关关）→ 卡面明确「不可借 + 原因 + hard」，而不是显示可借 30。"""
    h = _h(client)
    _p, c, _mini = _family(client, h, "13981017001", "未入会显示孩")
    book = _book_with_copies(client, h, "未入会显示书", 1)
    _set_config("allow_unpaid_offline_borrow", "false")

    card = _card(client, h, c["id"])
    assert card["available_quota"] == 30, "额度本身仍是 30（额度≠资格）"
    assert card["borrow_block"] and "未入会" in card["borrow_block"], card["borrow_block"]
    assert card["borrow_block_hard"] is True, "开关未开 = 硬拦截（放行也没用）"
    # 一致性：卡面说不能借 → 实际真的借不出
    assert _borrow(client, h, c["id"], book).status_code == 422


def test_unpaid_child_with_switch_is_overridable(client: TestClient):
    """开关开、但没填放行原因 → 卡面仍提示不可借，但 hard=False（填原因可放行）。"""
    h = _h(client)
    _p, c, _mini = _family(client, h, "13981017002", "未入会放行孩")
    book = _book_with_copies(client, h, "未入会放行书", 1)
    _set_config("allow_unpaid_offline_borrow", "true")
    _set_config("borrow_limit", "30")

    card = _card(client, h, c["id"])
    assert card["borrow_block"] and "放行" in card["borrow_block"]
    assert card["borrow_block_hard"] is False
    assert _borrow(client, h, c["id"], book).status_code == 422
    # 填原因 → 真能借；借完卡面变成"已借满限 1 本"（未入会每次限 1 本）
    assert _borrow(client, h, c["id"], book, override="家长现场沟通").status_code == 200
    card2 = _card(client, h, c["id"])
    assert card2["borrow_block"] and "限 1 本" in card2["borrow_block"]


def test_deposit_unpaid_shows_reason(client: TestClient):
    """观察期但押金未缴 → 卡面给「押金未缴纳（可人工放行）」，hard=False。"""
    h = _h(client)
    _p, c, _mini = _family(client, h, "13981017003", "欠押金显示孩")
    _pay(client, h, c["id"], "observation_fee")
    book = _book_with_copies(client, h, "欠押金显示书", 1)

    card = _card(client, h, c["id"])
    assert card["borrow_block"] and "押金未缴纳" in card["borrow_block"]
    assert card["borrow_block_hard"] is False
    assert _borrow(client, h, c["id"], book).status_code == 422
    assert _borrow(client, h, c["id"], book, override="馆员核实").status_code == 200


def test_healthy_child_card_has_no_block(client: TestClient):
    """观察期 + 押金已缴 → 卡面无拦截提示，且真能借（一致性正向）。"""
    h = _h(client)
    _p, c, _mini = _family(client, h, "13981017004", "正常资格孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book = _book_with_copies(client, h, "正常资格书", 1)

    card = _card(client, h, c["id"])
    assert card["borrow_block"] is None, card["borrow_block"]
    assert card["borrow_block_hard"] is False
    assert _borrow(client, h, c["id"], book).status_code == 200
