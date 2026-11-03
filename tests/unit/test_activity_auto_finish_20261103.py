# tests/unit/test_activity_auto_finish_20261103.py — 活动自动收口（2026-11-03 甲方报障）
"""已结束的活动只要有报名就永远停在 published → 后台一直显示「报名中」、签到失败。

本判据锁住新口径：
- 时间：签到窗关闭（`end_at + 30min`）后收口，不再等 start+24h；
- enrolled / checked_in 不阻止结束；
- pending_payment / refund_pending（资金在途）仍不结束；
- 结束时给活跃报名家长发「活动已结束」站内信，重跑不重发。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from backend.common.notification_models import Notification
from backend.domain.activity.models import Activity, ActivityEnrollment
from backend.domain.activity.service import ActivityService
from backend.domain.identity.models import Child
from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child


def _set_activity_end(db, activity_id: int, *, minutes_ago: int = 120) -> None:
    """把活动挪到过去（end 默认 2 小时前，签到窗已关闭）。"""
    a = db.query(Activity).filter(Activity.id == activity_id).first()
    assert a is not None
    end = datetime.now() - timedelta(minutes=minutes_ago)
    a.end_at = end
    a.start_at = end - timedelta(hours=2)
    db.commit()


def _enroll(client: TestClient, act: dict, child: dict, mini: dict) -> dict:
    r = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll",
        json={"child_id": child["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    return r.json()["enrollment"]


def _finish_notifications(db, activity_id: int) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(
            Notification.scene == "activity.finish",
            Notification.ref_type == "activity",
            Notification.ref_id == str(activity_id),
            Notification.is_deleted == 0,
        )
        .all()
    )


def test_auto_finish_closes_activity_with_enrolled_participant(client: TestClient, db):
    """核心回归：有 enrolled 报名的活动到期必须结束（旧逻辑永远跳过）。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=0, title="到期活动-已报名")
    child, mini = _mk_child(client, h, "13800001801", "到期孩一")
    enrollment = _enroll(client, act, child, mini)
    assert enrollment["status"] == "enrolled"
    _set_activity_end(db, act["id"])

    finished = ActivityService(db).activity_auto_finish()

    db.expire_all()
    a = db.query(Activity).filter(Activity.id == act["id"]).first()
    assert finished == 1
    assert a.status == Activity.STATUS_FINISHED
    notifs = _finish_notifications(db, act["id"])
    assert len(notifs) == 1
    assert notifs[0].title == "活动已结束"
    assert "到期活动-已报名" in notifs[0].content
    assert notifs[0].parent_id == db.query(Child).filter(Child.id == child["id"]).first().parent_id

    # 重跑：活动已是终态，不重复发
    assert ActivityService(db).activity_auto_finish() == 0
    assert len(_finish_notifications(db, act["id"])) == 1


def test_auto_finish_skips_pending_payment(client: TestClient, db):
    """资金在途（未收款）不结束——等收款确认或 48h 订单超时取消。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=50, title="到期活动-待支付")
    child, mini = _mk_child(client, h, "13800001802", "到期孩二")
    enrollment = _enroll(client, act, child, mini)
    assert enrollment["status"] == "pending_payment"
    _set_activity_end(db, act["id"])

    assert ActivityService(db).activity_auto_finish() == 0
    db.expire_all()
    assert db.query(Activity).filter(Activity.id == act["id"]).first().status == (
        Activity.STATUS_PUBLISHED
    )
    assert _finish_notifications(db, act["id"]) == []


def test_auto_finish_skips_refund_pending(client: TestClient, db):
    """退款审核在途不结束——先让退款落终态，避免活动终止打断资金链。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=0, title="到期活动-退款中")
    child, mini = _mk_child(client, h, "13800001803", "到期孩三")
    enrollment = _enroll(client, act, child, mini)
    e = db.query(ActivityEnrollment).filter(ActivityEnrollment.id == enrollment["id"]).first()
    e.status = ActivityEnrollment.STATUS_REFUND_PENDING
    db.commit()
    _set_activity_end(db, act["id"])

    assert ActivityService(db).activity_auto_finish() == 0
    db.expire_all()
    assert db.query(Activity).filter(Activity.id == act["id"]).first().status == (
        Activity.STATUS_PUBLISHED
    )


def test_auto_finish_waits_for_checkin_window(client: TestClient, db):
    """结束后 30 分钟内签到窗还开着 → 不结束（馆员仍可补签到）。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=0, title="到期活动-签到窗内")
    child, mini = _mk_child(client, h, "13800001804", "到期孩四")
    _enroll(client, act, child, mini)
    _set_activity_end(db, act["id"], minutes_ago=10)

    assert ActivityService(db).activity_auto_finish() == 0
    db.expire_all()
    assert db.query(Activity).filter(Activity.id == act["id"]).first().status == (
        Activity.STATUS_PUBLISHED
    )


def test_auto_finish_notifies_checked_in_participant(client: TestClient, db):
    """已签到的家长也收到「活动已结束」（不是只发未签到的人）。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=0, title="到期活动-已签到")
    child, mini = _mk_child(client, h, "13800001805", "到期孩五")
    enrollment = _enroll(client, act, child, mini)
    e = db.query(ActivityEnrollment).filter(ActivityEnrollment.id == enrollment["id"]).first()
    e.status = ActivityEnrollment.STATUS_CHECKED_IN
    db.commit()
    _set_activity_end(db, act["id"])

    assert ActivityService(db).activity_auto_finish() == 1
    db.expire_all()
    assert db.query(Activity).filter(Activity.id == act["id"]).first().status == (
        Activity.STATUS_FINISHED
    )
    assert len(_finish_notifications(db, act["id"])) == 1


def test_auto_finish_without_enrollment_still_finishes(client: TestClient, db):
    """无人报名的活动仍按时间收口（旧行为不回退），且不发通知。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=2, fee=0, title="到期活动-无人报名")
    _set_activity_end(db, act["id"])

    assert ActivityService(db).activity_auto_finish() == 1
    db.expire_all()
    assert db.query(Activity).filter(Activity.id == act["id"]).first().status == (
        Activity.STATUS_FINISHED
    )
    assert _finish_notifications(db, act["id"]) == []
