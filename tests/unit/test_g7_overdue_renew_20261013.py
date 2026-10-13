# tests/unit/test_g7_overdue_renew_20261013.py — G7 逾期书的馆员人工续借（2026-10-13 第五十五轮）
"""PRD §5.4 第 5 条：逾期中的书不能续借，「特殊情况馆员可以人工办理逾期书的续借（留痕），
但"每本书只能续借 1 次"的上限谁都不能突破」。本文件钉住该口径：

① 逾期书家长自助仍**硬拦**（G6 口径不回归）；② 馆员人工办理**必须填原因**（空/空白也拒）；
③ 人工办理的新到期日从**办理时刻**起算 `renew_days`（不是原到期日 +7——原到期日已过，
   再加 7 天往往续借后还在逾期，宽限失去意义）；④ 审计留痕：reason = 馆员填的原因、
   detail 带 `overdue/overdue_days`；⑤ 上限照旧：人工办理后第二次（代办或自助）一律拒；
⑥ 未逾期馆员续借不回归：不需原因、仍从**原到期日** +7。

回退即红：把 `_load_renewable` 的 `allow_overdue/overdue_reason` 去掉、或改回"一律硬拦"
→ 前五组用例转红；把新到期日的基准改回 `due_at` → ③ 转红。
"""

import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay, _pay_deposit


def _db_session():
    return _db()


def _member_with_book(client: TestClient, h: dict, phone: str):
    """正式会员 + 押金已缴 + 借出 1 本（返回 parent/child/mini/record）。"""
    p, c, mini = _family(client, h, phone, name="逾期续借孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay(client, h, c["id"], "formal_fee")
    _pay_deposit(client, h, c["id"])
    book_id = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": "逾期续借样例书", "word_count": 100, "copy_count": 1},
        headers=h,
    ).json()["id"]
    force_book_on(client, h, book_id)
    copy_id = client.get(f"/api/admin/books/{book_id}/copies", headers=h).json()[0]["id"]
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copy_id},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return p, c, mini, r.json()


def _make_overdue(record_id: int, days: int = 3) -> datetime:
    """把借阅记录改成已逾期 `days` 天（DB 造态：正常流程等到期太慢）。"""
    from backend.domain.circulation.models import BorrowRecord

    overdue_at = datetime.now().replace(microsecond=0) - timedelta(days=days)
    with _db_session() as db:
        db.query(BorrowRecord).filter(BorrowRecord.id == record_id).update(
            {"due_at": overdue_at, "status": BorrowRecord.STATUS_OVERDUE}
        )
        db.commit()
    return overdue_at


def _admin_renew(client: TestClient, h: dict, record_id: int, reason: str | None = None):
    body: dict = {"record_id": record_id}
    if reason is not None:
        body["reason"] = reason
    return client.post("/api/admin/circulation/renew", json=body, headers=h)


def test_overdue_requires_reason(client: TestClient):
    """人工办理必须有留痕原因：不传、空串、纯空白三种都不放行。"""
    h = _h(client)
    _, _, _, record = _member_with_book(client, h, "13981012311")
    _make_overdue(record["id"])

    no_reason = _admin_renew(client, h, record["id"])
    assert no_reason.status_code == 422, no_reason.text
    assert "必须填写原因" in no_reason.json()["detail"], no_reason.text

    blank = _admin_renew(client, h, record["id"], "   ")
    assert blank.status_code == 422, blank.text
    assert "必须填写原因" in blank.json()["detail"], blank.text

    # 三次都失败 → 记录没被动过（失败不留痕、不占次数）
    from backend.domain.circulation.models import BorrowRecord

    with _db_session() as db:
        row = db.query(BorrowRecord).filter(BorrowRecord.id == record["id"]).first()
        assert row.renew_used == 0, "被拒的尝试不能占用续借次数"
        assert row.due_at < datetime.now(), "被拒的尝试不能改到期日"


def test_overdue_renew_extends_from_now_with_reason(client: TestClient):
    """填了原因 → 放行；新到期日 = **办理时刻** + renew_days（不是原到期日 +7）。"""
    h = _h(client)
    _, _, _, record = _member_with_book(client, h, "13981012312")
    original_due = _make_overdue(record["id"], days=3)
    before = datetime.now()

    r = _admin_renew(client, h, record["id"], "家长电话说明孩子住院，同意宽限一周")
    assert r.status_code == 200, r.text
    data = r.json()
    new_due = datetime.fromisoformat(data["due_at"])
    assert abs((new_due - (before + timedelta(days=7))).total_seconds()) < 60, (
        f"人工办理应从**办理时刻**起算 7 天：now={before} → {new_due}"
    )
    from_original = original_due + timedelta(days=7)
    assert new_due > from_original + timedelta(days=3), (
        f"不能沿用「原到期日 +7」（{from_original}）——那样续借后仍在逾期"
    )
    assert new_due > datetime.now(), "续借后不能还是逾期"
    assert data["renew_used"] == 1, data


def test_overdue_renew_writes_audit_with_reason(client: TestClient):
    """留痕：reason = 馆员填的原因；detail 带 overdue=true 与逾期天数。"""
    from backend.common.system_models import AuditLog

    h = _h(client)
    _, _, _, record = _member_with_book(client, h, "13981012313")
    _make_overdue(record["id"], days=5)

    r = _admin_renew(client, h, record["id"], "家长出差在外，电话商定下周还")
    assert r.status_code == 200, r.text
    with _db_session() as db:
        log = (
            db.query(AuditLog)
            .filter(AuditLog.action == "circulation.renew", AuditLog.target_id == str(record["id"]))
            .first()
        )
    assert log is not None, "人工办理必须写审计"
    assert log.reason == "家长出差在外，电话商定下周还", log.reason
    detail = json.loads(log.detail)
    assert detail["overdue"] is True, detail
    assert detail["overdue_days"] >= 4, detail  # 5 天前到期（含毫秒截断，至少 4）


def test_overdue_human_renew_still_capped(client: TestClient):
    """上限谁都不能突破：人工办理用掉 1 次后，代办与自助两条路径都拒。"""
    h = _h(client)
    _, child, mini, record = _member_with_book(client, h, "13981012314")
    _make_overdue(record["id"])
    assert _admin_renew(client, h, record["id"], "特殊情况人工办理").status_code == 200

    again_staff = _admin_renew(client, h, record["id"], "再宽限一次")
    assert again_staff.status_code == 422, again_staff.text
    assert "续借机会已用完" in again_staff.json()["detail"], again_staff.text

    again_self = client.post(
        f"/api/miniapp/borrows/{record['id']}/renew",
        json={"child_id": child["id"]},
        headers=mini,
    )
    assert again_self.status_code == 422, again_self.text
    assert "续借机会已用完" in again_self.json()["detail"], again_self.text


def test_self_renew_still_blocked_when_overdue(client: TestClient):
    """家长自助对逾期书**仍硬拦**（人工办理是馆员线下的口子，不是给家长的开口）。"""
    h = _h(client)
    _, child, mini, record = _member_with_book(client, h, "13981012315")
    _make_overdue(record["id"])

    r = client.post(
        f"/api/miniapp/borrows/{record['id']}/renew",
        json={"child_id": child["id"]},
        headers=mini,
    )
    assert r.status_code == 422, r.text
    assert "已逾期" in r.json()["detail"], r.text

    rows = client.get("/api/miniapp/borrows", params={"child_id": child["id"]}, headers=mini).json()
    row = [x for x in rows if x["record_id"] == record["id"]][0]
    assert row["can_renew"] is False and row["overdue"] is True, row


def test_normal_renew_needs_no_reason_and_keeps_original_due(client: TestClient):
    """未逾期不回归：不必填原因；仍从**原到期日** +7（G6 口径不动）。"""
    h = _h(client)
    _, _, _, record = _member_with_book(client, h, "13981012316")
    original_due = datetime.fromisoformat(record["due_at"])

    r = _admin_renew(client, h, record["id"])  # 不传 reason
    assert r.status_code == 200, r.text
    new_due = datetime.fromisoformat(r.json()["due_at"])
    assert abs((new_due - original_due).total_seconds() - 7 * 86400) < 2, (
        f"未逾期仍从原到期日 +7：{original_due} → {new_due}"
    )
