# tests/unit/test_p0_fix23.py — 插修 13（23 号任务包 T1-T6）
"""活动域二轮返工红测试。T1：计数同源第 6 案——家长 tab 未读胶囊绑 unreadCount
state，该 state 仅 box=parent 时由 loadParent 填充（默认 admin 视角 loadParent
不跑）→ 胶囊恒 0（S1 修了显示层没修数据源触发）。
修法：todo-counts 补 parent_unread 字段（全体家长通知未读 COUNT，无筛选全量）
→ 胶囊改用全局数（loadParent 拉到后本地覆盖，双源同值）——胶囊数与视角无关。
"""

from datetime import datetime

from fastapi.testclient import TestClient

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
