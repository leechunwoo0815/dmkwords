# tests/unit/test_p0_fix20_d.py — 插修 10 T20d（#6）：联动退会单灰态显示
"""红测试：退款/转让驱动的联动退会单（source != normal）在管理待办以"待处理"
姿态躺着，运营误以为需要操作——实际全自动（execute 成功→withdrawn→completed
链已有，T5/B-13 修复族）。

修法（专家批准：ST_DONE + linkage 字段）：_decide_withdrawal 判 source——
联动单活跃态（applying/pending_settle/refunding）→ effective_status=ST_DONE
+ status_text="联动处理中·随退款自动推进" + linkage=True（不入 pending 计数，
不破坏 status_filter 二分）；直接退会单（normal）保持待处理。"""

from fastapi.testclient import TestClient

from tests.unit.test_wm13_admin_inbox import _h, _db, _send


def test_linkage_withdrawal_grey_state(client: TestClient):
    from backend.domain.identity.models import WithdrawalRequest

    h = _h(client)
    with _db() as db:
        w_link = WithdrawalRequest(
            child_id=1, source=WithdrawalRequest.SOURCE_REFUND, reason="x", status="applying"
        )
        w_direct = WithdrawalRequest(
            child_id=2, source=WithdrawalRequest.SOURCE_NORMAL, reason="x", status="applying"
        )
        db.add_all([w_link, w_direct])
        db.flush()
        link_id, direct_id = w_link.id, w_direct.id
        _send(db, "admin.withdrawal_apply", "withdrawal_request", link_id)
        _send(db, "admin.withdrawal_apply", "withdrawal_request", direct_id)
        db.commit()

    r = client.get("/api/admin/admin-notifications", headers=h)
    items = {(i["ref_type"], i["ref_id"]): i for i in r.json()["items"]}
    link = items[("withdrawal_request", str(link_id))]
    direct = items[("withdrawal_request", str(direct_id))]

    # 联动单：灰态文案 + linkage 标记 + 不入待处理
    assert link["status_text"] == "联动处理中·随退款自动推进", (
        f"联动单应灰态文案，实 {link['status_text']}"
    )
    assert link.get("linkage") is True, f"联动单应 linkage=True，实 {link.get('linkage')}"
    assert link["effective_status"] != "pending", (
        f"联动单不应入待处理，实 {link['effective_status']}"
    )

    # 直接退会单：保持待处理（操作按钮由前端按 effective_status=pending 渲染）
    assert direct["effective_status"] == "pending", (
        f"直接退会单应待处理，实 {direct['effective_status']}"
    )
    assert not direct.get("linkage"), "直接退会单 linkage 应为 False"

    # 计数：pending_count 只含直接退会单（联动单不入）
    assert r.json()["pending_count"] == 1, (
        f"pending_count 应 1（联动单不入），实 {r.json()['pending_count']}"
    )