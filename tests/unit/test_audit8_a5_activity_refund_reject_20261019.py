# tests/unit/test_audit8_a5_activity_refund_reject_20261019.py — 第八轮审查 A 批：P1-8 拒绝后恢复报名
"""**缺陷（第八轮审查 P1-8，实测复现）**：活动报名退款被拒后，报名**永久卡在 `refund_pending`**。

原先只有 deprecated 的 `POST /api/admin/activity-refunds/{eid}/review` 会恢复报名，而管理端
退款中心（`RefundCenter.tsx`）只调**统一审核端点** `/api/admin/refund-requests/{id}/review`
（`admin-web/src/api/activities.ts:108` 零引用）→ 拒绝后：占名额、签到被拒、不能重报（409）、
不能再次申请退款、聚合待办永不销项、活动永不自动 `finished`，**只能改库恢复**。

**修复**：把恢复动作收进 `RefundService.review` 的拒绝分支（成功路径 `_refund_activity_enrollment`
的对偶），实现落在 `activity/enrollment_ops.py`（条件 UPDATE/CAS）。

**判据**：拒绝后 ① 报名态回到 `enrolled`；② **能真签到**（用户可见后果，不是只看字段）；
③ 对照组：**通过**审核时不得提前恢复（`approve ≠ 钱已退`，报名须保持 `refund_pending` 等 execute）。
"""

from fastapi.testclient import TestClient

from backend.database import get_session
from backend.domain.activity.models import ActivityEnrollment
from backend.domain.identity.models import RefundRequest
from tests.unit.test_audit4_p2_batch3_20261017 import _activity, _enrolled_paid, _member_child
from tests.unit.test_wm11_tasks import _h


def _apply_refund(client: TestClient, c, mini, e) -> int:
    r = client.post(
        f"/api/miniapp/enrollments/{e['enrollment']['id']}/refund-apply",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    with get_session() as db:
        return (
            db.query(RefundRequest.id)
            .filter(
                RefundRequest.order_id == e["order_id"],
                RefundRequest.status == RefundRequest.STATUS_PENDING,
            )
            .scalar()
        )


def _status(eid: int) -> str:
    with get_session() as db:
        return db.get(ActivityEnrollment, eid).status


def test_reject_restores_enrollment_and_signin_works(client: TestClient):
    """核心守护：统一审核端点拒绝 → 报名回 `enrolled`，且**签到成功**。

    RED（还原修复：拒绝分支不恢复）：报名停在 `refund_pending`，签到 422
    「报名状态为 refund_pending，不可签到」。
    """
    h = _h(client)
    c, mini = _member_child(client, h, "13800003401")
    act = _activity(client, h, hours_later=72, title="拒绝恢复探针")
    e = _enrolled_paid(client, h, mini, c["id"], act["id"])
    eid = e["enrollment"]["id"]
    rr_id = _apply_refund(client, c, mini, e)
    assert _status(eid) == ActivityEnrollment.STATUS_REFUND_PENDING

    rv = client.post(
        f"/api/admin/refund-requests/{rr_id}/review",
        json={"approve": False, "remark": "家庭临时决定参加"},
        headers=h,
    )
    assert rv.status_code == 200, rv.text

    assert _status(eid) == ActivityEnrollment.STATUS_ENROLLED, "拒绝后报名必须恢复"
    with get_session() as db:  # 退款单本身仍是 rejected（不是被"修"回 pending）
        assert db.get(RefundRequest, rr_id).status == RefundRequest.STATUS_REJECTED

    # 用户可见后果：入场券可签到（refund_pending 会被 422 拦下）
    ticket = e["enrollment"]["ticket_code"]
    si = client.post("/api/admin/activity-signin", json={"ticket_code": ticket}, headers=h)
    assert si.status_code == 200, si.text
    with get_session() as db:
        row = db.get(ActivityEnrollment, eid)
        assert row.status == ActivityEnrollment.STATUS_CHECKED_IN, "签到成功即从「退款中」推进"
        assert row.cancel_reason is None


def test_approve_does_not_restore_enrollment(client: TestClient):
    """对照组（**防止修过头**）：通过审核 ≠ 钱已退——报名须保持 `refund_pending` 等 execute 联动。"""
    h = _h(client)
    c, mini = _member_child(client, h, "13800003402")
    act = _activity(client, h, hours_later=72, title="通过不恢复探针")
    e = _enrolled_paid(client, h, mini, c["id"], act["id"])
    eid = e["enrollment"]["id"]
    rr_id = _apply_refund(client, c, mini, e)

    rv = client.post(
        f"/api/admin/refund-requests/{rr_id}/review",
        json={"approve": True, "remark": "同意退款"},
        headers=h,
    )
    assert rv.status_code == 200, rv.text
    assert _status(eid) == ActivityEnrollment.STATUS_REFUND_PENDING, (
        "approve 阶段报名必须仍为退款中（终态由 execute 联动翻 refunded）"
    )


def test_reject_non_activity_order_untouched(client: TestClient):
    """对照组：非活动订单（会员费）退款被拒 → 不动报名表（判据只在活动订单上生效）。"""
    h = _h(client)
    c, mini = _member_child(client, h, "13800003403")
    act = _activity(client, h, hours_later=72, title="非活动订单探针")
    e = _enrolled_paid(client, h, mini, c["id"], act["id"])
    eid = e["enrollment"]["id"]

    # 直接对"观察期费订单"建退款单（活动报名单不动）→ 走同一审核端点
    with get_session() as db:
        from backend.domain.identity.models import Order

        obs = (
            db.query(Order)
            .filter(Order.child_id == c["id"], Order.order_type == Order.TYPE_OBSERVATION)
            .first()
        )
        rr = RefundRequest(
            child_id=c["id"],
            order_id=obs.id,
            kind=RefundRequest.KIND_ORDER,
            amount=obs.amount,
            status=RefundRequest.STATUS_PENDING,
            reason="探针：会员费退款",
        )
        db.add(rr)
        db.commit()
        rr_id = rr.id

    rv = client.post(
        f"/api/admin/refund-requests/{rr_id}/review",
        json={"approve": False, "remark": "不同意"},
        headers=h,
    )
    assert rv.status_code == 200, rv.text
    assert _status(eid) == ActivityEnrollment.STATUS_ENROLLED  # 本来就没进退款中
