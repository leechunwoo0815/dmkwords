# features/steps/wm3_steps.py — WM3-B1 家长/孩子编辑删除（订单守卫）BDD 步骤（真实 API 链路）

from behave import given, then, when  # type: ignore[import-not-found]

from features.environment import get_admin_headers


def _mk_parent(context, name: str, phone: str) -> dict:
    resp = context.client.post(
        "/api/admin/members/parents",
        json={"name": name, "phone": phone},
        headers=context.admin_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_child(context, parent_id: int, name: str) -> dict:
    resp = context.client.post(
        f"/api/admin/members/parents/{parent_id}/children",
        json={"name": name},
        headers=context.admin_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_order(context, child_id: int) -> dict:
    resp = context.client.post(
        "/api/admin/orders",
        json={"child_id": child_id, "order_type": "observation_fee"},
        headers=context.admin_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _find_child(context, name: str) -> dict:
    resp = context.client.get(
        "/api/admin/members/children?page=1&page_size=100", headers=context.admin_headers
    )
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"孩子 {name} 不在列表中")


@given('已存在家长账号 "{name}" 手机号 "{phone}" 且名下孩子 "{child}" 无任何订单')
def step_impl(context, name: str, phone: str, child: str) -> None:
    context.admin_headers = get_admin_headers(context)
    context.wm3_parent = _mk_parent(context, name, phone)
    context.wm3_child = _mk_child(context, context.wm3_parent["id"], child)


@given('已存在家长账号 "{name}" 手机号 "{phone}" 且名下孩子 "{child}" 已创建订单')
def step_impl_with_order(context, name: str, phone: str, child: str) -> None:
    context.admin_headers = get_admin_headers(context)
    context.wm3_parent = _mk_parent(context, name, phone)
    context.wm3_child = _mk_child(context, context.wm3_parent["id"], child)
    _mk_order(context, context.wm3_child["id"])


@when('超管编辑孩子 "{child}" 姓名改为 "{new_name}" 生日 "{birthday}"')
def step_edit_child(context, child: str, new_name: str, birthday: str) -> None:
    found = _find_child(context, child)
    context.resp = context.client.put(
        f"/api/admin/members/children/{found['id']}",
        json={"name": new_name, "birthday": birthday},
        headers=context.admin_headers,
    )


@when('超管尝试编辑孩子 "{child}" 姓名改为 "{new_name}"')
def step_try_edit_child(context, child: str, new_name: str) -> None:
    found = _find_child(context, child)
    context.resp = context.client.put(
        f"/api/admin/members/children/{found['id']}",
        json={"name": new_name},
        headers=context.admin_headers,
    )


@when('超管尝试删除孩子 "{child}"')
def step_try_delete_child(context, child: str) -> None:
    found = _find_child(context, child)
    context.resp = context.client.delete(
        f"/api/admin/members/children/{found['id']}", headers=context.admin_headers
    )


@when('超管尝试删除家长 "{name}"')
def step_try_delete_parent(context, name: str) -> None:
    resp = context.client.get(
        "/api/admin/members/parents-page?page=1&page_size=100", headers=context.admin_headers
    )
    target = next((i for i in resp.json()["items"] if i["name"] == name), None)
    assert target is not None, f"家长 {name} 不在列表"
    context.resp = context.client.delete(
        f"/api/admin/members/parents/{target['id']}", headers=context.admin_headers
    )


@when('超管编辑家长 "{name}" 手机号改为 "{new_phone}"')
def step_edit_parent_phone(context, name: str, new_phone: str) -> None:
    resp = context.client.get(
        "/api/admin/members/parents-page?page=1&page_size=100", headers=context.admin_headers
    )
    target = next((i for i in resp.json()["items"] if i["name"] == name), None)
    assert target is not None, f"家长 {name} 不在列表"
    context.resp = context.client.patch(
        f"/api/admin/members/parents/{target['id']}",
        json={"phone": new_phone},
        headers=context.admin_headers,
    )


@then("编辑保存成功 且孩子列表显示新姓名与生日")
def step_edit_saved(context) -> None:
    assert context.resp.status_code == 200, context.resp.text
    found = _find_child(context, "改好的名字")
    assert found["birthday"] == "2020-05-01"


@then("修改被拒绝返回 409 且提示已创建订单禁止修改")
def step_edit_rejected(context) -> None:
    assert context.resp.status_code == 409, context.resp.text
    assert "订单" in context.resp.json()["detail"]


@then("删除被拒绝返回 409 且孩子档案仍然可见")
def step_delete_child_rejected(context) -> None:
    assert context.resp.status_code == 409, context.resp.text
    assert "订单" in context.resp.json()["detail"]
    _find_child(context, "编辑孩B")  # 仍在列表


@then("删除被拒绝返回 409 且家长仍出现在家长列表")
def step_delete_parent_rejected(context) -> None:
    assert context.resp.status_code == 409, context.resp.text
    resp = context.client.get(
        "/api/admin/members/parents-page?page=1&page_size=100", headers=context.admin_headers
    )
    assert any(i["name"] == "编辑家长C" for i in resp.json()["items"])


@then("修改被拒绝 且提示手机号已存在")
def step_phone_rejected(context) -> None:
    assert context.resp.status_code in (409, 422), context.resp.text
    assert "手机号" in context.resp.json()["detail"]


# ---- F7 守卫口径细化（身份锁/学籍放）----


@then("编辑保存成功 且孩子列表显示新年级")
def step_edit_school_saved(context) -> None:
    assert context.resp.status_code == 200, context.resp.text
    found = _find_child(context, "学籍孩A")
    assert found["grade"] == "二年级"


@then("修改被拒绝返回 409 且提示身份字段锁定")
def step_edit_identity_rejected(context) -> None:
    assert context.resp.status_code == 409, context.resp.text
    assert "身份字段" in context.resp.json()["detail"]


@when('超管编辑孩子 "{child}" 年级改为 "{grade}"')
def step_edit_child_grade(context, child: str, grade: str) -> None:
    found = _find_child(context, child)
    context.resp = context.client.put(
        f"/api/admin/members/children/{found['id']}",
        json={"grade": grade},
        headers=context.admin_headers,
    )


@then("家长编辑保存成功")
def step_parent_edit_saved(context) -> None:
    assert context.resp.status_code == 200, context.resp.text
