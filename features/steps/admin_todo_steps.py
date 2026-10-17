# features/steps/admin_todo_steps.py — FEAT-071/072/073 运营审核工作台 步骤定义
"""2026-10-18（第七十一批·BDD 解封第十四批）· **admin_todo 域 7 场景**转真实执行（撤文件级 @draft）。

判据与实现同源（动手前逐条亲验，红线 108/136）：
- **接警**（FEAT-071）：三条申请链（退款 `wm10_service` / 退会 `wm10_withdrawal_service` / 转让
  `transfer_service`）都在**同一事务**里调 `AdminNotifyService.send`（`INSERT IGNORE` 幂等，
  键＝`(scene, ref_type, ref_id, dedup_key, is_deleted)` 唯一索引）→ 申请与待办同生同死。
- **显示态实时算**（FEAT-072）：`AdminTodoService.resolve_many` 按**业务单据当前状态**推导
  pending/done/invalid（退款 `cancelled` → 「已失效·家长已撤销」；转让 `expired` → 「已失效·已超时自动失效」）；
  列表与 `/todo-counts` **共用同一 resolver**（口径不分叉）。`handled_at` 人工标记**只覆盖 pending**，
  不会把"已失效"改写掉（T20c）。
- **超时两条腿**（FEAT-072）：`transfer_expire_check`（600s，超时 → expired + 解锁 + 家长站内信）
  与 `transfer_expiring_warn`（3600s，剩 ≤24h 预警，dedup 每单一次）。
- **权限**（FEAT-073）：`/todo-counts` 对非超管**不返回审核类计数**（退款/退会/转让/批量退款/超时提醒
  等一律 0，`admin_total` 只由 staff 类求和）；`/admin-notifications` 对非超管**返回全量记录 + read_only**
  （T20g #8 产品决策），而手动标记端点仍 `require_super_admin`。
- **闭环**（FEAT-071）：审核终态 `mark_handled(ref_type=…, admin=admin)` 写 handled_at/handled_by
  （收件箱回显 `handled_by_name`）；跳转定义在前端 `ADMIN_REF_ROUTE`（`ref_type + ref_id` →
  `/refund-center?tab=…&highlight={ref_id}`），故本域断"后端交付的 ref_type/ref_id 能定位到该单据"。

时间旅行（"超过 72 小时未审"）用 `TransferRequest.expires_at` 拨到过去/未来实现——**判定与文案同源**：
超时时限读配置 `transfer_review_timeout_hours`，步骤先断言配置＝场景里的 72 再拨表，不写死小时数。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from behave import given, then, when

from backend.common.admin_notification_models import AdminNotification
from tests.unit.test_wm10_concurrency import _db, _h

PHONE = "13800000002"  # 本域专用家长（手机号全局唯一）
REFUND_REASON = "书没读上，想退押金"
WITHDRAW_REASON = "搬家去外地，孩子不再来了"
REVIEW_REMARK = "不符合退款条件（已超可退期限）"
MANUAL_REASON = "线下已与家长核对并跟进"

#: 仅超管可见的审核类计数键（`todo_counts` 在非超管分支里根本不求和）
AUDIT_KEYS = (
    "refund_pending",
    "withdrawal_pending",
    "transfer_pending",
    "transfer_expiring",
    "activity_batch_refund",
    "activity_orders_voided",
    "copy_damage_pending",
    "payment_anomaly",
    "reservation_exception",
    "refund_review_timeout",
    "withdrawal_review_timeout",
)
#: staff 分支自己求和的键（`admin_total` 只由这些构成）
STAFF_KEYS = (
    "activity_enroll_pending",
    "member_follow_up",
    "pending_evaluation_followup",
    "deposit_settle_followup",
)


# ---------- 基建 ----------


def _admin(context) -> dict:
    if getattr(context, "h", None) is None:
        context.h = _h(context.client)
    return context.h


def _staff(context) -> dict:
    if getattr(context, "h_staff", None) is None:
        r = context.client.post(
            "/api/admin/login", json={"username": "staff01", "password": "dmkwords123"}
        )
        assert r.status_code == 200, r.text
        context.h_staff = {"Authorization": f"Bearer {r.json()['token']}"}
    return context.h_staff


def _parent(context) -> dict:
    if getattr(context, "parent_row", None) is None:
        _admin(context)
        r = context.client.post(
            "/api/admin/members/parents",
            json={"name": "待办家长", "phone": PHONE},
            headers=context.h,
        )
        assert r.status_code == 200, r.text
        context.parent_row = r.json()
        context.children = {}
        login = context.client.post("/api/miniapp/login", json={"phone": PHONE, "code": "1234"})
        assert login.status_code == 200, login.text
        context.mini = {"Authorization": f"Bearer {login.json()['token']}"}
    return context.parent_row


def _kid(context, name: str) -> dict:
    _parent(context)
    if name not in context.children:
        r = context.client.post(
            f"/api/admin/members/parents/{context.parent_row['id']}/children",
            json={"name": name},
            headers=_admin(context),
        )
        assert r.status_code == 200, r.text
        context.children[name] = r.json()
    return context.children[name]


def _pay_order(context, child_id: int, order_type: str) -> dict:
    """建单 + 人工确认收款（押金走押金专用端点，金额取配置标准额）。"""
    if order_type == "deposit":
        r = context.client.post(
            f"/api/admin/deposits/children/{child_id}/orders", headers=_admin(context)
        )
        assert r.status_code == 200, r.text
        order_id = r.json()["order_id"]
    else:
        r = context.client.post(
            "/api/admin/orders",
            json={"child_id": child_id, "order_type": order_type},
            headers=_admin(context),
        )
        assert r.status_code == 200, r.text
        order_id = r.json()["id"]
    c = context.client.post(
        f"/api/admin/orders/{order_id}/confirm-payment",
        json={"pay_method": "scan"},
        headers=_admin(context),
    )
    assert c.status_code == 200, c.text
    return c.json()


def _refund_apply(context, child: dict, order_id: int, reason: str = REFUND_REASON) -> dict:
    r = context.client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": child["id"], "order_id": order_id, "reason": reason},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _withdrawal_apply(context, child: dict, reason: str = WITHDRAW_REASON) -> dict:
    r = context.client.post(
        "/api/miniapp/withdrawals",
        json={"child_id": child["id"], "reason": reason},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _transfer_apply(context, source: dict, target: dict) -> dict:
    r = context.client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": source["id"], "target_child_id": target["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _inbox(context, headers, **params) -> dict:
    r = context.client.get(
        "/api/admin/admin-notifications",
        params={"page": 1, "page_size": 100, **params},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _item(context, headers, ref_type: str, ref_id, scene: str | None = None) -> dict:
    """收件箱里按 (ref_type, ref_id[, scene]) 取那一条（显示态由 resolver 实时算）。

    同一单据可能同时挂着**两条**待办（如转让：申请一条 + 临近超时预警一条），
    故按 ref_type/ref_id 找时若存在多条，必须再按 scene 收窄。
    """
    items = [
        i
        for i in _inbox(context, headers)["items"]
        if i["ref_type"] == ref_type
        and str(i["ref_id"]) == str(ref_id)
        and (scene is None or i["scene"] == scene)
    ]
    assert len(items) == 1, f"{ref_type}#{ref_id}(scene={scene}) 应恰好一条待办：{items}"
    return items[0]


def _counts(context, headers) -> dict:
    r = context.client.get("/api/admin/todo-counts", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _run_task(context, name: str) -> dict:
    r = context.client.post(f"/api/admin/tasks/{name}/run", headers=_admin(context))
    assert r.status_code == 200, r.text
    return r.json()


def _todos(context, **filters) -> list:
    """直读待办表（验"落了几行"这类**行数**事实；显示态一律以接口为准）。"""
    with _db() as db:
        q = db.query(AdminNotification).filter(AdminNotification.is_deleted == 0)
        for key, value in filters.items():
            q = q.filter(getattr(AdminNotification, key) == value)
        return [(n.scene, n.ref_type, n.ref_id, n.content) for n in q.all()]


def _set_transfer_expires(context, transfer_id: int, delta: timedelta) -> None:
    """时间旅行：把转让单的到期时刻拨到过去/未来（`expires_at` 是唯一时限判据）。"""
    from backend.domain.identity.models import TransferRequest

    with _db() as db:
        db.query(TransferRequest).filter(TransferRequest.id == transfer_id).update(
            {"expires_at": datetime.now() + delta}
        )
        db.commit()


def _transfer_status(context, transfer_id: int) -> str:
    from backend.domain.identity.models import TransferRequest

    with _db() as db:
        return db.query(TransferRequest.status).filter(TransferRequest.id == transfer_id).scalar()


def _timeout_hours(context) -> int:
    """超时时限读配置（判定与文案同源，步骤不许写死小时数）。"""
    from backend.common.config_service import ConfigService

    with _db() as db:
        return int(ConfigService(db).get_value("transfer_review_timeout_hours", "72"))


def _same_transaction_probe(context, child: dict, order_id: int) -> None:
    """**同事务**证明：服务层直调（`skip_commit=True`）→ 申请行与待办行在同一会话可见 → 回滚 → 两行一起消失。

    为什么不用 HTTP 验这条：申请接口一次性提交，外部观察不到"同事务"这件事；而
    "申请与待办同生同死"正是 FEAT-071 的核心承诺（早期版本曾出现"待办丢了/多写了"）。
    """
    from backend.domain.identity.models import Child, RefundRequest
    from backend.domain.identity.wm10_service import RefundService

    with _db() as db:
        kid = db.query(Child).filter(Child.id == child["id"]).first()
        req = RefundService(db).apply(kid, order_id, "同事务探针（不提交）", skip_commit=True)
        rid = str(req.id)
        same_tx = (
            db.query(AdminNotification)
            .filter(
                AdminNotification.ref_type == AdminNotification.REF_REFUND_REQUEST,
                AdminNotification.ref_id == rid,
            )
            .count()
        )
        assert same_tx == 1, "待办必须与申请**在同一事务内**可见"
        db.rollback()
    with _db() as db:
        assert db.query(RefundRequest).filter(RefundRequest.id == int(rid)).count() == 0, (
            "回滚后申请行应消失"
        )
        assert db.query(AdminNotification).filter(AdminNotification.ref_id == rid).count() == 0, (
            "回滚后待办行必须一起消失（证明同事务，而不是各自提交）"
        )


def _refund_flow_setup(context) -> dict:
    """家长 + 孩子 + 观察期费已收 + 退款申请（本域四个场景共用的最小动线）。

    为什么不用押金单：**押金退款不能单独申请**（V1.1 §3.5：随退会/权益转让流程自动发起），
    家长端单笔退款走的是会员费订单 → 会员费退款会**联动创建退会单并锁孩子**（R-309），
    该联动单**不发管理待办**（只有家长主动退会才发）——故本域待办仍只多退款那一条。
    """
    kid = _kid(context, "小明")
    _pay_order(context, kid["id"], "deposit")
    fee = _pay_order(context, kid["id"], "observation_fee")
    app = _refund_apply(context, kid, fee["id"])
    context.kid, context.order, context.refund = kid, fee, app
    return app


def _transfer_flow_setup(context) -> dict:
    """正式会员转出方 + 从未入会受让方 + 转让申请。"""
    src, tgt = _kid(context, "小刚"), _kid(context, "小兰")
    _pay_order(context, src["id"], "formal_fee")
    app = _transfer_apply(context, src, tgt)
    context.transfer = app
    return app


# ---------- 场景 1：申请事件产生管理待办通知 ----------


@when("家长在小程序申请退款或退会或权益转让")
def step_three_applications(context):
    _parent(context)
    ming, hong = _kid(context, "小明"), _kid(context, "小红")
    _pay_order(context, ming["id"], "deposit")
    fee = _pay_order(context, ming["id"], "observation_fee")
    _pay_order(context, hong["id"], "deposit")
    _pay_order(context, hong["id"], "observation_fee")
    _same_transaction_probe(context, ming, fee["id"])
    context.apps = {
        "refund": _refund_apply(context, ming, fee["id"]),
        "withdrawal": _withdrawal_apply(context, hong),
        "transfer": _transfer_flow_setup(context),
    }
    for name, app in context.apps.items():
        assert app.get("id"), (name, app)


@then("管理待办通知与申请在同一事务内落库")
def step_todo_written_with_application(context):
    from backend.domain.identity.models import RefundRequest, TransferRequest, WithdrawalRequest

    pairs = (
        (
            AdminNotification.SCENE_REFUND_APPLY,
            "refund_request",
            context.apps["refund"]["id"],
            RefundRequest,
        ),
        (
            AdminNotification.SCENE_WITHDRAWAL_APPLY,
            "withdrawal_request",
            context.apps["withdrawal"]["id"],
            WithdrawalRequest,
        ),
        (
            AdminNotification.SCENE_TRANSFER_APPLY,
            "transfer",
            context.apps["transfer"]["id"],
            TransferRequest,
        ),
    )
    for scene, ref_type, ref_id, model in pairs:
        rows = _todos(context, scene=scene, ref_id=str(ref_id))
        assert len(rows) == 1, f"{scene} 应恰好一条待办：{rows}"
        assert rows[0][1] == ref_type, rows
        with _db() as db:
            assert db.query(model).filter(model.id == ref_id).count() == 1, f"{ref_type} 业务行应在"


@then("退款与退会的通知内容包含家长填写的申请原因原文")
def step_content_keeps_reason(context):
    refund = _todos(context, scene=AdminNotification.SCENE_REFUND_APPLY)
    assert REFUND_REASON in refund[0][3], refund
    withdrawal = _todos(context, scene=AdminNotification.SCENE_WITHDRAWAL_APPLY)
    assert WITHDRAW_REASON in withdrawal[0][3], withdrawal
    # 转让申请表单**没有原因字段**（`TransferApplyRequest` 只有两个 child_id）→ 文案带双方姓名
    transfer = _todos(context, scene=AdminNotification.SCENE_TRANSFER_APPLY)
    assert "小刚" in transfer[0][3] and "小兰" in transfer[0][3], transfer


@then("同一申请重复触发不会产生重复通知")
def step_no_duplicate_notification(context):
    """幂等键 `(scene, ref_type, ref_id, dedup_key, is_deleted)`；直调写入层（HTTP 重复入口被业务守卫挡住）。"""
    from backend.common.admin_notifications import AdminNotifyService

    rid = str(context.apps["refund"]["id"])
    with _db() as db:
        again = AdminNotifyService(db).send(
            scene=AdminNotification.SCENE_REFUND_APPLY,
            title="【退款申请】",
            content="重复触发（应被幂等挡下）",
            ref_type=AdminNotification.REF_REFUND_REQUEST,
            ref_id=rid,
        )
        db.commit()
    assert again is False, "同一键重复触发不应再写一行"
    rows = _todos(context, scene=AdminNotification.SCENE_REFUND_APPLY, ref_id=rid)
    assert len(rows) == 1, rows


# ---------- 场景 2：家长撤销申请后待办诚实失效 ----------


@given("家长已提交退款申请且管理待办显示待处理")
def step_refund_pending(context):
    _refund_flow_setup(context)
    item = _item(context, _admin(context), "refund_request", context.refund["id"])
    assert item["effective_status"] == "pending" and item["status_text"] == "待处理", item
    context.todo_id = item["id"]
    assert _counts(context, _admin(context))["refund_pending"] == 1


@when("家长撤销该申请")
def step_parent_cancels(context):
    r = context.client.post(
        f"/api/miniapp/refund-requests/{context.refund['id']}/cancel",
        json={"child_id": context.kid["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled", r.json()


@then("该待办变为已失效并注明家长已撤销")
def step_todo_invalid_cancelled(context):
    item = _item(context, _admin(context), "refund_request", context.refund["id"])
    assert item["id"] == context.todo_id, item
    assert item["effective_status"] == "invalid", item
    assert "家长已撤销" in item["status_text"], item


@then("该待办不再计入待处理徽标计数")
def step_invalid_not_counted(context):
    counts = _counts(context, _admin(context))
    assert counts["refund_pending"] == 0, counts
    assert counts["admin_total"] == 0, counts


# ---------- 场景 3：转让超时后待办自动失效 ----------


@given("一笔权益转让申请已提交管理待办")
def step_transfer_pending(context):
    _transfer_flow_setup(context)
    item = _item(context, _admin(context), "transfer", context.transfer["id"])
    assert item["effective_status"] == "pending", item
    assert _counts(context, _admin(context))["transfer_pending"] == 1


@when("转让申请超过 {hours:d} 小时未审核被系统自动作废")
def step_transfer_auto_expire(context, hours):
    assert _timeout_hours(context) == hours, (
        "场景时限必须与配置同源（transfer_review_timeout_hours）"
    )
    _set_transfer_expires(context, context.transfer["id"], timedelta(hours=-(hours + 1)))
    res = _run_task(context, "transfer_expire_check")
    assert res["processed"] >= 1, res
    assert _transfer_status(context, context.transfer["id"]) == "expired", _transfer_status(
        context, context.transfer["id"]
    )


@then("该待办变为已失效并注明已超时")
def step_todo_invalid_expired(context):
    item = _item(context, _admin(context), "transfer", context.transfer["id"])
    assert item["effective_status"] == "invalid", item
    assert "已超时" in item["status_text"], item


@then("徽标计数随之归零")
def step_badge_zero(context):
    counts = _counts(context, _admin(context))
    assert counts["transfer_pending"] == 0 and counts["admin_total"] == 0, counts


# ---------- 场景 4：转让临近超时预警 ----------


@given("一笔权益转让申请剩余不足 {hours:d} 小时仍未审核")
def step_transfer_expiring_soon(context, hours):
    _transfer_flow_setup(context)
    assert _timeout_hours(context) == 72, "默认时限 72h（场景前提）"
    _set_transfer_expires(context, context.transfer["id"], timedelta(hours=hours - 4))
    item = _item(context, _admin(context), "transfer", context.transfer["id"])
    assert item["effective_status"] == "pending", item


@when("超时预警定时任务运行")
def step_run_expiring_warn(context):
    context.warn_first = _run_task(context, "transfer_expiring_warn")
    context.warn_second = _run_task(context, "transfer_expiring_warn")


@then("管理待办新增转让临近超时提醒")
def step_expiring_warn_written(context):
    scene = AdminNotification.SCENE_TRANSFER_EXPIRING
    rows = _todos(context, scene=scene, ref_id=str(context.transfer["id"]))
    assert len(rows) == 1, rows
    assert "小时" in rows[0][3] and "小刚" in rows[0][3], rows
    item = _item(
        context,
        _admin(context),
        "transfer",
        context.transfer["id"],
        scene=AdminNotification.SCENE_TRANSFER_APPLY,
    )
    assert item["effective_status"] == "pending", item  # 预警不改单据状态（转让仍待审）
    assert _counts(context, _admin(context))["transfer_expiring"] == 1


@then("同一转让只提醒一次")
def step_expiring_warn_once(context):
    assert context.warn_first["processed"] == 1, context.warn_first
    assert context.warn_second["processed"] == 0, context.warn_second
    rows = _todos(
        context, scene=AdminNotification.SCENE_TRANSFER_EXPIRING, ref_id=str(context.transfer["id"])
    )
    assert len(rows) == 1, rows


# ---------- 场景 5：权限按角色过滤待办 ----------


@given("运营专员登录管理后台")
def step_staff_login(context):
    """数据面：一条审核类待办（退款）+ 一笔待确认收款订单（staff 该看的那个数）。"""
    _refund_flow_setup(context)
    _pay_order_pending(context, _kid(context, "小强"))
    token = _staff(context)
    super_counts = _counts(context, _admin(context))
    assert super_counts["refund_pending"] == 1, super_counts  # 超管看得见
    assert super_counts["order_pending_manual"] == 1, super_counts
    assert token


def _pay_order_pending(context, child: dict) -> dict:
    """只建单不收款 → `pending_manual_confirm`（仪表盘「待确认收款」的数据源）。"""
    r = context.client.post(
        "/api/admin/orders",
        json={"child_id": child["id"], "order_type": "formal_fee"},
        headers=_admin(context),
    )
    assert r.status_code == 200, r.text
    return r.json()


@then("侧边栏徽标不显示审核类待办计数")
def step_staff_badge_hides_audit(context):
    """侧边栏徽标取 `counts.admin_total`（`admin-web/src/pages/Layout.tsx`），故这里断两件事：
    审核类键全为 0，且 `admin_total` **只由 staff 类键求和**得出。"""
    counts = _counts(context, _staff(context))
    for key in AUDIT_KEYS:
        assert counts[key] == 0, (key, counts)
    assert counts["admin_total"] == sum(counts[k] for k in STAFF_KEYS), counts


@then("管理待办页签可见申请记录但为只读")
def step_staff_inbox_readonly(context):
    """T20g（#8）：staff 可见全量记录（不 403、不再"暂无"），但**只读**——手动标记仍仅超管。"""
    inbox = _inbox(context, _staff(context))
    assert inbox["read_only"] is True, inbox
    assert inbox["items"], "staff 应看到记录（只读），不是空列表"
    assert any(i["scene"] == AdminNotification.SCENE_REFUND_APPLY for i in inbox["items"]), inbox
    r = context.client.post(
        f"/api/admin/admin-notifications/{inbox['items'][0]['id']}/handle",
        json={"reason": "专员不该能标记"},
        headers=_staff(context),
    )
    assert r.status_code == 403, r.text


@then("仪表盘待办卡仍显示待确认收款的真实计数")
def step_staff_dashboard_count(context):
    counts = _counts(context, _staff(context))
    assert counts["order_pending_manual"] == 1, counts


# ---------- 场景 6：审核完成待办闭环 ----------


@given("超级管理员从管理待办点击去处理")
def step_admin_open_todo(context):
    _refund_flow_setup(context)
    context.todo = _item(context, _admin(context), "refund_request", context.refund["id"])


@then("页面直达对应申请所在列表并高亮该行")
def step_todo_links_to_list(context):
    """跳转定义在前端 `ADMIN_REF_ROUTE`：`ref_type+ref_id` → `/refund-center?tab=refunds&highlight={ref_id}`。
    后端要交付的就是这对字段，且目标列表里**确实有该单**（否则高亮落空）。"""
    assert context.todo["ref_type"] == "refund_request", context.todo
    assert context.todo["ref_id"] == str(context.refund["id"]), context.todo
    rows = context.client.get("/api/admin/refund-requests", headers=_admin(context))
    assert rows.status_code == 200, rows.text
    assert any(int(r["id"]) == context.refund["id"] for r in rows.json()), rows.json()


@when("超级管理员完成审核通过或拒绝")
def step_admin_review(context):
    r = context.client.post(
        f"/api/admin/refund-requests/{context.refund['id']}/review",
        json={"approve": False, "remark": REVIEW_REMARK},
        headers=_admin(context),
    )
    assert r.status_code == 200, r.text


@then("该待办变为已审结并记录处理人")
def step_todo_done_with_handler(context):
    item = _item(context, _admin(context), "refund_request", context.refund["id"])
    assert item["effective_status"] == "done", item
    assert item["handled_by_name"] == "超级管理员", item
    assert item["handled_at"], item


@then("徽标计数立即刷新而非等待轮询")
def step_badge_refresh_now(context):
    """徽标"立即刷新"靠前端动作后重取；可审计的是**服务端已经即刻归零**（不必等任务/缓存 TTL）。"""
    counts = _counts(context, _admin(context))
    assert counts["refund_pending"] == 0 and counts["admin_total"] == 0, counts


# ---------- 场景 7：手动标记已处理必须留痕 ----------


@given("超级管理员对待办手动标记已处理")
def step_admin_manual_handle(context):
    _refund_flow_setup(context)
    context.todo = _item(context, _admin(context), "refund_request", context.refund["id"])
    assert context.todo["effective_status"] == "pending", context.todo


@then("必须填写处理原因否则提交被拒绝")
def step_reason_required(context):
    url = f"/api/admin/admin-notifications/{context.todo['id']}/handle"
    empty = context.client.post(url, json={"reason": ""}, headers=_admin(context))
    assert empty.status_code == 422, empty.text
    blank = context.client.post(url, json={"reason": "   "}, headers=_admin(context))
    assert blank.status_code == 422 and "原因" in blank.json()["detail"], blank.text
    # 被拒＝无副作用：待办仍是待处理
    item = _item(context, _admin(context), "refund_request", context.refund["id"])
    assert item["effective_status"] == "pending", item


@then("标记动作写入审计日志")
def step_handle_audited(context):
    from backend.common.system_models import AuditLog

    url = f"/api/admin/admin-notifications/{context.todo['id']}/handle"
    r = context.client.post(url, json={"reason": MANUAL_REASON}, headers=_admin(context))
    assert r.status_code == 200 and r.json()["handled"] is True, r.text
    with _db() as db:
        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "admin_notification.handle",
                AuditLog.target_id == str(context.todo["id"]),
            )
            .all()
        )
        assert len(rows) == 1, rows
        assert rows[0].reason == MANUAL_REASON, rows[0].reason
        assert rows[0].target_type == "admin_notification", rows[0].target_type
    item = _item(context, _admin(context), "refund_request", context.refund["id"])
    assert item["effective_status"] == "done", item
    assert "手动标记" in item["status_text"], item
