# features/steps/order_types_steps.py — FEAT-080 步骤定义（插修 6 R3）
from datetime import datetime, timedelta

from behave import given, then, when


@given('系统中已存在家长账号 "{name}" 且名下有正式会员孩子')
def step_given_family(context, name):
    from tests.unit.test_wm10_concurrency import _family, _h, _pay

    context.h = _h(context.client)
    p, c, mini = _family(context.client, context.h, "13981037001", name=name)
    _pay(context.client, context.h, c["id"], "observation_fee")
    context.child = c
    context.mini = mini


@when("馆员为孩子创建押金订单")
def step_when_deposit_order(context):
    r = context.client.post(
        "/api/admin/orders",
        json={"child_id": context.child["id"], "order_type": "deposit"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.order = r.json()


@then('订单类型为 "{otype}" 且金额等于押金标准配置 {amount:d}')
def step_then_deposit_order(context, otype, amount):
    assert context.order["order_type"] == otype
    from decimal import Decimal

    assert Decimal(context.order["amount"]) == Decimal(f"{amount}.00")


@then("确认收款后押金流水新增一笔缴纳记录余额等于 {balance:d}")
def step_then_deposit_ledger(context, balance):
    rc = context.client.post(
        f"/api/admin/orders/{context.order['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert rc.status_code == 200, rc.text
    from decimal import Decimal

    from backend.domain.billing.models import Deposit, DepositLedger
    from tests.unit.test_wm10_concurrency import _db

    with _db() as db:
        dep = db.query(Deposit).filter(Deposit.child_id == context.child["id"]).first()
        assert dep is not None and dep.status == Deposit.STATUS_PAID
        ledger = (
            db.query(DepositLedger)
            .filter(
                DepositLedger.deposit_id == dep.id,
                DepositLedger.entry_type == DepositLedger.ENTRY_PAY,
            )
            .order_by(DepositLedger.id.desc())
            .first()
        )
        assert ledger is not None and ledger.balance_after == Decimal(f"{balance}.00")


@given('馆员已发布一场收费活动 "{title}" 费用 {fee:d} 元')
def step_given_activity(context, title, fee):
    r = context.client.post(
        "/api/admin/activities",
        json={
            "title": title,
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内",
            "max_quota": 10,
            "fee": fee,
        },
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.activity = r.json()


@when("馆员为孩子创建活动订单并选择该活动")
def step_when_activity_order(context):
    r = context.client.post(
        "/api/admin/orders",
        json={
            "child_id": context.child["id"],
            "order_type": "activity_fee",
            "activity_id": context.activity["id"],
        },
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.order = r.json()


@then('订单类型为 "{otype}" 且金额等于 {amount:d}')
def step_then_activity_order(context, otype, amount):
    from decimal import Decimal

    assert context.order["order_type"] == otype
    assert Decimal(context.order["amount"]) == Decimal(f"{amount}.00")


@when('馆员为孩子创建自定义订单 说明 "{desc}" 金额 {amount:d} 元')
def step_when_custom_order(context, desc, amount):
    r = context.client.post(
        "/api/admin/orders",
        json={
            "child_id": context.child["id"],
            "order_type": "custom",
            "amount": f"{amount}.00",
            "remark": desc,
        },
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.order = r.json()


@then('订单类型为 "{otype}" 且金额为 {amount:d}')
def step_then_custom_order(context, otype, amount):
    from decimal import Decimal

    assert context.order["order_type"] == otype
    assert Decimal(context.order["amount"]) == Decimal(f"{amount}.00")


@given("孩子当前为正式会员")
def step_given_formal(context):
    from backend.domain.identity.models import Child
    from tests.unit.test_wm10_concurrency import _db

    with _db() as db:
        child = db.query(Child).filter(Child.id == context.child["id"]).first()
        context._status_before = child.member_status
        context._expire_before = child.member_expire


@when("馆员为孩子创建自定义订单并确认收款")
def step_when_custom_confirm(context):
    r = context.client.post(
        "/api/admin/orders",
        json={
            "child_id": context.child["id"],
            "order_type": "custom",
            "amount": "88.00",
            "remark": "会员态保持测试",
        },
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.order = r.json()
    rc = context.client.post(
        f"/api/admin/orders/{context.order['id']}/confirm-payment",
        json={"pay_method": "cash"},
        headers=context.h,
    )
    assert rc.status_code == 200, rc.text


@then("孩子会员状态与到期日保持不变")
def step_then_membership_unchanged(context):
    from backend.domain.identity.models import Child
    from tests.unit.test_wm10_concurrency import _db

    with _db() as db:
        child = db.query(Child).filter(Child.id == context.child["id"]).first()
        assert child.member_status == context._status_before
        assert child.member_expire == context._expire_before


@then("该订单可走退款链")
def step_then_refundable(context):
    r = context.client.post(
        "/api/miniapp/refund-requests",
        json={
            "child_id": context.child["id"],
            "order_id": context.order["id"],
            "reason": "自定义单退款",
        },
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
