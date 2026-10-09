# features/steps/refund_steps.py — FEAT-020/029 用户端退款 步骤定义
"""2026-10-09（第四十轮）BDD 解封：**退款域** 5 个场景（资金链，与第三十八轮 P0 修复同源）。

为什么先解封这一域：退款是"钱往回走"的链路，第三十八轮刚修完四条 P0（99 元资格、
押金旁道、5xx 未知态、押金负余额），本域的场景把这批口径钉在**端到端行为**上：
申请（金额由服务端算）→ 重复申请被拒 → 撤销恢复 → 拒绝后可再申请 → 审核通过原路退回。

走真实链路（真实 MySQL + TestClient）：建档 → 绑 openid → 小程序下单 → mock 通道支付成功
→ 家长申请 → 超管审核/执行。未实现的场景（零金额禁提交 / 前端不自行算金额 / 线下登记）
仍标 `@draft`，由单测覆盖（如实标注，不假装全绿）。
"""

from decimal import Decimal

from behave import given, then, when

from tests.unit.test_wm10_concurrency import _db, _h


def _family_named(client, h, phone: str, parent_name: str, child_name: str):
    """建档（家长名/孩子名按场景给）+ 家长登录态（小程序 token）。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": parent_name, "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": child_name}, headers=h
    ).json()
    login = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    return p, c, {"Authorization": f"Bearer {login['token']}"}


def _bind_openid(child_parent_id: int, openid: str) -> None:
    from backend.domain.identity.models import Parent

    with _db() as db:
        p = db.query(Parent).filter(Parent.id == child_parent_id).first()
        p.wechat_openid = openid
        db.commit()


def _order_row(order_id: int):
    from backend.domain.identity.models import Order

    with _db() as db:
        return db.query(Order).filter(Order.id == order_id).first()


def _refund_row(refund_id: int):
    from backend.domain.identity.models import RefundRequest

    with _db() as db:
        return db.query(RefundRequest).filter(RefundRequest.id == refund_id).first()


def _apply(context) -> dict:
    r = context.client.post(
        "/api/miniapp/refund-requests",
        json={
            "child_id": context.child["id"],
            "order_id": context.order["id"],
            "reason": "孩子时间不够",
        },
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 背景 ----------


@given('家长 "{parent_name}" 名下孩子 "{child_name}" 有一笔已支付的年费订单 实付 {amount} 元')
def step_paid_formal_order(context, parent_name, child_name, amount):
    """线上支付（mock 通道即时到账）的年费订单——"原路退回"场景必须有线上原单。"""
    context.h = _h(context.client)
    parent, child, mini = _family_named(
        context.client, context.h, "13981037201", parent_name, child_name
    )
    _bind_openid(parent["id"], "o_refund_bdd_1")
    created = context.client.post(
        "/api/miniapp/orders",
        json={"child_id": child["id"], "order_type": "formal_fee"},
        headers=mini,
    ).json()
    pay = context.client.post(f"/api/miniapp/orders/{created['id']}/pay", headers=mini)
    assert pay.status_code == 200, pay.text
    row = _order_row(created["id"])
    assert row.status == "paid" and row.transaction_id, "背景要求：订单已支付且是线上原单"
    assert Decimal(str(row.amount)) == Decimal(amount), (
        f"场景写死实付 {amount} 元，而配置价为 {row.amount}——改配置或改场景"
    )
    context.parent, context.child, context.mini = parent, child, mini
    context.order = created


# ---------- 场景：提交退款申请 ----------


@when('{who}选择该订单提交退款申请 原因"{reason}"')
def step_apply_refund(context, who, reason):
    context.refund = _apply(context)


@then('退款申请创建 状态为 "{status}"')
def step_refund_status(context, status):
    assert context.refund["status"] == status, context.refund


@then("可退金额由服务端计算为剩余天数比例金额")
def step_amount_from_server(context):
    """金额必须与**服务端预览端点**一致（前端/客户端不参与计算）。"""
    pv = context.client.get(
        "/api/miniapp/refund-preview",
        params={"child_id": context.child["id"], "order_id": context.order["id"]},
        headers=context.mini,
    )
    assert pv.status_code == 200, pv.text
    assert Decimal(str(context.refund["amount"])) == Decimal(str(pv.json()["refundable_amount"]))
    assert pv.json()["rule"], "服务端必须给出规则说明（审核员据此判断）"


# ---------- 场景：同一订单禁止重复申请 ----------


@given('该订单已存在状态为 "{status}" 的退款申请')
def step_existing_refund(context, status):
    context.refund = _apply(context)
    assert context.refund["status"] == status, context.refund


@when("{who}再次提交退款申请")
def step_apply_again(context, who):
    context.second = context.client.post(
        "/api/miniapp/refund-requests",
        json={
            "child_id": context.child["id"],
            "order_id": context.order["id"],
            "reason": "再试一次",
        },
        headers=context.mini,
    )


@then("系统拒绝提交")
def step_assert_duplicate_rejected(context):
    assert context.second.status_code == 409, context.second.text
    assert "进行中" in context.second.json()["detail"]


# ---------- 场景：待审核状态下家长可撤销 ----------


@given('退款申请处于 "{status}"')
def step_refund_in_status(context, status):
    context.refund = _apply(context)
    if status == "approved":
        r = context.client.post(
            f"/api/admin/refund-requests/{context.refund['id']}/review",
            json={"approve": True, "remark": "同意"},
            headers=context.h,
        )
        assert r.status_code == 200, r.text
    assert _refund_row(context.refund["id"]).status == status


@when("{who}撤销申请")
def step_cancel_refund(context, who):
    r = context.client.post(
        f"/api/miniapp/refund-requests/{context.refund['id']}/cancel",
        json={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text


@then('申请状态变为 "{status}" 订单恢复')
def step_assert_cancelled(context, status):
    assert _refund_row(context.refund["id"]).status == status
    row = _order_row(context.order["id"])
    assert row.status == "paid", "撤销后订单必须回到已支付（否则钱与状态不一致）"
    assert row.refund_status != "pending", "撤销后订单不得残留退款中标记"


# ---------- 场景：审核拒绝后可再次申请 ----------


@given("退款申请被拒绝 理由已告知")
def step_refund_rejected(context):
    context.refund = _apply(context)
    r = context.client.post(
        f"/api/admin/refund-requests/{context.refund['id']}/review",
        json={"approve": False, "remark": "不符合退款条件"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    assert _refund_row(context.refund["id"]).status == "rejected"


@then("新申请创建成功")
def step_assert_reapplied(context):
    assert context.new_refund["id"] != context.refund["id"]
    assert context.new_refund["status"] == "pending"


# ---------- 场景：审核通过执行原路退款 ----------


@when("退款执行成功")
def step_execute_refund(context):
    r = context.client.post(
        f"/api/admin/refund-requests/{context.refund['id']}/execute",
        json={"success": True, "remark": "原路退回"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.exec_result = r.json()


@then('申请状态变为 "{status}"')
def step_assert_refunded(context, status):
    assert _refund_row(context.refund["id"]).status == status


@then("微信支付订单按原路退回")
def step_assert_original_channel(context):
    """渠道跟着**原单**走：线上支付的单必须走网关原路退（有商户退款单号），不是线下登记。"""
    assert context.exec_result.get("channel") == "wechat", context.exec_result
    row = _refund_row(context.refund["id"])
    assert (row.out_refund_no or "").startswith(f"RF{row.id}-"), row.out_refund_no
    assert _order_row(context.order["id"]).status == "refunded"


# 复用：拒绝场景的"再次提交"需要单独存新单
@when("{who}重新提交退款申请")
def step_reapply(context, who):
    context.new_refund = _apply(context)
