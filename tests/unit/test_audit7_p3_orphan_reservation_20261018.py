# tests/unit/test_audit7_p3_orphan_reservation_20261018.py — 第七轮 P3-3：还书带损的预约善后
"""P3-3：`报损`/`手工改状态` 都走 `handle_copy_unavailable`（副本退场的预约善后漏斗），
**唯独"还书带损"没走**——靠的是"`reserved` 副本不会被借出，所以归还中的副本上没有 active 预约"
这条**隐式前提**（审查报告记为"当前不可利用，属隐式前提"）。

修法（不动锁序）：`return_book` **提交后**（行锁已释放）调用
`reservation_ops.settle_orphans_after_copy_gone`——正常路径零命中；
真命中 = 前提被打破 → **错误日志 + 审计留痕**后按漏斗善后（改锁到同书其它在馆副本 / 转 exception + 通知）。

两条用例（第二条是防误报守护）：
  1. 人为造出"归还中的副本上挂着 active 预约"（隐式前提被打破）→ 还书带损后预约**不得悬挂**，且有审计留痕；
  2. 正常带损归还（无预约）→ **不留痕、不误报**。
RED（去掉提交后的善后调用）：①的预约停在 active（悬挂）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_p1_audit_20261015 import _db, _family
from tests.unit.test_p2_audit_20261016 import _book, _pay_deposit

SCENE_AUDIT_ACTION = "circulation.copy_unavailable_with_active_reservation"


def _h(client: TestClient) -> dict:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _borrowed(client: TestClient, h: dict, phone: str, title: str) -> tuple[int, int, int]:
    """建档 + 押金 + 建书 + 借出，返回 (孩子 id, 副本 id, 借阅行 id)。"""
    _p, c, _mini = _family(client, h, phone, "带损孩")
    _pay_deposit(client, h, c["id"])
    _book_id, copies = _book(client, h, title)
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copies[0]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return c["id"], copies[0], r.json()["id"]


def _force_orphan(child_id: int, book_id: int, copy_id: int) -> int:
    """人为造出"在借副本上挂着 active 预约"（正常流程不可能出现——正是隐式前提的内容）。"""
    from backend.domain.reading.models import Reservation

    with _db() as db:
        res = Reservation(
            child_id=child_id,
            book_id=book_id,
            copy_id=copy_id,
            expires_at=datetime.now() + timedelta(hours=72),
            status=Reservation.STATUS_ACTIVE,
        )
        db.add(res)
        db.commit()
        return res.id


def _reservation(res_id: int):
    from backend.domain.reading.models import Reservation

    with _db() as db:
        return db.query(Reservation).filter(Reservation.id == res_id).first()


def _audit_rows(target_id: str) -> int:
    from backend.domain.admin.models import AuditLog

    with _db() as db:
        return (
            db.query(AuditLog)
            .filter(AuditLog.action == SCENE_AUDIT_ACTION, AuditLog.target_id == target_id)
            .count()
        )


def test_return_with_damage_settles_orphan_reservation(client: TestClient):
    """隐式前提被打破时：预约不得悬挂，且必须留下可追溯的审计。"""
    h = _h(client)
    child_id, copy_id, _rec_id = _borrowed(client, h, "13800007721", "带损善后样例")
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        book_id = db.query(BookCopy).filter(BookCopy.id == copy_id).first().book_id
    res_id = _force_orphan(child_id, book_id, copy_id)

    r = client.post(
        "/api/admin/circulation/return",
        json={"copy_id": copy_id, "condition": "maintenance"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    res = _reservation(res_id)
    assert res.status is not None
    assert res.status != "active", (
        f"副本已退场（维护），其上的预约不得停在 active（悬挂）：实际 {res.status}——"
        "RED=还书带损没走预约善后漏斗"
    )
    assert res.status == "exception", f"无其它在馆副本时应转 exception：{res.status}"
    assert _audit_rows(str(copy_id)) == 1, "隐式前提被打破必须留审计（人工可追溯）"


def test_return_with_damage_without_reservation_leaves_no_audit(client: TestClient):
    """正常带损归还（无预约）→ 零副作用、零误报。"""
    h = _h(client)
    _child_id, copy_id, _rec_id = _borrowed(client, h, "13800007722", "带损无预约样例")

    r = client.post(
        "/api/admin/circulation/return",
        json={"copy_id": copy_id, "condition": "maintenance"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert _audit_rows(str(copy_id)) == 0, "无预约的正常带损归还不得留下'前提被打破'的审计"
