# features/steps/withdrawal_steps.py — FEAT-028 退会申请 步骤定义
"""2026-10-13（第五十一轮）BDD 解封第一批 · **退会域** 8 个场景。

退会是"钱与服务同时收口"的动作：提交前置七项、审核期冻结 11 类动作、审核通过按剩余天数
比例结算、全部退款完成才落 withdrawn。这些口径此前只有单测覆盖，本文件钉成**端到端行为**。

走真实链路（真实 MySQL + TestClient）：建档 → 押金缴纳 → 观察期费+年费（正式会员）
→ 小程序提交/撤销退会 → 管理端审核 → 退款审核/执行 → 会员状态收口。

**判据必须与实现同源（红线 108）——写这一步前逐条亲验的实现事实**：
- 审核通过后的状态是 `refunding`（`review` 内部先 assert 到 `pending_settle` 再立刻推进，
  列表可见的是 refunding；无可退款时直接 `completed`）。
- "退会期间禁止删除档案"的**在位拦截是订单守卫**（`ChildService.delete`：有订单 → 409），
  冻结标志本身不判删档。可达性：冻结态必经在册会员 → 必有订单 → 结果一致；
  本文件按"结果"断言（409），并在注释里标明机制，不假装是冻结标志拦的。
- 冻结期"续借/归还"在产品路径上不可达（提交退会要求无在借），故用**DB 直插一条在借记录**
  构造冻结态下的防御性场景：续借必须被冻结拦住、归还必须仍放行。
- "按剩余天数比例"若都用今天付款，"剩余=全额"会让比例规则退化成恒等；故结算场景把
  `paid_at` 回拨（年费 100 天 / 观察期费 10 天）后**独立复算**期望金额再与系统结果比对。
"""

from datetime import datetime, timedelta
from decimal import Decimal

from behave import given, then, when

from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _h

# ---------- 基建 ----------


def _amount(value) -> Decimal:
    return Decimal(str(value))


def _family(client, h, phone, parent_name, child_name):
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


def _child_row(child_id: int):
    from backend.domain.identity.models import Child

    with _db() as db:
        return db.query(Child).filter(Child.id == child_id).first()


def _withdrawal_row(req_id: int):
    from backend.domain.identity.models import WithdrawalRequest

    with _db() as db:
        return db.query(WithdrawalRequest).filter(WithdrawalRequest.id == req_id).first()


def _orders_of(context) -> list:
    from backend.domain.identity.models import Order

    with _db() as db:
        return (
            db.query(Order)
            .filter(Order.child_id == context.child["id"], Order.is_deleted == 0)
            .all()
        )


def _pay_member_fee(context, order_type: str, child_id: int | None = None) -> dict:
    cid = child_id or context.child["id"]
    o = context.client.post(
        "/api/admin/orders", json={"child_id": cid, "order_type": order_type}, headers=context.h
    ).json()
    rc = context.client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert rc.status_code == 200, rc.text
    return o


def _deposit(context, child_id: int | None = None) -> dict:
    cid = child_id or context.child["id"]
    r = context.client.get(f"/api/admin/deposits/children/{cid}", headers=context.h)
    assert r.status_code == 200, r.text
    return r.json()


def _deduct(context, amount: int, reason: str) -> dict:
    r = context.client.post(
        f"/api/admin/deposits/children/{context.child['id']}/deduct",
        json={"amount": f"{amount}.00", "reason": reason},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _apply_raw(context, reason: str = "搬家离店"):
    return context.client.post(
        "/api/miniapp/withdrawals",
        json={"child_id": context.child["id"], "reason": reason},
        headers=context.mini,
    )


def _apply(context, reason: str = "搬家离店") -> dict:
    r = _apply_raw(context, reason)
    assert r.status_code == 200, r.text
    return r.json()


def _preview(context, req_id: int) -> dict:
    r = context.client.get(f"/api/admin/withdrawals/{req_id}/settle-preview", headers=context.h)
    assert r.status_code == 200, r.text
    return r.json()


def _approve(context, req_id: int, remark: str = "同意退会") -> dict:
    r = context.client.post(
        f"/api/admin/withdrawals/{req_id}/review",
        json={"approve": True, "remark": remark},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _refunds(context, status: str = "pending") -> list[dict]:
    r = context.client.get(
        "/api/admin/refund-requests", params={"status": status}, headers=context.h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _execute_all_refunds(context) -> list[dict]:
    """把该孩子名下所有退款单走完 审核 → 执行（结算退款全部成功）。"""
    done = []
    for row in _refunds(context, "pending"):
        if row["child_id"] != context.child["id"]:
            continue
        approved = context.client.post(
            f"/api/admin/refund-requests/{row['id']}/review",
            json={"approve": True, "remark": "同意退款"},
            headers=context.h,
        )
        assert approved.status_code == 200, approved.text
        executed = context.client.post(
            f"/api/admin/refund-requests/{row['id']}/execute",
            json={"success": True, "remark": "线下退还"},
            headers=context.h,
        )
        assert executed.status_code == 200, executed.text
        done.append(row)
    assert done, "没有任何退款单可执行——结算应有退款单"
    return done


def _book_with_copy(context, title: str) -> tuple[int, int]:
    book = context.client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 100, "copy_count": 1},
        headers=context.h,
    ).json()
    force_book_on(context.client, context.h, book["id"])
    copies = context.client.get(f"/api/admin/books/{book['id']}/copies", headers=context.h).json()
    return book["id"], copies[0]["id"]


def _seed_active_borrow(context, book_id: int, copy_id: int) -> int:
    """DB 直插一条在借记录 + 把副本置为借出。

    为什么要绕产品路径：提交退会的前置就是"无在借"，**冻结态下的在借**在产品里不可达
    （冻结期借书被拦）。这一条是**防御性**前置——用来验"冻结不该拦住归还、但必须拦住续借"。
    """
    from backend.domain.catalog.models import BookCopy
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        now = datetime.now()
        rec = BorrowRecord(
            child_id=context.child["id"],
            copy_id=copy_id,
            book_id=book_id,
            borrowed_at=now - timedelta(days=1),
            due_at=now + timedelta(days=13),
            status=BorrowRecord.STATUS_ACTIVE,
            renew_used=0,
            is_deleted=0,
        )
        db.add(rec)
        db.query(BookCopy).filter(BookCopy.id == copy_id).update(
            {"status": BookCopy.STATUS_BORROWED}
        )
        db.commit()
        return rec.id


def _seed_growth(context, words: int = 300, points: int = 150, level: str = "B") -> None:
    """造历史成就（词数/积分/等级）——退会必须保留这些，用真读接口比对前后。"""
    from backend.domain.growth.models import ChildGrowthState, PointLedger, WordsLedger

    book_ids = [_book_with_copy(context, f"成就样例 {i}")[0] for i in range(3)]
    context.audio_book_id = book_ids[0]  # 退会后"不可听音频"要用真实书目 id 走预检端点
    with _db() as db:
        for book_id in book_ids:
            db.add(
                WordsLedger(
                    child_id=context.child["id"],
                    book_id=book_id,
                    word_count=words // 3,
                    source="quiz",
                    is_deleted=0,
                )
            )
        db.add(
            PointLedger(
                child_id=context.child["id"],
                points=points,
                reason_type="words_convert",
                detail="历史成就（BDD 前置）",
                is_deleted=0,
            )
        )
        state = (
            db.query(ChildGrowthState)
            .filter(
                ChildGrowthState.child_id == context.child["id"], ChildGrowthState.is_deleted == 0
            )
            .first()
        )
        if not state:
            state = ChildGrowthState(child_id=context.child["id"])
            db.add(state)
        state.words_total = words
        state.points_total = points
        state.level = level
        db.commit()


def _summary(context) -> dict:
    r = context.client.get(
        "/api/miniapp/growth/summary",
        params={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _rewind_paid_at(context, order_type: str, days: int) -> None:
    """把某类订单的付款时刻回拨 N 天——"按剩余天数比例"必须用真实天数差验，否则退化成恒等。

    多减 1 秒是**必须**的：MySQL `DATETIME`（无小数位）写入时按**四舍五入**落秒，带微秒的
    `now - N 天` 可能被推后最多 0.5 秒，于是 `(now - paid_at).days` 从 N 掉成 N−1
    （首轮实证：500 元观察期费算出 350.00 = 500×21/30，即已用 9 天）。
    """
    from backend.domain.identity.models import Order

    with _db() as db:
        rows = (
            db.query(Order)
            .filter(
                Order.child_id == context.child["id"],
                Order.order_type == order_type,
                Order.is_deleted == 0,
            )
            .all()
        )
        assert rows, f"没有 {order_type} 订单可回拨"
        target = datetime.now() - timedelta(days=days, seconds=1)
        for row in rows:
            row.paid_at = target
        db.commit()


def _periods(context) -> tuple[int, int]:
    """观察期 / 年费期限（天）——与 `refund_rules.period_days` 同源配置，避免把 30/365 写死在断言里。"""
    from backend.common.config_service import ConfigService

    with _db() as db:
        svc = ConfigService(db)
        return (
            int(svc.get_value("observation_period_days", "30")),
            int(svc.get_value("formal_period_days", "365")),
        )


def _prorated(paid: Decimal, period_days: int, used_days: int) -> Decimal:
    """独立复算（不复用服务端函数）：可退 = 实付 ×（期限 − 已用）/ 期限。"""
    remaining = max(0, period_days - used_days)
    return (paid * Decimal(remaining) / Decimal(period_days)).quantize(Decimal("0.01"))


# ---------- 背景 ----------


@given('孩子 "{child_name}" 是正式会员 押金已缴 且家长 "{parent_name}" 可发起退会')
def step_bg_formal_with_deposit(context, child_name, parent_name):
    """背景句里的"发起退会"是**行为人**（张女士）：提交动作由各场景自己走，
    否则「提交退会申请」场景的 When 会撞上"已有进行中的退会申请"（409）。"""
    context.h = _h(context.client)
    parent, child, mini = _family(context.client, context.h, "13981037701", parent_name, child_name)
    _bind_openid(parent["id"], "o_withdraw_bdd_1")
    context.parent, context.child, context.mini = parent, child, mini
    context.child_name = child_name
    dep_order = context.client.post(
        f"/api/admin/deposits/children/{child['id']}/orders", headers=context.h
    ).json()
    paidd = context.client.post(
        f"/api/admin/orders/{dep_order['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert paidd.status_code == 200, paidd.text
    _pay_member_fee(context, "observation_fee")
    _pay_member_fee(context, "formal_fee")
    assert _child_row(child["id"]).member_status == "formal", "背景要求：正式会员"
    assert _amount(_deposit(context)["available_amount"]) > 0, "背景要求：押金已缴"


# ---------- 场景：提交退会申请 ----------


@given("小明无在借图书、无逾期、无未处理遗失损坏、无待结清赔偿")
def step_no_obligations(context):
    """把 Given 当断言逐项核一遍（前置与场景文本漂移时立刻红，而不是等到最后才奇怪）。"""
    from backend.domain.billing.models import Deposit
    from backend.domain.catalog.models import BookCopy
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        borrows = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == context.child["id"],
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .count()
        )
        dep = (
            db.query(Deposit)
            .filter(Deposit.child_id == context.child["id"], Deposit.is_deleted == 0)
            .first()
        )
        bad_copies = (
            db.query(BookCopy)
            .filter(
                BookCopy.status.in_([BookCopy.STATUS_LOST, BookCopy.STATUS_MAINTENANCE]),
                BookCopy.is_deleted == 0,
            )
            .count()
        )
    assert borrows == 0, f"不该有在借/逾期（{borrows}）"
    assert dep and _amount(dep.unpaid_balance) == 0, "不该有待结清赔偿"
    assert bad_copies == 0, "不该有未处理的遗失/损坏副本"


@when("张女士在小程序提交退会申请")
def step_submit(context):
    context.withdrawal = _apply(context)


@then('申请创建 状态为 "{status}"')
def step_submitted(context, status):
    assert context.withdrawal["status"] == status, context.withdrawal
    row = _withdrawal_row(context.withdrawal["id"])
    assert row.status == status and row.child_id == context.child["id"], row


# ---------- 场景：有在借图书时禁止提交 ----------


@given("小明有 {count:d} 本在借图书")
def step_has_borrows(context, count):
    for i in range(count):
        _, copy_id = _book_with_copy(context, f"在借样例 {i + 1}")
        r = context.client.post(
            "/api/admin/circulation/borrow",
            json={"child_id": context.child["id"], "copy_id": copy_id},
            headers=context.h,
        )
        assert r.status_code == 200, r.text


@when("张女士提交退会申请")
def step_submit_raw(context):
    context.rejected = _apply_raw(context)


@then("系统拒绝并提示先归还图书")
def step_rejected_borrows(context):
    assert context.rejected.status_code == 422, context.rejected.text
    detail = context.rejected.json()["detail"]
    assert "还有 2 本图书未归还" in detail, detail


# ---------- 场景：有待结清赔偿时禁止提交 ----------


@given("小明有待结清赔偿 {amount:d} 元")
def step_has_debt(context, amount):
    standard = int(_amount(_deposit(context)["available_amount"]))
    _deduct(context, standard + amount, f"遗失赔偿（超出押金 {amount} 元）")
    dep = _deposit(context)
    assert _amount(dep["unpaid_balance"]) == Decimal(amount), dep
    assert dep["status"] == "fully_deducted", dep


@then("系统拒绝并提示先结清赔偿")
def step_rejected_debt(context):
    assert context.rejected.status_code == 422, context.rejected.text
    detail = context.rejected.json()["detail"]
    assert "有未结清赔偿款 70.00 元" in detail, detail


# ---------- 场景：退会期间的操作锁定 ----------


@given("退会申请处于进行中")
def step_applying(context):
    context.withdrawal = _apply(context)
    child = _child_row(context.child["id"])
    assert child.operation_locked == 1, "提交退会后应进入冻结"


@then("小明禁止借书、预约、续借、开始新测验、发起转让、创建新会员订单、删除档案")
def step_all_frozen(context):
    book_id, copy_id = _book_with_copy(context, "冻结期借书样例")
    borrow = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": context.child["id"], "copy_id": copy_id},
        headers=context.h,
    )
    assert borrow.status_code == 422, borrow.text
    assert "借书已冻结" in borrow.json()["detail"], borrow.text

    reserve = context.client.post(
        "/api/miniapp/reservations",
        json={"child_id": context.child["id"], "book_id": book_id},
        headers=context.mini,
    )
    assert reserve.status_code == 422, reserve.text
    assert "预约已冻结" in reserve.json()["detail"], reserve.text

    # 续借：冻结态下的在借记录（产品路径不可达，见 _seed_active_borrow 说明）
    rbook_id, rcopy_id = _book_with_copy(context, "冻结期续借样例")
    record_id = _seed_active_borrow(context, rbook_id, rcopy_id)
    context.active_record_id, context.active_copy_id = record_id, rcopy_id
    renew = context.client.post(
        "/api/admin/circulation/renew", json={"record_id": record_id}, headers=context.h
    )
    assert renew.status_code == 422, renew.text
    assert "续借已冻结" in renew.json()["detail"], renew.text

    quiz = context.client.post(
        f"/api/miniapp/quiz/{book_id}/submit",
        json={"child_id": context.child["id"], "answers": ["A"]},
        headers=context.mini,
    )
    assert quiz.status_code == 422, quiz.text
    assert "新小挑战已冻结" in quiz.json()["detail"], quiz.text

    sibling = context.client.post(
        f"/api/admin/members/parents/{context.parent['id']}/children",
        json={"name": "小红"},
        headers=context.h,
    ).json()
    transfer = context.client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": context.child["id"], "target_child_id": sibling["id"]},
        headers=context.mini,
    )
    assert transfer.status_code == 422, transfer.text
    assert "转出方未处于冻结流程" in transfer.json()["detail"], transfer.text

    miniapp_order = context.client.post(
        "/api/miniapp/orders",
        json={"child_id": context.child["id"], "order_type": "formal_fee"},
        headers=context.mini,
    )
    assert miniapp_order.status_code == 422, miniapp_order.text
    assert "审核流程中" in miniapp_order.json()["detail"], miniapp_order.text

    admin_order = context.client.post(
        "/api/admin/orders",
        json={"child_id": context.child["id"], "order_type": "custom", "amount": "88.00"},
        headers=context.h,
    )
    assert admin_order.status_code == 422, admin_order.text
    assert "审核流程中" in admin_order.json()["detail"], admin_order.text

    # 删档：冻结判据优先于订单守卫（2026-10-13 补齐——原实现只靠订单守卫，
    # 受让方那类"无订单的冻结态"能钻过去）
    deleted = context.client.delete(
        f"/api/admin/members/children/{context.child['id']}", headers=context.h
    )
    assert deleted.status_code == 422, deleted.text
    assert "不能删除档案" in deleted.json()["detail"], deleted.text


@then("允许归还图书与查看历史数据")
def step_return_and_read_allowed(context):
    returned = context.client.post(
        "/api/admin/circulation/return",
        json={"copy_id": context.active_copy_id, "condition": "normal"},
        headers=context.h,
    )
    assert returned.status_code == 200, returned.text
    summary = _summary(context)
    assert "words_total" in summary and "level" in summary, summary


# ---------- 场景：审核通过进入结算 ----------


@given("退会申请通过审核")
def step_approved(context):
    context.withdrawal = _apply(context)
    # 让"按剩余天数比例"可验：年费回拨 100 天、观察期费回拨 10 天
    _rewind_paid_at(context, "formal_fee", 100)
    _rewind_paid_at(context, "observation_fee", 10)
    context.preview = _preview(context, context.withdrawal["id"])
    reviewed = _approve(context, context.withdrawal["id"])
    context.review_result = reviewed
    context.settle_refunds = [r for r in _refunds(context) if r["child_id"] == context.child["id"]]


@given("结算退款全部成功")
def step_all_refunds_success(context):
    context.withdrawal = _apply(context)
    _seed_growth(context)
    context.summary_before = _summary(context)
    _approve(context, context.withdrawal["id"])
    context.executed_refunds = _execute_all_refunds(context)


@then("系统计算观察期费、年费、押金各自可退金额")
def step_settlement_math(context):
    preview = context.preview
    items = preview["items"]
    order_types = {o.order_no: o.order_type for o in _orders_of(context)}
    by_type = {
        order_types.get(i["order_no"]) or "deposit": i for i in items if i["kind"] != "deposit"
    }
    by_type["deposit"] = next((i for i in items if i["kind"] == "deposit"), None)
    assert {"observation_fee", "formal_fee", "deposit"} <= set(by_type), items
    obs_period, formal_period = _periods(context)
    # 观察期费：30 天期，已用 10 天 → 可退 = 实付 × 20/30（独立复算）
    obs_paid = [
        _amount(o.amount) for o in _orders_of(context) if o.order_type == "observation_fee"
    ][0]
    assert _amount(by_type["observation_fee"]["amount"]) == _prorated(obs_paid, obs_period, 10), (
        by_type,
        obs_paid,
        obs_period,
    )
    assert f"{obs_period} 天期" in by_type["observation_fee"]["rule"], by_type
    # 年费：365 天期，已用 100 天 → 可退 = 实付 × 265/365（独立复算）
    formal_paid = [_amount(o.amount) for o in _orders_of(context) if o.order_type == "formal_fee"][
        0
    ]
    assert _amount(by_type["formal_fee"]["amount"]) == _prorated(formal_paid, formal_period, 100), (
        by_type,
        formal_paid,
        formal_period,
    )
    assert "年费按剩余天数比例" in by_type["formal_fee"]["rule"], by_type
    # 押金：退可用余额（已扣除部分不退）
    assert _amount(by_type["deposit"]["amount"]) == _amount(_deposit(context)["available_amount"])
    assert by_type["deposit"]["rule"] == "押金退可用余额（已扣除部分不退）", by_type
    assert _amount(preview["total"]) == sum(_amount(i["amount"]) for i in items), preview


@then("生成退款单进入退款流程")
def step_refunds_created(context):
    refunds = context.settle_refunds
    assert len(refunds) == len(context.preview["items"]), (refunds, context.preview)
    # 列表按 id 倒序返回，预估按订单顺序返回——**按身份配对**，不要依赖顺序
    order_types = {o.order_no: o.order_type for o in _orders_of(context)}
    by_key = {}
    for refund in refunds:
        by_key[order_types.get(refund.get("order_no")) or "deposit"] = refund
    for item in context.preview["items"]:
        key = order_types.get(item["order_no"]) or "deposit"
        refund = by_key.get(key)
        assert refund is not None, (key, by_key, item)
        assert _amount(refund["amount"]) == _amount(item["amount"]), (item, refund)
        assert refund["status"] == "pending", refund
        assert refund["reason"].startswith("退会结算："), refund


# ---------- 场景：管理端可直接审核直接退会申请 ----------


@given('张女士的退会申请处于 "{status}" 且来源为直接申请')
def step_applying_normal(context, status):
    context.withdrawal = _apply(context)
    row = _withdrawal_row(context.withdrawal["id"])
    assert row.status == status, row
    assert row.source == "normal", row


@when("管理员在退会列表看到该申请并点击通过")
def step_admin_sees_and_approves(context):
    listed = context.client.get(
        "/api/admin/withdrawals", params={"status": "applying"}, headers=context.h
    ).json()
    rows = [r for r in listed if r["id"] == context.withdrawal["id"]]
    assert rows, f"待审核列表必须能看到该申请：{listed[:3]}"
    assert rows[0]["child_name"] == context.child_name, rows[0]
    context.review_result = _approve(context, context.withdrawal["id"])


@then('审核通过后申请状态变为 "{status}"')
def step_status_after_review(context, status):
    assert context.review_result["status"] == status, context.review_result
    assert _withdrawal_row(context.withdrawal["id"]).status == status


@then("生成会员费按比例退款单与押金退款单")
def step_member_and_deposit_refunds(context):
    refunds = [r for r in _refunds(context) if r["child_id"] == context.child["id"]]
    kinds = sorted(r["kind"] for r in refunds)
    assert kinds == ["deposit", "order", "order"], refunds
    order_types = {o.id: o.order_type for o in _orders_of(context)}
    by_type = {order_types.get(r["order_id"]): r for r in refunds if r["kind"] == "order"}
    assert set(by_type) == {"observation_fee", "formal_fee"}, by_type
    for otype in ("observation_fee", "formal_fee"):
        assert "按剩余天数比例" in by_type[otype]["reason"], by_type[otype]
    deposit_refund = [r for r in refunds if r["kind"] == "deposit"][0]
    assert _amount(deposit_refund["amount"]) == _amount(_deposit(context)["available_amount"]), (
        deposit_refund,
        _deposit(context),
    )


# ---------- 场景：全部退款完成后退会完成 ----------


@then('小明会员状态变为 "withdrawn" 退会状态 "completed"')
def step_withdrawn(context):
    child = _child_row(context.child["id"])
    assert child.member_status == "withdrawn", child.member_status
    assert child.operation_locked == 0, "退会完成后必须解锁"
    assert _withdrawal_row(context.withdrawal["id"]).status == "completed"
    listed = context.client.get(
        "/api/miniapp/withdrawals",
        params={"child_id": context.child["id"]},
        headers=context.mini,
    ).json()
    assert [r["status"] for r in listed] == ["completed"], listed


@then("小明不可借书、听音频、做测验")
def step_no_service(context):
    _, copy_id = _book_with_copy(context, "退会后借书样例")
    borrow = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": context.child["id"], "copy_id": copy_id},
        headers=context.h,
    )
    assert borrow.status_code == 422, borrow.text
    assert "已退会" in borrow.json()["detail"], borrow.text

    audio = context.client.get(
        f"/api/miniapp/books/{context.audio_book_id}/audio-permission",
        params={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert audio.status_code == 200, audio.text
    assert audio.json() == {"allowed": False, "reason": "withdrawn"}, audio.json()

    quiz = context.client.post(
        f"/api/miniapp/quiz/{context.audio_book_id}/submit",
        json={"child_id": context.child["id"], "answers": ["A"]},
        headers=context.mini,
    )
    assert quiz.status_code == 422, quiz.text
    assert "已退会" in quiz.json()["detail"], quiz.text


@then("历史词数、积分、等级保留")
def step_growth_kept(context):
    after = _summary(context)
    before = context.summary_before
    assert before["words_total"] > 0 and before["points_total"] > 0, before
    assert after["words_total"] == before["words_total"], (before, after)
    assert after["points_total"] == before["points_total"], (before, after)
    assert after["level"] == before["level"], (before, after)


# ---------- 场景：用户撤销退会申请 ----------


@given('退会申请处于 "{status}"')
def step_withdrawal_in_status(context, status):
    context.withdrawal = _apply(context)
    assert _withdrawal_row(context.withdrawal["id"]).status == status


@when("张女士撤销")
def step_cancel(context):
    r = context.client.post(
        f"/api/miniapp/withdrawals/{context.withdrawal['id']}/cancel",
        json={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    context.cancelled = r.json()


@then('申请状态变为 "{status}" 锁定全部解除')
def step_cancelled_unlocked(context, status):
    assert context.cancelled["status"] == status, context.cancelled
    assert _withdrawal_row(context.withdrawal["id"]).status == status
    child = _child_row(context.child["id"])
    assert child.operation_locked == 0, "撤销后必须解锁"
    _, copy_id = _book_with_copy(context, "撤销后借书样例")
    borrow = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": context.child["id"], "copy_id": copy_id},
        headers=context.h,
    )
    assert borrow.status_code == 200, f"解锁后应可借书：{borrow.text}"
