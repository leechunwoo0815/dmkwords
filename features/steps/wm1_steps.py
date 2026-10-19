# features/steps/wm1_steps.py — WM1 平台基座 BDD 步骤（真实 API 链路）
"""覆盖：config.feature / rbac.feature / audit.feature 的 WM1 范围场景。"""

from behave import given, then, when  # type: ignore[import-not-found]

from features.environment import get_admin_headers, get_staff_headers


# ---------- 背景 ----------
@given('超管已登录后台 且存在配置项 "{key}" 当前值 {value}')
def step_admin_logged_in_with_config(context, key, value):
    context.admin_headers = get_admin_headers(context)
    resp = context.client.get("/api/admin/configs", headers=context.admin_headers)
    assert resp.status_code == 200
    config = next(c for c in resp.json() if c["config_key"] == key)
    assert config["config_value"] == value, f"{key} 期望 {value} 实际 {config['config_value']}"


# ---------- config.feature ----------
@when("超管打开配置管理页面")
def step_open_config_page(context):
    context.configs_resp = context.client.get("/api/admin/configs", headers=context.admin_headers)


@then("全部业务配置按分类展示 当前值与默认值并列")
def step_configs_listed(context):
    assert context.configs_resp.status_code == 200
    configs = context.configs_resp.json()
    assert len(configs) >= 29
    for c in configs:
        assert c["category"] and "config_value" in c and "default_value" in c
    categories = [c["category"] for c in configs]
    assert categories == sorted(categories)


@when('超管把 "{key}" 从 {old} 改为 {new} 并填写变更原因')
def step_update_config(context, key, old, new):
    reason = "测试"
    context.update_resp = context.client.put(
        f"/api/admin/configs/{key}",
        json={"value": new, "reason": reason},
        headers=context.admin_headers,
    )


@then("修改保存成功 新借阅校验立即按 {value} 执行")
def step_update_saved(context, value):
    assert context.update_resp.status_code == 200, context.update_resp.text
    resp = context.client.get("/api/admin/configs", headers=context.admin_headers)
    config = next(c for c in resp.json() if c["config_key"] == "borrow_limit")
    assert config["config_value"] == value


@then("变更记录操作人、时间、旧值、新值、原因")
def step_update_audited(context):
    resp = context.client.get(
        "/api/admin/audit-logs",
        params={"action": "config.update"},
        headers=context.admin_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, "缺少 config.update 审计记录"
    entry = items[0]
    assert entry["actor_name"] == "超级管理员"
    assert entry["created_at"]
    assert '"old": "30"' in entry["detail"]
    assert '"new": "20"' in entry["detail"]
    assert entry["reason"] == "测试"


@when('超管把数值型配置 "{key}" 提交为字符串 "{value}"')
def step_update_bad_type(context, key, value):
    context.bad_resp = context.client.put(
        f"/api/admin/configs/{key}",
        json={"value": value, "reason": "类型错误测试"},
        headers=context.admin_headers,
    )


@then("系统拒绝保存并提示类型错误")
def step_type_error_rejected(context):
    assert context.bad_resp.status_code == 422
    assert "类型" in context.bad_resp.json()["detail"]


@given("运营专员尝试修改任一配置")
def step_staff_try_update(context):
    context.staff_headers = get_staff_headers(context)
    context.staff_update_resp = context.client.put(
        "/api/admin/configs/borrow_limit",
        json={"value": "20", "reason": "越权"},
        headers=context.staff_headers,
    )
    context.forbidden_resp = context.staff_update_resp  # 供共享的「系统返回权限不足」断言取用


@given("馆员账号尝试审核一笔退款申请")
def step_staff_try_review_refund(context):
    """造一笔**待审退款单**，再让 staff01 去审 → 应被 `require_super_admin()` 拦。

    退款单走真实链路造：家长端申请（`POST /api/miniapp/refund-requests`）需要一笔已支付订单。
    """
    admin_headers = get_admin_headers(context)
    parent = context.client.post(
        "/api/admin/members/parents",
        json={"name": "越权家长", "phone": "13985000003"},
        headers=admin_headers,
    ).json()
    child = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": "越权孩"},
        headers=admin_headers,
    ).json()
    order = context.client.post(
        "/api/admin/orders",
        json={"child_id": child["id"], "order_type": "observation_fee"},
        headers=admin_headers,
    ).json()
    rc = context.client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=admin_headers,
    )
    assert rc.status_code == 200, rc.text
    mini = {
        "Authorization": "Bearer "
        + context.client.post(
            "/api/miniapp/login", json={"phone": "13985000003", "code": "1234"}
        ).json()["token"]
    }
    rr = context.client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": child["id"], "order_id": order["id"], "reason": "越权用例：待审退款"},
        headers=mini,
    )
    assert rr.status_code == 200, rr.text
    context.forbidden_resp = context.client.post(
        f"/api/admin/refund-requests/{rr.json()['id']}/review",
        json={"approve": True, "remark": "馆员越权审核"},
        headers=get_staff_headers(context),
    )


@then("系统返回权限不足")
def step_forbidden(context):
    """共享断言：**按 `context.forbidden_resp`** 判（各越权 Given 各自设置）。

    （原实现写死 `context.staff_update_resp`——只能服务"专员改配置"那一条；rbac 域解封时两条新场景
    各有自己的越权请求，故收敛成单一取用点。）
    """
    resp = getattr(context, "forbidden_resp", None)
    assert resp is not None, "该场景没有设置 forbidden_resp（越权请求未发出）"
    assert resp.status_code == 403, f"越权应 403，实 {resp.status_code} {resp.text[:120]}"


@given("家长 A 尝试查询家长 B 的孩子数据")  # feature 里是「假如」（Given）——首轮实测踩到
def step_parent_cross_child(context):
    """两个家庭各建一个孩子；家长 A 拿自己的 token 去取家长 B 的孩子数据（跨租户取数）。"""
    admin_headers = get_admin_headers(context)
    fams = []
    for suffix, name in (("13985000004", "A孩"), ("13985000005", "B孩")):
        p = context.client.post(
            "/api/admin/members/parents",
            json={"name": f"家长{suffix[-1]}", "phone": suffix},
            headers=admin_headers,
        ).json()
        c = context.client.post(
            f"/api/admin/members/parents/{p['id']}/children",
            json={"name": name},
            headers=admin_headers,
        ).json()
        token = context.client.post(
            "/api/miniapp/login", json={"phone": suffix, "code": "1234"}
        ).json()["token"]
        fams.append({"parent": p, "child": c, "headers": {"Authorization": f"Bearer {token}"}})
    context.fam_a, context.fam_b = fams[0], fams[1]
    # 家长 A 用**自己**的 token 指定家长 B 的 child_id（两处代表性读/写入口）
    context.cross_list = context.client.get(
        "/api/miniapp/children", headers=context.fam_a["headers"]
    )
    context.cross_write = context.client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": context.fam_b["child"]["id"],
            "book_id": 1,
            "position": 1,
            "session_start": 0,
        },
        headers=context.fam_a["headers"],
    )


@then("系统拒绝访问")
def step_cross_tenant_denied(context):
    """跨孩子取数必须**双证据**拒绝：① 读列表里没有别人的孩子；② 写接口非 2xx。"""
    assert context.cross_list.status_code == 200, context.cross_list.text
    payload = context.cross_list.json()
    # 该端点返回 `{"children": [...]}`（与登录载荷同源）——首轮实测按裸列表取值 TypeError
    rows = payload["children"] if isinstance(payload, dict) else payload
    ids = [c["id"] for c in rows]
    assert context.fam_b["child"]["id"] not in ids, f"家长 A 的列表里出现了家长 B 的孩子：{ids}"
    assert context.fam_a["child"]["id"] in ids, f"自己的孩子在列表里（否则是空列表假绿）：{ids}"
    # 写接口必须拒，且**必须是归属校验那一条**（422「孩子不存在」）——不能只断言"非 2xx"：
    # 首轮实测写死 `>= 400` 时，去掉归属校验的变异**照样绿**（别的错误也满足非 2xx）= 假绿。
    assert context.cross_write.status_code == 422, (
        f"跨孩子写入应被归属校验拒（422），实 {context.cross_write.status_code} {context.cross_write.text[:120]}"
    )
    assert "孩子不存在" in context.cross_write.json().get("detail", ""), context.cross_write.text[
        :200
    ]


@when("配置被修改后")
def step_config_modified(context):
    from backend.database import get_session
    from backend.domain.admin.service import ConfigService, invalidate_config_cache

    invalidate_config_cache()
    db = get_session()
    try:
        svc = ConfigService(db)
        assert svc.get_value("quiz_max_attempts") == "3"  # 先读进缓存
    finally:
        db.close()
    context.update_resp = context.client.put(
        "/api/admin/configs/quiz_max_attempts",
        json={"value": "5", "reason": "缓存失效验证"},
        headers=context.admin_headers,
    )
    assert context.update_resp.status_code == 200


@then("服务端缓存失效 下次读取返回新值")
def step_cache_invalidated(context):
    from backend.database import get_session
    from backend.domain.admin.service import ConfigService

    db = get_session()
    try:
        assert ConfigService(db).get_value("quiz_max_attempts") == "5"
    finally:
        db.close()


# ---------- rbac.feature ----------
@then("系统仅存在超级管理员与会员运营专员两种角色")
def step_two_roles(context):
    admin_headers = get_admin_headers(context)
    staff_headers = get_staff_headers(context)
    admin_role = context.client.get("/api/admin/me", headers=admin_headers).json()["user"]["role"]
    staff_role = context.client.get("/api/admin/me", headers=staff_headers).json()["user"]["role"]
    assert {admin_role, staff_role} == {"superadmin", "staff"}


@then("馆员可以借还续借核销、管理图书音频题目、办理会员、发布活动、跟进逾期、放行留痕")
def step_staff_permissions(context):
    staff_headers = get_staff_headers(context)
    perms = context.client.get("/api/admin/me", headers=staff_headers).json()["permissions"]
    for code in [
        "borrow.operate",
        "book.manage",
        "audio.manage",
        "quiz.manage",
        "member.manage",
        "activity.manage",
    ]:
        assert code in perms, f"staff 缺少权限码 {code}"


@then("退款审核、退会审核、转让审核、测验次数重置、价格规则调整、员工账号管理仅超管可操作")
def step_super_only_permissions(context):
    from backend.domain.admin.service import role_has_permission

    for code in [
        "refund.audit",
        "withdrawal.audit",
        "transfer.audit",
        "quiz.reset",
        "config.update",
        "user.manage",
    ]:
        assert role_has_permission("superadmin", code), f"超管应有权 {code}"
        assert not role_has_permission("staff", code), f"馆员不应有权 {code}"


@then("接口权限通过声明式依赖校验")
def step_declarative_auth(context):
    staff_headers = get_staff_headers(context)
    resp = context.client.get("/api/admin/audit-logs", headers=staff_headers)
    assert resp.status_code == 403  # require_perm 依赖注入生效


@then("前端路由守卫、后端接口、菜单显隐三处权限码一致")
def step_perm_codes_consistent(context):
    from backend.domain.admin.service import STAFF_PERMISSIONS, permissions_for_role

    staff_headers = get_staff_headers(context)
    admin_headers = get_admin_headers(context)
    staff_me = context.client.get("/api/admin/me", headers=staff_headers).json()
    admin_me = context.client.get("/api/admin/me", headers=admin_headers).json()
    # /me 下发的码与权限目录（后端 require_perm / 前端菜单同一数据源）完全一致
    assert staff_me["permissions"] == STAFF_PERMISSIONS
    assert admin_me["permissions"] == permissions_for_role("superadmin")
    # 前端菜单引用的码在目录中有定义（无死码）
    assert "config.view" in staff_me["permissions"]


# ---------- audit.feature ----------
@when("超管修改任一系统配置")
def step_admin_modify_config(context):
    context.audit_update_resp = context.client.put(
        "/api/admin/configs/borrow_days",
        json={"value": "45", "reason": "审计留痕验证"},
        headers=get_admin_headers(context),
    )
    assert context.audit_update_resp.status_code == 200


@then("日志记录旧值、新值与原因")
def step_log_records_change(context):
    admin_headers = get_admin_headers(context)
    resp = context.client.get(
        "/api/admin/audit-logs", params={"action": "config.update"}, headers=admin_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, "无配置变更日志"
    entry = items[0]
    assert '"old": "30"' in entry["detail"]
    assert '"new": "45"' in entry["detail"]
    assert entry["reason"] == "审计留痕验证"
    assert entry["created_at"]


@then("操作日志对任何角色均为只读 不可编辑或删除")
def step_logs_readonly(context):
    admin_headers = get_admin_headers(context)
    resp_del = context.client.delete("/api/admin/audit-logs/1", headers=admin_headers)
    resp_put = context.client.put(
        "/api/admin/audit-logs/1", json={"reason": "篡改"}, headers=admin_headers
    )
    assert resp_del.status_code in (404, 405)
    assert resp_put.status_code in (404, 405)


@when("馆员放行一笔异常借书并填写原因")
def step_borrow_with_override(context):
    """造一笔**带异常快照**的放行借书：未入会孩子 + 馆员填原因放行（`override_reason`）。

    异常快照 = 借书时服务端返回的 `warnings`（未入会临时借书 / 押金未缴…）——它必须原样进审计 detail，
    否则事后复盘看不出"当时到底异常在哪"（红线：人工放行边界）。
    """
    from features.steps.borrowing_steps import _book_with_copies, _borrow, _set_config

    context.h = get_admin_headers(context)
    # 未入会孩子要能借出，必须先开"未入会临时借书"开关（默认 false → 服务端直接拒）
    _set_config(context, "allow_unpaid_offline_borrow", "true")
    parent = context.client.post(
        "/api/admin/members/parents",
        json={"name": "放行家长", "phone": "13985000001"},
        headers=context.h,
    ).json()
    child = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": "放行孩"},
        headers=context.h,
    ).json()
    _book_id, copy_ids = _book_with_copies(context, "放行留痕书")
    context.borrow_reason = "家长临时体验，明日入会"
    r = _borrow(context, child["id"], copy_id=copy_ids[0], override=context.borrow_reason)
    assert r.status_code == 200, r.text
    context.borrow_resp = r.json()
    assert context.borrow_resp.get("warnings"), (
        f"该笔借书应有异常快照（warnings 非空）——否则用例前提不成立：{context.borrow_resp}"
    )


def _audit_items(context, *, action: str) -> list[dict]:
    resp = context.client.get(
        "/api/admin/audit-logs", params={"action": action}, headers=get_admin_headers(context)
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items, f"没有 {action} 的审计日志"
    return items


@then("日志记录操作人、时间、对象、动作、原因与当时异常快照")
def step_audit_borrow_override(context):
    import json

    entry = _audit_items(context, action="circulation.borrow")[0]
    assert entry["actor_name"], f"缺操作人：{entry}"
    assert entry["created_at"], f"缺时间：{entry}"
    assert entry["target_type"] == "borrow" and entry["target_id"], f"缺对象：{entry}"
    assert entry["reason"] == context.borrow_reason, f"原因应原样留痕：{entry['reason']}"
    detail = json.loads(entry["detail"])
    assert detail["warnings"] == context.borrow_resp["warnings"], (
        f"异常快照必须与借书当时一致：日志={detail['warnings']} 当时={context.borrow_resp['warnings']}"
    )
    assert detail["child"] and detail["book"] and detail["copy"], f"detail 缺业务要素：{detail}"


@when("任一退款、押金扣除、人工收款确认发生")
def step_three_fund_actions(context):
    """三种资金操作各真做一笔（走真实 API）：① 人工收款确认 ② 押金扣除 ③ 退款审核。

    各自记录**服务端返回的金额**到 context，供断言步骤比对（不写死，防口径漂移）。
    """
    context.h = get_admin_headers(context)
    parent = context.client.post(
        "/api/admin/members/parents",
        json={"name": "资金家长", "phone": "13985000002"},
        headers=context.h,
    ).json()
    child = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": "资金孩"},
        headers=context.h,
    ).json()
    cid = child["id"]

    # ① 人工收款确认（观察期费 500 → paid）
    order = context.client.post(
        "/api/admin/orders",
        json={"child_id": cid, "order_type": "observation_fee"},
        headers=context.h,
    ).json()
    rc = context.client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "审计留痕：人工收款"},
        headers=context.h,
    )
    assert rc.status_code == 200, rc.text
    context.paid_order = order

    # ② 押金扣除（缴 1200 → 扣 100 作赔偿）
    dep = context.client.post(
        f"/api/admin/deposits/children/{cid}/orders", headers=context.h
    ).json()
    dep_id = dep.get("id") or dep["order_id"]
    rc2 = context.client.post(
        f"/api/admin/orders/{dep_id}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert rc2.status_code == 200, rc2.text
    ded = context.client.post(
        f"/api/admin/deposits/children/{cid}/deduct",
        json={"amount": "100.00", "reason": "审计留痕：赔偿扣除"},
        headers=context.h,
    )
    assert ded.status_code == 200, ded.text
    context.deposit_after = ded.json()

    # ③ 退款（家长申请 → 超管审核通过）
    mini = {
        "Authorization": "Bearer "
        + context.client.post(
            "/api/miniapp/login", json={"phone": "13985000002", "code": "1234"}
        ).json()["token"]
    }
    rr = context.client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": cid, "order_id": order["id"], "reason": "审计留痕：退款"},
        headers=mini,
    )
    assert rr.status_code == 200, rr.text
    context.refund_req = rr.json()
    rv = context.client.post(
        f"/api/admin/refund-requests/{context.refund_req['id']}/review",
        json={"approve": True, "remark": "审计留痕：同意退款"},
        headers=context.h,
    )
    assert rv.status_code == 200, rv.text


@then("日志记录操作人、时间、对象、动作与金额明细")
def step_audit_fund_details(context):
    """三类资金操作各自的留痕字段（**量化值必须与业务返回值一致**）。

    「变更前后值」的落地口径（本批解封时按现行实现如实标注）：日志里记的是**量化结果**
    （金额/实扣/待结清/入账后会员态），单据自身的前后状态流转由业务表承载（orders.status /
    deposits.available_amount 等）；PRD §11.1 的留痕要求原文是「谁/时间/对谁/做了什么/原因」。
    """
    import json
    from decimal import Decimal

    def _money(a, b, what: str) -> None:
        """金额按 **Decimal 精确比**（同一笔钱两处字符串形态可能不同：建单返回 `"500"`、
        留痕 detail 是 `"500.00"`——首轮实测撞到；比 Decimal 既精确又不误报）。"""
        assert Decimal(str(a)) == Decimal(str(b)), f"{what} 金额不一致：日志={a} 业务={b}"

    def _entry(action: str, target_id: str | None = None) -> dict:
        """按动作取日志；给了 target_id 就**精确取那一笔**（同一动作本场景发生两次：
        观察期费与押金的收款确认——不按对象取会撞到押金那笔，首轮实测踩到）。"""
        items = _audit_items(context, action=action)
        if target_id is not None:
            items = [e for e in items if e["target_id"] == target_id]
            assert items, f"{action} 没有 target_id={target_id} 的日志"
        e = items[0]
        assert e["actor_name"], f"缺操作人：{e}"
        assert e["created_at"], f"缺时间：{e}"
        assert e["target_type"] and e["target_id"], f"缺对象：{e}"
        return e

    # ① 人工收款确认：金额 + 方式 + 入账后会员态（按订单号取，避开押金那笔）
    pay = _entry("order.confirm_payment", context.paid_order["order_no"])
    pd = json.loads(pay["detail"])
    _money(pd["amount"], context.paid_order["amount"], "人工收款")
    assert pd["method"] == "scan", pd
    assert pd["member_status"] == "observation", f"入账后会员态应留痕：{pd}"
    assert pay["target_type"] == "order" and pay["target_id"] == context.paid_order["order_no"], pay

    # ② 押金扣除：索赔额 / 实扣 / 待结清
    dd = json.loads(_entry("deposit.deduct")["detail"])
    assert dd["amount"] == "100.00", dd
    assert dd["deducted"] == "100.00", dd
    assert dd["unpaid"] == "0.00", dd
    assert dd["deducted"] == context.deposit_after["deducted_amount"], (dd, context.deposit_after)

    # ③ 退款审核：结论 / 金额 / 类型
    rv = _entry("refund.review")
    rd = json.loads(rv["detail"])
    assert rd["approve"] is True, rd
    _money(rd["amount"], context.refund_req["amount"], "退款审核")
    assert rv["reason"] == "审计留痕：同意退款", rv["reason"]
