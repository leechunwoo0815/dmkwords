# tests/unit/test_p0_fix23.py — 插修 13（23 号任务包 T1-T6）
"""活动域二轮返工红测试。T1：计数同源第 6 案——家长 tab 未读胶囊绑 unreadCount
state，该 state 仅 box=parent 时由 loadParent 填充（默认 admin 视角 loadParent
不跑）→ 胶囊恒 0（S1 修了显示层没修数据源触发）。
修法：todo-counts 补 parent_unread 字段（全体家长通知未读 COUNT，无筛选全量）
→ 胶囊改用全局数（loadParent 拉到后本地覆盖，双源同值）——胶囊数与视角无关。
"""

from datetime import datetime

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _mk_activity
from tests.unit.test_wm10_concurrency import _family
from tests.unit.test_wm13_admin_inbox import _db, _h


def test_t1_todo_counts_parent_unread(client: TestClient):
    """修复前：todo-counts 无 parent_unread 字段 = RED。"""
    h = _h(client)
    with _db() as db:
        from backend.common.notification_models import Notification

        db.add(
            Notification(
                parent_id=1,
                scene="activity.enroll",
                title="报名成功",
                content="未读通知",
                category=Notification.CATEGORY_ACTIVITY,
                ref_type="activity",
                ref_id="9001",
                dedup_key="t1-9001",
            )
        )
        db.add(
            Notification(
                parent_id=1,
                scene="borrow.success",
                title="借书成功",
                content="已读通知",
                category=Notification.CATEGORY_BORROW,
                ref_type="borrow_record",
                ref_id="9002",
                dedup_key="t1-9002",
                read_at=datetime.now(),
            )
        )
        db.commit()

    rc = client.get("/api/admin/todo-counts", headers=h)
    assert rc.status_code == 200, rc.text
    data = rc.json()
    # 造数 1 未读 + 1 已读 → parent_unread 应 ≥1（当前无该字段 = KeyError/缺失 = RED）
    assert "parent_unread" in data, f"todo-counts 缺 parent_unread 字段：{data.keys()}"
    assert data["parent_unread"] >= 1, f"parent_unread 应 ≥1（1 未读），实 {data['parent_unread']}"


# ---------- T2：管理端活动单=报名代客创建（用户裁定推翻插修 11 R2 修法） ----------


def test_t2_admin_order_creates_enrollment(client: TestClient):
    """修复前：管理端造活动单不联动报名（enrollment 无记录）= RED。"""
    from backend.domain.activity.models import ActivityEnrollment

    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=80, hours_later=72, title="代客报名活动")
    p, c, mini = _family(client, h, "13900041001", "代客孩")

    # 管理端直接造活动单（不走小程序报名）
    r = client.post(
        "/api/admin/orders",
        json={"order_type": "activity_fee", "child_id": c["id"], "activity_id": act["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    order_id = r.json()["id"]

    # enrollment 同生（PENDING_PAYMENT + order 关联 + ticket）= 本断言
    with __import__("backend.database", fromlist=["get_session"]).get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.order_id == order_id, ActivityEnrollment.is_deleted == 0)
            .first()
        )
        assert e is not None, "管理端造单应同步生成报名（代客创建）= RED"
        assert e.status == ActivityEnrollment.STATUS_PENDING_PAYMENT, f"实 {e.status}"
        assert e.ticket_code, "应生成入场券码"

    # 占位生效：detail my_enrollment 非空（小程序见待收款报名）
    d = client.get(
        f"/api/miniapp/activities/{act['id']}", params={"child_id": c["id"]}, headers=mini
    )
    assert d.status_code == 200, d.text
    my = d.json().get("my_enrollment")
    assert my is not None, f"待收款报名应在详情可见，实 {my}"


def test_t2_confirm_promotes_enrollment(client: TestClient):
    """确认收款 → 报名转正 ENROLLED（R2 联动恢复全量适用）。"""
    from backend.domain.activity.models import ActivityEnrollment

    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=80, hours_later=72, title="代客转正活动")
    p, c, mini = _family(client, h, "13900041002", "转正孩")
    order_id = client.post(
        "/api/admin/orders",
        json={"order_type": "activity_fee", "child_id": c["id"], "activity_id": act["id"]},
        headers=h,
    ).json()["id"]

    rc = client.post(
        f"/api/admin/orders/{order_id}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert rc.status_code == 200, rc.text
    with __import__("backend.database", fromlist=["get_session"]).get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.order_id == order_id, ActivityEnrollment.is_deleted == 0)
            .first()
        )
        assert e.status == ActivityEnrollment.STATUS_ENROLLED, f"确认后应 ENROLLED，实 {e.status}"


def test_t2_cancel_releases_quota(client: TestClient):
    """取消订单 → 报名联动取消 + 名额回补（手动 cancel 链当前不联动 = RED）。"""
    from backend.domain.activity.models import Activity, ActivityEnrollment

    h = _h(client)
    act = _mk_activity(client, h, quota=1, fee=80, hours_later=72, title="取消回补活动")
    p, c, mini = _family(client, h, "13900041003", "取消孩")
    order_id = client.post(
        "/api/admin/orders",
        json={"order_type": "activity_fee", "child_id": c["id"], "activity_id": act["id"]},
        headers=h,
    ).json()["id"]

    rc = client.post(f"/api/admin/orders/{order_id}/cancel", headers=h)
    assert rc.status_code == 200, rc.text
    with __import__("backend.database", fromlist=["get_session"]).get_session() as db:
        e = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.order_id == order_id, ActivityEnrollment.is_deleted == 0)
            .first()
        )
        assert e is not None and e.status == ActivityEnrollment.STATUS_CANCELLED, (
            f"取消应联动报名 cancelled，实 {e.status if e else None} = RED"
        )
        a = db.query(Activity).filter(Activity.id == act["id"]).first()
        from backend.domain.activity.service import ActivityService

        assert ActivityService(db)._quota_used(a.id) == 0, "名额应回补"


def test_t2_full_quota_rejects_422(client: TestClient):
    """满员 → 管理端造单 422（名额校验前置，当前只查活动存在性 = RED）。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=1, fee=80, hours_later=72, title="满员校验活动")
    p1, c1, mini1 = _family(client, h, "13900041004", "满员甲")
    p2, c2, mini2 = _family(client, h, "13900041005", "满员乙")
    # 甲占满名额
    r1 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c1["id"]}, headers=mini1
    )
    assert r1.status_code == 200, r1.text
    # 乙造单应 422
    r2 = client.post(
        "/api/admin/orders",
        json={"order_type": "activity_fee", "child_id": c2["id"], "activity_id": act["id"]},
        headers=h,
    )
    assert r2.status_code == 422, f"满员造单应 422，实 {r2.status_code} {r2.text[:100]} = RED"
