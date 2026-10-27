# features/steps/activity_steps.py — 线下活动域解封（FEAT-057~061）
"""角色报名/名额/签到/退款矩阵/99 元资格/取消批量（对齐 V1.1 §9、§9.2.1、§9.3、R-321）。

与 `activity_lifecycle_steps.py`（T40 全景回归）**共用口径不共用步骤文本**——behave 步骤表
全局唯一，重名会 AmbiguousStep；本文件只定义 activity.feature 需要的新步骤。

造数一律走真实 API（管理端建活动/造单/确认收款、家长端报名/退款申请）；只有"时间已过/已签到"
等正常流程走不到的态用 DB 直改，并在步骤注释里写明理由与来源。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from behave import given, then, when

REPO = Path(__file__).resolve().parents[2]

# 手机号自增（每场景 TRUNCATE，同场景多家庭也要唯一）
_PHONE_SEQ = [61000]


def _next_phone() -> str:
    _PHONE_SEQ[0] += 1
    return f"139813{_PHONE_SEQ[0]:05d}"


def _admin(ctx) -> dict:
    from tests.unit.test_wm10_concurrency import _h

    if not getattr(ctx, "h", None):
        ctx.h = _h(ctx.client)
    return ctx.h


def _mk_activity(
    ctx, *, title="读书会", quota=20, fee=50, hours=72, member_only=False, start_at=None
) -> dict:
    _admin(ctx)
    start = start_at or (datetime.now() + timedelta(hours=hours))
    payload = {
        "title": title,
        "activity_type": "book_club",
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=2)).isoformat(),
        "location": "馆内一层",
        "max_quota": quota,
        "fee": fee,
        "description": "活动域解封",
        "member_only": member_only,
    }
    r = ctx.client.post("/api/admin/activities", json=payload, headers=ctx.h)
    assert r.status_code == 200, f"建活动失败：{r.status_code} {r.text}"
    return r.json()


def _shift_activity_to_now(
    ctx, activity_id: int, *, start_minutes: int = -10, duration_minutes: int = 120
) -> None:
    """把活动挪进签到窗（docs/23 §二：开始前 60 分钟 ~ 结束后 30 分钟）。

    造数纪律允许直接改时间（与"时间已过/已签到"同类）；报名/收款等业务动作仍走真实 API。
    """
    from backend.database import get_session
    from backend.domain.activity.models import Activity

    now = datetime.now()
    with get_session() as db:
        a = db.query(Activity).filter(Activity.id == activity_id).first()
        a.start_at = now + timedelta(minutes=start_minutes)
        a.end_at = now + timedelta(minutes=duration_minutes)
        db.commit()


def _family(ctx, parent_name="张女士", kids=("小明",), *, member=False, login=True) -> dict:
    """建家长 + N 个孩子 + 小程序登录头。member=True 时把孩子办成正式会员（99 元/会员限定用）。"""
    from tests.unit.test_wm10_concurrency import _pay, _pay_deposit

    _admin(ctx)
    phone = _next_phone()
    p = ctx.client.post(
        "/api/admin/members/parents", json={"name": parent_name, "phone": phone}, headers=ctx.h
    ).json()
    children = []
    for name in kids:
        c = ctx.client.post(
            f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=ctx.h
        ).json()
        if member:
            _pay(ctx.client, ctx.h, c["id"], "observation_fee")
            _pay(ctx.client, ctx.h, c["id"], "formal_fee")
            _pay_deposit(ctx.client, ctx.h, c["id"])
        children.append(c)
    mini = None
    if login:
        mini = {
            "Authorization": (
                "Bearer "
                + ctx.client.post(
                    "/api/miniapp/login", json={"phone": phone, "code": "1234"}
                ).json()["token"]
            )
        }
    return {"parent": p, "children": children, "mini": mini, "phone": phone}


def _enroll(ctx, child_id, activity_id, *, mini=None):
    return ctx.client.post(
        f"/api/miniapp/activities/{activity_id}/enroll",
        json={"child_id": child_id},
        headers=mini or ctx.mini,
    )


def _confirm(ctx, order_id):
    r = ctx.client.post(
        f"/api/admin/orders/{order_id}/confirm-payment",
        json={"pay_method": "scan"},
        headers=_admin(ctx),
    )
    assert r.status_code == 200, f"确认收款失败：{r.status_code} {r.text}"
    return r


def _quota_used(activity_id: int) -> int:
    from backend.database import get_session
    from backend.domain.activity.service import ActivityService

    with get_session() as db:
        return ActivityService(db)._quota_used(activity_id)


def _enrollments(activity_id: int) -> list:
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment

    with get_session() as db:
        return (
            db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.activity_id == activity_id,
                ActivityEnrollment.is_deleted == 0,
            )
            .order_by(ActivityEnrollment.id)
            .all()
        )


def _enrollment_of(child_id: int, activity_id: int):
    for e in _enrollments(activity_id):
        if e.child_id == child_id:
            return e
    raise AssertionError(f"孩子 {child_id} 在活动 {activity_id} 没有报名记录")


def _enrolled_paid(ctx, activity_id: int, fam: dict, *, child_index: int = 0) -> dict:
    """报名 + 收款（付费活动正式报名态）。**按家庭传入**：token 与孩子必须同源，
    否则多家庭场景会出现"A 家 token 报 B 家孩子"→「孩子不存在」。"""
    child_id = fam["children"][child_index]["id"]
    r = _enroll(ctx, child_id, activity_id, mini=fam["mini"])
    assert r.status_code == 200, f"报名失败：{r.status_code} {r.text}"
    order_id = r.json()["order_id"]
    _confirm(ctx, order_id)
    e = _enrollment_of(child_id, activity_id)
    return {"enrollment_id": e.id, "ticket_code": e.ticket_code, "order_id": order_id}


def _enrolled_via_admin(ctx, activity_id: int, fam: dict, *, child_index: int = 0) -> dict:
    """管理端造单代客报名（R7 双链）+ 收款。用于**人多**的批量场景：
    小程序登录限流 3 次/分钟，15 个家庭会撞限流（不是产品缺陷，是防刷）。"""
    child_id = fam["children"][child_index]["id"]
    r = ctx.client.post(
        "/api/admin/orders",
        json={"order_type": "activity_fee", "child_id": child_id, "activity_id": activity_id},
        headers=_admin(ctx),
    )
    assert r.status_code == 200, f"造单失败：{r.status_code} {r.text}"
    order_id = r.json()["id"]
    _confirm(ctx, order_id)
    e = _enrollment_of(child_id, activity_id)
    return {"enrollment_id": e.id, "ticket_code": e.ticket_code, "order_id": order_id}


def _signin(ctx, ticket_code: str):
    return ctx.client.post(
        "/api/admin/activity-signin", json={"ticket_code": ticket_code}, headers=_admin(ctx)
    )


def _past(ctx, limit=30) -> list:
    r = ctx.client.get("/api/miniapp/activities/past", params={"limit": limit}, headers=ctx.mini)
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ---------- 背景：发布一场读书会 ----------


@given("馆员发布一场读书会 名额 {quota:d} 费用 {fee:d} 元 将于 {days:d} 天后开始")
def step_bg_reading_club(ctx, quota, fee, days):
    ctx.activity = _mk_activity(ctx, quota=quota, fee=fee, hours=days * 24)


# ---------- 报名 / 名额 ----------


@when("张女士为小明和小红同时报名")
def step_two_children_enroll(ctx):
    ctx.fam = _family(ctx, "张女士", ("小明", "小红"))
    ctx.mini = ctx.fam["mini"]
    ctx.responses = [_enroll(ctx, c["id"], ctx.activity["id"]) for c in ctx.fam["children"]]


@then("占用 {n:d} 个名额 生成 {n2:d} 张电子入场券")
def step_quota_and_tickets(ctx, n, n2):
    for r in ctx.responses:
        assert r.status_code == 200, f"报名应成功：{r.status_code} {r.text}"
    used = _quota_used(ctx.activity["id"])
    assert used == n, f"应占 {n} 个名额，实 {used}"
    tickets = [e.ticket_code for e in _enrollments(ctx.activity["id"])]
    assert len(tickets) == n2 and len(set(tickets)) == n2, f"应生成 {n2} 张不同入场券：{tickets}"
    # 付费活动：报名即建单待收款（占名额、待馆员确认）
    for r in ctx.responses:
        body = r.json()
        assert body["enrollment"]["status"] == "pending_payment", body
        assert body["order_id"], body


@given("名额仅剩 1 个 且两位家长同时提交报名")
def step_quota_one_two_parents(ctx):
    ctx.activity = _mk_activity(ctx, title="并发读书会", quota=1, fee=50)
    ctx.fam_a = _family(ctx, "并发家长甲", ("并发孩甲",))
    ctx.fam_b = _family(ctx, "并发家长乙", ("并发孩乙",))


@when("两笔报名并发处理")
def step_concurrent_enroll(ctx):
    """真线程并发（两个独立 TestClient）：验证 `enroll` 的活动行锁定读不超卖。"""
    from fastapi.testclient import TestClient

    from backend.main import app

    def _fire(fam):
        client = TestClient(app)
        return client.post(
            f"/api/miniapp/activities/{ctx.activity['id']}/enroll",
            json={"child_id": fam["children"][0]["id"]},
            headers=fam["mini"],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        ctx.concurrent = list(pool.map(_fire, [ctx.fam_a, ctx.fam_b]))


@then("仅一笔成功 另一笔提示名额已满")
def step_concurrent_result(ctx):
    codes = sorted(r.status_code for r in ctx.concurrent)
    assert codes == [200, 409], (
        f"应恰好一笔成功一笔拒绝：{[(r.status_code, r.text[:80]) for r in ctx.concurrent]}"
    )
    rejected = [r for r in ctx.concurrent if r.status_code == 409][0]
    assert "名额已满" in rejected.json()["detail"], rejected.text
    assert _quota_used(ctx.activity["id"]) == 1, "超卖：名额占用应恰好 1"


@given("名额已报满")
def step_quota_full(ctx):
    ctx.activity = _mk_activity(ctx, title="满员读书会", quota=1, fee=50)
    ctx.fam = _family(ctx, "满员家长", ("满员孩",))
    ctx.mini = ctx.fam["mini"]
    _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)


@when("家长尝试报名")
def step_try_enroll(ctx):
    ctx.fam2 = _family(ctx, "候补家长", ("候补孩",))
    before = len(_enrollments(ctx.activity["id"]))
    ctx.resp = _enroll(
        ctx, ctx.fam2["children"][0]["id"], ctx.activity["id"], mini=ctx.fam2["mini"]
    )
    ctx.enroll_count_before = before


@then("系统提示已满不做候补排队")
def step_full_no_waitlist(ctx):
    assert ctx.resp.status_code == 409, f"满员应拒：{ctx.resp.status_code} {ctx.resp.text}"
    assert "名额已满" in ctx.resp.json()["detail"], ctx.resp.text
    # 不做候补排队 = 满员后**不产生任何排队副作用**：无新报名行、无名额变化
    after = len(_enrollments(ctx.activity["id"]))
    assert after == ctx.enroll_count_before, (
        f"不应生成候补报名：{ctx.enroll_count_before} → {after}"
    )
    assert _quota_used(ctx.activity["id"]) == 1, "满员后名额占用不应变化"


@when("报名单创建进入待支付状态")
def step_enroll_pending(ctx):
    ctx.fam = _family(ctx, "待付家长", ("待付孩",))
    ctx.mini = ctx.fam["mini"]
    r = _enroll(ctx, ctx.fam["children"][0]["id"], ctx.activity["id"])
    assert r.status_code == 200, r.text
    ctx.order_id = r.json()["order_id"]
    ctx.enrollment_id = r.json()["enrollment"]["id"]


@then("名额被占用")
def step_quota_taken(ctx):
    assert _quota_used(ctx.activity["id"]) == 1, "待支付报名同样占名额（先锁位）"


@when("超时未支付")
def step_timeout_unpaid(ctx):
    """把订单创建时间回拨过 `pending_payment_timeout_hours`（默认 48h），再跑真任务。

    时间回拨理由：正常流程要等 48 小时，BDD 不能等——走的是产品真实链路
    （`order_timeout_cancel` 任务 + 报名联动取消），只把"时间"这一项造出来。
    """
    from backend.common.config_service import ConfigService
    from backend.database import get_session
    from backend.domain.identity.models import Order

    with get_session() as db:
        hours = int(ConfigService(db).get_value("pending_payment_timeout_hours", "48"))
        db.query(Order).filter(Order.id == ctx.order_id).update(
            {"create_time": datetime.now() - timedelta(hours=hours + 1)}
        )
        db.commit()
    r = ctx.client.post("/api/admin/tasks/order_timeout_cancel/run", headers=_admin(ctx))
    assert r.status_code == 200, f"任务触发失败：{r.status_code} {r.text}"
    assert isinstance(r.json(), dict), r.text  # 任务回执（含本次处理条数）


@then("报名单过期 名额自动释放")
def step_timeout_released(ctx):
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment
    from backend.domain.identity.models import Order

    with get_session() as db:
        order = db.query(Order).filter(Order.id == ctx.order_id).first()
        assert order.status == Order.STATUS_CANCELLED, f"订单应超时取消，实 {order.status}"
        e = db.query(ActivityEnrollment).filter(ActivityEnrollment.id == ctx.enrollment_id).first()
        assert e.status == ActivityEnrollment.STATUS_CANCELLED, f"报名应联动取消，实 {e.status}"
    assert _quota_used(ctx.activity["id"]) == 0, "名额应释放"


# ---------- 签到（扫码枪连扫口径，PRD §9.2.1） ----------


@when("馆员扫描张女士出示的入场券二维码")
def step_scan_ticket(ctx):
    ctx.fam = _family(ctx, "签到家长", ("签到孩",))
    ctx.mini = ctx.fam["mini"]
    ctx.paid = _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    _shift_activity_to_now(ctx, ctx.activity["id"])
    ctx.signin_resp = _signin(ctx, ctx.paid["ticket_code"])


@then("签到成功 记录时间与操作人")
def step_signin_ok(ctx):
    from backend.common.system_models import AuditLog
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment

    assert ctx.signin_resp.status_code == 200, ctx.signin_resp.text
    body = ctx.signin_resp.json()
    # PRD §9.2.1 表 4：回执须含孩子姓名 + 活动名（"签的是谁、哪场活动"当场可确认）
    assert body["child_name"] == "签到孩", body
    assert body["activity_title"] == ctx.activity["title"], body
    assert body["checked_in_at"], body
    with get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.id == ctx.paid["enrollment_id"])
            .first()
        )
        assert e.status == ActivityEnrollment.STATUS_CHECKED_IN
        assert e.checked_in_by is not None, "签到须记录操作人"
        assert e.checked_in_at is not None, "签到须记录时间"
        log = (
            db.query(AuditLog)
            .filter(AuditLog.action == "activity.signin", AuditLog.target_id == str(e.id))
            .first()
        )
        assert log is not None, "签到须写审计"
    assert _quota_used(ctx.activity["id"]) == 1, "签到不释放名额"


@given("{count:d} 个孩子已报名该活动并持有入场券")
def step_n_children_enrolled(ctx, count):
    ctx.fam = _family(ctx, "连扫家长", tuple(f"连扫孩{i}" for i in range(1, count + 1)))
    ctx.mini = ctx.fam["mini"]
    ctx.paid_list = [
        _enrolled_paid(ctx, ctx.activity["id"], ctx.fam, child_index=i)
        for i in range(len(ctx.fam["children"]))
    ]


@when("馆员用扫码枪连续扫描 {count:d} 张券（每张回车即提交）")
def step_scan_burst(ctx, count):
    """连扫 = 依次提交（每条一次请求，与扫码枪回车等价）；回执线索逐一收集。"""
    _shift_activity_to_now(ctx, ctx.activity["id"])
    ctx.signin_results = [_signin(ctx, p["ticket_code"]) for p in ctx.paid_list]


@then("{count:d} 张券状态依次变为 {status}")
def step_burst_all_checked_in(ctx, count, status):
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment

    expected = status.strip('"')
    assert len(ctx.signin_results) == count
    for r in ctx.signin_results:
        assert r.status_code == 200, r.text
    with get_session() as db:
        rows = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.activity_id == ctx.activity["id"])
            .order_by(ActivityEnrollment.id)
            .all()
        )
    assert [e.status for e in rows] == [expected] * count, [e.status for e in rows]


@then("每次成功提示都带孩子的姓名与活动名")
def step_burst_receipt(ctx):
    names = [r.json()["child_name"] for r in ctx.signin_results]
    assert names == [c["name"] for c in ctx.fam["children"]], names
    for r in ctx.signin_results:
        assert r.json()["activity_title"] == ctx.activity["title"], r.json()


@given("某孩子的入场券已经签到成功")
def step_one_checked_in(ctx):
    ctx.fam = _family(ctx, "重扫家长", ("重扫孩",))
    ctx.mini = ctx.fam["mini"]
    ctx.paid = _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    _shift_activity_to_now(ctx, ctx.activity["id"])
    first = _signin(ctx, ctx.paid["ticket_code"])
    assert first.status_code == 200, first.text
    ctx.first_checked_in_at = first.json()["checked_in_at"]


@when("馆员再次扫描同一张券")
def step_rescan(ctx):
    ctx.rescan = _signin(ctx, ctx.paid["ticket_code"])


@then("系统提示{msg}而不新增签到记录")
def step_rescan_rejected(ctx, msg):
    from backend.common.system_models import AuditLog
    from backend.database import get_session

    assert ctx.rescan.status_code == 409, f"重扫应拒：{ctx.rescan.status_code} {ctx.rescan.text}"
    assert "已签到" in ctx.rescan.json()["detail"], ctx.rescan.text
    with get_session() as db:
        logs = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "activity.signin",
                AuditLog.target_id == str(ctx.paid["enrollment_id"]),
            )
            .count()
        )
    assert logs == 1, f"重扫不得新增签到记录/审计：{logs}"


@then("该孩子的签到时间保持第一次的值")
def step_rescan_time_kept(ctx):
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment

    with get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.id == ctx.paid["enrollment_id"])
            .first()
        )
        delta = abs(
            (e.checked_in_at - datetime.fromisoformat(ctx.first_checked_in_at)).total_seconds()
        )
        assert delta < 1, f"签到时间被改写：{ctx.first_checked_in_at} → {e.checked_in_at}"


@given("馆员分别扫描四种无效券：不存在的券码、已签到过的券、未收款的券、已取消活动的券")
def step_four_invalid_tickets(ctx):
    """PRD §9.2.1 表 5 的失败四类（**不是**"越场券"——口径订正见 docs/09 第五十六轮）。"""
    _admin(ctx)
    # ① 不存在的券码
    ctx.invalid_cases = [("不存在", "ZZZ-NOT-EXIST-0001")]
    # ② 已签到过的券
    ctx.activity = _mk_activity(ctx, title="四类失败读书会", quota=5, fee=50)
    ctx.fam = _family(ctx, "四类家长", ("四类孩甲", "四类孩乙"))
    ctx.mini = ctx.fam["mini"]
    paid_a = _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    ctx.invalid_cases.append(("已签到", paid_a["ticket_code"]))
    # ③ 未完成收款（待收款报名，券码已存在但不可签到）
    r = _enroll(ctx, ctx.fam["children"][1]["id"], ctx.activity["id"])
    assert r.status_code == 200, r.text
    ctx.invalid_cases.append(("未收款", r.json()["enrollment"]["ticket_code"]))
    # 报名动作完成后才把活动挪进签到窗（否则"已开始"会挡住第 ③ 步报名）
    _shift_activity_to_now(ctx, ctx.activity["id"])
    assert _signin(ctx, paid_a["ticket_code"]).status_code == 200
    # ④ 活动已取消/结束的券：**走 DB 直改**构造——正常取消流程会把已付费报名一并翻成
    # refund_pending（先撞"报名状态不可签到"分支），自动结束任务又要求"无进行中报名"，
    # 所以"活动终态 + 报名仍 ENROLLED"这个组合在产品路径上不可达（本步骤专测签到侧的
    # 活动终态守卫，不测取消流程——那由"门店取消活动逐单审核退款"场景覆盖）。
    a2 = _mk_activity(ctx, title="将被取消的读书会", quota=5, fee=50)
    fam2 = _family(ctx, "被取消家长", ("被取消孩",))
    paid_b = _enrolled_paid(ctx, a2["id"], fam2)
    from backend.database import get_session
    from backend.domain.activity.models import Activity

    with get_session() as db:
        db.query(Activity).filter(Activity.id == a2["id"]).update(
            {"status": Activity.STATUS_CANCELLED}
        )
        db.commit()
    ctx.invalid_cases.append(("活动已取消", paid_b["ticket_code"]))


@when("提交扫描结果")
def step_submit_invalid_scan(ctx):
    ctx.invalid_results = {label: _signin(ctx, code) for label, code in ctx.invalid_cases}


@then("四类失败各有明确原因（券码不存在 / 已签到过 / 未完成收款或已退款 / 活动已取消）")
def step_four_reasons(ctx):
    r = ctx.invalid_results
    assert r["不存在"].status_code == 404, r["不存在"].text
    assert "入场券不存在" in r["不存在"].json()["detail"], r["不存在"].text
    assert r["已签到"].status_code == 409 and "已签到" in r["已签到"].json()["detail"]
    assert r["未收款"].status_code == 422, r["未收款"].text
    assert "不可签到" in r["未收款"].json()["detail"], r["未收款"].text
    assert r["活动已取消"].status_code == 422, r["活动已取消"].text
    assert "已取消" in r["活动已取消"].json()["detail"], r["活动已取消"].text


@then("不产生任何签到记录")
def step_no_signin_rows(ctx):
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment

    with get_session() as db:
        rows = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.status == ActivityEnrollment.STATUS_CHECKED_IN)
            .count()
        )
    # 本场景只有"已签到过"那一张券留有一条签到（首扫成功）；其余三类零副作用
    assert rows == 1, f"失败扫描产生了签到记录：{rows}"


# ---------- 退款矩阵（V1.1 §9.3） ----------


@given("小明已签到该活动")
def step_xiaoming_checked_in(ctx):
    ctx.fam = _family(ctx, "已签家长", ("小明",))
    ctx.mini = ctx.fam["mini"]
    ctx.paid = _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    _shift_activity_to_now(ctx, ctx.activity["id"])
    assert _signin(ctx, ctx.paid["ticket_code"]).status_code == 200


@when("张女士申请活动退款")
def step_apply_refund(ctx):
    ctx.refund_resp = ctx.client.post(
        f"/api/miniapp/enrollments/{ctx.paid['enrollment_id']}/refund-apply",
        json={"child_id": ctx.fam["children"][0]["id"]},
        headers=ctx.mini,
    )


@then("系统拒绝退款")
def step_refund_rejected(ctx):
    assert ctx.refund_resp.status_code == 422, ctx.refund_resp.text
    assert "已签到" in ctx.refund_resp.json()["detail"], ctx.refund_resp.text


@when("张女士在活动开始前 {hours:d} 小时申请退款")
def step_apply_refund_before(ctx, hours):
    ctx.activity = _mk_activity(
        ctx,
        title="三小时后退款会",
        quota=5,
        fee=50,
        start_at=datetime.now() + timedelta(hours=hours),
    )
    ctx.fam = _family(ctx, "退款家长", ("退款孩",))
    ctx.mini = ctx.fam["mini"]
    ctx.paid = _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    ctx.refund_resp = ctx.client.post(
        f"/api/miniapp/enrollments/{ctx.paid['enrollment_id']}/refund-apply",
        json={"child_id": ctx.fam["children"][0]["id"]},
        headers=ctx.mini,
    )


@then("退款申请创建 全额 {fee:d} 元")
def step_refund_created_full(ctx, fee):
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment
    from backend.domain.identity.models import RefundRequest

    assert ctx.refund_resp.status_code == 200, ctx.refund_resp.text
    assert ctx.refund_resp.json()["status"] == "refund_pending", ctx.refund_resp.text
    with get_session() as db:
        req = (
            db.query(RefundRequest)
            .filter(RefundRequest.order_id == ctx.paid["order_id"], RefundRequest.is_deleted == 0)
            .first()
        )
        assert req is not None, "应创建统一退款台账（RefundRequest）"
        assert str(req.amount) == f"{fee}.00", f"活动费未开始应全额退 {fee}：实 {req.amount}"
        e = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.id == ctx.paid["enrollment_id"])
            .first()
        )
        assert e.status == ActivityEnrollment.STATUS_REFUND_PENDING
    assert _quota_used(ctx.activity["id"]) == 1, "退款待审期间名额仍占用（审核通过才释放）"


@when("张女士在活动开始前 {mins:d} 分钟尝试线上退款")
def step_apply_refund_inside_cutoff(ctx, mins):
    ctx.activity = _mk_activity(
        ctx,
        title="临近开始退款会",
        quota=5,
        fee=50,
        start_at=datetime.now() + timedelta(minutes=mins),
    )
    ctx.fam = _family(ctx, "临期家长", ("临期孩",))
    ctx.mini = ctx.fam["mini"]
    ctx.paid = _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    ctx.refund_resp = ctx.client.post(
        f"/api/miniapp/enrollments/{ctx.paid['enrollment_id']}/refund-apply",
        json={"child_id": ctx.fam["children"][0]["id"]},
        headers=ctx.mini,
    )


@then("系统提示请联系馆员线下处理")
def step_cutoff_rejected(ctx):
    assert ctx.refund_resp.status_code == 422, ctx.refund_resp.text
    assert "线上退款已关闭" in ctx.refund_resp.json()["detail"], ctx.refund_resp.text


# ---------- 门店取消活动：逐单审核退款 ----------


@given("门店取消该活动 且已有 {count:d} 个已支付未签到报名")
def step_cancel_with_n_enrolled(ctx, count):
    ctx.activity = _mk_activity(ctx, title="批量退款会", quota=count + 5, fee=50)
    ctx.paid_list = []
    for i in range(count):
        fam = _family(ctx, f"批量家长{i}", (f"批量孩{i}",), login=False)
        ctx.paid_list.append(_enrolled_via_admin(ctx, ctx.activity["id"], fam))
    ctx.cancel_resp = ctx.client.post(
        f"/api/admin/activities/{ctx.activity['id']}/cancel", headers=_admin(ctx)
    )
    assert ctx.cancel_resp.status_code == 200, ctx.cancel_resp.text


@then("{count:d} 笔订单进入 {status}")
def step_orders_refund_pending(ctx, count, status):
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment

    expected = status.strip('"')
    with get_session() as db:
        rows = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.activity_id == ctx.activity["id"])
            .all()
        )
    assert len(rows) == count and all(e.status == expected for e in rows), [e.status for e in rows]


@then("系统为每笔创建退款申请由超管逐单审核")
def step_each_refund_request(ctx):
    from backend.database import get_session
    from backend.domain.identity.models import RefundRequest

    with get_session() as db:
        reqs = (
            db.query(RefundRequest)
            .filter(
                RefundRequest.order_id.in_([p["order_id"] for p in ctx.paid_list]),
                RefundRequest.status == RefundRequest.STATUS_PENDING,
                RefundRequest.is_deleted == 0,
            )
            .all()
        )
    assert len(reqs) == len(ctx.paid_list), f"每笔应各一条退款申请：{len(reqs)}"
    # 超管逐单审核：审核端点仅超管可调（staff 只读不处理）
    from tests.unit.test_wm10_concurrency import _h

    staff = _h(ctx.client, "staff01")
    one = reqs[0]
    r = ctx.client.post(
        f"/api/admin/refund-requests/{one.id}/review",
        json={"approve": True, "remark": "越权尝试"},
        headers=staff,
    )
    assert r.status_code in (403, 401), f"staff 不应能审核退款：{r.status_code} {r.text[:120]}"


@then("拒绝的订单恢复 {status}")
def step_reject_restores(ctx, status):
    """拒审走**活动域**审核端点（`activity-refunds/{eid}/review`）：只有它会联动把报名
    从 refund_pending 翻回 enrolled；通用 `refund-requests/{id}/review` 只管台账不碰报名。"""
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment
    from backend.domain.identity.models import RefundRequest

    expected = status.strip('"')
    target = ctx.paid_list[-1]
    with get_session() as db:
        req = (
            db.query(RefundRequest)
            .filter(RefundRequest.order_id == target["order_id"], RefundRequest.is_deleted == 0)
            .first()
        )
        assert req is not None
        req_id = req.id
    r = ctx.client.post(
        f"/api/admin/activity-refunds/{target['enrollment_id']}/review",
        json={"approve": False, "remark": "家长到店参加，撤回退款"},
        headers=_admin(ctx),
    )
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    with get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.id == target["enrollment_id"])
            .first()
        )
        assert e.status == expected, f"拒审后应恢复 {expected}：实 {e.status}"
        rr = db.query(RefundRequest).filter(RefundRequest.id == req_id).first()
        assert rr.status == RefundRequest.STATUS_REJECTED, f"台账应转 rejected：实 {rr.status}"


# ---------- 99 元首场活动费资格（R-321） ----------


def _buy_first_activity(ctx, child_id, *, mini=None):
    """99 元首场活动费下单：家长端在线下单（与管理端同一条定价链 `price_member_order`）。

    走家长端而非管理端造单：R-321 的"每账号一次"判定在两端口径同源，家长端才是真实路径。
    """
    return ctx.client.post(
        "/api/miniapp/orders",
        json={"child_id": child_id, "order_type": "first_activity_fee"},
        headers=mini or ctx.mini,
    )


def _paid_first_activity(ctx) -> dict:
    ctx.fam = _family(ctx, "九九家长", ("九九孩甲", "九九孩乙"))
    ctx.mini = ctx.fam["mini"]
    r = _buy_first_activity(ctx, ctx.fam["children"][0]["id"])
    assert r.status_code == 200, f"99 元下单失败：{r.status_code} {r.text}"
    ctx.order_id = r.json()["id"]
    _confirm(ctx, ctx.order_id)
    return r.json()


@given("张女士已支付过 99 元首场活动费且未全额退款")
def step_first_activity_paid(ctx):
    _paid_first_activity(ctx)


@when("尝试再次购买 99 元活动")
def step_buy_again(ctx):
    # 换个孩子再买：资格是**按账号**（parent）判定，不是按孩子——用二孩才能真正测到这条规则
    ctx.again = _buy_first_activity(ctx, ctx.fam["children"][1]["id"])


@then("系统拒绝")
def step_buy_rejected(ctx):
    assert ctx.again.status_code == 409, f"应拒：{ctx.again.status_code} {ctx.again.text}"
    assert "每账号仅一次" in ctx.again.json()["detail"], ctx.again.text


@given("张女士的 99 元订单退款申请处理中")
def step_first_activity_refunding(ctx):
    _paid_first_activity(ctx)
    r = ctx.client.post(
        "/api/miniapp/refund-requests",
        json={
            "child_id": ctx.fam["children"][0]["id"],
            "order_id": ctx.order_id,
            "reason": "活动时间冲突",
        },
        headers=ctx.mini,
    )
    assert r.status_code == 200, f"退款申请失败：{r.status_code} {r.text}"
    ctx.refund_id = r.json()["id"]
    from backend.database import get_session
    from backend.domain.identity.models import Order

    with get_session() as db:
        o = db.query(Order).filter(Order.id == ctx.order_id).first()
        assert o.refund_status == Order.REFUND_STATUS_PENDING, (
            f"退款申请处理中（refund_status=pending），实 {o.refund_status}"
        )


@then("无法再次购买 99 元活动")
def step_refunding_cannot_rebuy(ctx):
    r = _buy_first_activity(ctx, ctx.fam["children"][1]["id"])
    assert r.status_code == 409, f"退款处理中资格不恢复：{r.status_code} {r.text}"
    assert "每账号仅一次" in r.json()["detail"], r.text


@given("99 元订单已全额退款 且张女士未实际参加活动")
def step_first_activity_refunded(ctx):
    """走完整退款链（申请 → 超管审 → 执行）把 refund_status 打到 refunded。

    "未实际参加"这一条**不是代码判据**（审批规则文案写明"已参加过不退，审核时核对"——
    由审核员人工核对），资格恢复的机器判据只有 `refund_status == refunded`
    （`order_guards.assert_first_activity_eligible`）。场景按实现口径断言，差异在 docs/09 记明。
    """
    _paid_first_activity(ctx)
    r = ctx.client.post(
        "/api/miniapp/refund-requests",
        json={
            "child_id": ctx.fam["children"][0]["id"],
            "order_id": ctx.order_id,
            "reason": "活动取消未参加",
        },
        headers=ctx.mini,
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    rv = ctx.client.post(
        f"/api/admin/refund-requests/{rid}/review",
        json={"approve": True, "remark": "核对未参加，同意全额退"},
        headers=_admin(ctx),
    )
    assert rv.status_code == 200, rv.text
    ex = ctx.client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "线下原路打款"},
        headers=_admin(ctx),
    )
    assert ex.status_code == 200, ex.text
    from backend.database import get_session
    from backend.domain.identity.models import Order

    with get_session() as db:
        o = db.query(Order).filter(Order.id == ctx.order_id).first()
        assert o.refund_status == Order.REFUND_STATUS_REFUNDED, (
            f"应全额退款到账，实 {o.refund_status}"
        )


@then("购买资格恢复")
def step_first_activity_eligible_again(ctx):
    r = _buy_first_activity(ctx, ctx.fam["children"][1]["id"])
    assert r.status_code == 200, f"全额退款后应恢复购买资格：{r.status_code} {r.text}"
    assert float(r.json()["amount"]) == 99.0, r.json()
