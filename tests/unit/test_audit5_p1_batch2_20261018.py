# tests/unit/test_audit5_p1_batch2_20261018.py — 第五轮大审查·P1 批量（子批 2：周期类去重键）
"""P1-2：续借后**第二周期**的到期提醒 / 再次逾期通知被去重键吞掉（**静默**丢失）。

根因两条（同族）：
  · `circulation/reminders.py` 到期提醒：`dedup_key=str(days)`（只有节点值，无周期）；
  · `circulation/service.py::overdue_mark` 逾期通知：`send()` 不传 `dedup_key` → 落默认 `"1"`。
唯一索引是 `(parent_id, scene, ref_type, ref_id, dedup_key, is_deleted)`，而 `ref_id` 是**借阅行 id**
（终身不变）→ 同一行的第二周期判重命中 → 提醒被静默丢弃（家长以为已提醒过）。

修法（红线：**周期类场景的去重键必须带周期**）：键里带**本次到期日**
`f"{days}:{due_at:%Y%m%d}"` / `f"overdue:{due_at:%Y%m%d}"`。

两条用例逐条**去修复即红**（变异自证见 docs/09 第七十五批）：
  1. 到期提醒 → 馆员续借（到期日 +7）→ 第二周期同一节点（5 天）必须**再**提醒一次；
  2. 逾期通知 → 续借（G7 人工办理）→ 再次逾期必须**再**通知一次。
时钟用**可控替换**（不 sleep）：`reminders` 模块内 `datetime.now()` 由用例钉死，两周期可判定。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_p1_audit_20261015 import _db, _family
from tests.unit.test_p2_audit_20261016 import _book, _pay_deposit

DUE_REMIND_SCENE = "borrow.due_remind"
OVERDUE_SCENE = "borrow.overdue"


def _h(client: TestClient) -> dict:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _borrow(client: TestClient, h: dict, phone: str, child_name: str, title: str) -> tuple[int, int]:
    """建档 + 缴押金 + 建书 + 借出一本，返回 (孩子 id, 借阅行 id)。"""
    _p, c, _mini = _family(client, h, phone, child_name)
    _pay_deposit(client, h, c["id"])
    _book_id, copies = _book(client, h, title)
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copies[0]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return c["id"], r.json()["id"]


class _Clock(datetime):
    """可控时钟：`reminders` 内 `datetime.now()` 返回本类 `fixed`（不 sleep、可判定）。"""

    fixed: datetime | None = None

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return cls.fixed


def test_due_remind_second_period_after_renew(client: TestClient, monkeypatch):
    """到期提醒：第一周期（节点 5）→ 馆员续借 → 第二周期（节点 5）必须再提醒。"""
    from backend.common.notification_models import Notification
    from backend.domain.circulation import reminders as reminders_mod
    from backend.domain.circulation.models import BorrowRecord

    h = _h(client)
    _child_id, rec_id = _borrow(client, h, "13981600011", "续借提醒孩", "续借提醒样例")
    base = date.today() + timedelta(days=30)  # 远离真实"今天"，避免真实时钟干扰

    def _run_remind(at: date) -> tuple[int, list[str]]:
        _Clock.fixed = datetime.combine(at, datetime.min.time()) + timedelta(hours=10)
        monkeypatch.setattr(reminders_mod, "datetime", _Clock)
        with _db() as db:
            sent = reminders_mod.book_due_remind(db)
            keys = [
                n.dedup_key
                for n in db.query(Notification)
                .filter(Notification.scene == DUE_REMIND_SCENE, Notification.is_deleted == 0)
                .all()
            ]
        return sent, keys

    with _db() as db:
        db.query(BorrowRecord).filter(BorrowRecord.id == rec_id).update({"due_at": _dt(base)})
        db.commit()

    sent1, keys1 = _run_remind(base - timedelta(days=5))  # 节点 5，第一周期
    assert sent1 == 1, f"第一周期应提醒一次（实 {sent1}）keys={keys1}"

    r = client.post(
        "/api/admin/circulation/renew",
        json={"record_id": rec_id, "reason": "家长到店续借"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    new_due = r.json()["due_at"][:10]

    sent2, keys2 = _run_remind(datetime.fromisoformat(new_due).date() - timedelta(days=5))
    assert sent2 == 1, (
        f"续借后的**第二周期**同一节点必须再提醒一次（实 {sent2}）——"
        f"RED=去重键只含节点值，与上一周期判重命中被吞。keys={keys2}"
    )
    assert len(keys2) == 2, keys2


def test_overdue_notice_second_period_after_renew(client: TestClient):
    """逾期通知：第一次逾期 → 续借（G7 人工办理）→ 再次逾期必须再通知一次。"""
    from backend.common.notification_models import Notification
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.circulation.service import CirculationService

    h = _h(client)
    _child_id, rec_id = _borrow(client, h, "13981600012", "逾期续借孩", "逾期续借样例")

    with _db() as db:
        db.query(BorrowRecord).filter(BorrowRecord.id == rec_id).update(
            {"due_at": _dt(date.today() - timedelta(days=3))}
        )
        db.commit()
        assert CirculationService(db).overdue_mark() == 1
        first = (
            db.query(Notification)
            .filter(Notification.scene == OVERDUE_SCENE, Notification.is_deleted == 0)
            .count()
        )
    assert first == 1, "第一次逾期应通知一条"

    # G7：馆员对逾期书人工续借（必须填原因）→ 状态回在借、到期日顺延
    r = client.post(
        "/api/admin/circulation/renew",
        json={"record_id": rec_id, "reason": "家长来电口头承诺，人工续借"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    with _db() as db:
        # 模拟时间流逝：新到期日又已过 → 第二次逾期
        db.query(BorrowRecord).filter(BorrowRecord.id == rec_id).update(
            {"due_at": _dt(date.today() - timedelta(days=1))}
        )
        db.commit()
        marked = CirculationService(db).overdue_mark()
        rows = (
            db.query(Notification)
            .filter(Notification.scene == OVERDUE_SCENE, Notification.is_deleted == 0)
            .all()
        )
    assert marked == 1, "第二次逾期应重新标记"
    assert len(rows) == 2, (
        f"再次逾期必须**再**通知一次（实 {len(rows)}）——"
        f"RED=逾期通知用默认 dedup_key，同一借阅行终身只发一条。"
        f"keys={[x.dedup_key for x in rows]}"
    )


def _dt(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time()) + timedelta(hours=20)
