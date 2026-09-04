# tests/unit/test_wm3_member_edit.py — WM3-B1 家长/孩子编辑+删除（订单守卫双保险）
#
# 用户拍板口径（2026-09-01）："还没创建用户订单，创建订单以后禁止删除和修改"
# 守卫：
#   - 孩子：存在任何未删订单（任意状态含 cancelled）→ 409 禁改禁删
#   - 家长：名下任一孩子存在订单 → 409 禁改禁删（Order.parent_id 冗余直查）
#   - 家长手机号唯一（与新建同规 422/409）；手机号是小程序登录标识

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _parent(client, h, phone="13800002001", name="编辑测试家长") -> dict:
    r = client.post("/api/admin/members/parents", json={"name": name, "phone": phone}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _child(client, h, parent_id: int, name="编辑测试孩") -> dict:
    r = client.post(
        f"/api/admin/members/parents/{parent_id}/children", json={"name": name}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _order(client, h, child_id: int, order_type="observation_fee") -> dict:
    r = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_b1_edit_child_without_orders_ok(client: TestClient):
    """编辑无订单孩子 → 200 且字段落库（姓名/性别/生日/年级全开）。"""
    h = _h(client)
    p = _parent(client, h, "13800002001")
    c = _child(client, h, p["id"], "打错字孩")
    r = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"name": "改对的字", "gender": 1, "birthday": "2020-05-01", "grade": "一年级"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "改对的字"
    assert body["gender"] == 1
    assert body["birthday"] == "2020-05-01"
    assert body["grade"] == "一年级"
    # 列表回读确认落库
    lr = client.get("/api/admin/members/children?keyword=改对的字&page=1&page_size=20", headers=h)
    assert lr.status_code == 200
    assert any(i["name"] == "改对的字" for i in lr.json()["items"])


def test_b1_edit_child_with_orders_rejected(client: TestClient):
    """编辑有订单孩子 → 409（用户口径：创建订单后禁改禁删）。"""
    h = _h(client)
    p = _parent(client, h, "13800002002")
    c = _child(client, h, p["id"])
    _order(client, h, c["id"])
    r = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"name": "试图改", "grade": "二年级"},
        headers=h,
    )
    assert r.status_code == 409, r.text
    assert "订单" in r.json()["detail"]


def test_b1_delete_parent_with_orders_rejected(client: TestClient):
    """删除有订单家长 → 409（名下任一孩子存在订单，Order.parent_id 冗余直查）。"""
    h = _h(client)
    p = _parent(client, h, "13800002003")
    c = _child(client, h, p["id"])
    _order(client, h, c["id"])
    r = client.delete(f"/api/admin/members/parents/{p['id']}", headers=h)
    assert r.status_code == 409, r.text
    assert "订单" in r.json()["detail"]


def test_b1_delete_parent_without_orders_soft_deleted(client: TestClient):
    """删除无订单家长 → 200 软删，列表消失。"""
    h = _h(client)
    p = _parent(client, h, "13800002004")
    c = _child(client, h, p["id"])
    # 名下孩子无订单，但孩子档案随家长一起软删（孤儿档案必须清）
    r = client.delete(f"/api/admin/members/parents/{p['id']}", headers=h)
    assert r.status_code == 200, r.text
    lr = client.get("/api/admin/members/parents-page?page=1&page_size=50", headers=h)
    assert lr.status_code == 200
    assert all(i["id"] != p["id"] for i in lr.json()["items"])
    # 孩子列表也不可见
    cr = client.get("/api/admin/members/children?page=1&page_size=50", headers=h)
    assert all(i["id"] != c["id"] for i in cr.json()["items"])


def test_b1_delete_child_with_orders_rejected(client: TestClient):
    """删除有订单孩子 → 409。"""
    h = _h(client)
    p = _parent(client, h, "13800002005")
    c = _child(client, h, p["id"])
    _order(client, h, c["id"])
    r = client.delete(f"/api/admin/members/children/{c['id']}", headers=h)
    assert r.status_code == 409, r.text


def test_b1_delete_child_without_orders_ok(client: TestClient):
    """删除无订单孩子 → 200 软删，列表消失（家长保留）。"""
    h = _h(client)
    p = _parent(client, h, "13800002006")
    c = _child(client, h, p["id"])
    r = client.delete(f"/api/admin/members/children/{c['id']}", headers=h)
    assert r.status_code == 200, r.text
    cr = client.get("/api/admin/members/children?page=1&page_size=50", headers=h)
    assert all(i["id"] != c["id"] for i in cr.json()["items"])


def test_b1_update_parent_phone_duplicate_rejected(client: TestClient):
    """家长手机号改成已存在号 → 422（唯一性校验与新建同规）。"""
    h = _h(client)
    p1 = _parent(client, h, "13800002007", "家长甲")
    p2 = _parent(client, h, "13800002008", "家长乙")
    r = client.patch(
        f"/api/admin/members/parents/{p2['id']}",
        json={"name": "家长乙改名", "phone": p1["phone"]},
        headers=h,
    )
    assert r.status_code in (409, 422), r.text


def test_b1_update_parent_ok_and_guard(client: TestClient):
    """编辑无订单家长 → 200；名下孩子建档后有订单 → 禁改 409。"""
    h = _h(client)
    p = _parent(client, h, "13800002009", "待改名字长")
    r = client.patch(
        f"/api/admin/members/parents/{p['id']}",
        json={"name": "改好的名字", "phone": p["phone"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "改好的名字"
    c = _child(client, h, p["id"])
    _order(client, h, c["id"])
    r2 = client.patch(f"/api/admin/members/parents/{p['id']}", json={"name": "再改一次"}, headers=h)
    assert r2.status_code == 409, r2.text


def test_b1_parents_page_has_orders_flag(client: TestClient):
    """家长分页列表：children_count / has_orders 标志（前端禁用按钮数据源）。"""
    h = _h(client)
    p1 = _parent(client, h, "13800002010", "无单家长")
    _child(client, h, p1["id"])
    p2 = _parent(client, h, "13800002011", "有单家长")
    c2 = _child(client, h, p2["id"])
    _order(client, h, c2["id"])
    lr = client.get("/api/admin/members/parents-page?page=1&page_size=50", headers=h)
    assert lr.status_code == 200, lr.text
    rows = {i["id"]: i for i in lr.json()["items"]}
    assert rows[p1["id"]]["children_count"] == 1
    assert rows[p1["id"]]["has_orders"] is False
    assert rows[p2["id"]]["children_count"] == 1
    assert rows[p2["id"]]["has_orders"] is True


def test_b1_child_list_has_orders_flag(client: TestClient):
    """孩子列表 has_orders 标志（孩子行删除按钮禁用数据源）。"""
    h = _h(client)
    p = _parent(client, h, "13800002012")
    c1 = _child(client, h, p["id"], "无单孩")
    c2 = _child(client, h, p["id"], "有单孩")
    _order(client, h, c2["id"])
    lr = client.get("/api/admin/members/children?page=1&page_size=50", headers=h)
    rows = {i["id"]: i for i in lr.json()["items"]}
    assert rows[c1["id"]]["has_orders"] is False
    assert rows[c2["id"]]["has_orders"] is True


# ---- F7 守卫口径细化（用户拍板 2026-09-01）：身份字段锁 / 学籍字段放 ----


def test_f7_child_with_orders_can_update_school_fields(client: TestClient):
    """有订单孩子 PATCH 仅 grade → 200 落库（学籍动态字段放开）。"""
    h = _h(client)
    p = _parent(client, h, "13800003001")
    c = _child(client, h, p["id"])
    _order(client, h, c["id"])
    r = client.put(f"/api/admin/members/children/{c['id']}", json={"grade": "二年级"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["grade"] == "二年级"


def test_f7_child_with_orders_name_still_rejected(client: TestClient):
    """有订单孩子 PATCH 含 name（身份字段）→ 409（守卫细化不破）。"""
    h = _h(client)
    p = _parent(client, h, "13800003002")
    c = _child(client, h, p["id"])
    _order(client, h, c["id"])
    r = client.put(f"/api/admin/members/children/{c['id']}", json={"name": "试图改"}, headers=h)
    assert r.status_code == 409, r.text
    assert "身份字段" in r.json()["detail"]


def test_f7_child_with_orders_english_and_ar_upgrade_ok(client: TestClient):
    """有订单孩子 PATCH english_name+ar_level（升级值）→ 200。"""
    h = _h(client)
    p = _parent(client, h, "13800003003")
    c = _child(client, h, p["id"])
    _order(client, h, c["id"])
    r = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"english_name": "NewName", "ar_level": "3.5"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["english_name"] == "NewName"
    assert r.json()["ar_level"] == "3.5"


def test_f7_child_with_orders_birthday_identity_rejected(client: TestClient):
    """有订单孩子 PATCH 含 birthday（身份字段）→ 409。"""
    h = _h(client)
    p = _parent(client, h, "13800003004")
    c = _child(client, h, p["id"])
    _order(client, h, c["id"])
    r = client.put(
        f"/api/admin/members/children/{c['id']}", json={"birthday": "2020-01-01"}, headers=h
    )
    assert r.status_code == 409, r.text


# ---- F2 家长 tab 创建时间（后端 schema 漏字段，前端列已就绪） ----


def test_f2_parents_page_includes_create_time(client: TestClient):
    """parents-page 响应含 create_time 且为 ISO 串（前端创建时间列数据源）。"""
    h = _h(client)
    _parent(client, h, "13800004001")
    lr = client.get("/api/admin/members/parents-page?page=1&page_size=20", headers=h)
    assert lr.status_code == 200, lr.text
    row = lr.json()["items"][0]
    assert "create_time" in row, f"create_time 缺失: {row.keys()}"
    assert isinstance(row["create_time"], str) and len(row["create_time"]) >= 19


# ---- B6（插修5）：守卫判"值变更"而非"字段提交"——全量表单原样回传不误拦 ----


def test_b6_with_orders_same_name_plus_grade_ok(client: TestClient):
    """有订单孩 PATCH name=原名 + grade=新值（全量表单场景）→ 200（当前 409 = RED）。"""
    h = _h(client)
    p = _parent(client, h, "13800003005")
    c = _child(client, h, p["id"], "原样回传孩")
    _order(client, h, c["id"])
    r = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"name": "原样回传孩", "grade": "二年级"},
        headers=h,
    )
    assert r.status_code == 200, (
        f"身份字段原值回传+改学籍字段应 200，实 {r.status_code} {r.text[:80]}（RED=提交≠变更误拦）"
    )
    assert r.json()["grade"] == "二年级"


def test_b6_with_orders_new_name_mixed_still_rejected(client: TestClient):
    """有订单孩 PATCH name=新值 + grade → 409（真变更仍拦）。"""
    h = _h(client)
    p = _parent(client, h, "13800003006")
    c = _child(client, h, p["id"], "真改名孩")
    _order(client, h, c["id"])
    r = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"name": "真的改了", "grade": "三年级"},
        headers=h,
    )
    assert r.status_code == 409, r.text
    assert "身份字段" in r.json()["detail"]


def test_b6_without_orders_full_form_ok(client: TestClient):
    """无订单孩全量表单（身份+学籍全提交）→ 200 回归。"""
    h = _h(client)
    p = _parent(client, h, "13800003007")
    c = _child(client, h, p["id"], "全表单孩")
    r = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"name": "全表单改名", "gender": 1, "birthday": "2021-06-01", "grade": "一年级"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "全表单改名"
