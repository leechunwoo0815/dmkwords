# features/steps/activity_lifecycle_steps.py — T40 活动域全景回归步骤定义
"""报名三路径×状态机全组合（五轮插修教训沉淀）。造数复用 unit 基建
（_family/_h 同款 API 链）；before_scenario 每场景清库+seed（phone 可固定）。"""

from datetime import datetime, timedelta
from pathlib import Path

from behave import given, then, when

REPO = Path(__file__).resolve().parents[2]

# 手机号自增（11 位=139810+5 位序号；每场景 TRUNCATE 后不冲突，同场景多家庭也唯一——
# 场景 5 双家庭同号 409/场景 6 10 位 422 两案教训）
_PHONE_SEQ = [40000]


def _next_phone() -> str:
    _PHONE_SEQ[0] += 1
    return f"139810{_PHONE_SEQ[0]:05d}"


def _mk_activity(ctx, title="全景活动", quota=5, fee=60):
    r = ctx.client.post(
        "/api/admin/activities",
        json={
            "title": title,
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内一层",
            "max_quota": quota,
            "fee": fee,
            "description": "T40 全景回归",
            "member_only": False,
        },
        headers=ctx.h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _mk_order(ctx, child_id, activity_id):
    r = ctx.client.post(
        "/api/admin/orders",
        json={"order_type": "activity_fee", "child_id": child_id, "activity_id": activity_id},
        headers=ctx.h,
    )
    return r


def _confirm(ctx, order_id):
    r = ctx.client.post(
        f"/api/admin/orders/{order_id}/confirm-payment", json={"pay_method": "scan"}, headers=ctx.h
    )
    assert r.status_code == 200, r.text


def _counts(ctx):
    return ctx.client.get("/api/admin/todo-counts", headers=ctx.h).json()


def _enroll(ctx, child_id, activity_id):
    return ctx.client.post(
        f"/api/miniapp/activities/{activity_id}/enroll",
        json={"child_id": child_id},
        headers=ctx.mini,
    )


# ---------- 公共造数 ----------


@given("管理端发布付费活动 名额 {quota:d} 费用 {fee:d} 元 {hours:d} 天后开始")
def step_given_activity(ctx, quota, fee, hours):
    from tests.unit.test_wm10_concurrency import _h

    ctx.h = _h(ctx.client)
    ctx.activity = _mk_activity(ctx, quota=quota, fee=fee)


@given('已建档家长"{parent}"带孩子"{child}"')
def step_given_family(ctx, parent, child):
    from tests.unit.test_wm10_concurrency import _family, _h

    # 兜底：无活动 given 的场景（路径 3/4）自备默认活动（场景 1/2 已有则跳过）
    if not hasattr(ctx, "activity"):
        ctx.h = _h(ctx.client)
        ctx.activity = _mk_activity(ctx)
    p, c, mini = _family(ctx.client, ctx.h, _next_phone(), name=child)
    ctx.parent, ctx.child, ctx.mini = p, c, mini


@given('已建档家长"{parent}"带孩子"{child}"完成了付费活动报名与收款')
def step_given_family_enrolled(ctx, parent, child):
    step_given_family(ctx, parent, child)
    r = _enroll(ctx, ctx.child["id"], ctx.activity["id"])
    assert r.status_code == 200, r.text
    ctx.enrollment = r.json()
    _confirm(ctx, ctx.enrollment["order_id"])


@given('已建档家长"{parent}"带孩子"{child}"已在某付费活动有有效报名')
def step_given_family_active(ctx, parent, child):
    step_given_family(ctx, parent, child)
    r = _enroll(ctx, ctx.child["id"], ctx.activity["id"])
    assert r.status_code == 200, r.text
    ctx.enrollment = r.json()


@given('已建档家长"{parent}"带孩子"{child}"已报名占满名额')
def step_given_family_full(ctx, parent, child):
    step_given_family(ctx, parent, child)
    r = _enroll(ctx, ctx.child["id"], ctx.activity["id"])
    assert r.status_code == 200, r.text
    ctx.enrollment = r.json()


@given("付费活动已有两名完成收款的已报名孩子")
def step_given_two_enrolled(ctx):
    from tests.unit.test_wm10_concurrency import _family, _h

    if not hasattr(ctx, "activity"):
        ctx.h = _h(ctx.client)
        ctx.activity = _mk_activity(ctx)
    ctx.families = []
    for i, name in enumerate(("甲孩", "乙孩")):
        p, c, mini = _family(ctx.client, ctx.h, _next_phone(), name=name)
        ctx.mini = mini  # _enroll 读 ctx.mini
        r = _enroll(ctx, c["id"], ctx.activity["id"])
        assert r.status_code == 200, r.text
        _confirm(ctx, r.json()["order_id"])
        ctx.families.append({"child": c, "mini": mini, "enrollment": r.json()})


@given("存在退款驱动的联动退会单和直接发起的退会单")
def step_given_withdrawals(ctx):
    from tests.unit.test_wm10_concurrency import _h

    if not hasattr(ctx, "h"):
        ctx.h = _h(ctx.client)
    from backend.database import get_session
    from backend.domain.identity.models import WithdrawalRequest
    from tests.unit.test_wm13_admin_inbox import _send

    with get_session() as db:
        w_link = WithdrawalRequest(
            child_id=1, source=WithdrawalRequest.SOURCE_REFUND, reason="联动", status="applying"
        )
        w_direct = WithdrawalRequest(
            child_id=2, source=WithdrawalRequest.SOURCE_NORMAL, reason="直接", status="applying"
        )
        db.add_all([w_link, w_direct])
        db.flush()
        ctx.link_id, ctx.direct_id = w_link.id, w_direct.id
        _send(db, "admin.withdrawal_apply", "withdrawal_request", ctx.link_id)
        _send(db, "admin.withdrawal_apply", "withdrawal_request", ctx.direct_id)
        db.commit()


# ---------- 动作 ----------


@when("家长在小程序报名该活动")
@when("家长再次报名")
@when("满员孩的家长报名该活动")
def step_when_enroll(ctx):
    ctx.resp = _enroll(ctx, ctx.child["id"], ctx.activity["id"])
    if ctx.resp.status_code == 200:
        ctx.enrollment = ctx.resp.json()


@when("馆员确认收款")
def step_when_confirm(ctx):
    oid = getattr(ctx, "order_id", None) or ctx.enrollment["order_id"]
    _confirm(ctx, oid)


@when("馆员为孩子创建活动费用订单")
@when("馆员再为孩子创建同活动订单")
@when("馆员为满员孩创建活动费用订单")
def step_when_admin_order(ctx):
    ctx.resp = _mk_order(ctx, ctx.child["id"], ctx.activity["id"])
    if ctx.resp.status_code == 200:
        ctx.order_id = ctx.resp.json()["id"]


@when("家长申请退款并走完审核执行链")
def step_when_refund_flow(ctx):
    eid = ctx.enrollment["enrollment"]["id"]
    r = ctx.client.post(
        f"/api/miniapp/enrollments/{eid}/refund-apply",
        json={"child_id": ctx.child["id"]},
        headers=ctx.mini,
    )
    assert r.status_code == 200, r.text
    ra = ctx.client.post(
        f"/api/admin/activity-refunds/{eid}/review",
        json={"approve": True, "remark": "同意"},
        headers=ctx.h,
    )
    assert ra.status_code == 200, ra.text
    from backend.database import get_session
    from backend.domain.identity.models import RefundRequest

    with get_session() as db:
        rr = (
            db.query(RefundRequest)
            .filter(
                RefundRequest.order_id == ctx.enrollment["order_id"],
                RefundRequest.is_deleted == 0,
            )
            .order_by(RefundRequest.id.desc())
            .first()
        )
        rid = rr.id
    re_ = ctx.client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "线下打款"},
        headers=ctx.h,
    )
    assert re_.status_code == 200, re_.text


@when("馆员取消该活动")
def step_when_cancel_activity(ctx):
    r = ctx.client.post(f"/api/admin/activities/{ctx.activity['id']}/cancel", headers=ctx.h)
    assert r.status_code == 200, r.text


@when("扫描小程序活动域页面全部 status 输出点")
def step_when_scan_status_outputs(ctx):
    """Q1 批复两层断言之一：全量输出点扫描（活动域 wxml 的 {{…status}} 输出点）。"""
    import re

    pkg = REPO / "miniapp" / "pages" / "activity-pkg"
    ctx.wxml_files = sorted(pkg.rglob("*.wxml"))
    assert ctx.wxml_files, "活动域 wxml 不存在（目录漂移？）"
    # 裸输出点：{{…my_enrollment.status}} / {{…item.status}} 等（含 status 字样
    # 且非 statusText 且非比较表达式）——有比较（===）或映射变量（statusText）不算裸
    bare = []
    for f in ctx.wxml_files:
        for m in re.finditer(r"\{\{[^}]*\.status[^}]*\}\}", f.read_text()):
            expr = m.group(0)
            if "===" in expr or "statusText" in expr or "'===" in expr:
                continue
            bare.append((f.name, expr))
    ctx.bare_status_outputs = bare


# ---------- 断言 ----------


@then("报名为待收款 生成入场券 且 名额占用 1")
def step_then_pending(ctx):
    r = ctx.resp
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enrollment"]["status"] == "pending_payment", body
    assert body["enrollment"].get("ticket_code"), body
    from backend.database import get_session
    from backend.domain.activity.models import Activity
    from backend.domain.activity.service import ActivityService

    with get_session() as db:
        a = db.query(Activity).filter(Activity.id == ctx.activity["id"]).first()
        assert ActivityService(db)._quota_used(a.id) == 1, "名额应占用 1"


@then("报名同步生成且为待收款 生成入场券 且 名额占用 1")
def step_then_admin_order_enrollment(ctx):
    r = ctx.resp
    assert r.status_code == 200, r.text
    order_id = r.json()["id"]
    from backend.database import get_session
    from backend.domain.activity.models import Activity, ActivityEnrollment
    from backend.domain.activity.service import ActivityService

    with get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.order_id == order_id, ActivityEnrollment.is_deleted == 0)
            .first()
        )
        assert e is not None, "造单应同步生成报名（T2）"
        assert e.status == ActivityEnrollment.STATUS_PENDING_PAYMENT and e.ticket_code
        a = db.query(Activity).filter(Activity.id == ctx.activity["id"]).first()
        assert ActivityService(db)._quota_used(a.id) == 1, "名额应占用 1"


@then("管理待办出现活动报名待确认通知")
def step_then_todo_exists(ctx):
    assert _counts(ctx)["activity_enroll_pending"] >= 1, "待确认计数应 +1（R7 双链）"


@then("报名转为已报名 且 待确认计数归零")
def step_then_enrolled_and_zero(ctx):
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment

    with get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.order_id == ctx.order_id, ActivityEnrollment.is_deleted == 0)
            .first()
        )
        assert e.status == ActivityEnrollment.STATUS_ENROLLED, f"实 {e.status}"
    assert _counts(ctx)["activity_enroll_pending"] == 0, "确认后待确认应归零"


@then("报名转为已报名 且 订单状态为已支付")
def step_then_enrolled_paid(ctx):
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment
    from backend.domain.identity.models import Order

    with get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.order_id == ctx.enrollment["order_id"],
                ActivityEnrollment.is_deleted == 0,
            )
            .first()
        )
        assert e.status == ActivityEnrollment.STATUS_ENROLLED, f"实 {e.status}"
        o = db.query(Order).filter(Order.id == ctx.enrollment["order_id"]).first()
        assert o.status == Order.STATUS_PAID, f"订单实 {o.status}"


@then("报名状态为已退款 且 名额释放 且 订单状态为已退款")
def step_then_refunded(ctx):
    from backend.database import get_session
    from backend.domain.activity.models import Activity, ActivityEnrollment
    from backend.domain.activity.service import ActivityService
    from backend.domain.identity.models import Order

    with get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.order_id == ctx.enrollment["order_id"],
                ActivityEnrollment.is_deleted == 0,
            )
            .first()
        )
        assert e.status == ActivityEnrollment.STATUS_REFUNDED, f"实 {e.status}"
        o = db.query(Order).filter(Order.id == ctx.enrollment["order_id"]).first()
        assert o.status == Order.STATUS_REFUNDED, f"订单实 {o.status}"
        a = db.query(Activity).filter(Activity.id == ctx.activity["id"]).first()
        assert ActivityService(db)._quota_used(a.id) == 0, "名额应释放"


@then("报名成功")
def step_then_reenroll_ok(ctx):
    assert ctx.resp.status_code == 200, ctx.resp.text


@then("系统拒绝并提示不可重复创建")
def step_then_dup_rejected(ctx):
    assert ctx.resp.status_code == 422, f"应 422 拦截（R8），实 {ctx.resp.status_code}"
    assert "重复" in ctx.resp.json()["detail"], ctx.resp.text


@then("系统提示名额已满")
def step_then_full_rejected(ctx):
    assert ctx.resp.status_code in (409, 422), (
        f"满员应拦截，实 {ctx.resp.status_code} {ctx.resp.text[:120]}"
    )


@then("系统拒绝且提示名额已满")
def step_then_admin_full(ctx):
    assert ctx.resp.status_code == 422, f"满员造单应 422，实 {ctx.resp.status_code}"
    assert "名额已满" in ctx.resp.json()["detail"], ctx.resp.text


@then("两名孩子报名均转为退款待审")
def step_then_cancel_batch(ctx):
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment

    with get_session() as db:
        for fam in ctx.families:
            e = (
                db.query(ActivityEnrollment)
                .filter(
                    ActivityEnrollment.order_id == fam["enrollment"]["order_id"],
                    ActivityEnrollment.is_deleted == 0,
                )
                .first()
            )
            assert e.status == ActivityEnrollment.STATUS_REFUND_PENDING, (
                f"应退款待审，实 {e.status}"
            )


@then("联动单显示联动处理中 且 待处理计数只含直接退会单")
def step_then_linkage_grey(ctx):
    r = ctx.client.get("/api/admin/admin-notifications", headers=ctx.h)
    items = {(i["ref_type"], i["ref_id"]): i for i in r.json()["items"]}
    link = items[("withdrawal_request", str(ctx.link_id))]
    direct = items[("withdrawal_request", str(ctx.direct_id))]
    assert link["status_text"] == "联动处理中·随退款自动推进", link["status_text"]
    assert link["effective_status"] == "done"
    assert direct["effective_status"] == "pending"


@then("每个输出点都有中文映射")
def step_then_no_bare(ctx):
    assert not ctx.bare_status_outputs, f"裸 status 输出点（T4 回归）：{ctx.bare_status_outputs}"


@then("映射覆盖后端六态全集")
def step_then_map_complete(ctx):
    """Q1 批复两层断言之二：映射完备性对拍——映射集 ⊇ 六态全集（models 常量）。"""
    import re

    js_files = (REPO / "miniapp" / "pages" / "activity-pkg").rglob("*.js")
    mapped: set[str] = set()
    for f in js_files:
        text = f.read_text()
        for m in re.finditer(
            r"(enrolled|checked_in|pending_payment|refund_pending|refunded|cancelled)\s*[:=]", text
        ):
            mapped.add(m.group(1))
    from backend.domain.activity.models import ActivityEnrollment

    required = set(ActivityEnrollment.ACTIVE_STATUSES) | {
        ActivityEnrollment.STATUS_REFUNDED,
        ActivityEnrollment.STATUS_CANCELLED,
    }
    missing = required - mapped
    assert not missing, f"前端映射缺状态（未来加态忘映射会裸输出）：{missing}"
