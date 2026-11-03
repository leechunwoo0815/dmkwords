# tests/unit/test_member_add_child_with_orders_20261103.py — 有订单的家长仍可加孩子
"""甲方报障：家长名下孩子已创建订单 → 家长「编辑」被禁用，于是没有入口再添加孩子。

口径：订单守卫只限制**改家长资料/删家长**（手机号是登录标识、删除会牵动历史），
**不限制新增孩子**——`POST /members/parents/{id}/children` 本就无订单守卫。
本判据锁死 API 行为，防止未来有人把家长级守卫误加到建档入口。
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _h


def test_add_child_allowed_when_parent_has_orders(client: TestClient):
    h = _h(client)
    p = client.post(
        "/api/admin/members/parents",
        json={"name": "有单家长", "phone": "13800001901"},
        headers=h,
    ).json()
    first = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": "老大"},
        headers=h,
    ).json()
    order = client.post(
        "/api/admin/orders",
        json={"child_id": first["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    paid = client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    assert paid.status_code == 200, paid.text

    # 家长级订单守卫仍生效：编辑家长资料 409
    blocked = client.patch(
        f"/api/admin/members/parents/{p['id']}",
        json={"remark": "想改备注"},
        headers=h,
    )
    assert blocked.status_code == 409, blocked.text

    # 新增孩子不受影响（本次修复的核心）
    second = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": "老二"},
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert second.json()["parent_id"] == p["id"]

    kids = client.get(f"/api/admin/members/parents/{p['id']}/children", headers=h).json()
    assert {k["name"] for k in kids} == {"老大", "老二"}
