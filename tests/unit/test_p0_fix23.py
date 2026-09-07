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


# ---------- T3：活动列表搜索 + 类型/状态筛选（列表三件套泛化纪律） ----------


def _mk_activity_typed(client, h, title, activity_type, fee=0, quota=5):
    from datetime import timedelta

    r = client.post(
        "/api/admin/activities",
        json={
            "title": title,
            "activity_type": activity_type,
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内一层",
            "max_quota": quota,
            "fee": fee,
            "description": "T3 筛选测试",
            "member_only": False,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_t3_activity_list_filters(client: TestClient):
    """修复前：keyword/activity_type 参数被忽略（全量返回）= RED。"""
    h = _h(client)
    a1 = _mk_activity_typed(client, h, "绘本共读读书会", "book_club")
    a2 = _mk_activity_typed(client, h, "亲子户外日", "parent_child")
    a3 = _mk_activity_typed(client, h, "读书会取消专场", "book_club")
    rc = client.post(f"/api/admin/activities/{a3['id']}/cancel", headers=h)
    assert rc.status_code == 200, rc.text

    titles_of = lambda r: [x["title"] for x in r.json()]
    # keyword 模糊
    r = client.get("/api/admin/activities", params={"keyword": "亲子"}, headers=h)
    assert r.status_code == 200, r.text
    ts = titles_of(r)
    assert "亲子户外日" in ts and "绘本共读读书会" not in ts, f"keyword 过滤失效：{ts} = RED"
    # 类型过滤
    r = client.get("/api/admin/activities", params={"activity_type": "parent_child"}, headers=h)
    ts = titles_of(r)
    assert "亲子户外日" in ts and "绘本共读读书会" not in ts, f"类型过滤失效：{ts} = RED"
    # 状态过滤（cancelled）
    r = client.get("/api/admin/activities", params={"status": "cancelled"}, headers=h)
    ts = titles_of(r)
    assert "读书会取消专场" in ts and "绘本共读读书会" not in ts, f"状态过滤失效：{ts} = RED"
    # 组合查询回归
    r = client.get(
        "/api/admin/activities",
        params={"keyword": "读书会", "activity_type": "book_club", "status": "published"},
        headers=h,
    )
    ts = titles_of(r)
    assert "绘本共读读书会" in ts and "亲子户外日" not in ts and "读书会取消专场" not in ts, (
        f"组合查询失效：{ts} = RED"
    )


# ---------- T6：活动报名（fee>0）进管理待办（新场景+单独计数口径） ----------


def test_t6_activity_enroll_admin_todo(client: TestClient):
    """修复前：报名只发家长通知，管理端零感知（todo-counts 无 activity_enroll_pending）= RED。"""
    from backend.common.admin_notification_models import AdminNotification

    h = _h(client)
    hs = _h(client, "staff01")
    act = _mk_activity(client, h, quota=5, fee=50, hours_later=72, title="待确认报名活动")
    act_free = _mk_activity(client, h, quota=5, fee=0, hours_later=72, title="免费对照活动")
    p, c, mini = _family(client, h, "13900042001", "待确认孩")

    base = client.get("/api/admin/todo-counts", headers=h).json().get("activity_enroll_pending", 0)

    # 免费报名 → 不发管理待办（无需运营动作）
    rf = client.post(
        f"/api/miniapp/activities/{act_free['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    )
    assert rf.status_code == 200, rf.text
    rc1 = client.get("/api/admin/todo-counts", headers=h).json()
    assert rc1["activity_enroll_pending"] == base, f"免费报名不应发管理待办，实 {rc1} = RED"

    # 付费报名 → 管理待办 +1（新字段）
    re_ = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    )
    assert re_.status_code == 200, re_.text
    eid = re_.json()["enrollment"]["id"]
    order_id = re_.json()["order_id"]
    rc2 = client.get("/api/admin/todo-counts", headers=h).json()
    assert rc2["activity_enroll_pending"] == base + 1, f"付费报名应进管理待办，实 {rc2} = RED"

    # staff（member.manage）可见
    rcs = client.get("/api/admin/todo-counts", headers=hs).json()
    assert rcs["activity_enroll_pending"] == base + 1, f"staff 应可见，实 {rcs} = RED"

    # 收件箱出现该场景通知（content 含孩子/活动/金额）
    rl = client.get(
        "/api/admin/admin-notifications",
        params={"scene": "admin.activity_enroll_manual"},
        headers=h,
    )
    assert rl.status_code == 200, rl.text
    items = rl.json()["items"]
    assert any("待确认孩" in i["content"] and "待确认报名活动" in i["content"] for i in items), (
        f"列表应含【活动报名待确认】通知 = RED：{[i['content'] for i in items][:2]}"
    )

    # 确认收款 → mark_handled + 计数归零
    rcp = client.post(
        f"/api/admin/orders/{order_id}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert rcp.status_code == 200, rcp.text
    rc3 = client.get("/api/admin/todo-counts", headers=h).json()
    assert rc3["activity_enroll_pending"] == base, f"确认后计数应归零，实 {rc3} = RED"
    with _db() as db:
        n = (
            db.query(AdminNotification)
            .filter(
                AdminNotification.scene == "admin.activity_enroll_manual",
                AdminNotification.ref_type == "activity_enrollment",
                AdminNotification.ref_id == str(eid),
                AdminNotification.is_deleted == 0,
            )
            .first()
        )
        assert n is not None, "管理待办通知应存在"
        assert n.handled_at is not None, "确认收款应回写 handled_at = RED"
