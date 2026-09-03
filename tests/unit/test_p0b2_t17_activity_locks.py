# tests/unit/test_p0b2_t17_activity_locks.py — P0 第二批 T17（E-2+E-5+E-8）活动域行锁+冻结检查
"""并发红测试（线程模式，对齐 T3/T4 先例）：

- E-2 signin：查 enrollment 无锁——并发双扫同券双写签到字段。
  s1 锁 enrollment 改 checked_in（未提交）→ B 线程 signin：修复前无锁直接成功
  （双签到 RED）；修复后阻塞在锁 → s1 提交 → 锁定读 populate_existing 读到
  checked_in → ConflictError。
- E-5 cancel_activity：全程无锁——取消遍历期间并发 enroll 滞留活跃态不进批量退款。
  s1 锁 Activity 行（模拟取消进行中）→ B 线程 enroll：修复前报名成功（RED）；
  修复后阻塞 → s1 提交（cancelled）→ B 锁定读 → 422 活动已取消。
- E-8 enroll：冻结孩（operation_locked）报名 422。
"""

import threading
import time

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child


def _db():
    from backend.database import get_session

    return get_session()


def _enrolled_free(client, h, phone, name, title):
    c, m = _mk_child(client, h, phone, name)
    act = _mk_activity(client, h, quota=5, fee=0, title=title)
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
    ).json()
    assert e["enrollment"]["status"] == "enrolled"
    return act, e["enrollment"]["id"], e["enrollment"]["ticket_code"]


def test_signin_concurrent_same_ticket(client: TestClient, session_pair):
    h = _h(client)
    act, eid, ticket = _enrolled_free(client, h, "13981017001", "双扫孩", "双扫活动")

    from backend.domain.activity.models import ActivityEnrollment
    from backend.domain.activity.service import ActivityService

    s1, s2 = session_pair
    # s1 模拟先到者 A：锁 enrollment + 置 checked_in（未提交）
    locked = (
        s1.query(ActivityEnrollment).filter(ActivityEnrollment.id == eid).with_for_update().first()
    )
    assert locked is not None
    locked.status = ActivityEnrollment.STATUS_CHECKED_IN
    s1.flush()

    results = {}

    def b_signin():
        try:
            r = ActivityService(s2).signin(_admin_for(s2), ticket)
            s2.commit()
            results["ok"] = r
        except Exception as e:  # noqa: BLE001
            results["err"] = type(e).__name__

    from backend.domain.admin.models import AdminUser

    def _admin_for(db):
        return db.query(AdminUser).filter(AdminUser.username == "admin").first()

    t = threading.Thread(target=b_signin)
    t.start()
    time.sleep(1.0)  # 给 B 时间进锁（修复后应阻塞在 enrollment 锁）
    s1.commit()
    t.join(timeout=8)
    assert not t.is_alive(), "B 线程 8s 未结束（疑似死锁）"
    assert results.get("err") == "ConflictError", (
        f"并发双扫应 409 已签到，实 {results}（RED=双写签到字段）"
    )


def test_cancel_activity_serializes_enroll(client: TestClient, session_pair):
    h = _h(client)
    c_new, m_new = _mk_child(client, h, "13981017002", "滞留孩")
    act, eid, ticket = _enrolled_free(client, h, "13981017003", "取消伴孩", "取消串行活动")

    from backend.domain.activity.models import Activity
    from backend.domain.activity.service import ActivityService

    s1, s2 = session_pair
    # s1 模拟取消者：锁 Activity 行（持锁期间 B 不应能完成报名）
    locked = s1.query(Activity).filter(Activity.id == act["id"]).with_for_update().first()
    assert locked is not None

    results = {}

    def b_enroll():
        try:
            r = ActivityService(s2).enroll(c_new, act["id"])
            s2.commit()
            results["ok"] = r
        except Exception as e:  # noqa: BLE001
            results["err"] = type(e).__name__

    t = threading.Thread(target=b_enroll)
    t.start()
    time.sleep(1.0)  # 修复后 B 应阻塞在 Activity 锁；修复前 B 此窗口内已报名成功
    locked.status = Activity.STATUS_CANCELLED  # 持锁期间落取消态
    s1.commit()  # 释放锁 → B 获锁锁定读到 cancelled → 422
    t.join(timeout=8)
    assert not t.is_alive(), "B 线程 8s 未结束（疑似死锁）"
    assert results.get("err") == "ValidationError", (
        f"取消持锁期间并发 enroll 应被串行化后拦（活动已取消），实 {results}"
        "（RED=取消遍历期间报名滞留活跃态，不进批量退款）"
    )


def test_frozen_child_enroll_blocked(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13981017004", "冻结报名孩")
    act = _mk_activity(client, h, quota=5, fee=0, title="冻结报名活动")
    from backend.domain.identity.models import Child

    with _db() as db:
        child = db.query(Child).filter(Child.id == c["id"]).first()
        child.operation_locked = 1
        db.commit()
    r = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
    )
    assert r.status_code == 422, f"冻结孩报名应 422，实 {r.status_code} {r.text[:80]}"
    assert "冻结" in r.json()["detail"]
