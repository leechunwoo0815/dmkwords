# features/steps/membership_steps.py — FEAT-021/022/023/024/030 会员生命周期 步骤定义
"""2026-10-14（第五十八批·BDD 解封第五批）· **会员生命周期 13 场景**全部转真实执行
（文件级 `@draft` 撤销；7 处场景文本按实现/PRD 现行口径就地改写，逐处注明见 `features/membership.feature` 头部）。

判据与实现同源（动手前逐条亲验，红线 108/136）：
- **开通/续费**（PRD §3.2/§3.4）：观察期到期日 = 付款日 + `observation_period_days(30)`；年费 =
  `formal_period_days(365)`；**提前续费从原到期日顺延、过期后续费从付款日起算**（`order_service._settle_paid`：
  原到期日仍有效则作基期，否则取今天）。
- **自动到期**（§3.3）：`member_expire_check` 任务（300 秒轮询，**不是零点 cron**）——formal 到期 → `expired`；
  observation 到期 → `pending_evaluation`；落库条件是 `member_expire < today`（"最后一天"当天还不落）。
- **二孩九折**（§3.2）：**只适用年费**（6000→5400），判据是**下单那一刻**账号下另有有效会员孩子
  （观察期/待评估/正式都算）；观察期费、活动费、押金一律原价。
- **过期边界**（§3.7 表格 + 问答案）：预约/续借家长端自动拦截；**借书是馆员端"需放行"软闸**；
  在借照常归还、在借音频可听、已解锁测验可完成且计词数；历史数据全保留。
- **重新入会**（§3.4）：可重走观察期或直接买年费，历史不清零；**押金不自动复位**
  （`borrow_gate.deposit_gate` 白名单，退会结算后是 `refunded`）→ 本文件不断言"重新入会后可直接借书"。
- **无已支付订单不能转正**：转正只能由已支付年费订单触发；`evaluate-approve` 只建单且要求已处于待评估
  （对 `none` 直接 422）→ 用它当"绕过订单"的探针。

造数纪律：**时间态只能造数**（"到期日为昨天"/"待评估 45 天"/"5 天前到期"——墙钟不可回拨），
一律走 `_child_db` 并注明；`Child.update_time` 有 `onupdate`，与状态同一笔 UPDATE 写，避免被覆盖。
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from behave import given, then, when

from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _h

PHONE = "13981037951"  # 会员域专用家长（手机号唯一，与其它域错开）
DAY = timedelta(days=1)


# ---------- 基建 ----------


def _db_session():
    return _db()


def _admin(context) -> dict:
    if getattr(context, "h", None) is None:
        context.h = _h(context.client)
    return context.h


def _bg(context) -> dict:
    """背景：家长「张女士」+ 小明/小红 + 小程序登录头。

    **同一家长**是二孩折扣场景的前提（折扣看"账号下另有有效会员孩子"）；
    一次登录覆盖两个孩子（`child_of_parent` 归属校验）。
    """
    if getattr(context, "parent", None) is None:
        _admin(context)
        context.parent = context.client.post(
            "/api/admin/members/parents",
            json={"name": "张女士", "phone": PHONE},
            headers=context.h,
        ).json()
        assert context.parent.get("id"), context.parent
        context.kids = {}
        for name in ("小明", "小红"):
            child = context.client.post(
                f"/api/admin/members/parents/{context.parent['id']}/children",
                json={"name": name},
                headers=context.h,
            ).json()
            assert child.get("id"), child
            context.kids[name] = child
        login = context.client.post(
            "/api/miniapp/login", json={"phone": PHONE, "code": "1234"}
        ).json()
        assert login.get("token"), login
        context.mini_token = login["token"]
        context.mini = {"Authorization": f"Bearer {login['token']}"}
    return context.parent


def _kid(context, name: str) -> dict:
    _bg(context)
    return context.kids[name]


def _child_view(context, name: str) -> dict:
    """读孩子子档（管理端列表端点 → member_status / member_expire）。"""
    _bg(context)
    rows = context.client.get(
        f"/api/admin/members/parents/{context.parent['id']}/children", headers=context.h
    ).json()
    return next(c for c in rows if c["name"] == name)


def _child_db(context, name: str, **fields) -> None:
    """直改会员字段——时间态只能造数（墙钟不可回拨）。

    `update_time` 若要在同一笔里改，必须显式写进 `fields`（模型有 `onupdate`，
    分开写会被自动刷新覆盖 —— 见 `base_model` 与 `docs/09` 第五十八轮 §三）。
    """
    from backend.domain.identity.models import Child

    kid = _kid(context, name)
    with _db_session() as db:
        db.query(Child).filter(Child.id == kid["id"]).update(fields)
        db.commit()
    context.kids[name].update(fields)


def _order(
    context, name: str, order_type: str, *, confirm: bool = True, expect_amount=None
) -> dict:
    """建单（+可选收款确认）。金额一律 Decimal 比。"""
    kid = _kid(context, name)
    r = context.client.post(
        "/api/admin/orders",
        json={"child_id": kid["id"], "order_type": order_type},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    order = r.json()
    if expect_amount is not None:
        assert Decimal(order["amount"]) == Decimal(str(expect_amount)), (
            f"{name} 的 {order_type} 应 {expect_amount}，实得 {order['amount']}"
        )
    if confirm:
        c = context.client.post(
            f"/api/admin/orders/{order['id']}/confirm-payment",
            json={"pay_method": "scan"},
            headers=context.h,
        )
        assert c.status_code == 200, c.text
        order = c.json()
    context.last_order = order
    return order


def _pay_deposit(context, name: str) -> None:
    """押金已缴（借书前置；押金单走独立端点）。"""
    kid = _kid(context, name)
    r = context.client.post(
        f"/api/admin/deposits/children/{kid['id']}/orders", headers=context.h
    ).json()
    c = context.client.post(
        f"/api/admin/orders/{r['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert c.status_code == 200, c.text


def _run_task(context, name: str) -> dict:
    r = context.client.post(f"/api/admin/tasks/{name}/run", headers=context.h)
    assert r.status_code == 200, r.text
    return r.json()


def _book(context, title: str = "会员样例书", questions: int = 0, seconds: int = 1800) -> dict:
    """建书（上架）+ 1 副本 + 音频 + N 道判断题（答案统一「对」，便于整卷提交）。"""
    created = context.client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 2500, "copy_count": 1},
        headers=context.h,
    )
    assert created.status_code == 200, created.text
    book = created.json()
    force_book_on(context.client, context.h, book["id"])
    rel = f"bdd/audio-{book['id']}.mp3"
    from backend.config import get_settings
    from backend.domain.catalog.models import Book

    full = Path(get_settings().UPLOADS_DIR) / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    if not full.is_file():
        full.write_bytes(b"ID3fake-mp3")
    with _db_session() as db:
        db.query(Book).filter(Book.id == book["id"]).update(
            {"audio_path": rel, "audio_duration_seconds": seconds}
        )
        db.commit()
    for i in range(questions):
        q = context.client.post(
            f"/api/admin/books/{book['id']}/questions",
            json={
                "question_type": "boolean",
                "question_text": f"{title} 第 {i + 1} 题",
                "options": ["对", "错"],
                "answer": "对",
            },
            headers=context.h,
        )
        assert q.status_code == 200, q.text
    copies = context.client.get(f"/api/admin/books/{book['id']}/copies", headers=context.h).json()
    return {
        "id": book["id"],
        "title": title,
        "copy_id": copies[0]["id"],
        "seconds": seconds,
        "questions": questions,
    }


def _borrow(context, name: str, book: dict, *, override_reason: str | None = None):
    kid = _kid(context, name)
    body: dict = {"child_id": kid["id"], "copy_id": book["copy_id"]}
    if override_reason is not None:
        body["override_reason"] = override_reason
    return context.client.post("/api/admin/circulation/borrow", json=body, headers=context.h)


def _audio_allowed(context, name: str, book: dict) -> bool:
    kid = _kid(context, name)
    r = context.client.get(
        f"/api/miniapp/books/{book['id']}/audio-permission",
        params={"child_id": kid["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return bool(r.json()["allowed"])


def _assert_benefits(context, name: str, book: dict) -> None:
    """「权益保留」的最小真跑集：**借书 200 + 音频预检 ok + 测验端点可进**。

    （比对比 `is_active_member` 属性更有说服力：走的是三条真实守卫链。）
    """
    kid = _kid(context, name)
    r = _borrow(context, name, book)
    assert r.status_code == 200, f"权益保留期内应可借书：{r.status_code} {r.text[:160]}"
    assert _audio_allowed(context, name, book) is True, "权益保留期内应可听音频"
    quiz = context.client.get(
        f"/api/miniapp/quiz/{book['id']}",
        params={"child_id": kid["id"]},
        headers=context.mini,
    )
    assert quiz.status_code == 200, f"权益保留期内应可进测验：{quiz.status_code} {quiz.text[:160]}"


def _seed_finished(context, name: str, book: dict) -> None:
    """造「已听完」：直插完播进度行（完播判定链本身由 `audio_reading.feature` 真跑覆盖）。"""
    from backend.domain.reading.models import ReadingProgress

    kid = _kid(context, name)
    with _db_session() as db:
        db.add(
            ReadingProgress(
                child_id=kid["id"],
                book_id=book["id"],
                intervals=f"[[0,{book['seconds']}]]",
                coverage_seconds=book["seconds"],
                total_seconds=book["seconds"],
                finished=1,
                finished_at=datetime.now(),
                last_position=book["seconds"],
                reading_minutes=book["seconds"] // 60,
                is_deleted=0,
            )
        )
        db.commit()


def _seed_history(context, name: str) -> None:
    """退会前的历史（词数 / 积分 / 等级 / 打卡）——重新入会后必须原样保留。"""
    from backend.domain.growth.models import ChildGrowthState, PointLedger, WordsLedger
    from backend.domain.reading.models import CheckIn

    kid = _kid(context, name)
    book = _book(context, "历史保留样例书")
    context.history_book = book
    with _db_session() as db:
        db.add(
            WordsLedger(
                child_id=kid["id"],
                book_id=book["id"],
                word_count=2500,
                source="quiz",
                is_deleted=0,
            )
        )
        db.add(
            PointLedger(
                child_id=kid["id"],
                points=150,
                reason_type="manual_adjust",
                detail="退会前的历史积分（B14 造数）",
                is_deleted=0,
            )
        )
        db.add(
            ChildGrowthState(
                child_id=kid["id"],
                words_total=2500,
                books_total=1,
                points_total=150,
                words_remainder=0,
                level="B",
                is_deleted=0,
            )
        )
        db.add(
            CheckIn(
                child_id=kid["id"],
                checkin_date=date.today() - 3 * DAY,
                book_id=book["id"],
                streak=3,
                is_deleted=0,
            )
        )
        db.commit()


# ================= 场景步骤 =================


# ---------- 背景 ----------


@given('家长 "{parent_name}" 名下有孩子 "{c1}" 和 "{c2}"')
def step_bg(context, parent_name, c1, c2):
    _bg(context)
    assert sorted(context.kids) == sorted([c1, c2]), context.kids


# ---------- 1 购买观察期成为观察期会员 ----------


@when("小明支付观察期费 {fee:d} 元成功")
def step_buy_observation(context, fee):
    _order(context, "小明", "observation_fee", expect_amount=fee)
    context.paid_child = "小明"


@when("小红支付年费 {fee:d} 元成功")
def step_buy_formal_xiaohong(context, fee):
    _order(context, "小红", "formal_fee", expect_amount=fee)
    context.paid_child = "小红"


@then('小明的会员状态变为 "{status}"')
def step_xiaoming_status(context, status):
    view = _child_view(context, "小明")
    assert view["member_status"] == status, view


@then('小红的会员状态变为 "{status}"')
def step_xiaohong_status(context, status):
    view = _child_view(context, "小红")
    assert view["member_status"] == status, view


@then("会员有效期从支付日起 {days:d} 天")
def step_expire_from_payment(context, days):
    view = _child_view(context, context.paid_child)
    expected = (date.today() + days * DAY).isoformat()
    assert view["member_expire"] == expected, view
    assert view["member_start"] == date.today().isoformat(), view


@then("有效期 {days:d} 天")
def step_expire_days(context, days):
    view = _child_view(context, context.paid_child)
    assert view["member_expire"] == (date.today() + days * DAY).isoformat(), view


# ---------- 2 观察期到期自动进入待评估 ----------


@given("小明的观察期已过（到期日为昨天）")
def step_observation_over(context):
    _order(context, "小明", "observation_fee")  # 真开通观察期（拿到合规的到期日与状态）
    book = _book(context, "待评估可借样例书")
    context.benefit_book = book
    _pay_deposit(context, "小明")  # 借书前置（押金与会员费并列两笔）
    _child_db(context, "小明", member_expire=date.today() - DAY)  # 造数：到期日挪到昨天
    assert _child_view(context, "小明")["member_status"] == "observation", (
        "任务落库前库里仍是观察期"
    )


@when("系统执行会员到期状态检查")
def step_run_expire_check(context):
    context.expire_task = _run_task(context, "member_expire_check")
    assert context.expire_task.get("processed", 0) >= 1, context.expire_task


@then("小明的全部会员权益继续保留")
def step_pending_keeps_benefits(context):
    _assert_benefits(context, "小明", context.benefit_book)


@then("馆员的待评估人数统计把小明算进去")
def step_pending_count(context):
    data = context.client.get("/api/admin/dashboard", headers=context.h).json()
    assert data["pending_evaluation_count"] >= 1, data
    assert data["pending_evaluation_count"] == 1, f"本场景只有小明待评估：{data}"


# ---------- 3 待评估无时限权益不失效 ----------


@given('小明处于 "pending_evaluation" 已 {days:d} 天')
def step_pending_45_days(context, days):
    _order(context, "小明", "observation_fee")
    context.benefit_book = _book(context, "待评估无时限样例书")
    _pay_deposit(context, "小明")
    # 造数：状态 + **待评估时钟**同一笔写（P2-13 第六十九批：任务按 `pending_evaluation_at`
    # 判"超过 7 天"——原先挂 update_time，家长换头像这类无关更新会把时钟归零）
    _child_db(
        context,
        "小明",
        member_status="pending_evaluation",
        member_expire=date.today() - days * DAY,
        pending_evaluation_at=datetime.now() - days * DAY,
    )
    assert _child_view(context, "小明")["member_status"] == "pending_evaluation"


@then("小明仍可借书、听音频、做测验")
def step_pending_benefits(context):
    _assert_benefits(context, "小明", context.benefit_book)


@then("待评估超过 {days:d} 天的孩子进入馆员的每周提醒名单")
def step_pending_weekly(context, days):
    """PRD §3.3：每周整理"待评估超过 7 天"的名单**提醒馆员跟进**。

    2026-10-14（第五十九批 G12）：原实现只返回一个计数（名单落不到任何人手上）→ 现在跑真任务
    会**逐人写管理端待办**（`admin.pending_evaluation`）；显示态按会员状态推导（转正/退会自动审结），
    dedup 按 ISO 周。这里两步都断：任务 processed + 待办墙上确有其人。
    """
    kid = _kid(context, "小明")
    res = _run_task(context, "pending_evaluation_weekly")
    assert res.get("processed", 0) >= 1, f"45 天 > {days} 天阈值，应进每周提醒名单：{res}"
    inbox = context.client.get(
        "/api/admin/admin-notifications", params={"page_size": 50}, headers=context.h
    ).json()
    mine = [
        n
        for n in inbox["items"]
        if n["ref_type"] == "child" and n["ref_id"] == str(kid["id"]) and n["title"] == "待评估跟进"
    ]
    assert mine, f"待评估名单要落到待办墙：{[n['title'] for n in inbox['items']]}"
    assert mine[0]["effective_status"] == "pending", mine[0]
    assert int(mine[0]["content"].split("待评估已 ")[1].split(" 天")[0]) >= days, mine[0]
    counts = context.client.get("/api/admin/todo-counts", headers=context.h).json()
    assert counts["pending_evaluation_followup"] >= 1, counts


# ---------- 4/5/6/7 年费、九折与打折边界 ----------


@given("小明已是有效会员")
def step_xiaoming_active(context):
    _order(context, "小明", "observation_fee")


@when("张女士为小红下单年费")
def step_order_formal_for_xiaohong(context):
    _order(context, "小红", "formal_fee", confirm=False)


@when("张女士立即为小红下单年费")
def step_order_formal_for_xiaohong_now(context):
    _order(context, "小红", "formal_fee", confirm=False)


@then("订单金额自动为 {amount:d} 元")
def step_order_amount(context, amount):
    assert Decimal(context.last_order["amount"]) == Decimal(amount), context.last_order


@then("下单即按 {amount:d} 计价（无人工审批环节）")
def step_order_priced_directly(context, amount):
    """系统**没有"折扣申请"这个面**：下单即由服务端定价、订单直接落"待人工确认收款"
    （`Order.STATUS_PENDING_MANUAL`；折扣没有任何审批/申请态可言）。"""
    order = context.last_order
    assert Decimal(order["amount"]) == Decimal(amount), order
    assert order["status"] == "pending_manual_confirm", order


@given("小明的年费订单已创建但尚未支付")
def step_xiaoming_unpaid_formal(context):
    _order(context, "小明", "formal_fee", confirm=False)
    context.unpaid_order = context.last_order
    assert _child_view(context, "小明")["member_status"] == "none", "未收款不算入会"


@then("小红的订单金额为 {amount:d} 元")
def step_xiaohong_order_amount(context, amount):
    assert Decimal(context.last_order["amount"]) == Decimal(amount), context.last_order


@then("此时小明还不属于有效会员（折扣只看下单时刻）")
def step_xiaoming_not_active(context):
    view = _child_view(context, "小明")
    assert view["member_status"] not in ("observation", "pending_evaluation", "formal"), view
    assert context.unpaid_order["status"] == "pending_manual_confirm", context.unpaid_order
    # 边界：折扣判定发生在**下单那一刻**（订单已创建 ≠ 已入会；收款后状态才翻）
    assert Decimal(context.last_order["amount"]) == Decimal(6000), context.last_order


@when("张女士为小红购买观察期或报名付费活动")
def step_xiaohong_no_discount(context):
    context.obs_order = _order(context, "小红", "observation_fee", confirm=False)
    # 付费活动：建活动（含封面/名额等必填）→ 由小程序报名（免费+付费并存，本处走付费）
    act = context.client.post(
        "/api/admin/activities",
        json={
            "title": "会员折扣边界活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内一层",
            "max_quota": 20,
            "fee": 50,
            "description": "B14：活动费不打折",
            "member_only": False,
        },
        headers=context.h,
    )
    assert act.status_code == 200, act.text
    activity = act.json()
    kid = _kid(context, "小红")
    enroll = context.client.post(
        f"/api/miniapp/activities/{activity['id']}/enroll",
        json={"child_id": kid["id"]},
        headers=context.mini,
    )
    assert enroll.status_code == 200, enroll.text
    order_id = enroll.json()["order_id"]
    rows = context.client.get(
        "/api/admin/orders", params={"page_size": 50}, headers=context.h
    ).json()
    context.activity_order = next(o for o in rows["items"] if o["id"] == order_id)
    context.activity = activity


@then("均按原价收取不享受九折")
def step_no_discount_anywhere(context):
    """九折只认**年费**（PRD §3.2）：观察期费与活动费都按原价。"""
    assert Decimal(context.obs_order["amount"]) == Decimal(500), context.obs_order
    assert Decimal(context.activity_order["amount"]) == Decimal("50.00"), context.activity_order
    assert Decimal(context.obs_order["amount"]) != Decimal("450"), "观察期费不得打折"
    assert Decimal(context.activity_order["amount"]) != Decimal("45"), "活动费不得打折"


# ---------- 8 会员到期未续费 ----------


@given("小明的会员已过期且有一本在借图书")
def step_expired_with_holding(context):
    # 前提必须是**正式会员（年费）**：观察期到期落的是 `pending_evaluation`（PRD §3.3），
    # 只有 formal 到期才落 `expired`（§3.4/§3.7）——本场景要验的是后者。
    _order(context, "小明", "formal_fee")
    context.holding_book = _book(context, "过期在借样例书")
    _pay_deposit(context, "小明")
    r = _borrow(context, "小明", context.holding_book)
    assert r.status_code == 200, r.text
    context.holding_borrow = r.json()
    # 造数：到期日挪到昨天（库内状态仍是 observation，等任务落 expired——这正是不变量）
    context.outside_book = _book(context, "过期不可听样例书")  # 不在手的书：过期后应听不了
    _child_db(context, "小明", member_expire=date.today() - DAY)
    assert _child_view(context, "小明")["member_status"] == "formal", "任务落库前库里仍是 formal"
    # 场景 8 的 `那么 小明的会员状态变为 "expired"` 由通用步骤（`"{status}"` 占位）承担 ——
    # 库里"任务前是 formal、任务后才 expired"这一前一后正是"状态由任务落库"的证据。


@then("家长端预约与续借被自动拦截")
def step_miniapp_blocked(context):
    kid = _kid(context, "小明")
    free_book = _book(context, "过期预约样例书")
    res = context.client.post(
        "/api/miniapp/reservations",
        json={"child_id": kid["id"], "book_id": free_book["id"]},
        headers=context.mini,
    )
    assert res.status_code == 422, f"过期后小程序预约应被拦：{res.status_code} {res.text[:160]}"
    assert "仅有效会员可预约" in res.json()["detail"], res.text
    renew = context.client.post(
        f"/api/miniapp/borrows/{context.holding_borrow['id']}/renew",
        json={"child_id": kid["id"]},
        headers=context.mini,
    )
    assert renew.status_code == 422, f"过期后续借应被拦：{renew.status_code} {renew.text[:160]}"
    assert "会员状态无效" in renew.json()["detail"], renew.text


@then('馆员端借书是"需放行"的软闸（填原因可借）')
def step_staff_soft_gate(context):
    book = _book(context, "过期可放行样例书")
    blocked = _borrow(context, "小明", book)
    assert blocked.status_code == 422, f"未放行应拦：{blocked.status_code} {blocked.text[:160]}"
    assert "可放行" in blocked.json()["detail"], blocked.text
    passed = _borrow(context, "小明", book, override_reason="家长明天续费（BDD 放行留痕）")
    assert passed.status_code == 200, f"填原因应可放行：{passed.status_code} {passed.text[:160]}"
    context.override_book = book


@then('放行后生成"续费跟进"待办（家长续费后自动审结）')
def step_renewal_followup(context):
    """PRD §3.7（2026-10-14 G11 落地）：**过期放行**借书 → 运营专员待办墙出现「续费跟进」。

    与未入会的「入会跟进」共用 scene + `ref_type=child`，显示态由 `todo_service` 按**有效会员状态**
    实时推导：家长一旦续费成有效会员即自动审结（不需要人工销项）。
    """
    kid = _kid(context, "小明")

    def _followup_rows():
        return context.client.get(
            "/api/admin/admin-notifications",
            params={"page_size": 50},
            headers=context.h,
        ).json()

    inbox = _followup_rows()
    mine = [n for n in inbox["items"] if n["ref_id"] == str(kid["id"]) and n["ref_type"] == "child"]
    assert mine, f"过期放行后应有会籍跟进待办：{inbox}"
    row = mine[0]
    assert row["title"] == "续费跟进", row
    assert row["effective_status"] == "pending", row
    assert "放行原因" in row["content"], row
    counts = context.client.get("/api/admin/todo-counts", headers=context.h).json()
    assert counts["member_follow_up"] == 1, counts

    # 闭环：家长续费 → 同一行自动审结、待办数归零（跟进不靠人工销项）
    _order(context, "小明", "formal_fee")
    after = _followup_rows()
    row2 = next(n for n in after["items"] if n["id"] == row["id"])
    assert row2["effective_status"] == "done", row2
    counts2 = context.client.get("/api/admin/todo-counts", headers=context.h).json()
    assert counts2["member_follow_up"] == 0, counts2


@then("在借图书照常归还")
def step_return_ok(context):
    r = context.client.post(
        "/api/admin/circulation/return",
        json={"copy_id": context.override_book["copy_id"], "condition": "normal"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("returned", "normal"), r.json()


@then("在借图书的音频仍可收听")
def step_holding_audio(context):
    assert _audio_allowed(context, "小明", context.holding_book) is True, (
        "在手在借的音频不得因过期断掉"
    )
    # 边界：**不在手**的书听不了（过期只听"在手"，与 audio_reading.feature 同口径）
    assert _audio_allowed(context, "小明", context.outside_book) is False


@then("已听完图书的测验仍可完成且计词数")
def step_expired_quiz(context):
    from backend.domain.growth.models import WordsLedger

    kid = _kid(context, "小明")
    book = _book(context, "过期测验样例书", questions=5)
    _seed_finished(context, "小明", book)  # 造数：已完播（完播链由 audio_reading.feature 真跑覆盖）
    view = context.client.get(
        f"/api/miniapp/quiz/{book['id']}",
        params={"child_id": kid["id"]},
        headers=context.mini,
    )
    assert view.status_code == 200 and view.json()["unlocked"] is True, view.text
    r = context.client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={"child_id": kid["id"], "answers": ["对"] * 5},
        headers=context.mini,
    )
    assert r.status_code == 200 and r.json()["passed"] is True, r.text
    with _db_session() as db:
        rows = (
            db.query(WordsLedger)
            .filter(WordsLedger.child_id == kid["id"], WordsLedger.is_deleted == 0)
            .all()
        )
    assert any(w.book_id == book["id"] for w in rows), [(w.book_id, w.word_count) for w in rows]


# ---------- 9/10 续费基期 ----------


@given("小明的会员到期日为 {days:d} 天后")
def step_expire_in_days(context, days):
    _order(context, "小明", "formal_fee")
    _child_db(
        context, "小明", member_expire=date.today() + days * DAY
    )  # 造数：把到期日挪到 30 天后
    context.expire_before = date.today() + days * DAY
    assert _child_view(context, "小明")["member_expire"] == context.expire_before.isoformat()


@when("张女士在到期前支付续费年费")
def step_renew_before_expiry(context):
    _order(context, "小明", "formal_fee")


@when("张女士在过期后支付续费年费")
def step_renew_after_expiry(context):
    _order(context, "小明", "formal_fee")


@then("新到期日为原到期日加 {days:d} 天")
def step_new_expire_from_old(context, days):
    view = _child_view(context, "小明")
    expected = (context.expire_before + days * DAY).isoformat()
    assert view["member_expire"] == expected, f"提前续费应从原到期日顺延：{view}"


@then("新到期日为付款日加 {days:d} 天")
def step_new_expire_from_today(context, days):
    view = _child_view(context, "小明")
    assert view["member_expire"] == (date.today() + days * DAY).isoformat(), view


@given("小明的会员已于 {days:d} 天前到期")
def step_expired_days_ago(context, days):
    _order(context, "小明", "formal_fee")
    _child_db(context, "小明", member_status="formal", member_expire=date.today() - days * DAY)
    assert _child_view(context, "小明")["member_status"] == "formal", (
        "库内仍是 formal（读时兜底不算）"
    )


# ---------- 11/12 退会孩子重新入会 ----------


@given('小明曾退会 状态为 "withdrawn"')
def step_withdrawn(context):
    _order(context, "小明", "observation_fee")
    _seed_history(context, "小明")
    # 造数：直接落 withdrawn（完整退会链（申请/审核/押金结算）由 withdrawal.feature 真跑覆盖，
    # 本场景只关心"重新入会"这一跳；且退会后押金是 refunded 终态、不会自动复位）
    _child_db(
        context,
        "小明",
        member_status="withdrawn",
        member_expire=date.today() - DAY,
        withdraw_reason="BDD：历史保留场景",
    )
    assert _child_view(context, "小明")["member_status"] == "withdrawn"


@when("小明重新支付观察期费 {fee:d} 元成功")
def step_rejoin_observation(context, fee):
    _order(context, "小明", "observation_fee", expect_amount=fee)


@when("小明重新支付年费成功")
def step_rejoin_formal(context):
    _order(context, "小明", "formal_fee")


@then('小明的会员状态直接变为 "formal"')
def step_rejoin_formal_directly(context):
    """「直接」= 跳过观察期（PRD §3.2 第 B 条）：退会 → 付年费 → 直接正式会员。"""
    view = _child_view(context, "小明")
    assert view["member_status"] == "formal", view


@then("历史词数、积分、等级、打卡全部保留")
def step_history_kept(context):
    kid = _kid(context, "小明")
    r = context.client.get(
        "/api/miniapp/growth/summary", params={"child_id": kid["id"]}, headers=context.mini
    )
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["words_total"] == 2500, summary
    assert summary["points_total"] == 150, summary
    assert summary["level"] == "B", summary
    cal = context.client.get(
        "/api/miniapp/checkins",
        params={"child_id": kid["id"], "days": 30},
        headers=context.mini,
    )
    assert cal.status_code == 200, cal.text
    assert (date.today() - 3 * DAY).isoformat() in cal.json()["dates"], cal.json()


# ---------- 13 无已支付订单不能转正 ----------


@given('小明处于 "none" 状态')
def step_none_status(context):
    view = _child_view(context, "小明")
    assert view["member_status"] == "none", view
    assert view["has_orders"] is False, "本场景前提：小明名下没有任何订单"


@when("馆员尝试把小明直接标记为转正（没有任何已支付订单）")
def step_probe_approve(context):
    kid = _kid(context, "小明")
    context.probe = context.client.post(
        f"/api/admin/members/children/{kid['id']}/evaluate-approve",
        json={"reason": "BDD：无订单直接转正探针"},
        headers=context.h,
    )


@then("状态变更被拒绝（只能由已支付年费订单触发）")
def step_probe_rejected(context):
    assert context.probe.status_code == 422, (
        f"无已支付订单不得转正：{context.probe.status_code} {context.probe.text[:200]}"
    )
    detail = context.probe.json()["detail"]
    assert "仅待评估孩子可评估通过转正" in detail, detail


@then("系统记录违规尝试留痕")
def step_rejection_audited(context):
    """G13（2026-10-14 第五十九批）：被拒的状态变更尝试要**留痕**（`member.transition_rejected`）。

    为什么是独立会话写审计：拒绝要抛异常 → 请求事务整体回滚，同事务写的留痕会一起丢
    （见 `audit_events.publish_rejection_audit`）。
    """
    logs = context.client.get(
        "/api/admin/audit-logs",
        params={"action": "member.transition_rejected", "page_size": 50},
        headers=context.h,
    ).json()
    assert logs["total"] >= 1, logs
    kid = _kid(context, "小明")
    mine = [row for row in logs["items"] if row["target_id"] == str(kid["id"])]
    assert mine, f"留痕应指向该孩子：{logs['items']}"
    assert "仅供" not in mine[0]["reason"], mine[0]
    assert mine[0]["reason"], mine[0]


@then('小明的会员状态保持 "none"')
def step_status_still_none(context):
    view = _child_view(context, "小明")
    assert view["member_status"] == "none", view
    assert view["has_orders"] is False, "被拒的探针不得产生任何订单"
