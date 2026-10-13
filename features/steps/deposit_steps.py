# features/steps/deposit_steps.py — FEAT-025/026/027 押金与赔偿 步骤定义
"""2026-10-13（第五十一轮）BDD 解封第一批 · **押金域** 9 个场景。

为什么先解封这一域（与退款域同源：都是"钱袋子"）：押金链路上半年踩过两次真金漏洞
（E-20260909-03 补缴漏待结清、2026-10-12 已缴押金可再线上缴），口径只写在单测里
不够——本文件把它钉成**端到端行为**：首缴入账 → 报损 → 赔偿扣除（够扣 / 超扣记待结清）
→ 家长小程序补缴（扣多少补多少、拆分入账）→ 兄弟互不影响 → 退会退还可用余额（已扣不退）
→ 遗失找回恢复上架（系统只记状态、不动钱）。

走真实链路（真实 MySQL + TestClient）：管理端建档 → 押金订单 → 确认收款 → 报损登记
→ 赔偿扣除 → 小程序 mock 通道补缴 → 退会审核 + 退款执行。

**判据必须与实现同源（红线 108）**：
- 小程序补缴受 R-313 权限矩阵约束——`member_status=none`（未缴费）返回 422「入会员后可用」，
  故"家长在小程序补缴"的场景先在 Given 里办入会（真实门店顺序也是先入会再押金）；
  同理"预约被硬拦截"必须让孩子在册，否则先撞"仅有效会员可预约"，测不到押金闸门。
- "系统持续提醒馆员跟进待结清"的**在位实现** = 管理端押金列表恒显 `unpaid_balance`
  + 借还台孩子卡面 `borrow_block` 回显欠款原因 + 预约硬拦。**主动提醒通道（管理端待办/推送）
  尚未实现**，属已登记缺口（PRD §3.6「系统持续提醒馆员」），本文件只断言在位机制，
  不假装有推送通道。
- "定价 68 元的书"是**线下协商依据**，书目表没有 price 字段；赔偿金额由馆员登记时输入，
  场景里的元数就是登记金额。
"""

from decimal import Decimal

from behave import given, then, when

from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _h

# ---------- 基建 ----------


def _family_named(client, h, phone, parent_name, child_name):
    """建档（家长名/孩子名按场景给）+ 家长登录态（小程序 token）。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": parent_name, "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": child_name}, headers=h
    ).json()
    login = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    return p, c, {"Authorization": f"Bearer {login['token']}"}


def _bind_openid(parent_id: int, openid: str) -> None:
    from backend.domain.identity.models import Parent

    with _db() as db:
        p = db.query(Parent).filter(Parent.id == parent_id).first()
        p.wechat_openid = openid
        db.commit()


def _detail(context, child_id: int | None = None) -> dict:
    """押金详情（管理端视图=馆员看到的账面）。"""
    cid = child_id or context.child["id"]
    r = context.client.get(f"/api/admin/deposits/children/{cid}", headers=context.h)
    assert r.status_code == 200, r.text
    assert r.json(), "押金卡必须存在（否则后续金额断言无意义）"
    return r.json()


def _ledgers(context, child_id: int | None = None) -> list[dict]:
    cid = child_id or context.child["id"]
    r = context.client.get(f"/api/admin/deposits/children/{cid}/ledgers", headers=context.h)
    assert r.status_code == 200, r.text
    return r.json()


def _amount(value) -> Decimal:
    return Decimal(str(value))


def _pay_deposit_order(context, child_id: int, expected: int | None = None) -> dict:
    """押金订单 + 线下扫码确认收款（首缴入口带"仅 unpaid 可建"闸）。"""
    r = context.client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=context.h)
    assert r.status_code == 200, r.text
    order = r.json()
    if expected is not None:
        assert _amount(order["amount"]) == Decimal(expected), (
            f"场景写死押金 {expected} 元，而配置 deposit_amount 为 {order['amount']}——改配置或改场景"
        )
    rc = context.client.post(
        f"/api/admin/orders/{order['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert rc.status_code == 200, rc.text
    return order


def _make_member(context, kind: str = "observation") -> None:
    """办入会（observation / formal）——小程序补缴与预约都受 R-313 矩阵约束。"""
    types = ["observation_fee"] if kind == "observation" else ["observation_fee", "formal_fee"]
    for order_type in types:
        o = context.client.post(
            "/api/admin/orders",
            json={"child_id": context.child["id"], "order_type": order_type},
            headers=context.h,
        ).json()
        rc = context.client.post(
            f"/api/admin/orders/{o['id']}/confirm-payment",
            json={"pay_method": "scan"},
            headers=context.h,
        )
        assert rc.status_code == 200, rc.text


def _book_with_copy(context, title: str) -> tuple[int, int]:
    book = context.client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 100, "copy_count": 1},
        headers=context.h,
    ).json()
    force_book_on(context.client, context.h, book["id"])
    copies = context.client.get(f"/api/admin/books/{book['id']}/copies", headers=context.h).json()
    return book["id"], copies[0]["id"]


def _report_lost(context, copy_id: int, price: int) -> None:
    """报损·遗失（2026-10-12 拍板 P5 动作）：只标副本状态 + 发赔偿待办，不自动扣钱。"""
    r = context.client.post(
        f"/api/admin/copies/{copy_id}/report-damage",
        json={"kind": "lost", "reason": f"家长报遗失，协商按定价 {price} 元赔偿"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text


def _deduct(context, amount: int, reason: str, copy_id: int | None = None) -> dict:
    body: dict = {"amount": f"{amount}.00", "reason": reason}
    if copy_id:
        body["copy_id"] = copy_id
    r = context.client.post(
        f"/api/admin/deposits/children/{context.child['id']}/deduct", json=body, headers=context.h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _parent_supplement(context, amount: int) -> dict:
    """家长在小程序建补缴单并支付（mock 通道即时到账，与真实回调同一条结算链）。"""
    r = context.client.post(
        "/api/miniapp/deposits/supplement-orders",
        json={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    order = r.json()
    assert _amount(order["amount"]) == Decimal(amount), (
        f"补缴额 = 标准额 − 可用余额 + 待结清：场景期望 {amount} 元，服务端算出 {order['amount']}"
    )
    pay = context.client.post(f"/api/miniapp/orders/{order['order_id']}/pay", headers=context.mini)
    assert pay.status_code == 200, pay.text
    assert pay.json().get("instant_paid") is True, pay.json()
    return order


# ---------- 背景 ----------


@given('家长 "{parent_name}" 为孩子 "{child_name}" 缴纳押金 {amount:d} 元')
def step_bg_deposit(context, parent_name, child_name, amount):
    context.h = _h(context.client)
    parent, child, mini = _family_named(
        context.client, context.h, "13981037601", parent_name, child_name
    )
    _bind_openid(parent["id"], "o_deposit_bdd_1")
    context.parent, context.child, context.mini = parent, child, mini
    context.child_name = child_name
    _pay_deposit_order(context, child["id"], amount)


# ---------- 场景：押金初始状态 ----------


@then('小明的押金状态为 "{status}" 可用余额 {amount:d} 元')
def step_initial_state(context, status, amount):
    dep = _detail(context)
    assert dep["status"] == status, dep
    assert _amount(dep["available_amount"]) == Decimal(amount), dep
    assert _amount(dep["deducted_amount"]) == Decimal(0), dep


# ---------- 场景：遗失赔偿从押金扣除 ----------


@given("小明遗失一本定价 {price:d} 元的书 且协商确定赔偿")
def step_lost_book(context, price):
    book_id, copy_id = _book_with_copy(context, f"遗失赔偿样例（定价 {price} 元）")
    _report_lost(context, copy_id, price)
    context.book_id, context.copy_id = book_id, copy_id
    context.claim = price


@when("馆员登记赔偿")
def step_register_claim(context):
    context.deducted = _deduct(
        context,
        context.claim,
        f"遗失赔偿（定价 {context.claim} 元，副本 {context.copy_id}）",
        copy_id=context.copy_id,
    )


@then("小明押金可用余额变为 {amount:d} 元")
def step_available_after_deduct(context, amount):
    dep = _detail(context)
    assert _amount(dep["available_amount"]) == Decimal(amount), dep


@then('押金状态变为 "{status}"')
def step_status_after_deduct(context, status):
    assert _detail(context)["status"] == status, _detail(context)


@then("扣除流水记录金额与事由")
def step_deduct_ledger(context):
    top = _ledgers(context)[0]
    assert top["entry_type"] == "deduct", top
    assert _amount(top["amount"]) == Decimal(context.claim), top
    assert top["reason"].strip(), "扣除流水必须记事由（红线：资金动作必留痕）"
    assert top["related_copy_id"] == context.copy_id, top
    assert _amount(top["balance_after"]) == _amount(_detail(context)["available_amount"]), top


# ---------- 场景：押金不足扣到零为止 ----------


@given("小明押金可用余额仅 {left:d} 元 又遗失定价 {price:d} 元的书")
def step_low_balance(context, left, price):
    _make_member(context)  # 见文件头：未缴费态会先撞会员闸门，测不到押金段
    standard = _amount(_detail(context)["amount"])
    _deduct(context, int(standard - Decimal(left)), "前次损坏赔偿")
    book_id, copy_id = _book_with_copy(context, f"遗失赔偿样例（定价 {price} 元）")
    _report_lost(context, copy_id, price)
    context.book_id, context.copy_id = book_id, copy_id
    context.claim = price
    assert _amount(_detail(context)["available_amount"]) == Decimal(left), "前置余额未构造出来"


@when("登记赔偿 {amount:d} 元")
def step_register_claim_n(context, amount):
    context.claim = amount
    context.deducted = _deduct(context, amount, f"遗失赔偿 {amount} 元")


@then("可用余额扣至 {zero:d} 元 差额 {diff:d} 元记为待结清")
def step_fully_deducted(context, zero, diff):
    dep = _detail(context)
    assert _amount(dep["available_amount"]) == Decimal(zero), dep
    assert _amount(dep["unpaid_balance"]) == Decimal(diff), dep
    assert dep["status"] == "fully_deducted", dep


@then("系统持续提醒馆员跟进待结清")
def step_pending_settlement_surfaced(context):
    """在位提醒面（无主动推送，见文件头缺口说明）：管理端押金列表恒显待结清 + 借还台卡面回显。"""
    listed = context.client.get(
        "/api/admin/deposits",
        params={"keyword": context.child_name},
        headers=context.h,
    ).json()
    rows = [i for i in listed["items"] if i["child_id"] == context.child["id"]]
    assert rows, f"押金列表按关键词 {context.child_name} 查不到该孩子：{listed}"
    assert _amount(rows[0]["unpaid_balance"]) > 0, rows[0]
    card = context.client.get(
        f"/api/admin/circulation/children/{context.child['id']}/card", headers=context.h
    ).json()
    assert "有未结清赔偿款" in (card.get("borrow_block") or ""), card


@then("待结清期间借书需馆员人工放行 预约被硬拦截")
def step_pending_settlement_gates(context):
    book_id, copy_id = _book_with_copy(context, "待结清借书样例")
    blocked = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": context.child["id"], "copy_id": copy_id},
        headers=context.h,
    )
    assert blocked.status_code == 422, blocked.text
    detail = blocked.json()["detail"]
    assert "有未结清赔偿款" in detail and "人工放行" in detail, detail
    passed = context.client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": context.child["id"],
            "copy_id": copy_id,
            "override_reason": "家长已到店，本周内结清赔偿",
        },
        headers=context.h,
    )
    assert passed.status_code == 200, passed.text
    reserved = context.client.post(
        "/api/miniapp/reservations",
        json={"child_id": context.child["id"], "book_id": book_id},
        headers=context.mini,
    )
    assert reserved.status_code == 422, reserved.text
    assert "有未结清赔偿款" in reserved.json()["detail"], reserved.text


# ---------- 场景：超扣后补缴扣多少补多少 ----------


@given("小明押金可用余额 {left:d} 元 又遗失定价 {price:d} 元的书")
def step_balance_then_lost(context, left, price):
    _make_member(context)  # 家长要在小程序补缴 → 须在册（R-313）
    assert _amount(_detail(context)["available_amount"]) == Decimal(left), "前置余额与背景不符"
    book_id, copy_id = _book_with_copy(context, f"遗失赔偿样例（定价 {price} 元）")
    _report_lost(context, copy_id, price)
    context.book_id, context.copy_id = book_id, copy_id
    context.claim = price


@when("张女士补缴 {amount:d} 元")
def step_parent_supplement(context, amount):
    context.supplement_order = _parent_supplement(context, amount)


@then('可用余额恢复 {amount:d} 元 待结清归零 状态回到 "{status}"')
def step_restored(context, amount, status):
    dep = _detail(context)
    assert _amount(dep["available_amount"]) == Decimal(amount), dep
    assert _amount(dep["unpaid_balance"]) == Decimal(0), dep
    assert dep["status"] == status, dep


@then("补缴流水记录含结清待结清金额")
def step_supplement_settled_ledger(context):
    top = _ledgers(context)[0]
    assert top["entry_type"] == "supplement", top
    assert "结清" in top["reason"], top["reason"]
    assert _amount(top["amount"]) == _amount(context.supplement_order["amount"]), top
    assert "800" in top["reason"], "流水必须写明结清的待结清金额（扣多少补多少）"


# ---------- 场景：兄弟押金互不影响 ----------


@given("小明的弟弟小红也有押金 {amount:d} 元")
def step_sibling_deposit(context, amount):
    sibling = context.client.post(
        f"/api/admin/members/parents/{context.parent['id']}/children",
        json={"name": "小红"},
        headers=context.h,
    ).json()
    context.sibling = sibling
    _pay_deposit_order(context, sibling["id"], amount)


@when("小明的押金被扣除赔偿")
def step_deduct_self(context):
    context.deducted = _deduct(context, 68, "遗失赔偿 68 元")


@then("小红的押金余额不变")
def step_sibling_untouched(context):
    dep = _detail(context, context.sibling["id"])
    assert _amount(dep["available_amount"]) == Decimal(1200), dep
    assert _amount(dep["deducted_amount"]) == Decimal(0), dep
    assert dep["status"] == "paid", dep


# ---------- 场景：家长补缴押金 ----------


@given('小明押金可用余额 {left:d} 元 状态 "{status}"')
def step_balance_with_status(context, left, status):
    _make_member(context)
    standard = _amount(_detail(context)["amount"])
    _deduct(context, int(standard - Decimal(left)), "遗失赔偿")
    dep = _detail(context)
    assert _amount(dep["available_amount"]) == Decimal(left), dep
    assert dep["status"] == status, dep


@when("张女士在小程序补缴 {amount:d} 元")
def step_parent_supplement_in_miniapp(context, amount):
    context.supplement_order = _parent_supplement(context, amount)


@then('可用余额恢复 {amount:d} 元 状态回到 "{status}"')
def step_restored_status_only(context, amount, status):
    dep = _detail(context)
    assert _amount(dep["available_amount"]) == Decimal(amount), dep
    assert dep["status"] == status, dep


@then("补缴流水记录 补缴累计金额更新")
def step_supplement_total(context):
    dep = _detail(context)
    assert _amount(dep["supplemented_total"]) == _amount(context.supplement_order["amount"]), dep
    top = _ledgers(context)[0]
    assert top["entry_type"] == "supplement", top
    assert _amount(top["amount"]) == _amount(context.supplement_order["amount"]), top


# ---------- 场景：退会时退还可用余额 ----------


@given("小明退会 押金可用余额 {left:d} 元 历史扣除 {deducted:d} 元")
def step_withdraw_refund_setup(context, left, deducted):
    _make_member(context, kind="formal")  # 退会前置要求有效会员（正式会员进结算）
    standard = _amount(_detail(context)["amount"])
    _deduct(context, int(standard - Decimal(left)), "遗失赔偿")
    dep = _detail(context)
    assert _amount(dep["available_amount"]) == Decimal(left), dep
    assert _amount(dep["deducted_amount"]) == Decimal(deducted), dep
    applied = context.client.post(
        "/api/miniapp/withdrawals",
        json={"child_id": context.child["id"], "reason": "搬家离店"},
        headers=context.mini,
    )
    assert applied.status_code == 200, applied.text
    context.withdrawal = applied.json()
    preview = context.client.get(
        f"/api/admin/withdrawals/{context.withdrawal['id']}/settle-preview", headers=context.h
    )
    assert preview.status_code == 200, preview.text
    items = preview.json()["items"]
    deposit_items = [i for i in items if i["kind"] == "deposit"]
    assert deposit_items, f"结算预估里必须有押金项：{preview.json()}"
    context.deposit_item = deposit_items[0]


@when("押金退款执行")
def step_execute_deposit_refund(context):
    reviewed = context.client.post(
        f"/api/admin/withdrawals/{context.withdrawal['id']}/review",
        json={"approve": True, "remark": "同意退会"},
        headers=context.h,
    )
    assert reviewed.status_code == 200, reviewed.text
    rows = context.client.get(
        "/api/admin/refund-requests", params={"status": "pending"}, headers=context.h
    ).json()
    deposit_rows = [r for r in rows if r["kind"] == "deposit"]
    assert len(deposit_rows) == 1, f"审核通过应生成且只生成一张押金退款单：{rows}"
    context.deposit_refund = deposit_rows[0]
    approved = context.client.post(
        f"/api/admin/refund-requests/{context.deposit_refund['id']}/review",
        json={"approve": True, "remark": "同意原路退回"},
        headers=context.h,
    )
    assert approved.status_code == 200, approved.text
    executed = context.client.post(
        f"/api/admin/refund-requests/{context.deposit_refund['id']}/execute",
        json={"success": True, "remark": "押金按线下现金退还"},
        headers=context.h,
    )
    assert executed.status_code == 200, executed.text


@then("退还 {amount:d} 元")
def step_refunded_available(context, amount):
    assert _amount(context.deposit_refund["amount"]) == Decimal(amount), context.deposit_refund
    assert _amount(context.deposit_item["amount"]) == Decimal(amount), context.deposit_item
    dep = _detail(context)
    assert _amount(dep["available_amount"]) == Decimal(0), dep
    assert dep["status"] == "refunded", dep


@then("已扣除的 {deducted:d} 元不退还")
def step_deducted_kept(context, deducted):
    dep = _detail(context)
    assert _amount(dep["deducted_amount"]) == Decimal(deducted), dep
    assert _amount(context.deposit_refund["amount"]) == Decimal(1200) - Decimal(deducted), (
        "退还额不得含已扣除部分（R-312：退可用余额，不退已扣）"
    )
    assert "已扣除部分不退" in context.deposit_item["rule"], context.deposit_item


# ---------- 场景：赔偿后图书找回恢复上架 ----------


@given("小明赔偿的书在家中找到并归还")
def step_book_recovered(context):
    book_id, copy_id = _book_with_copy(context, "遗失找回样例（定价 68 元）")
    _report_lost(context, copy_id, 68)
    context.book_id, context.copy_id = book_id, copy_id
    context.claim = 68
    _deduct(context, 68, f"遗失赔偿 68 元（副本 {copy_id}）", copy_id=copy_id)


@when("馆员登记找回")
def step_register_recovered(context):
    r = context.client.put(
        f"/api/admin/copies/{context.copy_id}/status",
        json={"status": "available", "reason": "书在家中找到，家长送回"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text


@then("图书副本恢复可借状态")
def step_copy_back_on_shelf(context):
    copies = context.client.get(
        f"/api/admin/books/{context.book_id}/copies", headers=context.h
    ).json()
    row = [c for c in copies if c["id"] == context.copy_id]
    assert row and row[0]["status"] == "available", copies


@then("赔偿款处理方式由门店线下决定 系统仅记录恢复")
def step_no_money_movement(context):
    dep = _detail(context)
    assert _amount(dep["deducted_amount"]) == Decimal(68), dep
    assert _amount(dep["available_amount"]) == Decimal(1132), dep
    kinds = {entry["entry_type"] for entry in _ledgers(context)}
    assert "refund" not in kinds and "supplement" not in kinds, (
        f"找回只记副本状态，系统不得自动动钱：{kinds}"
    )
