# tests/unit/test_p0_fix20_c.py — 插修 10 T20c（#5）：手动标记已处理被显示态覆盖
"""红测试：标记已处理写库+审计全部成功（handled_at 落库），但显示态实时算
（resolve_many）只认业务单据状态——机器推导"pending"覆盖人工标记（用户看到
标记了还显示待处理）。

修法（人工明确意图优先于机器推导）：resolve_many 判定前置 handled_at 层——
handled_at 非空 → effective_status=ST_DONE + status_text="已处理·手动标记"；
计数（pending_count/todo-counts admin_total）同步用本口径。业务单据真实推进
（退款中心审单）不受影响——通知只是感知层。
"""

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.unit.test_wm13_admin_inbox import _db, _h, _send


def test_manual_handle_overrides_display_state(client: TestClient):
    """修复前：handle 成功但 list_inbox 仍显示 pending（机器推导覆盖 = RED）。"""
    h = _h(client)
    with _db() as db:
        from backend.domain.identity.models import RefundRequest

        r_pending = RefundRequest(
            kind="order", child_id=1, amount=Decimal("500"), reason="x", status="pending"
        )
        db.add(r_pending)
        db.flush()
        _send(db, "admin.refund_apply", "refund_request", r_pending.id, amount=Decimal("500"))
        db.commit()

    # 基线：待处理计数 1
    rc0 = client.get("/api/admin/todo-counts", headers=h)
    assert rc0.json()["admin_total"] == 1, f"基线待处理应 1，实 {rc0.json()}"

    # 列表拿通知 id → 手动标记已处理
    r0 = client.get("/api/admin/admin-notifications", headers=h)
    nid = r0.json()["items"][0]["id"]
    rh = client.post(
        f"/api/admin/admin-notifications/{nid}/handle", json={"reason": "手动标记已处理"}, headers=h
    )
    assert rh.status_code == 200, rh.text

    # 修复后：pending 过滤不含（机器推导被人工标记覆盖）
    rp = client.get(
        "/api/admin/admin-notifications", params={"status_filter": "pending"}, headers=h
    )
    assert all(i["id"] != nid for i in rp.json()["items"]), (
        f"手动标记后仍出现在 pending（机器推导覆盖人工标记 = RED）：{rp.json()['items'][:2]}"
    )

    # finished 过滤含 + 文案"已处理·手动标记"
    rf = client.get(
        "/api/admin/admin-notifications", params={"status_filter": "finished"}, headers=h
    )
    item = next((i for i in rf.json()["items"] if i["id"] == nid), None)
    assert item is not None, "手动标记后未出现在 finished"
    assert item["status_text"] == "已处理·手动标记", (
        f"文案应'已处理·手动标记'，实 {item['status_text']}"
    )

    # todo-counts admin_total 同步 -1（标记后不再计入待处理）
    rc = client.get("/api/admin/todo-counts", headers=h)
    assert rc.json()["admin_total"] == 0, f"标记后 admin_total 应 0，实 {rc.json()}"
