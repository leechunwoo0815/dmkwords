# tests/unit/test_audit8_a2_notify_openid_20261019.py — 第八轮审查 A 批：P2-10 通知 openid 单点
"""**缺陷（第八轮审查 P2-10）**：全仓 18 个 `NotificationService.send` 调用点里有 **10 个**没传
`openid` → `_wechat_push` 走到"无 openid"分支直接写 `skipped: 家长未授权（无 openid）`，
这些场景（退款审核结果 / 退会审核结果 / 转让结果 / 报名成功 / 周月报 / 评估报告 / 阅读圈点赞…）
的**微信通道恒为空转**，且跳过原因是**假原因**（家长其实绑了微信、模板也已配）。

**修复**：`wm_notify.parent_openid()` 作为唯一取用点（跨域延迟 import），10 处补齐。

**判据设计（避开假绿，如实记录首版踩坑）**：`_wechat_push` 的第一道短路是"通道未启用"——若不在用例里
**打开通道**，无论有没有 openid 都只会看到"通道未启用"，用例**不可证伪**（本批首版就是这么写的，
跑出对照组也显示"通道未启用"才发现）。三条用例统一 `WECHAT_SUBSCRIBE_ENABLED=True`，把判定推进到
openid 之后那一步：

| 用例 | 家长 | 期望的跳过原因 |
|---|---|---|
| 已绑 + 场景已映射 | 有 openid | **订阅模板 REFUND_RESULT 未配置**（证明 openid 取到、场景命中） |
| 已绑 + 场景未映射 | 有 openid | **场景未映射订阅模板（activity.enroll）**（同样证明 openid 取到） |
| 未绑 | 无 openid | **家长未授权（无 openid）**（对照组：不许修过头把真原因也抹掉） |
"""

from fastapi.testclient import TestClient

from backend.common.notifications import SCENE_ACTIVITY_ENROLL, SCENE_MONEY_REFUND_RESULT
from backend.config import get_settings
from backend.database import get_session
from backend.domain.identity.models import Parent, RefundRequest
from tests.unit.test_audit4_p2_batch3_20261017 import _activity, _enrolled_paid, _member_child
from tests.unit.test_wm11_tasks import _h, _notifs

NO_OPENID = "家长未授权（无 openid）"


def _parent_id_by_phone(phone: str) -> int:
    with get_session() as db:
        return db.query(Parent.id).filter(Parent.phone == phone).scalar()


def _bind_openid(parent_id: int, openid: str = "o_probe_openid") -> None:
    """模拟家长已授权微信（真机走 `bind_wechat`；单测直改，理由：外部通道不可达）。"""
    with get_session() as db:
        db.get(Parent, parent_id).wechat_openid = openid
        db.commit()


def _notif(parent_id: int, scene: str):
    rows = [n for n in _notifs(parent_id) if n.scene == scene]
    assert rows, f"没有 {scene} 通知：{[(n.scene, n.wechat_error) for n in _notifs(parent_id)]}"
    return rows[0]


def _notifs_of(parent_id: int, scene: str) -> list:
    """该场景的**全部**通知（同一 scene 可能有多个生产点，只看一行会漏掉没改的那个）。"""
    rows = [n for n in _notifs(parent_id) if n.scene == scene]
    assert rows, f"没有 {scene} 通知"
    return rows


def _enable_wechat(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "WECHAT_SUBSCRIBE_ENABLED", True, raising=False)


def _apply_and_reject_refund(client: TestClient, h, c, mini, title: str) -> int:
    """报名 → 申请退款 → 超管拒绝；返回退款单 id（走真实端点，不绕过服务层）。"""
    act = _activity(client, h, hours_later=72, title=title)
    e = _enrolled_paid(client, h, mini, c["id"], act["id"])
    r = client.post(
        f"/api/miniapp/enrollments/{e['enrollment']['id']}/refund-apply",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    with get_session() as db:
        rr_id = (
            db.query(RefundRequest.id)
            .filter(
                RefundRequest.order_id == e["order_id"],
                RefundRequest.status == RefundRequest.STATUS_PENDING,
            )
            .scalar()
        )
    rv = client.post(
        f"/api/admin/refund-requests/{rr_id}/review",
        json={"approve": False, "remark": "探针拒绝"},
        headers=h,
    )
    assert rv.status_code == 200, rv.text
    return rr_id


def test_refund_reviewed_notify_carries_openid(client: TestClient, monkeypatch):
    """已绑家长 + 已映射场景：跳过原因必须是**模板未配置**，而不是"家长未授权"。

    RED（还原修复：`wm_notify` 三个 send 不传 openid）：`wechat_error == NO_OPENID`。
    """
    _enable_wechat(monkeypatch)
    h = _h(client)
    c, mini = _member_child(client, h, "13800003201")
    pid = _parent_id_by_phone("13800003201")
    _bind_openid(pid)

    _apply_and_reject_refund(client, h, c, mini, "openid 探针活动 A")

    n = _notif(pid, SCENE_MONEY_REFUND_RESULT)
    assert n.wechat_error != NO_OPENID, "openid 没取到 → 微信通道被假原因掩盖"
    assert n.wechat_error == "订阅模板 REFUND_RESULT 未配置（微信后台申请后填入 .env）"


def test_enroll_notify_carries_openid(client: TestClient, monkeypatch):
    """已绑家长 + 未映射场景：原因必须推进到"场景未映射"（同样证明 openid 已取到）。

    `activity.enroll` 有**两个**生产点（家长报名 `activity/service.py` + 收款确认
    `order_service.py`，dedup 键不同 → 两行都在），所以断言**该场景每一行**都满足——
    只抽查一行会在"只改了一处"的变异下假绿（本批首版即如此，如实记录）。

    RED（还原修复：activity 侧不传 openid）：该场景有一行 `wechat_error == NO_OPENID`。
    """
    _enable_wechat(monkeypatch)
    h = _h(client)
    c, mini = _member_child(client, h, "13800003202")
    pid = _parent_id_by_phone("13800003202")
    _bind_openid(pid)

    act = _activity(client, h, hours_later=72, title="openid 探针活动 B")
    _enrolled_paid(client, h, mini, c["id"], act["id"])

    rows = _notifs_of(pid, SCENE_ACTIVITY_ENROLL)
    assert len(rows) == 2, f"报名 + 收款确认各一条，实际 {[(n.dedup_key) for n in rows]}"
    for n in rows:
        assert n.wechat_error != NO_OPENID, f"openid 没取到（dedup_key={n.dedup_key}）"
        assert n.wechat_error == "场景未映射订阅模板（activity.enroll）——仅站内消息"


def test_unbound_parent_still_reports_missing_openid(client: TestClient, monkeypatch):
    """对照组（**防止修过头**）：家长确实没绑微信时，原因就该是"家长未授权（无 openid）"。"""
    _enable_wechat(monkeypatch)
    h = _h(client)
    c, mini = _member_child(client, h, "13800003203")
    pid = _parent_id_by_phone("13800003203")

    act = _activity(client, h, hours_later=72, title="openid 探针活动 C")
    _enrolled_paid(client, h, mini, c["id"], act["id"])

    n = _notif(pid, SCENE_ACTIVITY_ENROLL)
    assert n.wechat_error == NO_OPENID
