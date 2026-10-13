# tests/unit/test_g9_milestone_staff_notify_20261013.py — G9 里程碑的馆员通知通道
"""PRD §7.4「每达成一个节点：自动解锁对应勋章，**同时通知家长和馆员**（馆员可以借此线下
表扬、发朋友圈、作为颁奖盛典候选）」——此前只有家长侧（`reading.milestone`），
馆员侧无通道（docs/09 第五十四轮登记为 **G9**）。

本文件钉住 2026-10-13（第五十五轮）补上的口径：
① 越线发放时**两侧都有**（家长小程序通知 + 管理端待办墙 `admin.milestone_achieved`）；
② 一个节点一条、`dedup_key` = 节点词数 → 重复提醒不刷屏（幂等）；
③ 显示态固定"已达成·可线下表扬"且**不计入待办数**（知会而非待审事项）；
④ 馆员（staff01，只读视角）也能看到——它不是超管专属事项。

回退即红：去掉 `_on_milestone_achieved` 里的 AdminNotifyService 调用 → ①④ 转红；
去掉 resolver 的 scene 分支 → ③ 的文案断言转红。
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm7_growth import _h, _setup_finished_book


def _cross_milestone(client: TestClient, h: dict, phone: str, isbn: str):
    """把节点降到 2000（书 2500 词）→ 完播 + 测验通过 → 一次越线发放。返回 (child, mini)。"""
    r = client.put(
        "/api/admin/configs/milestone_nodes",
        json={"value": "2000,100000,500000", "reason": "G9 测试：降低节点以触发发放"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    c, book, mini = _setup_finished_book(client, h, phone, isbn, word_count=2500)
    sub = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={"child_id": c["id"], "answers": ["A", "A", "A", "A", "对"]},
        headers=mini,
    )
    assert sub.status_code == 200, sub.text
    return c, book, mini


def _inbox(client: TestClient, headers: dict, **params) -> dict:
    q = {"scene": "admin.milestone_achieved", **params}
    r = client.get("/api/admin/admin-notifications", params=q, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_milestone_notifies_parent_and_staff(client: TestClient):
    h = _h(client)
    c, _, mini = _cross_milestone(client, h, "13800000731", "9787100000071")

    parent_items = client.get(
        "/api/miniapp/notifications",
        params={"scene": "reading.milestone"},
        headers=mini,
    ).json()["items"]
    assert parent_items, "家长侧应有里程碑通知（既有通道，G6/WM11 不动）"

    inbox = _inbox(client, h)
    assert inbox["total"] == 1, inbox
    row = inbox["items"][0]
    assert row["ref_type"] == "child", row
    assert row["ref_id"] == str(c["id"]), row
    assert "2,000" in row["title"], row  # 一个节点一条，标题带节点词数
    assert "线下表扬" in row["content"], row
    assert row["applicant_name"].endswith(f"·{c['name']}"), row  # 家长名·孩子名


def test_staff_notice_visible_to_operator(client: TestClient):
    """馆员（staff01）也要看得到——PRD 的"通知馆员"不是"通知超管"（待办墙 staff 只读可见）。"""
    h = _h(client)
    _cross_milestone(client, h, "13800000732", "9787100000072")

    staff = _h(client, "staff01")
    inbox = _inbox(client, staff)
    assert inbox["total"] == 1, inbox
    assert inbox["read_only"] is True, inbox


def test_staff_notice_is_not_a_todo(client: TestClient):
    """知会不进待办数：显示态"已达成·可线下表扬"，待处理筛选与 admin_total 都不含它。"""
    h = _h(client)
    _cross_milestone(client, h, "13800000733", "9787100000073")

    row = _inbox(client, h)["items"][0]
    assert row["effective_status"] == "done", row
    assert row["status_text"] == "已达成·可线下表扬", row

    pending = _inbox(client, h, status_filter="pending")
    assert pending["total"] == 0 and pending["pending_count"] == 0, pending

    counts = client.get("/api/admin/todo-counts", headers=h).json()
    assert counts["admin_total"] == 0, counts


def test_staff_notice_kept_out_of_todo_wall(client: TestClient):
    """知会类**不进待办墙默认名单**（2026-10-13 用户要求：混在一起看着乱）——
    待办墙（待处理/已处理）就是"待审事项墙"，里程碑只有显式按 `scene` 筛选才列出
    （前端「里程碑知会」视图走这条）。"""
    h = _h(client)
    _cross_milestone(client, h, "13800000735", "9787100000075")

    for st in ("pending", "finished"):
        wall = client.get(
            "/api/admin/admin-notifications", params={"status_filter": st}, headers=h
        ).json()
        assert wall["total"] == 0, f"知会类不该出现在待办墙（{st}）：{wall}"

    assert _inbox(client, h)["total"] == 1, "显式按里程碑场景筛选必须能看到"


def test_staff_notice_deduped_per_node(client: TestClient):
    """幂等：同孩子同节点重复提醒不刷屏（dedup_key = 节点词数，撞唯一索引静默忽略）。"""
    from backend.common.events import MilestoneAchievedEvent, event_bus
    from backend.database import get_session

    h = _h(client)
    c, _, _ = _cross_milestone(client, h, "13800000734", "9787100000074")
    assert _inbox(client, h)["total"] == 1

    with get_session() as db:
        event_bus.publish(MilestoneAchievedEvent(child_id=c["id"], nodes=(2000,)), db=db)
        db.commit()

    inbox = _inbox(client, h)
    assert inbox["total"] == 1, f"同节点不应重复写入：{inbox}"
