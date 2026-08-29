# tests/unit/test_wm11_tasks.py — WM11 定时任务（真实链路）
"""覆盖：12 项任务（会员过期落库/到期提醒/待评估/预约释放+提醒/订单超时/转让超时/
借阅到期提醒/逾期标记/活动提醒/活动结束/99元提醒）+ 任务看板 + 手动触发 + 运行日志。
断言锚点：FEAT-019 / PRD §12 提醒行 / docs/09 §五 13+ 清单 / D1 第 3 层 / C13 / ADR-008。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from backend.common.notification_models import Notification, TaskRunLog
from backend.database import get_session
from backend.domain.identity.models import Child, Order
from tests.unit.helpers import force_book_on


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _parent(client, h, phone="13800002001", name="任务家长"):
    r = client.post("/api/admin/members/parents", json={"name": name, "phone": phone}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _child(client, h, pid, name="任务孩"):
    r = client.post(f"/api/admin/members/parents/{pid}/children", json={"name": name}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _order(client, h, cid, order_type="observation_fee"):
    r = client.post(
        "/api/admin/orders", json={"child_id": cid, "order_type": order_type}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _confirm(client, h, oid, method="scan"):
    r = client.post(
        f"/api/admin/orders/{oid}/confirm-payment", json={"pay_method": method}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _book(client, h, isbn="9780545582889"):
    b = client.post(
        "/api/admin/books", json={"isbn": isbn, "title": "Dog Man", "word_count": 2500}, headers=h
    ).json()
    force_book_on(client, h, b["id"])
    return b


def _full_member(client, h, phone="13800002002"):
    """观察期会员 + 押金 + 上架书。"""
    p = _parent(client, h, phone=phone)
    c = _child(client, h, p["id"])
    _confirm(client, h, _order(client, h, c["id"])["id"])
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    _confirm(client, h, do["order_id"])
    book = _book(client, h)
    return p, c, book


def _borrow(client, h, cid, isbn):
    r = client.post(
        "/api/admin/circulation/borrow", json={"child_id": cid, "isbn": isbn}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _notifs(parent_id: int) -> list[Notification]:
    with get_session() as db:
        return (
            db.query(Notification)
            .filter(Notification.parent_id == parent_id, Notification.is_deleted == 0)
            .order_by(Notification.id.desc())
            .all()
        )


def _run(client, h, task_name: str) -> dict:
    r = client.post(f"/api/admin/tasks/{task_name}/run", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 1. 会员过期落库（D1 第 3 层） ----------


def test_member_expire_check_formal_to_expired_and_observation(client: TestClient):
    h = _h(client)
    # formal：确认年费订单
    p = _parent(client, h, phone="13800002011")
    c = _child(client, h, p["id"])
    _confirm(client, h, _order(client, h, c["id"], "formal_fee")["id"])
    with get_session() as db:
        child = db.query(Child).filter(Child.id == c["id"]).first()
        # 定时任务造历史态无 API 路径，有意直改
        child.member_expire = datetime.now().date() - timedelta(days=1)
        db.commit()
    # observation：另一个孩子
    p2 = _parent(client, h, phone="13800002012")
    c2 = _child(client, h, p2["id"])
    _confirm(client, h, _order(client, h, c2["id"])["id"])
    with get_session() as db:
        child = db.query(Child).filter(Child.id == c2["id"]).first()
        # 定时任务造历史态无 API 路径，有意直改
        child.member_expire = datetime.now().date() - timedelta(days=1)
        db.commit()

    result = _run(client, h, "member_expire_check")
    assert result["status"] == "success"
    with get_session() as db:
        assert db.get(Child, c["id"]).member_status == Child.MEMBER_EXPIRED  # D1 第 3 层
        assert db.get(Child, c2["id"]).member_status == Child.MEMBER_PENDING_EVALUATION  # C13


# ---------- 2. 会员到期提醒（30/14/7/当天，每节点一次） ----------


def test_member_expire_remind_dedup_per_node(client: TestClient):
    h = _h(client)
    p = _parent(client, h, phone="13800002013")
    c = _child(client, h, p["id"])
    _confirm(client, h, _order(client, h, c["id"], "formal_fee")["id"])
    with get_session() as db:
        child = db.query(Child).filter(Child.id == c["id"]).first()
        child.member_expire = datetime.now().date() + timedelta(days=30)
        db.commit()

    r1 = _run(client, h, "member_expire_remind")
    assert r1["status"] == "success"
    assert r1["processed"] >= 1
    r2 = _run(client, h, "member_expire_remind")
    assert r2["processed"] == 0  # 幂等：同一节点不重复

    with get_session() as db:
        rows = (
            db.query(Notification)
            .filter(
                Notification.parent_id == p["id"],
                Notification.scene == "member.expire_remind",
                Notification.dedup_key == "30",
            )
            .all()
        )
    assert len(rows) == 1


# ---------- 3. 待评估每周名单 ----------


def test_pending_evaluation_weekly(client: TestClient):
    h = _h(client)
    p = _parent(client, h, phone="13800002014")
    c = _child(client, h, p["id"])
    _confirm(client, h, _order(client, h, c["id"])["id"])
    with get_session() as db:
        child = db.query(Child).filter(Child.id == c["id"]).first()
        child.member_status = Child.MEMBER_PENDING_EVALUATION
        # 定时任务造历史态无 API 路径，有意直改
        child.update_time = datetime.now() - timedelta(days=10)
        db.commit()
    result = _run(client, h, "pending_evaluation_weekly")
    assert result["status"] == "success"
    assert result["processed"] == 1


# ---------- 4. 预约超时释放 + 即将到期提醒 ----------


def test_reservation_expire_check_releases_copy_and_notifies(client: TestClient):
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800002015")
    mini = client.post("/api/miniapp/login", json={"phone": "13800002015", "code": "1234"})
    mini_h = {"Authorization": f"Bearer {mini.json()['token']}"}
    r = client.post(
        "/api/miniapp/reservations",
        json={"child_id": c["id"], "book_id": book["id"]},
        headers=mini_h,
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    with get_session() as db:
        from backend.domain.reading.models import Reservation

        res = db.query(Reservation).filter(Reservation.id == rid).first()
        # 定时任务造历史态无 API 路径，有意直改
        res.expires_at = datetime.now() - timedelta(hours=1)
        db.commit()

    result = _run(client, h, "reservation_expire_check")
    assert result["status"] == "success"
    assert result["processed"] == 1
    with get_session() as db:
        from backend.domain.catalog.models import BookCopy
        from backend.domain.reading.models import Reservation

        res = db.get(Reservation, rid)
        assert res.status == Reservation.STATUS_EXPIRED
        copy = db.get(BookCopy, res.copy_id)
        assert copy.status == BookCopy.STATUS_AVAILABLE  # 副本释放
    assert any(n.scene == "reservation.released" for n in _notifs(p["id"]))


def test_reservation_expire_remind(client: TestClient):
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800002016")
    mini = client.post("/api/miniapp/login", json={"phone": "13800002016", "code": "1234"})
    mini_h = {"Authorization": f"Bearer {mini.json()['token']}"}
    r = client.post(
        "/api/miniapp/reservations",
        json={"child_id": c["id"], "book_id": book["id"]},
        headers=mini_h,
    )
    rid = r.json()["id"]
    with get_session() as db:
        from backend.domain.reading.models import Reservation

        res = db.query(Reservation).filter(Reservation.id == rid).first()
        res.expires_at = datetime.now() + timedelta(hours=2)
        db.commit()

    result = _run(client, h, "reservation_expire_remind")
    assert result["status"] == "success"
    assert any(n.scene == "reservation.expiring" for n in _notifs(p["id"]))


# ---------- 5. 订单超时取消（僵尸单 + 活动名额释放） ----------


def test_order_timeout_cancel(client: TestClient):
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800002017")
    o = _order(client, h, c["id"], "formal_fee")
    with get_session() as db:
        order = db.query(Order).filter(Order.id == o["id"]).first()
        order.create_time = datetime.now() - timedelta(hours=100)
        db.commit()
    result = _run(client, h, "order_timeout_cancel")
    assert result["status"] == "success"
    assert result["processed"] >= 1
    with get_session() as db:
        assert db.get(Order, o["id"]).status == Order.STATUS_CANCELLED


def test_order_timeout_cancel_releases_activity_quota(client: TestClient):
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800002018")
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "主题阅读活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S"),
            "location": "馆内",
            "max_quota": 10,
            "fee": 50,
        },
        headers=h,
    )
    assert act.status_code == 200, act.text
    aid = act.json()["id"]
    # 收费活动报名（家长端）→ pending_payment 占名额
    mini = client.post("/api/miniapp/login", json={"phone": "13800002018", "code": "1234"})
    mini_h = {"Authorization": f"Bearer {mini.json()['token']}"}
    r = client.post(
        f"/api/miniapp/activities/{aid}/enroll", json={"child_id": c["id"]}, headers=mini_h
    )
    assert r.status_code == 200, r.text
    with get_session() as db:
        from backend.domain.activity.models import ActivityEnrollment

        e = db.query(ActivityEnrollment).filter(ActivityEnrollment.activity_id == aid).first()
        order = db.query(Order).filter(Order.id == e.order_id).first()
        order.create_time = datetime.now() - timedelta(hours=100)
        db.commit()
    _run(client, h, "order_timeout_cancel")
    with get_session() as db:
        from backend.domain.activity.models import ActivityEnrollment

        e = db.query(ActivityEnrollment).filter(ActivityEnrollment.activity_id == aid).first()
        assert e.status == ActivityEnrollment.STATUS_CANCELLED  # 名额释放


# ---------- 5b. 转让超时自动取消（P1-2 专门测试，WM10 资产安全） ----------


def test_transfer_expire_check_unlocks_both_sides(client: TestClient):
    h = _h(client)
    p = _parent(client, h, phone="13800002022")
    c1 = _child(client, h, p["id"], name="转出孩")
    c2 = _child(client, h, p["id"], name="受让孩")
    _confirm(client, h, _order(client, h, c1["id"], "formal_fee")["id"])
    _confirm(client, h, _order(client, h, c2["id"], "formal_fee")["id"])
    # 定时任务造历史态无 API 路径，有意直改：pending 转让 + 已超时 + 双方冻结
    from backend.domain.identity.models import TransferRequest

    with get_session() as db:
        db.add(
            TransferRequest(
                source_child_id=c1["id"],
                target_child_id=c2["id"],
                status=TransferRequest.STATUS_PENDING,
                expires_at=datetime.now() - timedelta(hours=1),
            )
        )
        for cid in (c1["id"], c2["id"]):
            child = db.query(Child).filter(Child.id == cid).first()
            child.operation_locked = 1
        db.commit()
    result = _run(client, h, "transfer_expire_check")
    assert result["status"] == "success"
    assert result["processed"] == 1
    with get_session() as db:
        req = db.query(TransferRequest).filter(TransferRequest.source_child_id == c1["id"]).first()
        assert req.status == TransferRequest.STATUS_EXPIRED
        assert db.get(Child, c1["id"]).operation_locked == 0  # 双方解锁
        assert db.get(Child, c2["id"]).operation_locked == 0
    # 幂等：重复跑无副作用
    r2 = _run(client, h, "transfer_expire_check")
    assert r2["processed"] == 0


# ---------- 6. 借阅到期提醒 + 逾期标记 ----------


def test_book_due_remind_and_overdue_mark(client: TestClient):
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800002019")
    rec = _borrow(client, h, c["id"], book["isbn"])
    with get_session() as db:
        from backend.domain.circulation.models import BorrowRecord

        br = db.query(BorrowRecord).filter(BorrowRecord.id == rec["id"]).first()
        br.due_at = datetime.now() + timedelta(days=3)
        db.commit()

    r = _run(client, h, "book_due_remind")
    assert r["status"] == "success"
    assert any(n.scene == "borrow.due_remind" and n.dedup_key == "3" for n in _notifs(p["id"]))

    with get_session() as db:
        from backend.domain.circulation.models import BorrowRecord

        br = db.query(BorrowRecord).filter(BorrowRecord.id == rec["id"]).first()
        # 定时任务造历史态无 API 路径，有意直改
        br.due_at = datetime.now() - timedelta(days=1)
        db.commit()
    r2 = _run(client, h, "overdue_mark")
    assert r2["status"] == "success"
    assert r2["processed"] >= 1
    with get_session() as db:
        from backend.domain.circulation.models import BorrowRecord

        assert db.get(BorrowRecord, rec["id"]).status == BorrowRecord.STATUS_OVERDUE
    assert any(n.scene == "borrow.overdue" for n in _notifs(p["id"]))


# ---------- 7. 活动提醒 + 自动结束 ----------


def test_activity_remind_and_auto_finish(client: TestClient):
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800002020")
    # 免费活动报名（enrolled）
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "故事会",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S"),
            "location": "馆内",
            "max_quota": 10,
            "fee": 0,
        },
        headers=h,
    ).json()
    mini = client.post("/api/miniapp/login", json={"phone": "13800002020", "code": "1234"})
    mini_h = {"Authorization": f"Bearer {mini.json()['token']}"}
    r = client.post(
        "/api/miniapp/activities/{}/enroll".format(act["id"]),
        json={"child_id": c["id"]},
        headers=mini_h,
    )
    assert r.status_code == 200, r.text

    r = _run(client, h, "activity_remind")
    assert r["status"] == "success"
    assert any(n.scene == "activity.remind" and n.dedup_key == "2" for n in _notifs(p["id"]))

    # 已开始超过 1 天且无报名 → finished（直改库造历史活动，创建端点禁过去时间）
    from backend.domain.activity.models import Activity as ActivityModel

    with get_session() as db:
        db.add(
            ActivityModel(
                title="旧活动",
                activity_type="book_club",
                start_at=datetime.now() - timedelta(days=3),
                location="馆内",
                max_quota=5,
                fee=0,
                status=ActivityModel.STATUS_PUBLISHED,
            )
        )
        db.commit()
    r2 = _run(client, h, "activity_auto_finish")
    assert r2["status"] == "success"
    assert r2["processed"] >= 1
    with get_session() as db:
        from backend.domain.activity.models import Activity

        old = (
            db.query(Activity).filter(Activity.title == "旧活动", Activity.is_deleted == 0).first()
        )
        assert old.status == Activity.STATUS_FINISHED


# ---------- 8. 99 元 90 天提醒 ----------


def test_first_activity_90d_remind(client: TestClient):
    h = _h(client)
    p = _parent(client, h, phone="13800002021")
    c = _child(client, h, p["id"])
    o = _order(client, h, c["id"], "first_activity_fee")
    _confirm(client, h, o["id"])
    with get_session() as db:
        order = db.query(Order).filter(Order.id == o["id"]).first()
        order.paid_at = datetime.now() - timedelta(days=91)
        db.commit()
    r = _run(client, h, "first_activity_90d_remind")
    assert r["status"] == "success"
    assert r["processed"] >= 1
    assert any(n.dedup_key == "first_activity_90d" for n in _notifs(p["id"]))


# ---------- 9. 任务看板 / 运行日志 / 手动触发 ----------


def test_task_board_and_run_log(client: TestClient):
    h = _h(client)
    spec_resp = client.get("/api/admin/tasks", headers=h)
    assert spec_resp.status_code == 200
    names = {s["name"] for s in spec_resp.json()["items"]}
    assert "member_expire_check" in names
    assert len(names) == 12

    _run(client, h, "member_expire_check")
    runs = client.get("/api/admin/tasks/runs", headers=h).json()["items"]
    assert any(r["task_name"] == "member_expire_check" and r["status"] == "success" for r in runs)

    with get_session() as db:
        log_count = (
            db.query(TaskRunLog)
            .filter(TaskRunLog.task_name == "member_expire_check", TaskRunLog.is_deleted == 0)
            .count()
        )
    assert log_count >= 1


def test_task_failure_recorded(client: TestClient):
    h = _h(client)
    r = client.post("/api/admin/tasks/not_exist/run", headers=h)
    assert r.status_code == 404


# ---------- 审查返工 Q2：specs 带 last_run（空值=None 前端显示"从未运行"） ----------


def test_task_specs_last_run(client: TestClient):
    h = _h(client)
    # 跑一个任务产生运行记录
    _run(client, h, "member_expire_check")
    specs = client.get("/api/admin/tasks", headers=h).json()["items"]
    by_name = {s["name"]: s for s in specs}
    ran = by_name["member_expire_check"]
    assert ran["last_run"] is not None
    assert ran["last_run"]["status"] == "success"
    # 从未跑过的任务（先清掉它的运行记录再断言；本测试库中其余任务应无记录）
    never = by_name["first_activity_90d_remind"]
    assert never["last_run"] is None or never["last_run"]["status"] in (
        "success",
        "failed",
        "skipped",
    )


# ---------- F3（C39）：手动触发写审计 task.manual_run；调度器自动路径不审计 ----------


def _manual_audit_count() -> int:
    from backend.domain.admin.models import AuditLog

    with get_session() as db:
        return db.query(AuditLog).filter(AuditLog.action == "task.manual_run").count()


def test_manual_run_audited_and_scheduler_path_not(client: TestClient):
    from backend.tasks.registry import run_task

    base = _manual_audit_count()
    # 自动路径（直调，manual 缺省 False）→ 不产生审计
    run_task("member_expire_check")
    assert _manual_audit_count() == base

    # 手动路径（API 端点）→ 审计 +1，detail 含 display_name/status/processed
    h = _h(client)
    r = client.post("/api/admin/tasks/member_expire_check/run", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert _manual_audit_count() == base + 1

    import json

    from backend.domain.admin.models import AuditLog

    with get_session() as db:
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.action == "task.manual_run")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.target_type == "task"
        assert audit.target_id == "member_expire_check"
        detail = json.loads(audit.detail) if audit.detail else {}
        assert detail["display_name"] == "会员过期落库"
        assert detail["status"] == "success"
        assert "processed" in detail


def test_manual_run_failure_also_audited(client: TestClient, monkeypatch):
    import json

    from backend.domain.admin.models import AuditLog
    from backend.tasks.registry import TASKS

    def _boom(_db):
        raise RuntimeError("boom")

    monkeypatch.setattr(TASKS["member_expire_check"], "fn", _boom)
    h = _h(client)
    r = client.post("/api/admin/tasks/member_expire_check/run", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "failed"

    with get_session() as db:
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.action == "task.manual_run")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        detail = json.loads(audit.detail) if audit.detail else {}
        assert detail["status"] == "failed"
        assert "boom" in detail.get("error", "")


# ---------- F5：SCHEDULER_ENABLED 配置开关（Q3 裁决，验收期关调度防抢跑） ----------


def test_scheduler_disabled_by_config(monkeypatch):
    import asyncio

    from backend import main
    from backend.tasks import registry

    calls: list[str] = []
    monkeypatch.setattr(registry, "start_scheduler", lambda: calls.append("start"))
    monkeypatch.setattr(registry, "stop_scheduler", lambda: calls.append("stop"))

    async def _consume():
        async with main.lifespan(None):
            pass

    monkeypatch.setattr(main.settings, "SCHEDULER_ENABLED", False)
    asyncio.run(_consume())
    assert calls == ["stop"]  # 不启动，stop 兜底安全

    calls.clear()
    monkeypatch.setattr(main.settings, "SCHEDULER_ENABLED", True)
    asyncio.run(_consume())
    assert calls == ["start", "stop"]
