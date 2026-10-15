# tests/unit/test_g5_reservation_copy_exception_20261014.py — 第六十批：G5 预约期副本转维护 +
# 连带发现的三类待办显示态/500（G14）回归
"""PRD §5.5：「**预约期间书出意外**（例如被标记维修）：系统自动尝试换同一种书的其他在馆副本；
没有其他副本了，预约转『异常』状态并通知家长和馆员，名额立即释放。」

本文件钉住两件事：

**一、G5（本轮功能新增）**——`Reservation.STATUS_EXCEPTION` 此前**只有常量、没有写入点**，
也没有"自动改锁同书其他在馆副本"的逻辑（`reservation.feature` 那条场景挂着 `@draft`）。
两条可达写入链（`catalog.update_copy_status` / `catalog.report_copy_damage`）现在都过
`reading.reservation_ops.handle_copy_unavailable` 漏斗。

**二、连带发现（G14）**——在同一个 resolver 里改 G5 时发现的既有缺陷，两类症状同根：
`AdminTodoService.resolve_many` 原先**只按 `ref_type` 分派**，2026-10-12 拍板新增的三个场景
撞进了别人的分支——报损赔偿/资金异常（`ref_type=child`）落进「入会跟进」推导（有效会员的孩子
→ 待办立刻"已审结"），活动订单作废（`ref_type=activity`）落进「活动批量退款」推导（无退款单
→ 立刻"已审结"）；更硬的症状是资金异常的 `ref_id` 是**订单号 `DMK…`**，流经 `_child_states`
的裸 `int()` → ValueError → **超管待办墙整页 500**。修复＝场景一级判定前置 + 非数字 id 跳过。
"""

from fastapi.testclient import TestClient

from tests.unit.helpers import force_book_on


def _admin_h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _member_child(client, h, phone, name="小明") -> tuple[dict, dict]:
    """建家长 + 孩子，缴押金、买观察期年费（=有效会员），返回 (child, mini_headers)。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    dep = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    rc = client.post(
        f"/api/admin/orders/{dep['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    assert rc.status_code == 200, rc.text
    for order_type in ("observation_fee", "formal_fee"):
        o = client.post(
            "/api/admin/orders", json={"child_id": c["id"], "order_type": order_type}, headers=h
        ).json()
        rc = client.post(
            f"/api/admin/orders/{o['id']}/confirm-payment",
            json={"pay_method": "scan"},
            headers=h,
        )
        assert rc.status_code == 200, rc.text
    login = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    return c, {"Authorization": f"Bearer {login['token']}"}


def _book(client, h, title: str, copies: int) -> tuple[int, list[int]]:
    """建一本**上架**书，返回 (book_id, [副本id…])（下架书禁预约，P6）。"""
    created = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 100, "copy_count": copies},
        headers=h,
    )
    assert created.status_code == 200, created.text
    book_id = created.json()["id"]
    force_book_on(client, h, book_id)  # 幂等：已是上架态则空转
    rows = client.get(f"/api/admin/books/{book_id}/copies", headers=h).json()
    return book_id, [r["id"] for r in rows]


def _reserve(client, mini, child_id: int, book_id: int) -> dict:
    r = client.post(
        "/api/miniapp/reservations", json={"child_id": child_id, "book_id": book_id}, headers=mini
    )
    assert r.status_code == 200, r.text
    return r.json()


def _set_copy_status(client, h, copy_id: int, status: str):
    return client.put(
        f"/api/admin/copies/{copy_id}/status",
        json={"status": status, "reason": "副本损坏送修"},
        headers=h,
    )


def _report_damage(client, h, copy_id: int, kind: str):
    return client.post(
        f"/api/admin/copies/{copy_id}/report-damage",
        json={"kind": kind, "reason": "家长还书时发现封面撕裂"},
        headers=h,
    )


def _reservation_row(res_id: int):
    from backend.database import get_session
    from backend.domain.reading.models import Reservation

    with get_session() as db:
        return db.query(Reservation).filter(Reservation.id == res_id).populate_existing().first()


def _copy_row(copy_id: int):
    from backend.database import get_session
    from backend.domain.catalog.models import BookCopy

    with get_session() as db:
        return db.query(BookCopy).filter(BookCopy.id == copy_id).populate_existing().first()


def _quota(client, h, child_id: int) -> dict:
    return client.get(f"/api/admin/circulation/children/{child_id}/card", headers=h).json()


def _notifications(scene: str, *, parent_side: bool) -> list:
    """按 scene 取通知（parent_side=True 读家长站内消息，否则读管理端待办）。"""
    from backend.database import get_session

    if parent_side:
        from backend.common.notification_models import Notification as Model
    else:
        from backend.common.admin_notification_models import AdminNotification as Model

    with get_session() as db:
        return db.query(Model).filter(Model.scene == scene, Model.is_deleted == 0).all()


# ---------- 一、G5：换锁到同书其他在馆副本 ----------


def test_copy_maintenance_switches_reservation_to_another_copy(client: TestClient):
    """有第二册在馆 → 预约**改锁**它，预约仍 active、到期时间不变、新副本转 reserved。"""
    h = _admin_h(client)
    child, mini = _member_child(client, h, "13800009101")
    book_id, copies = _book(client, h, "换锁样例", copies=2)
    res = _reserve(client, mini, child["id"], book_id)
    assert res["id"]

    before = _reservation_row(res["id"])
    assert before.copy_id == copies[0] and _copy_row(copies[0]).status == "reserved"

    r = _set_copy_status(client, h, copies[0], "maintenance")
    assert r.status_code == 200, r.text

    after = _reservation_row(res["id"])
    assert after.status == "active", f"有在馆副本可换，预约必须继续有效：{after.status}"
    assert after.copy_id == copies[1], f"应改锁第二册，实 {after.copy_id} ≠ {copies[1]}"
    assert after.expires_at == before.expires_at, "改锁不该动到期时间"
    assert _copy_row(copies[1]).status == "reserved", "新副本必须被锁成 reserved"
    assert _copy_row(copies[0]).status == "maintenance"
    # 额度不变（仍占 1 个预约名额）
    assert _quota(client, h, child["id"])["available_quota"] == 29


def test_no_alternative_copy_turns_exception_and_notifies_both_sides(client: TestClient):
    """只有一册且转维护 → 预约转 exception + 家长站内消息 + 馆员待办 + **名额立即释放**。"""
    from backend.common.admin_notification_models import AdminNotification
    from backend.common.notifications import SCENE_RESERVATION_EXCEPTION

    h = _admin_h(client)
    child, mini = _member_child(client, h, "13800009102")
    book_id, copies = _book(client, h, "单册样例", copies=1)
    res = _reserve(client, mini, child["id"], book_id)
    assert _quota(client, h, child["id"])["available_quota"] == 29, "预约先占 1 个额度"

    r = _set_copy_status(client, h, copies[0], "maintenance")
    assert r.status_code == 200, r.text

    assert _reservation_row(res["id"]).status == "exception", "无副本可换 → 转异常态"
    parents = _notifications(SCENE_RESERVATION_EXCEPTION, parent_side=True)
    assert len(parents) == 1, "家长必须收到「预约异常」站内消息"
    assert "单册样例" in parents[0].content
    admins = _notifications(AdminNotification.SCENE_RESERVATION_EXCEPTION, parent_side=False)
    assert len(admins) == 1, "馆员必须收到「预约异常待跟进」待办"
    assert admins[0].ref_type == "reservation" and admins[0].ref_id == str(res["id"])
    # PRD §5.4 公式：可借 = 上限 − 在借(含逾期) − 预约(**active**)。异常态不再是 active → 立即释放
    assert _quota(client, h, child["id"])["available_quota"] == 30, "异常态必须立即释放名额"


def test_report_damage_path_also_handles_reservation(client: TestClient):
    """第二条链（报损·遗失）同样过漏斗——damage → maintenance、lost → lost 都转异常。"""
    from backend.common.admin_notification_models import AdminNotification

    h = _admin_h(client)
    child, mini = _member_child(client, h, "13800009103")
    b1, c1 = _book(client, h, "报损书甲", copies=1)
    r1 = _reserve(client, mini, child["id"], b1)
    assert _report_damage(client, h, c1[0], "damage").status_code == 200
    assert _reservation_row(r1["id"]).status == "exception"
    assert _copy_row(c1[0]).status == "maintenance"

    b2, c2 = _book(client, h, "遗失书乙", copies=1)
    r2 = _reserve(client, mini, child["id"], b2)
    assert _report_damage(client, h, c2[0], "lost").status_code == 200
    assert _reservation_row(r2["id"]).status == "exception"
    assert _copy_row(c2[0]).status == "lost"

    admins = _notifications(AdminNotification.SCENE_RESERVATION_EXCEPTION, parent_side=False)
    assert {n.ref_id for n in admins} == {str(r1["id"]), str(r2["id"])}, "两条链各出一条待办"


def test_copy_unavailable_without_reservation_is_noop(client: TestClient):
    """无活跃预约时改状态 → 不凭空造单、不报错（防"过度触发"）。"""
    from backend.database import get_session
    from backend.domain.reading.models import Reservation

    h = _admin_h(client)
    _book_id, copies = _book(client, h, "无人预约的书", copies=2)
    with get_session() as db:
        before = db.query(Reservation).count()
    assert _set_copy_status(client, h, copies[0], "maintenance").status_code == 200
    with get_session() as db:
        assert db.query(Reservation).count() == before, "无预约就不该有预约行变动"
    assert _copy_row(copies[1]).status == "available", "第二册不该被无故锁定"


# ---------- 二、G5 的管理端待办显示态与计数 ----------


def test_exception_todo_pending_and_counted(client: TestClient):
    """馆员待办显示态=待处理、计入 `reservation_exception` 与 `admin_total`，与收件箱同口径。"""
    h = _admin_h(client)
    child, mini = _member_child(client, h, "13800009104")
    book_id, copies = _book(client, h, "待办计数样例", copies=1)
    res = _reserve(client, mini, child["id"], book_id)
    base = client.get("/api/admin/todo-counts", headers=h).json()

    assert _set_copy_status(client, h, copies[0], "maintenance").status_code == 200

    counts = client.get("/api/admin/todo-counts", headers=h).json()
    assert counts["reservation_exception"] == base["reservation_exception"] + 1, counts
    assert counts["admin_total"] == base["admin_total"] + 1, counts
    inbox = client.get(
        "/api/admin/admin-notifications", params={"status_filter": "pending"}, headers=h
    ).json()
    mine = [i for i in inbox["items"] if i["ref_id"] == str(res["id"])]
    assert len(mine) == 1, f"待办墙应收录这条：{inbox['items']}"
    assert mine[0]["effective_status"] == "pending"
    assert counts["admin_total"] == inbox["pending_count"], "两端口径必须一致（同一 resolver）"


def test_handled_exception_todo_leaves_pending(client: TestClient):
    """馆员线下跟进后手动标记 → 离开待处理、计数回落（人工销项路径可用）。"""
    h = _admin_h(client)
    child, mini = _member_child(client, h, "13800009105")
    book_id, copies = _book(client, h, "销项样例", copies=1)
    _reserve(client, mini, child["id"], book_id)
    assert _set_copy_status(client, h, copies[0], "maintenance").status_code == 200

    inbox = client.get(
        "/api/admin/admin-notifications", params={"status_filter": "pending"}, headers=h
    ).json()
    target = [i for i in inbox["items"] if "预约异常" in i["title"]]
    assert len(target) == 1, inbox["items"]
    rc = client.post(
        f"/api/admin/admin-notifications/{target[0]['id']}/handle",
        json={"reason": "已电话联系家长改约"},
        headers=h,
    )
    assert rc.status_code == 200, rc.text
    after = client.get("/api/admin/todo-counts", headers=h).json()
    assert after["reservation_exception"] == 0, after


# ---------- 三、连带发现 G14：三类"人工销项"待办 ----------


def _seed_admin_todo(scene: str, ref_type: str, ref_id: str) -> None:
    from backend.common.admin_notifications import AdminNotifyService
    from backend.database import get_session

    with get_session() as db:
        AdminNotifyService(db).send(
            scene=scene, title="t", content="c", ref_type=ref_type, ref_id=ref_id
        )
        db.commit()


def test_payment_anomaly_todo_ref_id_is_order_no_and_wall_must_not_500(client: TestClient):
    """G14 硬症状：资金异常待办挂的是**订单号**（非数字）——修复前 `int()` 会把待办墙打成 500。

    修复前实测（去修复即红）：`GET /api/admin/todo-counts` 与 `/api/admin/admin-notifications`
    双双 500（`ValueError: invalid literal for int()`），运营打开待办页就是白屏。
    """
    from backend.database import get_session
    from backend.domain.identity.payment_service import PaymentService

    h = _admin_h(client)
    child, _mini = _member_child(client, h, "13800009106")
    res = client.post(
        "/api/admin/orders",
        json={
            "child_id": child["id"],
            "order_type": "custom",
            "amount": "10.00",
            "remark": "定制借阅卡工本费",  # 自定义单必须填类型说明（422 否则）
        },
        headers=h,
    )
    assert res.status_code == 200, res.text
    order = res.json()
    assert order["order_no"].startswith("DMK"), order["order_no"]
    with get_session() as db:
        PaymentService(db)._audit_anomaly("测试资金异常", target_id=order["order_no"], reason="G14")
        db.commit()

    counts = client.get("/api/admin/todo-counts", headers=h)
    assert counts.status_code == 200, f"待办计数不该 500：{counts.text[:300]}"
    inbox = client.get("/api/admin/admin-notifications", headers=h)
    assert inbox.status_code == 200, f"待办墙不该 500：{inbox.text[:300]}"
    assert counts.json()["payment_anomaly"] >= 1, counts.json()
    rows = [i for i in inbox.json()["items"] if i["scene"] == "admin.payment_anomaly"]
    assert rows and rows[0]["effective_status"] == "pending", rows


def test_manual_pending_scenes_are_pending_not_auto_closed(client: TestClient):
    """G14 显示态症状：三类待办**生成即待处理**，不再被别人的分支"秒审结"。

    修复前：① 报损赔偿（ref_type=child，孩子是有效会员）→ 显示"已审结"；
    ② 资金异常（ref_type=child，ref_id 是订单号）→ 同上；③ 活动订单作废（ref_type=activity，
    活动已取消无退款单）→ 显示"已审结"。三条待办在待办墙上**全都看不见**。
    """
    from backend.common.admin_notification_models import AdminNotification as AN

    h = _admin_h(client)
    child, _mini = _member_child(client, h, "13800009107")
    cases = [
        (AN.SCENE_COPY_DAMAGE_COMPENSATION, AN.REF_CHILD, str(child["id"])),
        (AN.SCENE_PAYMENT_ANOMALY, AN.REF_CHILD, "DMK20260101120000ABCDEF"),
        (AN.SCENE_ACTIVITY_ORDER_VOIDED, AN.REF_ACTIVITY, "1"),
    ]
    for scene, ref_type, ref_id in cases:
        _seed_admin_todo(scene, ref_type, ref_id)

    inbox = client.get(
        "/api/admin/admin-notifications", params={"status_filter": "pending"}, headers=h
    ).json()
    pending_scenes = {i["scene"] for i in inbox["items"]}
    for scene, _t, _i in cases:
        assert scene in pending_scenes, f"{scene} 应显示待处理，实得 {pending_scenes}"
    counts = client.get("/api/admin/todo-counts", headers=h).json()
    assert counts["admin_total"] == inbox["pending_count"], (
        "admin_total 必须与收件箱 pending_count 同口径"
    )
