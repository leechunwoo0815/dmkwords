# tests/unit/test_p0b3_t31_cancel_atomic.py — P0 第三批 T31（挂账）cancel_activity 事务原子性
"""红测试（二批严审观察项实锤）：批量循环内逐单 refund_svc.apply()（内含 commit），
第 K 单失败 → 前 K-1 单已提交 + 活动 status 已 CANCELLED 提交 → 重跑被
"活动状态不可取消"挡住 → 剩余 ENROLLED 报名永久滞留。

修复：apply 加 skip_commit（批量路径只 add+flush）+ cancel_activity 循环外
单次 commit + 断点续跑幂等（CANCELLED 且有 ENROLLED → 继续处理）。

红测试场景（monkeypatch 模拟第 2 单 apply 失败）：
- 2 个付费报名 → cancel_activity 中途炸 → 整体回滚（活动仍 published、报名仍
  enrolled、无台账单 = 修复前半程提交 = RED）
- 修复失败原因 → 重跑 → 活动 cancelled + 2 单全进 refund_pending
"""

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child


def _db():
    from backend.database import get_session

    return get_session()


def _two_paid_enrolled(client, h):
    out = []
    for i, (phone, name) in enumerate(
        [("13981031001", "原子取消孩一"), ("13981031002", "原子取消孩二")]
    ):
        c, m = _mk_child(client, h, phone, name)
        act = _mk_activity(client, h, quota=5, fee=50, title=f"原子活动{i}")
        e = client.post(
            f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=m
        ).json()
        r = client.post(
            f"/api/admin/orders/{e['order_id']}/confirm-payment",
            json={"pay_method": "scan"},
            headers=h,
        )
        assert r.status_code == 200
        out.append((act, e))
    return out


def test_cancel_activity_atomic_no_partial_commit(client: TestClient, monkeypatch):
    h = _h(client)
    (act1, e1), (act2, e2) = _two_paid_enrolled(client, h)
    # 两个报名放同一活动：手动把孩二报名挪到活动一（SQLAlchemy 直插改 activity_id）
    from backend.domain.activity.models import ActivityEnrollment

    with _db() as db:
        row = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.id == e2["enrollment"]["id"])
            .first()
        )
        row.activity_id = act1["id"]
        db.commit()

    # 模拟第 2 单 apply 失败（第 1 单成功后炸）
    from backend.domain.identity import wm10_service

    orig = wm10_service.RefundService.apply
    calls = {"n": 0}

    def flaky(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("模拟批量中途失败（台账写库炸）")
        return orig(self, *a, **kw)

    monkeypatch.setattr(wm10_service.RefundService, "apply", flaky)
    with pytest.raises(RuntimeError):
        client.post(f"/api/admin/activities/{act1['id']}/cancel", headers=h)
    monkeypatch.undo()

    # 原子性断言：活动仍 published、报名未动、零台账单（修复前：半程提交 = RED）
    from backend.domain.identity.models import RefundRequest

    with _db() as db:
        from backend.domain.activity.models import Activity

        a = db.query(Activity).filter(Activity.id == act1["id"]).first()
        assert a.status == "published", (
            f"中途失败应整体回滚（活动仍 published），实 {a.status}（RED=半程提交）"
        )
        e1r = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.id == e1["enrollment"]["id"])
            .first()
        )
        assert e1r.status == "enrolled", f"报名应未动，实 {e1r.status}（RED=半程提交）"
        rr_cnt = db.query(RefundRequest).filter(RefundRequest.is_deleted == 0).count()
        assert rr_cnt == 0, f"不应有半程台账单，实 {rr_cnt}"

    # 重跑（断点续跑）：全部进退款待审
    r = client.post(f"/api/admin/activities/{act1['id']}/cancel", headers=h)
    assert r.status_code == 200, r.text
    with _db() as db:
        from backend.domain.activity.models import Activity

        a = db.query(Activity).filter(Activity.id == act1["id"]).first()
        assert a.status == "cancelled"
        for eid in (e1["enrollment"]["id"], e2["enrollment"]["id"]):
            row = db.query(ActivityEnrollment).filter(ActivityEnrollment.id == eid).first()
            assert row.status == "refund_pending", f"重跑后报名应 refund_pending，实 {row.status}"
        assert db.query(RefundRequest).filter(RefundRequest.is_deleted == 0).count() == 2
