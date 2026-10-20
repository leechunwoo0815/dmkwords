# tests/unit/test_p1_audit_20261015.py — 第三轮大审查（2026-10-15）P1 修复·资金四条
"""逐条对应 `docs/审查报告-全量-2026-10-15.md` §一：

- **#3** 退会结算按类型白名单扫全部 PAID 单、不看报名状态 → 已签到参加过的活动费
  也被自动结算出款（成本已发生还退钱）。判据抽到 `refund_rules.activity_refund_block`，
  与家长申请入口共用。
- **#4** 超管「代发起退款」（`POST /api/admin/orders/{id}/refund`）不查
  `refund_status=transferred` 与活动冻结矩阵 → 同一笔年费既转让给受让方又退款。
- **#5** 冻结期（`operation_locked`）只在建单/发起支付/确认收款三处拦，**线上回调结算链
  不查** → 冻结前拿到的支付参数（约 2 小时有效）在退会审核期间付进来照常入账。
- **#6** 结算读 Child 无行锁 → 同一孩子两笔会员单并发收款各自读旧到期日再各写
  `base+365`（丢更新：两笔钱只加一次时长）。

每条修复都做过「去修复即红」的变异自证（红线 147）：用例 docstring 里写明还原修复后的失败形态。
"""

import threading
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func

from tests.unit.helpers import assert_locked_read, lock_wait_query
from tests.unit.test_wm9_activity import _h, _mk_activity


def _db():
    from backend.database import get_session

    return get_session()


def _pay(client, h, child_id, order_type):
    """管理端造单 + 确认收款。"""
    o = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    ).json()
    r = client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r.status_code == 200, r.text
    return o


def _family(client, h, phone, name="孩"):
    """家长 + 孩子 + 小程序登录头（先缴观察期费成观察期会员）。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    token = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()["token"]
    _pay(client, h, c["id"], "observation_fee")
    return p, c, {"Authorization": f"Bearer {token}"}


def _enroll_activity(client, h, mini, child_id, *, hours_later=72, fee=50, title="审查活动"):
    """报名收费活动 + 确认收款（报名转正）。"""
    act = _mk_activity(client, h, quota=3, fee=fee, hours_later=hours_later, title=title)
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": child_id}, headers=mini
    ).json()
    r = client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r.status_code == 200, r.text
    return act, e


def _order_no(order_id: int) -> str:
    from backend.domain.identity.models import Order

    with _db() as db:
        return db.query(Order.order_no).filter(Order.id == order_id).scalar()


def _signin(client, h, enrollment_id: int) -> None:
    from backend.domain.activity.models import ActivityEnrollment

    with _db() as db:
        ticket = (
            db.query(ActivityEnrollment.ticket_code)
            .filter(ActivityEnrollment.id == enrollment_id)
            .scalar()
        )
    r = client.post("/api/admin/activity-signin", json={"ticket_code": ticket}, headers=h)
    assert r.status_code == 200, r.text


def _apply_withdrawal(client, mini, child_id, reason="大审查用例") -> int:
    r = client.post(
        "/api/miniapp/withdrawals", json={"child_id": child_id, "reason": reason}, headers=mini
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _preview(client, h, wid: int) -> dict:
    r = client.get(f"/api/admin/withdrawals/{wid}/settle-preview", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _order_nos(body: dict) -> set:
    return {x["order_no"] for x in body["items"] if x["kind"] == "order"}


# ---------- #3 退会结算不核报名状态 ----------


def _force_deposit_order(child_id: int) -> int:
    """直落库造一笔待人工确认的押金单（构造"两笔并存"历史态；接口层已封，见用例注释）。"""
    from backend.database import get_session
    from backend.domain.identity.models import Child, Order
    from backend.domain.identity.order_service import OrderService

    with get_session() as db:
        child = db.get(Child, child_id)
        o = Order(
            order_no=OrderService.new_order_no(),
            order_type=Order.TYPE_DEPOSIT,
            parent_id=child.parent_id,
            child_id=child.id,
            amount=Decimal("1200.00"),
            status=Order.STATUS_PENDING_MANUAL,
            remark="探针：旁路构造的第二笔押金单",
        )
        db.add(o)
        db.commit()
        return o.id


def test_settle_skips_checked_in_activity_order(client: TestClient):
    """已签到参加过的活动费**不进退会结算清单**（成本已发生）。

    RED（还原修复：`_settle_items` 不调 activity_refund_block）：该活动单出现在
    preview items 里，并随 review 生成退款单 → 线上单自动出款。
    """
    from backend.domain.identity.models import RefundRequest

    h = _h(client)
    _p, c, mini = _family(client, h, "13981500001", "签到退会孩")
    _act, e = _enroll_activity(client, h, mini, c["id"], title="已参加读书会")
    _signin(client, h, e["enrollment"]["id"])
    activity_order_no = _order_no(e["order_id"])

    wid = _apply_withdrawal(client, mini, c["id"])
    body = _preview(client, h, wid)
    assert activity_order_no not in _order_nos(body), (
        f"已签到活动费不该进结算清单，实 items={body['items']}"
        "（RED=按类型白名单一把扫，签到与否不看）"
    )
    # 观察期费仍在（防"一刀切拦掉所有订单"的假修复）
    assert _order_nos(body), "观察期费应仍可退（本次修复只针对活动单）"

    rv = client.post(
        f"/api/admin/withdrawals/{wid}/review",
        json={"approve": True, "remark": "审核通过"},
        headers=h,
    )
    assert rv.status_code == 200, rv.text
    with _db() as db:
        rows = db.query(RefundRequest).filter(RefundRequest.child_id == c["id"]).all()
        assert e["order_id"] not in {r.order_id for r in rows}, (
            f"已签到活动单不该生成退款单，实 {[(r.order_id, str(r.amount)) for r in rows]}"
        )


def test_settle_keeps_upcoming_activity_order(client: TestClient):
    """未开始未签到的活动费仍全额进清单（矩阵只拦违规，不拦合法）。"""
    h = _h(client)
    _p, c, mini = _family(client, h, "13981500002", "未参加退会孩")
    _act, e = _enroll_activity(client, h, mini, c["id"], title="未开始读书会")
    order_no = _order_no(e["order_id"])
    wid = _apply_withdrawal(client, mini, c["id"])
    body = _preview(client, h, wid)
    assert order_no in _order_nos(body), f"未开始活动费应可退，实 items={body['items']}"


# ---------- #4 超管代发起退款绕 transferred / 活动矩阵 ----------


def test_admin_refund_blocked_for_transferred_order(client: TestClient):
    """已随权益转让处置的年费单不可再发起退款（家长侧早有此闸，超管侧漏）。"""
    from backend.domain.identity.models import Order

    h = _h(client)
    _p, c, _mini = _family(client, h, "13981500003", "转让退款孩")
    order = _pay(client, h, c["id"], "formal_fee")
    # 直接置 transferred：转让审核链（R-305）在同一事务里就是这两条 UPDATE 之一，
    # 重造整条转让链对本判据没有额外信息
    with _db() as db:
        row = db.query(Order).filter(Order.id == order["id"]).first()
        row.refund_status = Order.REFUND_STATUS_TRANSFERRED
        db.commit()

    r = client.post(
        f"/api/admin/orders/{order['id']}/refund", json={"remark": "转让后再退"}, headers=h
    )
    assert r.status_code == 422, r.text
    assert "转让" in r.json()["detail"]


def test_admin_refund_blocked_for_checked_in_activity_order(client: TestClient):
    """活动单同样过冻结矩阵（同端点此前可退已签到的活动费）。"""
    h = _h(client)
    _p, c, mini = _family(client, h, "13981500004", "超管退活动孩")
    _act, e = _enroll_activity(client, h, mini, c["id"], title="超管退活动")
    _signin(client, h, e["enrollment"]["id"])
    r = client.post(
        f"/api/admin/orders/{e['order_id']}/refund", json={"remark": "超管退已签到"}, headers=h
    )
    assert r.status_code == 422, r.text
    assert "签到" in r.json()["detail"]


def test_admin_refund_still_works_for_legit_order(client: TestClient):
    """防过度拦截：未转让、未参加的年费单仍可正常建 pending 退款申请。"""
    h = _h(client)
    _p, c, _mini = _family(client, h, "13981500005", "正常退款孩")
    order = _pay(client, h, c["id"], "formal_fee")
    r = client.post(
        f"/api/admin/orders/{order['id']}/refund", json={"remark": "家长诉求"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"


# ---------- #5 冻结期回调结算不查 operation_locked ----------


def test_frozen_child_payment_callback_not_settled(client: TestClient):
    """退会审核中（operation_locked=1）收到支付成功回调 → **不入账**，留资金异常待办。

    RED（还原修复：`_settle_paid` 不查冻结）：订单转 paid、会员到期日顺延，
    而这笔钱不在已生成的退会结算清单里（押金场景最重 → 可能永远不退）。
    """
    from backend.common.admin_notification_models import AdminNotification
    from backend.domain.identity.models import Child, Order

    h = _h(client)
    _p, c, mini = _family(client, h, "13981500006", "冻结收款孩")
    o = client.post(
        "/api/miniapp/orders", json={"child_id": c["id"], "order_type": "formal_fee"}, headers=mini
    ).json()
    assert o["status"] == Order.STATUS_PENDING_PAYMENT, o
    # 冻结：家长申请退会（operation_locked=1，退会审核中）
    _apply_withdrawal(client, mini, c["id"], reason="冻结期收款")
    with _db() as db:
        assert db.query(Child.operation_locked).filter(Child.id == c["id"]).scalar() == 1

    r = client.post(
        "/api/admin/payments/simulate-callback", json={"order_no": o["order_no"]}, headers=h
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["http_status"] == 200 and "待人工" in body["response"]["message"], body

    with _db() as db:
        row = db.query(Order).filter(Order.id == o["id"]).first()
        assert row.status == Order.STATUS_PENDING_PAYMENT, (
            f"冻结期回调不得入账，实 status={row.status}（RED=钱收了、单也结了）"
        )
        assert row.paid_at is None and not row.transaction_id
        kid = db.query(Child).filter(Child.id == c["id"]).first()
        assert kid.member_status != Child.MEMBER_FORMAL, "冻结期回调不得开通会员"
        anomaly = (
            db.query(func.count(AdminNotification.id))
            .filter(
                AdminNotification.scene == AdminNotification.SCENE_PAYMENT_ANOMALY,
                AdminNotification.is_deleted == 0,
            )
            .scalar()
        )
        assert anomaly and anomaly >= 1, "必须留下资金异常待办（钱已收，不能悄悄过去）"


# ---------- #6 并发确认收款丢更新 ----------


def test_concurrent_settle_annual_fees_no_lost_update(client: TestClient, session_pair):
    """同一孩子两笔年费单并发收款 → 到期日必须两笔都算（+730），不是 +365。

    RED（还原修复：结算读 Child 无 `with_for_update().populate_existing()`）：
    后到的结算按**旧快照**的到期日算 base（观察期 +30 天），覆盖先到者的 +365 →
    最终 +395，两笔钱只加一次时长。
    """
    from types import SimpleNamespace

    from backend.domain.identity.models import Child, Order
    from backend.domain.identity.order_service import OrderService

    h = _h(client)
    _p, c, _mini = _family(client, h, "13981500007", "并发收款孩")
    # 两笔年费单：先落库，都不确认收款
    oa = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    ob = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    with _db() as db:
        expire0 = db.query(Child.member_expire).filter(Child.id == c["id"]).scalar()
    assert expire0 == date.today() + timedelta(days=30), f"观察期 30 天基线，实 {expire0}"

    s1, s2 = session_pair
    actor = SimpleNamespace(id=1, display_name="并发管理员")
    order_b = s2.query(Order).filter(Order.id == ob["id"]).first()
    out: dict = {}

    # 第八轮审查 P2-63：原先用 `sleep(1.5)` 猜"HTTP 侧应该到了"——对方没到时用例会
    # 提前放锁，断言可能因为别的原因通过（并发守护退化成空跑）。改成**可判定**：
    # 持锁线程等主线程确认"HTTP 侧真的被行锁挡住"后才提交（Event 握手，不猜时序）。
    holding = threading.Event()
    release = threading.Event()

    def _settle_b():
        try:
            OrderService(s2)._settle_paid(
                order_b,
                actor=actor,
                pay_method="scan",
                remark="并发先到者",
                payer_id=1,
                action="order.confirm_payment",
                reason="并发先到者",
            )
            s2.flush()
            holding.set()  # 我已持锁（孩子行 + 订单行）
            release.wait(timeout=15)  # 等主线程确认 HTTP 侧被挡住再提交
            s2.commit()
            out["b"] = "ok"
        except Exception as exc:  # noqa: BLE001 —— 线程内异常要带回主线程断言
            s2.rollback()
            out["b"] = repr(exc)

    t = threading.Thread(target=_settle_b)
    t.start()
    assert holding.wait(timeout=15), "结算线程未进入持锁段"

    r_a_box: dict = {}

    def _http_settle_a():
        r_a_box["r"] = client.post(
            f"/api/admin/orders/{oa['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
        )

    th = threading.Thread(target=_http_settle_a)
    th.start()
    assert_locked_read(lock_wait_query(), "HTTP 侧结算等的 Child 锁定读")
    release.set()
    t.join(timeout=15)
    th.join(timeout=15)
    assert not t.is_alive(), f"结算线程 15s 未结束（疑似死锁）：{out}"
    assert not th.is_alive(), f"HTTP 结算 15s 未结束（疑似死锁）：{r_a_box}"
    assert out.get("b") == "ok", out
    r_a = r_a_box["r"]
    assert r_a.status_code == 200, r_a.text

    with _db() as db:
        kid = db.query(Child).filter(Child.id == c["id"]).first()
        assert kid.member_expire == expire0 + timedelta(days=730), (
            f"两笔年费应各加一年（期望 {expire0 + timedelta(days=730)}），实 {kid.member_expire}"
            "（RED=后到者按旧快照 base 覆盖写，两笔钱只加一次时长）"
        )
        statuses = {
            o.id: o.status for o in db.query(Order).filter(Order.id.in_([oa["id"], ob["id"]])).all()
        }
        assert set(statuses.values()) == {Order.STATUS_PAID}, statuses


# ---------- #1 完播防刷：60 秒宽限被逐笔叠加 ----------


def test_heartbeat_grace_only_applies_to_first_report(client: TestClient):
    """宽限只在首笔：1 秒后声称又覆盖 30 秒 → 拒（合法速率上限 2.4 秒/秒）。

    RED（还原修复：`allowed = elapsed×2.4 + 60` 每笔都加）：1×2.4+60 = 62.4 ≥ 30 →
    放行。心跳 1 秒一次时每笔可新增 62.4 秒覆盖（≈60 倍速），600 秒书 10 笔、约 29 秒
    墙上时间即可刷满 95% 完播（连带打卡/积分/测验解锁/词数）。
    """
    from tests.unit.test_wm6_reading import _backdate, _report, _setup

    h = _h(client)
    c, book, mini = _setup(client, h, "13981510001", "9788151000011")
    # 首笔：宽限仍生效（首帧/对时抖动），一次覆盖 60 秒合法
    r1 = _report(client, mini, c["id"], book["id"], 60, 0)
    assert r1.status_code == 200, r1.text
    assert r1.json()["coverage_seconds"] == 60

    _backdate(client, c["id"], book["id"], 1)
    r2 = _report(client, mini, c["id"], book["id"], 90, 60)
    assert r2.status_code == 422, f"逐笔叠加宽限 = 60 倍速（实 {r2.status_code} {r2.text[:80]}）"
    # 拒绝不落库
    view = client.get(
        f"/api/miniapp/books/{book['id']}/progress?child_id={c['id']}", headers=mini
    ).json()
    assert view["coverage_seconds"] == 60


def test_legit_2x_speed_playback_still_accepted(client: TestClient):
    """防过度拦截：正常心跳（含 2 倍速播放）仍畅通。"""
    from tests.unit.test_wm6_reading import _backdate, _report, _setup

    h = _h(client)
    c, book, mini = _setup(client, h, "13981510002", "9788151000028")
    assert _report(client, mini, c["id"], book["id"], 10, 0).status_code == 200
    _backdate(client, c["id"], book["id"], 10)
    r2 = _report(client, mini, c["id"], book["id"], 30, 10)  # 10s 墙钟覆盖 20s = 2 倍速
    assert r2.status_code == 200, r2.text
    _backdate(client, c["id"], book["id"], 20)
    r3 = _report(client, mini, c["id"], book["id"], 70, 30)  # 20s 墙钟覆盖 40s = 2 倍速
    assert r3.status_code == 200, r3.text
    assert r3.json()["coverage_seconds"] == 70


# ---------- #2 三类通知按"孩子×场景"终身去重 ----------


def test_quiz_result_notifies_every_submission(client: TestClient):
    """每次提交都要有成绩通知（PRD §十）：**同一结果重复出现也要各发一条**。

    共 5 次提交：未通过×2 → 通过 → （重置机会）→ 通过 → 未通过。
    RED（还原修复：任一处理器的 dedup_key 回到默认 "1"）：同结果的两条撞唯一索引 →
    只剩 3~4 条；「未通过」之后永远收不到「通过」也是这个原因。
    """
    from backend.common.notifications import SCENE_READING_QUIZ_RESULT
    from tests.unit.test_wm7_growth import _setup_finished_book

    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13981510003", "9788151000035")
    wrong = ["B", "B", "B", "B", "错"]
    right = ["A", "A", "A", "A", "对"]

    def _submit(answers: list[str], expect_passed: bool | None = None) -> dict:
        r = client.post(
            f"/api/miniapp/quiz/{book['id']}/submit",
            json={"child_id": c["id"], "answers": answers},
            headers=mini,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        if expect_passed is not None:
            assert body["passed"] is expect_passed, body
        return body

    _submit(wrong, False)  # attempt 1
    _submit(wrong, False)  # attempt 2（同一结果，也要有一条通知）
    _submit(right, True)  # attempt 3
    rst = client.post(
        "/api/admin/quiz/attempts/reset",
        json={"child_id": c["id"], "book_id": book["id"], "reason": "大审查用例"},
        headers=h,
    )
    assert rst.status_code == 200, rst.text
    _submit(right, True)  # attempt 4（重置后再通过）
    _submit(wrong, False)  # attempt 5

    data = client.get(
        f"/api/miniapp/notifications?scene={SCENE_READING_QUIZ_RESULT}", headers=mini
    ).json()
    items = data["items"] if isinstance(data, dict) and "items" in data else data
    assert len(items) == 5, (
        f"5 次提交应 5 条成绩通知，实 {len(items)} 条"
        f"（RED=dedup_key 默认 1，同结果的第 2 条被唯一索引吞掉）"
    )
    contents = " ".join(x.get("content", "") for x in items)
    assert contents.count("已通过") == 2, contents
    assert contents.count("未通过") == 3, contents


def test_level_up_notifies_each_transition(client: TestClient):
    """等级升级通知按"跃迁"去重：A→B、B→C 各一条；重复同跃迁不再发。"""
    from backend.common.events import LevelAdvancedEvent
    from backend.common.notifications import SCENE_READING_LEVEL_UP
    from backend.tasks.notify_handlers import _on_level_up

    h = _h(client)
    _p, c, _mini = _family(client, h, "13981510004", "升级知会孩")
    with _db() as db:
        _on_level_up(LevelAdvancedEvent(child_id=c["id"], from_level="A", to_level="B"), db)
        _on_level_up(LevelAdvancedEvent(child_id=c["id"], from_level="B", to_level="C"), db)
        _on_level_up(LevelAdvancedEvent(child_id=c["id"], from_level="A", to_level="B"), db)
        db.commit()
        n = _count_notifications(db, SCENE_READING_LEVEL_UP)
        assert n == 2, f"两次不同跃迁应两条（重复跃迁不再发），实 {n}（RED=只发第一条）"


def test_milestone_notifies_each_node(client: TestClient):
    """里程碑通知按"节点集合"去重：1000 词、5000 词各一条；重复节点不再发。"""
    from backend.common.events import MilestoneAchievedEvent
    from backend.common.notifications import SCENE_READING_MILESTONE
    from backend.tasks.notify_handlers import _on_milestone_achieved

    h = _h(client)
    _p, c, _mini = _family(client, h, "13981510005", "里程碑知会孩")
    with _db() as db:
        _on_milestone_achieved(MilestoneAchievedEvent(child_id=c["id"], nodes=(1000,)), db)
        _on_milestone_achieved(MilestoneAchievedEvent(child_id=c["id"], nodes=(5000,)), db)
        _on_milestone_achieved(MilestoneAchievedEvent(child_id=c["id"], nodes=(1000,)), db)
        db.commit()
        n = _count_notifications(db, SCENE_READING_MILESTONE)
        assert n == 2, f"两个节点应两条通知（重复节点不再发），实 {n}（RED=终身只发第一条）"


def _count_notifications(db, scene: str) -> int:
    from backend.common.notification_models import Notification

    return (
        db.query(func.count(Notification.id))
        .filter(Notification.scene == scene, Notification.is_deleted == 0)
        .scalar()
    )


# ---------- #8 管理端「释放锁定」只掐副本、不动预约 ----------


def test_release_lock_turns_reservation_exception(client: TestClient):
    """只有一册时「释放锁定」（reserved→available）→ 预约转异常 + 通知家长 + 馆员待办。

    RED（还原修复：`update_copy_status` 只处理 maintenance/lost）：预约仍 `active`——
    占着 1 个额度、家长端仍显示"预约中"，副本却已能被任何孩子借走；该家长到店核销必 409。
    """
    from backend.common.admin_notification_models import AdminNotification
    from backend.common.notifications import SCENE_RESERVATION_EXCEPTION
    from tests.unit.test_g5_reservation_copy_exception_20261014 import (
        _admin_h,
        _book,
        _copy_row,
        _member_child,
        _notifications,
        _reservation_row,
        _reserve,
        _set_copy_status,
    )

    h = _admin_h(client)
    child, mini = _member_child(client, h, "13981520001", "释放锁定孩")
    book_id, copies = _book(client, h, "释放锁定样例", copies=1)
    res = _reserve(client, mini, child["id"], book_id)
    assert _copy_row(copies[0]).status == "reserved"

    r = _set_copy_status(client, h, copies[0], "available")
    assert r.status_code == 200, r.text

    assert _copy_row(copies[0]).status == "available"
    assert _reservation_row(res["id"]).status == "exception", (
        "释放锁定后预约单必须一并处置（RED=预约仍 active 并占名额）"
    )
    parents = _notifications(SCENE_RESERVATION_EXCEPTION, parent_side=True)
    assert len(parents) == 1 and "释放锁定样例" in parents[0].content, [p.content for p in parents]
    todos = _notifications(AdminNotification.SCENE_RESERVATION_EXCEPTION, parent_side=False)
    assert len(todos) == 1, "馆员侧要有跟进待办"
    # 名额立即释放（异常态不占）
    card = client.get(f"/api/admin/circulation/children/{child['id']}/card", headers=h).json()
    assert card["available_quota"] == 30


def test_release_lock_switches_to_other_copy(client: TestClient):
    """有两册时「释放锁定」→ 预约改锁另一册（预约继续有效），被释放那册回到在馆。"""
    from tests.unit.test_g5_reservation_copy_exception_20261014 import (
        _admin_h,
        _book,
        _copy_row,
        _member_child,
        _reservation_row,
        _reserve,
        _set_copy_status,
    )

    h = _admin_h(client)
    child, mini = _member_child(client, h, "13981520002", "改锁孩")
    book_id, copies = _book(client, h, "释放改锁样例", copies=2)
    res = _reserve(client, mini, child["id"], book_id)
    before = _reservation_row(res["id"])
    assert before.copy_id == copies[0]

    r = _set_copy_status(client, h, copies[0], "available")
    assert r.status_code == 200, r.text

    after = _reservation_row(res["id"])
    assert after.status == "active" and after.copy_id == copies[1], (
        f"应改锁第二册，实 status={after.status} copy={after.copy_id}（RED=预约仍锁已释放的一册）"
    )
    assert _copy_row(copies[0]).status == "available"
    assert _copy_row(copies[1]).status == "reserved"


# ---------- #14 还书带损不生成赔偿待办 ----------


def test_return_with_damage_creates_compensation_todo(client: TestClient):
    """还书时登记损坏/遗失 → 与「报损·遗失」同口径：写赔偿待办（含借阅人）。

    RED（还原修复：return_book 只改状态）：待办数为 0，运营视角"同一件事两种后果"。
    """
    from backend.common.admin_notification_models import AdminNotification
    from backend.domain.catalog.models import BookCopy
    from tests.unit.test_g5_reservation_copy_exception_20261014 import _notifications
    from tests.unit.test_wm5_circulation import _full_child
    from tests.unit.test_wm5_circulation import _h as _circ_h

    h = _circ_h(client)
    c, book = _full_child(client, h, "13981520003", "还书带损孩")
    rec = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "isbn": book["isbn"]},
        headers=h,
    ).json()
    r = client.post(
        "/api/admin/circulation/return",
        json={"copy_id": rec["copy_id"], "condition": "maintenance"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    todos = _notifications(AdminNotification.SCENE_COPY_DAMAGE_COMPENSATION, parent_side=False)
    assert len(todos) == 1, f"还书带损必须生成赔偿待办，实 {len(todos)} 条（RED=只改状态）"
    assert "还书带损孩" in todos[0].content and "损坏" in todos[0].content, todos[0].content
    with _db() as db:
        copy = db.query(BookCopy).filter(BookCopy.id == rec["copy_id"]).first()
        assert copy.status == BookCopy.STATUS_MAINTENANCE


# ---------- #11 小程序不消费 off_shelf ----------


def test_miniapp_consumes_off_shelf(client: TestClient):
    """「后端下发 `off_shelf`、前端零消费」的形状必须消失：两个消费点都在。

    这是**静态契约**检查（不是渲染检查）：PRD §4.4 要求收藏夹/详情给下架书标注"已下架"，
    原先全仓 `grep off_shelf miniapp/` 只命中音频文案里的字符串、wxml 零消费——
    下架书与正常书在收藏夹里外观完全一致，点进去点播放才被拦。
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    shelf = (root / "miniapp/pages/shelf/shelf.wxml").read_text(encoding="utf-8")
    detail = (root / "miniapp/pages/reading-pkg/book-detail/book-detail.wxml").read_text(
        encoding="utf-8"
    )
    assert "item.off_shelf" in shelf and "已下架" in shelf, "收藏夹必须标注已下架（PRD §4.4）"
    assert "book.off_shelf" in detail and "已下架" in detail, "详情页必须标注已下架（PRD §4.4）"
    # 样式必须落地（悬空类名 = 标注看不见；style 检查器 R12 也会红）
    for rel in (
        "miniapp/pages/shelf/shelf.wxss",
        "miniapp/pages/reading-pkg/book-detail/book-detail.wxss",
    ):
        assert ".tag-off" in (root / rel).read_text(encoding="utf-8"), f"{rel} 缺 .tag-off 样式"


# ---------- #12 403 吞掉真实 detail / error_code 无生产方 ----------


def test_audio_403_carries_reason_and_error_code(client: TestClient):
    """下架书音频 403：`detail` 是真实原因（家长能看到），`error_code=audio.off_shelf`（新通道）。

    RED（还原修复：`ForbiddenError(AUDIO_REASON_TEXT[reason])` 无 error_code）：
    error_code 断言失败——前端两条通道（detail 文案 / error_code 分支）里第二条恒为空。
    """
    from tests.unit.helpers import force_book_on
    from tests.unit.test_wm6_reading import _setup

    h = _h(client)
    c, book, mini = _setup(client, h, "13981530001", "9788153000018")
    force_book_on(client, h, book["id"])  # 幂等（_setup 已上架）

    token = mini["Authorization"].split(" ", 1)[1]
    url = f"/api/miniapp/books/{book['id']}/audio?child_id={c['id']}&token={token}"
    ok = client.get(url)
    assert ok.status_code == 200, ok.text

    # 下架 → 音频流必须 403 且带真实原因 + error_code
    t = client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=h)
    assert t.status_code == 200, t.text
    bad = client.get(url)
    assert bad.status_code == 403, bad.text
    body = bad.json()
    assert "已下架" in body["detail"], body
    assert body.get("error_code") == "audio.off_shelf", (
        f"403 必须带结构化 error_code（RED=通道无生产方）：{body}"
    )
    # 预检端点（家长侧文案分流）同步给同一原因
    perm = client.get(
        f"/api/miniapp/books/{book['id']}/audio-permission?child_id={c['id']}", headers=mini
    )
    assert perm.status_code == 200, perm.text
    assert perm.json() == {"allowed": False, "reason": "off_shelf"}, perm.json()


# ---------- #13 押金重复在线单（无 Deposit 行时建单闸失效 → 钱收了不入账） ----------


def _bind_openid(parent_id: int, openid: str = "o_audit_13") -> None:
    from backend.domain.identity.models import Parent

    with _db() as db:
        row = db.query(Parent).filter(Parent.id == parent_id).first()
        row.wechat_openid = openid
        db.commit()


def test_second_inflight_deposit_order_blocked(client: TestClient):
    """无 Deposit 行时，第二笔**在途**押金单必须被拒（否则两笔都能付 → 第二笔钱收了不入账）。

    RED（还原修复：建单闸只看"已有 Deposit 行且非 unpaid"）：第二笔也 200 →
    两笔都进待支付，家长先后付两笔，第二笔走回调兜底（anomaly + 押金不动 + 人工退款）。
    """
    h = _h(client)
    p, c, mini = _family(client, h, "13981540001", "押金双单孩")
    _bind_openid(p["id"])
    assert (
        client.get(f"/api/admin/deposits/children/{c['id']}", headers=h).status_code
        in (200, 404)  # 现在还没有押金账户
    )
    first = client.post(
        "/api/miniapp/orders", json={"child_id": c["id"], "order_type": "deposit"}, headers=mini
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/miniapp/orders", json={"child_id": c["id"], "order_type": "deposit"}, headers=mini
    )
    assert second.status_code == 422, (
        f"已有一笔在途押金单时不得再建（实 {second.status_code} {second.text[:120]}）"
        "（RED=闸只看已有押金账户，无账户时形同不存在）"
    )
    assert "押金" in second.json()["detail"]


def test_stale_deposit_order_cannot_be_paid_after_deposit_paid(client: TestClient):
    """押金已缴后，另一笔旧押金单发起支付 → 必须在**调网关前**被拒（订单状态不得前移）。

    RED（还原修复：押金资格只在结算时判）：Phase 1 会先把订单推到 pending_payment 并提交，
    然后才在结算链里失败——真通道下微信已收钱 → 回调兜底 200「已记录待人工处理」+ anomaly，
    押金账户纹丝不动（只能人工退款）。
    """
    h = _h(client)
    p, c, mini = _family(client, h, "13981540002", "押金旧单孩")
    _bind_openid(p["id"], "o_audit_13b")
    # 管理端造两笔押金单（线下收款场景），家长随后在线上支付
    a = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "deposit"}, headers=h
    ).json()
    # 第八轮审查 P2-1 之后接口层已封"第二笔在途押金单"——本用例验的是**家长端支付侧**的闸
    # （旧单发起支付必须在调网关前被拒），故直落库构造历史态
    b = {"id": _force_deposit_order(c["id"])}
    pa = client.post(f"/api/miniapp/orders/{a['id']}/pay", headers=mini)
    assert pa.status_code == 200 and pa.json()["instant_paid"] is True, pa.text

    pb = client.post(f"/api/miniapp/orders/{b['id']}/pay", headers=mini)
    assert pb.status_code == 422, pb.text
    assert "押金" in pb.json()["detail"], pb.json()
    # 未调网关证据：订单状态不得被推到 pending_payment
    with _db() as db:
        from backend.domain.identity.models import Order

        status = db.query(Order.status).filter(Order.id == b["id"]).scalar()
        assert status == Order.STATUS_PENDING_MANUAL, (
            f"拒付必须发生在状态前移/调网关之前（实 {status}）"
        )
        assert db.query(Order.transaction_id).filter(Order.id == b["id"]).scalar() in (None, "")


def test_zero_delta_report_not_rejected_by_clock_rounding(client: TestClient):
    """负 elapsed（DATETIME 秒级四舍五入造成"上一拍在未来"）不得把零新增上报判成异常。

    RED（不钳 max(0, elapsed)：`allowed` 变负 → `0 > -0.995` 为真 → 422）：这是 2026-10-16
    全量门禁 behave 里**随机红/绿**的那条（`audio_reading.feature` 完整听完判定读完）——
    上一拍 `05.63s` 落库成 `06s`，下一拍读回"未来 0.4 秒"，恰好 delta 为 0 时被负容差拒。
    """
    from datetime import datetime, timedelta

    from tests.unit.test_wm6_reading import _report, _setup

    h = _h(client)
    c, book, mini = _setup(client, h, "13981550001", "9788155000017")
    assert _report(client, mini, c["id"], book["id"], 60, 0).status_code == 200
    with _db() as db:
        from backend.domain.reading.models import ReadingProgress

        row = (
            db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == c["id"],
                ReadingProgress.book_id == book["id"],
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
        # 模拟秒级四舍五入：上一拍落在"未来 0.4 秒"
        row.last_report_at = datetime.now() + timedelta(milliseconds=400)
        db.commit()
    r = _report(client, mini, c["id"], book["id"], 60, 0)  # 零新增覆盖
    assert r.status_code == 200, f"零新增上报不该被拒（负容差假红）：{r.text[:120]}"
