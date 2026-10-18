# tests/unit/test_audit7_p2_dead_letter_board_20261018.py — 第七轮 P2-1：死信读取面
"""P2-1：`dead_letters` 只写不读（`grep DeadLetter backend | grep -v models|events → 0`）——
事件处理器重试后仍失败 = 跨域副作用（积分/词数/通知/审计）静默缺失，而运营**看不到**。

修法：管理端死信看板（列表 + 待处理计数 + 人工销项），权限与任务看板同源（`dashboard.view`）。
用例走**真事件总线**造死信（不是直接插库），确保"写入路径 ↔ 读取路径"真的接上。

RED（去掉端点/去掉销项字段）：列表 404 / 待处理计数不变。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.unit.test_p1_audit_20261015 import _db

EVENT = "p2_1.probe"


def _h(client: TestClient, username: str = "admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _make_dead_letter() -> int:
    """经真事件总线造一条死信（处理器抛错 → 兜底落库），返回死信行 id。"""
    from backend.common.events import DomainEvent, event_bus
    from backend.common.notification_models import DeadLetter

    def boom(event, db):
        raise ValueError("P2-1 探针：订阅器故意失败")

    event_bus.subscribe(EVENT, boom)
    db = _db()
    try:
        try:
            event_bus.publish(DomainEvent(event_type=EVENT), db=db)
        except ValueError:
            db.rollback()
        else:  # pragma: no cover
            raise AssertionError("共享会话模式下订阅器异常必须 re-raise")
    finally:
        db.close()
        event_bus.unsubscribe(EVENT, boom)

    check = _db()
    try:
        row = (
            check.query(DeadLetter)
            .filter(DeadLetter.event_type == EVENT)
            .order_by(DeadLetter.id.desc())
            .first()
        )
        assert row is not None, "死信必须落库（前置）"
        return row.id
    finally:
        check.close()


def _audit_count() -> int:
    from backend.domain.admin.models import AuditLog

    with _db() as db:
        return db.query(AuditLog).filter(AuditLog.action == "dead_letter.handle").count()


def test_dead_letter_board_lists_and_handles(client: TestClient):
    """死信看板：看得到（含待处理计数）→ 能销项 → 计数回落 + 审计留痕 + 幂等。"""
    h = _h(client)
    dl_id = _make_dead_letter()

    r = client.get("/api/admin/dead-letters", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending"] >= 1, f"待处理计数必须能看见死信：{body}"
    item = next((i for i in body["items"] if i["id"] == dl_id), None)
    assert item is not None, f"新建的死信必须出现在列表：{body['items'][:3]}"
    assert item["event_type"] == EVENT and item["handled_at"] is None
    assert "故意失败" in item["error"], item
    assert json.loads(item["payload"]) is not None

    pending_before = body["pending"]
    ok = client.post(f"/api/admin/dead-letters/{dl_id}/handle", headers=h)
    assert ok.status_code == 200, ok.text
    assert ok.json()["handled"] is True and ok.json()["already_handled"] is False

    after = client.get("/api/admin/dead-letters", headers=h).json()
    assert after["pending"] == pending_before - 1, f"销项后待处理计数必须回落：{after}"
    handled = next(i for i in after["items"] if i["id"] == dl_id)
    assert handled["handled_at"] is not None and handled["handled_by"] is not None
    assert _audit_count() == 1, "销项必须留审计"

    # 幂等：重复销项不改首处理人/时刻，也不重复写审计
    again = client.post(f"/api/admin/dead-letters/{dl_id}/handle", headers=h)
    assert again.status_code == 200 and again.json()["already_handled"] is True
    assert _audit_count() == 1


def test_dead_letter_board_unresolved_filter(client: TestClient):
    """`unresolved_only=true` 只出未处理（徽标/待办口径）。"""
    h = _h(client)
    dl_id = _make_dead_letter()
    client.post(f"/api/admin/dead-letters/{dl_id}/handle", headers=h)

    only_pending = client.get(
        "/api/admin/dead-letters", params={"unresolved_only": True}, headers=h
    ).json()
    assert all(i["handled_at"] is None for i in only_pending["items"]), only_pending["items"]
    assert not any(i["id"] == dl_id for i in only_pending["items"]), "已销项的不该出现在未处理列表"


def test_dead_letter_handle_unknown_id_404(client: TestClient):
    """不存在的死信 → 404（业务异常，不是 500）。"""
    h = _h(client)
    r = client.post("/api/admin/dead-letters/99999999/handle", headers=h)
    assert r.status_code == 404, r.text
