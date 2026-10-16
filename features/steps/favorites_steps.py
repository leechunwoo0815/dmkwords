# features/steps/favorites_steps.py — FEAT-056 收藏夹 步骤定义
"""2026-10-16（第六十五批·BDD 解封第六批）· **收藏夹 6 场景**全部转真实执行。

判据与实现同源（动手前逐条亲验，红线 108/136）：
- **收藏**（PRD §8.2）：想读清单、**不限量、不占额度、不产生任何权利**；同孩子同书唯一
  （`uq_fav_child_book` + 活跃行 409）；取消是**软删**，再收藏走"复活"（原行复用）。
- **下架书**（PRD §4.4）：收藏夹里**仍然显示并标注"已下架"**（`off_shelf` 字段 + `shelf.wxml`
  的 `tag-off`），但**借/约/听三处全拦**（借 422 · 约 422 · 音频 403 `off_shelf`）。
- **未缴费/退会**（R-314）：未缴费**可收藏**（收藏是"标记"，不产生权利）；退会**只读**
  ——列表放行（四态皆可查看，列表端点本就无门禁）+ 增删挂 `FAVORITE_WRITE`（422「已退会，收藏夹只读」）。

手法与红线：
- 场景 3（"收藏不限量不占额度"）与场景 5 在 feature 里**没有"当"**（是状态断言）——步骤内
  **真实收藏**到 40 本（超过 30 的借阅上限）再断言，不直插 `favorites` 行（预置行 = 假绿）。
- 场景 1 的"**按时间倒序**"必须有 ≥2 条才可判定（1 条恒真）——步骤内真实收藏第二本再断言顺序。
- 步骤文本里的孩子名用**字面量**（`小明收藏《…》` / `小明再次收藏《…》`）：behave 的
  歧义检查是拿"另一个定义的**原文**（含 `{}` 占位符）"去跑本定义的匹配器，`{child}` 这种宽占位符
  会把"小明**再次**收藏"整段吃进去 → `AmbiguousStep`（红线 144 的新形状）。
- 场景 5 的 `假如 孩子 "小红" 未入会` **复用既有步骤定义**（`growth_quiz_steps.py`，behave 步骤表
  全局唯一，红线 144）：该步骤把"小红"建在**同一家长**名下并置 `member_status=none`，
  故本文件用 `context.mini`（家长 token）+ `context.other["id"]` 操作她。
- 退会态用 DB 直改 `member_status`（与成长/榜单各域同款手法：退会真链路要退押金+结算，
  与本域判据无关，另见 `withdrawal.feature`）。
"""

from behave import given, then, when  # type: ignore[import-not-found]

from tests.unit.test_wm10_concurrency import _db, _h

PHONE = "13981037962"  # 收藏夹场景专用家长（家长手机号唯一，与本仓其他 feature 错开）
NO_CAP_BOOKS = 40  # 「不限量」的证据量：超过 30 的借阅上限，收藏照样收下


# ---------- 基建 ----------


def _db_session():
    return _db()


def _admin(context) -> dict:
    if getattr(context, "h", None) is None:
        context.h = _h(context.client)
    return context.h


def _family(context) -> dict:
    """背景家长 + 孩子"小明"（有效会员）+ 小程序登录；**幂等**（同一场景多次调用不重复建档）。"""
    if getattr(context, "fav", None) is not None:
        return context.fav
    h = _admin(context)
    parent = context.client.post(
        "/api/admin/members/parents", json={"name": "收藏家长", "phone": PHONE}, headers=h
    ).json()
    child = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children", json={"name": "小明"}, headers=h
    ).json()
    fee = context.client.post(
        "/api/admin/orders",
        json={"child_id": child["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    r = context.client.post(
        f"/api/admin/orders/{fee['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r.status_code == 200, r.text
    login = context.client.post("/api/miniapp/login", json={"phone": PHONE, "code": "1234"}).json()
    assert login.get("token"), login
    context.fav = {
        "parent": parent,
        "child": child,
        "mini": {"Authorization": f"Bearer {login['token']}"},
        "books": {},
    }
    return context.fav


def _book(context, title: str, tag: str = "") -> dict:
    key = title + tag
    fav = _family(context)
    if key in fav["books"]:
        return fav["books"][key]
    created = context.client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 2500, "copy_count": 1},
        headers=_admin(context),
    )
    assert created.status_code == 200, created.text
    book = created.json()
    # 新建书目**默认下架**（D1 契约：上架要过完整性五检）——收藏/下架场景都需要先让它上架，
    # 否则场景 1 的 `off_shelf is False` 与场景 4 的"再下架一次"都不成立（实测踩到：
    # toggle 把默认下架的书往**上**切 → 409「未传封面、未传音频、未配置 AR、未满 5 道题」）。
    from tests.unit.helpers import force_book_on

    force_book_on(context.client, _admin(context), book["id"])
    book["title"] = title
    fav["books"][key] = book
    return book


def _add(context, book: dict, *, child: dict | None = None, mini: dict | None = None):
    fav = _family(context)
    return context.client.post(
        "/api/miniapp/favorites",
        json={"child_id": (child or fav["child"])["id"], "book_id": book["id"]},
        headers=mini or fav["mini"],
    )


def _list(context, child: dict | None = None, mini: dict | None = None) -> list[dict]:
    fav = _family(context)
    r = context.client.get(
        "/api/miniapp/favorites",
        params={"child_id": (child or fav["child"])["id"]},
        headers=mini or fav["mini"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _card(context, child_id: int) -> dict:
    r = context.client.get(
        f"/api/admin/circulation/children/{child_id}/card", headers=_admin(context)
    )
    assert r.status_code == 200, r.text
    return r.json()


def _set_member_status(child_id: int, status: str) -> None:
    from backend.domain.identity.models import Child

    with _db_session() as db:
        db.query(Child).filter(Child.id == child_id).update({"member_status": status})
        db.commit()


def _borrow_count(child_id: int) -> int:
    from backend.domain.circulation.models import BorrowRecord

    with _db_session() as db:
        return (
            db.query(BorrowRecord)
            .filter(BorrowRecord.child_id == child_id, BorrowRecord.is_deleted == 0)
            .count()
        )


# ---------- 场景 1：收藏书目 ----------


@when("小明收藏《{title}》")
def step_favorite(context, title: str) -> None:
    book = _book(context, title)
    r = _add(context, book)
    assert r.status_code == 200, r.text
    context.fav["first"] = {"book": book, "resp": r.json()}


@then("收藏成功且收藏列表按时间倒序展示")
def step_favorited_desc(context) -> None:
    """「按时间倒序」1 条恒真——真实收藏第二本再断言顺序（红线 136：先问"可能是假的吗"）。"""
    first = context.fav["first"]
    assert first["resp"]["book_id"] == first["book"]["id"]
    second = _book(context, "Cat Kid")
    assert _add(context, second).status_code == 200
    rows = _list(context)
    assert [r["book_id"] for r in rows] == [second["id"], first["book"]["id"]], rows
    assert rows[0]["title"] == "Cat Kid" and rows[0]["off_shelf"] is False, rows[0]


# ---------- 场景 2：同孩子同书唯一 ----------


@when("小明再次收藏《{title}》")
def step_favorite_again(context, title: str) -> None:
    """「再次」= 已经有第一次：没有就先真收藏一次，再收藏一次（两次都走真实接口）。"""
    book = _book(context, title)
    if not _list(context):
        assert _add(context, book).status_code == 200
    context.fav["dup_resp"] = _add(context, book)


@then("系统拒绝重复收藏")
def step_duplicate_rejected(context) -> None:
    r = context.fav["dup_resp"]
    assert r.status_code == 409, (r.status_code, r.text)
    assert "已收藏" in r.json()["detail"], r.text
    rows = _list(context)
    assert len(rows) == 1, rows


# ---------- 场景 3：不限量不占额度（feature 里没有"当"） ----------


@then("收藏数量无上限")
def step_no_cap(context) -> None:
    """真实收藏 40 本（> 借阅上限 30）——不直插 `favorites` 行（预置行 = 假绿）。"""
    fav = _family(context)
    fav["quota_before"] = _card(context, fav["child"]["id"])["available_quota"]
    for i in range(NO_CAP_BOOKS):
        book = _book(context, f"收藏演练书{i:02d}")
        r = _add(context, book)
        assert r.status_code == 200, (i, r.status_code, r.text)
    rows = _list(context)
    assert len(rows) == NO_CAP_BOOKS, len(rows)
    limit = _card(context, fav["child"]["id"])["borrow_limit"]
    assert NO_CAP_BOOKS > limit, f"证据量要超过借阅上限（{limit}）才说明收藏不是借阅"


@then("收藏不影响借阅额度与借阅状态")
def step_no_quota_impact(context) -> None:
    fav = context.fav
    child_id = fav["child"]["id"]
    card = _card(context, child_id)
    assert card["available_quota"] == fav["quota_before"], (fav["quota_before"], card)
    assert card["active_borrows"] == 0, card
    assert _borrow_count(child_id) == 0, "收藏不该产生任何借阅记录"
    assert (
        context.client.get(
            "/api/miniapp/borrows", params={"child_id": child_id}, headers=fav["mini"]
        ).json()
        == []
    )


# ---------- 场景 4：下架书可见但三处拦 ----------


@given("收藏的《{title}》已下架")
def step_favorited_off_shelf(context, title: str) -> None:
    book = _book(context, title)
    assert _add(context, book).status_code == 200
    r = context.client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=_admin(context))
    assert r.status_code == 200, r.text
    context.fav["off_shelf_book"] = book


@then('收藏夹仍展示该书并标注"已下架"')
def step_off_shelf_visible(context) -> None:
    book = context.fav["off_shelf_book"]
    rows = _list(context)
    assert [(r["book_id"], r["title"], r["off_shelf"]) for r in rows] == [
        (book["id"], "Dog Man", True)
    ], rows
    # 标记要有**消费方**（P1 #11 的修法：字段下发不消费 = 未交付，红线 158）——
    # 收藏夹渲染在书架页的收藏页签（`shelf.wxml` 的 `tag-off`「已下架」）
    from pathlib import Path

    wxml = (Path(__file__).resolve().parents[2] / "miniapp/pages/shelf/shelf.wxml").read_text(
        encoding="utf-8"
    )
    assert "item.off_shelf" in wxml and "已下架" in wxml, "收藏夹页面没有消费 off_shelf 标记"


@then("不可借阅不可预约不可播放音频")
def step_off_shelf_blocked(context) -> None:
    fav = context.fav
    book, child = fav["off_shelf_book"], fav["child"]
    copies = context.client.get(
        f"/api/admin/books/{book['id']}/copies", headers=_admin(context)
    ).json()
    borrow = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": child["id"], "copy_id": copies[0]["id"]},
        headers=_admin(context),
    )
    assert borrow.status_code == 422 and "已下架" in borrow.json()["detail"], borrow.text
    res = context.client.post(
        "/api/miniapp/reservations",
        json={"child_id": child["id"], "book_id": book["id"]},
        headers=fav["mini"],
    )
    assert res.status_code == 422 and "已下架" in res.json()["detail"], res.text
    perm = context.client.get(
        f"/api/miniapp/books/{book['id']}/audio-permission",
        params={"child_id": child["id"]},
        headers=fav["mini"],
    )
    assert perm.status_code == 200 and perm.json()["reason"] == "off_shelf", perm.text
    # 直链也拦（预检与音频流同源，R2/C-13 禁漂移）
    stream = context.client.get(
        f"/api/miniapp/books/{book['id']}/audio",
        params={"child_id": child["id"], "token": fav["mini"]["Authorization"].split()[1]},
    )
    assert stream.status_code == 403 and "下架" in stream.json()["detail"], stream.text


# ---------- 场景 5：未缴费也可收藏（"小红未入会"由 growth_quiz_steps 的既有步骤建档） ----------


@then("小红可以正常使用收藏夹")
def step_unpaid_can_favorite(context) -> None:
    child = context.other  # 既有步骤（growth_quiz_steps）建在**同一家长**名下、member_status=none
    from backend.domain.identity.models import Child

    with _db_session() as db:
        row = db.query(Child).filter(Child.id == child["id"]).first()
        assert row.member_status == "none", row.member_status
    books = [_book(context, "未缴费收藏书甲"), _book(context, "未缴费收藏书乙")]
    for b in books:
        r = _add(context, b, child=child, mini=context.mini)
        assert r.status_code == 200, (r.status_code, r.text)
    rows = _list(context, child=child, mini=context.mini)
    assert [r["book_id"] for r in rows] == [books[1]["id"], books[0]["id"]], rows
    # 取消也允许（R-314 只对"退会"只读，未缴费不设只读）
    rm = context.client.delete(
        f"/api/miniapp/favorites/{books[0]['id']}",
        params={"child_id": child["id"]},
        headers=context.mini,
    )
    assert rm.status_code == 200, rm.text
    assert [r["book_id"] for r in _list(context, child=child, mini=context.mini)] == [
        books[1]["id"]
    ]


# ---------- 场景 6：退会只读 ----------


@given("小明已退会")
def step_withdrawn(context) -> None:
    book = _book(context, "退会演练书")
    assert _add(context, book).status_code == 200
    context.fav["withdrawn_book"] = book
    _set_member_status(_family(context)["child"]["id"], "withdrawn")


@then("收藏列表可查看但不可新增或删除")
def step_withdrawn_readonly(context) -> None:
    fav = context.fav
    book, child = fav["withdrawn_book"], fav["child"]
    rows = _list(context)  # 可查看（R-314：四态皆可看）
    assert [r["book_id"] for r in rows] == [book["id"]], rows
    add = _add(context, _book(context, "退会后新书"))
    assert add.status_code == 422 and add.json()["detail"] == "已退会，收藏夹只读", add.text
    rm = context.client.delete(
        f"/api/miniapp/favorites/{book['id']}",
        params={"child_id": child["id"]},
        headers=fav["mini"],
    )
    assert rm.status_code == 422 and rm.json()["detail"] == "已退会，收藏夹只读", rm.text
    assert [r["book_id"] for r in _list(context)] == [book["id"]], "只读态下收藏夹内容不得变化"
