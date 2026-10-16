# tests/unit/test_favorites_20261016.py — 收藏夹（FEAT-056 / R-314）
"""2026-10-16（第六十五批·BDD 解封 favorites）：收藏夹 6 场景的口径 + 一处入参健壮性缺口。

亲验结论：6 条场景的判据与实现**同源**（收藏唯一/不限量不占额度/下架可见且三处拦/未缴费可收藏/
退会只读），故本文件不重复 BDD，只钉住两件 BDD 不便验的事：

1. **口径边界**：倒序是 `id.desc()`（同秒落库也稳定）、软删行**复活**而非 409、退会四态里
   "列表无门禁" 与 "增删挂 FAVORITE_WRITE" 的分工；
2. **入参健壮性缺口（本批修）**：`POST /favorites` 原先是裸 `body: dict` + `int(body.get(...))`，
   非法入参（字符串/数组/缺键）会抛 `ValueError`/`AttributeError` → **500**；改类型化
   `FavoriteAddRequest` 后由 FastAPI 归为 **422**（与同域其他端点一致）。
"""

from fastapi.testclient import TestClient

PHONE = "13800008171"


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _child(client, h, phone=PHONE, name="小明", *, member=True):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    if member:
        o = client.post(
            "/api/admin/orders",
            json={"child_id": c["id"], "order_type": "observation_fee"},
            headers=h,
        ).json()
        client.post(
            f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
        )
    m = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    return c, {"Authorization": f"Bearer {m['token']}"}


def _book(client, h, title):
    r = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 1000, "copy_count": 1},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _off_shelf(client, h, book_id):
    r = client.post(f"/api/admin/books/{book_id}/toggle-status", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _favorites(client, mini, child_id):
    r = client.get("/api/miniapp/favorites", params={"child_id": child_id}, headers=mini)
    assert r.status_code == 200, r.text
    return r.json()


def _add(client, mini, child_id, book_id):
    return client.post(
        "/api/miniapp/favorites",
        json={"child_id": child_id, "book_id": book_id},
        headers=mini,
    )


def test_favorites_roundtrip_order_and_revive(client: TestClient):
    h = _h(client)
    c, mini = _child(client, h)
    b1 = _book(client, h, "Dog Man")
    b2 = _book(client, h, "Cat Kid")

    assert _add(client, mini, c["id"], b1["id"]).status_code == 200
    assert _add(client, mini, c["id"], b2["id"]).status_code == 200
    rows = _favorites(client, mini, c["id"])
    # 判据「按时间倒序」= 后收藏的在前（同秒落库也要稳定：按 id 倒序）
    assert [r["book_id"] for r in rows] == [b2["id"], b1["id"]], rows
    assert rows[0]["title"] == "Cat Kid" and rows[0]["off_shelf"] is False

    # 重复收藏 → 409（唯一索引 uq_fav_child_book + 活跃行判据）
    dup = _add(client, mini, c["id"], b1["id"])
    assert dup.status_code == 409 and "已收藏" in dup.json()["detail"], dup.text

    # 取消 → 列表移除；再收藏 → **复活**（软删行占唯一索引，B5/D-4 的修法在这里被钉住）
    rm = client.delete(
        f"/api/miniapp/favorites/{b1['id']}", params={"child_id": c["id"]}, headers=mini
    )
    assert rm.status_code == 200, rm.text
    assert [r["book_id"] for r in _favorites(client, mini, c["id"])] == [b2["id"]]
    again = _add(client, mini, c["id"], b1["id"])
    assert again.status_code == 200, again.text
    # 复活 = **原行复用**（`is_deleted` 翻回 0），id/created_at 都保留 → 列表回到**原位置**，
    # 而不是跳到最前。判据"按时间倒序"按**收藏时间**解释即自洽；"重新收藏应置顶"是另一种
    # 产品口径，未在 feature 明写 → 记为挂账（docs/09 第六十五轮），此处按现状钉死。
    assert [r["book_id"] for r in _favorites(client, mini, c["id"])] == [b2["id"], b1["id"]]


def test_favorites_off_shelf_visible_and_blocked(client: TestClient):
    """PRD §4.4：下架书在收藏夹**仍可见并标注已下架**，但借/约/听三处全拦。"""
    h = _h(client)
    c, mini = _child(client, h)
    book = _book(client, h, "下架演练书")
    assert _add(client, mini, c["id"], book["id"]).status_code == 200

    _off_shelf(client, h, book["id"])
    rows = _favorites(client, mini, c["id"])
    assert [(r["book_id"], r["off_shelf"]) for r in rows] == [(book["id"], True)], rows

    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()
    borrow = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copies[0]["id"]},
        headers=h,
    )
    assert borrow.status_code == 422 and "已下架" in borrow.json()["detail"], borrow.text

    res = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book["id"]}, headers=mini
    )
    assert res.status_code == 422 and "已下架" in res.json()["detail"], res.text

    perm = client.get(
        f"/api/miniapp/books/{book['id']}/audio-permission",
        params={"child_id": c["id"]},
        headers=mini,
    )
    assert perm.status_code == 200, perm.text
    assert perm.json() == {"allowed": False, "reason": "off_shelf"}, perm.json()


def test_favorites_withdrawn_readonly_per_r314(client: TestClient):
    """R-314：退会只读 = **列表放行**（四态皆可查看）+ 增删拒。"""
    from backend.database import get_session
    from backend.domain.identity.models import Child

    h = _h(client)
    c, mini = _child(client, h)
    book = _book(client, h, "退会演练书")
    assert _add(client, mini, c["id"], book["id"]).status_code == 200
    with get_session() as db:
        db.query(Child).filter(Child.id == c["id"]).update({"member_status": "withdrawn"})
        db.commit()

    assert [r["book_id"] for r in _favorites(client, mini, c["id"])] == [book["id"]]
    add = _add(client, mini, c["id"], _book(client, h, "另一本")["id"])
    assert add.status_code == 422, add.text
    assert add.json()["detail"] == "已退会，收藏夹只读", add.text
    rm = client.delete(
        f"/api/miniapp/favorites/{book['id']}", params={"child_id": c["id"]}, headers=mini
    )
    assert rm.status_code == 422 and rm.json()["detail"] == "已退会，收藏夹只读", rm.text
    assert [r["book_id"] for r in _favorites(client, mini, c["id"])] == [book["id"]]


def test_favorites_unpaid_allowed_and_quota_untouched(client: TestClient):
    """R-314：未缴费**可收藏**；收藏不占额度、不改借阅状态（PRD §8.2 表）。"""
    h = _h(client)
    c, mini = _child(client, h, member=False)
    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    quota_before = card["available_quota"]

    books = [_book(client, h, f"未缴费收藏书{i}") for i in range(3)]
    for b in books:
        assert _add(client, mini, c["id"], b["id"]).status_code == 200, b
    assert len(_favorites(client, mini, c["id"])) == 3

    card_after = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card_after["available_quota"] == quota_before, (quota_before, card_after)
    assert (
        client.get("/api/miniapp/borrows", params={"child_id": c["id"]}, headers=mini).json() == []
    )


def test_favorites_add_rejects_garbage_body_422_not_500(client: TestClient):
    """缺口探针（本批修）：非法入参必须 422——原先裸 `body: dict` + `int(...)` 会 500。"""
    h = _h(client)
    c, mini = _child(client, h)
    book = _book(client, h, "参数演练书")
    cases = [
        {"child_id": "abc", "book_id": book["id"]},
        {"child_id": c["id"], "book_id": "xyz"},
        {"child_id": c["id"]},
        {},
    ]
    for body in cases:
        r = client.post("/api/miniapp/favorites", json=body, headers=mini)
        assert r.status_code == 422, (body, r.status_code, r.text)
    # 非对象体（数组/字符串）同样归 422，不是 AttributeError→500
    for payload in ([1, 2, 3], "oops"):
        r = client.post("/api/miniapp/favorites", json=payload, headers=mini)
        assert r.status_code == 422, (payload, r.status_code, r.text)
    # 合法形状仍照常工作（类型化没有改掉正常路径）
    assert _add(client, mini, c["id"], book["id"]).status_code == 200
