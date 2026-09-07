# tests/unit/test_p0_fix24.py — 插修 14（25 号任务包 R7-R10）
"""活动域三轮收口红测试。R7：管理端造单链绕过 T6 发送点（enroll() 内发）——
造单+确认全链通通知 0 条。R8：管理端造活动单漏抄 dup 检查（双倍收费风险）。
R9：类型下拉 allowClear 当"全部"（A4 第三犯）。R10：admin_total 口径统一+
activity_enrollment 去处理路由缺失（计数同源第 7 案）。"""

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _mk_activity
from tests.unit.test_wm10_concurrency import _family, _h
from tests.unit.test_wm13_admin_inbox import _db

ADMIN_ORDER_URL = "/api/admin/orders"


# ---------- R7：管理端造单链发报名待确认通知（+免费单 422 防呆） ----------


def test_r7_admin_order_sends_enroll_manual(client: TestClient):
    """修复前：管理端造付费单不发管理待办（T6 发送点在家长 enroll 内）= RED。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=60, hours_later=72, title="造单通知活动")
    p, c, mini = _family(client, h, "13900043001", "造单通知孩")

    base = client.get("/api/admin/todo-counts", headers=h).json()["activity_enroll_pending"]

    r = client.post(
        ADMIN_ORDER_URL,
        json={"order_type": "activity_fee", "child_id": c["id"], "activity_id": act["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    order_id = r.json()["id"]

    rc = client.get("/api/admin/todo-counts", headers=h).json()
    assert rc["activity_enroll_pending"] == base + 1, f"造单应发待确认通知，实 {rc} = RED"

    # 收件箱出现通知（content 含孩子/活动）
    rl = client.get(
        "/api/admin/admin-notifications",
        params={"scene": "admin.activity_enroll_manual"},
        headers=h,
    )
    assert rl.status_code == 200, rl.text
    items = rl.json()["items"]
    assert any("造单通知孩" in i["content"] and "造单通知活动" in i["content"] for i in items), (
        f"收件箱应含通知 = RED：{[i['content'] for i in items][:2]}"
    )

    # 确认收款 → mark_handled + 计数归零
    rcp = client.post(
        f"/api/admin/orders/{order_id}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert rcp.status_code == 200, rcp.text
    rc2 = client.get("/api/admin/todo-counts", headers=h).json()
    assert rc2["activity_enroll_pending"] == base, f"确认后应归零，实 {rc2} = RED"


def test_r7_admin_free_activity_order_422(client: TestClient):
    """免费活动管理端造单 → 422（当前 200=RED；免费无需收款单，防呆硬拦）。"""
    h = _h(client)
    act_free = _mk_activity(client, h, quota=5, fee=0, hours_later=72, title="免费防呆活动")
    p, c, mini = _family(client, h, "13900043002", "免费防呆孩")
    r = client.post(
        ADMIN_ORDER_URL,
        json={"order_type": "activity_fee", "child_id": c["id"], "activity_id": act_free["id"]},
        headers=h,
    )
    assert r.status_code == 422, f"免费活动造单应 422，实 {r.status_code} = RED"


# ---------- R8：管理端造活动单防重复报名（与 R7 同刀实施，测试后补锁定） ----------


def test_r8_dup_enrollment_rejects_422(client: TestClient):
    """已报名孩（活跃态）管理端再造同活动单 → 422（双倍收费拦截）。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=60, hours_later=72, title="防重复活动")
    p, c, mini = _family(client, h, "13900043003", "防重复孩")
    # 家长端报名（占位）
    r1 = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    )
    assert r1.status_code == 200, r1.text
    # 管理端再造单 → 422
    r2 = client.post(
        ADMIN_ORDER_URL,
        json={"order_type": "activity_fee", "child_id": c["id"], "activity_id": act["id"]},
        headers=h,
    )
    assert r2.status_code == 422, f"已报名孩造单应 422，实 {r2.status_code} = FAIL"


def test_r8_refunded_child_can_reorder(client: TestClient):
    """refunded 终态孩重造单 → 200（S4 口径：退款后可重报=可重造单）。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=60, hours_later=72, title="退款后重造活动")
    p, c, mini = _family(client, h, "13900043004", "退款重造孩")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    client.post(
        f"/api/miniapp/enrollments/{e['enrollment']['id']}/refund-apply",
        json={"child_id": c["id"]},
        headers=mini,
    )
    # 超管审核通过 + execute 成功（refunded 终态）
    from backend.domain.identity.models import RefundRequest

    ra = client.post(
        f"/api/admin/activity-refunds/{e['enrollment']['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert ra.status_code == 200, ra.text
    with _db() as db:
        rr = (
            db.query(RefundRequest)
            .filter(RefundRequest.order_id == e["order_id"], RefundRequest.is_deleted == 0)
            .order_by(RefundRequest.id.desc())
            .first()
        )
        rid = rr.id
    re_ = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "线下打款"},
        headers=h,
    )
    assert re_.status_code == 200, re_.text
    # 终态后重造单 → 200
    r2 = client.post(
        ADMIN_ORDER_URL,
        json={"order_type": "activity_fee", "child_id": c["id"], "activity_id": act["id"]},
        headers=h,
    )
    assert r2.status_code == 200, f"refunded 孩重造单应 200，实 {r2.status_code} {r2.text[:120]}"


# ---------- R10：admin_total 口径统一（计数同源第 7 案，用户裁定） ----------


def test_r10_admin_total_includes_enroll_manual(client: TestClient):
    """修复前：admin_total 排除活动报名待确认（tab 数字含、徽标不含=口径分叉）= RED。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=60, hours_later=72, title="口径统一活动")
    p, c, mini = _family(client, h, "13900043005", "口径孩")

    base_total = client.get("/api/admin/todo-counts", headers=h).json()["admin_total"]

    r = client.post(
        ADMIN_ORDER_URL,
        json={"order_type": "activity_fee", "child_id": c["id"], "activity_id": act["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text

    data = client.get("/api/admin/todo-counts", headers=h).json()
    # admin_total 应含活动报名待确认（运营视角一个数=全部要干的活）
    assert data["admin_total"] == base_total + 1, f"admin_total 应含活动报名待确认，实 {data} = RED"
    # 与收件箱 pending_count 严格一致（口径声明变事实）
    rl = client.get(
        "/api/admin/admin-notifications", params={"status_filter": "pending"}, headers=h
    )
    assert rl.status_code == 200, rl.text
    pending = rl.json().get("pending_count")
    if pending is not None:
        assert data["admin_total"] == pending, (
            f"admin_total {data['admin_total']} 应=tab pending_count {pending} = RED"
        )


# ---------- R10b（目视补刀）：订单 tab 高亮——订单行响应补 enrollment_id ----------


def test_r10b_order_list_carries_enrollment_id(client: TestClient):
    """修复前：订单行无 enrollment_id——通知跳订单 tab 无法高亮对应行 = RED。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=60, hours_later=72, title="高亮锚点活动")
    p, c, mini = _family(client, h, "13900043006", "高亮孩")
    r1 = client.post(
        ADMIN_ORDER_URL,
        json={"order_type": "activity_fee", "child_id": c["id"], "activity_id": act["id"]},
        headers=h,
    )
    assert r1.status_code == 200, r1.text
    activity_order_id = r1.json()["id"]
    # 对照：会员单（无 enrollment）
    r2 = client.post(
        ADMIN_ORDER_URL,
        json={"order_type": "observation_fee", "child_id": c["id"], "remark": "高亮对照"},
        headers=h,
    )
    assert r2.status_code == 200, r2.text

    rl = client.get("/api/admin/orders", params={"page_size": 50}, headers=h)
    assert rl.status_code == 200, rl.text
    rows = {x["id"]: x for x in rl.json()["items"]}
    assert rows[activity_order_id].get("enrollment_id") is not None, (
        f"活动单行应带 enrollment_id（高亮锚点），实 {rows[activity_order_id]} = RED"
    )
    assert rows[r2.json()["id"]].get("enrollment_id") is None, "非活动单应 None"
