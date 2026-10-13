# features/steps/transfer_steps.py — FEAT-063/064/065 权益转让 步骤定义
"""2026-10-13（第五十一轮）BDD 解封第一批 · **转让域** 8 个场景 + 条件校验大纲 9 行。

转让是"会期搬家"的资金级动作：发起 12 项条件校验 → 双方冻结 → 超管审核 12 步同事务
（转出方退会、年费不退、押金退款自动发起、受让方继承到期日、双方解锁、家长通知）。
本文件把它钉成**端到端行为**，并把大纲 9 行前置逐行构造后验证"失败项点名"。

**判据必须与实现同源（红线 108）——动手前逐条亲验的实现事实**：
- 状态值是 `pending`（不是文档旧写的 `pending_review`；见外部专家 L-2 登记取舍）。
- `检查条件`返回的是 `[{name, ok}]`，`apply` 失败时的 detail 形如"转让条件不满足：<失败项名>；…"
  ——断言**点名的那一项**，不做全文匹配。
- "未处理遗失"在实现里经**未结清赔偿款**收口（报损只登记管理待办+标副本状态，登记赔偿后才成债）；
  本文件按"报损 + 登记赔偿"构造，并在注释里标明口径（只报损未登记赔偿不拦，属现状）。
- "审核期间借出了一本书""冻结期续借/归还"在产品路径上不可达（发起前置=无在借、冻结期禁借），
  故这三处用**DB 直插在借记录**构造防御性场景（构造的是产品路径不可达的状态，不是伪造断言）。
- "人工修改到期日"**功能本身不存在**（无端点可改 member_expire），断言按"传了也改不动"。
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from behave import given, then, when

from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _h

# ---------- 基建 ----------


def _amount(value) -> Decimal:
    return Decimal(str(value))


def _new_child(context, name: str) -> dict:
    return context.client.post(
        f"/api/admin/members/parents/{context.parent['id']}/children",
        json={"name": name},
        headers=context.h,
    ).json()


def _child_row(child_id: int):
    from backend.domain.identity.models import Child

    with _db() as db:
        return db.query(Child).filter(Child.id == child_id).first()


def _transfer_row(req_id: int):
    from backend.domain.identity.models import TransferRequest

    with _db() as db:
        return db.query(TransferRequest).filter(TransferRequest.id == req_id).first()


def _set_expire(child_id: int, days_from_today: int) -> None:
    from backend.domain.identity.models import Child

    with _db() as db:
        child = db.query(Child).filter(Child.id == child_id).first()
        child.member_expire = date.today() + timedelta(days=days_from_today)
        db.commit()


def _pay_member_fee(context, child_id: int, order_type: str) -> dict:
    o = context.client.post(
        "/api/admin/orders",
        json={"child_id": child_id, "order_type": order_type},
        headers=context.h,
    ).json()
    rc = context.client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert rc.status_code == 200, rc.text
    return o


def _deposit_detail(context, child_id: int) -> dict:
    r = context.client.get(f"/api/admin/deposits/children/{child_id}", headers=context.h)
    assert r.status_code == 200, r.text
    return r.json()


def _deduct(context, child_id: int, amount: int, reason: str) -> None:
    r = context.client.post(
        f"/api/admin/deposits/children/{child_id}/deduct",
        json={"amount": f"{amount}.00", "reason": reason},
        headers=context.h,
    )
    assert r.status_code == 200, r.text


def _book_with_copy(context, title: str) -> tuple[int, int]:
    book = context.client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 100, "copy_count": 1},
        headers=context.h,
    ).json()
    force_book_on(context.client, context.h, book["id"])
    copies = context.client.get(f"/api/admin/books/{book['id']}/copies", headers=context.h).json()
    return book["id"], copies[0]["id"]


def _seed_active_borrow(
    context, child_id: int, book_id: int, copy_id: int, *, overdue: bool = False
):
    """DB 直插在借记录（+副本置借出）——冻结态/审核态下的在借在产品路径不可达，见文件头。"""
    from backend.domain.catalog.models import BookCopy
    from backend.domain.circulation.models import BorrowRecord

    now = datetime.now()
    with _db() as db:
        rec = BorrowRecord(
            child_id=child_id,
            copy_id=copy_id,
            book_id=book_id,
            borrowed_at=now - timedelta(days=20 if overdue else 1),
            due_at=now - timedelta(days=5) if overdue else now + timedelta(days=13),
            status=BorrowRecord.STATUS_OVERDUE if overdue else BorrowRecord.STATUS_ACTIVE,
            renew_used=0,
            is_deleted=0,
        )
        db.add(rec)
        db.query(BookCopy).filter(BookCopy.id == copy_id).update(
            {"status": BookCopy.STATUS_BORROWED}
        )
        db.commit()
        return rec.id


def _apply(context):
    return context.client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": context.source["id"], "target_child_id": context.target["id"]},
        headers=context.mini,
    )


def _approve(context, req_id: int, remark: str = "同意"):
    return context.client.post(
        f"/api/admin/transfers/{req_id}/review",
        json={"approve": True, "remark": remark},
        headers=context.h,
    )


def _orders_of(context, child_id: int) -> list[dict]:
    """管理端订单列表**没有 child_id 过滤参数**（只有 status/keyword）——取整页后按孩子过滤。"""
    r = context.client.get(
        "/api/admin/orders", params={"page": 1, "page_size": 100}, headers=context.h
    )
    assert r.status_code == 200, r.text
    return [o for o in r.json()["items"] if o["child_id"] == child_id]


def _formal_order(context, child_id: int) -> dict:
    rows = [o for o in _orders_of(context, child_id) if o["order_type"] == "formal_fee"]
    assert rows, f"应有年费订单：{_orders_of(context, child_id)}"
    return rows[0]


def _summary(context, child_id: int) -> dict:
    r = context.client.get(
        "/api/miniapp/growth/summary", params={"child_id": child_id}, headers=context.mini
    )
    assert r.status_code == 200, r.text
    return r.json()


def _notifications(context, scene: str | None = None) -> list[dict]:
    params = {"scene": scene} if scene else {}
    r = context.client.get("/api/miniapp/notifications", params=params, headers=context.mini)
    assert r.status_code == 200, r.text
    data = r.json()
    return data["items"] if isinstance(data, dict) and "items" in data else data


def _seed_growth(context, child_id: int, words: int = 300, points: int = 150, level: str = "B"):
    from backend.domain.growth.models import ChildGrowthState, PointLedger, WordsLedger
    from backend.domain.reading.models import CheckIn

    book_ids = [_book_with_copy(context, f"成就样例 {i}")[0] for i in range(3)]
    with _db() as db:
        for book_id in book_ids:
            db.add(
                WordsLedger(
                    child_id=child_id,
                    book_id=book_id,
                    word_count=words // 3,
                    source="quiz",
                    is_deleted=0,
                )
            )
        db.add(
            PointLedger(
                child_id=child_id,
                points=points,
                reason_type="words_convert",
                detail="历史成就（BDD 前置）",
                is_deleted=0,
            )
        )
        db.add(
            CheckIn(
                child_id=child_id,
                checkin_date=date.today() - timedelta(days=1),
                book_id=book_ids[0],
                streak=3,
                is_deleted=0,
            )
        )
        state = (
            db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child_id, ChildGrowthState.is_deleted == 0)
            .first()
        )
        if not state:
            state = ChildGrowthState(child_id=child_id)
            db.add(state)
        state.words_total = words
        state.points_total = points
        state.level = level
        db.commit()


# 大纲 9 行：前置情况 → （构造动作, 必须被点名的失败项）
_EXPECTED_CONDITION = {
    "小明有在借图书": "转出方图书已全部归还",
    "小明有逾期未还": "转出方图书已全部归还",
    "小明有未处理遗失": "转出方无未结清赔偿款",
    "小明有待结清赔偿": "转出方无未结清赔偿款",
    "小明有进行中的退款申请": "转出方没有进行中的申请（退款/退会/转让）",
    "小明有进行中的退会申请": "转出方没有进行中的申请（退款/退会/转让）",
    "小红已是正式会员": "受让方从未入会或已退会",
    "小红有进行中的转让": "没有进行中的其他转让",
    "小明剩余天数为 0": "转出方会员剩余时间大于 0",
}


# ---------- 背景 ----------


@given(
    '家长 "{parent_name}" 名下孩子 "{source_name}" 是正式会员 剩余 {days:d} 天 且孩子 "{target_name}" 从未入会'
)
def step_bg(context, parent_name, source_name, target_name, days):
    context.h = _h(context.client)
    parent = context.client.post(
        "/api/admin/members/parents",
        json={"name": parent_name, "phone": "13981037801"},
        headers=context.h,
    ).json()
    source = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": source_name},
        headers=context.h,
    ).json()
    target = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": target_name},
        headers=context.h,
    ).json()
    login = context.client.post(
        "/api/miniapp/login", json={"phone": "13981037801", "code": "1234"}
    ).json()
    context.parent, context.source, context.target = parent, source, target
    context.source_name, context.target_name = source_name, target_name
    context.mini = {"Authorization": f"Bearer {login['token']}"}
    deposit_order = context.client.post(
        f"/api/admin/deposits/children/{source['id']}/orders", headers=context.h
    ).json()
    rc = context.client.post(
        f"/api/admin/orders/{deposit_order['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert rc.status_code == 200, rc.text
    _pay_member_fee(context, source["id"], "observation_fee")
    _pay_member_fee(context, source["id"], "formal_fee")
    _set_expire(source["id"], days)  # 场景写死"剩余 N 天"（付年费默认 365 天）
    row = _child_row(source["id"])
    assert row.member_status == "formal", "背景要求：转出方是正式会员"
    assert _child_row(target["id"]).member_status == "none", "背景要求：受让方从未入会"


# ---------- 场景：满足全部条件发起转让 ----------


@when("张女士发起转让 转出小明 受让小红")
def step_apply_transfer(context):
    context.transfer_response = _apply(context)


@then('转让申请创建 状态 "{status}"')
def step_transfer_created(context, status):
    assert context.transfer_response.status_code == 200, context.transfer_response.text
    context.transfer = context.transfer_response.json()
    assert context.transfer["status"] == status, context.transfer
    assert all(c["ok"] for c in context.transfer["conditions"]), context.transfer
    assert _transfer_row(context.transfer["id"]).status == status


@then("双方进入转让锁定")
def step_both_locked(context):
    assert _child_row(context.source["id"]).operation_locked == 1, "转出方应被锁定"
    assert _child_row(context.target["id"]).operation_locked == 1, "受让方应被锁定"


# ---------- 场景大纲：转让发起条件校验（9 行） ----------


@given('前置情况 "{situation}"')
def step_precondition(context, situation):
    context.situation = situation
    assert situation in _EXPECTED_CONDITION, f"大纲新增了行但步骤没实现：{situation}"
    source_id, target_id = context.source["id"], context.target["id"]

    if situation == "小明有在借图书":
        _, copy_id = _book_with_copy(context, "在借样例")
        r = context.client.post(
            "/api/admin/circulation/borrow",
            json={"child_id": source_id, "copy_id": copy_id},
            headers=context.h,
        )
        assert r.status_code == 200, r.text
    elif situation == "小明有逾期未还":
        book_id, copy_id = _book_with_copy(context, "逾期样例")
        _seed_active_borrow(context, source_id, book_id, copy_id, overdue=True)
    elif situation == "小明有未处理遗失":
        # 报损只登记管理待办 + 标副本状态；赔偿登记（超押金）后才成为"未结清赔偿款"——
        # 与退会域同口径（遗失/损坏并入 unpaid_balance）。只报损不登记赔偿不拦，属现状。
        book_id, copy_id = _book_with_copy(context, "遗失样例")
        r = context.client.post(
            f"/api/admin/copies/{copy_id}/report-damage",
            json={"kind": "lost", "reason": "家长报遗失"},
            headers=context.h,
        )
        assert r.status_code == 200, r.text
        standard = _amount(_deposit_detail(context, source_id)["available_amount"])
        _deduct(context, source_id, int(standard) + 70, "遗失赔偿（超出押金 70 元）")
    elif situation == "小明有待结清赔偿":
        standard = _amount(_deposit_detail(context, source_id)["available_amount"])
        _deduct(context, source_id, int(standard) + 70, "遗失赔偿（超出押金 70 元）")
    elif situation == "小明有进行中的退款申请":
        formal = _formal_order(context, source_id)
        r = context.client.post(
            "/api/miniapp/refund-requests",
            json={"child_id": source_id, "order_id": formal["id"], "reason": "孩子不来了"},
            headers=context.mini,
        )
        assert r.status_code == 200, r.text
    elif situation == "小明有进行中的退会申请":
        r = context.client.post(
            "/api/miniapp/withdrawals",
            json={"child_id": source_id, "reason": "搬家"},
            headers=context.mini,
        )
        assert r.status_code == 200, r.text
    elif situation == "小红已是正式会员":
        _pay_member_fee(context, target_id, "observation_fee")
        _pay_member_fee(context, target_id, "formal_fee")
    elif situation == "小红有进行中的转让":
        first = _apply(context)
        assert first.status_code == 200, f"先造一单进行中的转让（小红为受让方）：{first.text}"
    elif situation == "小明剩余天数为 0":
        _set_expire(source_id, 0)
    else:  # pragma: no cover —— 上面已断言覆盖
        raise AssertionError(situation)


@then('系统拒绝提交并给出具体失败原因 "{expected}"')
def step_rejected_with_condition(context, expected):
    resp = context.transfer_response
    assert resp.status_code == 422, (
        f"{context.situation} 应拒绝，实 {resp.status_code} {resp.text[:120]}"
    )
    detail = resp.json()["detail"]
    condition = _EXPECTED_CONDITION[context.situation]
    assert condition in detail, f"{context.situation} 应点名失败项「{condition}」，实：{detail}"
    if "先归还" in expected:
        assert "图书已全部归还" in detail, detail
    if "none 或 withdrawn" in expected:
        assert "受让方从未入会或已退会" in detail, detail


# ---------- 场景：转让锁定范围 ----------


@given("转让申请审核中")
def step_in_review(context):
    context.transfer_response = _apply(context)
    assert context.transfer_response.status_code == 200, context.transfer_response.text
    context.transfer = context.transfer_response.json()
    assert _child_row(context.source["id"]).operation_locked == 1
    assert _child_row(context.target["id"]).operation_locked == 1


@then(
    "双方禁止借书、预约、续借、退款申请、退会申请、档案删除、创建观察期或年费或补缴订单、"
    "发起新转让、成为其他转让受让方、人工修改到期日"
)
def step_lock_scope(context):
    source_id, target_id = context.source["id"], context.target["id"]

    # 借书（转出方）
    _, copy_id = _book_with_copy(context, "冻结期借书样例")
    borrow = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": source_id, "copy_id": copy_id},
        headers=context.h,
    )
    assert borrow.status_code == 422 and "借书已冻结" in borrow.json()["detail"], borrow.text

    # 预约（转出方）
    book_id, _ = _book_with_copy(context, "冻结期预约样例")
    reserve = context.client.post(
        "/api/miniapp/reservations",
        json={"child_id": source_id, "book_id": book_id},
        headers=context.mini,
    )
    assert reserve.status_code == 422 and "预约已冻结" in reserve.json()["detail"], reserve.text

    # 续借（DB 直插在借，见文件头）
    rbook_id, rcopy_id = _book_with_copy(context, "冻结期续借样例")
    record_id = _seed_active_borrow(context, source_id, rbook_id, rcopy_id)
    context.active_copy_id = rcopy_id
    renew = context.client.post(
        "/api/admin/circulation/renew", json={"record_id": record_id}, headers=context.h
    )
    assert renew.status_code == 422 and "续借已冻结" in renew.json()["detail"], renew.text

    # 退款申请（转出方，年费单）
    formal = _formal_order(context, source_id)
    refund = context.client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": source_id, "order_id": formal["id"], "reason": "不来了"},
        headers=context.mini,
    )
    assert refund.status_code == 422 and "冻结" in refund.json()["detail"], refund.text

    # 退会申请（转出方）
    withdrawal = context.client.post(
        "/api/miniapp/withdrawals",
        json={"child_id": source_id, "reason": "搬家"},
        headers=context.mini,
    )
    assert withdrawal.status_code == 422 and "冻结" in withdrawal.json()["detail"], withdrawal.text

    # 档案删除：冻结判据优先于订单守卫（本轮补的洞）——**双方都返回 422**
    del_source = context.client.delete(
        f"/api/admin/members/children/{source_id}", headers=context.h
    )
    assert del_source.status_code == 422, del_source.text
    assert "不能删除档案" in del_source.json()["detail"], del_source.text
    del_target = context.client.delete(
        f"/api/admin/members/children/{target_id}", headers=context.h
    )
    assert del_target.status_code == 422, del_target.text
    assert "不能删除档案" in del_target.json()["detail"], del_target.text

    # 创建订单：管理端观察期/年费、押金补缴、家长端在线建单三处
    admin_order = context.client.post(
        "/api/admin/orders",
        json={"child_id": source_id, "order_type": "observation_fee"},
        headers=context.h,
    )
    assert admin_order.status_code == 422 and "审核流程中" in admin_order.json()["detail"], (
        admin_order.text
    )
    supplement = context.client.post(
        f"/api/admin/deposits/children/{source_id}/supplement-orders", headers=context.h
    )
    assert supplement.status_code == 422, supplement.text
    assert "冻结" in supplement.json()["detail"], supplement.text
    miniapp_order = context.client.post(
        "/api/miniapp/orders",
        json={"child_id": source_id, "order_type": "formal_fee"},
        headers=context.mini,
    )
    assert miniapp_order.status_code == 422 and "审核流程中" in miniapp_order.json()["detail"], (
        miniapp_order.text
    )

    # 发起新转让 + 成为其他转让受让方
    again = _apply(context)
    assert again.status_code == 422, again.text
    assert "转出方未处于冻结流程" in again.json()["detail"], again.text
    other = _new_child(context, "小刚")
    _pay_member_fee(context, other["id"], "observation_fee")
    _pay_member_fee(context, other["id"], "formal_fee")
    as_target = context.client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": other["id"], "target_child_id": source_id},
        headers=context.mini,
    )
    assert as_target.status_code == 422, as_target.text
    assert "受让方未处于冻结流程" in as_target.json()["detail"], as_target.text

    # 人工修改到期日：平台**没有**这个口子（无端点可改 member_expire）→ 传了也改不动
    before = _child_row(source_id).member_expire
    put = context.client.put(
        f"/api/admin/members/children/{source_id}",
        json={"name": context.source_name, "member_expire": "2030-01-01"},
        headers=context.h,
    )
    assert put.status_code in (200, 422), put.text
    assert _child_row(source_id).member_expire == before, "到期日不应被人工修改（该功能未做）"


@then("允许查看书目、收藏夹、报告、护照、榜单、积分明细")
def step_read_only_allowed(context):
    source_id = context.source["id"]
    for path, params in (
        ("/api/miniapp/books", {}),
        ("/api/miniapp/favorites", {"child_id": source_id}),
        ("/api/miniapp/reports/weekly", {"child_id": source_id}),
        ("/api/miniapp/passport", {"child_id": source_id}),
        ("/api/miniapp/leaderboard", {"child_id": source_id}),
        ("/api/miniapp/points", {"child_id": source_id}),
    ):
        r = context.client.get(path, params=params, headers=context.mini)
        assert r.status_code == 200, f"{path} 冻结期应可读：{r.status_code} {r.text[:120]}"


# ---------- 场景：审核通过同一事务完成十二步 ----------


@given("超管审核通过 且二次校验全部通过")
def step_approved(context):
    context.transfer_response = _apply(context)
    assert context.transfer_response.status_code == 200, context.transfer_response.text
    context.transfer = context.transfer_response.json()
    context.source_expire_before = _child_row(context.source["id"]).member_expire
    context.approve_response = _approve(context, context.transfer["id"])
    assert context.approve_response.status_code == 200, context.approve_response.text


@then('转让申请状态 "{status}"')
def step_transfer_status(context, status):
    assert context.approve_response.json()["status"] == status, context.approve_response.json()
    assert _transfer_row(context.transfer["id"]).status == status


@then('小明会员状态 "{member_status}" 退会原因 "{reason}"')
def step_source_withdrawn(context, member_status, reason):
    child = _child_row(context.source["id"])
    assert child.member_status == member_status, child.member_status
    assert child.withdraw_reason == reason, child.withdraw_reason


@then("小明年费不退款 但系统自动发起小明押金退款申请")
def step_annual_fee_and_deposit(context):
    from backend.domain.identity.models import Order, RefundRequest

    with _db() as db:
        formal = (
            db.query(Order)
            .filter(
                Order.child_id == context.source["id"],
                Order.order_type == Order.TYPE_FORMAL,
                Order.is_deleted == 0,
            )
            .all()
        )
        assert formal, "转出方应有年费订单"
        for order in formal:
            assert order.refund_status == Order.REFUND_STATUS_TRANSFERRED, (
                "年费不随转让退：订单应标记 transferred",
                order.refund_status,
            )
        refunds = (
            db.query(RefundRequest)
            .filter(
                RefundRequest.child_id == context.source["id"],
                RefundRequest.is_deleted == 0,
            )
            .all()
        )
    kinds = [r.kind for r in refunds]
    assert kinds == [RefundRequest.KIND_DEPOSIT], f"只应自动发起押金退款（年费不退）：{kinds}"
    assert refunds[0].status == RefundRequest.STATUS_PENDING, refunds[0].status
    assert _amount(refunds[0].amount) == _amount(
        _deposit_detail(context, context.source["id"])["available_amount"]
    )


@then('小红会员状态 "{member_status}"')
def step_target_formal(context, member_status):
    assert _child_row(context.target["id"]).member_status == member_status


@then("小红会员到期日等于小明原到期日")
def step_target_inherits_expire(context):
    assert _child_row(context.target["id"]).member_expire == context.source_expire_before, (
        _child_row(context.target["id"]).member_expire,
        context.source_expire_before,
    )


@then("转让记录生成 双方锁定解除 家长收到通知")
def step_record_unlock_notify(context):
    row = _transfer_row(context.transfer["id"])
    assert row.reviewed_by and row.reviewed_at, "审核留痕（谁审的、何时审的）"
    assert _child_row(context.source["id"]).operation_locked == 0
    assert _child_row(context.target["id"]).operation_locked == 0
    notices = _notifications(context, scene="other.transfer_result")
    assert any("审核通过" in n.get("content", "") for n in notices), notices


# ---------- 场景：审核通过时二次校验失败 ----------


@given("审核期间小明借出了一本书")
def step_borrow_during_review(context):
    """审核期借书在产品路径上被冻结拦住 → 用 DB 直插构造（文件头已说明理由）。"""
    context.transfer_response = _apply(context)
    assert context.transfer_response.status_code == 200, context.transfer_response.text
    context.transfer = context.transfer_response.json()
    book_id, copy_id = _book_with_copy(context, "审核期借书样例")
    _seed_active_borrow(context, context.source["id"], book_id, copy_id)


@when("超管尝试审核通过")
def step_try_approve(context):
    context.approve_response = _approve(context, context.transfer["id"])


@then("系统拒绝通过并提示具体原因")
def step_approve_rejected(context):
    resp = context.approve_response
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "审核时条件不满足" in detail and "图书已全部归还" in detail, detail
    row = _transfer_row(context.transfer["id"])
    assert row.status == "pending", "拒绝后申请必须仍是待审核（不得半推进）"
    assert _child_row(context.source["id"]).member_status == "formal", "转出方不得被改动"
    assert _child_row(context.target["id"]).member_status == "none", "受让方不得被改动"


# ---------- 场景：审核超时自动取消 ----------


@given("转让申请超过 72 小时未审核")
def step_overdue(context):
    from backend.domain.identity.models import TransferRequest

    context.transfer_response = _apply(context)
    assert context.transfer_response.status_code == 200, context.transfer_response.text
    context.transfer = context.transfer_response.json()
    with _db() as db:
        row = db.query(TransferRequest).filter(TransferRequest.id == context.transfer["id"]).first()
        row.expires_at = datetime.now() - timedelta(hours=1)  # 真等 72 小时不可行 → 直改到期时刻
        db.commit()


@then('申请状态自动变为 "{status}"')
def step_expired(context, status):
    listed = context.client.get(
        "/api/admin/transfers", params={"status": status}, headers=context.h
    )
    assert listed.status_code == 200, listed.text
    assert context.transfer["id"] in [r["id"] for r in listed.json()], "惰性超时扫描应把该单推进"
    assert _transfer_row(context.transfer["id"]).status == status


@then("双方锁定自动解除")
def step_unlocked_after_expire(context):
    assert _child_row(context.source["id"]).operation_locked == 0
    assert _child_row(context.target["id"]).operation_locked == 0


@then("家长收到超时通知")
def step_overdue_notice(context):
    notices = _notifications(context, scene="other.transfer_result")
    assert any("超时" in (n.get("title", "") + n.get("content", "")) for n in notices), notices


# ---------- 场景：家长撤销转让 ----------


@given('转让申请处于 "{status}"')
def step_transfer_in_status(context, status):
    context.transfer_response = _apply(context)
    assert context.transfer_response.status_code == 200, context.transfer_response.text
    context.transfer = context.transfer_response.json()
    assert context.transfer["status"] == status, context.transfer


@when("张女士撤销转让")
def step_cancel_transfer(context):
    r = context.client.post(
        f"/api/miniapp/transfers/{context.transfer['id']}/cancel", headers=context.mini
    )
    assert r.status_code == 200, r.text
    context.cancelled = r.json()


@then('申请状态 "cancelled" 双方锁定解除')
def step_cancelled_transfer(context):
    status = "cancelled"
    assert context.cancelled["status"] == status, context.cancelled
    assert _transfer_row(context.transfer["id"]).status == status
    assert _child_row(context.source["id"]).operation_locked == 0
    assert _child_row(context.target["id"]).operation_locked == 0


# ---------- 场景：成就数据不随转让转移 ----------


@given("转让已完成")
def step_transfer_completed(context):
    _seed_growth(context, context.source["id"])
    context.source_summary_before = _summary(context, context.source["id"])
    context.transfer_response = _apply(context)
    assert context.transfer_response.status_code == 200, context.transfer_response.text
    context.transfer = context.transfer_response.json()
    approved = _approve(context, context.transfer["id"])
    assert approved.status_code == 200, approved.text
    assert _transfer_row(context.transfer["id"]).status == "approved"


@then("小红的词数、积分、等级、打卡从零开始")
def step_target_starts_from_zero(context):
    from backend.domain.reading.models import CheckIn

    summary = _summary(context, context.target["id"])
    assert summary["words_total"] == 0, summary
    assert summary["points_total"] == 0, summary
    assert summary["level"] == "A", f"受让方从 A 级开始（不继承转出方等级）：{summary}"
    with _db() as db:
        checkins = (
            db.query(CheckIn)
            .filter(CheckIn.child_id == context.target["id"], CheckIn.is_deleted == 0)
            .count()
        )
    assert checkins == 0, "受让方打卡不继承"


@then("小明的历史成就数据保留只读")
def step_source_keeps_history(context):
    before = context.source_summary_before
    after = _summary(context, context.source["id"])
    assert before["words_total"] > 0, before
    assert after["words_total"] == before["words_total"], (before, after)
    assert after["points_total"] == before["points_total"], (before, after)
    assert after["level"] == before["level"], (before, after)
    # 只读：退会后写动作被拒（生词本写入 = R-313 的 VOCAB_WRITE）
    _, copy_id = _book_with_copy(context, "退会后只读样例")
    borrow = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": context.source["id"], "copy_id": copy_id},
        headers=context.h,
    )
    assert borrow.status_code == 422, borrow.text
    assert "已退会" in borrow.json()["detail"], borrow.text
